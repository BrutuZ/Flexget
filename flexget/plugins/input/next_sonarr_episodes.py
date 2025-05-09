import math
from urllib.parse import urlparse

from loguru import logger
from requests import RequestException

from flexget import plugin
from flexget.entry import Entry
from flexget.event import event

logger = logger.bind(name='next_sonarr_episodes')


class NextSonarrEpisodes:
    r"""Return the 1st missing episode of every show configures in Sonarr.

    This can be used with the discover plugin or set_series_begin plugin to
    get the relevant data from Sonarr.

    Syntax::

        next_sonarr_episodes:
          base_url=<value> (Required)
          port=<value> (Default is 80)
          api_key=<value> (Required)
          include_ended=<yes|no> (Default is yes)
          only_monitored=<yes|no> (Default is yes)
          page_size=<value> (Default is 50)

    Page size determines the amount of results per each API call.
    Higher value means a bigger response. Lower value means more calls.
    Should be changed if there are performance issues.


    Usage (Example with discover)::

        discover_from_sonarr_task:
          discover:
            what:
              - next_sonarr_episodes:
                  base_url: '{? credentials.sonarr.url ?}'
                  port: 8989
                  api_key: '{? credentials.sonarr.api_key ?}'
                  include_ended: false
            from:
              - kat:
                  verified: yes
          all_series: yes
          download: c:\bla\

    Usage (Example with set_series_begin)::

        set-series-begin-from-sonarr:
          next_sonarr_episodes:
            base_url: '{{ secrets.credentials.sonarr.url }}'
            port: 8989
            api_key: '{{ secrets.credentials.sonarr.api_key }}'
            include_ended: false
          accept_all: yes
          set_series_begin: yes
    """

    schema = {
        'type': 'object',
        'properties': {
            'base_url': {'type': 'string'},
            'port': {'type': 'number', 'default': 80},
            'api_key': {'type': 'string'},
            'include_ended': {'type': 'boolean', 'default': True},
            'only_monitored': {'type': 'boolean', 'default': True},
            'page_size': {'type': 'number', 'default': 50},
        },
        'required': ['api_key', 'base_url'],
        'additionalProperties': False,
    }

    # Function that gets a page number and page size and returns the responding result json
    def get_page(self, task, config, page_number):
        parsedurl = urlparse(config.get('base_url'))
        url = '{}://{}:{}{}/api/v3/wanted/missing?page={}&pageSize={}&sortKey=series.title&sortdir=asc'.format(
            parsedurl.scheme,
            parsedurl.netloc,
            config.get('port'),
            parsedurl.path,
            page_number,
            config.get('page_size'),
        )
        headers = {'X-Api-Key': config['api_key']}
        try:
            json = task.requests.get(url, headers=headers).json()
        except RequestException as e:
            raise plugin.PluginError(
                'Unable to connect to Sonarr at {}://{}:{}{}. Error: {}'.format(
                    parsedurl.scheme, parsedurl.netloc, config.get('port'), parsedurl.path, e
                )
            )
        return json

    def on_task_input(self, task, config):
        json = self.get_page(task, config, 1)
        pages = math.ceil(
            json['totalRecords'] / config.get('page_size')
        )  # Sets number of requested pages
        current_series_id = 0  # Initializes current series parameter
        for page in range(1, pages + 1):
            json = self.get_page(task, config, page)
            for record in json['records']:
                # Verifies that we only get the first missing episode from a series
                if current_series_id != record['seriesId']:
                    current_series_id = record['seriesId']
                    season = record['seasonNumber']
                    episode = record['episodeNumber']
                    entry = Entry(
                        url='',
                        series_name=record['series']['title'],
                        series_season=season,
                        series_episode=episode,
                        series_id=f'S{season:02d}E{episode:02d}',
                        tvdb_id=record['series'].get('tvdbId'),
                        tvrage_id=record['series'].get('tvRageId'),
                        tvmaze_id=record['series'].get('tvMazeId'),
                        title=record['series']['title'] + ' ' + f'S{season:02d}E{episode:02d}',
                    )
                    # Test mode logging
                    if entry and task.options.test:
                        logger.verbose('Test mode. Entry includes:')
                        for key, value in list(entry.items()):
                            logger.verbose('     {}: {}', key.capitalize(), value)
                    if entry.isvalid():
                        yield entry
                    else:
                        logger.error('Invalid entry created? {}', entry)


@event('plugin.register')
def register_plugin():
    plugin.register(NextSonarrEpisodes, 'next_sonarr_episodes', api_ver=2)
