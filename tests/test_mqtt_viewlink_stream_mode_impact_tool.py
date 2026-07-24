from __future__ import annotations

import sys
import unittest
from argparse import Namespace
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "tools"))

from mqtt_viewlink_stream_mode_impact_test import run_test


class FakeClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def request(self, method, data, timeout, **kwargs):
        self.calls.append((method, data))
        if method == "viewlink_get_record_mode":
            return 0, {
                "data": {
                    "result": 0,
                    "viewlink_result": {
                        "success": True,
                        "mode": 2,
                        "mode_name": "录像模式",
                    },
                }
            }
        if method == "camera_mode_switch":
            return 0, {
                "data": {
                    "result": 0,
                    "mode": data["mode"],
                    "viewlink_result": {"success": True},
                }
            }
        if method == "camera_photo_take":
            return 0, {"data": {"result": 0}}
        return 1, None


class FakeMonitor:
    def __init__(self) -> None:
        self.last_frame = 100
        self.max_frame_gap = 0.2
        self.unexpected_exit = False
        self.events = []
        self.snapshots: list[str] = []

    def start(self) -> bool:
        return True

    def stop(self) -> None:
        pass

    def snapshot(self):
        return {
            "frames": self.last_frame,
            "seconds_since_last_frame": 0.1,
            "max_frame_gap": self.max_frame_gap,
            "ffmpeg_running": True,
        }

    def affected(self) -> bool:
        return False


def build_args(**overrides):
    values = {
        "timeout": 5.0,
        "rtsp_url": "rtsp://example/live",
        "stream_warmup": 0.0,
        "stream_observe": 0.0,
        "stream_observe_after_video": 0.0,
        "direct_photo_only": False,
        "direct_photo_then_video": True,
        "switch_video_after_photo": False,
        "take_photo": False,
        "media_url": "",
        "mode_sequence": ["photo"],
        "payload_index": "52-0-0",
        "camera_index": 0,
        "camera_type": "zoom",
        "allow_local_mode_only": False,
        "verify_record_mode": True,
        "mode_settle": 0.0,
        "require_record_mode": False,
        "photo_wait": 0.0,
        "download_media": False,
        "verbose": False,
        "stall_threshold": 3.0,
    }
    values.update(overrides)
    return Namespace(**values)


class ViewLinkStreamModeImpactToolTest(unittest.TestCase):
    def test_direct_photo_then_video_skips_pre_photo_mode_switch(self):
        client = FakeClient()

        rc = run_test(client, build_args(), monitor=FakeMonitor(), sleep_fn=lambda _: None)

        self.assertEqual(rc, 0)
        methods = [method for method, _ in client.calls]
        self.assertEqual(
            methods,
            [
                "viewlink_get_record_mode",
                "viewlink_get_record_mode",
                "camera_photo_take",
                "camera_mode_switch",
                "viewlink_get_record_mode",
            ],
        )
        switch_calls = [data for method, data in client.calls if method == "camera_mode_switch"]
        self.assertEqual(len(switch_calls), 1)
        self.assertEqual(switch_calls[0]["mode"], "video")


if __name__ == "__main__":
    unittest.main()
