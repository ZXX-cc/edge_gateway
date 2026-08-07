from __future__ import annotations

import sys
import unittest
from argparse import Namespace
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "tools"))

from mqtt_payload_gimbal_reset_test import build_gimbal_reset_payload, run_gimbal_reset_check


class FakeClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict, str, set[int]]] = []

    def request(self, method, data, timeout, **kwargs):
        self.calls.append((method, data, kwargs.get("channel"), kwargs.get("allowed_results")))
        if method == "gimbal_reset":
            return 0, {
                "method": method,
                "data": {
                    "result": 0,
                    "payload_index": data["payload_index"],
                },
            }
        return 1, None


def build_args(**overrides):
    values = {
        "config": "config/gateway.yaml",
        "payload_index": "52-0-0",
        "timeout": 5.0,
    }
    values.update(overrides)
    return Namespace(**values)


class PayloadGimbalResetToolTest(unittest.TestCase):
    def test_uses_existing_dock3_services_gimbal_reset_method(self):
        client = FakeClient()

        rc = run_gimbal_reset_check(client, build_args())

        self.assertEqual(rc, 0)
        self.assertEqual(len(client.calls), 1)
        method, data, channel, allowed = client.calls[0]
        self.assertEqual(method, "gimbal_reset")
        self.assertEqual(channel, "services")
        self.assertEqual(allowed, {0})
        self.assertEqual(data, {"payload_index": "52-0-0"})

    def test_build_payload_contains_only_official_payload_index(self):
        payload = build_gimbal_reset_payload(build_args(payload_index="99-1-2"))

        self.assertEqual(payload, {"payload_index": "99-1-2"})


if __name__ == "__main__":
    unittest.main()
