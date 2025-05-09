"""Input plugin for www.betaseries.com."""

from hashlib import md5

from loguru import logger

from flexget import plugin
from flexget.entry import Entry
from flexget.event import event
from flexget.utils import requests
from flexget.utils.cached_input import cached

logger = logger.bind(name='betaseries_list')

API_URL_PREFIX = 'https://api.betaseries.com/'


class BetaSeriesList:
    """Emit an entry for each serie followed by one or more BetaSeries account.

    See https://www.betaseries.com/

    Configuration examples::

        # will get all series followed by the account identified by your_user_name
        betaseries_list:
          username: your_user_name
          password: your_password
          api_key: your_api_key

    ::

        # will get all series followed by the account identified by some_other_guy
        betaseries_list:
          username: your_user_name
          password: your_password
          api_key: your_api_key
          members:
            - some_other_guy

    ::

        # will get all series followed by the accounts identified by guy1 and guy2
        betaseries_list:
          username: your_user_name
          password: your_password
          api_key: your_api_key
          members:
            - guy1
            - guy2


    Api key can be requested at https://www.betaseries.com/api.

    This plugin is meant to work with the import_series plugin as follow::

        import_series:
          from:
            betaseries_list:
              username: xxxxx
              password: xxxxx
              api_key: xxxxx
    """

    schema = {
        'type': 'object',
        'properties': {
            'username': {'type': 'string'},
            'password': {'type': 'string'},
            'api_key': {'type': 'string'},
            'members': {'type': 'array', 'items': {'title': 'member name', 'type': 'string'}},
        },
        'required': ['username', 'password', 'api_key'],
        'additionalProperties': False,
    }

    @cached('betaseries_list', persist='2 hours')
    def on_task_input(self, task, config):
        username = config['username']
        password = config['password']
        api_key = config['api_key']
        members = config.get('members', [username])

        titles = set()
        try:
            user_token = create_token(api_key, username, password)
            for member in members:
                titles.update(query_series(api_key, user_token, member))
        except (requests.RequestException, AssertionError) as err:
            logger.opt(exception=True).critical(
                'Failed to get series at BetaSeries.com: {}', err.message
            )

        logger.verbose('series: ' + ', '.join(titles))
        entries = []
        for t in titles:
            e = Entry()
            e['title'] = t
            entries.append(e)
        return entries


def create_token(api_key, login, password):
    """Login in and request an new API token.

    https://www.betaseries.com/wiki/Documentation#cat-members

    :param string api_key: Api key requested at https://www.betaseries.com/api
    :param string login: Login name
    :param string password: Password
    :return: User token
    """
    r = requests.post(
        API_URL_PREFIX + 'members/auth',
        params={'login': login, 'password': md5(password.encode('utf-8')).hexdigest()},
        headers={
            'Accept': 'application/json',
            'X-BetaSeries-Version': '2.1',
            'X-BetaSeries-Key': api_key,
        },
    )
    assert r.status_code == 200, f'Bad HTTP status code: {r.status_code}'
    j = r.json()
    error_list = j['errors']
    for err in error_list:
        logger.error(str(err))
    if not error_list:
        return j['token']
    return None


def query_member_id(api_key, user_token, login_name):
    """Get the member id of a member identified by its login name.

    :param string api_key: Api key requested at https://www.betaseries.com/api
    :param string user_token: obtained with a call to create_token()
    :param string login_name: The login name of the member
    :return: Id of the member identified by its login name or `None` if not found
    """
    r = requests.get(
        API_URL_PREFIX + 'members/search',
        params={'login': login_name},
        headers={
            'Accept': 'application/json',
            'X-BetaSeries-Version': '2.1',
            'X-BetaSeries-Key': api_key,
            'X-BetaSeries-Token': user_token,
        },
    )
    assert r.status_code == 200, f'Bad HTTP status code: {r.status_code}'
    j = r.json()
    error_list = j['errors']
    for err in error_list:
        logger.error(str(err))
    found_id = None
    if not error_list:
        for candidate in j['users']:
            if candidate['login'] == login_name:
                found_id = candidate['id']
                break
    return found_id


def query_series(api_key, user_token, member_name=None):
    """Get the list of series followed by the authenticated user.

    :param string api_key: Api key requested at https://www.betaseries.com/api
    :param string user_token: Obtained with a call to create_token()
    :param string member_name: [optional] A member name to get the list of series from. If None, will query the member
        for whom the user_token was for
    :return: List of serie titles or empty list
    """
    params = {}
    if member_name:
        member_id = query_member_id(api_key, user_token, member_name)
        if member_id:
            params = {'id': member_id}
        else:
            logger.error('member {!r} not found', member_name)
            return []
    r = requests.get(
        API_URL_PREFIX + 'shows/member',
        params=params,
        headers={
            'Accept': 'application/json',
            'X-BetaSeries-Version': '2.1',
            'X-BetaSeries-Key': api_key,
            'X-BetaSeries-Token': user_token,
        },
    )
    assert r.status_code == 200, f'Bad HTTP status code: {r.status_code}'
    j = r.json()
    error_list = j['errors']
    for err in error_list:
        logger.error(str(err))
    if not error_list:
        return [x['title'] for x in j['shows'] if x['user']['archived'] is False]
    return []


@event('plugin.register')
def register_plugin():
    plugin.register(BetaSeriesList, 'betaseries_list', api_ver=2, interfaces=['task'])
