"""Focused tests for the Dock3 camera_frame_zoom MQTT tool."""

from __future__ import annotations

import sys
import unittest
from argparse import Namespace
from pathlib import Path
from unittest import mock

from src.utils.dji_error_codes import DOCK_SYSTEM_ERROR


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "tools"))

from mqtt_dock3_frame_zoom_test import build_frame_zoom_payload, run_frame_zoom


class FakeFrameZoomClient:
    def __init__(self, replies):
        self.replies = list(replies)
        self.calls: list[tuple[str, dict]] = []

    def request(self, method, data, timeout, **kwargs):
        self.calls.append((method, data))
        if not self.replies:
            return 2, None
        return self.replies.pop(0)


def ok_reply():
    return {
        "data": {
            "result": 0,
            "zoom_ratio": 4.0,
            "center_result": {"success": True, "method": "target_track"},
            "software_pipeline": [
                {"step": "normalize_selection", "status": "ok"},
                {"step": "compute_center_offset", "status": "ok"},
                {"step": "gimbal_center_track", "status": "ok"},
                {"step": "camera_zoom_to", "status": "ok"},
            ],
            "viewlink_result": {"success": True},
        }
    }


def connecting_reply():
    return {
        "data": {
            "result": DOCK_SYSTEM_ERROR,
            "software_pipeline": [
                {"step": "normalize_selection", "status": "ok"},
                {"step": "compute_center_offset", "status": "ok"},
                {"step": "camera_zoom_to", "status": "pending"},
            ],
            "viewlink_result": {
                "success": False,
                "connecting": True,
                "stage": "connecting",
            },
        }
    }


def args(**overrides):
    values = {
        "config": "config/gateway.yaml",
        "payload_index": "52-0-0",
        "camera_index": 0,
        "camera_type": "zoom",
        "normalized": True,
        "x": 0.35,
        "y": 0.30,
        "width": 0.20,
        "height": 0.20,
        "frame_width": 1920,
        "frame_height": 1080,
        "zoom_ratio": 4.0,
        "locked": True,
        "centering_enabled": True,
        "allow_zoom_without_center": True,
        "center_mode": "",
        "video_url": "",
        "snapshot_dir": "logs/viewlink_frame_zoom",
        "snapshot_prefix": "frame_zoom",
        "capture_before": True,
        "capture_after": True,
        "require_snapshots": True,
        "after_wait": 1.0,
        "ffmpeg": "ffmpeg",
        "snapshot_timeout": 20.0,
        "timeout": 5.0,
        "retry_connecting": True,
        "connect_retry_delay": 4.0,
        "require_center_track": True,
        "require_zoom_to": True,
    }
    values.update(overrides)
    return Namespace(**values)


class Dock3FrameZoomToolTest(unittest.TestCase):
    def test_builds_cloud_services_frame_zoom_payload(self):
        payload = build_frame_zoom_payload(args())

        self.assertEqual(payload["payload_index"], "52-0-0")
        self.assertEqual(payload["camera_type"], "zoom")
        self.assertEqual(payload["normalized_box"], {
            "x": 0.35,
            "y": 0.30,
            "width": 0.20,
            "height": 0.20,
        })
        self.assertNotIn("sdk_dir", payload)
        self.assertNotIn("gimbal_ip", payload)

    def test_runs_single_camera_frame_zoom_request(self):
        client = FakeFrameZoomClient([(0, ok_reply())])

        rc = run_frame_zoom(client, args(), sleep_fn=lambda _: None)

        self.assertEqual(rc, 0)
        self.assertEqual([method for method, _ in client.calls], ["camera_frame_zoom"])

    def test_retries_when_viewlink_background_connecting(self):
        client = FakeFrameZoomClient([(0, connecting_reply()), (0, ok_reply())])
        sleeps: list[float] = []

        rc = run_frame_zoom(client, args(), sleep_fn=sleeps.append)

        self.assertEqual(rc, 0)
        self.assertEqual([method for method, _ in client.calls], ["camera_frame_zoom", "camera_frame_zoom"])
        self.assertEqual(sleeps, [4.0])

    def test_fails_when_track_step_is_missing(self):
        reply = ok_reply()
        reply["data"]["software_pipeline"] = [
            {"step": "normalize_selection", "status": "ok"},
            {"step": "camera_zoom_to", "status": "ok"},
        ]
        client = FakeFrameZoomClient([(0, reply)])

        rc = run_frame_zoom(client, args(), sleep_fn=lambda _: None)

        self.assertEqual(rc, 5)

    def test_captures_real_video_before_and_after_when_configured(self):
        client = FakeFrameZoomClient([(0, ok_reply())])
        sleeps: list[float] = []

        with mock.patch("mqtt_dock3_frame_zoom_test._capture_snapshot", return_value=0) as capture:
            rc = run_frame_zoom(
                client,
                args(video_url="rtsp://192.168.1.119:554/main", snapshot_dir="snapshots"),
                sleep_fn=sleeps.append,
            )

        self.assertEqual(rc, 0)
        self.assertEqual([method for method, _ in client.calls], ["camera_frame_zoom"])
        self.assertEqual(sleeps, [1.0])
        self.assertEqual(capture.call_count, 2)
        self.assertEqual(capture.call_args_list[0].args[1].name, "frame_zoom_before.jpg")
        self.assertEqual(capture.call_args_list[1].args[1].name, "frame_zoom_after.jpg")


if __name__ == "__main__":
    unittest.main()
