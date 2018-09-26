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

import mock
from functools import wraps

from unit_tests.test_utils import CharmTestCase

with mock.patch('charmhelpers.core.hookenv.cached') as cached:
    def passthrough(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            return func(*args, **kwargs)
        wrapper._wrapped = func
        return wrapper
    cached.side_effect = passthrough
    import actions


class PauseTestCase(CharmTestCase):

    def setUp(self):
        super(PauseTestCase, self).setUp(
            actions, ["pause_unit_helper", "ConfigRenderer"])
        self.ConfigRenderer.return_value = 'test-config'

    def test_pauses_services(self):
        actions.pause([])
        self.pause_unit_helper.assert_called_once_with('test-config')


class ResumeTestCase(CharmTestCase):

    def setUp(self):
        super(ResumeTestCase, self).setUp(
            actions, ["resume_unit_helper", "ConfigRenderer"])
        self.ConfigRenderer.return_value = 'test-config'

    def test_pauses_services(self):
        actions.resume([])
        self.resume_unit_helper.assert_called_once_with('test-config')


class ClusterStatusTestCase(CharmTestCase):

    def setUp(self):
        super(ClusterStatusTestCase, self).setUp(
            actions, ["check_output", "action_set", "action_fail"])

    def test_cluster_status(self):
        self.check_output.return_value = b'Cluster status OK'
        actions.cluster_status([])
        self.check_output.assert_called_once_with(['rabbitmqctl',
                                                   'cluster_status'],
                                                  universal_newlines=True)
        self.action_set.assert_called()

    def test_cluster_status_execption(self):
        self.check_output.side_effect = actions.CalledProcessError(1,
                                                                   "Failure")
        actions.cluster_status([])
        self.check_output.assert_called_once_with(['rabbitmqctl',
                                                   'cluster_status'],
                                                  universal_newlines=True)
        self.action_set.assert_called()
        self.action_fail.assert_called()


class CheckQueuesTestCase(CharmTestCase):
    TEST_QUEUE_RESULT = b'Listing queues ...\ntest\t0\ntest\t0\n""'

    def dummy_action_get(self, key):
        action_values = {"queue-depth": -1, "vhost": "/"}
        return action_values[key]

    def setUp(self):
        super(CheckQueuesTestCase, self).setUp(
            actions, ["check_output", "action_set", "action_fail",
                      "ConfigRenderer", "action_get"])

    def test_check_queues(self):
        self.action_get.side_effect = self.dummy_action_get
        self.check_output.return_value = self.TEST_QUEUE_RESULT

        actions.check_queues([])
        self.check_output.assert_called_once_with(['rabbitmqctl',
                                                   'list_queues',
                                                   '-p', "/"])
        self.action_set.assert_called()

    def test_check_queues_execption(self):
        self.action_get.side_effect = self.dummy_action_get
        self.check_output.return_value = self.TEST_QUEUE_RESULT

        self.check_output.side_effect = actions.CalledProcessError(1,
                                                                   "Failure")
        actions.check_queues([])
        self.check_output.assert_called_once_with(['rabbitmqctl',
                                                   'list_queues',
                                                   '-p', '/'])


class MainTestCase(CharmTestCase):

    def setUp(self):
        super(MainTestCase, self).setUp(actions, ["action_fail"])

    def test_invokes_action(self):
        dummy_calls = []

        def dummy_action(args):
            dummy_calls.append(True)

        with mock.patch.dict(actions.ACTIONS, {"foo": dummy_action}):
            actions.main(["foo"])
        self.assertEqual(dummy_calls, [True])

    def test_unknown_action(self):
        """Unknown actions aren't a traceback."""
        exit_string = actions.main(["foo"])
        self.assertEqual("Action foo undefined", exit_string)

    def test_failing_action(self):
        """Actions which traceback trigger action_fail() calls."""
        dummy_calls = []

        self.action_fail.side_effect = dummy_calls.append

        def dummy_action(args):
            raise ValueError("uh oh")

        with mock.patch.dict(actions.ACTIONS, {"foo": dummy_action}):
            actions.main(["foo"])
        self.assertEqual(dummy_calls, ["Action foo failed: uh oh"])
