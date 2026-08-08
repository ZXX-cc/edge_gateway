from __future__ import annotations

import sys
import unittest
from argparse import Namespace
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "tools"))

from mqtt_dock3_zoom_control_real_gateway_test import (
    build_focal_length_payload,
    build_frame_zoom_payload,
    run_zoom_acceptance,
)


class FakeZoomClient:
    def __init__(self, replies):
        self.replies = list(replies)
        self.calls: list[tuple[str, dict, str]] = []
        self.stats = {"accepted": 0}

    def request(self, method, data, timeout, **kwargs):
        self.calls.append((method, data, kwargs.get("channel")))
        self.stats["accepted"] += 1
        if not self.replies:
            return 2, None
        return self.replies.pop(0)

    def stale_total(self):
        return 0

    def unrelated_reply_total(self):
        return 0


def ok_reply(method):
    return {"method": method, "data": {"result": 0, "viewlink_result": {"success": True}}}


def frame_ok_reply():
    return {
        "method": "camera_frame_zoom",
        "data": {
            "result": 0,
            "software_pipeline": [
                {"step": "normalize_selection", "status": "ok"},
                {"step": "compute_center_offset", "status": "ok"},
                {"step": "gimbal_center_track", "status": "ok"},
                {"step": "camera_zoom_to", "status": "ok"},
            ],
            "viewlink_result": {"success": True},
        },
    }


def fail_reply(method):
    return {"method": method, "data": {"result": -1}, "msg": "SDK failed"}


def args(**overrides):
    values = {
        "config": "config/gateway.yaml",
        "host": "127.0.0.1",
        "port": 1883,
        "sn": "gateway-sn",
        "timeout": 5.0,
        "payload_index": "52-0-0",
        "camera_index": 0,
        "camera_type": "zoom",
        "zoom_ratio": 4.0,
        "camera_types": ["zoom", "infrared"],
        "camera_type_zoom_ratio": 2.0,
        "overrange_zoom_ratio": 999.0,
        "x": 0.35,
        "y": 0.30,
        "width": 0.20,
        "height": 0.20,
        "locked": True,
        "centering_enabled": True,
        "allow_zoom_without_center": True,
        "scenario": "normal",
        "video_url": "",
        "snapshot_dir": "logs/dock3_zoom_control",
        "capture_snapshots": False,
        "require_snapshots": False,
        "after_wait": 0.0,
        "ffmpeg": "ffmpeg",
        "snapshot_timeout": 1.0,
        "require_center_track": True,
        "require_zoom_to": True,
        "verbose": False,
    }
    values.update(overrides)
    return Namespace(**values)


class Dock3ZoomControlRealGatewayToolTest(unittest.TestCase):
    def test_payloads_use_cloud_fields_without_viewlink_connection_details(self):
        focal = build_focal_length_payload(args())
        frame = build_frame_zoom_payload(args())

        self.assertEqual(focal["zoom_factor"], 4.0)
        self.assertEqual(focal["camera_type"], "zoom")
        self.assertEqual(frame["normalized_box"]["width"], 0.20)
        for payload in (focal, frame):
            self.assertNotIn("sdk_dir", payload)
            self.assertNotIn("gimbal_ip", payload)
            self.assertNotIn("viewlink_method", payload)

    def test_normal_flow_uses_only_camera_cloud_methods_on_services(self):
        client = FakeZoomClient([
            (0, ok_reply("camera_focal_length_set")),
            (0, frame_ok_reply()),
            (0, ok_reply("camera_focal_length_set")),
            (0, ok_reply("camera_focal_length_set")),
        ])

        rc = run_zoom_acceptance(client, args())

        self.assertEqual(rc, 0)
        self.assertEqual(
            [method for method, _, _ in client.calls],
            [
                "camera_focal_length_set",
                "camera_frame_zoom",
                "camera_focal_length_set",
                "camera_focal_length_set",
            ],
        )
        self.assertEqual({channel for _, _, channel in client.calls}, {"services"})
        self.assertTrue(all(not method.startswith("viewlink_") for method, _, _ in client.calls))

    def test_overrange_scenario_expects_cloud_failure_reply(self):
        client = FakeZoomClient([(0, fail_reply("camera_focal_length_set"))])

        rc = run_zoom_acceptance(client, args(scenario="overrange"))

        self.assertEqual(rc, 0)
        self.assertEqual(client.calls[0][0], "camera_focal_length_set")
        self.assertEqual(client.calls[0][1]["zoom_factor"], 999.0)

    def test_disconnected_scenario_requires_both_cloud_methods_to_fail(self):
        client = FakeZoomClient([
            (0, fail_reply("camera_focal_length_set")),
            (0, fail_reply("camera_frame_zoom")),
        ])

        rc = run_zoom_acceptance(client, args(scenario="disconnected"))

        self.assertEqual(rc, 0)
        self.assertEqual(
            [method for method, _, _ in client.calls],
            ["camera_focal_length_set", "camera_frame_zoom"],
        )


if __name__ == "__main__":
    unittest.main()
