import unittest
from unittest.mock import patch

from demo_platform import app as demo_app


class DemoPlatformWorkflowTest(unittest.TestCase):
    def test_real_wayline_retest_uses_user_file_without_auto_execute(self):
        command = demo_app.RETEST_ACTIONS["real-hd-wayline"]["command"]

        self.assertIn("--real-sitl", command)
        self.assertIn("--wayline-file", command)
        self.assertIn("/home/fenghuolun/zwz/HD.waypoints", command)
        self.assertNotIn("--execute-real", command)

    def test_real_wayline_waits_for_prearm_gate_reply(self):
        calls = []

        def fake_publish(method, data, channel, timeout=8.0, need_reply=1):
            calls.append((method, data, channel, timeout))
            return {"method": method, "status": "replied"}

        with (
            patch.object(demo_app, "publish_command", side_effect=fake_publish),
            patch.object(demo_app, "broadcast"),
            patch.object(demo_app, "append_retest_log"),
        ):
            result = demo_app.run_workflow("real_hd_wayline", timeout=8.0)

        self.assertEqual(result["status"], "ok")
        first_method, first_data, _, first_timeout = calls[0]
        self.assertEqual(first_method, "set_flight_mode")
        self.assertEqual(first_data["prearm_timeout"], 30)
        self.assertEqual(first_timeout, 60.0)
        self.assertTrue(all(call[3] == 8.0 for call in calls[1:]))

        prepare_data = calls[1][1]
        self.assertEqual(
            prepare_data["file"]["url"],
            "file:///home/fenghuolun/zwz/HD.waypoints",
        )


if __name__ == "__main__":
    unittest.main()
