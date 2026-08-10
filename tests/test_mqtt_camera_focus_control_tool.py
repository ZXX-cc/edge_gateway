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
    ensure_real_gateway_ready,
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
        "focus_values": "0,32768,65535",
        "focus_speed": 2,
        "focus_duration": 0.1,
        "focus_reset_distance": None,
        "focus_reset_wait": 0.3,
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
        "observe_wait": 0.0,
        "skip_gateway_preflight": False,
    }
    values.update(overrides)
    return Namespace(**values)


def ok_reply(method):
    return {"method": method, "data": {"result": 0}}


def gateway_state_reply(device="bound"):
    return {
        "method": "gateway_state_query",
        "data": {
            "result": 0,
            "ready": True,
            "state": {"device": device},
        },
    }


def unsupported_reply(method):
    return {"method": method, "data": {"result": DEVICE_STATE_UNSUPPORTED}}


class CameraFocusControlToolTest(unittest.TestCase):
    def test_cases_use_only_existing_cloud_focus_methods(self):
        cases = build_cases(args())

        self.assertTrue(cases)
        self.assertEqual({case.method for case in cases}.issubset(CLOUD_METHODS), True)
        self.assertTrue(all(not case.method.startswith("viewlink_") for case in cases))
        self.assertNotIn("gateway_state_query", {case.method for case in cases})

    def test_strict_default_requires_absolute_and_point_focus_success(self):
        focus_cases = {
            case.method: case
            for case in build_cases(args())
            if case.method in {"camera_focus_value_set", "camera_point_focus_action"}
            and case.expect_success
        }

        self.assertTrue(focus_cases["camera_focus_value_set"].expect_success)
        self.assertEqual(focus_cases["camera_focus_value_set"].allowed_results, {0})
        self.assertTrue(focus_cases["camera_point_focus_action"].expect_success)
        self.assertEqual(focus_cases["camera_point_focus_action"].allowed_results, {0})

    def test_viewlink_limitation_mode_can_probe_current_unsupported_behavior(self):
        focus_cases = {
            case.method: case
            for case in build_cases(args(allow_viewlink_focus_limitations=True))
            if case.method in {"camera_focus_value_set", "camera_point_focus_action"}
            and "非法" not in case.label
        }

        self.assertFalse(focus_cases["camera_focus_value_set"].expect_success)
        self.assertEqual(focus_cases["camera_focus_value_set"].allowed_results, {DEVICE_STATE_UNSUPPORTED})
        self.assertFalse(focus_cases["camera_point_focus_action"].expect_success)
        self.assertEqual(focus_cases["camera_point_focus_action"].allowed_results, {DEVICE_STATE_UNSUPPORTED})

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

    def test_focus_values_expand_to_visual_sweep_cases_after_manual_mode(self):
        cases = build_cases(args())

        self.assertEqual(cases[0].label, "对焦复位-切换手动前")
        self.assertEqual(cases[0].method, "camera_focus_value_set")
        self.assertEqual(cases[1].method, "camera_focus_mode_set")
        self.assertEqual(cases[1].data["mode"], "manual_focus")
        sweep = [
            case
            for case in cases
            if case.method == "camera_focus_value_set" and case.label.startswith("绝对对焦值-")
        ]
        self.assertEqual([case.data["focus_distance"] for case in sweep], [0.0, 32768.0, 65535.0])

    def test_manual_to_auto_focus_requires_reset_step(self):
        cases = build_cases(args(focus_distance=5.0, focus_reset_distance=12.0))

        manual_index = next(
            index
            for index, case in enumerate(cases)
            if case.method == "camera_focus_mode_set" and case.data["mode"] == "manual_focus"
        )
        auto_index = next(
            index
            for index, case in enumerate(cases)
            if case.method == "camera_focus_mode_set" and case.data["mode"] == "auto_focus"
        )
        between = cases[manual_index + 1:auto_index]

        self.assertTrue(between)
        self.assertEqual(between[-1].label, "对焦复位-切换自动前")
        self.assertEqual(between[-1].method, "camera_focus_value_set")
        self.assertEqual(between[-1].data["focus_distance"], 12.0)
        self.assertIn("不能直接", between[-1].note)

    def test_auto_to_manual_focus_requires_reset_step_before_manual(self):
        cases = build_cases(args(focus_distance=5.0, focus_reset_distance=12.0))

        manual_index = next(
            index
            for index, case in enumerate(cases)
            if case.method == "camera_focus_mode_set" and case.data["mode"] == "manual_focus"
        )

        self.assertGreater(manual_index, 0)
        self.assertEqual(cases[manual_index - 1].label, "对焦复位-切换手动前")
        self.assertEqual(cases[manual_index - 1].method, "camera_focus_value_set")
        self.assertEqual(cases[manual_index - 1].data["focus_distance"], 12.0)
        self.assertIn("不能直接", cases[manual_index - 1].note)

    def test_auto_suite_to_continuous_enum_requires_reset_step(self):
        cases = build_cases(args(focus_distance=5.0, focus_reset_distance=12.0))

        continuous_index = next(
            index
            for index, case in enumerate(cases)
            if case.method == "camera_focus_mode_set" and case.data["mode"] == "continuous_auto_focus"
        )

        self.assertGreater(continuous_index, 0)
        self.assertEqual(cases[continuous_index - 1].label, "对焦复位-连续自动枚举前")
        self.assertEqual(cases[continuous_index - 1].method, "camera_focus_value_set")

    def test_gateway_preflight_uses_cloud_services_method(self):
        client = FakeFocusClient([(0, gateway_state_reply())])

        ok, detail = ensure_real_gateway_ready(client, args(sn="EDGEGW001"))

        self.assertTrue(ok, detail)
        self.assertEqual(client.calls[0][0], "gateway_state_query")
        self.assertEqual(client.calls[0][2], "services")
        self.assertEqual(detail["device"], "bound")

    def test_gateway_preflight_rejects_unbound_gateway(self):
        client = FakeFocusClient([(0, gateway_state_reply(device="unbound"))])

        ok, detail = ensure_real_gateway_ready(client, args(sn="EDGEGW001"))

        self.assertFalse(ok)
        self.assertIn("error", detail)


if __name__ == "__main__":
    unittest.main()
