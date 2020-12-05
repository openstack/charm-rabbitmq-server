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
from pathlib import Path
from tempfile import NamedTemporaryFile, TemporaryDirectory
import unittest

import check_rabbitmq_queues


class CheckRabbitTest(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.tmpdir = TemporaryDirectory()
        cronjob = Path(cls.tmpdir.name) / "rabbitmq-stats"
        with cronjob.open('w') as f:
            f.write("*/5 * * * * root timeout -k 10s -s SIGINT 300 "
                    "/usr/local/bin/collect_rabbitmq_stats.sh 2>&1 | "
                    "logger -p local0.notice")
        cls.old_cron = check_rabbitmq_queues.CRONJOB
        check_rabbitmq_queues.CRONJOB = str(cronjob)

    @classmethod
    def tearDownClass(cls):
        """Tear down class fixture."""
        cls.tmpdir.cleanup()
        check_rabbitmq_queues.CRONJOB = cls.old_cron

    def test_check_stats_file_freshness_fresh(self):
        with NamedTemporaryFile() as stats_file:
            results = check_rabbitmq_queues.check_stats_file_freshness(
                stats_file.name
            )
            self.assertEqual(results[0], "OK")

    def test_check_stats_file_freshness_nonfresh(self):
        with NamedTemporaryFile() as stats_file:
            next_hour = datetime.now() + timedelta(hours=1)
            results = check_rabbitmq_queues.check_stats_file_freshness(
                stats_file.name, asof=next_hour
            )
            self.assertEqual(results[0], "CRIT")

    def test_get_stats_cron_schedule(self):
        schedule = check_rabbitmq_queues.get_stats_cron_schedule()
        self.assertEqual(schedule, "*/5 * * * *")
