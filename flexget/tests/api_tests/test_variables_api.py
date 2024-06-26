from flexget.components.variables.variables import Variables
from flexget.manager import Session
from flexget.utils import json


class TestVariablesAPI:
    config = 'tasks: {}'

    variables_dict = {'test_variable_db': True}

    def test_variables_get(self, api_client):
        with Session() as session:
            s = Variables(variables=self.variables_dict)
            session.add(s)

        rsp = api_client.get('/variables/')
        assert rsp.status_code == 200, f'Response code is {rsp.status_code}'
        assert json.loads(rsp.get_data(as_text=True)) == self.variables_dict

    def test_variables_put(self, api_client):
        rsp = api_client.get('/variables/')
        assert rsp.status_code == 200, f'Response code is {rsp.status_code}'
        assert json.loads(rsp.get_data(as_text=True)) == {}

        rsp = api_client.json_put('/variables/', data=json.dumps(self.variables_dict))
        assert rsp.status_code == 201, f'Response code is {rsp.status_code}'
        assert json.loads(rsp.get_data(as_text=True)) == self.variables_dict

        rsp = api_client.get('/variables/')
        assert rsp.status_code == 200, f'Response code is {rsp.status_code}'
        assert json.loads(rsp.get_data(as_text=True)) == self.variables_dict

    def test_variables_patch(self, api_client):
        data = {'a': 'b', 'c': 'd'}
        api_client.json_put('/variables/', data=json.dumps(data))
        new_data = {'a': [1, 2, 3], 'foo': 'bar'}

        rsp = api_client.json_patch('/variables/', data=json.dumps(new_data))
        assert rsp.status_code == 200, f'Response code is {rsp.status_code}'
        assert json.loads(rsp.get_data(as_text=True)) == {'a': [1, 2, 3], 'foo': 'bar', 'c': 'd'}
