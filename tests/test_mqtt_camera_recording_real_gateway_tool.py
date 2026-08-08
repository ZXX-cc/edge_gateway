from __future__ import annotations

import sys
import unittest
from argparse import Namespace
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "tools"))

from mqtt_camera_recording_real_gateway_test import (
    run_disconnected_expectation,
    run_recording_acceptance,
)


class FakeClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict, str]] = []
        self.stats = {"accepted": 0}

    def request(self, method, data, timeout, **kwargs):
        self.calls.append((method, data, kwargs.get("channel")))
        self.stats["accepted"] += 1
        call_no = len(self.calls)
        if method == "camera_recording_stop" and call_no == 1:
            return 0, {"method": method, "data": {"result": -1}, "msg": "Not recording"}
        if method == "camera_recording_start" and call_no == 2:
            return 0, {"method": method, "data": {"result": 0}}
        if method == "camera_recording_start" and call_no == 3:
            return 0, {"method": method, "data": {"result": -1}, "msg": "Already recording"}
        if method == "camera_recording_stop" and call_no == 4:
            return 0, {"method": method, "data": {"result": 0, "recording": False, "record_time": 2}}
        if method == "camera_recording_stop" and call_no == 5:
            return 0, {"method": method, "data": {"result": -1}, "msg": "Not recording"}
        return 1, None

    def stale_total(self):
        return 0

    def unrelated_reply_total(self):
        return 0


class DisconnectedFakeClient(FakeClient):
    def request(self, method, data, timeout, **kwargs):
        self.calls.append((method, data, kwargs.get("channel")))
        return 0, {"method": method, "data": {"result": -1}, "msg": "device disconnected"}


class SanitizedStopFakeClient(FakeClient):
    def request(self, method, data, timeout, **kwargs):
        self.calls.append((method, data, kwargs.get("channel")))
        self.stats["accepted"] += 1
        call_no = len(self.calls)
        if method == "camera_recording_stop" and call_no == 1:
            return 0, {"method": method, "data": {"result": -1}, "msg": "Not recording"}
        if method == "camera_recording_start" and call_no == 2:
            return 0, {"method": method, "data": {"result": 0}}
        if method == "camera_recording_start" and call_no == 3:
            return 0, {"method": method, "data": {"result": -1}, "msg": "Already recording"}
        if method == "camera_recording_stop" and call_no == 4:
            return 0, {"method": method, "data": {"result": 0}}
        if method == "camera_recording_stop" and call_no == 5:
            return 0, {"method": method, "data": {"result": -1}, "msg": "Not recording"}
        return 1, None


def build_args(**overrides):
    values = {
        "config": "config/gateway.yaml",
        "host": "127.0.0.1",
        "port": 1883,
        "sn": "gateway-sn",
        "timeout": 5.0,
        "payload_index": "52-0-0",
        "camera_index": 0,
        "camera_type": "zoom",
        "resolution": "1080p",
        "framerate": 30,
        "encoding": "h264",
        "video_id": "",
        "file_name": "",
        "record_duration": 0.0,
        "min_record_time": 1.0,
        "strict_initial_idle": False,
        "media_url": "",
        "media_timeout": 1.0,
        "media_poll_timeout": 0.0,
        "media_poll_interval": 0.1,
        "media_max_depth": 1,
        "download_recording": False,
        "output_dir": "unused",
        "verify_media_params": False,
        "ffprobe": "ffprobe",
        "ffprobe_timeout": 1.0,
        "framerate_tolerance": 1.0,
        "verbose": False,
    }
    values.update(overrides)
    return Namespace(**values)


class CameraRecordingRealGatewayToolTest(unittest.TestCase):
    def test_normal_flow_uses_only_existing_recording_cloud_methods(self):
        client = FakeClient()

        rc = run_recording_acceptance(client, build_args())

        self.assertEqual(rc, 0)
        self.assertEqual(
            [method for method, _, _ in client.calls],
            [
                "camera_recording_stop",
                "camera_recording_start",
                "camera_recording_start",
                "camera_recording_stop",
                "camera_recording_stop",
            ],
        )
        self.assertEqual({channel for _, _, channel in client.calls}, {"services"})

    def test_sanitized_stop_reply_with_only_result_zero_can_continue(self):
        client = SanitizedStopFakeClient()

        rc = run_recording_acceptance(client, build_args())

        self.assertEqual(rc, 0)
        self.assertEqual(
            [method for method, _, _ in client.calls],
            [
                "camera_recording_stop",
                "camera_recording_start",
                "camera_recording_start",
                "camera_recording_stop",
                "camera_recording_stop",
            ],
        )

    def test_disconnected_flow_uses_start_and_stop_only(self):
        client = DisconnectedFakeClient()

        rc = run_disconnected_expectation(client, build_args())

        self.assertEqual(rc, 0)
        self.assertEqual(
            [method for method, _, _ in client.calls],
            ["camera_recording_start", "camera_recording_stop"],
        )
        self.assertEqual({channel for _, _, channel in client.calls}, {"services"})


if __name__ == "__main__":
    unittest.main()
