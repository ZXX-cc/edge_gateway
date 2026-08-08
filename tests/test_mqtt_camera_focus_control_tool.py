from __future__ import annotations

import sys
import unittest
from argparse import Namespace
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "tools"))

from mqtt_camera_focus_control_test import (  # noqa: E402
    CLOUD_METHODS,
    build_cases,
    run_case,
)
from src.utils.dji_error_codes import DEVICE_STATE_UNSUPPORTED  # noqa: E402


class FakeFocusClient:
    def __init__(self, replies):
        self.replies = list(replies)
        self.calls: list[tuple[str, dict, str]] = []

    def request(self, method, data, timeout, **kwargs):
        self.calls.append((method, data, kwargs.get("channel")))
        if not self.replies:
            return 2, None
        return self.replies.pop(0)


def args(**overrides):
    values = {
        "config": "config/gateway.yaml",
        "payload_index": "52-0-0",
        "camera_index": 0,
        "camera_type": "zoom",
        "focus_distance": 5.0,
        "focus_speed": 2,
        "focus_duration": 0.1,
        "focus_x": 0.5,
        "focus_y": 0.5,
        "invalid_focus_mode": "bad_focus_mode",
        "invalid_focus_distance": 1000000.0,
        "invalid_focus_x": 1.5,
        "invalid_focus_y": -0.2,
        "allow_viewlink_focus_limitations": False,
        "timeout": 5.0,
        "verbose": False,
        "max_elapsed_ms": 3000,
        "video_url": "",
        "capture_snapshots": False,
        "require_snapshots": False,
        "snapshot_dir": "logs/camera_focus_control",
        "after_wait": 0.0,
        "ffmpeg": "ffmpeg",
        "snapshot_timeout": 1.0,
    }
    values.update(overrides)
    return Namespace(**values)


def ok_reply(method):
    return {"method": method, "data": {"result": 0}}


def unsupported_reply(method):
    return {"method": method, "data": {"result": DEVICE_STATE_UNSUPPORTED}}


class CameraFocusControlToolTest(unittest.TestCase):
    def test_cases_use_only_existing_cloud_focus_methods(self):
        cases = build_cases(args())

        self.assertTrue(cases)
        self.assertEqual({case.method for case in cases}.issubset(CLOUD_METHODS), True)
        self.assertTrue(all(not case.method.startswith("viewlink_") for case in cases))

    def test_strict_default_requires_absolute_and_point_focus_success(self):
        focus_cases = {
            case.label: case
            for case in build_cases(args())
            if case.method in {"camera_focus_value_set", "camera_point_focus_action"}
        }

        self.assertTrue(focus_cases["绝对对焦值"].expect_success)
        self.assertEqual(focus_cases["绝对对焦值"].allowed_results, {0})
        self.assertTrue(focus_cases["点对焦坐标"].expect_success)
        self.assertEqual(focus_cases["点对焦坐标"].allowed_results, {0})

    def test_viewlink_limitation_mode_can_probe_current_unsupported_behavior(self):
        focus_cases = {
            case.label: case
            for case in build_cases(args(allow_viewlink_focus_limitations=True))
            if case.method in {"camera_focus_value_set", "camera_point_focus_action"}
        }

        self.assertFalse(focus_cases["绝对对焦值"].expect_success)
        self.assertEqual(focus_cases["绝对对焦值"].allowed_results, {DEVICE_STATE_UNSUPPORTED})
        self.assertFalse(focus_cases["点对焦坐标"].expect_success)
        self.assertEqual(focus_cases["点对焦坐标"].allowed_results, {DEVICE_STATE_UNSUPPORTED})

    def test_run_case_publishes_on_services_channel(self):
        case = next(case for case in build_cases(args()) if case.method == "camera_focus_mode_set")
        client = FakeFocusClient([(0, ok_reply(case.method))])

        ok, detail = run_case(client, case, args())

        self.assertTrue(ok, detail)
        self.assertEqual(client.calls[0][0], "camera_focus_mode_set")
        self.assertEqual(client.calls[0][2], "services")

    def test_strict_focus_value_rejects_unsupported_reply(self):
        case = next(case for case in build_cases(args()) if case.method == "camera_focus_value_set")
        client = FakeFocusClient([(1, unsupported_reply(case.method))])

        ok, detail = run_case(client, case, args())

        self.assertFalse(ok)
        self.assertIn("error", detail)


if __name__ == "__main__":
    unittest.main()
