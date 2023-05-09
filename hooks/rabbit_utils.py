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

import copy
import json
import os
import re
import sys
import subprocess
import glob
import tempfile
import time
import shutil
import socket
import yaml
import functools

from collections import OrderedDict, defaultdict

try:
    from croniter import CroniterBadCronError
except ImportError:
    # NOTE(lourot): CroniterBadCronError doesn't exist in croniter
    # 0.3.12 and older, i.e. it doesn't exist in Bionic and older.
    # croniter used to raise a ValueError on these older versions:
    CroniterBadCronError = ValueError
from croniter import croniter

from datetime import datetime

from rabbitmq_context import (
    RabbitMQSSLContext,
    RabbitMQClusterContext,
    RabbitMQEnvContext,
    SSL_CA_FILE,
)

from charmhelpers.contrib.charmsupport import nrpe
import charmhelpers.contrib.openstack.deferred_events as deferred_events
from charmhelpers.core.templating import render

from charmhelpers.contrib.openstack.utils import (
    _determine_os_workload_status,
    get_hostname,
    pause_unit,
    resume_unit,
    is_unit_paused_set,
)

from charmhelpers.core.hookenv import (
    relation_id,
    relation_ids,
    related_units,
    relations_for_id,
    log, ERROR,
    WARNING,
    INFO, DEBUG,
    service_name,
    status_set,
    cached,
    flush,
    relation_set,
    relation_get,
    application_version_set,
    config,
    is_leader,
    leader_get,
    local_unit,
    action_name,
    charm_dir
)

from charmhelpers.core.host import (
    pwgen,
    mkdir,
    write_file,
    cmp_pkgrevno,
    rsync,
    lsb_release,
    CompareHostReleases,
    path_hash,
)

from charmhelpers.contrib.peerstorage import (
    peer_store,
    peer_retrieve,
)

from charmhelpers.fetch import (
    apt_pkg,
    apt_update,
    apt_install,
    get_upstream_version,
)

CLUSTER_MODE_KEY = 'cluster-partition-handling'
CLUSTER_MODE_FOR_INSTALL = 'ignore'
import charmhelpers.coordinator as coordinator


PACKAGES = ['rabbitmq-server', 'python3-amqplib', 'lockfile-progs',
            'python3-croniter']

VERSION_PACKAGE = 'rabbitmq-server'

RABBITMQ_CTL = '/usr/sbin/rabbitmqctl'
COOKIE_PATH = '/var/lib/rabbitmq/.erlang.cookie'
ENV_CONF = '/etc/rabbitmq/rabbitmq-env.conf'
RABBITMQ_CONFIG = '/etc/rabbitmq/rabbitmq.config'
RABBITMQ_CONF = '/etc/rabbitmq/rabbitmq.conf'
ENABLED_PLUGINS = '/etc/rabbitmq/enabled_plugins'
NRPE_USER = 'nagios'
RABBIT_USER = 'rabbitmq'
LIB_PATH = '/var/lib/rabbitmq/'
HOSTS_FILE = '/etc/hosts'
NAGIOS_PLUGINS = '/usr/local/lib/nagios/plugins'
SCRIPTS_DIR = '/usr/local/bin'
STATS_CRONFILE = '/etc/cron.d/rabbitmq-stats'
CRONJOB_CMD = ("{schedule} rabbitmq timeout -k 10s -s SIGINT {timeout} "
               "{command} 2>&1 | logger -p local0.notice\n")

COORD_KEY_RESTART = "restart"
COORD_KEY_PKG_UPGRADE = "pkg_upgrade"
COORD_KEY_CLUSTER = "cluster"
COORD_KEYS = [COORD_KEY_RESTART, COORD_KEY_PKG_UPGRADE, COORD_KEY_CLUSTER]

_named_passwd = '/var/lib/charm/{}/{}.passwd'
_local_named_passwd = '/var/lib/charm/{}/{}.local_passwd'
_service_password_glob = '/var/lib/charm/{}/*.passwd'


# hook_contexts are used as a convenient mechanism to render templates
# logically, consider building a hook_context for template rendering so
# the charm doesn't concern itself with template specifics etc.

_CONFIG_FILES = OrderedDict([
    (RABBITMQ_CONF, {
        'hook_contexts': [
            RabbitMQSSLContext(),
            RabbitMQClusterContext(),
        ],
        'services': ['rabbitmq-server']
    }),
    (RABBITMQ_CONFIG, {
        'hook_contexts': [
            RabbitMQSSLContext(),
            RabbitMQClusterContext(),
        ],
        'services': ['rabbitmq-server']
    }),
    (ENV_CONF, {
        'hook_contexts': [
            RabbitMQEnvContext(),
        ],
        'services': ['rabbitmq-server']
    }),
    (ENABLED_PLUGINS, {
        'hook_contexts': None,
        'services': ['rabbitmq-server']
    }),
])


def CONFIG_FILES():
    _cfiles = copy.deepcopy(_CONFIG_FILES)
    if cmp_pkgrevno('rabbitmq-server', '3.7') >= 0:
        del _cfiles[RABBITMQ_CONFIG]
    else:
        del _cfiles[RABBITMQ_CONF]
    return _cfiles


class NotLeaderError(Exception):
    """Exception raised if not the leader."""
    pass


class InvalidServiceUserError(Exception):
    """Exception raised if an invalid Service User is detected."""
    pass


class ConfigRenderer(object):
    """
    This class is a generic configuration renderer for
    a given dict mapping configuration files and hook_contexts.
    """
    def __init__(self, config):
        """
        :param config: see CONFIG_FILES
        :type config: dict
        """
        self.config_data = {}

        for config_path, data in config.items():
            hook_contexts = data.get('hook_contexts', None)
            if hook_contexts:
                ctxt = {}
                for svc_context in hook_contexts:
                    ctxt.update(svc_context())
                self.config_data[config_path] = ctxt

    def write(self, config_path):
        data = self.config_data.get(config_path, None)
        if data:
            log("writing config file: %s , data: %s" % (config_path,
                                                        str(data)),
                level='DEBUG')

            render(os.path.basename(config_path), config_path,
                   data, perms=0o644)

    def write_all(self):
        """Write all the defined configuration files"""
        for service in self.config_data.keys():
            self.write(service)

    def complete_contexts(self):
        return []


class RabbitmqError(Exception):
    pass


def run_cmd(cmd):
    """Run provided command and decode the output.

    :param cmd: Command to run
    :type cmd: List[str]
    :returns: output from command
    :rtype: str
    """
    output = subprocess.check_output(cmd)
    output = output.decode('utf-8')
    return output


def rabbit_supports_json():
    """Check if version of rabbit supports json formatted output.

    :returns: If json output is supported.
    :rtype: bool
    """
    return caching_cmp_pkgrevno('rabbitmq-server', '3.8.2') >= 0


@cached
def caching_cmp_pkgrevno(package, revno, pkgcache=None):
    """Compare supplied revno with the revno of the installed package.

    *  1 => Installed revno is greater than supplied arg
    *  0 => Installed revno is the same as supplied arg
    * -1 => Installed revno is less than supplied arg

    :param package: Package to check revno of
    :type package: str
    :param revno: Revision number to compare against
    :type revno: str
    :param pkgcache: Version obj from pkgcache
    :type pkgcache: ubuntu_apt_pkg.Version
    :returns: Whether versions match
    :rtype: int
    """
    return cmp_pkgrevno(package, revno, pkgcache)


def query_rabbit(cmd, raw_processor=None, json_processor=None,
                 binary=RABBITMQ_CTL):
    """Run query against rabbit.

    Run query against rabbit and then run post-query processor on the
    output. If the version of rabbit that is installed supports formatting
    the output in json format then the '--formatter=json' flag is added.

    :param cmd: Query to run
    :type cmd: List[str]
    :param raw_processor: Function to call with command output as the only
                          argument.
    :type raw_processor: Callable
    :param json_processor: Function to call with json loaded output as the only
                          argument.
    :type json_processor: Callable
    :returns: Return processed output from query
    :rtype: ANY
    """
    cmd.insert(0, binary)
    if rabbit_supports_json():
        cmd.append('--formatter=json')
        output = json.loads(run_cmd(cmd))
        if json_processor:
            return json_processor(output)
        else:
            # A processor may not be needed for loaded json.
            return output
    else:
        if raw_processor:
            return raw_processor(run_cmd(cmd))
        else:
            raise NotImplementedError


def list_vhosts():
    """Returns a list of all the available vhosts

    :returns: List of vhosts
    :rtype: [str]
    """
    def _json_processor(output):
        return [ll['name'] for ll in output]

    def _raw_processor(output):
        if '...done' in output:
            return output.split('\n')[1:-2]
        else:
            return output.split('\n')[1:-1]

    try:
        return query_rabbit(
            ['list_vhosts'],
            raw_processor=_raw_processor,
            json_processor=_json_processor)
    except Exception as ex:
        # if no vhosts, just raises an exception
        log(str(ex), level='DEBUG')
        return []


def list_vhost_queue_info(vhost):
    """Provide a list of queue info objects for the given vhost.

    :returns: List of dictionaries of queue information
              eg [{'name': 'queue name', 'messages': 0, 'consumers': 1}, ...]
    :rtype: List[Dict[str, Union[str, int]]]
    :raises: CalledProcessError
    """
    def _raw_processor(output):
        queue_info = []
        if '...done' in output:
            queues = output.split('\n')[1:-2]
        else:
            queues = output.split('\n')[1:-1]

        for queue in queues:
            [qname, qmsgs, qconsumers] = queue.split()
            queue_info.append({
                'name': qname,
                'messages': int(qmsgs),
                'consumers': int(qconsumers)
            })

        return queue_info

    cmd = ['-p', vhost, 'list_queues', 'name', 'messages', 'consumers']
    return query_rabbit(
        cmd,
        raw_processor=_raw_processor)


def list_users():
    """Returns a list of users.

    :returns: List of users
    :rtype: [str]
    """
    def _json_processor(output):
        return [ll['user'] for ll in output]

    def _raw_processor(output):
        lines = output.split('\n')[1:]
        return [line.split('\t')[0] for line in lines]

    return query_rabbit(
        ['list_users'],
        raw_processor=_raw_processor,
        json_processor=_json_processor)


def list_user_tags(user):
    """Get list of tags for user.

    :param user: Name of user to get tags for
    :type user: str
    :returns: List of tags associated with user.
    :rtype: [str]
    :raises: NotImplementedError
    """
    all_tags = query_rabbit(['list_users'])
    users = [
        tag_dict['tags']
        for tag_dict in all_tags
        if tag_dict['user'] == user]
    # The datastructure returned by rabbitctl is a list of dicts in the
    # form [{'user': 'testuser1', 'tags': []}, ...] so it is possible
    # for there to be multiple dicts which apply to the same user. Handle
    # this unlikely event by merging the lists.
    result = sum(users, [])
    result = sorted(list(set(result)))
    return result


def list_user_permissions(user):
    """Get list of user permissions.

    :param user: Name of user to get permissions for
    :type user: str
    :returns: List of dictionaries e.g. [{'vhost': 'vhost-name',
                                          'configure': '.*',
                                          'write': '.*',
                                          'read': '.*'},...]
    :rtype: List[Dict[str,str]]
    :raises: NotImplementedError
    """
    return query_rabbit(['list_user_permissions', user])


def list_user_vhost_permissions(user, vhost):
    """Get list of user permissions for a vhost.

    :param user: Name of user to get permissions for
    :type user: str
    :param vhost: Name of vhost to get permissions for
    :type vhost: str
    :returns: List of dictionaries e.g. [{'configure': '.*',
                                          'write': '.*',
                                          'read': '.*'},...]
    :rtype: List[Dict[str,str]]
    :raises: NotImplementedError
    """
    perms = list_user_permissions(user)
    vhost_perms = [
        {
            'configure': tag_dict['configure'],
            'write': tag_dict['write'],
            'read': tag_dict['read']}
        for tag_dict in perms
        if tag_dict['vhost'] == vhost]
    return vhost_perms


def list_plugins():
    """List plugins.

    Return a list of dictionaries relating to each plugin.

    :returns: List of dictionaries e.g.
        [
            {
                'enabled': str,
                'name': str,
                'running': bool,
                'running_version': int,
                'version': [int, int, ...]},
            ...
        ]
    :rtype: List[Dict[str, Union[str, bool, int, List[int]]]]
    """
    def _json_processor(output):
        return output['plugins']
    return query_rabbit(
        ['list'],
        json_processor=_json_processor,
        binary=get_plugin_manager())


def list_policies(vhost):
    """List policies for a vhost.

    Return a list of dictionaries relating to each policy.
    [
        {
           'apply-to': str,
           'definition': str,
           'name': str,
           'pattern': str,
           'priority': int,
           'vhost': str}
        ...]

    :param vhost: Name of vhost to get policies for
    :type vhost: str
    :returns: List of dictionaries e.g.
    :rtype: List[Dict[str, Union[str, int]]]
    """
    return query_rabbit(['list_policies', '-p', vhost])


def get_vhost_policy(vhost, policy_name):
    """Get a policy with a given name on a given vhost.

    Return policy:

        {
           'apply-to': str,
           'definition': str,
           'name': str,
           'pattern': str,
           'priority': int,
           'vhost': str}

    :param vhost: Name of vhost to get policies for
    :type vhost: str
    :param policy_name: Name of policy
    :type policy_name: str
    :returns: Policy dict.
    :rtype: Union[Dict[str, Union[str, int]], None]
    """
    policies = [
        p for p in list_policies(vhost)
        if p['name'] == policy_name]
    if len(policies) > 0:
        return policies[0]
    else:
        return None


def list_enabled_plugins():
    """Get list of enabled plugins.

    :returns: List of enabled plugins
    :rtype: [str]
    :raises: NotImplementedError
    """
    enabled_plugins = [
        pdict['name']
        for pdict in list_plugins()
        if pdict['enabled'] in ['enabled', 'implicit']]
    log("Enabled plugins: {}".format(enabled_plugins))
    return sorted(list(set(enabled_plugins)))


def vhost_queue_info(vhost):
    return list_vhost_queue_info(vhost)


def vhost_exists(vhost):
    return vhost in list_vhosts()


def create_vhost(vhost):
    if vhost_exists(vhost):
        return
    rabbitmqctl('add_vhost', vhost)
    log('Created new vhost (%s).' % vhost)


def user_exists(user):
    return user in list_users()


def apply_tags(user, tags):
    """Apply tags to user if needed.

    Apply tags to user. Any existing tags will be removed.

    :param user: Name of user to apply tags to.
    :type user: str
    :param tags: Tags to apply to user.
    :type tags: List[str]
    :raises: NotImplementedError
    """
    log('Adding tags [{}] to user {}'.format(
        ', '.join(tags),
        user
    ))
    try:
        existing_user_tags = list_user_tags(user)
        if existing_user_tags == sorted(list(set(tags))):
            log("User {} already has tags {}".format(user, tags))
        else:
            log("Existing user tags for {} are {} which do not match {}. "
                "Updating tags.".format(user, existing_user_tags, tags))
            rabbitmqctl('set_user_tags', user, ' '.join(tags))
    except NotImplementedError:
        # Cannot cheeck existing tags so apply them to be on thee
        # safe side.
        log("Cannot retrieve existing tags for {}".format(user))
        rabbitmqctl('set_user_tags', user, ' '.join(tags))


def create_user(user, password, tags=[]):
    exists = user_exists(user)

    if not exists:
        log('Creating new user (%s).' % user)
        rabbitmqctl('add_user', user, password)

    if 'administrator' in tags:
        log('Granting admin access to {}'.format(user))

    apply_tags(user, tags)


def change_user_password(user, new_password):
    """Change the password of the rabbitmq user.

    :param user: the user to change; must exist in the rabbitmq instance.
    :type user: str
    :param new_password: the password to change to.
    :type new_password: str
    :raises KeyError: if the user doesn't exist.
    """
    exists = user_exists(user)
    if not exists:
        msg = "change_user_password: user '{}' doesn't exist.".format(user)
        log(msg, ERROR)
        raise KeyError(msg)
    rabbitmqctl('change_password', user, new_password)
    log("Changed password on rabbitmq for user: {}".format(user), INFO)


def grant_permissions(user, vhost):
    """Grant all permissions on a vhost to a user.

    :param user: Name of user to give permissions to.
    :type user: str
    :param vhost: Name of vhost to give permissions on
    :type vhost: str
    """
    log(
        "Granting permissions for user {} on vhost {}".format(user, vhost),
        level='DEBUG')
    try:
        vhost_perms = list_user_vhost_permissions(user, vhost)
        if len(vhost_perms) > 1:
            # This will almost certainly never happen but handle it just in
            # case.
            log('Multiple permissions found for the same user and vhost. '
                'Resetting permission.')
            apply_perms = True
        elif len(vhost_perms) == 1:
            perms = vhost_perms[0]
            apply_perms = False
            for attr in ['configure', 'write', 'read']:
                if perms[attr] != '.*':
                    log(
                        'Permissions for user {} on vhost {} to {} are {} '
                        'not {}'.format(user, vhost, attr, perms[attr], '.*'))
                    apply_perms = True
        else:
            apply_perms = True
    except NotImplementedError:
        apply_perms = True
    if apply_perms:
        rabbitmqctl('set_permissions', '-p',
                    vhost, user, '.*', '.*', '.*')
    else:
        log('No permissions update needed for user {} on vhost {}'.format(
            user, vhost))


def compare_policy(vhost, policy_name, pattern, definition, priority=None,
                   apply_to=None):
    """Check a policy matches the supplied attributes.

    :param vhost: Name of vhost to find policy in
    :type vhost: str
    :param policy_name: Name of policy
    :type policy_name: str
    :param pattern: The regular expression, which when matches on a given
                    resources causes the policy to apply.
    :type pattern: str
    :param definition: The definition of the policy, as a JSON term.
    :type definition: str
    :param priority: The priority of the policy as an integer.
    :type priority: int
    :param apply_to: Which types of object this policy should apply to.
                     Possible values are: queues exchanges all
    :type apply_to: str
    :returns: Whether they match
    :rtype: bool
    """
    policy = get_vhost_policy(vhost, policy_name)
    # Convert unset options to defaults
    # https://www.rabbitmq.com/rabbitmqctl.8.html#set_policy
    if priority is None:
        priority = 0
    if apply_to is None:
        apply_to = 'all'
    if not policy:
        log("Old policy not found.")
        return False
    if policy.get('pattern') != pattern:
        log("Policy pattern does not match, {} != {}".format(
            policy.get('pattern'),
            pattern))
        return False
    if policy.get('priority') != int(priority):
        log("Policy priority does not match, {} != {}".format(
            policy.get('priority'),
            priority))
        return False
    if policy.get('apply-to') != apply_to:
        log("Policy apply_to does not match, {} != {}".format(
            policy.get('apply-to'),
            apply_to))
        return False
    existing_definition = json.loads(policy['definition'])
    new_definition = json.loads(definition)
    if existing_definition != new_definition:
        log("Policy definition does not match, {} != {}".format(
            policy.get('existing_definition'),
            new_definition))
        return False
    log("Policies match")
    return True


def set_policy(vhost, policy_name, pattern, definition, priority=None,
               apply_to=None):
    """Apply a policy

    :param vhost: Name of vhost to apply policy to
    :type vhost: str
    :param policy_name: Name of policy
    :type policy_name: str
    :param pattern: The regular expression, which when matches on a given
                    resources causes the policy to apply.
    :type pattern: str
    :param definition: The definition of the policy, as a JSON term.
    :type definition: str
    :param priority: The priority of the policy as an integer.
    :type priority: int
    :param apply_to: Which types of object this policy should apply to.
                     Possible values are: queues exchanges all
    :type apply_to: str
    """
    try:
        if compare_policy(vhost, policy_name, pattern, definition, priority,
                          apply_to):
            log("{} on vhost {} matched proposed policy, no update "
                "needed.".format(policy_name, vhost))
            policy_update_needed = False
        else:
            log("{} on vhost {} did not match proposed policy, update "
                "needed.".format(policy_name, vhost))
            policy_update_needed = True

    except NotImplementedError:
        log("Could not query exiting policies. Assuming update of {} on vhost "
            "{} is needed.".format(policy_name, vhost))
        policy_update_needed = True

    if policy_update_needed:
        log("{} on vhost {} required update.".format(policy_name, vhost))
        log("setting policy", level='DEBUG')
        cmd = ['set_policy', '-p', vhost]
        if priority:
            cmd.extend(['--priority', priority])
        if apply_to:
            cmd.extend(['--apply-to', apply_to])
        cmd.extend([policy_name, pattern, definition])
        rabbitmqctl(*cmd)


def clear_policy(vhost, policy_name):
    """Remove a policy

    :param vhost: Name of vhost to remove policy from
    :type vhost: str
    :param policy_name: Name of policy
    :type policy_name: str
    """
    try:
        policy = get_vhost_policy(vhost, policy_name)
        if policy:
            policy_update_needed = True
        else:
            log("{} on vhost {} not found, do not need to clear it.".format(
                policy_name,
                vhost))
            policy_update_needed = False
    except NotImplementedError:
        log("Policy lookup failed for {} on vhost {} not.".format(
            policy_name,
            vhost))
        policy_update_needed = True
    if policy_update_needed:
        rabbitmqctl('clear_policy', '-p', vhost, policy_name)


def set_ha_mode(vhost, mode, params=None, sync_mode='automatic'):
    """Valid mode values:

      * 'all': Queue is mirrored across all nodes in the cluster. When a new
         node is added to the cluster, the queue will be mirrored to that node.
      * 'exactly': Queue is mirrored to count nodes in the cluster.
      * 'nodes': Queue is mirrored to the nodes listed in node names

    More details at http://www.rabbitmq.com./ha.html

    :param vhost: virtual host name
    :param mode: ha mode
    :param params: values to pass to the policy, possible values depend on the
                   mode chosen.
    :param sync_mode: when `mode` is 'exactly' this used to indicate how the
                      sync has to be done
                      http://www.rabbitmq.com./ha.html#eager-synchronisation
    """
    if mode == 'all':
        definition = {
            "ha-mode": "all",
            "ha-sync-mode": sync_mode}
    elif mode == 'exactly':
        definition = {
            "ha-mode": "exactly",
            "ha-params": params,
            "ha-sync-mode": sync_mode}
    elif mode == 'nodes':
        definition = {
            "ha-mode": "nodes",
            "ha-params": params,
            "ha-sync-mode": sync_mode}
    else:
        raise RabbitmqError(("Unknown mode '%s', known modes: "
                             "all, exactly, nodes"))

    log("Setting HA policy to vhost '%s'" % vhost, level='INFO')
    set_policy(vhost, 'HA', r'^(?!amq\.).*', json.dumps(definition))


def clear_ha_mode(vhost, name='HA', force=False):
    """
    Clear policy from the `vhost` by `name`
    """
    log("Clearing '%s' policy from vhost '%s'" % (name, vhost), level='INFO')
    try:
        clear_policy(vhost, name)
    except subprocess.CalledProcessError as ex:
        if not force:
            raise ex


def set_all_mirroring_queues(enable):
    """
    :param enable: if True then enable mirroring queue for all the vhosts,
                   otherwise the HA policy is removed
    """
    if enable:
        status_set('active', 'Checking queue mirroring is enabled')
    else:
        status_set('active', 'Checking queue mirroring is disabled')

    for vhost in list_vhosts():
        if enable:
            set_ha_mode(vhost, 'all')
        else:
            clear_ha_mode(vhost, force=True)


def rabbitmqctl(action, *args):
    ''' Run rabbitmqctl with action and args. This function uses
        subprocess.check_call. For uses that need check_output
        use a direct subprocess call or rabbitmqctl_normalized_output
        function.
     '''
    # NOTE(lourot): before rabbitmq-server 3.8 (focal),
    # `rabbitmqctl wait <pidfile>` doesn't have a `--timeout` option and thus
    # may hang forever and needs to be wrapped in
    # `timeout 180 rabbitmqctl wait <pidfile>`.
    # Since 3.8 there is a `--timeout` option, whose default is 10 seconds. [1]
    #
    # [1]: https://github.com/rabbitmq/rabbitmq-server/commit/3dd58ae1
    WAIT_TIMEOUT_SECONDS = 180
    focal_or_newer = rabbitmq_version_newer_or_equal('3.8')

    cmd = []
    if 'wait' in action and not focal_or_newer:
        cmd.extend(['timeout', str(WAIT_TIMEOUT_SECONDS)])
    cmd.extend([RABBITMQ_CTL, action])
    for arg in args:
        cmd.append(arg)
    if 'wait' in action and focal_or_newer:
        cmd.extend(['--timeout', str(WAIT_TIMEOUT_SECONDS)])
    log("Running {}".format(cmd), 'DEBUG')
    subprocess.check_call(cmd)


def configure_notification_ttl(vhost, ttl=3600000):
    ''' Configure 1h minute TTL for notfication topics in the provided vhost
        This is a workaround for filling notification queues in OpenStack
        until a more general service discovery mechanism exists so that
        notifications can be enabled/disabled on each individual service.
    '''
    set_policy(
        vhost,
        'TTL',
        '^(versioned_)?notifications.*',
        '{{"message-ttl":{ttl}}}'.format(ttl=ttl),
        priority='1',
        apply_to='queues')


def configure_ttl(vhost, ttlname, ttlreg, ttl):
    ''' Some topic queues like in heat also need to set TTL, see lp:1925436
        Configure TTL for heat topics in the provided vhost, this is a
        workaround for filling heat queues and for future other queues
    '''
    log('configure_ttl: ttlname={} ttlreg={} ttl={}'.format(
        ttlname, ttlreg, ttl), INFO)
    if not all([ttlname, ttlreg, ttl]):
        return
    set_policy(
        vhost,
        '{ttlname}'.format(ttlname=ttlname),
        '{ttlreg}'.format(ttlreg=ttlreg),
        '{{"expires":{ttl}}}'.format(ttl=ttl),
        priority='1',
        apply_to='queues')


def rabbitmqctl_normalized_output(*args):
    ''' Run rabbitmqctl with args. Normalize output by removing
        whitespace and return it to caller for further processing.
    '''
    cmd = [RABBITMQ_CTL]
    cmd.extend(args)
    out = (subprocess
           .check_output(cmd, stderr=subprocess.STDOUT)
           .decode('utf-8'))

    # Output is in Erlang External Term Format (ETF).  The amount of whitespace
    # (including newlines in the middle of data structures) in the output
    # depends on the data presented.  ETF resembles JSON, but it is not.
    # Writing our own parser is a bit out of scope, enabling management-plugin
    # to use REST interface might be overkill at this stage.
    #
    # Removing whitespace will let our simple pattern matching work and is a
    # compromise.
    return out.translate(str.maketrans(dict.fromkeys(' \t\n')))


def wait_app():
    ''' Wait until rabbitmq has fully started '''
    run_dir = '/var/run/rabbitmq/'
    if os.path.isdir(run_dir):
        pid_file = run_dir + 'pid'
    else:
        pid_file = '/var/lib/rabbitmq/mnesia/rabbit@' \
                   + socket.gethostname() + '.pid'
    log('Waiting for rabbitmq app to start: {}'.format(pid_file), DEBUG)
    try:
        rabbitmqctl('wait', pid_file)
        log('Confirmed rabbitmq app is running', DEBUG)
        return True
    except subprocess.CalledProcessError as ex:
        status_set('blocked', 'RabbitMQ failed to start')
        try:
            status_cmd = ['rabbitmqctl', 'status']
            log(subprocess.check_output(status_cmd).decode('utf-8'), DEBUG)
        except Exception:
            pass
        raise ex


def start_app():
    ''' Start the rabbitmq app and wait until it is fully started '''
    status_set('maintenance', 'Starting rabbitmq application')
    rabbitmqctl('start_app')
    wait_app()


def join_cluster(node):
    ''' Join cluster with node '''
    if cmp_pkgrevno('rabbitmq-server', '3.0.1') >= 0:
        cluster_cmd = 'join_cluster'
    else:
        cluster_cmd = 'cluster'
    status_set('maintenance',
               'Clustering with remote rabbit host (%s).' % node)
    rabbitmqctl('stop_app')
    # Intentionally using check_output so we can see rabbitmqctl error
    # message if it fails
    cmd = [RABBITMQ_CTL, cluster_cmd, node]
    subprocess.check_output(cmd, stderr=subprocess.STDOUT)
    start_app()
    log('Host clustered with %s.' % node, 'INFO')


def clustered_with_leader():
    """Whether this unit is clustered with the leader

    :returns: Whether this unit is clustered with the leader
    :rtype: bool
    """
    node = leader_node()
    if node:
        return node in running_nodes()
    status_set('waiting', 'Leader not available for clustering')
    return False


def update_peer_cluster_status():
    """Inform peers that this unit is clustered if it is."""
    # clear the nodes cache so that the rabbit nodes are re-evaluated if the
    # cluster has formed during the hook execution.
    clear_nodes_cache()
    # check the leader and try to cluster with it
    if clustered_with_leader():
        log('Host already clustered with %s.' % leader_node())

        cluster_rid = relation_id('cluster', local_unit())
        is_clustered = relation_get(attribute='clustered',
                                    rid=cluster_rid,
                                    unit=local_unit())

        log('am I clustered?: %s' % bool(is_clustered), level=DEBUG)
        if not is_clustered:
            # NOTE(freyes): this node needs to be marked as clustered, it's
            # part of the cluster according to 'rabbitmqctl cluster_status'
            # (LP: #1691510)
            log("Setting 'clustered' on 'cluster' relation", level=DEBUG)
            relation_set(relation_id=cluster_rid,
                         clustered=get_unit_hostname(),
                         timestamp=time.time())
        else:
            log("Already set 'clustered' on cluster relation", level=DEBUG)
    else:
        log('Host not clustered with leader', level=DEBUG)


def join_leader():
    """Attempt to cluster with leader.

    Attempt to cluster with leader.
    """
    if is_unit_paused_set():
        log("Do not run cluster_with while unit is paused", "WARNING")
        return
    if clustered_with_leader():
        log("Unit already clustered with leader", "DEBUG")
    else:
        log("Attempting to cluster with leader", "INFO")
        try:
            node = leader_node()
            if node is None:
                log("Couldn't identify leader", "INFO")
                status_set('blocked', "Leader not yet identified")
                return
            join_cluster(node)
            # NOTE: toggle the cluster relation to ensure that any peers
            #       already clustered re-assess status correctly
            update_peer_cluster_status()
        except subprocess.CalledProcessError as e:
            status_set('blocked', 'Failed to cluster with %s. Exception: %s'
                       % (node, e))


def check_cluster_memberships():
    """Check for departed nodes.

    Iterate over RabbitMQ node list, compare it to charm cluster relationships,
    and notify about any nodes previously abruptly removed from the cluster.

    :returns: String node name or None
    :rtype: Union[str, None]
    """
    for rid in relation_ids('cluster'):
        for node in nodes():
            if not any(rel.get('clustered', None) == node.split('@')[1]
                       for rel in relations_for_id(relid=rid)) and \
                    node not in running_nodes():
                log("check_cluster_memberships(): '{}' in nodes but not in "
                    "charm relations or running_nodes."
                    .format(node), level=DEBUG)
                return node


def leave_cluster():
    ''' Leave cluster gracefully '''
    try:
        rabbitmqctl('stop_app')
        rabbitmqctl('reset')
        start_app()
        log('Successfully left cluster gracefully.')
    except Exception:
        # error, no nodes available for clustering
        log('Cannot leave cluster, we might be the last disc-node in the '
            'cluster.', level=ERROR)
        raise


def get_plugin_manager():
    """Find the path to the executable for managing plugins.

    :returns: Path to rabbitmq-plugins executable
    :rtype: str
    """
    # At version 3.8.2, only /sbin/rabbitmq-plugins can enable plugin correctly
    if os.path.exists("/sbin/rabbitmq-plugins"):
        return '/sbin/rabbitmq-plugins'
    else:
        return glob.glob(
            '/usr/lib/rabbitmq/lib/rabbitmq_server-*/sbin/rabbitmq-plugins')[0]


def _manage_plugin(plugin, action):
    os.environ['HOME'] = '/root'
    plugin_manager = get_plugin_manager()
    subprocess.check_call([plugin_manager, action, plugin])


def enable_plugin(plugin):
    try:
        enabled_plugins = list_enabled_plugins()
        if plugin in enabled_plugins:
            log("Plugin {} already in list of enabled plugins {}. Do not "
                "need to enable it.".format(plugin, enabled_plugins))
        else:
            log("Enabling plugin {}".format(plugin))
            _manage_plugin(plugin, 'enable')
    except (NotImplementedError, subprocess.CalledProcessError):
        log("Cannot get list of plugins, running enable just in "
            "case.")
        _manage_plugin(plugin, 'enable')


def disable_plugin(plugin):
    try:
        enabled_plugins = list_enabled_plugins()
        if plugin in enabled_plugins:
            log("Disabling plugin {}".format(plugin))
            _manage_plugin(plugin, 'disable')
        else:
            log("Plugin {} not in list of enabled plugins {}. Do not need to "
                "disable it.".format(plugin, enabled_plugins))
    except (NotImplementedError, subprocess.CalledProcessError):
        log("Cannot get list of plugins, running disable just in "
            "case.")
        _manage_plugin(plugin, 'disable')


def get_managment_port():
    if rabbitmq_version_newer_or_equal('3'):
        return 15672
    else:
        return 55672


def execute(cmd, die=False, echo=False):
    """ Executes a command

    if die=True, script will exit(1) if command does not return 0
    if echo=True, output of command will be printed to stdout

    returns a tuple: (stdout, stderr, return code)
    """
    p = subprocess.Popen(cmd.split(" "),
                         stdout=subprocess.PIPE,
                         stdin=subprocess.PIPE,
                         stderr=subprocess.PIPE)
    stdout = ""
    stderr = ""

    def print_line(ll):
        if echo:
            print(ll.strip('\n'))
            sys.stdout.flush()

    for ll in iter(p.stdout.readline, ''):
        print_line(ll)
        stdout += ll
    for ll in iter(p.stderr.readline, ''):
        print_line(ll)
        stderr += ll

    p.communicate()
    rc = p.returncode

    if die and rc != 0:
        log("command %s return non-zero." % cmd, level=ERROR)
    return (stdout, stderr, rc)


def get_rabbit_password_on_disk(username, password=None, local=False):
    ''' Retrieve, generate or store a rabbit password for
    the provided username on disk'''
    if local:
        _passwd_file = _local_named_passwd.format(service_name(), username)
    else:
        _passwd_file = _named_passwd.format(service_name(), username)

    _password = None
    if os.path.exists(_passwd_file):
        with open(_passwd_file, 'r') as passwd:
            _password = passwd.read().strip()
    else:
        mkdir(os.path.dirname(_passwd_file), owner=RABBIT_USER,
              group=RABBIT_USER, perms=0o775)
        os.chmod(os.path.dirname(_passwd_file), 0o775)
        _password = password or pwgen(length=64)
        write_file(_passwd_file, _password, owner=RABBIT_USER,
                   group=RABBIT_USER, perms=0o660)

    return _password


def migrate_passwords_to_peer_relation():
    '''Migrate any passwords storage on disk to cluster peer relation'''
    for f in glob.glob(_service_password_glob.format(service_name())):
        _key = os.path.basename(f)
        with open(f, 'r') as passwd:
            _value = passwd.read().strip()
        try:
            peer_store(_key, _value)
            os.unlink(f)
        except ValueError:
            # NOTE cluster relation not yet ready - skip for now
            pass


def get_usernames_for_passwords_on_disk():
    """Return a list of usernames that have passwords on the disk.

    Note this is only for non local passwords (i.e. that end in .passwd)

    :returns: the list of usernames with passwords on the disk.
    :rtype: List[str]
    """
    return [
        os.path.splitext(os.path.basename(f))[0]
        for f in glob.glob(_service_password_glob.format(service_name()))]


def get_usernames_for_passwords():
    """Return a list of usernames that have passwords.

    This checks BOTH the peer relationship (leader-storage, or the fallback to
    the 'cluster' relation) and on disk.  If the peer storage has usernames,
    ignore the ones on disk (as they have already been migrated), otherwise
    return the ones on disk.

    The keys that have passwords in peer storage end with .passwd.

    :returns: the list of usernames that have had passwords set.
    :rtype: List[str]
    """
    # first get from leader settings/peer relation, if available
    peer_keys = None
    try:
        peer_keys = peer_retrieve(None)
    except ValueError:
        pass
    if peer_keys is None:
        peer_keys = {}
    usernames = set(u[:-7] for u in peer_keys.keys() if u.endswith(".passwd"))
    # if usernames were found in peer storage, return them.
    if usernames:
        return sorted(usernames)
    # otherwise, return the ones on disk, if any
    return sorted(get_usernames_for_passwords_on_disk())


def get_rabbit_password(username, password=None, local=False):
    ''' Retrieve, generate or store a rabbit password for
    the provided username using peer relation cluster'''
    if local:
        return get_rabbit_password_on_disk(username, password, local)
    else:
        migrate_passwords_to_peer_relation()
        _key = '{}.passwd'.format(username)
        try:
            _password = peer_retrieve(_key)
            if _password is None:
                _password = password or pwgen(length=64)
                peer_store(_key, _password)
        except ValueError:
            # cluster relation is not yet started, use on-disk
            _password = get_rabbit_password_on_disk(username, password)
        return _password


def update_hosts_file(map):
    """Rabbitmq does not currently like ipv6 addresses so we need to use dns
    names instead. In order to make them resolvable we ensure they are  in
    /etc/hosts.

    """
    with open(HOSTS_FILE, 'r') as hosts:
        lines = hosts.readlines()

    log("Updating hosts file with: %s (current: %s)" % (map, lines),
        level=INFO)

    newlines = []
    for ip, hostname in map.items():
        if not ip or not hostname:
            continue

        keepers = []
        for line in lines:
            _line = line.split()
            if len(line) < 2 or not (_line[0] == ip or hostname in _line[1:]):
                keepers.append(line)
            else:
                log("Removing line '%s' from hosts file" % (line))

        lines = keepers
        newlines.append("%s %s\n" % (ip, hostname))

    lines += newlines

    with tempfile.NamedTemporaryFile(delete=False) as tmpfile:
        with open(tmpfile.name, 'w') as hosts:
            for line in lines:
                hosts.write(line)

    shutil.move(tmpfile.name, HOSTS_FILE)
    os.chmod(HOSTS_FILE, 0o644)


def restart_map():
    '''Determine the correct resource map to be passed to
    charmhelpers.core.restart_on_change() based on the services configured.

    :returns: dict: A dictionary mapping config file to lists of services
                    that should be restarted when file changes.
    '''
    _map = []
    for f, ctxt in _CONFIG_FILES.items():
        svcs = []
        for svc in ctxt['services']:
            svcs.append(svc)
        if svcs:
            _map.append((f, svcs))
    return OrderedDict(_map)


def services():
    ''' Returns a list of services associate with this charm '''
    _services = []
    for v in restart_map().values():
        _services = _services + v
    return list(set(_services))


def get_cluster_status(cmd_timeout=None):
    """Raturn rabbit cluster status

    :param cmd_timeout: How long to give the command to complete.
    :type cmd_timeout: int
    :returns: Rabbitmq cluster status
    :rtype: dict
    :raises: NotImplementedError, subprocess.TimeoutExpired,
    """
    if caching_cmp_pkgrevno('rabbitmq-server', '3.8.2') >= 0:
        cmd = [RABBITMQ_CTL, 'cluster_status', '--formatter=json']
        output = subprocess.check_output(
            cmd,
            timeout=cmd_timeout).decode('utf-8')
        return json.loads(output)
    else:
        # rabbitmqctl has not implemented the formatter option.
        raise NotImplementedError


@cached
def nodes(get_running=False):
    ''' Get list of nodes registered in the RabbitMQ cluster '''
    # NOTE(ajkavanagh): In focal and above, rabbitmq-server now has a
    # --formatter option.
    try:
        status = get_cluster_status()
        if get_running:
            return status['running_nodes']
        return status['disk_nodes'] + status['ram_nodes']
    except NotImplementedError:
        out = rabbitmqctl_normalized_output('cluster_status')
        cluster_status = {}
        for m in re.finditer(r"{([^,]+),(?!\[{)\[([^\]]*)", out):
            state = m.group(1)
            items = m.group(2).split(',')
            items = [x.replace("'", '').strip() for x in items]
            cluster_status.update({state: items})

        if get_running:
            return cluster_status.get('running_nodes', [])

        return cluster_status.get('disc', []) + cluster_status.get('ram', [])


@cached
def is_partitioned():
    """Check whether rabbitmq cluster is partitioned.

    :returns: Whether cluster is partitioned
    :rtype: bool
    :raises: NotImplementedError, subprocess.TimeoutExpired,
    """
    status = get_cluster_status(cmd_timeout=60)
    return status.get('partitions') != {}


@cached
def running_nodes():
    ''' Determine the current set of running nodes in the RabbitMQ cluster '''
    _nodes = nodes(get_running=True)
    log("running_nodes: {}".format(_nodes), DEBUG)
    return _nodes


@cached
def leader_node():
    ''' Provide the leader node for clustering

    @returns leader node's hostname or None
    '''
    # Each rabbitmq node should join_cluster with the leader
    # to avoid split-brain clusters.
    try:
        leader_node_hostname = peer_retrieve('leader_node_hostname')
    except ValueError:
        # This is a single unit
        log("leader_node: None", DEBUG)
        return None
    if leader_node_hostname:
        log("leader_node: rabbit@{}".format(leader_node_hostname), DEBUG)
        return "rabbit@" + leader_node_hostname
    else:
        log("leader_node: None", DEBUG)
        return None


def clear_nodes_cache():
    """Clear the running_nodes() and nodes() and leader_node() cache"""
    log("Clearing the cache for nodes.", DEBUG)
    flush('leader_node')
    flush('running_nodes')
    flush('nodes')
    flush('clustered')


def get_node_hostname(ip_addr):
    ''' Resolve IP address to hostname '''
    try:
        nodename = get_hostname(ip_addr, fqdn=False)
    except Exception:
        log('Cannot resolve hostname for %s using DNS servers' % ip_addr,
            level=WARNING)
        log('Falling back to use socket.gethostname()',
            level=WARNING)
        # If the private-address is not resolvable using DNS
        # then use the current hostname
        nodename = socket.gethostname()
    log('local nodename: %s' % nodename, level=INFO)
    return nodename


@cached
def clustered():
    ''' Determine whether local rabbitmq-server is clustered '''
    # NOTE: A rabbitmq node can only join a cluster once.
    # Simply checking for more than one running node tells us
    # if this unit is in a cluster.
    if len(running_nodes()) > 1:
        return True
    else:
        return False


def assess_cluster_status(*args):
    ''' Assess the status for the current running unit '''
    if is_unit_paused_set():
        return "maintenance", "Paused"

    # NOTE: ensure rabbitmq is actually installed before doing
    #       any checks
    if not rabbitmq_is_installed():
        return 'waiting', 'RabbitMQ is not yet installed'

    # Sufficient peers
    if not is_sufficient_peers():
        return 'waiting', ("Waiting for all {} peers to complete the "
                           "cluster.".format(config('min-cluster-size')))
    # Clustering Check
    peer_ids = relation_ids('cluster')
    if peer_ids and len(related_units(peer_ids[0])):
        if not clustered():
            return 'waiting', 'Unit has peers, but RabbitMQ not clustered'

    # See if all the rabbitmq charms think they are clustered (e.g. that the
    # 'clustered' attribute is set on the 'cluster' relationship
    if not cluster_ready():
        return 'waiting', 'RabbitMQ is clustered, but not all charms are ready'
    # Departed nodes
    departed_node = check_cluster_memberships()
    if departed_node:
        return (
            'blocked',
            'Node {} in the cluster but not running. If it is a departed '
            'node, remove with `forget-cluster-node` action'
            .format(departed_node))

    # Check if cluster is partitioned
    try:
        if peer_ids and len(related_units(peer_ids[0])) and is_partitioned():
            return ('blocked', 'RabbitMQ is partitioned')
    except (subprocess.TimeoutExpired, NotImplementedError):
        pass

    # General status check
    if not wait_app():
        return (
            'blocked', 'Unable to determine if the rabbitmq service is up')

    if leader_get(CLUSTER_MODE_KEY) != config(CLUSTER_MODE_KEY):
        return (
            'waiting',
            'Not reached target {} mode'.format(CLUSTER_MODE_KEY))

    # we're active - so just return the 'active' state, but if 'active'
    # is returned, then it is ignored by the assess_status system.
    return 'active', "message is ignored"


def in_run_deferred_hooks_action():
    """Check if current execution context is the run-deferred-hooks action

    :returns: Whether currently in run-deferred-hooks hook
    :rtype: bool
    """
    return action_name() == 'run-deferred-hooks'


def coordinated_restart_on_change(restart_map, restart_function):
    """Decorator to check for file changes.

    Check for file changes after decorated function runs. If a change
    is detected then a restart request is made using the coordinator
    module.

    :param restart_map: {file: [service, ...]}
    :type restart_map: Dict[str, List[str,]]
    :param restart_functions: Function to be used to perform restart if
                              lock is immediatly granted.
    :type restart_functions: Callable
    """
    def wrap(f):
        @functools.wraps(f)
        def wrapped_f(*args, **kwargs):
            if is_unit_paused_set():
                return f(*args, **kwargs)
            pre = {path: path_hash(path) for path in restart_map}
            f(*args, **kwargs)
            post = {path: path_hash(path) for path in restart_map}
            if pre == post:
                log("No restart needed")
            else:
                changed = [
                    path
                    for path in restart_map.keys()
                    if pre[path] != post[path]]
                log("Requesting restart. File(s) {} have changed".format(
                    ','.join(changed)))
                if in_run_deferred_hooks_action():
                    log("In action context, requesting immediate restart")
                    restart_function(coordinate_restart=False)
                else:
                    serial = coordinator.Serial()
                    serial.acquire(COORD_KEY_RESTART)
                    # Run the restart function which will check if lock is
                    # is immediatly available.
                    restart_function(coordinate_restart=True)
        return wrapped_f
    return wrap


def assess_status(configs):
    """Assess status of current unit
    Decides what the state of the unit should be based on the current
    configuration.
    SIDE EFFECT: calls set_os_workload_status(...) which sets the workload
    status of the unit.
    Also calls status_set(...) directly if paused state isn't complete.
    @param configs: a templating.OSConfigRenderer() object
    @returns None - this function is executed for its side-effect
    """
    deferred_events.check_restart_timestamps()
    assess_status_func(configs)()
    rmq_version = get_upstream_version(VERSION_PACKAGE)
    if rmq_version:
        application_version_set(rmq_version)


def assess_status_func(configs):
    """Helper function to create the function that will assess_status() for
    the unit.
    Uses charmhelpers.contrib.openstack.utils.make_assess_status_func() to
    create the appropriate status function and then returns it.
    Used directly by assess_status() and also for pausing and resuming
    the unit.

    NOTE(ajkavanagh) ports are not checked due to race hazards with services
    that don't behave sychronously w.r.t their service scripts.  e.g.
    apache2.
    @param configs: a templating.OSConfigRenderer() object
    @return f() -> None : a function that assesses the unit's workload status
    """
    def _assess_status_func():
        state, message = _determine_os_workload_status(
            configs, {},
            charm_func=assess_cluster_status,
            services=services(), ports=None)
        if state == 'active' and clustered():
            message = 'Unit is ready and clustered'
        # Remind the administrator cluster_series_upgrading is set.
        # If the cluster has completed the series upgrade, run the
        # complete-cluster-series-upgrade action to clear this setting.
        if leader_get('cluster_series_upgrading'):
            message += (", run complete-cluster-series-upgrade when the "
                        "cluster has completed its upgrade")
            # Edge case when the first rabbitmq unit is upgraded it will show
            # waiting for peers. Force "active" workload state for various
            # testing suites like zaza to recognize a successful series upgrade
            # of the first unit.
            if state == "waiting":
                state = "active"

        # Validate that the cron schedule for nrpe status checks is correct. An
        # invalid cron schedule will not prevent the rabbitmq service from
        # running but may cause problems with nrpe checks.
        schedule = config('stats_cron_schedule')
        if schedule and not is_cron_schedule_valid(schedule):
            message += ". stats_cron_schedule is invalid"

        # Deferred restarts should be managed by _determine_os_workload_status
        # but rabbits wlm code needs refactoring to make it consistent with
        # other charms as any message returned by _determine_os_workload_status
        # is currently dropped on the floor if: state == 'active'
        events = defaultdict(set)
        for e in deferred_events.get_deferred_events():
            events[e.action].add(e.service)
        for action, svcs in events.items():
            svc_msg = "Services queued for {}: {}".format(
                action, ', '.join(sorted(svcs)))
            message = "{}. {}".format(message, svc_msg)
        deferred_hooks = deferred_events.get_deferred_hooks()
        if deferred_hooks:
            svc_msg = "Hooks skipped due to disabled auto restarts: {}".format(
                ', '.join(sorted(deferred_hooks)))
            message = "{}. {}".format(message, svc_msg)

        serial = coordinator.Serial()
        requested_locks = [k for k in COORD_KEYS if serial.requested(k)]
        if requested_locks:
            message = "{}. {}".format(
                message,
                'Waiting for {} lock(s)'.format(','.join(requested_locks)))
            state = "waiting"
        status_set(state, message)

    return _assess_status_func


def pause_unit_helper(configs):
    """Helper function to pause a unit, and then call assess_status(...) in
    effect, so that the status is correctly updated.
    Uses charmhelpers.contrib.openstack.utils.pause_unit() to do the work.
    @param configs: a templating.OSConfigRenderer() object
    @returns None - this function is executed for its side-effect
    """
    _pause_resume_helper(pause_unit, configs)


def resume_unit_helper(configs):
    """Helper function to resume a unit, and then call assess_status(...) in
    effect, so that the status is correctly updated.
    Uses charmhelpers.contrib.openstack.utils.resume_unit() to do the work.
    @param configs: a templating.OSConfigRenderer() object
    @returns None - this function is executed for its side-effect
    """
    _pause_resume_helper(resume_unit, configs)


def _pause_resume_helper(f, configs):
    """Helper function that uses the make_assess_status_func(...) from
    charmhelpers.contrib.openstack.utils to create an assess_status(...)
    function that can be used with the pause/resume of the unit
    @param f: the function to be used with the assess_status(...) function
    @returns None - this function is executed for its side-effect
    """
    # TODO(ajkavanagh) - ports= has been left off because of the race hazard
    # that exists due to service_start()
    f(assess_status_func(configs),
      services=services(),
      ports=None)


def get_unit_hostname():
    """Return this unit's hostname.

    @returns hostname
    """
    return socket.gethostname()


def is_sufficient_peers():
    """Sufficient number of expected peers to build a complete cluster

    If min-cluster-size has been provided, check that we have sufficient
    number of peers who have presented a hostname for a complete cluster.

    If not defined assume a single unit.

    @returns boolean
    """
    min_size = config('min-cluster-size')
    if min_size:
        log("Checking for minimum of {} peer units".format(min_size),
            level=DEBUG)

        # Include this unit
        units = 1
        for rid in relation_ids('cluster'):
            for unit in related_units(rid):
                if relation_get(attribute='hostname',
                                rid=rid, unit=unit):
                    units += 1

        if units < min_size:
            log("Insufficient number of peer units to form cluster "
                "(expected=%s, got=%s)" % (min_size, units), level=INFO)
            return False
        else:
            log("Sufficient number of peer units to form cluster {}"
                "".format(min_size), level=DEBUG)
            return True
    else:
        log("min-cluster-size is not defined, race conditions may occur if "
            "this is not a single unit deployment.", level=WARNING)
        return True


def rabbitmq_is_installed():
    """Determine if rabbitmq is installed

    @returns boolean
    """
    return os.path.exists(RABBITMQ_CTL)


def cluster_ready():
    """Determine if each node in the cluster is ready and the cluster is
    complete with the expected number of peers.

    Once cluster_ready returns True it is safe to execute client relation
    hooks. Having min-cluster-size set will guarantee cluster_ready will not
    return True until the expected number of peers are clustered and ready.

    If min-cluster-size is not set it must assume the cluster is ready in order
    to allow for single unit deployments.

    @returns boolean
    """
    min_size = int(config('min-cluster-size') or 0)
    units = 1
    for rid in relation_ids('cluster'):
        units += len(related_units(rid))
    if not min_size:
        min_size = units

    if not is_sufficient_peers():
        log("In cluster_ready: not sufficient peers", level=DEBUG)
        return False
    elif min_size > 1:
        if not clustered():
            log("This unit is not detected as clustered, but should be at "
                "this stage", WARNING)
            return False
        clustered_units = 1
        for rid in relation_ids('cluster'):
            for remote_unit in related_units(rid):
                if not relation_get(attribute='clustered',
                                    rid=rid,
                                    unit=remote_unit):
                    log("{} is not yet clustered".format(remote_unit),
                        DEBUG)
                    return False
                else:
                    clustered_units += 1
        if clustered_units < min_size:
            log("Fewer than minimum cluster size:{} rabbit units reporting "
                "clustered".format(min_size),
                DEBUG)
            return False
        else:
            log("All {} rabbit units reporting clustered"
                "".format(min_size),
                DEBUG)
            return True

    log("Must assume this is a single unit returning 'cluster' ready", DEBUG)
    return True


def client_node_is_ready():
    """Determine if the leader node has set amqp client data

    @returns boolean
    """
    # Bail if this unit is paused
    if is_unit_paused_set():
        return False
    for rid in relation_ids('amqp'):
        if leader_get(attribute='{}_password'.format(rid)):
            return True
    return False


def leader_node_is_ready():
    """Determine if the leader node is ready to handle client relationship
    hooks.

    IFF rabbit is not paused, is installed, this is the leader node and the
    cluster is complete.

    @returns boolean
    """
    # Paused check must run before other checks
    # Bail if this unit is paused
    if is_unit_paused_set():
        return False
    return (rabbitmq_is_installed() and
            is_leader() and
            cluster_ready())


def archive_upgrade_available():
    """Check if the change in sources.list would warrant running
    apt-get update/upgrade

    @returns boolean:
        True: the "source" had changed, so upgrade is available
        False: the "source" had not changed, no upgrade needed
    """
    log('checking if upgrade is available', DEBUG)

    c = config()
    old_source = c.previous('source')
    log('Previous "source" config options was: {}'.format(old_source), DEBUG)
    new_source = c['source']
    log('Current "source" config options is: {}'.format(new_source), DEBUG)

    if old_source != new_source:
        log('The "source" config option change warrants the upgrade.', INFO)

    return old_source != new_source


def install_or_upgrade_packages():
    """Run apt-get update/upgrade mantra.
    This is called from either install hook, or from config-changed,
    if upgrade is warranted
    """
    status_set('maintenance', 'Installing/upgrading RabbitMQ packages')
    apt_update(fatal=True)
    apt_install(PACKAGES, fatal=True)


def remove_file(path):
    """Delete the file or skip it if not exist.

    :param path: the file to delete
    :type path: str
    """
    if os.path.isfile(path):
        os.remove(path)
    elif os.path.exists(path):
        log('{} path is not file'.format(path), level='ERROR')
    else:
        log('{} file does not exist'.format(path), level='DEBUG')


def management_plugin_enabled():
    """Check if management plugin should be enabled.

    :returns: Whether anagement plugin should be enabled
    :rtype: bool
    """
    _release = lsb_release()['DISTRIB_CODENAME'].lower()
    if CompareHostReleases(_release) < "bionic":
        return False
    else:
        return config('management_plugin') is True


def add_nrpe_file_access():
    """ Add Nagios user the access to rabbitmq directories

    It allows Nagios NRPE agent to collect the stats from rabbitmq
    """
    run_cmd(['usermod', '-a', '-G', 'rabbitmq', NRPE_USER])


def fix_nrpe_file_owner():
    """ Set rabbitmq as owner of logs data generated from cron job

    Necessary on older deployments where the cron task would create files
    with root as owner previously
    It ensures the compatibility with existing deployments
    """
    # Although this should run only when related to nrpe subordinate
    # there is still a possibility for a race condition if upgrading the units
    # before the cron task had the time to create the files
    if os.path.exists(f'{LIB_PATH}data') and os.path.exists(f'{LIB_PATH}logs'):
        run_cmd(['chown',
                 '-R',
                 f'{RABBIT_USER}:{RABBIT_USER}',
                 f'{LIB_PATH}data/',
                 ])
        run_cmd(['chown',
                 '-R',
                 f'{RABBIT_USER}:{RABBIT_USER}',
                 f'{LIB_PATH}logs/',
                 ])
        run_cmd(['chmod',
                 '750',
                 f'{LIB_PATH}data/',
                 f'{LIB_PATH}logs/',
                 ])


def sync_nrpe_files():
    """Sync all NRPE-related files.

    Copy all the custom NRPE scripts and create the cron file to run
    rabbitmq stats collection
    """
    if not os.path.exists(NAGIOS_PLUGINS):
        os.makedirs(NAGIOS_PLUGINS, exist_ok=True)

    if config('ssl'):
        rsync(os.path.join(charm_dir(), 'files', 'check_rabbitmq.py'),
              os.path.join(NAGIOS_PLUGINS, 'check_rabbitmq.py'))
    if config('queue_thresholds') and config('stats_cron_schedule'):
        rsync(os.path.join(charm_dir(), 'files', 'check_rabbitmq_queues.py'),
              os.path.join(NAGIOS_PLUGINS, 'check_rabbitmq_queues.py'))
    if management_plugin_enabled():
        rsync(os.path.join(charm_dir(), 'files', 'check_rabbitmq_cluster.py'),
              os.path.join(NAGIOS_PLUGINS, 'check_rabbitmq_cluster.py'))

    if config('stats_cron_schedule'):
        rsync(os.path.join(charm_dir(), 'files', 'collect_rabbitmq_stats.sh'),
              os.path.join(SCRIPTS_DIR, 'collect_rabbitmq_stats.sh'))
        cronjob = CRONJOB_CMD.format(
            schedule=config('stats_cron_schedule'),
            timeout=config('cron-timeout'),
            command=os.path.join(SCRIPTS_DIR, 'collect_rabbitmq_stats.sh'))
        write_file(STATS_CRONFILE, cronjob)


def remove_nrpe_files():
    """Remove the cron file and all the custom NRPE scripts."""
    if not config('stats_cron_schedule'):
        # These scripts are redundant if the value `stats_cron_schedule`
        # isn't in the config
        remove_file(STATS_CRONFILE)
        remove_file(os.path.join(SCRIPTS_DIR, 'collect_rabbitmq_stats.sh'))

    if not config('ssl'):
        # This script is redundant if the value `ssl` isn't in the config
        remove_file(os.path.join(NAGIOS_PLUGINS, 'check_rabbitmq.py'))

    if not config('queue_thresholds') or not config('stats_cron_schedule'):
        # This script is redundant if the value `queue_thresholds` or
        # `stats_cron_schedule` isn't in the config
        remove_file(os.path.join(NAGIOS_PLUGINS, 'check_rabbitmq_queues.py'))

    if not management_plugin_enabled():
        # This script is redundant if the value `management_plugin` isn't
        # in the config
        remove_file(os.path.join(NAGIOS_PLUGINS, 'check_rabbitmq_cluster.py'))


def get_nrpe_credentials():
    """Get the NRPE hostname, unit and user details.

    :returns: (hostname, unit, vhosts, user, password)
    :rtype: Tuple[str, str, List[Dict[str, str]], str, str]
    """
    # Find out if nrpe set nagios_hostname
    hostname = nrpe.get_nagios_hostname()
    unit = nrpe.get_nagios_unit_name()

    # create unique user and vhost for each unit
    current_unit = local_unit().replace('/', '-')
    user = 'nagios-{}'.format(current_unit)
    vhosts = [{'vhost': user, 'shortname': RABBIT_USER}]
    password = get_rabbit_password(user, local=True)
    create_user(user, password, ['monitoring'])

    if config('check-vhosts'):
        for other_vhost in config('check-vhosts').split(' '):
            if other_vhost:
                item = {'vhost': other_vhost,
                        'shortname': 'rabbit_{}'.format(other_vhost)}
                vhosts.append(item)

    return hostname, unit, vhosts, user, password


def nrpe_update_vhost_check(nrpe_compat, unit, user, password, vhost):
    """Add/Remove the RabbitMQ non-SSL check

    If the SSL is set to `off` or `on`, it will add the non-SSL RabbitMQ check,
    otherwise it will remove it.

    :param nrpe_compat: the NRPE class object
    :type: nrpe.NRPE
    :param unit: NRPE unit
    :type: str
    :param user: username of NRPE user
    :type: str
    :param password: password of NRPE user
    :type: str
    :param vhost: dictionary with vhost and shortname
    :type: Dict[str, str]
    """
    ssl_config = config('ssl') or ''
    if ssl_config.lower() in ['off', 'on']:
        log('Adding rabbitmq non-SSL check for {}'.format(vhost['vhost']),
            level=DEBUG)
        nrpe_compat.add_check(
            shortname=vhost['shortname'],
            description='Check RabbitMQ {} {}'.format(unit, vhost['vhost']),
            check_cmd='{}/check_rabbitmq.py --user {} --password {} '
                      '--vhost {}'.format(
                          NAGIOS_PLUGINS, user, password, vhost['vhost']))
    else:
        log('Removing rabbitmq non-SSL check for {}'.format(vhost['vhost']),
            level=DEBUG)
        nrpe_compat.remove_check(
            shortname=vhost['shortname'],
            description='Remove check RabbitMQ {} {}'.format(
                unit, vhost['vhost']),
            check_cmd='{}/check_rabbitmq.py'.format(NAGIOS_PLUGINS))


def nrpe_update_vhost_ssl_check(nrpe_compat, unit, user, password, vhost):
    """Add/Remove the RabbitMQ SSL check

    If the SSL is set to `only` or `on`, it will add the SSL RabbitMQ check,
    otherwise it will remove it.

    :param nrpe_compat: the NRPE class object
    :type: nrpe.NRPE
    :param unit: NRPE unit
    :type: str
    :param user: username of NRPE user
    :type: str
    :param password: password of NRPE user
    :type: str
    :param vhost: dictionary with vhost and shortname
    :type: Dict[str, str]
    """
    ssl_config = config('ssl') or ''
    if ssl_config.lower() in ['only', 'on']:
        log('Adding rabbitmq SSL check for {}'.format(vhost['vhost']),
            level=DEBUG)
        nrpe_compat.add_check(
            shortname=vhost['shortname'] + "_ssl",
            description='Check RabbitMQ (SSL) {} {}'.format(
                unit, vhost['vhost']),
            check_cmd='{}/check_rabbitmq.py --user {} --password {} '
                      '--vhost {} --ssl --ssl-ca {} --port {}'.format(
                          NAGIOS_PLUGINS, user, password, vhost['vhost'],
                          SSL_CA_FILE, int(config('ssl_port'))))
    else:
        log('Removing rabbitmq SSL check for {}'.format(vhost['vhost']),
            level=DEBUG)
        nrpe_compat.remove_check(
            shortname=vhost['shortname'] + "_ssl",
            description='Remove check RabbitMQ (SSL) {} {}'.format(
                unit, vhost['vhost']),
            check_cmd='{}/check_rabbitmq.py'.format(NAGIOS_PLUGINS))


def is_cron_schedule_valid(cron_schedule):
    """Returns whether or not the stats_cron_schedule can be properly parsed.

    :param cron_schedule: the cron schedule to validate
    :return: True if the cron schedule defined can be parsed by the croniter
             library, False otherwise
    """
    try:
        croniter(cron_schedule).get_next(datetime)
        return True
    except CroniterBadCronError:
        return False


def get_max_stats_file_age():
    """Returns the max stats file age for NRPE checks.

    Max stats file age is determined by a heuristic of 2x the configured
    interval in the stats_cron_schedule config value.

    :return: the maximum age (in seconds) the queues check should consider
             a stats file as aged. If a cron schedule is not defined,
             then return 0.
    :rtype: int
    """
    cron_schedule = config('stats_cron_schedule')
    if not cron_schedule:
        return 0

    try:
        it = croniter(cron_schedule)
        interval = it.get_next(datetime) - it.get_prev(datetime)
        return int(interval.total_seconds() * 2)
    except CroniterBadCronError as err:
        # The config value is being passed straight into croniter and it may
        # not be valid which will cause croniter to raise an error. Catch any
        # of the errors raised.
        log('Specified cron schedule is invalid: %s' % err,
            level=ERROR)
        return 0


def nrpe_update_queues_check(nrpe_compat, rabbit_dir):
    """Add/Remove the RabbitMQ queues check

    The RabbitMQ Queues check should be added if the `queue_thresholds` and
    the `stats_cron_schedule` variables are in the configuration. Otherwise,
    this check should be removed.
    The cron job configured with the `stats_cron_schedule`
    variable is responsible for creating the data files read by this check.

    :param nrpe_compat: the NRPE class object
    :type: nrpe.NRPE
    :param rabbit_dir: path to the RabbitMQ directory
    :type: str
    """
    stats_datafile = os.path.join(
        rabbit_dir, 'data', '{}_queue_stats.dat'.format(get_unit_hostname()))

    if config('queue_thresholds') and config('stats_cron_schedule'):
        cmd = ""
        # If value of queue_thresholds is incorrect we want the hook to fail
        for item in yaml.safe_load(config('queue_thresholds')):
            cmd += ' -c "{}" "{}" {} {}'.format(*item)
        for item in yaml.safe_load(config('exclude_queues')):
            cmd += ' -e "{}" "{}"'.format(*item)
        busiest_queues = config('busiest_queues')
        if busiest_queues is not None and int(busiest_queues) > 0:
            cmd += ' -d "{}"'.format(busiest_queues)

        max_age = get_max_stats_file_age()
        if max_age > 0:
            cmd += ' -m {}'.format(max_age)

        nrpe_compat.add_check(
            shortname=RABBIT_USER + '_queue',
            description='Check RabbitMQ Queues',
            check_cmd='{}/check_rabbitmq_queues.py{} {}'.format(
                NAGIOS_PLUGINS, cmd, stats_datafile))
    else:
        log('Removing rabbitmq Queues check', level=DEBUG)
        nrpe_compat.remove_check(
            shortname=RABBIT_USER + '_queue',
            description='Remove check RabbitMQ Queues',
            check_cmd='{}/check_rabbitmq_queues.py'.format(NAGIOS_PLUGINS))


def nrpe_update_cluster_check(nrpe_compat, user, password):
    """Add/Remove the RabbitMQ cluster check

    If the management_plugin is set to `True`, it will add the cluster RabbitMQ
    check, otherwise it will remove it.

    :param nrpe_compat: the NRPE class object
    :type: nrpe.NRPE
    :param user: username of NRPE user
    :type: str
    :param password: password of NRPE user
    :type: str
    """
    if management_plugin_enabled():
        cmd = '{}/check_rabbitmq_cluster.py --port {} ' \
              '--user {} --password {}'.format(
                  NAGIOS_PLUGINS, get_managment_port(), user, password)
        nrpe_compat.add_check(
            shortname=RABBIT_USER + '_cluster',
            description='Check RabbitMQ Cluster',
            check_cmd=cmd)
    else:
        log('Removing rabbitmq Cluster check', level=DEBUG)
        nrpe_compat.remove_check(
            shortname=RABBIT_USER + '_cluster',
            description='Remove check RabbitMQ Cluster',
            check_cmd='{}/check_rabbitmq_cluster.py'.format(NAGIOS_PLUGINS))


def rabbitmq_version_newer_or_equal(version):
    """Compare the installed RabbitMQ version

    :param version: Version to compare with
    :type: str
    :returns: True if the installed RabbitMQ version is newer or equal.
    :rtype: bool
    """
    rmq_version = get_upstream_version(VERSION_PACKAGE)
    return apt_pkg.version_compare(rmq_version, version) >= 0
