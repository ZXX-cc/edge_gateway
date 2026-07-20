"""Focused tests for the visible ViewLink MQTT tool."""

from __future__ import annotations

import sys
import unittest
from argparse import Namespace
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "tools"))

from mqtt_viewlink_visible_test import _run_visible_motion


class FakeClient:
    def __init__(self, *, fail_method: str = ""):
        self.fail_method = fail_method
        self.calls: list[tuple[str, dict]] = []

    def request(self, method, data, timeout, **kwargs):
        self.calls.append((method, data))
        if method == self.fail_method:
            return 2, None
        if method == "workspace_bind_status":
            return 0, {"data": {"result": 0, "is_bind": True}}
        if method == "payload_status_query":
            return 0, {
                "data": {
                    "result": 0,
                    "payloads": [
                        {
                            "id": "gimbal",
                            "connection_state": "configured",
                            "channels": {
                                "control": {
                                    "host": "192.168.1.119",
                                    "port": 2000,
                                    "sdk_dir": "D:/ViewLink/bin/win64",
                                }
                            },
                        }
                    ],
                }
            }
        viewlink_result = {"success": True, "message": f"{method} ok"}
        if method == "viewlink_is_connected":
            viewlink_result["connected"] = True
        return 0, {"data": {"result": 0, "viewlink_result": viewlink_result}}


def build_args(**overrides):
    values = {
        "timeout": 5.0,
        "sdk_dir": "",
        "gimbal_ip": "",
        "gimbal_port": 0,
        "yaw_speed": 500,
        "pitch_speed": 500,
        "move_duration": 0.8,
        "zoom_speed": 2,
        "zoom_duration": 0.8,
        "turn_yaw": 0.0,
        "turn_pitch": 0.0,
        "settle_seconds": 1.0,
        "pause_between": 0.4,
    }
    values.update(overrides)
    return Namespace(**values)


class ViewLinkVisibleToolTest(unittest.TestCase):
    def test_runs_visible_sequence_without_recording_or_photo(self):
        client = FakeClient()
        sleeps: list[float] = []

        rc = _run_visible_motion(client, build_args(), sleeps.append)

        self.assertEqual(rc, 0)
        methods = [method for method, _ in client.calls]
        self.assertIn("viewlink_home", methods)
        self.assertIn("viewlink_turn_to", methods)
        self.assertIn("viewlink_zoom_in", methods)
        self.assertIn("viewlink_zoom_out", methods)
        self.assertNotIn("viewlink_take_photo", methods)
        self.assertNotIn("viewlink_start_recording", methods)
        self.assertNotIn("viewlink_stop_recording", methods)

    def test_stops_motion_when_move_reply_times_out(self):
        client = FakeClient(fail_method="viewlink_move")

        rc = _run_visible_motion(client, build_args(), lambda _: None)

        self.assertEqual(rc, 2)
        methods = [method for method, _ in client.calls]
        self.assertIn("viewlink_stop", methods)


if __name__ == "__main__":
    unittest.main()
