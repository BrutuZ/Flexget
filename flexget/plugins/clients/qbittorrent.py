import os

import pendulum
from loguru import logger
from requests import Session
from requests.exceptions import RequestException

from flexget import plugin
from flexget.entry import Entry
from flexget.event import event
from flexget.utils.template import RenderError
from flexget.utils.tools import parse_timedelta

logger = logger.bind(name='qbittorrent')


class OutputQBitTorrent:
    """The QBitTorrent plugin.

    Example::

        qbittorrent:
          username: <USERNAME> (default: (none))
          password: <PASSWORD> (default: (none))
          host: <HOSTNAME> (default: localhost)
          port: <PORT> (default: 8080)
          use_ssl: <SSL> (default: False)
          verify_cert: <VERIFY> (default: True)
          path: <OUTPUT_DIR> (default: (none))
          label: <LABEL> (default: (none))
          tags: <TAGS> (default: (none))
          maxupspeed: <torrent upload speed limit> (default: 0)
          maxdownspeed: <torrent download speed limit> (default: 0)
          add_paused: <ADD_PAUSED> (default: False)
          ratio_limit: <RATIO_LIMIT> (default: -2)
          seeding_time_limit: <SEEDING_TIME_LIMIT> (default: -1)
    """

    schema = {
        'anyOf': [
            {'type': 'boolean'},
            {
                'type': 'object',
                'properties': {
                    'username': {'type': 'string'},
                    'password': {'type': 'string'},
                    'host': {'type': 'string'},
                    'port': {'type': 'integer'},
                    'use_ssl': {'type': 'boolean'},
                    'verify_cert': {'type': 'boolean'},
                    'path': {'type': 'string'},
                    'label': {'type': 'string'},
                    'tags': {'type': 'array', 'items': {'type': 'string'}},
                    'maxupspeed': {'type': 'integer'},
                    'maxdownspeed': {'type': 'integer'},
                    'fail_html': {'type': 'boolean'},
                    'add_paused': {'type': 'boolean'},
                    'skip_check': {'type': 'boolean'},
                    'ratio_limit': {'type': 'number'},
                    'seeding_time_limit': {'type': 'string', 'format': 'interval'},
                },
                'additionalProperties': False,
            },
        ]
    }

    def __init__(self):
        super().__init__()
        self.session = Session()
        self.api_url_login = None
        self.api_url_upload = None
        self.api_url_download = None
        self.api_url_info = None
        self.url = None
        self.connected = False

    def _request(self, method, url, msg_on_fail=None, **kwargs):
        try:
            response = self.session.request(method, url, **kwargs)
            if response.text == 'Ok.':
                return True
            msg = msg_on_fail if msg_on_fail else f'Failure. URL: {url}, data: {kwargs}'
        except RequestException as e:
            msg = str(e)
        logger.error('Error when trying to send request to qBittorrent: {}', msg)
        return False

    def check_api_version(self, msg_on_fail, verify=True):
        try:
            url = self.url + '/api/v2/app/webapiVersion'
            response = self.session.request('get', url, verify=verify)
            if response.status_code != 404:
                self.api_url_login = '/api/v2/auth/login'
                self.api_url_upload = '/api/v2/torrents/add'
                self.api_url_download = '/api/v2/torrents/add'
                self.api_url_info = '/api/v2/torrents/info'
                return response

            url = self.url + '/version/api'
            response = self.session.request('get', url, verify=verify)
            if response.status_code != 404:
                self.api_url_login = '/login'
                self.api_url_upload = '/command/upload'
                self.api_url_download = '/command/download'
                self.api_url_info = '/query/torrents'
                return response

            msg = msg_on_fail if msg_on_fail else f'Failure. URL: {url}'
        except RequestException as e:
            msg = str(e)
        raise plugin.PluginError(f'Error when trying to send request to qBittorrent: {msg}')

    def connect(self, config):
        """Connect to qBittorrent Web UI.

        Username and password not necessary if 'Bypass authentication for localhost' is checked and host is 'localhost'.
        """
        self.url = '{}://{}:{}'.format(
            'https' if config['use_ssl'] else 'http', config['host'], config['port']
        )
        self.check_api_version('Check API version failed.', verify=config['verify_cert'])
        if config.get('username') and config.get('password'):
            data = {'username': config['username'], 'password': config['password']}
            if not self._request(
                'post',
                self.url + self.api_url_login,
                data=data,
                msg_on_fail='Authentication failed.',
                verify=config['verify_cert'],
            ):
                raise plugin.PluginError('Not connected.')
        logger.debug('Successfully connected to qBittorrent')
        self.connected = True

    def check_torrent_exists(self, hash_torrent, verify_cert):
        if not self.connected:
            raise plugin.PluginError('Not connected.')

        if not isinstance(hash_torrent, str):
            logger.error('Error getting torrent info, invalid hash {}', hash_torrent)
            return False

        hash_torrent = hash_torrent.lower()

        logger.debug('Checking if torrent with hash {!r} already in session.', hash)

        url = f'{self.url}{self.api_url_info}'
        params = {'hashes': hash_torrent}

        try:
            respose = self.session.request(
                'get',
                url,
                params=params,
                verify=verify_cert,
            )
        except RequestException:
            logger.error('Error getting torrent info, request to hash {} failed', hash_torrent)
            return False

        if respose.status_code != 200:
            logger.error(
                'Error getting torrent info, hash {} search returned',
                hash_torrent,
                respose.status_code,
            )
            return False

        check_file = respose.json()

        if isinstance(check_file, list) and check_file:
            logger.warning('File with hash {} already in qbittorrent', hash_torrent)
            return True

        return False

    def add_torrent_file(self, entry, data, verify_cert):
        file_path = entry['file']
        if not self.connected:
            raise plugin.PluginError('Not connected.')

        multipart_data = {k: (None, v) for k, v in data.items()}
        with open(file_path, 'rb') as f:
            multipart_data['torrents'] = f
            if not self._request(
                'post',
                self.url + self.api_url_upload,
                msg_on_fail='Failed to add file to qBittorrent',
                files=multipart_data,
                verify=verify_cert,
            ):
                entry.fail(f'Error adding file `{file_path}` to qBittorrent')
                return
        logger.debug('Added torrent file {} to qBittorrent', file_path)

    def add_torrent_url(self, entry, data, verify_cert):
        url = entry['url']
        if not self.connected:
            raise plugin.PluginError('Not connected.')

        data['urls'] = url
        multipart_data = {k: (None, v) for k, v in data.items()}
        if not self._request(
            'post',
            self.url + self.api_url_download,
            msg_on_fail=f'Failed to add url to qBittorrent: {url}',
            files=multipart_data,
            verify=verify_cert,
        ):
            entry.fail(f'Error adding url `{url}` to qBittorrent')
            return
        logger.debug('Added url {} to qBittorrent', url)

    @staticmethod
    def prepare_config(config):
        if isinstance(config, bool):
            config = {'enabled': config}
        config.setdefault('enabled', True)
        config.setdefault('host', 'localhost')
        config.setdefault('port', 8080)
        config.setdefault('use_ssl', False)
        config.setdefault('verify_cert', True)
        config.setdefault('label', '')
        config.setdefault('tags', [])
        config.setdefault('maxupspeed', 0)
        config.setdefault('maxdownspeed', 0)
        config.setdefault('fail_html', True)
        return config

    def add_entries(self, task, config):
        for entry in task.accepted:
            form_data = {}
            try:
                save_path = entry.render(entry.get('path', config.get('path', '')))
                if save_path:
                    form_data['savepath'] = save_path
            except RenderError as e:
                logger.error('Error setting path for {}: {}', entry['title'], e)

            label = entry.render(entry.get('label', config.get('label', '')))
            if label:
                form_data['label'] = label  # qBittorrent v3.3.3-
                form_data['category'] = label  # qBittorrent v3.3.4+

            tags = entry.get('tags', []) + config.get('tags', [])
            if tags:
                try:
                    form_data['tags'] = entry.render(','.join(tags))
                except RenderError as e:
                    logger.error('Error rendering tags for {}: {}', entry['title'], e)
                    form_data['tags'] = ','.join(tags)

            add_paused = entry.get('add_paused', config.get('add_paused'))
            if add_paused:
                form_data['paused'] = 'true'  # qBittorrent v4.6.7-
                form_data['stopped'] = 'true'  # qBittorrent v5.0.0+

            skip_check = entry.get('skip_check', config.get('skip_check'))
            if skip_check:
                form_data['skip_checking'] = 'true'

            maxupspeed = entry.get('maxupspeed', config.get('maxupspeed'))
            if maxupspeed:
                form_data['upLimit'] = maxupspeed * 1024

            maxdownspeed = entry.get('maxdownspeed', config.get('maxdownspeed'))
            if maxdownspeed:
                form_data['dlLimit'] = maxdownspeed * 1024

            ratio_limit = entry.get('ratio_limit', config.get('ratio_limit'))
            if ratio_limit:
                form_data['ratioLimit'] = str(ratio_limit)

            seeding_time_limit = entry.get('seeding_time_limit', config.get('seeding_time_limit'))
            if seeding_time_limit:
                form_data['seedingTimeLimit'] = int(
                    parse_timedelta(seeding_time_limit).total_seconds() / 60
                )

            is_magnet = entry['url'].startswith('magnet:')

            if task.manager.options.test:
                logger.info('Test mode.')
                logger.info('Would add torrent to qBittorrent with:')
                if not is_magnet:
                    logger.info('File: {}', entry.get('file'))
                else:
                    logger.info('Url: {}', entry.get('url'))
                logger.info('Save path: {}', form_data.get('savepath'))
                logger.info('Label: {}', form_data.get('label'))
                logger.info('Tags: {}', form_data.get('tags'))
                logger.info('Paused: {}', form_data.get('paused', 'false'))
                logger.info('Skip Hash Check: {}', form_data.get('skip_checking', 'false'))
                if maxupspeed:
                    logger.info('Upload Speed Limit: {}', form_data.get('upLimit'))
                if maxdownspeed:
                    logger.info('Download Speed Limit: {}', form_data.get('dlLimit'))
                logger.info('Ratio limit: {}', form_data.get('ratio_limit', -2))
                logger.info(
                    'Seeding time limit: {} minutes', form_data.get('seeding_time_limit', -1)
                )
                continue

            if self.check_torrent_exists(
                entry.get('torrent_info_hash'), config.get('verify_cert')
            ):
                continue

            if not is_magnet:
                if 'file' not in entry:
                    entry.fail('File missing?')
                    continue
                if not os.path.exists(entry['file']):
                    tmp_path = os.path.join(task.manager.config_base, 'temp')
                    logger.debug('entry: {}', entry)
                    logger.debug('temp: {}', ', '.join(os.listdir(tmp_path)))
                    entry.fail("Downloaded temp file '{}' doesn't exist!?".format(entry['file']))
                    continue
                self.add_torrent_file(entry, form_data, config['verify_cert'])
            else:
                self.add_torrent_url(entry, form_data, config['verify_cert'])

    @plugin.priority(120)
    def on_task_download(self, task, config):
        """Call download plugin to generate torrent files to load into qBittorrent."""
        config = self.prepare_config(config)
        if not config['enabled']:
            return
        if 'download' not in task.config:
            download = plugin.get('download', self)
            download.get_temp_files(task, handle_magnets=True, fail_html=config['fail_html'])

    @plugin.priority(135)
    def on_task_output(self, task, config):
        """Add torrents to qBittorrent at exit."""
        if task.accepted:
            config = self.prepare_config(config)
            self.connect(config)
            self.add_entries(task, config)


class FromQBitTorrent:
    schema = {
        'type': 'object',
        'properties': {
            'category': {'type': 'string'},
            'completed': {'type': 'boolean'},
            'username': {'type': 'string'},
            'password': {'type': 'string'},
            'host': {'type': 'string'},
            'port': {'type': 'integer'},
        },
        'additionalProperties': False,
        'required': ['username', 'password', 'host', 'port'],
    }

    @staticmethod
    def client(host: str, port: int, username: str, password: str):
        """Import client or abort task."""
        try:
            import qbittorrentapi
        except ImportError:
            raise plugin.DependencyError(issued_by='from_qbittorrent', missing='qbittorrent-api')

        return qbittorrentapi.Client(host=host, port=port, username=username, password=password)

    def on_task_input(self, task, config):
        client = self.client(
            config['host'], int(config['port']), config['username'], config['password']
        )

        for torrent in client.torrents_info():
            if 'category' in config:
                logger.debug('filtered `{}` by wrong category', torrent['name'])
                if torrent['category'] != config['category']:
                    continue

            if 'completed' in config and not torrent.state_enum.is_complete:
                logger.debug('filtered `{}` by not completed', torrent['name'])
                continue

            yield Entry(
                title=torrent['name'],
                url=torrent['magnet_uri'],
                content_files=[f['name'] for f in torrent.files],
                content_size=torrent['size'],
                torrent_info_hash=torrent['infohash_v1'],
                torrent_info_hash_v2=torrent['infohash_v2'],
                torrent_seeds=torrent['num_seeds'],
                torrent_peers=torrent['num_leechs'],
                qbittorrent_ratio=torrent['ratio'],
                qbittorrent_category=torrent['category'],
                qbittorrent_state=torrent['state'],
                qbittorrent_eta=torrent['eta'],
                qbittorrent_added_on=pendulum.from_timestamp(torrent['added_on']),
                qbittorrent_completion_on=pendulum.from_timestamp(torrent['completion_on']),
                qbittorrent_completed_path=torrent['content_path'],
                qbittorrent_download_path=torrent['download_path'],
                qbittorrent_save_path=torrent['save_path'],
                qbittorrent_size=torrent['size'],
                qbittorrent_dl_speed=torrent['dlspeed'],
                qbittorrent_up_speed=torrent['upspeed'],
                qbittorrent_is_checking=torrent.state_enum.is_checking,
                qbittorrent_is_complete=torrent.state_enum.is_complete,
                qbittorrent_is_downloading=torrent.state_enum.is_downloading,
                qbittorrent_is_errored=torrent.state_enum.is_errored,
                qbittorrent_is_paused=torrent.state_enum.is_paused,
                qbittorrent_is_uploading=torrent.state_enum.is_uploading,
            )


@event('plugin.register')
def register_plugin():
    plugin.register(OutputQBitTorrent, 'qbittorrent', api_ver=2)
    plugin.register(FromQBitTorrent, 'from_qbittorrent', api_ver=2)
