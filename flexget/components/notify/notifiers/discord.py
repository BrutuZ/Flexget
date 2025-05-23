from datetime import datetime, timedelta
from time import sleep as wait

from dateutil.parser import ParserError, isoparse
from loguru import logger
from requests.exceptions import ReadTimeout

from flexget import plugin
from flexget.event import event
from flexget.plugin import PluginWarning
from flexget.utils.requests import RequestException, Session, TokenBucketLimiter

plugin_name = 'discord'

logger = logger.bind(name=plugin_name)
session = Session()
session.add_domain_limiter(TokenBucketLimiter('discord.com', 6, '3 seconds'))


class DiscordNotifier:
    """Send notification to Discord.

    Example::

        notify:
          entries:
            via:
              - discord:
                  web_hook_url: <string>
                  [silent: <boolean>] (suppress notification)
                  [username: <string>] (override the default username of the webhook)
                  [avatar_url: <string>] (override the default avatar of the webhook)
                  [embeds: <arrays>[<object>]] (override embeds)
    """

    schema = {
        'type': 'object',
        'properties': {
            'web_hook_url': {'type': 'string', 'format': 'uri'},
            'username': {'type': 'string', 'default': 'Flexget'},
            'avatar_url': {'type': 'string', 'format': 'uri'},
            'silent': {'type': 'boolean', 'default': False},
            'embeds': {
                'type': 'array',
                'items': {
                    'type': 'object',
                    'properties': {
                        'title': {'type': 'string'},
                        'description': {'type': 'string'},
                        'url': {'type': 'string', 'format': 'uri'},
                        'color': {
                            'oneOf': [
                                {'type': 'integer'},
                                {'type': 'string'},
                            ]
                        },
                        'footer': {
                            'type': 'object',
                            'properties': {
                                'text': {'type': 'string'},
                                'icon_url': {'type': 'string', 'format': 'uri'},
                                'proxy_icon_url': {'type': 'string', 'format': 'uri'},
                            },
                            'required': ['text'],
                            'additionalProperties': False,
                        },
                        'image': {
                            'type': 'object',
                            'properties': {
                                'url': {'type': 'string', 'format': 'uri'},
                                'proxy_url': {'type': 'string', 'format': 'uri'},
                            },
                            'additionalProperties': False,
                        },
                        'thumbnail': {
                            'type': 'object',
                            'properties': {
                                'url': {'type': 'string', 'format': 'uri'},
                                'proxy_url': {'type': 'string', 'format': 'uri'},
                            },
                            'additionalProperties': False,
                        },
                        'timestamp': {'type': 'string'},
                        'provider': {
                            'type': 'object',
                            'properties': {
                                'name': {'type': 'string'},
                                'url': {'type': 'string', 'format': 'uri'},
                            },
                            'additionalProperties': False,
                        },
                        'author': {
                            'type': 'object',
                            'properties': {
                                'name': {'type': 'string'},
                                'url': {'type': 'string', 'format': 'uri'},
                                'icon_url': {'type': 'string', 'format': 'uri'},
                                'proxy_icon_url': {'type': 'string', 'format': 'uri'},
                            },
                            'additionalProperties': False,
                        },
                        'fields': {
                            'type': 'array',
                            'minItems': 1,
                            'items': {
                                'type': 'object',
                                'properties': {
                                    'name': {'type': 'string'},
                                    'value': {'type': 'string'},
                                    'inline': {'type': 'boolean'},
                                },
                                'required': ['name', 'value'],
                                'additionalProperties': False,
                            },
                        },
                    },
                    'additionalProperties': False,
                },
            },
        },
        'required': ['web_hook_url'],
        'additionalProperties': False,
    }

    def notify(self, title, message, config):
        """Send discord notification.

        :param str message: message body
        :param dict config: discord plugin config
        """
        for embed in config.get('embeds', []):
            ts = embed.get('timestamp')
            if ts:
                if isinstance(ts, str):
                    if ts.isdigit():
                        try:
                            ts = datetime.utcfromtimestamp(int(ts))
                        except (ValueError, OverflowError):
                            logger.info(
                                "Value provided for 'timestamp' ({}) is not a timestamp ({}).",
                                embed['timestamp'],
                                int(datetime.now().timestamp()),
                            )
                    else:
                        try:
                            ts = isoparse(ts)
                            embed['timestamp'] = ts
                        except (ParserError, ValueError) as e:
                            logger.info("'timestamp' is in an invalid format: {}", e)
                if not isinstance(ts, datetime):
                    embed.pop('timestamp', None)
                    logger.warning("'timestamp' is invalid, dropping it")
                else:
                    embed['timestamp'] = datetime.strftime(ts, r'%Y-%m-%dT%H:%M:%S%z')

            if isinstance(embed.get('color'), str):
                try:
                    int(embed['color'], 16)
                except TypeError:
                    logger.warning("Invalid 'color' for embed ({}), ignoring", embed['color'])
                    embed.pop('color', None)

        web_hook = {
            'content': message,
            'username': config.get('username'),
            'avatar_url': config.get('avatar_url'),
            'embeds': config.get('embeds'),
        }

        if config.get('silent'):
            web_hook['flags'] = 4096  # Suppress notification bitfield

        # Send the request and handle the rate-limit response.
        for _ in range(3):
            try:
                req = session.post(config['web_hook_url'], json=web_hook)
                tokens = int(req.headers.get('x-ratelimit-remaining', 1))
                tokens_reset = timedelta(
                    seconds=int(req.headers.get('x-ratelimit-reset-after', 3))
                )
                logger.trace(f'Remaining rate tokens: {tokens}. Resets afer {tokens_reset} secs.')
                session.add_domain_limiter(
                    TokenBucketLimiter('discord.com', tokens=tokens, rate=tokens_reset)
                )
            except ReadTimeout:
                logger.info('Request timed out')
                continue
            except RequestException as e:
                if e.response.status_code == 429:
                    timeout = int(
                        e.response.headers.get(
                            'retry-after', e.response.json().get('retry_after', 3)
                        )
                    )
                    logger.info('Rate-limited, waiting for {}', timedelta(seconds=timeout))
                    wait(timeout)
                    continue
                raise PluginWarning(e.args[0])
            else:
                break


@event('plugin.register')
def register_plugin():
    plugin.register(DiscordNotifier, plugin_name, api_ver=2, interfaces=['notifiers'])
