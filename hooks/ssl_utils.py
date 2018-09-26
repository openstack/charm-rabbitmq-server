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

from charmhelpers.contrib.ssl.service import ServiceCA

from charmhelpers.core.hookenv import (
    config,
    relation_ids,
    relation_set,
    relation_get,
    local_unit,
)

import base64


def get_ssl_mode():
    ssl_mode = config('ssl')
    external_ca = False

    # Legacy config boolean option
    ssl_on = config('ssl_enabled')
    if ssl_mode == 'off' and ssl_on is False:
        ssl_mode = 'off'
    elif ssl_mode == 'off' and ssl_on:
        ssl_mode = 'on'

    ssl_key = config('ssl_key')
    ssl_cert = config('ssl_cert')

    if all((ssl_key, ssl_cert)):
        external_ca = True
    return ssl_mode, external_ca


def configure_client_ssl(relation_data):
    """Configure client with ssl
    """
    ssl_mode, external_ca = get_ssl_mode()
    if ssl_mode == 'off':
        return
    relation_data['ssl_port'] = config('ssl_port')
    if external_ca:
        if config('ssl_ca'):
            if "BEGIN CERTIFICATE" in config('ssl_ca'):
                ssl_ca_encoded = (base64
                                  .b64encode(config('ssl_ca').encode('ascii'))
                                  .decode('ascii'))
            else:
                ssl_ca_encoded = config('ssl_ca')
            relation_data['ssl_ca'] = ssl_ca_encoded
        return
    ca = ServiceCA.get_ca()
    relation_data['ssl_ca'] = (
        base64.b64encode(ca.get_ca_bundle().encode('ascii')).decode('ascii'))


def reconfigure_client_ssl(ssl_enabled=False):
    ssl_config_keys = set(('ssl_key', 'ssl_cert', 'ssl_ca'))
    for rid in relation_ids('amqp'):
        rdata = relation_get(rid=rid, unit=local_unit())
        if not ssl_enabled and ssl_config_keys.intersection(rdata):
            # No clean way to remove entirely, but blank them.
            relation_set(relation_id=rid, ssl_key='', ssl_cert='', ssl_ca='',
                         ssl_port='')
        elif ssl_enabled and not ssl_config_keys.intersection(rdata):
            configure_client_ssl(rdata)
            relation_set(relation_id=rid, **rdata)
