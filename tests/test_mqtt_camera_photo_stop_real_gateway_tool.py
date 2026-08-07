from __future__ import annotations

import sys
import unittest
from argparse import Namespace
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "tools"))

from mqtt_camera_photo_stop_full_chain_sim_test import validate_real_gateway_reply


def build_args(**overrides):
    values = {
        "allow_success": False,
        "accept_any_failure": False,
    }
    values.update(overrides)
    return Namespace(**values)


class CameraPhotoStopRealGatewayToolTest(unittest.TestCase):
    def test_fails_when_real_gateway_returns_success(self):
        rc = validate_real_gateway_reply(
            {
                "method": "camera_photo_stop",
                "data": {"result": 0},
            },
            build_args(),
        )

        self.assertEqual(rc, 6)

    def test_passes_when_real_gateway_returns_stop_timeout_failure(self):
        rc = validate_real_gateway_reply(
            {
                "method": "camera_photo_stop",
                "data": {"result": 319999},
            },
            build_args(),
        )

        self.assertEqual(rc, 0)

    def test_other_non_zero_result_is_inconclusive_by_default(self):
        rc = validate_real_gateway_reply(
            {
                "method": "camera_photo_stop",
                "data": {"result": 325100},
            },
            build_args(),
        )

        self.assertEqual(rc, 5)

    def test_accept_any_failure_allows_other_non_zero_results(self):
        rc = validate_real_gateway_reply(
            {
                "method": "camera_photo_stop",
                "data": {"result": 325100},
            },
            build_args(accept_any_failure=True),
        )

        self.assertEqual(rc, 0)


if __name__ == "__main__":
    unittest.main()
