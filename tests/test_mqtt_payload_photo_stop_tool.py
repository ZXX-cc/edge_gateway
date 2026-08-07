from __future__ import annotations

import sys
import unittest
from argparse import Namespace
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "tools"))

from mqtt_payload_photo_stop_test import (
    build_photo_stop_payload,
    run_photo_stop_check,
    validate_photo_stop_reply,
)


class FakeClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict, str, set[int]]] = []

    def request(self, method, data, timeout, **kwargs):
        self.calls.append((method, data, kwargs.get("channel"), kwargs.get("allowed_results")))
        return 0, {
            "method": method,
            "data": {
                "result": 0,
            },
        }


def build_args(**overrides):
    values = {
        "config": "config/gateway.yaml",
        "payload_index": "52-0-0",
        "timeout": 5.0,
    }
    values.update(overrides)
    return Namespace(**values)


class PayloadPhotoStopToolTest(unittest.TestCase):
    def test_uses_only_dock3_services_camera_photo_stop(self):
        client = FakeClient()

        rc = run_photo_stop_check(client, build_args())

        self.assertEqual(rc, 0)
        self.assertEqual(len(client.calls), 1)
        method, data, channel, allowed = client.calls[0]
        self.assertEqual(method, "camera_photo_stop")
        self.assertEqual(channel, "services")
        self.assertEqual(allowed, {0})
        self.assertEqual(data, {"payload_index": "52-0-0"})

    def test_build_payload_contains_only_payload_index(self):
        payload = build_photo_stop_payload(build_args(payload_index="52-0-1"))

        self.assertEqual(payload, {"payload_index": "52-0-1"})

    def test_rejects_success_reply_when_photo_session_is_still_running(self):
        rc = validate_photo_stop_reply({
            "method": "camera_photo_stop",
            "data": {
                "result": 0,
                "running": True,
                "stopped": False,
                "timeout_seconds": 2.0,
            },
        })

        self.assertEqual(rc, 6)


if __name__ == "__main__":
    unittest.main()
