# Copyright 2016 Canonical Ltd
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

import os
import shutil
import subprocess
import sys
import tempfile

from test_utils import CharmTestCase
from mock import patch, MagicMock, call

from charmhelpers.core.unitdata import Storage

os.environ['JUJU_UNIT_NAME'] = 'UNIT_TEST/0'  # noqa - needed for import

# python-apt is not installed as part of test-requirements but is imported by
# some charmhelpers modules so create a fake import.
mock_apt = MagicMock()
sys.modules['apt'] = mock_apt
mock_apt.apt_pkg = MagicMock()

with patch('charmhelpers.contrib.hardening.harden.harden') as mock_dec:
    mock_dec.side_effect = (lambda *dargs, **dkwargs: lambda f:
                            lambda *args, **kwargs: f(*args, **kwargs))
    import rabbitmq_server_relations
    import rabbit_utils

TO_PATCH = [
    # charmhelpers.core.hookenv
    'is_leader',
    'relation_ids',
    'related_units',
]


class RelationUtil(CharmTestCase):
    def setUp(self):
        self.fake_repo = {}
        super(RelationUtil, self).setUp(rabbitmq_server_relations,
                                        TO_PATCH)
        self.tmp_dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp_dir)
        super(RelationUtil, self).tearDown()

    @patch('rabbitmq_server_relations.rabbit.leader_node_is_ready')
    @patch('rabbitmq_server_relations.peer_store_and_set')
    @patch('rabbitmq_server_relations.config')
    @patch('rabbitmq_server_relations.relation_set')
    @patch('rabbitmq_server_relations.cmp_pkgrevno')
    @patch('rabbitmq_server_relations.is_clustered')
    @patch('rabbitmq_server_relations.ssl_utils.configure_client_ssl')
    @patch('rabbitmq_server_relations.rabbit.get_unit_ip')
    @patch('rabbitmq_server_relations.relation_get')
    @patch('rabbitmq_server_relations.is_elected_leader')
    def test_amqp_changed_compare_versions_ha_queues(
            self,
            is_elected_leader,
            relation_get,
            get_unit_ip,
            configure_client_ssl,
            is_clustered,
            cmp_pkgrevno,
            relation_set,
            mock_config,
            mock_peer_store_and_set,
            mock_leader_node_is_ready):
        """
        Compare version above and below 3.0.1.
        Make sure ha_queues is set correctly on each side.
        """

        def config(key):
            if key == 'prefer-ipv6':
                return False

            return None

        mock_leader_node_is_ready.return_value = True
        mock_config.side_effect = config
        host_addr = "10.1.2.3"
        get_unit_ip.return_value = host_addr
        is_elected_leader.return_value = True
        relation_get.return_value = {}
        is_clustered.return_value = False
        cmp_pkgrevno.return_value = -1

        rabbitmq_server_relations.amqp_changed(None, None)
        mock_peer_store_and_set.assert_called_with(
            relation_settings={'private-address': '10.1.2.3',
                               'hostname': host_addr,
                               'ha_queues': True},
            relation_id=None)

        cmp_pkgrevno.return_value = 1
        rabbitmq_server_relations.amqp_changed(None, None)
        mock_peer_store_and_set.assert_called_with(
            relation_settings={'private-address': '10.1.2.3',
                               'hostname': host_addr},
            relation_id=None)

    @patch('rabbitmq_server_relations.rabbit.leader_node_is_ready')
    @patch('rabbitmq_server_relations.peer_store_and_set')
    @patch('rabbitmq_server_relations.config')
    @patch('rabbitmq_server_relations.relation_set')
    @patch('rabbitmq_server_relations.cmp_pkgrevno')
    @patch('rabbitmq_server_relations.is_clustered')
    @patch('rabbitmq_server_relations.ssl_utils.configure_client_ssl')
    @patch('rabbitmq_server_relations.rabbit.get_unit_ip')
    @patch('rabbitmq_server_relations.relation_get')
    @patch('rabbitmq_server_relations.is_elected_leader')
    def test_amqp_changed_compare_versions_ha_queues_prefer_ipv6(
            self,
            is_elected_leader,
            relation_get,
            get_unit_ip,
            configure_client_ssl,
            is_clustered,
            cmp_pkgrevno,
            relation_set,
            mock_config,
            mock_peer_store_and_set,
            mock_leader_node_is_ready):
        """
        Compare version above and below 3.0.1.
        Make sure ha_queues is set correctly on each side.
        """

        def config(key):
            if key == 'prefer-ipv6':
                return True

            return None

        mock_leader_node_is_ready.return_value = True
        mock_config.side_effect = config
        ipv6_addr = "2001:db8:1:0:f816:3eff:fed6:c140"
        get_unit_ip.return_value = ipv6_addr
        is_elected_leader.return_value = True
        relation_get.return_value = {}
        is_clustered.return_value = False
        cmp_pkgrevno.return_value = -1

        rabbitmq_server_relations.amqp_changed(None, None)
        mock_peer_store_and_set.assert_called_with(
            relation_settings={'private-address': ipv6_addr,
                               'hostname': ipv6_addr,
                               'ha_queues': True},
            relation_id=None)

        cmp_pkgrevno.return_value = 1
        rabbitmq_server_relations.amqp_changed(None, None)
        mock_peer_store_and_set.assert_called_with(
            relation_settings={'private-address': ipv6_addr,
                               'hostname': ipv6_addr},
            relation_id=None)

    @patch('rabbitmq_server_relations.amqp_changed')
    @patch('rabbitmq_server_relations.rabbit.client_node_is_ready')
    @patch('rabbitmq_server_relations.rabbit.leader_node_is_ready')
    def test_update_clients(self, mock_leader_node_is_ready,
                            mock_client_node_is_ready,
                            mock_amqp_changed):
        # Not ready
        mock_client_node_is_ready.return_value = False
        mock_leader_node_is_ready.return_value = False
        rabbitmq_server_relations.update_clients()
        self.assertFalse(mock_amqp_changed.called)

        # Leader Ready
        self.relation_ids.return_value = ['amqp:0']
        self.related_units.return_value = ['client/0']
        mock_leader_node_is_ready.return_value = True
        mock_client_node_is_ready.return_value = False
        rabbitmq_server_relations.update_clients()
        mock_amqp_changed.assert_called_with(relation_id='amqp:0',
                                             remote_unit='client/0')

        # Client Ready
        self.relation_ids.return_value = ['amqp:0']
        self.related_units.return_value = ['client/0']
        mock_leader_node_is_ready.return_value = False
        mock_client_node_is_ready.return_value = True
        rabbitmq_server_relations.update_clients()
        mock_amqp_changed.assert_called_with(relation_id='amqp:0',
                                             remote_unit='client/0')

        # Both Ready
        self.relation_ids.return_value = ['amqp:0']
        self.related_units.return_value = ['client/0']
        mock_leader_node_is_ready.return_value = True
        mock_client_node_is_ready.return_value = True
        rabbitmq_server_relations.update_clients()
        mock_amqp_changed.assert_called_with(relation_id='amqp:0',
                                             remote_unit='client/0')

    @patch.object(rabbitmq_server_relations, 'is_leader')
    @patch.object(rabbitmq_server_relations.rabbit, 'set_ha_mode')
    @patch.object(rabbitmq_server_relations.rabbit, 'get_rabbit_password')
    @patch.object(rabbitmq_server_relations.rabbit, 'create_vhost')
    @patch.object(rabbitmq_server_relations.rabbit, 'create_user')
    @patch.object(rabbitmq_server_relations.rabbit, 'grant_permissions')
    @patch.object(rabbitmq_server_relations, 'config', lambda *args: True)
    def test_configure_amqp(self, mock_grant_permissions, mock_create_vhost,
                            mock_create_user, mock_get_rabbit_password,
                            mock_set_ha_mode, mock_is_leader):
        mock_is_leader.return_value = True
        tmpdir = tempfile.mkdtemp()
        try:
            db_path = '{}/kv.db'.format(tmpdir)
            rid = 'amqp:1'
            store = Storage(db_path)
            with patch('charmhelpers.core.unitdata._KV', store):
                # Check .set
                with patch.object(store, 'set') as mock_set:
                    rabbitmq_server_relations.configure_amqp('user_foo',
                                                             'vhost_blah', rid)

                    d = {rid: {"username": "user_foo", "vhost": "vhost_blah",
                               "mirroring-queues": True}}
                    mock_set.assert_has_calls([call(key='amqp_config_tracker',
                                                    value=d)])

                    for m in [mock_grant_permissions, mock_create_vhost,
                              mock_create_user, mock_set_ha_mode]:
                        self.assertTrue(m.called)
                        m.reset_mock()

                # Check .get
                with patch.object(store, 'get') as mock_get:
                    mock_get.return_value = d
                    rabbitmq_server_relations.configure_amqp('user_foo',
                                                             'vhost_blah', rid)
                    mock_set.assert_has_calls([call(key='amqp_config_tracker',
                                                    value=d)])
                    for m in [mock_grant_permissions, mock_create_vhost,
                              mock_create_user, mock_set_ha_mode]:
                        self.assertFalse(m.called)

                # Check invalid relation id
                self.assertRaises(Exception,
                                  rabbitmq_server_relations.configure_amqp,
                                  'user_foo', 'vhost_blah', None, admin=True)

                # Test writing data
                d = {}
                for rid, user in [('amqp:1', 'userA'), ('amqp:2', 'userB')]:
                    rabbitmq_server_relations.configure_amqp(user,
                                                             'vhost_blah', rid)

                    d.update({rid: {"username": user, "vhost": "vhost_blah",
                                    "mirroring-queues": True}})
                    self.assertEqual(store.get('amqp_config_tracker'), d)

                @rabbitmq_server_relations.validate_amqp_config_tracker
                def fake_configure_amqp(*args, **kwargs):
                    return rabbitmq_server_relations.configure_amqp(*args,
                                                                    **kwargs)

                # Test invalidating data
                mock_is_leader.return_value = False
                d['amqp:2']['stale'] = True
                for rid, user in [('amqp:1', 'userA'), ('amqp:3', 'userC')]:
                    fake_configure_amqp(user, 'vhost_blah', rid)
                    d[rid] = {"username": user, "vhost": "vhost_blah",
                              "mirroring-queues": True, 'stale': True}
                    # Since this is a dummy case we need to toggle the stale
                    # values.
                    del d[rid]['stale']
                    self.assertEqual(store.get('amqp_config_tracker'), d)
                    d[rid]['stale'] = True
        finally:
            if os.path.exists(tmpdir):
                shutil.rmtree(tmpdir)

    @patch('rabbitmq_server_relations.local_unit')
    @patch('charmhelpers.contrib.charmsupport.nrpe.NRPE.add_check')
    @patch('subprocess.check_call')
    @patch('rabbit_utils.get_rabbit_password_on_disk')
    @patch('charmhelpers.contrib.charmsupport.nrpe.relation_ids')
    @patch('charmhelpers.contrib.charmsupport.nrpe.config')
    @patch('charmhelpers.contrib.charmsupport.nrpe.get_nagios_unit_name')
    @patch('charmhelpers.contrib.charmsupport.nrpe.get_nagios_hostname')
    @patch('os.fchown')
    @patch('rabbitmq_server_relations.charm_dir')
    @patch('subprocess.check_output')
    @patch('rabbitmq_server_relations.config')
    def test_update_nrpe_checks(self, mock_config, mock_check_output,
                                mock_charm_dir, mock_fchown,
                                mock_get_nagios_hostname,
                                mock_get_nagios_unit_name, mock_config2,
                                mock_nrpe_relation_ids,
                                mock_get_rabbit_password_on_disk,
                                mock_check_call, mock_add_check,
                                mock_local_unit):

        self.test_config.set('ssl', 'on')

        mock_charm_dir.side_effect = lambda: self.tmp_dir
        mock_config.side_effect = self.test_config
        mock_config2.side_effect = self.test_config
        rabbitmq_server_relations.STATS_CRONFILE = os.path.join(
            self.tmp_dir, "rabbitmq-stats")
        mock_get_nagios_hostname.return_value = "foo-0"
        mock_get_nagios_unit_name.return_value = "bar-0"
        mock_get_rabbit_password_on_disk.return_value = "qwerty"
        mock_nrpe_relation_ids.side_effect = lambda x: [
            'nrpe-external-master:1']
        mock_local_unit.return_value = 'unit/0'

        rabbitmq_server_relations.update_nrpe_checks()

        mock_check_output.assert_any_call(
            ['/usr/bin/rsync', '-r', '--delete', '--executability',
             '%s/scripts/collect_rabbitmq_stats.sh' % self.tmp_dir,
             '/usr/local/bin/collect_rabbitmq_stats.sh'],
            stderr=subprocess.STDOUT)

        # regular check on 5672
        cmd = ('{plugins_dir}/check_rabbitmq.py --user {user} '
               '--password {password} --vhost {vhost}').format(
                   plugins_dir=rabbitmq_server_relations.NAGIOS_PLUGINS,
                   user='nagios-unit-0', vhost='nagios-unit-0',
                   password='qwerty')

        mock_add_check.assert_any_call(
            shortname=rabbit_utils.RABBIT_USER,
            description='Check RabbitMQ {%s}' % 'bar-0', check_cmd=cmd)

        # check on ssl port 5671
        cmd = ('{plugins_dir}/check_rabbitmq.py --user {user} '
               '--password {password} --vhost {vhost} '
               '--ssl --ssl-ca {ssl_ca} --port {port}').format(
                   plugins_dir=rabbitmq_server_relations.NAGIOS_PLUGINS,
                   user='nagios-unit-0',
                   password='qwerty',
                   port=int(self.test_config['ssl_port']),
                   vhost='nagios-unit-0',
                   ssl_ca=rabbitmq_server_relations.SSL_CA_FILE)
        mock_add_check.assert_any_call(
            shortname=rabbit_utils.RABBIT_USER + "_ssl",
            description='Check RabbitMQ (SSL) {%s}' % 'bar-0', check_cmd=cmd)
