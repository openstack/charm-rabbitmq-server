#!/usr/bin/python
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

import os
import shutil
import sys
import subprocess
import glob

try:
    import yaml  # flake8: noqa
except ImportError:
    if sys.version_info.major == 2:
        subprocess.check_call(['apt-get', 'install', '-y', 'python-yaml'])
    else:
        subprocess.check_call(['apt-get', 'install', '-y', 'python3-yaml'])
    import yaml  # flake8: noqa

try:
    import requests  # flake8: noqa
except ImportError:
    if sys.version_info.major == 2:
        subprocess.check_call(['apt-get', 'install', '-y',
                               'python-requests'])
    else:
        subprocess.check_call(['apt-get', 'install', '-y',
                               'python3-requests'])
    import requests  # flake8: noqa

import rabbit_utils as rabbit
import ssl_utils
from rabbitmq_context import SSL_CA_FILE

from lib.utils import (
    chown, chmod,
    is_newer,
)
from charmhelpers.contrib.hahelpers.cluster import (
    is_clustered,
    is_elected_leader
)
from charmhelpers.contrib.openstack.utils import (
    is_unit_paused_set,
    set_unit_upgrading,
    is_unit_upgrading_set,
    clear_unit_paused,
    clear_unit_upgrading,
)

import charmhelpers.contrib.storage.linux.ceph as ceph
from charmhelpers.contrib.openstack.utils import save_script_rc
from charmhelpers.contrib.hardening.harden import harden

from charmhelpers.fetch import (
    add_source,
)

from charmhelpers.core.hookenv import (
    open_port,
    close_port,
    log,
    DEBUG,
    ERROR,
    INFO,
    leader_set,
    leader_get,
    relation_get,
    relation_clear,
    relation_set,
    relation_id as get_relation_id,
    relation_ids,
    related_units,
    service_name,
    local_unit,
    config,
    is_relation_made,
    Hooks,
    UnregisteredHookError,
    is_leader,
    charm_dir,
    status_set,
    unit_private_ip,
)
from charmhelpers.core.host import (
    cmp_pkgrevno,
    rsync,
    service_stop,
    service_restart,
    write_file,
)
from charmhelpers.contrib.charmsupport import nrpe

from charmhelpers.contrib.peerstorage import (
    peer_echo,
    peer_retrieve,
    peer_store,
    peer_store_and_set,
    peer_retrieve_by_prefix,
    leader_get,
)

from charmhelpers.core.unitdata import kv

hooks = Hooks()

SERVICE_NAME = os.getenv('JUJU_UNIT_NAME').split('/')[0]
POOL_NAME = SERVICE_NAME
RABBIT_DIR = '/var/lib/rabbitmq'
RABBIT_USER = 'rabbitmq'
RABBIT_GROUP = 'rabbitmq'
NAGIOS_PLUGINS = '/usr/local/lib/nagios/plugins'
SCRIPTS_DIR = '/usr/local/bin'
STATS_CRONFILE = '/etc/cron.d/rabbitmq-stats'
STATS_DATAFILE = os.path.join(RABBIT_DIR, 'data',
                              '{}_queue_stats.dat'
                              ''.format(rabbit.get_unit_hostname()))
CRONJOB_CMD = ("{schedule} root timeout -k 10s -s SIGINT {timeout} "
               "{command} 2>&1 | logger -p local0.notice\n")
INITIAL_CLIENT_UPDATE_KEY = 'initial_client_update_done'


@hooks.hook('install.real')
@harden()
def install():
    pre_install_hooks()
    add_source(config('source'), config('key'))
    rabbit.install_or_upgrade_packages()


def validate_amqp_config_tracker(f):
    """Decorator to mark all existing tracked amqp configs as stale so that
    they are refreshed the next time the current unit leader.
    """
    def _validate_amqp_config_tracker(*args, **kwargs):
        if not is_leader():
            kvstore = kv()
            tracker = kvstore.get('amqp_config_tracker')
            if tracker:
                for rid in tracker:
                    tracker[rid]['stale'] = True

                kvstore.set(key='amqp_config_tracker', value=tracker)
                kvstore.flush()

        return f(*args, **kwargs)
    return _validate_amqp_config_tracker


def configure_amqp(username, vhost, relation_id, admin=False):
    """Configure rabbitmq server.

    This function creates user/password, vhost and sets user permissions. It
    also enabales mirroring queues if requested.

    Calls to rabbitmqctl are costly and as such we aim to limit them by only
    doing them if we detect that a settings needs creating or updating. To
    achieve this we track what we set by storing key/value pairs associated
    with a particular relation id in a local database.

    Since this function is only supposed to be called by the cluster leader,
    the database is expected to be invalidated if it exists and we are no
    longer leader so as to ensure that a leader switch results in a
    rabbitmq configuraion consistent with the current leader's view.

    :param username: client username.
    :param vhost: vhost name.
    :param relation_id: optional relation id used to identify the context of
                        this operation. This should always be provided
                        so that we can track what has been set.
    :param admin: boolean value defining whether the new user is admin.
    :returns: user password
    """
    log("Configuring rabbitmq for user '{}' vhost '{}' (rid={})".
        format(username, vhost, relation_id), DEBUG)

    if not relation_id:
        raise Exception("Invalid relation id '{}' provided to "
                        "{}()".format(relation_id, configure_amqp.__name__))

    # get and update service password
    password = rabbit.get_rabbit_password(username)

    expected = {'username': username, 'vhost': vhost,
                'mirroring-queues': config('mirroring-queues')}
    kvstore = kv()
    tracker = kvstore.get('amqp_config_tracker') or {}
    val = tracker.get(relation_id)
    if val == expected and not val.get('stale'):
        log("Rabbit already configured for relation "
            "'{}'".format(relation_id), DEBUG)
        return password
    else:
        tracker[relation_id] = expected

    # update vhost
    rabbit.create_vhost(vhost)
    if admin:
        rabbit.create_user(username, password, ['administrator'])
    else:
        rabbit.create_user(username, password)
    rabbit.grant_permissions(username, vhost)

    # NOTE(freyes): after rabbitmq-server 3.0 the method to define HA in the
    # queues is different
    # http://www.rabbitmq.com/blog/2012/11/19/breaking-things-with-rabbitmq-3-0
    if config('mirroring-queues'):
        rabbit.set_ha_mode(vhost, 'all')

    kvstore.set(key='amqp_config_tracker', value=tracker)
    kvstore.flush()

    return password


def update_clients():
    """Update amqp client relation hooks

    IFF leader node is ready. Client nodes are considered ready once the leader
    has already run amqp_changed.
    """
    if rabbit.leader_node_is_ready() or rabbit.client_node_is_ready():
        for rid in relation_ids('amqp'):
            for unit in related_units(rid):
                amqp_changed(relation_id=rid, remote_unit=unit)
        kvstore = kv()
        update_done = kvstore.get(INITIAL_CLIENT_UPDATE_KEY, False)
        if not update_done:
            kvstore.set(key=INITIAL_CLIENT_UPDATE_KEY, value=True)
            kvstore.flush()


@validate_amqp_config_tracker
@hooks.hook('amqp-relation-changed')
def amqp_changed(relation_id=None, remote_unit=None):
    singleset = set(['username', 'vhost'])
    host_addr = rabbit.get_unit_ip()

    if rabbit.leader_node_is_ready():
        relation_settings = {'hostname': host_addr,
                             'private-address': host_addr}
        # NOTE: active/active case
        if config('prefer-ipv6'):
            relation_settings['private-address'] = host_addr

        current = relation_get(rid=relation_id, unit=remote_unit)
        if singleset.issubset(current):
            if not all([current.get('username'), current.get('vhost')]):
                log('Relation not ready.', DEBUG)
                return

            # Provide credentials to relations. If password is already
            # available on peer relation then use it instead of reconfiguring.
            username = current['username']
            vhost = current['vhost']
            admin = current.get('admin', False)
            amqp_rid = relation_id or get_relation_id()
            password = configure_amqp(username, vhost, amqp_rid, admin=admin)
            relation_settings['password'] = password
        else:
            # NOTE(hopem): we should look at removing this code since i don't
            #              think it's ever used anymore and stems from the days
            #              when we needed to ensure consistency between
            #              peerstorage (replaced by leader get/set) and amqp
            #              relations.
            queues = {}
            for k, v in current.iteritems():
                amqp_rid = k.split('_')[0]
                x = '_'.join(k.split('_')[1:])
                if amqp_rid not in queues:
                    queues[amqp_rid] = {}

                queues[amqp_rid][x] = v

            for amqp_rid in queues:
                if singleset.issubset(queues[amqp_rid]):
                    username = queues[amqp_rid]['username']
                    vhost = queues[amqp_rid]['vhost']
                    password = configure_amqp(username, vhost, amqp_rid,
                                              admin=admin)
                    key = '_'.join([amqp_rid, 'password'])
                    relation_settings[key] = password

        ssl_utils.configure_client_ssl(relation_settings)

        if is_clustered():
            relation_settings['clustered'] = 'true'
            # NOTE(dosaboy): this stanza can be removed once we fully remove
            #                deprecated HA support.
            if is_relation_made('ha'):
                # active/passive settings
                relation_settings['vip'] = config('vip')
                # or ha-vip-only to support active/active, but
                # accessed via a VIP for older clients.
                if config('ha-vip-only') is True:
                    relation_settings['ha-vip-only'] = 'true'

        # set if need HA queues or not
        if cmp_pkgrevno('rabbitmq-server', '3.0.1') < 0:
            relation_settings['ha_queues'] = True

        log("Updating relation {} keys {}"
            .format(relation_id or get_relation_id(),
                    ','.join(relation_settings.keys())), DEBUG)
        peer_store_and_set(relation_id=relation_id,
                           relation_settings=relation_settings)
    elif not is_leader() and rabbit.client_node_is_ready():
        log("Propagating peer settings to all amqp relations", DEBUG)

        # NOTE(jamespage) clear relation to deal with data being
        #                 removed from peer storage.
        relation_clear(relation_id)

        # Each unit needs to set the db information otherwise if the unit
        # with the info dies the settings die with it Bug# 1355848
        for rel_id in relation_ids('amqp'):
            peerdb_settings = peer_retrieve_by_prefix(rel_id)
            if 'password' in peerdb_settings:
                peerdb_settings['hostname'] = host_addr
                peerdb_settings['private-address'] = host_addr
                relation_set(relation_id=rel_id, **peerdb_settings)


@hooks.hook('cluster-relation-joined')
def cluster_joined(relation_id=None):
    relation_settings = {
        'hostname': rabbit.get_unit_hostname(),
        'private-address':
            rabbit.get_unit_ip(config_override=rabbit.CLUSTER_OVERRIDE_CONFIG,
                               interface=rabbit.CLUSTER_INTERFACE),
    }

    relation_set(relation_id=relation_id,
                 relation_settings=relation_settings)

    if is_relation_made('ha') and \
            config('ha-vip-only') is False:
        log('hacluster relation is present, skipping native '
            'rabbitmq cluster config.')
        return

    try:
        if not is_leader():
            log('Not the leader, deferring cookie propagation to leader')
            return
    except NotImplementedError:
        if is_newer():
            log('cluster_joined: Relation greater.')
            return

    if not os.path.isfile(rabbit.COOKIE_PATH):
        log('erlang cookie missing from %s' % rabbit.COOKIE_PATH,
            level=ERROR)
        return

    if is_leader():
        log('Leader peer_storing cookie', level=INFO)
        cookie = open(rabbit.COOKIE_PATH, 'r').read().strip()
        peer_store('cookie', cookie)
        peer_store('leader_node_ip', unit_private_ip())
        peer_store('leader_node_hostname', rabbit.get_unit_hostname())


@hooks.hook('cluster-relation-changed')
def cluster_changed(relation_id=None, remote_unit=None):
    # Future travelers beware ordering is significant
    rdata = relation_get(rid=relation_id, unit=remote_unit)

    # sync passwords
    blacklist = ['hostname', 'private-address', 'public-address']
    whitelist = [a for a in rdata.keys() if a not in blacklist]
    peer_echo(includes=whitelist)

    cookie = peer_retrieve('cookie')
    if not cookie:
        log('cluster_changed: cookie not yet set.', level=INFO)
        return

    if rdata:
        hostname = rdata.get('hostname', None)
        private_address = rdata.get('private-address', None)

        if hostname and private_address:
            rabbit.update_hosts_file({private_address: hostname})

    # sync the cookie with peers if necessary
    update_cookie()

    if is_relation_made('ha') and \
            config('ha-vip-only') is False:
        log('hacluster relation is present, skipping native '
            'rabbitmq cluster config.', level=INFO)
        return

    # NOTE(freyes): all the nodes need to marked as 'clustered' (LP: #1691510)
    rabbit.cluster_with()

    if not is_leader() and is_relation_made('nrpe-external-master'):
        update_nrpe_checks()


@hooks.hook('stop')
def stop():
    """Gracefully remove ourself from RabbitMQ cluster before unit is removed

    If RabbitMQ have objections to node removal, for example because of this
    being the only disc node to leave the cluster, the operation will fail and
    unit removal will be blocked with error for operator to investigate.

    In the event of a unit being forcefully or abrubtly removed from the
    cluster without a chance to remove itself, it will be left behind as a
    stopped node in the RabbitMQ cluster.  Having a dormant no longer existing
    stopped node lying around will cause trouble in the event that all RabbitMQ
    nodes are shut down.  In such a situation the cluster most likely will not
    start again without operator intervention as RabbitMQ will want to
    interrogate the now non-existing stopped node about any queue it thinks it
    would be most likely to have authoritative knowledge about.

    For this reason any abruptly removed nodes will be cleaned up periodically
    by the leader unit during its update-status hook run.

    This call is placed in stop hook and not in the cluster-relation-departed
    hook because the latter is not called on the unit being removed.
    """
    rabbit.leave_cluster()


def update_cookie(leaders_cookie=None):
    # sync cookie
    if leaders_cookie:
        cookie = leaders_cookie
    else:
        cookie = peer_retrieve('cookie')
    cookie_local = None
    with open(rabbit.COOKIE_PATH, 'r') as f:
        cookie_local = f.read().strip()

    if cookie_local == cookie:
        log('Cookie already synchronized with peer.')
        return

    service_stop('rabbitmq-server')
    with open(rabbit.COOKIE_PATH, 'wb') as out:
        out.write(cookie)
    if not is_unit_paused_set():
        service_restart('rabbitmq-server')
        rabbit.wait_app()


@hooks.hook('ha-relation-joined')
@rabbit.restart_on_change({rabbit.ENV_CONF:
                           rabbit.restart_map()[rabbit.ENV_CONF]})
def ha_joined():
    corosync_bindiface = config('ha-bindiface')
    corosync_mcastport = config('ha-mcastport')
    vip = config('vip')
    vip_iface = config('vip_iface')
    vip_cidr = config('vip_cidr')
    rbd_name = config('rbd-name')
    vip_only = config('ha-vip-only')

    if None in [corosync_bindiface, corosync_mcastport, vip, vip_iface,
                vip_cidr, rbd_name] and vip_only is False:
        log('Insufficient configuration data to configure hacluster.',
            level=ERROR)
        sys.exit(1)
    elif None in [corosync_bindiface, corosync_mcastport, vip, vip_iface,
                  vip_cidr] and vip_only is True:
        log('Insufficient configuration data to configure VIP-only hacluster.',
            level=ERROR)
        sys.exit(1)

    if not is_relation_made('ceph', 'auth') and vip_only is False:
        log('ha_joined: No ceph relation yet, deferring.')
        return

    ctxt = {rabbit.ENV_CONF: rabbit.CONFIG_FILES[rabbit.ENV_CONF]}
    rabbit.ConfigRenderer(ctxt).write(rabbit.ENV_CONF)

    relation_settings = {}
    relation_settings['corosync_bindiface'] = corosync_bindiface
    relation_settings['corosync_mcastport'] = corosync_mcastport

    if vip_only is True:
        relation_settings['resources'] = {
            'res_rabbitmq_vip': 'ocf:heartbeat:IPaddr2',
        }
        relation_settings['resource_params'] = {
            'res_rabbitmq_vip': 'params ip="%s" cidr_netmask="%s" nic="%s"' %
                                (vip, vip_cidr, vip_iface),
        }
    else:
        relation_settings['resources'] = {
            'res_rabbitmq_rbd': 'ocf:ceph:rbd',
            'res_rabbitmq_fs': 'ocf:heartbeat:Filesystem',
            'res_rabbitmq_vip': 'ocf:heartbeat:IPaddr2',
            'res_rabbitmq-server': 'lsb:rabbitmq-server',
        }

        relation_settings['resource_params'] = {
            'res_rabbitmq_rbd': 'params name="%s" pool="%s" user="%s" '
                                'secret="%s"' %
                                (rbd_name, POOL_NAME,
                                 SERVICE_NAME, ceph._keyfile_path(
                                     SERVICE_NAME)),
            'res_rabbitmq_fs': 'params device="/dev/rbd/%s/%s" directory="%s" '
                               'fstype="ext4" op start start-delay="10s"' %
                               (POOL_NAME, rbd_name, RABBIT_DIR),
            'res_rabbitmq_vip': 'params ip="%s" cidr_netmask="%s" nic="%s"' %
                                (vip, vip_cidr, vip_iface),
            'res_rabbitmq-server': 'op start start-delay="5s" '
                                   'op monitor interval="5s"',
        }

        relation_settings['groups'] = {
            'grp_rabbitmq':
            'res_rabbitmq_rbd res_rabbitmq_fs res_rabbitmq_vip '
            'res_rabbitmq-server',
        }

    for rel_id in relation_ids('ha'):
        relation_set(relation_id=rel_id, relation_settings=relation_settings)

    env_vars = {
        'OPENSTACK_PORT_EPMD': 4369,
        'OPENSTACK_PORT_MCASTPORT': config('ha-mcastport'),
    }
    save_script_rc(**env_vars)


@hooks.hook('ha-relation-changed')
def ha_changed():
    if not is_clustered():
        return
    vip = config('vip')
    log('ha_changed(): We are now HA clustered. '
        'Advertising our VIP (%s) to all AMQP clients.' %
        vip)


@hooks.hook('ceph-relation-joined')
def ceph_joined():
    log('Start Ceph Relation Joined')
    # NOTE fixup
    # utils.configure_source()
    ceph.install()
    log('Finish Ceph Relation Joined')


@hooks.hook('ceph-relation-changed')
def ceph_changed():
    log('Start Ceph Relation Changed')
    auth = relation_get('auth')
    key = relation_get('key')
    use_syslog = str(config('use-syslog')).lower()
    if None in [auth, key]:
        log('Missing key or auth in relation')
        sys.exit(0)

    ceph.configure(service=SERVICE_NAME, key=key, auth=auth,
                   use_syslog=use_syslog)

    if is_elected_leader('res_rabbitmq_vip'):
        rbd_img = config('rbd-name')
        rbd_size = config('rbd-size')
        sizemb = int(rbd_size.split('G')[0]) * 1024
        blk_device = '/dev/rbd/%s/%s' % (POOL_NAME, rbd_img)
        ceph.create_pool(service=SERVICE_NAME, name=POOL_NAME,
                         replicas=int(config('ceph-osd-replication-count')))
        ceph.ensure_ceph_storage(service=SERVICE_NAME, pool=POOL_NAME,
                                 rbd_img=rbd_img, sizemb=sizemb,
                                 fstype='ext4', mount_point=RABBIT_DIR,
                                 blk_device=blk_device,
                                 system_services=['rabbitmq-server'])
        subprocess.check_call(['chown', '-R', '%s:%s' %
                               (RABBIT_USER, RABBIT_GROUP), RABBIT_DIR])
    else:
        log('This is not the peer leader. Not configuring RBD.')
        log('Stopping rabbitmq-server.')
        service_stop('rabbitmq-server')

    # If 'ha' relation has been made before the 'ceph' relation
    # it is important to make sure the ha-relation data is being
    # sent.
    if is_relation_made('ha'):
        log('*ha* relation exists. Triggering ha_joined()')
        ha_joined()
    else:
        log('*ha* relation does not exist.')
    log('Finish Ceph Relation Changed')


@hooks.hook('nrpe-external-master-relation-changed')
def update_nrpe_checks():
    if os.path.isdir(NAGIOS_PLUGINS):
        rsync(os.path.join(charm_dir(), 'scripts',
                           'check_rabbitmq.py'),
              os.path.join(NAGIOS_PLUGINS, 'check_rabbitmq.py'))
        rsync(os.path.join(charm_dir(), 'scripts',
                           'check_rabbitmq_queues.py'),
              os.path.join(NAGIOS_PLUGINS, 'check_rabbitmq_queues.py'))
        if config('management_plugin'):
            rsync(os.path.join(charm_dir(), 'scripts',
                               'check_rabbitmq_cluster.py'),
                  os.path.join(NAGIOS_PLUGINS, 'check_rabbitmq_cluster.py'))

    if config('stats_cron_schedule'):
        script = os.path.join(SCRIPTS_DIR, 'collect_rabbitmq_stats.sh')
        cronjob = CRONJOB_CMD.format(schedule=config('stats_cron_schedule'),
                                     timeout=config('cron-timeout'),
                                     command=script)
        rsync(os.path.join(charm_dir(), 'scripts',
                           'collect_rabbitmq_stats.sh'), script)
        write_file(STATS_CRONFILE, cronjob)
    elif os.path.isfile(STATS_CRONFILE):
        os.remove(STATS_CRONFILE)

    # Find out if nrpe set nagios_hostname
    hostname = nrpe.get_nagios_hostname()
    myunit = nrpe.get_nagios_unit_name()

    # create unique user and vhost for each unit
    current_unit = local_unit().replace('/', '-')
    user = 'nagios-%s' % current_unit
    vhost = 'nagios-%s' % current_unit
    password = rabbit.get_rabbit_password(user, local=True)

    rabbit.create_vhost(vhost)
    rabbit.create_user(user, password, ['monitoring'])
    rabbit.grant_permissions(user, vhost)

    nrpe_compat = nrpe.NRPE(hostname=hostname)
    if config('ssl') in ['off', 'on']:
        cmd = ('{plugins_dir}/check_rabbitmq.py --user {user} '
               '--password {password} --vhost {vhost}')
        cmd = cmd.format(plugins_dir=NAGIOS_PLUGINS, user=user,
                         password=password, vhost=vhost)
        nrpe_compat.add_check(
            shortname=rabbit.RABBIT_USER,
            description='Check RabbitMQ {%s}' % myunit,
            check_cmd=cmd
        )
    if config('ssl') in ['only', 'on']:
        log('Adding rabbitmq SSL check', level=DEBUG)
        cmd = ('{plugins_dir}/check_rabbitmq.py --user {user} '
               '--password {password} --vhost {vhost} '
               '--ssl --ssl-ca {ssl_ca} --port {port}')
        cmd = cmd.format(plugins_dir=NAGIOS_PLUGINS,
                         user=user,
                         password=password,
                         port=int(config('ssl_port')),
                         vhost=vhost,
                         ssl_ca=SSL_CA_FILE)
        nrpe_compat.add_check(
            shortname=rabbit.RABBIT_USER + "_ssl",
            description='Check RabbitMQ (SSL) {%s}' % myunit,
            check_cmd=cmd
        )

    if config('queue_thresholds'):
        cmd = ""
        # If value of queue_thresholds is incorrect we want the hook to fail
        for item in yaml.safe_load(config('queue_thresholds')):
            cmd += ' -c "{}" "{}" {} {}'.format(*item)
        nrpe_compat.add_check(
            shortname=rabbit.RABBIT_USER + '_queue',
            description='Check RabbitMQ Queues',
            check_cmd='{}/check_rabbitmq_queues.py{} {}'.format(
                        NAGIOS_PLUGINS, cmd, STATS_DATAFILE)
        )
    if config('management_plugin'):
        # add NRPE check
        nrpe_compat.add_check(
            shortname=rabbit.RABBIT_USER + '_cluster',
            description='Check RabbitMQ Cluster',
            check_cmd='{}/check_rabbitmq_cluster.py --port {} --user {} --password {}'.format(
                        NAGIOS_PLUGINS,
                        rabbit.get_managment_port(),
                        user,
                        password
            )
        )

    nrpe_compat.write()


@hooks.hook('upgrade-charm')
@harden()
def upgrade_charm():
    pre_install_hooks()

    # Ensure older passwd files in /var/lib/juju are moved to
    # /var/lib/rabbitmq which will end up replicated if clustered
    for f in [f for f in os.listdir('/var/lib/juju')
              if os.path.isfile(os.path.join('/var/lib/juju', f))]:
        if f.endswith('.passwd'):
            s = os.path.join('/var/lib/juju', f)
            d = os.path.join('/var/lib/charm/{}'.format(service_name()), f)

            log('upgrade_charm: Migrating stored passwd'
                ' from %s to %s.' % (s, d))
            shutil.move(s, d)
    if is_elected_leader('res_rabbitmq_vip'):
        rabbit.migrate_passwords_to_peer_relation()

    # explicitly update buggy file name naigos.passwd
    old = os.path.join('var/lib/rabbitmq', 'naigos.passwd')
    if os.path.isfile(old):
        new = os.path.join('var/lib/rabbitmq', 'nagios.passwd')
        shutil.move(old, new)

    # NOTE(freyes): cluster_with() will take care of marking the node as
    # 'clustered' for existing deployments (LP: #1691510).
    rabbit.cluster_with()

    # Ensure all client connections are up to date on upgrade
    update_clients()


MAN_PLUGIN = 'rabbitmq_management'


@hooks.hook('config-changed')
@rabbit.restart_on_change(rabbit.restart_map())
@harden()
def config_changed():

    if is_unit_paused_set():
        log("Do not run config_changed while unit is paused", "WARNING")
        return

    # Update hosts with this unit's information
    rabbit.update_hosts_file(
        {rabbit.get_unit_ip(config_override=rabbit.CLUSTER_OVERRIDE_CONFIG,
                            interface=rabbit.CLUSTER_INTERFACE):
                                rabbit.get_unit_hostname()})

    # Add archive source if provided and not in the upgrade process
    if not leader_get("cluster_series_upgrading"):
        add_source(config('source'), config('key'))
    # Copy in defaults file for updated ulimits
    shutil.copyfile(
        'templates/rabbitmq-server',
        '/etc/default/rabbitmq-server')

    # Install packages to ensure any changes to source
    # result in an upgrade if applicable only if we change the 'source'
    # config option
    if rabbit.archive_upgrade_available():
        # Avoid packge upgrade collissions
        # Stopping and attempting to start rabbitmqs at the same time leads to
        # failed restarts
        rabbit.cluster_wait()
        rabbit.install_or_upgrade_packages()

    if config('ssl') == 'off':
        open_port(5672)
        close_port(int(config('ssl_port')))
    elif config('ssl') == 'on':
        open_port(5672)
        open_port(int(config('ssl_port')))
    elif config('ssl') == 'only':
        close_port(5672)
        open_port(int(config('ssl_port')))
    else:
        log("Unknown ssl config value: '%s'" % config('ssl'), level=ERROR)

    chown(RABBIT_DIR, rabbit.RABBIT_USER, rabbit.RABBIT_USER)
    chmod(RABBIT_DIR, 0o775)

    if config('management_plugin') is True:
        rabbit.enable_plugin(MAN_PLUGIN)
        open_port(rabbit.get_managment_port())
    else:
        rabbit.disable_plugin(MAN_PLUGIN)
        close_port(rabbit.get_managment_port())
        # LY: Close the old managment port since it may have been opened in a
        #     previous version of the charm. close_port is a noop if the port
        #     is not open
        close_port(55672)

    rabbit.ConfigRenderer(
        rabbit.CONFIG_FILES).write_all()

    # Only set values if this is the leader
    if not is_leader():
        return

    rabbit.set_all_mirroring_queues(config('mirroring-queues'))

    if is_relation_made("ha"):
        ha_is_active_active = config("ha-vip-only")

        if ha_is_active_active:
            update_nrpe_checks()
        else:
            if is_elected_leader('res_rabbitmq_vip'):
                update_nrpe_checks()
            else:
                log("hacluster relation is present but this node is not active"
                    " skipping update nrpe checks")
    elif is_relation_made('nrpe-external-master'):
        update_nrpe_checks()

    # Update cluster in case min-cluster-size has changed
    for rid in relation_ids('cluster'):
        for unit in related_units(rid):
            cluster_changed(relation_id=rid, remote_unit=unit)


@hooks.hook('leader-elected')
def leader_elected():
    status_set("maintenance", "{} is the elected leader".format(local_unit()))


@hooks.hook('leader-settings-changed')
def leader_settings_changed():

    if is_unit_paused_set():
        log("Do not run config_changed while unit is paused", "WARNING")
        return

    if not os.path.exists(rabbit.RABBITMQ_CTL):
        log('Deferring cookie configuration, RabbitMQ not yet installed')
        return
    # Get cookie from leader, update cookie locally and
    # force cluster-relation-changed hooks to run on peers
    cookie = leader_get(attribute='cookie')
    if cookie:
        update_cookie(leaders_cookie=cookie)
        # Force cluster-relation-changed hooks to run on peers
        # This will precipitate peer clustering
        # Without this a chicken and egg scenario prevails when
        # using LE and peerstorage
        for rid in relation_ids('cluster'):
            relation_set(relation_id=rid, relation_settings={'cookie': cookie})


def pre_install_hooks():
    for f in glob.glob('exec.d/*/charm-pre-install'):
        if os.path.isfile(f) and os.access(f, os.X_OK):
            subprocess.check_call(['sh', '-c', f])


@hooks.hook('pre-series-upgrade')
def series_upgrade_prepare():
    set_unit_upgrading()
    if not is_unit_paused_set():
        log("Pausing unit for series upgrade.")
        rabbit.pause_unit_helper(rabbit.ConfigRenderer(rabbit.CONFIG_FILES))
    if is_leader():
        if not leader_get('cluster_series_upgrading'):
            # Inform the entire cluster a series upgrade is occurring.
            # Run the complete-cluster-series-upgrade action on the leader to
            # clear this setting when the full cluster has completed its
            # upgrade.
            leader_set(cluster_series_upgrading=True)


@hooks.hook('post-series-upgrade')
def series_upgrade_complete():
    log("Running complete series upgrade hook", "INFO")
    clear_unit_paused()
    clear_unit_upgrading()
    rabbit.resume_unit_helper(rabbit.ConfigRenderer(rabbit.CONFIG_FILES))


@hooks.hook('update-status')
@harden()
def update_status():
    log('Updating status.')

    # leader check for previously unsuccessful cluster departures
    #
    # This must be done here and not in the cluster-relation-departed hook.  At
    # the point in time the cluster-relation-departed hook is called we know
    # that a unit is departing.  We also know that RabbitMQ will not have
    # noticed its departure yet.  We cannot remove a node pre-emptively.
    #
    # In the normal case the departing node should remove itself from the
    # cluster in its stop hook.  We clean up the ones that for whatever reason
    # are unable to clean up after themselves successfully here.
    #
    # Have a look at the docstring of the stop() function for detailed
    # explanation.
    if is_leader() and not is_unit_paused_set():
        rabbit.check_cluster_memberships()

if __name__ == '__main__':
    try:
        hooks.execute(sys.argv)
    except UnregisteredHookError as e:
        log('Unknown hook {} - skipping.'.format(e))
    # This solves one off problems waiting for the cluster to complete
    # It will get executed only once as soon as leader_node_is_ready()
    # or client_node_is_ready() returns True
    # Subsequent client requests will be handled by normal
    # amqp-relation-changed hooks
    kvstore = kv()
    if not kvstore.get(INITIAL_CLIENT_UPDATE_KEY, False):
        update_clients()

    rabbit.assess_status(rabbit.ConfigRenderer(rabbit.CONFIG_FILES))
