from __future__ import annotations

import sys
import unittest
from argparse import Namespace
from pathlib import Path
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "tools"))

from mqtt_camera_mode_switch_visual_test import run_visual_acceptance


class FakeClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict, str]] = []
        self.record_mode = 2

    def request(self, method, data, timeout, **kwargs):
        self.calls.append((method, data, kwargs.get("channel")))
        if method == "gateway_state_query":
            return 0, {"method": method, "data": {"result": 0, "state": {"device": "bound"}}}
        if method == "payload_status_query":
            return 0, {
                "method": method,
                "data": {
                    "result": 0,
                    "payloads": [
                        {
                            "id": "gimbal",
                            "channels": {
                                "control": {
                                    "sdk_dir": "ViewLink",
                                    "host": "192.168.1.119",
                                    "port": 2000,
                                }
                            },
                        }
                    ],
                },
            }
        if method == "viewlink_initialize":
            return 0, {"method": method, "data": {"result": 0, "viewlink_result": {"success": True}}}
        if method == "viewlink_connect":
            return 0, {"method": method, "data": {"result": 0, "viewlink_result": {"success": True}}}
        if method == "viewlink_is_connected":
            return 0, {"method": method, "data": {"result": 0, "viewlink_result": {"success": True, "connected": True}}}
        if method == "viewlink_get_record_mode":
            return 0, {"method": method, "data": {"result": 0, "viewlink_result": {"success": True, "mode": self.record_mode}}}
        if method == "camera_mode_switch":
            mode = data.get("mode")
            if mode == "illegal_mode":
                return 0, {"method": method, "data": {"result": 325001}}
            if mode == "photo":
                self.record_mode = 1
                return 0, {"method": method, "data": {"result": 0, "mode": "photo", "viewlink_result": {"success": True}}}
            if mode == "video":
                self.record_mode = 2
            return 0, {"method": method, "data": {"result": 0, "mode": mode, "viewlink_result": {"success": True}}}
        return 1, None


def build_args(**overrides):
    values = {
        "timeout": 1.0,
        "skip_bind_check": False,
        "skip_viewlink_connect": False,
        "sdk_dir": "",
        "gimbal_ip": "",
        "gimbal_port": 0,
        "payload_index": "52-0-0",
        "camera_index": 0,
        "camera_type": "zoom",
        "mode_settle": 0.0,
        "busy_settle": 0.0,
        "invalid_mode": "illegal_mode",
        "invalid_camera_mode": 99,
        "require_active_record_mode": False,
        "skip_playback": True,
        "expect_playback_success": False,
        "skip_illegal_mode": False,
        "skip_busy": True,
        "no_visual": True,
        "observe_seconds": 0.0,
        "pause_between_steps": 0.0,
        "verbose": False,
    }
    values.update(overrides)
    return Namespace(**values)


class CameraModeSwitchVisualToolTest(unittest.TestCase):
    @patch("mqtt_camera_mode_switch_visual_test.time.sleep", lambda _seconds: None)
    def test_visual_acceptance_uses_only_existing_services_methods(self):
        client = FakeClient()

        rc = run_visual_acceptance(client, build_args())

        self.assertEqual(rc, 0)
        methods = [method for method, _, _ in client.calls]
        self.assertIn("camera_mode_switch", methods)
        self.assertIn("viewlink_get_record_mode", methods)
        self.assertEqual({channel for _, _, channel in client.calls}, {"services"})
        self.assertNotIn("set_camera_mode", methods)


if __name__ == "__main__":
    unittest.main()
