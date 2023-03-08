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

from unit_tests.test_utils import CharmTestCase, patch_open
from unittest.mock import patch, MagicMock, call

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
    'coordinator',
    'deferred_events',
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

    @patch('rabbitmq_server_relations.is_hook_allowed')
    @patch('rabbitmq_server_relations.rabbit.leader_node_is_ready')
    @patch('rabbitmq_server_relations.peer_store_and_set')
    @patch('rabbitmq_server_relations.config')
    @patch('rabbitmq_server_relations.relation_set')
    @patch('rabbitmq_server_relations.cmp_pkgrevno')
    @patch('rabbitmq_server_relations.is_clustered')
    @patch('rabbitmq_server_relations.ssl_utils.configure_client_ssl')
    @patch('rabbitmq_server_relations.ch_ip.get_relation_ip')
    @patch('rabbitmq_server_relations.relation_get')
    @patch('rabbitmq_server_relations.is_elected_leader')
    def test_amqp_changed_compare_versions_ha_queues(
            self,
            is_elected_leader,
            relation_get,
            get_relation_ip,
            configure_client_ssl,
            is_clustered,
            cmp_pkgrevno,
            relation_set,
            mock_config,
            mock_peer_store_and_set,
            mock_leader_node_is_ready,
            is_hook_allowed):
        """
        Compare version above and below 3.0.1.
        Make sure ha_queues is set correctly on each side.
        """

        def config(key):
            if key == 'prefer-ipv6':
                return False

            return None

        is_hook_allowed.return_value = (True, '')
        mock_leader_node_is_ready.return_value = True
        mock_config.side_effect = config
        host_addr = "10.1.2.3"
        get_relation_ip.return_value = host_addr
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

    @patch('rabbitmq_server_relations.is_hook_allowed')
    @patch('rabbitmq_server_relations.rabbit.leader_node_is_ready')
    @patch('rabbitmq_server_relations.peer_store_and_set')
    @patch('rabbitmq_server_relations.config')
    @patch('rabbitmq_server_relations.relation_set')
    @patch('rabbitmq_server_relations.cmp_pkgrevno')
    @patch('rabbitmq_server_relations.is_clustered')
    @patch('rabbitmq_server_relations.ssl_utils.configure_client_ssl')
    @patch('rabbitmq_server_relations.ch_ip.get_relation_ip')
    @patch('rabbitmq_server_relations.relation_get')
    @patch('rabbitmq_server_relations.is_elected_leader')
    def test_amqp_changed_compare_versions_ha_queues_prefer_ipv6(
            self,
            is_elected_leader,
            relation_get,
            get_relation_ip,
            configure_client_ssl,
            is_clustered,
            cmp_pkgrevno,
            relation_set,
            mock_config,
            mock_peer_store_and_set,
            mock_leader_node_is_ready,
            is_hook_allowed):
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
        is_hook_allowed.return_value = (True, '')
        ipv6_addr = "2001:db8:1:0:f816:3eff:fed6:c140"
        get_relation_ip.return_value = ipv6_addr
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
                                             remote_unit='client/0',
                                             check_deferred_restarts=True)

        # Client Ready
        self.relation_ids.return_value = ['amqp:0']
        self.related_units.return_value = ['client/0']
        mock_leader_node_is_ready.return_value = False
        mock_client_node_is_ready.return_value = True
        rabbitmq_server_relations.update_clients()
        mock_amqp_changed.assert_called_with(relation_id='amqp:0',
                                             remote_unit='client/0',
                                             check_deferred_restarts=True)

        # Both Ready
        self.relation_ids.return_value = ['amqp:0']
        self.related_units.return_value = ['client/0']
        mock_leader_node_is_ready.return_value = True
        mock_client_node_is_ready.return_value = True
        rabbitmq_server_relations.update_clients()
        mock_amqp_changed.assert_called_with(relation_id='amqp:0',
                                             remote_unit='client/0',
                                             check_deferred_restarts=True)

    @patch.object(rabbitmq_server_relations.rabbit,
                  'configure_ttl')
    @patch.object(rabbitmq_server_relations.rabbit,
                  'configure_notification_ttl')
    @patch.object(rabbitmq_server_relations, 'is_leader')
    @patch.object(rabbitmq_server_relations.rabbit, 'set_ha_mode')
    @patch.object(rabbitmq_server_relations.rabbit, 'get_rabbit_password')
    @patch.object(rabbitmq_server_relations.rabbit, 'create_vhost')
    @patch.object(rabbitmq_server_relations.rabbit, 'create_user')
    @patch.object(rabbitmq_server_relations.rabbit, 'grant_permissions')
    @patch.object(rabbitmq_server_relations, 'config')
    def test_configure_amqp(self, mock_config,
                            mock_grant_permissions, mock_create_vhost,
                            mock_create_user, mock_get_rabbit_password,
                            mock_set_ha_mode, mock_is_leader,
                            mock_configure_notification_ttl,
                            mock_configure_ttl):
        config_data = {
            'notification-ttl': 450000,
            'mirroring-queues': True,
        }
        mock_is_leader.return_value = True
        mock_config.side_effect = lambda attribute: config_data.get(attribute)
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
                               "ttl": None, "mirroring-queues": True}}
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
                                    "ttl": None, "mirroring-queues": True}})
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
                              "ttl": None,
                              "mirroring-queues": True, 'stale': True}
                    # Since this is a dummy case we need to toggle the stale
                    # values.
                    del d[rid]['stale']
                    self.assertEqual(store.get('amqp_config_tracker'), d)
                    d[rid]['stale'] = True

                mock_configure_notification_ttl.assert_not_called()
                mock_configure_ttl.assert_not_called()

                # Test openstack notification workaround
                d = {}
                for rid, user in [('amqp:1', 'userA')]:
                    rabbitmq_server_relations.configure_amqp(
                        user, 'openstack', rid, admin=False,
                        ttlname='heat_expiry',
                        ttlreg='heat-engine-listener|engine_worker', ttl=45000)
                (mock_configure_notification_ttl.
                    assert_called_once_with('openstack', 450000))
                (mock_configure_ttl.
                    assert_called_once_with(
                        'openstack', 'heat_expiry',
                        'heat-engine-listener|engine_worker', 45000))

        finally:
            if os.path.exists(tmpdir):
                shutil.rmtree(tmpdir)

    @patch.object(rabbitmq_server_relations.rabbit, 'grant_permissions')
    @patch('rabbit_utils.create_user')
    @patch('rabbit_utils.local_unit')
    @patch('rabbit_utils.nrpe.NRPE.add_check')
    @patch('rabbit_utils.nrpe.NRPE.remove_check')
    @patch('subprocess.check_call')
    @patch('rabbit_utils.get_rabbit_password_on_disk')
    @patch('charmhelpers.contrib.charmsupport.nrpe.relation_ids')
    @patch('charmhelpers.contrib.charmsupport.nrpe.config')
    @patch('charmhelpers.contrib.charmsupport.nrpe.get_nagios_unit_name')
    @patch('charmhelpers.contrib.charmsupport.nrpe.get_nagios_hostname')
    @patch('os.fchown')
    @patch('rabbit_utils.charm_dir')
    @patch('subprocess.check_output')
    @patch('rabbitmq_server_relations.config')
    @patch('rabbit_utils.config')
    @patch('rabbit_utils.remove_file')
    def test_update_nrpe_checks(self,
                                mock_remove_file,
                                mock_config3,
                                mock_config,
                                mock_check_output,
                                mock_charm_dir, mock_fchown,
                                mock_get_nagios_hostname,
                                mock_get_nagios_unit_name, mock_config2,
                                mock_nrpe_relation_ids,
                                mock_get_rabbit_password_on_disk,
                                mock_check_call,
                                mock_remove_check, mock_add_check,
                                mock_local_unit,
                                mock_create_user,
                                mock_grant_permissions):

        self.test_config.set('ssl', 'on')

        mock_charm_dir.side_effect = lambda: self.tmp_dir
        mock_config.side_effect = self.test_config
        mock_config2.side_effect = self.test_config
        mock_config3.side_effect = self.test_config
        stats_confile = os.path.join(self.tmp_dir, "rabbitmq-stats")
        rabbit_utils.STATS_CRONFILE = stats_confile
        nagios_plugins = os.path.join(self.tmp_dir, "nagios_plugins")
        rabbit_utils.NAGIOS_PLUGINS = nagios_plugins
        scripts_dir = os.path.join(self.tmp_dir, "scripts_dir")
        rabbit_utils.SCRIPTS_DIR = scripts_dir
        mock_get_nagios_hostname.return_value = "foo-0"
        mock_get_nagios_unit_name.return_value = "bar-0"
        mock_get_rabbit_password_on_disk.return_value = "qwerty"
        mock_nrpe_relation_ids.side_effect = lambda x: [
            'nrpe-external-master:1']
        mock_local_unit.return_value = 'unit/0'

        rabbitmq_server_relations.update_nrpe_checks()
        mock_check_output.assert_any_call(
            ['/usr/bin/rsync', '-r', '--delete', '--executability',
             '{}/files/collect_rabbitmq_stats.sh'.format(self.tmp_dir),
             '{}/collect_rabbitmq_stats.sh'.format(scripts_dir)],
            stderr=subprocess.STDOUT)

        # regular check on 5672
        cmd_5672 = ('{plugins_dir}/check_rabbitmq.py --user {user} '
                    '--password {password} --vhost {vhost}').format(
                        plugins_dir=nagios_plugins,
                        user='nagios-unit-0', vhost='nagios-unit-0',
                        password='qwerty')

        mock_add_check.assert_any_call(
            shortname=rabbit_utils.RABBIT_USER,
            description='Check RabbitMQ {} {}'.format('bar-0',
                                                      'nagios-unit-0'),
            check_cmd=cmd_5672)

        # check on ssl port 5671
        cmd_5671 = ('{plugins_dir}/check_rabbitmq.py --user {user} '
                    '--password {password} --vhost {vhost} '
                    '--ssl --ssl-ca {ssl_ca} --port {port}').format(
                        plugins_dir=nagios_plugins,
                        user='nagios-unit-0',
                        password='qwerty',
                        port=int(self.test_config['ssl_port']),
                        vhost='nagios-unit-0',
                        ssl_ca=rabbit_utils.SSL_CA_FILE)
        mock_add_check.assert_any_call(
            shortname=rabbit_utils.RABBIT_USER + "_ssl",
            description='Check RabbitMQ (SSL) {} {}'.format('bar-0',
                                                            'nagios-unit-0'),
            check_cmd=cmd_5671)

        # test stats_cron_schedule has been removed
        mock_remove_file.reset_mock()
        mock_add_check.reset_mock()
        mock_remove_check.reset_mock()
        self.test_config.unset('stats_cron_schedule')
        self.test_config.set('management_plugin', False)
        rabbitmq_server_relations.update_nrpe_checks()
        mock_remove_file.assert_has_calls([
            call(stats_confile),
            call('{}/collect_rabbitmq_stats.sh'.format(scripts_dir)),
            call('{}/check_rabbitmq_queues.py'.format(nagios_plugins)),
            call('{}/check_rabbitmq_cluster.py'.format(nagios_plugins))])
        mock_add_check.assert_has_calls([
            call(shortname=rabbit_utils.RABBIT_USER,
                 description='Check RabbitMQ {} {}'.format('bar-0',
                                                           'nagios-unit-0'),
                 check_cmd=cmd_5672),
            call(shortname=rabbit_utils.RABBIT_USER + "_ssl",
                 description='Check RabbitMQ (SSL) {} {}'.format(
                     'bar-0', 'nagios-unit-0'),
                 check_cmd=cmd_5671),
        ])
        mock_remove_check.assert_has_calls([
            call(shortname=rabbit_utils.RABBIT_USER + '_queue',
                 description='Remove check RabbitMQ Queues',
                 check_cmd='{}/check_rabbitmq_queues.py'.format(
                     nagios_plugins)),
            call(shortname=rabbit_utils.RABBIT_USER + '_cluster',
                 description='Remove check RabbitMQ Cluster',
                 check_cmd='{}/check_rabbitmq_cluster.py'.format(
                     nagios_plugins))])

    @patch.object(rabbitmq_server_relations, 'update_clients')
    @patch.object(rabbitmq_server_relations.rabbit, 'wait_app')
    def test_manage_restart(self, wait_app, update_clients):
        self.coordinator.Serial().granted.return_value = True
        rabbitmq_server_relations.manage_restart()
        self.deferred_events.deferrable_svc_restart.assert_called_once_with(
            'rabbitmq-server')
        update_clients.assert_called_once_with()
        wait_app.assert_called_once_with()

        self.deferred_events.deferrable_svc_restart.reset_mock()
        update_clients.reset_mock()
        wait_app.reset_mock()

        self.coordinator.Serial().granted.return_value = False
        rabbitmq_server_relations.manage_restart()
        self.assertFalse(self.deferred_events.called)
        self.assertFalse(update_clients.called)
        self.assertFalse(wait_app.called)

        self.deferred_events.deferrable_svc_restart.reset_mock()
        update_clients.reset_mock()
        wait_app.reset_mock()

        self.coordinator.Serial().granted.return_value = True
        self.deferred_events.is_restart_permitted.return_value = False
        rabbitmq_server_relations.manage_restart()
        self.deferred_events.deferrable_svc_restart.assert_called_once_with(
            'rabbitmq-server')
        wait_app.assert_called_once_with()
        self.assertFalse(update_clients.called)

    def test_read_erlang_cookie(self):
        with patch_open() as (mock_open, mock_file):
            mock_file.read.return_value = "the-cookie\n"
            self.assertEqual(rabbitmq_server_relations.read_erlang_cookie(),
                             "the-cookie")
            mock_open.assert_called_once_with(rabbit_utils.COOKIE_PATH, 'r')

    def test_write_erlang_cookie(self):
        with patch_open() as (mock_open, mock_file):
            rabbitmq_server_relations.write_erlang_cookie('a-cookie')
            mock_open.assert_called_once_with(rabbit_utils.COOKIE_PATH, 'wb')
            mock_file.write.assert_called_once_with(b'a-cookie')

    @patch.object(rabbitmq_server_relations.rabbit, 'wait_app')
    @patch.object(rabbitmq_server_relations, 'log')
    @patch.object(rabbitmq_server_relations, 'is_unit_paused_set')
    @patch.object(rabbitmq_server_relations, 'service_stop')
    @patch.object(rabbitmq_server_relations, 'service_restart')
    @patch.object(rabbitmq_server_relations, 'peer_store')
    @patch.object(rabbitmq_server_relations, 'peer_retrieve')
    @patch('subprocess.check_output')
    @patch.object(rabbitmq_server_relations, 'read_erlang_cookie')
    @patch.object(rabbitmq_server_relations, 'write_erlang_cookie')
    def test_check_erlang_cookie_on_series_upgrade__is_leader(
        self,
        write_erlang_cookie,
        read_erlang_cookie,
        subprocess_check_output,
        peer_retrieve,
        peer_store,
        service_restart,
        service_stop,
        is_unit_paused_set,
        log,
        wait_app,
    ):
        # check that reading the peer cookie writes to the peer storage, but no
        # new cookie is generated, no restart needed.
        self.is_leader.return_value = True
        read_erlang_cookie.return_value = 'the-cookie'
        peer_retrieve.return_value = 'worse-cookie'
        rabbitmq_server_relations.check_erlang_cookie_on_series_upgrade()
        read_erlang_cookie.assert_called_once_with()
        peer_store.assert_called_once_with('cookie', 'the-cookie')
        service_stop.assert_not_called()
        write_erlang_cookie.assert_not_called()
        is_unit_paused_set.assert_not_called()
        wait_app.assert_not_called()

    @patch.object(rabbitmq_server_relations.rabbit, 'wait_app')
    @patch.object(rabbitmq_server_relations, 'log')
    @patch.object(rabbitmq_server_relations, 'is_unit_paused_set')
    @patch.object(rabbitmq_server_relations, 'service_stop')
    @patch.object(rabbitmq_server_relations, 'service_restart')
    @patch.object(rabbitmq_server_relations, 'peer_store')
    @patch.object(rabbitmq_server_relations, 'peer_retrieve')
    @patch('subprocess.check_output')
    @patch.object(rabbitmq_server_relations, 'read_erlang_cookie')
    @patch.object(rabbitmq_server_relations, 'write_erlang_cookie')
    def test_check_erlang_cookie_on_series_upgrade__is_leader__insecure_cookie(
        self,
        write_erlang_cookie,
        read_erlang_cookie,
        subprocess_check_output,
        peer_retrieve,
        peer_store,
        service_restart,
        service_stop,
        is_unit_paused_set,
        log,
        wait_app,
    ):
        # check that a new cookie does have to be upgraded (e.g. it matches the
        # 20 charmacters of A-Z.  Also, the unit is not paused.
        self.is_leader.return_value = True
        peer_retrieve.return_value = 'worse-cookie'
        read_erlang_cookie.return_value = "ABCDEABCDEABCDEABCDE"
        is_unit_paused_set.return_value = False
        subprocess_check_output.return_value = b"really-good-cookie"
        rabbitmq_server_relations.check_erlang_cookie_on_series_upgrade()
        log.assert_not_called()
        subprocess_check_output.assert_called_once_with(
            ['openssl', 'rand', '-base64', '42'])
        read_erlang_cookie.assert_called_once_with()
        peer_store.assert_called_once_with('cookie', 'really-good-cookie')
        service_stop.assert_called_once_with('rabbitmq-server')
        write_erlang_cookie.assert_called_once_with('really-good-cookie')
        is_unit_paused_set.assert_called_once_with()
        service_restart.assert_called_once_with('rabbitmq-server')
        wait_app.assert_called_once_with()

    @patch.object(rabbitmq_server_relations.rabbit, 'wait_app')
    @patch.object(rabbitmq_server_relations, 'log')
    @patch.object(rabbitmq_server_relations, 'is_unit_paused_set')
    @patch.object(rabbitmq_server_relations, 'service_stop')
    @patch.object(rabbitmq_server_relations, 'service_restart')
    @patch.object(rabbitmq_server_relations, 'peer_store')
    @patch.object(rabbitmq_server_relations, 'peer_retrieve')
    @patch('subprocess.check_output')
    @patch.object(rabbitmq_server_relations, 'read_erlang_cookie')
    @patch.object(rabbitmq_server_relations, 'write_erlang_cookie')
    def test_check_erlang_cookie_on_series_upgrade__is_leader__unit_paused(
        self,
        write_erlang_cookie,
        read_erlang_cookie,
        subprocess_check_output,
        peer_retrieve,
        peer_store,
        service_restart,
        service_stop,
        is_unit_paused_set,
        log,
        wait_app,
    ):
        # check that a new cookie does have to be upgraded (e.g. it matches the
        # 20 charmacters of A-Z.  Also, the unit is paused.
        self.is_leader.return_value = True
        peer_retrieve.return_value = 'worse-cookie'
        read_erlang_cookie.return_value = "ABCDEABCDEABCDEABCDE"
        is_unit_paused_set.return_value = True
        subprocess_check_output.return_value = b"really-good-cookie"
        rabbitmq_server_relations.check_erlang_cookie_on_series_upgrade()
        log.assert_not_called()
        subprocess_check_output.assert_called_once_with(
            ['openssl', 'rand', '-base64', '42'])
        read_erlang_cookie.assert_called_once_with()
        peer_store.assert_called_once_with('cookie', 'really-good-cookie')
        service_stop.assert_called_once_with('rabbitmq-server')
        write_erlang_cookie.assert_called_once_with('really-good-cookie')
        is_unit_paused_set.assert_called_once_with()
        service_restart.assert_not_called()
        wait_app.assert_not_called()

    @patch.object(rabbitmq_server_relations.rabbit, 'wait_app')
    @patch.object(rabbitmq_server_relations, 'log')
    @patch.object(rabbitmq_server_relations, 'is_unit_paused_set')
    @patch.object(rabbitmq_server_relations, 'service_stop')
    @patch.object(rabbitmq_server_relations, 'service_restart')
    @patch.object(rabbitmq_server_relations, 'peer_store')
    @patch.object(rabbitmq_server_relations, 'peer_retrieve')
    @patch('subprocess.check_output')
    @patch.object(rabbitmq_server_relations, 'read_erlang_cookie')
    @patch.object(rabbitmq_server_relations, 'write_erlang_cookie')
    def test_check_erlang_cookie_on_series_upgrade__is_leader__subp_error(
        self,
        write_erlang_cookie,
        read_erlang_cookie,
        subprocess_check_output,
        peer_retrieve,
        peer_store,
        service_restart,
        service_stop,
        is_unit_paused_set,
        log,
        wait_app,
    ):
        # check that a new cookie does have to be upgraded (e.g. it matches the
        # 20 charmacters of A-Z.  However, the subprocess fails and so no new
        # cookie is generated.
        self.is_leader.return_value = True
        peer_retrieve.return_value = 'worse-cookie'
        read_erlang_cookie.return_value = "ABCDEABCDEABCDEABCDE"
        is_unit_paused_set.return_value = True

        def _raise_error(*args, **kwargs):
            raise subprocess.CalledProcessError(
                cmd="some-command",
                returncode=1,
                stderr="went bang",
                output="nothing good")

        subprocess_check_output.side_effect = _raise_error
        rabbitmq_server_relations.check_erlang_cookie_on_series_upgrade()
        log.assert_called_once_with(
            "Couldn't generate a new /var/lib/rabbitmq/.erlang.cookie: reason:"
            " Command 'some-command' returned non-zero exit status 1.",
            level='ERROR')
        subprocess_check_output.assert_called_once_with(
            ['openssl', 'rand', '-base64', '42'])
        read_erlang_cookie.assert_called_once_with()
        peer_store.assert_called_once_with('cookie', 'ABCDEABCDEABCDEABCDE')
        service_stop.assert_not_called()
        write_erlang_cookie.assert_not_called()
        is_unit_paused_set.assert_not_called()
        service_restart.assert_not_called()
        wait_app.assert_not_called()

    @patch.object(rabbitmq_server_relations.rabbit, 'wait_app')
    @patch.object(rabbitmq_server_relations, 'log')
    @patch.object(rabbitmq_server_relations, 'is_unit_paused_set')
    @patch.object(rabbitmq_server_relations, 'service_stop')
    @patch.object(rabbitmq_server_relations, 'service_restart')
    @patch.object(rabbitmq_server_relations, 'peer_store')
    @patch.object(rabbitmq_server_relations, 'peer_retrieve')
    @patch('subprocess.check_output')
    @patch.object(rabbitmq_server_relations, 'read_erlang_cookie')
    @patch.object(rabbitmq_server_relations, 'write_erlang_cookie')
    def test_check_erlang_cookie_on_series_upgrade__is_not_leader(
        self,
        write_erlang_cookie,
        read_erlang_cookie,
        subprocess_check_output,
        peer_retrieve,
        peer_store,
        service_restart,
        service_stop,
        is_unit_paused_set,
        log,
        wait_app,
    ):
        # Not the leader, so just verify if the cookie in the peer relation is
        # different and if so, store it and restart the app (start by being
        # paused).
        self.is_leader.return_value = False
        peer_retrieve.return_value = 'worse-cookie'
        read_erlang_cookie.return_value = "ABCDEABCDEABCDEABCDE"
        is_unit_paused_set.return_value = True
        subprocess_check_output.return_value = b"really-good-cookie"
        rabbitmq_server_relations.check_erlang_cookie_on_series_upgrade()
        log.assert_not_called()
        subprocess_check_output.assert_not_called()
        read_erlang_cookie.assert_called_once_with()
        peer_store.assert_not_called()
        service_stop.assert_called_once_with('rabbitmq-server')
        write_erlang_cookie.assert_called_once_with('worse-cookie')
        is_unit_paused_set.assert_called_once_with()
        service_restart.assert_not_called()
        wait_app.assert_not_called()

    @patch.object(rabbitmq_server_relations, 'leader_set')
    @patch.object(rabbitmq_server_relations, 'leader_get')
    @patch.object(rabbitmq_server_relations, 'apt_install')
    @patch.object(rabbitmq_server_relations, 'filter_installed_packages')
    @patch.object(rabbitmq_server_relations, 'apt_update')
    @patch.object(rabbitmq_server_relations, 'update_clients')
    @patch('rabbit_utils.clustered_with_leader')
    @patch('rabbit_utils.update_peer_cluster_status')
    @patch('rabbit_utils.migrate_passwords_to_peer_relation')
    @patch.object(rabbitmq_server_relations, 'is_elected_leader')
    @patch('os.listdir')
    @patch('charmhelpers.contrib.hardening.harden.config')
    @patch.object(rabbitmq_server_relations, 'config')
    def test_upgrade_charm(self, config, harden_config, listdir,
                           is_elected_releader,
                           migrate_passwords_to_peer_relation,
                           update_peer_cluster_status,
                           clustered_with_leader,
                           update_clients,
                           apt_update,
                           filter_installed_packages,
                           apt_install,
                           leader_get,
                           leader_set):
        config.side_effect = self.test_config
        harden_config.side_effect = self.test_config
        is_elected_releader.return_value = True
        clustered_with_leader.return_value = True
        filter_installed_packages.side_effect = lambda x: x

        leader_get.return_value = None

        rabbitmq_server_relations.upgrade_charm()

        migrate_passwords_to_peer_relation.assert_called()
        update_peer_cluster_status.assert_called()
        apt_update.assert_called_with(fatal=True)
        apt_install.assert_called_with(['python3-amqplib',
                                        'python3-croniter'],
                                       fatal=True)
        leader_set.assert_called_with(
            {rabbit_utils.CLUSTER_MODE_KEY:
             self.test_config.get(rabbit_utils.CLUSTER_MODE_KEY)}
        )
