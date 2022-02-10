#!/usr/bin/python3

# Copyright 2014-2022 Canonical Limited.
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
Checks for services with deferred restarts.

This Nagios check will parse /var/lib/policy-rd.d/
to find any restarts that are currently deferred.
"""

import argparse
import glob
import sys
import yaml


DEFERRED_EVENTS_DIR = '/var/lib/policy-rc.d'


def get_deferred_events():
    """Return a list of deferred events dicts from policy-rc.d files.

    Events are read from DEFERRED_EVENTS_DIR and are of the form:
    {
        action: restart,
        policy_requestor_name: rabbitmq-server,
        policy_requestor_type: charm,
        reason: 'Pkg update',
        service: rabbitmq-server,
        time: 1614328743
    }

    :raises OSError: Raised in case of a system error while reading a policy file
    :raises yaml.YAMLError: Raised if parsing a policy file fails

    :returns: List of deferred event dictionaries
    :rtype: list
    """
    deferred_events_files = glob.glob(
        '{}/*.deferred'.format(DEFERRED_EVENTS_DIR))

    deferred_events = []
    for event_file in deferred_events_files:
        with open(event_file, 'r') as f:
            event = yaml.safe_load(f)
            deferred_events.append(event)

    return deferred_events


def get_deferred_restart_services(application=None):
    """Returns a list of services with deferred restarts.

    :param str application: Name of the application that blocked the service restart.
                            If application is None, all services with deferred restarts
                            are returned. Services which are blocked by a non-charm
                            requestor are always returned.

    :raises OSError: Raised in case of a system error while reading a policy file
    :raises yaml.YAMLError: Raised if parsing a policy file fails

    :returns: List of services with deferred restarts belonging to application.
    :rtype: list
    """

    deferred_restart_events = filter(
        lambda e: e['action'] == 'restart', get_deferred_events())

    deferred_restart_services = set()
    for restart_event in deferred_restart_events:
        if application:
            if (
                restart_event['policy_requestor_type'] != 'charm' or
                restart_event['policy_requestor_type'] == 'charm' and
                restart_event['policy_requestor_name'] == application
            ):
                deferred_restart_services.add(restart_event['service'])
        else:
            deferred_restart_services.add(restart_event['service'])

    return list(deferred_restart_services)


def main():
    """Check for services with deferred restarts."""
    parser = argparse.ArgumentParser(
        description='Check for services with deferred restarts')
    parser.add_argument(
        '--application', help='Check services belonging to this application only')

    args = parser.parse_args()

    services = set(get_deferred_restart_services(args.application))

    if len(services) == 0:
        print('OK: No deferred service restarts.')
        sys.exit(0)
    else:
        print(
            'CRITICAL: Restarts are deferred for services: {}.'.format(', '.join(services)))
        sys.exit(1)


if __name__ == '__main__':
    try:
        main()
    except OSError as e:
        print('CRITICAL: A system error occurred: {} ({})'.format(e.errno, e.strerror))
        sys.exit(1)
    except yaml.YAMLError as e:
        print('CRITICAL: Failed to parse a policy file: {}'.format(str(e)))
        sys.exit(1)
    except Exception as e:
        print('CRITICAL: An unknown error occurred: {}'.format(str(e)))
        sys.exit(1)
