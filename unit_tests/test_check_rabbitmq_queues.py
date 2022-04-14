import unittest

import check_rabbitmq_queues


class TestCheckRabbitmqQueues(unittest.TestCase):
    def test_gen_stats(self):
        incomplete_queue = ["landscape "
                            "landscape.notifications-queue."
                            "aed6fb68-b1ff-4a68-980e-df6adf786beb "
                            "DOWN 1572621605"]
        x = list(check_rabbitmq_queues.gen_stats(incomplete_queue))
        self.assertEqual(x, [])

        complete_queue = ["landscape "
                          "landscape.notifications-queue."
                          "b9557ad1-9908-425e-a860-d424e34f63d7 "
                          "0 0 0 0 34952 RUNNING 1572621605"]
        y = list(check_rabbitmq_queues.gen_stats(complete_queue))
        self.assertEqual(y, [("landscape",
                              "landscape.notifications-queue."
                              "b9557ad1-9908-425e-a860-d424e34f63d7",
                              0)])
