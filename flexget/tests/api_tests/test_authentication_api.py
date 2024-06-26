import json

from flexget.api.app import base_message


class TestAuthenticationAPI:
    config = "{'tasks': {}}"

    def test_login(self, api_client, schema_match):
        rsp = api_client.json_post('/auth/login/', data=json.dumps({}))
        assert rsp.status_code == 422, f'Response code is {rsp.status_code}'

        data = json.loads(rsp.get_data(as_text=True))

        errors = schema_match(base_message, data)
        assert not errors

        invalid_credentials = {'username': 'bla', 'password': 'bla'}

        rsp = api_client.json_post('/auth/login/', data=json.dumps(invalid_credentials))
        assert rsp.status_code == 401, f'Response code is {rsp.status_code}'

        data = json.loads(rsp.get_data(as_text=True))

        errors = schema_match(base_message, data)
        assert not errors
