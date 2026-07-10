import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from demo_platform import app as demo_app


class DemoPlatformWorkflowTest(unittest.TestCase):
    @staticmethod
    def command_record(result, output_status, message=""):
        return {
            "tid": "demo-tid",
            "method": "flighttask_pause",
            "status": "replied",
            "reply": {
                "data": {
                    "result": result,
                    "output": {"status": output_status},
                },
                "msg": message,
            },
        }

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
            return {
                "method": method,
                "status": "replied",
                "business_success": True,
            }

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

    def test_api_command_uses_business_result_instead_of_reply_delivery(self):
        rejected = self.command_record(258, "rejected", "Mission not running")
        with patch.object(demo_app, "publish_command", return_value=rejected):
            response = demo_app.app.test_client().post(
                "/api/command",
                json={"channel": "services", "method": "flighttask_pause", "data": {}},
            )

        payload = response.get_json()
        self.assertEqual(response.status_code, 200)
        self.assertFalse(payload["ok"])
        self.assertFalse(payload["record"]["business_success"])
        self.assertEqual(payload["record"]["business_result"], 258)
        self.assertEqual(payload["record"]["business_status"], "rejected")
        self.assertEqual(payload["record"]["business_message"], "Mission not running")

        failed_with_zero = self.command_record(0, "failed", "flight controller rejected")
        with patch.object(demo_app, "publish_command", return_value=failed_with_zero):
            response = demo_app.app.test_client().post(
                "/api/command",
                json={"channel": "services", "method": "flighttask_pause", "data": {}},
            )
        self.assertFalse(response.get_json()["ok"])

        malformed = self.command_record(False, "paused")
        with patch.object(demo_app, "publish_command", return_value=malformed):
            response = demo_app.app.test_client().post(
                "/api/command",
                json={"channel": "services", "method": "flighttask_pause", "data": {}},
            )
        self.assertFalse(response.get_json()["ok"])

        paused = self.command_record(0, "paused")
        with patch.object(demo_app, "publish_command", return_value=paused):
            response = demo_app.app.test_client().post(
                "/api/command",
                json={"channel": "services", "method": "flighttask_pause", "data": {}},
            )
        self.assertTrue(response.get_json()["ok"])

    def test_publish_command_marks_rejected_reply_as_failed_retest(self):
        reply = self.command_record(325004, "rejected", "mode switch timeout")["reply"]

        class ReplyingMqttClient:
            def publish(self, topic, payload, qos):
                request = json.loads(payload)
                waiter = demo_app.pending[request["tid"]]
                waiter["reply"] = reply
                waiter["topic"] = f"thing/product/{demo_app.GATEWAY_SN}/services_reply"
                waiter["event"].set()
                return SimpleNamespace(rc=0)

        captured_logs = []
        demo_app.pending.clear()
        with (
            patch.object(demo_app, "mqtt_client", ReplyingMqttClient()),
            patch.object(demo_app, "broadcast"),
            patch.object(
                demo_app,
                "append_retest_log",
                side_effect=lambda entry: captured_logs.append(entry) or entry,
            ),
        ):
            record = demo_app.publish_command(
                "flighttask_pause",
                {},
                "services",
                timeout=0.1,
            )

        self.assertEqual(record["status"], "replied")
        self.assertFalse(record["business_success"])
        self.assertEqual(captured_logs[-1]["status"], "fail")
        self.assertEqual(captured_logs[-1]["evidence"]["result"], 325004)
        self.assertEqual(captured_logs[-1]["evidence"]["status"], "rejected")
        self.assertIn("mode switch timeout", captured_logs[-1]["note"])

    def test_workflow_stops_on_business_rejection(self):
        rejected = {
            **self.command_record(258, "rejected", "Mission not running"),
            "business_success": False,
        }
        with (
            patch.object(
                demo_app,
                "workflow_steps",
                return_value={
                    "pause-only": [{
                        "method": "flighttask_pause",
                        "data": {},
                    }],
                },
            ),
            patch.object(demo_app, "publish_command", return_value=rejected),
            patch.object(demo_app, "broadcast"),
        ):
            run = demo_app.run_workflow("pause-only")

        self.assertEqual(run["status"], "failed")
        self.assertEqual(len(run["steps"]), 1)

    def test_frontend_unwraps_api_record_and_extends_pause_resume_timeout(self):
        source = (demo_app.STATIC_DIR / "app.js").read_text(encoding="utf-8")

        self.assertIn("const record = payload.record || payload;", source)
        self.assertIn("const result = payload.record || payload;", source)
        self.assertIn("business_status", source)
        self.assertIn("business_result", source)
        self.assertIn("business_message", source)
        self.assertIn('"flighttask_pause"', source)
        self.assertIn('"flighttask_resume"', source)
        self.assertIn("missionModeCommands.has(method) ? 30 : 10", source)


if __name__ == "__main__":
    unittest.main()
