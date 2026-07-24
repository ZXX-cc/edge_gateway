from __future__ import annotations

import sys
import unittest
from argparse import Namespace
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "tools"))

from mqtt_viewlink_stream_recording_impact_test import run_test


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
        if method == "camera_recording_start":
            return 0, {"data": {"result": 0}}
        if method == "camera_recording_stop":
            return 0, {"data": {"result": 0, "record_time": 8, "recording": False}}
        if method == "camera_mode_switch":
            return 0, {
                "data": {
                    "result": 0,
                    "mode": data["mode"],
                    "viewlink_result": {"success": True},
                }
            }
        return 1, None


class FakeMonitor:
    def __init__(self) -> None:
        self.last_frame = 200
        self.max_frame_gap = 0.4
        self.unexpected_exit = False
        self.events = []

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
        "record_duration": 0.0,
        "stream_observe": 0.0,
        "stream_observe_after_video": 0.0,
        "stream_observe_after_retrieve": 0.0,
        "switch_video_before_recording": False,
        "switch_video_after_recording": True,
        "stop_recording_on_error": True,
        "media_url": "",
        "retrieve_recording": False,
        "media_timeout": 1.0,
        "media_poll_timeout": 0.0,
        "media_poll_interval": 0.1,
        "media_max_depth": 1,
        "play_retrieved_video": False,
        "download_retrieved_video": False,
        "retrieve_duration": 0.0,
        "retrieve_timeout": 1.0,
        "output_dir": "unused",
        "payload_index": "52-0-0",
        "camera_index": 0,
        "camera_type": "zoom",
        "resolution": "1080p",
        "framerate": 30,
        "encoding": "h264",
        "allow_local_mode_only": False,
        "verify_record_mode": True,
        "mode_settle": 0.0,
        "require_record_mode": False,
        "verbose": False,
        "stall_threshold": 3.0,
    }
    values.update(overrides)
    return Namespace(**values)


class ViewLinkStreamRecordingImpactToolTest(unittest.TestCase):
    def test_direct_record_then_video_skips_pre_recording_mode_switch(self):
        client = FakeClient()

        rc = run_test(client, build_args(), monitor=FakeMonitor(), sleep_fn=lambda _: None)

        self.assertEqual(rc, 0)
        self.assertEqual(
            [method for method, _ in client.calls],
            [
                "viewlink_get_record_mode",
                "viewlink_get_record_mode",
                "camera_recording_start",
                "camera_recording_stop",
                "camera_mode_switch",
                "viewlink_get_record_mode",
            ],
        )
        switch_calls = [data for method, data in client.calls if method == "camera_mode_switch"]
        self.assertEqual(len(switch_calls), 1)
        self.assertEqual(switch_calls[0]["mode"], "video")


if __name__ == "__main__":
    unittest.main()
