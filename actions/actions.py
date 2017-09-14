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
import sys
import re

from subprocess import (
    check_output,
    CalledProcessError,
)

sys.path.append('hooks/')

from charmhelpers.core.hookenv import (
    action_fail,
    action_set,
    action_get,
)

from rabbit_utils import (
    ConfigRenderer,
    CONFIG_FILES,
    pause_unit_helper,
    resume_unit_helper,
)


def pause(args):
    """Pause the RabbitMQ services.
    @raises Exception should the service fail to stop.
    """
    pause_unit_helper(ConfigRenderer(CONFIG_FILES))


def resume(args):
    """Resume the RabbitMQ services.
    @raises Exception should the service fail to start."""
    resume_unit_helper(ConfigRenderer(CONFIG_FILES))


def cluster_status(args):
    """Return the output of 'rabbitmqctl cluster_status'."""
    try:
        clusterstat = check_output(['rabbitmqctl', 'cluster_status'],
                                   universal_newlines=True)
        action_set({'output': clusterstat})
    except CalledProcessError as e:
        action_set({'output': e.output})
        action_fail('Failed to run rabbitmqctl cluster_status')
    except Exception:
        raise


def check_queues(args):
    """Check for queues with greater than N messages.
    Return those queues to the user."""
    queue_depth = (action_get('queue-depth'))
    vhost = (action_get('vhost'))
    result = []
    # rabbitmqctl's output contains lines we don't want, such as
    # 'Listing queues ..' and '...done.', which may vary by release.
    # Actual queue results *should* always look like 'test\t0'
    queue_pattern = re.compile('.*\t[0-9]*')
    try:
        queues = check_output(['rabbitmqctl', 'list_queues',
                               '-p', vhost]).split('\n')
        result = list({queue: size for (queue, size) in
                       [i.split('\t') for i in queues
                        if re.search(queue_pattern, i)]
                       if int(size) >= queue_depth})

        action_set({'output': result, 'outcome': 'Success'})
    except CalledProcessError as e:
        action_set({'output': e.output})
        action_fail('Failed to run rabbitmqctl list_queues')


# A dictionary of all the defined actions to callables (which take
# parsed arguments).
ACTIONS = {"pause": pause, "resume": resume, "cluster-status": cluster_status,
           "check-queues": check_queues}


def main(args):
    action_name = os.path.basename(args[0])
    try:
        action = ACTIONS[action_name]
    except KeyError:
        s = "Action {} undefined".format(action_name)
        action_fail(s)
        return s
    else:
        try:
            action(args)
        except Exception as e:
            action_fail("Action {} failed: {}".format(action_name, str(e)))


if __name__ == "__main__":
    sys.exit(main(sys.argv))
