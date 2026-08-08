from __future__ import annotations

import sys
import unittest
from argparse import Namespace
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "tools"))

from mqtt_dock3_ir_metering_real_visual_test import (  # noqa: E402
    CLOUD_METHODS,
    VisualCase,
    build_visual_cases,
    evaluate_visual_case,
    run_real_visual_ir_test,
)


class FakeIrClient:
    def __init__(self):
        self.calls: list[tuple[str, dict, str]] = []

    def service(self, method, data, timeout):
        self.calls.append((method, data, "services"))
        if method == "ir_display_mode_set":
            return 0, {"data": {"result": 0, "viewlink_result": {"success": True, "frame_hex": ["AA"]}}}
        if method == "ir_metering_point_set":
            return 0, {
                "data": {
                    "result": 0,
                    "protocol_point": {"x": 0, "y": 0},
                    "viewlink_result": {"success": True, "frame_hex": ["AA", "BB"]},
                }
            }
        if method == "ir_metering_area_set":
            return 0, {
                "data": {
                    "result": 0,
                    "protocol_area": {"left": -1, "top": -1, "right": 1, "bottom": 1},
                    "viewlink_result": {"success": True, "frame_hex": ["AA", "BB", "CC"]},
                }
            }
        return 0, {"data": {"result": 0, "viewlink_result": {"success": True, "frame_hex": ["AA"]}}}


def args(**overrides):
    values = {
        "config": "config/gateway.yaml",
        "timeout": 5.0,
        "payload_index": "52-0-0",
        "extra_payload_index": [],
        "extra_payload_should_succeed": False,
        "camera_type": "ir",
        "frame_width": 1280,
        "frame_height": 720,
        "metering_modes": ["point", "area", "full_screen"],
        "interval_frames": 4,
        "point_x": 0.5,
        "point_y": 0.5,
        "area_x": 0.25,
        "area_y": 0.25,
        "area_width": 0.5,
        "area_height": 0.5,
        "invalid_x": 1.2,
        "invalid_y": -0.1,
        "invalid_area_x": 0.8,
        "invalid_area_y": 0.8,
        "invalid_area_width": 0.5,
        "invalid_area_height": 0.5,
        "invalid_small_area_width": 0.0,
        "invalid_small_area_height": -0.1,
        "skip_invalid": True,
        "video_url": "rtsp://example.invalid/main",
        "ffmpeg": "ffmpeg",
        "snapshot_timeout": 1.0,
        "output_dir": "logs/test_ir_metering_real_visual",
        "after_wait": 0.0,
        "capture_after_each": False,
        "require_snapshots": False,
        "switch_ir_display": True,
        "display_mode": "ir",
        "palette": "white_hot",
        "stop_on_failure": False,
        "verbose_reply": False,
        "open_report": False,
    }
    values.update(overrides)
    return Namespace(**values)


class Dock3IrMeteringRealVisualToolTest(unittest.TestCase):
    def test_build_cases_use_only_existing_cloud_methods(self):
        cases = build_visual_cases(args(skip_invalid=False, extra_payload_index=["52-1-0"]))

        self.assertTrue(cases)
        self.assertTrue(all(case.method in CLOUD_METHODS for case in cases))
        self.assertIn("ir_metering_mode_set", [case.method for case in cases])
        self.assertIn("ir_metering_point_set", [case.method for case in cases])
        self.assertIn("ir_metering_area_set", [case.method for case in cases])

    def test_invalid_success_reply_is_failure(self):
        case = VisualCase(
            "invalid point",
            "ir_metering_point_set",
            {"payload_index": "52-0-0", "x": 1.2, "y": -0.1},
            False,
            True,
        )

        status, detail = evaluate_visual_case(
            case,
            0,
            {"data": {"result": 0, "viewlink_result": {"success": True, "frame_hex": ["AA"]}}},
        )

        self.assertEqual(status, "fail")
        self.assertIn("invalid input was accepted", detail)

    def test_run_flow_uses_services_cloud_methods(self):
        client = FakeIrClient()
        rc = run_real_visual_ir_test(client, args(), sleep_fn=lambda _: None)

        self.assertEqual(rc, 0)
        self.assertEqual({channel for _, _, channel in client.calls}, {"services"})
        self.assertTrue(all(method in CLOUD_METHODS for method, _, _ in client.calls))
        self.assertEqual(
            [method for method, _, _ in client.calls],
            [
                "ir_display_mode_set",
                "ir_metering_mode_set",
                "ir_metering_mode_set",
                "ir_metering_mode_set",
                "ir_metering_point_set",
                "ir_metering_area_set",
            ],
        )


if __name__ == "__main__":
    unittest.main()
