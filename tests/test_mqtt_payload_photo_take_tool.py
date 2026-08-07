from __future__ import annotations

import sys
import unittest
from argparse import Namespace
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "tools"))

from mqtt_payload_photo_take_test import build_photo_payload, run_photo_take_check


class FakeClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict, str, set[int]]] = []
        self.event_calls: list[tuple[str, int, float, object]] = []

    def request(self, method, data, timeout, **kwargs):
        self.calls.append((method, data, kwargs.get("channel"), kwargs.get("allowed_results")))
        return 0, {
            "method": method,
            "data": {
                "result": 0,
                "camera_index": data["camera_index"],
                "photo_source": "viewlink_sdk",
                "viewlink_result": {"success": True},
            },
        }

    def event_count(self, method):
        return 0

    def wait_event(self, method, previous_count, timeout, expected_status=None):
        self.event_calls.append((method, previous_count, timeout, expected_status))
        return 0, {
            "method": method,
            "data": {
                "result": 0,
                "output": {
                    "status": "ok",
                    "progress": {"percent": 100},
                },
            },
        }


def build_args(**overrides):
    values = {
        "config": "config/gateway.yaml",
        "payload_index": "52-0-0",
        "camera_index": 0,
        "camera_type": "zoom",
        "file_name": "",
        "timeout": 5.0,
        "progress_timeout": 3.0,
        "expect_source": "",
        "require_media_hint": False,
        "skip_progress_event": False,
    }
    values.update(overrides)
    return Namespace(**values)


class PayloadPhotoTakeToolTest(unittest.TestCase):
    def test_uses_dock3_services_camera_photo_take(self):
        client = FakeClient()

        rc = run_photo_take_check(client, build_args())

        self.assertEqual(rc, 0)
        self.assertEqual(len(client.calls), 1)
        self.assertEqual(len(client.event_calls), 1)
        method, data, channel, allowed = client.calls[0]
        self.assertEqual(method, "camera_photo_take")
        self.assertEqual(channel, "services")
        self.assertEqual(allowed, {0})
        event_method, previous_count, timeout, expected_status = client.event_calls[0]
        self.assertEqual(event_method, "camera_photo_take_progress")
        self.assertEqual(previous_count, 0)
        self.assertEqual(timeout, 3.0)
        self.assertIsNone(expected_status)
        self.assertEqual(
            data,
            {
                "payload_index": "52-0-0",
                "camera_index": 0,
                "camera_type": "zoom",
            },
        )

    def test_build_payload_can_omit_camera_type_and_include_file_name(self):
        payload = build_photo_payload(
            build_args(camera_type="", file_name="manual-photo.jpg", camera_index=1)
        )

        self.assertEqual(
            payload,
            {
                "payload_index": "52-0-0",
                "camera_index": 1,
                "file_name": "manual-photo.jpg",
            },
        )


if __name__ == "__main__":
    unittest.main()
