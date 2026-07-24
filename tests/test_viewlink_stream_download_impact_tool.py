from __future__ import annotations

import sys
import unittest
from argparse import Namespace
from pathlib import Path
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "tools"))

from viewlink_stream_download_impact_test import DownloadResult, run_test


class FakeMonitor:
    def __init__(self, affected: bool = False) -> None:
        self.last_frame = 320
        self.max_frame_gap = 0.5
        self.unexpected_exit = False
        self.events = []
        self._affected = affected

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
        return self._affected


def build_args(**overrides):
    values = {
        "config": "config/gateway.yaml",
        "rtsp_url": "rtsp://example/live",
        "file_url": ["http://example/media/demo.mp4"],
        "media_url": "",
        "media_timeout": 1.0,
        "media_max_depth": 1,
        "ext": [],
        "latest_only": True,
        "max_files": 3,
        "repeat": 1,
        "concurrency": 1,
        "chunk_size": 1024,
        "output_dir": "unused",
        "stream_warmup": 0.0,
        "stream_observe": 0.0,
        "stall_threshold": 3.0,
        "fail_on_impact": False,
    }
    values.update(overrides)
    return Namespace(**values)


class ViewLinkStreamDownloadImpactToolTest(unittest.TestCase):
    def test_run_test_downloads_direct_file_url_while_monitoring_stream(self):
        result = DownloadResult(
            url="http://example/media/demo.mp4",
            target="unused/demo.mp4",
            bytes=2048,
            duration_seconds=0.25,
            ok=True,
        )

        with patch("viewlink_stream_download_impact_test._download_many", return_value=[result]) as download_many:
            rc = run_test(build_args(), monitor=FakeMonitor(), sleep_fn=lambda _: None)

        self.assertEqual(rc, 0)
        download_many.assert_called_once()
        self.assertEqual(download_many.call_args.args[1], ["http://example/media/demo.mp4"])

    def test_run_test_can_fail_when_stream_is_affected(self):
        result = DownloadResult(
            url="http://example/media/demo.mp4",
            target="unused/demo.mp4",
            bytes=2048,
            duration_seconds=0.25,
            ok=True,
        )

        with patch("viewlink_stream_download_impact_test._download_many", return_value=[result]):
            rc = run_test(build_args(fail_on_impact=True), monitor=FakeMonitor(affected=True), sleep_fn=lambda _: None)

        self.assertEqual(rc, 60)


if __name__ == "__main__":
    unittest.main()
