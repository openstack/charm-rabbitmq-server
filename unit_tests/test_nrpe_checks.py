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

from datetime import datetime, timedelta
from tempfile import NamedTemporaryFile
import unittest

from mock import MagicMock, patch
import check_rabbitmq_queues


class CheckRabbitTest(unittest.TestCase):
    @patch(
        "check_rabbitmq_queues.config",
        MagicMock(return_value="*/5 * * * *"),
    )
    def test_check_stats_file_freshness_fresh(self):
        with NamedTemporaryFile() as stats_file:
            results = check_rabbitmq_queues.check_stats_file_freshness(
                stats_file.name
            )
            self.assertEqual(results[0], "OK")

    @patch(
        "check_rabbitmq_queues.config",
        MagicMock(return_value="*/5 * * * *"),
    )
    def test_check_stats_file_freshness_nonfresh(self):
        with NamedTemporaryFile() as stats_file:
            next_hour = datetime.now() + timedelta(hours=1)
            results = check_rabbitmq_queues.check_stats_file_freshness(
                stats_file.name, asof=next_hour
            )
            self.assertEqual(results[0], "CRIT")
