from __future__ import annotations

import sys
import unittest
from argparse import Namespace
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "tools"))

from mqtt_payload_gimbal_reset_modes_test import (
    build_gimbal_reset_payload,
    parse_reset_modes,
    run_reset_mode_checks,
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
                "payload_index": data.get("payload_index"),
                "reset_mode": data.get("reset_mode"),
            },
        }


def build_args(**overrides):
    values = {
        "config": "config/gateway.yaml",
        "payload_index": "52-0-0",
        "timeout": 5.0,
        "sn": "EDGEGW001",
        "reset_mode": ["0,1,2,3"],
        "query_status": False,
        "manual_confirm": False,
        "pre_position": False,
        "pre_pitch": -20.0,
        "pre_yaw": 25.0,
        "settle_seconds": 0.0,
        "stop_on_failure": False,
    }
    values.update(overrides)
    return Namespace(**values)


class PayloadGimbalResetModesToolTest(unittest.TestCase):
    def test_build_payload_uses_existing_gimbal_reset_fields(self):
        payload = build_gimbal_reset_payload("99-1-2", 3)

        self.assertEqual(payload, {"payload_index": "99-1-2", "reset_mode": 3})

    def test_parse_reset_modes_accepts_repeatable_and_csv(self):
        self.assertEqual(parse_reset_modes(["0,1", "2", "3"]), [0, 1, 2, 3])

    def test_runs_reset_modes_through_services_gimbal_reset_only(self):
        client = FakeClient()

        rc = run_reset_mode_checks(client, build_args(reset_mode=["0,2"]))

        self.assertEqual(rc, 0)
        self.assertEqual(
            client.calls,
            [
                ("gimbal_reset", {"payload_index": "52-0-0", "reset_mode": 0}, "services", {0}),
                ("gimbal_reset", {"payload_index": "52-0-0", "reset_mode": 2}, "services", {0}),
            ],
        )

    def test_optional_status_and_pre_position_use_existing_services_methods(self):
        client = FakeClient()

        rc = run_reset_mode_checks(
            client,
            build_args(reset_mode=["1"], query_status=True, pre_position=True),
        )

        self.assertEqual(rc, 0)
        methods = [call[0] for call in client.calls]
        self.assertEqual(methods, ["gimbal_rotate", "gimbal_status_query", "gimbal_reset", "gimbal_status_query"])
        self.assertTrue(all(call[2] == "services" for call in client.calls))


if __name__ == "__main__":
    unittest.main()
