#!/usr/bin/env python
#
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

"""
Basic 3-node rabbitmq-server native cluster + nrpe functional tests

Cinder is present to exercise and inspect amqp relation functionality.

Each individual test is idempotent, in that it creates/deletes
a rmq test user, enables or disables ssl as needed.

Test order is not required, however tests are numbered to keep
relevant tests grouped together in run order.
"""

import amulet
import time
import subprocess
import json

from charmhelpers.contrib.openstack.amulet.deployment import (
    OpenStackAmuletDeployment
)

from charmhelpers.contrib.openstack.amulet.utils import (
    OpenStackAmuletUtils,
    DEBUG,
    # ERROR
)
from charmhelpers.contrib.openstack.utils import CompareOpenStackReleases

from charmhelpers.core.host import (
    lsb_release,
    CompareHostReleases,
)

# Use DEBUG to turn on debug logging
u = OpenStackAmuletUtils(DEBUG)


class RmqBasicDeployment(OpenStackAmuletDeployment):
    """Amulet tests on a basic rabbitmq cluster deployment. Verify
       relations, service status, users and endpoint service catalog."""

    def __init__(self, series=None, openstack=None, source=None, stable=True):
        """Deploy the entire test environment."""
        super(RmqBasicDeployment, self).__init__(series, openstack, source,
                                                 stable)
        self.client_series = lsb_release()['DISTRIB_CODENAME']
        self._add_services()
        self._add_relations()
        self._configure_services()
        self._deploy()

        u.log.info('Waiting on extended status checks...')
        exclude_services = ['nrpe']

        # Wait for deployment ready msgs, except exclusions
        self._auto_wait_for_status(exclude_services=exclude_services)

        # Specifically wait for rmq cluster status msgs
        u.rmq_wait_for_cluster(self, init_sleep=0)

        self.d.sentry.wait(timeout=900)
        self._initialize_tests()

    def _add_services(self):
        """Add services

           Add the services that we're testing, where rmq is local,
           and the rest of the service are from lp branches that are
           compatible with the local charm (e.g. stable or next).
           """
        this_service = {
            'name': 'rabbitmq-server',
            'units': 3,
            'constraints': {'cpu-cores': 2},
        }
        other_services = [
            {'name': 'cinder'},
            {'name': 'percona-cluster'},
            {'name': 'keystone'},
            {'name': 'nrpe'}
        ]

        super(RmqBasicDeployment, self)._add_services(this_service,
                                                      other_services)

    def _add_relations(self):
        """Add relations for the services."""
        relations = {
            'cinder:amqp': 'rabbitmq-server:amqp',
            'cinder:shared-db': 'percona-cluster:shared-db',
            'cinder:identity-service': 'keystone:identity-service',
            'cinder:amqp': 'rabbitmq-server:amqp',
            'keystone:shared-db': 'percona-cluster:shared-db',
            'nrpe:nrpe-external-master': 'rabbitmq-server:'
                                         'nrpe-external-master'
        }

        super(RmqBasicDeployment, self)._add_relations(relations)

    def _configure_services(self):
        """Configure all of the services."""
        rmq_config = {
            'min-cluster-size': '3',
            'max-cluster-tries': '6',
            'ssl': 'off',
            'management_plugin': 'False',
            'stats_cron_schedule': '*/1 * * * *'
        }

        pxc_config = {
            'max-connections': 1000,
        }

        keystone_config = {
            'admin-password': 'openstack',
            'admin-token': 'ubuntutesting',
        }

        cinder_config = {}

        configs = {
            'rabbitmq-server': rmq_config,
            'percona-cluster': pxc_config,
            'keystone': keystone_config,
            'cinder': cinder_config,
        }
        super(RmqBasicDeployment, self)._configure_services(configs)

    def _initialize_tests(self):
        """Perform final initialization before tests get run."""
        # Access the sentries for inspecting service units
        self.rmq0_sentry = self.d.sentry['rabbitmq-server'][0]
        self.rmq1_sentry = self.d.sentry['rabbitmq-server'][1]
        self.rmq2_sentry = self.d.sentry['rabbitmq-server'][2]
        self.keystone_sentry = self.d.sentry['keystone'][0]
        self.pxc_sentry = self.d.sentry['percona-cluster'][0]
        self.cinder_sentry = self.d.sentry['cinder'][0]
        self.nrpe_sentry = self.d.sentry['nrpe'][0]
        u.log.debug('openstack release val: {}'.format(
            self._get_openstack_release()))
        u.log.debug('openstack release str: {}'.format(
            self._get_openstack_release_string()))

    def _run_action(self, unit_id, action, *args):
        command = ["juju", "action", "do", "--format=json", unit_id, action]
        command.extend(args)
        print("Running command: %s\n" % " ".join(command))
        output = subprocess.check_output(command)
        output_json = output.decode(encoding="UTF-8")
        data = json.loads(output_json)
        action_id = data[u'Action queued with id']
        return action_id

    def _wait_on_action(self, action_id):
        command = ["juju", "action", "fetch", "--format=json", action_id]
        while True:
            try:
                output = subprocess.check_output(command)
            except Exception as e:
                print(e)
                return False
            output_json = output.decode(encoding="UTF-8")
            data = json.loads(output_json)
            if data[u"status"] == "completed":
                return True
            elif data[u"status"] == "failed":
                return False
            time.sleep(2)

    def _get_rmq_sentry_units(self):
        """Local helper specific to this 3-node rmq series of tests."""
        return [self.rmq0_sentry,
                self.rmq1_sentry,
                self.rmq2_sentry]

    def _test_rmq_amqp_messages_all_units(self, sentry_units,
                                          ssl=False, port=None):
        """Reusable test to send amqp messages to every listed rmq unit
        and check every listed rmq unit for messages.

        :param sentry_units: list of sentry units
        :returns: None if successful.  Raise on error.
        """

        # Add test user if it does not already exist
        u.add_rmq_test_user(sentry_units)

        # Handle ssl (includes wait-for-cluster)
        if ssl:
            u.configure_rmq_ssl_on(sentry_units, deployment=self, port=port)
        else:
            u.configure_rmq_ssl_off(sentry_units, deployment=self)

        # Publish and get amqp messages in all possible unit combinations.
        # Qty of checks == (qty of units) ^ 2
        amqp_msg_counter = 1
        host_names = u.get_unit_hostnames(sentry_units)

        for dest_unit in sentry_units:
            dest_unit_name = dest_unit.info['unit_name']
            dest_unit_host = dest_unit.info['public-address']
            dest_unit_host_name = host_names[dest_unit_name]

            for check_unit in sentry_units:
                check_unit_name = check_unit.info['unit_name']
                check_unit_host = check_unit.info['public-address']
                check_unit_host_name = host_names[check_unit_name]

                amqp_msg_stamp = u.get_uuid_epoch_stamp()
                amqp_msg = ('Message {}@{} {}'.format(amqp_msg_counter,
                                                      dest_unit_host,
                                                      amqp_msg_stamp)).upper()
                # Publish amqp message
                u.log.debug('Publish message to: {} '
                            '({} {})'.format(dest_unit_host,
                                             dest_unit_name,
                                             dest_unit_host_name))

                u.publish_amqp_message_by_unit(dest_unit,
                                               amqp_msg, ssl=ssl,
                                               port=port)

                # Wait a bit before checking for message
                time.sleep(10)

                # Get amqp message
                u.log.debug('Get message from:   {} '
                            '({} {})'.format(check_unit_host,
                                             check_unit_name,
                                             check_unit_host_name))

                amqp_msg_rcvd = u.get_amqp_message_by_unit(check_unit,
                                                           ssl=ssl,
                                                           port=port)

                # Validate amqp message content
                if amqp_msg == amqp_msg_rcvd:
                    u.log.debug('Message {} received '
                                'OK.'.format(amqp_msg_counter))
                else:
                    u.log.error('Expected: {}'.format(amqp_msg))
                    u.log.error('Actual:   {}'.format(amqp_msg_rcvd))
                    msg = 'Message {} mismatch.'.format(amqp_msg_counter)
                    amulet.raise_status(amulet.FAIL, msg)

                amqp_msg_counter += 1

        # Delete the test user
        u.delete_rmq_test_user(sentry_units)

    def test_100_rmq_processes(self):
        """Verify that the expected service processes are running
        on each rabbitmq-server unit."""
        u.log.debug('Checking system services on units...')

        # Beam and epmd sometimes briefly have more than one PID,
        # Process is named 'beam' with 1 cpu; 'beam.smp' for >1 cpu.
        # True checks for at least 1.
        rmq_processes = {
            'beam.smp': True,
            'epmd': True,
        }

        # Units with process names and PID quantities expected
        expected_processes = {
            self.rmq0_sentry: rmq_processes,
            self.rmq1_sentry: rmq_processes,
            self.rmq2_sentry: rmq_processes
        }

        actual_pids = u.get_unit_process_ids(expected_processes)
        ret = u.validate_unit_process_ids(expected_processes, actual_pids)
        if ret:
            amulet.raise_status(amulet.FAIL, msg=ret)

        u.log.info('OK\n')

    def test_102_services(self):
        """Verify that the expected services are running on the
           corresponding service units."""
        services = {
            self.rmq0_sentry: ['rabbitmq-server'],
            self.rmq1_sentry: ['rabbitmq-server'],
            self.rmq2_sentry: ['rabbitmq-server'],
            self.cinder_sentry: ['cinder-scheduler',
                                 'cinder-volume'],
        }

        _release = self._get_openstack_release_string()
        if CompareOpenStackReleases(_release) >= 'ocata':
            services[self.cinder_sentry].append('apache2')
        else:
            services[self.cinder_sentry].append('cinder-api')

        ret = u.validate_services_by_name(services)
        if ret:
            amulet.raise_status(amulet.FAIL, msg=ret)

        u.log.info('OK\n')

    def test_103_clustered_attribute(self):
        """Verify the 'clustered' attribute was set in the 'cluster' relation
        for all the units"""

        for unit in self.d.sentry['rabbitmq-server']:
            rid, code = unit.run('relation-ids cluster')
            print('unit: %s , code: %s , output: %s' % (unit.info['unit_name'],
                                                        code, rid))
            assert code == 0

            clustered, code = unit.run('relation-get -r %s clustered %s'
                                       % (rid, unit.info['unit_name']))
            print('unit: %s , code: %s , output: %s' % (unit.info['unit_name'],
                                                        code, clustered))
            assert code == 0

            hostname, code = unit.run('hostname')
            print('unit: %s , code: %s , output: %s' % (unit.info['unit_name'],
                                                        code, hostname))
            assert code == 0
            assert hostname == clustered

    def test_200_rmq_cinder_amqp_relation(self):
        """Verify the rabbitmq-server:cinder amqp relation data"""
        u.log.debug('Checking rmq:cinder amqp relation data...')
        unit = self.rmq0_sentry
        relation = ['amqp', 'cinder:amqp']
        expected = {
            'private-address': u.valid_ip,
            'password': u.not_null,
            'hostname': u.valid_ip
        }
        ret = u.validate_relation_data(unit, relation, expected)
        if ret:
            msg = u.relation_error('amqp cinder', ret)
            amulet.raise_status(amulet.FAIL, msg=msg)

        u.log.info('OK\n')

    def test_201_cinder_rmq_amqp_relation(self):
        """Verify the cinder:rabbitmq-server amqp relation data"""
        u.log.debug('Checking cinder:rmq amqp relation data...')
        unit = self.cinder_sentry
        relation = ['amqp', 'rabbitmq-server:amqp']
        expected = {
            'private-address': u.valid_ip,
            'vhost': 'openstack',
            'username': u.not_null
        }
        ret = u.validate_relation_data(unit, relation, expected)
        if ret:
            msg = u.relation_error('cinder amqp', ret)
            amulet.raise_status(amulet.FAIL, msg=msg)

        u.log.info('OK\n')

    def test_202_rmq_nrpe_ext_master_relation(self):
        """Verify rabbitmq-server:nrpe nrpe-external-master relation data"""
        u.log.debug('Checking rmq:nrpe external master relation data...')
        unit = self.rmq0_sentry
        relation = ['nrpe-external-master',
                    'nrpe:nrpe-external-master']

        mon_sub = ('monitors:\n  remote:\n    nrpe:\n      rabbitmq: '
                   '{command: check_rabbitmq}\n      rabbitmq_queue: '
                   '{command: check_rabbitmq_queue}\n')

        expected = {
            'private-address': u.valid_ip,
            'monitors': mon_sub
        }

        ret = u.validate_relation_data(unit, relation, expected)
        if ret:
            msg = u.relation_error('amqp nrpe', ret)
            amulet.raise_status(amulet.FAIL, msg=msg)

        u.log.info('OK\n')

    def test_203_nrpe_rmq_ext_master_relation(self):
        """Verify nrpe:rabbitmq-server nrpe-external-master relation data"""
        u.log.debug('Checking nrpe:rmq external master relation data...')
        unit = self.nrpe_sentry
        relation = ['nrpe-external-master',
                    'rabbitmq-server:nrpe-external-master']

        expected = {
            'private-address': u.valid_ip
        }

        ret = u.validate_relation_data(unit, relation, expected)
        if ret:
            msg = u.relation_error('nrpe amqp', ret)
            amulet.raise_status(amulet.FAIL, msg=msg)

        u.log.info('OK\n')

    def test_400_rmq_cluster_running_nodes(self):
        """Verify that cluster status from each rmq juju unit shows
        every cluster node as a running member in that cluster."""
        u.log.debug('Checking that all units are in cluster_status '
                    'running nodes...')

        sentry_units = self._get_rmq_sentry_units()

        ret = u.validate_rmq_cluster_running_nodes(sentry_units)
        if ret:
            amulet.raise_status(amulet.FAIL, msg=ret)

        u.log.info('OK\n')

    def test_402_rmq_connect_with_ssl_off(self):
        """Verify successful non-ssl amqp connection to all units when
        charm config option for ssl is set False."""
        u.log.debug('Confirming that non-ssl connection succeeds when '
                    'ssl config is off...')
        sentry_units = self._get_rmq_sentry_units()
        u.add_rmq_test_user(sentry_units)
        u.configure_rmq_ssl_off(sentry_units, deployment=self)

        # Check amqp connection for all units, expect connections to succeed
        for unit in sentry_units:
            connection = u.connect_amqp_by_unit(unit, ssl=False, fatal=False)
            connection.close()

        u.delete_rmq_test_user(sentry_units)
        u.log.info('OK\n')

    def test_404_rmq_ssl_connect_with_ssl_off(self):
        """Verify unsuccessful ssl amqp connection to all units when
        charm config option for ssl is set False."""
        u.log.debug('Confirming that ssl connection fails when ssl '
                    'config is off...')
        sentry_units = self._get_rmq_sentry_units()
        u.add_rmq_test_user(sentry_units)
        u.configure_rmq_ssl_off(sentry_units, deployment=self)

        # Check ssl amqp connection for all units, expect connections to fail
        for unit in sentry_units:
            connection = u.connect_amqp_by_unit(unit, ssl=True,
                                                port=5971, fatal=False)
            if connection:
                connection.close()
                msg = 'SSL connection unexpectedly succeeded with ssl=off'
                amulet.raise_status(amulet.FAIL, msg)

        u.delete_rmq_test_user(sentry_units)
        u.log.info('OK - Confirmed that ssl connection attempt fails '
                   'when ssl config is off.')

    def test_406_rmq_amqp_messages_all_units_ssl_off(self):
        """Send amqp messages to every rmq unit and check every rmq unit
        for messages.  Standard amqp tcp port, no ssl."""
        u.log.debug('Checking amqp message publish/get on all units '
                    '(ssl off)...')

        sentry_units = self._get_rmq_sentry_units()
        self._test_rmq_amqp_messages_all_units(sentry_units, ssl=False)
        u.log.info('OK\n')

    def test_408_rmq_amqp_messages_all_units_ssl_on(self):
        """Send amqp messages with ssl enabled, to every rmq unit and
        check every rmq unit for messages.  Standard ssl tcp port."""
        # http://pad.lv/1625044
        if (CompareHostReleases(self.client_series) >= 'xenial' and
                CompareHostReleases(self.series) <= 'trusty'):
            u.log.info('SKIP')
            u.log.info('Skipping SSL tests due to client'
                       ' compatibility issues')
            return
        u.log.debug('Checking amqp message publish/get on all units '
                    '(ssl on)...')

        sentry_units = self._get_rmq_sentry_units()
        self._test_rmq_amqp_messages_all_units(sentry_units,
                                               ssl=True, port=5671)
        u.log.info('OK\n')

    def test_410_rmq_amqp_messages_all_units_ssl_alt_port(self):
        """Send amqp messages with ssl on, to every rmq unit and check
        every rmq unit for messages.  Custom ssl tcp port."""
        # http://pad.lv/1625044
        if (CompareHostReleases(self.client_series) >= 'xenial' and
                CompareHostReleases(self.series) <= 'trusty'):
            u.log.info('SKIP')
            u.log.info('Skipping SSL tests due to client'
                       ' compatibility issues')
            return
        u.log.debug('Checking amqp message publish/get on all units '
                    '(ssl on)...')

        sentry_units = self._get_rmq_sentry_units()
        self._test_rmq_amqp_messages_all_units(sentry_units,
                                               ssl=True, port=5999)
        u.log.info('OK\n')

    def test_412_rmq_management_plugin(self):
        """Enable and check management plugin."""
        u.log.debug('Checking tcp socket connect to management plugin '
                    'port on all rmq units...')

        sentry_units = self._get_rmq_sentry_units()
        mgmt_port = 15672

        # Enable management plugin
        u.log.debug('Enabling management_plugin charm config option...')
        config = {'management_plugin': 'True'}
        self.d.configure('rabbitmq-server', config)
        self.d.sentry.wait(timeout=900)
        u.rmq_wait_for_cluster(self)

        # Check tcp connect to management plugin port
        max_wait = 600
        tries = 0
        ret = u.port_knock_units(sentry_units, mgmt_port)
        while ret and tries < (max_wait / 30):
            time.sleep(30)
            u.log.debug('Attempt {}: {}'.format(tries, ret))
            ret = u.port_knock_units(sentry_units, mgmt_port)
            tries += 1

        if ret:
            amulet.raise_status(amulet.FAIL, ret)
        else:
            u.log.debug('Connect to all units (OK)\n')

        # Disable management plugin
        u.log.debug('Disabling management_plugin charm config option...')
        config = {'management_plugin': 'False'}
        self.d.configure('rabbitmq-server', config)
        self.d.sentry.wait(timeout=900)
        u.rmq_wait_for_cluster(self)

        # Negative check - tcp connect to management plugin port
        u.log.info('Expect tcp connect fail since charm config '
                   'option is disabled.')
        tries = 0
        ret = u.port_knock_units(sentry_units, mgmt_port, expect_success=False)
        while ret and tries < (max_wait / 30):
            time.sleep(30)
            u.log.debug('Attempt {}: {}'.format(tries, ret))
            ret = u.port_knock_units(sentry_units, mgmt_port,
                                     expect_success=False)
            tries += 1

        if ret:
            amulet.raise_status(amulet.FAIL, ret)
        else:
            u.log.info('Confirm mgmt port closed on all units (OK)\n')

    def test_414_rmq_nrpe_monitors(self):
        """Check rabbimq-server nrpe monitor basic functionality."""
        sentry_units = self._get_rmq_sentry_units()
        host_names = u.get_unit_hostnames(sentry_units)

        # check_rabbitmq monitor
        u.log.debug('Checking nrpe check_rabbitmq on units...')
        cmds = ['egrep -oh /usr/local.* /etc/nagios/nrpe.d/'
                'check_rabbitmq.cfg']
        ret = u.check_commands_on_units(cmds, sentry_units)
        if ret:
            amulet.raise_status(amulet.FAIL, msg=ret)

        u.log.debug('Sleeping 2ms for 1m cron job to run...')
        time.sleep(120)

        # check_rabbitmq_queue monitor
        u.log.debug('Checking nrpe check_rabbitmq_queue on units...')
        cmds = ['egrep -oh /usr/local.* /etc/nagios/nrpe.d/'
                'check_rabbitmq_queue.cfg']
        ret = u.check_commands_on_units(cmds, sentry_units)
        if ret:
            amulet.raise_status(amulet.FAIL, msg=ret)

        # check dat file existence
        u.log.debug('Checking nrpe dat file existence on units...')
        for sentry_unit in sentry_units:
            unit_name = sentry_unit.info['unit_name']
            unit_host_name = host_names[unit_name]

            cmds = [
                'stat /var/lib/rabbitmq/data/{}_general_stats.dat'.format(
                    unit_host_name),
                'stat /var/lib/rabbitmq/data/{}_queue_stats.dat'.format(
                    unit_host_name)
            ]

            ret = u.check_commands_on_units(cmds, [sentry_unit])
            if ret:
                amulet.raise_status(amulet.FAIL, msg=ret)

        u.log.info('OK\n')

    def test_415_cluster_partitioning(self):
        """Test if the cluster-partition-handling configuration is applied
        to the config file as expected."""
        u.log.debug('Checking cluster partitioning config option...')

        sentry_units = self._get_rmq_sentry_units()
        set_default = {'cluster-partition-handling': 'ignore'}
        set_alternate = {'cluster-partition-handling': 'autoheal'}

        u.log.debug('Setting cluster-partition-handling to autoheal...')
        self.d.configure('rabbitmq-server', set_alternate)
        self.d.sentry.wait(timeout=900)
        u.rmq_wait_for_cluster(self)

        cmds = ["grep autoheal /etc/rabbitmq/rabbitmq.config"]
        ret = u.check_commands_on_units(cmds, sentry_units)
        if ret:
            amulet.raise_status(amulet.FAIL, msg=ret)

        u.log.debug('Setting cluster-partition-handling back to default...')
        self.d.configure('rabbitmq-server', set_default)
        self.d.sentry.wait(timeout=900)
        u.rmq_wait_for_cluster(self)

        u.log.info('OK\n')

    def check_unit_rmq_cluster_nodes(self, sentry, unit_node_names):
        unit_name = sentry.info['unit_name']
        nodes = []
        errors = []
        str_stat = u.get_rmq_cluster_status(sentry)
        # make the interesting part of rabbitmqctl cluster_status output
        # json-parseable.
        if 'nodes,[{disc,' in str_stat:
            pos_start = str_stat.find('nodes,[{disc,') + 13
            pos_end = str_stat.find(']}]},', pos_start) + 1
            str_nodes = str_stat[pos_start:pos_end].replace("'", '"')
            nodes = json.loads(str_nodes)
        for node in nodes:
            if node not in unit_node_names:
                errors.append('Cluster registration check failed on {}: '
                              '{} should not be registered with RabbitMQ '
                              'after unit removal.\n'
                              ''.format(unit_name, node))

        return errors

    def test_901_remove_unit(self):
        """Test if a unit correctly cleans up by removing itself from the
           RabbitMQ cluster on removal"""
        u.log.debug('Checking that units correctly clean up after themselves '
                    'on unit removal...')
        configs = {'rabbitmq-server': {'min-cluster-size': '2'}}
        super(RmqBasicDeployment, self)._configure_services(configs)
        self.d.sentry.wait(timeout=900)
        u.rmq_wait_for_cluster(self)

        self.d.remove_unit(self.rmq2_sentry.info['unit_name'])
        self.d.sentry.wait(timeout=900)
        u.rmq_wait_for_cluster(self)

        sentry_units = self._get_rmq_sentry_units()[:-1]
        unit_host_names = u.get_unit_hostnames(sentry_units)
        unit_node_names = []
        for unit in unit_host_names:
            unit_node_names.append('rabbit@{}'.format(unit_host_names[unit]))
        errors = []

        for sentry in sentry_units:
            e = self.check_unit_rmq_cluster_nodes(sentry, unit_node_names)
            if e:
                # NOTE: cluster status may not have been updated yet so wait a
                # little and try one more time. Need to find a better way to do
                # this.
                time.sleep(10)
                e = self.check_unit_rmq_cluster_nodes(sentry, unit_node_names)
                if e:
                    errors.append(e)

        if errors:
            amulet.raise_status(amulet.FAIL, msg=errors)
        u.log.debug('OK')

    def test_910_pause_and_resume(self):
        """The services can be paused and resumed. """
        u.log.debug('Checking pause and resume actions...')
        assert u.status_get(self.rmq0_sentry)[0] == "active"

        action_id = u.run_action(self.rmq0_sentry, "pause")
        assert u.wait_on_action(action_id), "Pause action failed."
        assert u.status_get(self.rmq0_sentry)[0] == "maintenance"

        action_id = u.run_action(self.rmq0_sentry, "resume")
        assert u.wait_on_action(action_id), "Resume action failed."
        assert u.status_get(self.rmq0_sentry)[0] == "active"

        self.d.sentry.wait(timeout=900)
        u.rmq_wait_for_cluster(self)
        u.log.debug('OK')

    def test_911_cluster_status(self):
        """ rabbitmqctl cluster_status action can be returned. """
        u.log.debug('Checking cluster status action...')

        action_id = u.run_action(self.rmq0_sentry, "cluster-status")
        assert u.wait_on_action(action_id), "Cluster status action failed."

        u.log.debug('OK')

    def test_912_check_queues(self):
        """ rabbitmqctl check_queues action can be returned. """
        u.log.debug('Checking cluster status action...')

        action_id = u.run_action(self.rmq0_sentry, "check-queues")
        assert u.wait_on_action(action_id), "Check queues action failed."
