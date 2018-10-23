# Copyright 2017 Canonical Ltd
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#  http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.


from test_utils import CharmTestCase
from mock import patch

import ssl_utils

TO_PATCH = [
    'config',
]

TEST_CA = """-----BEGIN CERTIFICATE-----
MIIDbTCCAlWgAwIBAgIURtdGGKKjckiLPLue8Wn/sCS5u+QwDQYJKoZIhvcNAQEL
BQAwPTE7MDkGA1UEAxMyVmF1bHQgUm9vdCBDZXJ0aWZpY2F0ZSBBdXRob3JpdHkg
KGNoYXJtLXBraS1sb2NhbCkwIBgPMDAwMTAxMDEwMDAwMDBaFw0xODExMjQxMzQx
MjdaMD0xOzA5BgNVBAMTMlZhdWx0IFJvb3QgQ2VydGlmaWNhdGUgQXV0aG9yaXR5
IChjaGFybS1wa2ktbG9jYWwpMIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKC
AQEAwUEg8XFO2GzI19aNAfH8KeBsLvpYTX4nNREEGLMkl7qfqO+rcwNmN/60UxSu
Hbsqfjv6B6kWD6dd1/OvveYjxqPA97OqO5LOUE43ojzUkxai5GeF5fvu3QGIR7iZ
a9PEDFjFKeCdwyKLoIHNdXw1TM0sQmWM7sSiMhCfrpeZEe+En+KZQugo+BiLrhKA
yZTIkEP5+6r/Nrxfkx2/Kklrq8LOyLfH91LbmJEVEKQNloCYphZYwB7n9GPvKlGv
pvPuJc7wEkmtCMp0dNjo3MZ0ij1SIN6Ntx8DqhPJ8QKvNDogVmeEGpQFBcrzfkol
LMXPBpX2Qx6dPqLGHCbWQDnvewIDAQABo2MwYTAOBgNVHQ8BAf8EBAMCAQYwDwYD
VR0TAQH/BAUwAwEB/zAdBgNVHQ4EFgQUc1rh2BEHSQJ0qxhPTDQKRJg2AGEwHwYD
VR0jBBgwFoAUc1rh2BEHSQJ0qxhPTDQKRJg2AGEwDQYJKoZIhvcNAQELBQADggEB
ABZvreticW5UuoQS7NAVICCvh5FwgrkC5tnHX3p8TOhMIpJTgrKhedJZKzLc254g
/jAsb7q775IcMOhS2vFJSQd6rV0cMNCdFjk0sTTe01OXoJj2fN3MMbEEGfs6crwk
TKiXEJ9XYc04Ul4b8XJ0d5hYejr5IF9leJ2JJMiGTJFGU1Oi8Lctj7qyX0nlo+x5
Xhj8BbsJsbUGoA+bXvCOO88voyOZoRGCg1JFztbpgIAV6k64DJ7xp9tNDhZJj0Uo
2MDrWbfUYFWMiD5L0d5MjeX7aGIPhJsMund1zFHr1ho64OdCJ1zDmtk4UYzZ0deE
5nLA3FXh+snaEpmpl7X9Xus=
-----END CERTIFICATE-----"""

B64_TEST_CA = """LS0tLS1CRUdJTiBDRVJUSUZJQ0FURS0tLS0tCk1JSURiVENDQWxXZ0F3SUJBZ0lVUnRkR0dLS2pja2lMUEx1ZThXbi9zQ1M1dStRd0RRWUpLb1pJaHZjTkFRRUwKQlFBd1BURTdNRGtHQTFVRUF4TXlWbUYxYkhRZ1VtOXZkQ0JEWlhKMGFXWnBZMkYwWlNCQmRYUm9iM0pwZEhrZwpLR05vWVhKdExYQnJhUzFzYjJOaGJDa3dJQmdQTURBd01UQXhNREV3TURBd01EQmFGdzB4T0RFeE1qUXhNelF4Ck1qZGFNRDB4T3pBNUJnTlZCQU1UTWxaaGRXeDBJRkp2YjNRZ1EyVnlkR2xtYVdOaGRHVWdRWFYwYUc5eWFYUjUKSUNoamFHRnliUzF3YTJrdGJHOWpZV3dwTUlJQklqQU5CZ2txaGtpRzl3MEJBUUVGQUFPQ0FROEFNSUlCQ2dLQwpBUUVBd1VFZzhYRk8yR3pJMTlhTkFmSDhLZUJzTHZwWVRYNG5OUkVFR0xNa2w3cWZxTytyY3dObU4vNjBVeFN1Ckhic3FmanY2QjZrV0Q2ZGQxL092dmVZanhxUEE5N09xTzVMT1VFNDNvanpVa3hhaTVHZUY1ZnZ1M1FHSVI3aVoKYTlQRURGakZLZUNkd3lLTG9JSE5kWHcxVE0wc1FtV003c1NpTWhDZnJwZVpFZStFbitLWlF1Z28rQmlMcmhLQQp5WlRJa0VQNSs2ci9Ocnhma3gyL0trbHJxOExPeUxmSDkxTGJtSkVWRUtRTmxvQ1lwaFpZd0I3bjlHUHZLbEd2CnB2UHVKYzd3RWttdENNcDBkTmpvM01aMGlqMVNJTjZOdHg4RHFoUEo4UUt2TkRvZ1ZtZUVHcFFGQmNyemZrb2wKTE1YUEJwWDJReDZkUHFMR0hDYldRRG52ZXdJREFRQUJvMk13WVRBT0JnTlZIUThCQWY4RUJBTUNBUVl3RHdZRApWUjBUQVFIL0JBVXdBd0VCL3pBZEJnTlZIUTRFRmdRVWMxcmgyQkVIU1FKMHF4aFBURFFLUkpnMkFHRXdId1lEClZSMGpCQmd3Rm9BVWMxcmgyQkVIU1FKMHF4aFBURFFLUkpnMkFHRXdEUVlKS29aSWh2Y05BUUVMQlFBRGdnRUIKQUJadnJldGljVzVVdW9RUzdOQVZJQ0N2aDVGd2dya0M1dG5IWDNwOFRPaE1JcEpUZ3JLaGVkSlpLekxjMjU0ZwovakFzYjdxNzc1SWNNT2hTMnZGSlNRZDZyVjBjTU5DZEZqazBzVFRlMDFPWG9KajJmTjNNTWJFRUdmczZjcndrClRLaVhFSjlYWWMwNFVsNGI4WEowZDVoWWVqcjVJRjlsZUoySkpNaUdUSkZHVTFPaThMY3RqN3F5WDBubG8reDUKWGhqOEJic0pzYlVHb0ErYlh2Q09PODh2b3lPWm9SR0NnMUpGenRicGdJQVY2azY0REo3eHA5dE5EaFpKajBVbwoyTURyV2JmVVlGV01pRDVMMGQ1TWplWDdhR0lQaEpzTXVuZDF6RkhyMWhvNjRPZENKMXpEbXRrNFVZelowZGVFCjVuTEEzRlhoK3NuYUVwbXBsN1g5WHVzPQotLS0tLUVORCBDRVJUSUZJQ0FURS0tLS0t"""  # noqa: E501


class TestSSLUtils(CharmTestCase):

    def setUp(self):
        super(TestSSLUtils, self).setUp(ssl_utils, TO_PATCH)

    def test_get_ssl_mode_off(self):
        test_config = {
            'ssl': 'off',
            'ssl_enabled': False,
            'ssl_on': False,
            'ssl_key': None,
            'ssl_cert': None}
        self.config.side_effect = lambda x: test_config[x]
        self.assertEqual(
            ssl_utils.get_ssl_mode(),
            ('off', False))

    def test_get_ssl_enabled_true(self):
        test_config = {
            'ssl': 'off',
            'ssl_enabled': True,
            'ssl_on': False,
            'ssl_key': None,
            'ssl_cert': None}
        self.config.side_effect = lambda x: test_config[x]
        self.assertEqual(
            ssl_utils.get_ssl_mode(),
            ('on', False))

    def test_get_ssl_enabled_false(self):
        test_config = {
            'ssl': 'on',
            'ssl_enabled': False,
            'ssl_on': False,
            'ssl_key': None,
            'ssl_cert': None}
        self.config.side_effect = lambda x: test_config[x]
        self.assertEqual(
            ssl_utils.get_ssl_mode(),
            ('on', False))

    def test_get_ssl_enabled_external_ca(self):
        test_config = {
            'ssl': 'on',
            'ssl_enabled': False,
            'ssl_on': False,
            'ssl_key': 'key1',
            'ssl_cert': 'cert1'}
        self.config.side_effect = lambda x: test_config[x]
        self.assertEqual(
            ssl_utils.get_ssl_mode(),
            ('on', True))

    @patch('ssl_utils.get_ssl_mode')
    def test_get_ssl_mode_ssl_off(self, get_ssl_mode):
        get_ssl_mode.return_value = ('off', False)
        relation_data = {}
        ssl_utils.configure_client_ssl(relation_data)
        self.assertEqual(relation_data, {})

    @patch('ssl_utils.ServiceCA')
    @patch('ssl_utils.get_ssl_mode')
    def test_get_ssl_mode_ssl_on_no_ca(self, get_ssl_mode, ServiceCA):
        ServiceCA.get_ca().get_ca_bundle.return_value = 'cert1'
        get_ssl_mode.return_value = ('on', False)
        test_config = {
            'ssl_port': '9090'}
        self.config.side_effect = lambda x: test_config[x]
        relation_data = {}
        ssl_utils.configure_client_ssl(relation_data)
        self.assertEqual(
            relation_data,
            {'ssl_port': '9090', 'ssl_ca': 'Y2VydDE='})

    @patch('ssl_utils.get_ssl_mode')
    def test_get_ssl_mode_ssl_on_ext_ca(self, get_ssl_mode):
        get_ssl_mode.return_value = ('on', True)
        test_config = {
            'ssl_port': '9090',
            'ssl_ca': TEST_CA}
        self.config.side_effect = lambda x: test_config[x]
        relation_data = {}
        ssl_utils.configure_client_ssl(relation_data)
        self.maxDiff = None
        self.assertEqual(
            relation_data,
            {'ssl_port': '9090', 'ssl_ca': B64_TEST_CA})

    @patch('ssl_utils.get_ssl_mode')
    def test_get_ssl_mode_ssl_on_ext_ca_b64(self, get_ssl_mode):
        get_ssl_mode.return_value = ('on', True)
        test_config = {
            'ssl_port': '9090',
            'ssl_ca': 'ZXh0X2Nh'}
        self.config.side_effect = lambda x: test_config[x]
        relation_data = {}
        ssl_utils.configure_client_ssl(relation_data)
        self.assertEqual(
            relation_data,
            {'ssl_port': '9090', 'ssl_ca': 'ZXh0X2Nh'})

    @patch('ssl_utils.local_unit')
    @patch('ssl_utils.relation_ids')
    @patch('ssl_utils.relation_get')
    @patch('ssl_utils.configure_client_ssl')
    @patch('ssl_utils.relation_set')
    def test_reconfigure_client_ssl_no_ssl(self, relation_set,
                                           configure_client_ssl, relation_get,
                                           relation_ids, local_unit):
        relation_ids.return_value = ['rel1']
        relation_get.return_value = {'ssl_key': 'aa'}
        ssl_utils.reconfigure_client_ssl(ssl_enabled=False)
        relation_set.assert_called_with(
            relation_id='rel1',
            ssl_ca='',
            ssl_cert='',
            ssl_key='',
            ssl_port='')

    @patch('ssl_utils.local_unit')
    @patch('ssl_utils.relation_ids')
    @patch('ssl_utils.relation_get')
    @patch('ssl_utils.configure_client_ssl')
    @patch('ssl_utils.relation_set')
    def test_reconfigure_client_ssl(self, relation_set, configure_client_ssl,
                                    relation_get, relation_ids, local_unit):
        relation_ids.return_value = ['rel1']
        relation_get.return_value = {}
        ssl_utils.reconfigure_client_ssl(ssl_enabled=True)
        configure_client_ssl.assert_called_with({})
