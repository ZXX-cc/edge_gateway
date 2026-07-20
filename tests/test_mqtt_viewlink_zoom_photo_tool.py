"""Focused tests for the ViewLink zoom/photo MQTT tool."""

from __future__ import annotations

import sys
import unittest
from argparse import Namespace
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "tools"))

from mqtt_viewlink_zoom_photo_test import run_zoom_photo


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
        "zoom_direction": "in",
        "zoom_speed": 2,
        "zoom_duration": 1.0,
        "settle_seconds": 0.5,
        "photo_wait": 1.0,
        "video_url": "",
        "snapshot_output": "unused.jpg",
        "ffmpeg": "ffmpeg",
        "preview_timeout": 5.0,
        "restore_zoom": False,
    }
    values.update(overrides)
    return Namespace(**values)


class ViewLinkZoomPhotoToolTest(unittest.TestCase):
    def test_runs_safe_sequence_from_runtime_config(self):
        client = FakeClient()
        sleeps: list[float] = []

        rc = run_zoom_photo(client, build_args(), sleep_fn=sleeps.append)

        self.assertEqual(rc, 0)
        self.assertEqual(
            [method for method, _ in client.calls],
            [
                "workspace_bind_status",
                "payload_status_query",
                "viewlink_initialize",
                "viewlink_connect",
                "viewlink_is_connected",
                "viewlink_set_record_mode",
                "viewlink_zoom_in",
                "viewlink_stop_zoom",
                "viewlink_take_photo",
            ],
        )
        self.assertEqual(sleeps, [0.5, 1.0, 0.5, 1.0])

    def test_stops_zoom_when_zoom_reply_times_out(self):
        client = FakeClient(fail_method="viewlink_zoom_in")

        rc = run_zoom_photo(client, build_args(), sleep_fn=lambda _: None)

        self.assertEqual(rc, 2)
        methods = [method for method, _ in client.calls]
        self.assertIn("viewlink_stop_zoom", methods)
        self.assertNotIn("viewlink_take_photo", methods)


if __name__ == "__main__":
    unittest.main()
