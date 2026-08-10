from __future__ import annotations

import sys
import unittest
from argparse import Namespace
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "tools"))

from mqtt_private_set_flight_mode_auto_test import (  # noqa: E402
    PRIVATE_METHOD,
    build_auto_payload,
    run_auto_mode_test,
    validate_auto_reply,
)


class FakeAutoModeClient:
    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []
        self.stats = {"accepted": 0}

    def request(self, method, data, timeout, **kwargs):
        self.calls.append((method, data, timeout, kwargs.get("channel"), kwargs.get("allowed_results")))
        self.stats["accepted"] += 1
        if not self.replies:
            return 2, None
        return self.replies.pop(0)


def args(**overrides):
    values = {
        "timeout": 15.0,
        "mode_timeout": 10.0,
        "skip_gateway_state": False,
        "verbose": False,
        "print_summary": False,
    }
    values.update(overrides)
    return Namespace(**values)


class PrivateSetFlightModeAutoToolTest(unittest.TestCase):
    def test_payload_uses_explicit_auto_mode_name_not_mode_code(self):
        payload = build_auto_payload(12.5)

        self.assertEqual(payload, {"flight_mode": "AUTO", "timeout": 12.5})
        self.assertNotIn("mode_code", payload)
        self.assertNotIn("mode", payload)

    def test_validate_auto_reply_requires_mode_and_mode_code(self):
        ok, detail = validate_auto_reply({"data": {"result": 0, "mode": "AUTO", "mode_code": 4}})

        self.assertTrue(ok, detail)

    def test_validate_auto_reply_rejects_guided_code_collision(self):
        ok, detail = validate_auto_reply({"data": {"result": 0, "mode": "GUIDED", "mode_code": 4}})

        self.assertFalse(ok)
        self.assertIn("data.mode", detail)

    def test_run_auto_mode_test_uses_services_private_method(self):
        client = FakeAutoModeClient([
            (0, {"data": {"result": 0, "state": "ready"}}),
            (0, {"data": {"result": 0, "mode": "AUTO", "mode_code": 4}}),
        ])

        rc = run_auto_mode_test(client, args())

        self.assertEqual(rc, 0)
        self.assertEqual(client.calls[0][0], "gateway_state_query")
        self.assertEqual(client.calls[0][3], "services")
        self.assertEqual(client.calls[1][0], PRIVATE_METHOD)
        self.assertEqual(client.calls[1][1], {"flight_mode": "AUTO", "timeout": 10.0})
        self.assertEqual(client.calls[1][3], "services")
        self.assertEqual(client.calls[1][4], {0})

    def test_can_skip_gateway_state_probe(self):
        client = FakeAutoModeClient([
            (0, {"data": {"result": 0, "mode": "AUTO", "mode_code": 4}}),
        ])

        rc = run_auto_mode_test(client, args(skip_gateway_state=True, mode_timeout=8.0))

        self.assertEqual(rc, 0)
        self.assertEqual([call[0] for call in client.calls], [PRIVATE_METHOD])
        self.assertEqual(client.calls[0][1], {"flight_mode": "AUTO", "timeout": 8.0})


if __name__ == "__main__":
    unittest.main()
