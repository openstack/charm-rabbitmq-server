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
            'ssl_ca': 'ext_ca'}
        self.config.side_effect = lambda x: test_config[x]
        relation_data = {}
        ssl_utils.configure_client_ssl(relation_data)
        self.assertEqual(
            relation_data,
            {'ssl_port': '9090', 'ssl_ca': 'ZXh0X2Nh'})

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
