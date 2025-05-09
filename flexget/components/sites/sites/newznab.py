import feedparser
from loguru import logger

from flexget import plugin
from flexget.entry import Entry
from flexget.event import event
from flexget.utils.requests import RequestException

__author__ = 'deksan'

logger = logger.bind(name='newznab')


class Newznab:
    """Newznab search plugin.

    Provide a url or your website + apikey and a category.
    When a URL is provided it will override website and apikey; category will not be used in the URL
    but is still needed to properly fill out search parameters.

    Config example:

        newznab:
          url: "http://website/api?apikey=xxxxxxxxxxxxxxxxxxxxxxxxxx&t=movie&extended=1"
          website: https://website
          apikey: xxxxxxxxxxxxxxxxxxxxxxxxxx
          category: movie

    Category is any of: movie, tvsearch, tv, music, book, all
    """

    schema = {
        'type': 'object',
        'properties': {
            'category': {
                'type': 'string',
                'enum': ['movie', 'tvsearch', 'tv', 'music', 'book', 'all'],
            },
            'url': {'type': 'string', 'format': 'url'},
            'website': {'type': 'string', 'format': 'url'},
            'apikey': {'type': 'string'},
        },
        'required': ['category'],
        'oneOf': [{'required': ['url']}, {'required': ['website']}],
        'additionalProperties': False,
    }

    @staticmethod
    def get_url_and_params(config):
        logger.debug(type(config))
        params = {}

        # url overrides all other settings
        if 'url' in config:
            return config['url'], params

        category = config['category']
        if category == 'tv':
            category = 'tvsearch'
        if category == 'all':
            category = 'search'

        params['t'] = category
        params['extended'] = 1
        params['apikey'] = config.get('apikey')

        url = f'{config["website"]}/api'

        return url, params

    def fill_entries_for_url(self, url, params, task):
        entries = []
        logger.verbose("Fetching '{}', with parameters '{}'", url, params)

        try:
            r = task.requests.get(url, params=params)
        except RequestException as e:
            logger.error("Failed fetching '{}', with parameters '{}': {}", url, params, e)
        else:
            rss = feedparser.parse(r.content)
            logger.debug('Raw RSS: {}', rss)

            if not rss.entries:
                logger.info('No results returned')

            for rss_entry in rss.entries:
                new_entry = Entry()

                for key in list(rss_entry.keys()):
                    new_entry[key] = rss_entry[key]
                new_entry['url'] = new_entry['link']
                if rss_entry.enclosures:
                    size = int(rss_entry.enclosures[0]['length'])  # B
                    new_entry['content_size'] = size  # B
                entries.append(new_entry)

        return entries

    def search(self, task, entry, config=None):
        url, params = self.get_url_and_params(config)
        if config['category'] == 'movie':
            return self.do_search_movie(entry, task, url, params)
        if config['category'] in ('tv', 'tvsearch'):
            return self.do_search_tvsearch(entry, task, url, params)
        if config['category'] == 'all':
            return self.do_search_all(entry, task, url, params)
        entries = []
        logger.warning(
            'Work in progress. Searching for the specified category is not supported yet...'
        )
        return entries

    def do_search_tvsearch(self, arg_entry, task, url, params):
        logger.info('Searching for {}', arg_entry['title'])
        # normally this should be used with next_series_episodes who has provided season and episodenumber
        if (
            'series_name' not in arg_entry
            or 'series_season' not in arg_entry
            or 'series_episode' not in arg_entry
        ):
            return []
        if arg_entry.get('tvdb_id'):
            params['tvdbid'] = arg_entry.get('tvdb_id')
        elif arg_entry.get('trakt_id'):
            params['traktid'] = arg_entry.get('trakt_id')
        elif arg_entry.get('tvmaze_series_id'):
            params['tvmazeid'] = arg_entry.get('tvmaze_series_id')
        elif arg_entry.get('tmdb_id'):
            params['tmdbid'] = arg_entry.get('tmdb_id')
        elif arg_entry.get('imdb_id'):
            params['imdbid'] = arg_entry.get('imdb_id')
        elif arg_entry.get('tvrage_id'):
            params['rid'] = arg_entry.get('tvrage_id')
        else:
            params['q'] = arg_entry['series_name']
        params['ep'] = arg_entry['series_episode']
        params['season'] = arg_entry['series_season']
        return self.fill_entries_for_url(url, params, task)

    def do_search_movie(self, arg_entry, task, url, params):
        logger.info('Searching for {} (imdb_id:{})', arg_entry['title'], arg_entry.get('imdb_id'))
        # normally this should be used with emit_movie_queue who has imdbid (i guess)
        if not arg_entry.get('imdb_id'):
            logger.error('Cannot search for `{}` without imdb_id', arg_entry['title'])
            return []

        imdb_id = arg_entry['imdb_id'].replace('tt', '')
        params['imdbid'] = imdb_id
        return self.fill_entries_for_url(url, params, task)

    def do_search_all(self, arg_entry, task, url, params):
        logger.info('Searching for {}', arg_entry['title'])
        params['q'] = arg_entry['title']
        return self.fill_entries_for_url(url, params, task)


@event('plugin.register')
def register_plugin():
    plugin.register(Newznab, 'newznab', api_ver=2, interfaces=['search'])
