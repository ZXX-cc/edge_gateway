from __future__ import annotations

import sys
import unittest
from argparse import Namespace
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "tools"))

from mqtt_payload_recording_start_test import build_recording_start_payload, run_recording_start_check


class FakeClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict, str, set[int]]] = []

    def request(self, method, data, timeout, **kwargs):
        self.calls.append((method, data, kwargs.get("channel"), kwargs.get("allowed_results")))
        if method == "camera_recording_start":
            return 0, {
                "method": method,
                "data": {
                    "result": 0,
                    "camera_index": data["camera_index"],
                    "recording": True,
                },
            }
        if method == "camera_recording_stop":
            return 0, {
                "method": method,
                "data": {
                    "result": 0,
                    "record_time": 1,
                    "recording": False,
                },
            }
        return 1, None


def build_args(**overrides):
    values = {
        "config": "config/gateway.yaml",
        "payload_index": "52-0-0",
        "camera_index": 0,
        "camera_type": "zoom",
        "resolution": "1080p",
        "framerate": 30,
        "encoding": "h264",
        "video_id": "recording-start-test",
        "file_name": "",
        "timeout": 5.0,
        "expect_recording_flag": True,
        "stop_after_start": False,
    }
    values.update(overrides)
    return Namespace(**values)


class PayloadRecordingStartToolTest(unittest.TestCase):
    def test_uses_dock3_services_camera_recording_start(self):
        client = FakeClient()

        rc = run_recording_start_check(client, build_args())

        self.assertEqual(rc, 0)
        self.assertEqual(len(client.calls), 1)
        method, data, channel, allowed = client.calls[0]
        self.assertEqual(method, "camera_recording_start")
        self.assertEqual(channel, "services")
        self.assertEqual(allowed, {0})
        self.assertEqual(
            data,
            {
                "payload_index": "52-0-0",
                "camera_index": 0,
                "resolution": "1080p",
                "framerate": 30,
                "camera_type": "zoom",
                "encoding": "h264",
                "video_id": "recording-start-test",
            },
        )

    def test_optional_cleanup_uses_existing_recording_stop_method(self):
        client = FakeClient()

        rc = run_recording_start_check(client, build_args(stop_after_start=True))

        self.assertEqual(rc, 0)
        self.assertEqual([method for method, _, _, _ in client.calls], [
            "camera_recording_start",
            "camera_recording_stop",
        ])
        stop_method, stop_data, stop_channel, stop_allowed = client.calls[1]
        self.assertEqual(stop_method, "camera_recording_stop")
        self.assertEqual(stop_channel, "services")
        self.assertEqual(stop_allowed, {0})
        self.assertEqual(
            stop_data,
            {
                "payload_index": "52-0-0",
                "camera_index": 0,
                "camera_type": "zoom",
                "video_id": "recording-start-test",
            },
        )

    def test_build_payload_can_omit_optional_fields(self):
        payload = build_recording_start_payload(
            build_args(camera_type="", encoding="", video_id="", file_name="manual-video.mp4")
        )

        self.assertEqual(
            payload,
            {
                "payload_index": "52-0-0",
                "camera_index": 0,
                "resolution": "1080p",
                "framerate": 30,
                "file_name": "manual-video.mp4",
            },
        )


if __name__ == "__main__":
    unittest.main()
