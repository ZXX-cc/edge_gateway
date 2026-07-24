from __future__ import annotations

import sys
import unittest
from argparse import Namespace
from pathlib import Path
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "tools"))

from mqtt_viewlink_camera_mode_test import _download_photo, _switch_camera_mode
from viewlink_media_download import latest_media_link_by_http_metadata, latest_media_link


class FakeClient:
    def __init__(self, record_mode: int = 1) -> None:
        self.record_mode = record_mode
        self.calls: list[tuple[str, dict]] = []

    def request(self, method, data, timeout, **kwargs):
        self.calls.append((method, data))
        if method == "camera_mode_switch":
            return 0, {
                "data": {
                    "result": 0,
                    "mode": data["mode"],
                    "viewlink_result": {"success": True},
                }
            }
        if method == "viewlink_get_record_mode":
            return 0, {
                "data": {
                    "result": 0,
                    "viewlink_result": {
                        "success": True,
                        "mode": self.record_mode,
                        "mode_name": {0: "无模式", 1: "拍照模式", 2: "录像模式"}.get(self.record_mode, "未知"),
                    },
                }
            }
        return 1, None


def build_args(**overrides):
    values = {
        "payload_index": "52-0-0",
        "camera_type": "zoom",
        "timeout": 5.0,
        "allow_local_mode_only": False,
        "verify_record_mode": True,
        "mode_settle": 0.0,
        "require_record_mode": False,
    }
    values.update(overrides)
    return Namespace(**values)


class ViewLinkCameraModeToolTest(unittest.TestCase):
    def test_switch_camera_mode_reads_back_viewlink_record_mode(self):
        client = FakeClient(record_mode=1)

        rc = _switch_camera_mode(client, build_args(), "photo")

        self.assertEqual(rc, 0)
        self.assertEqual(
            [method for method, _ in client.calls],
            ["camera_mode_switch", "viewlink_get_record_mode"],
        )

    def test_switch_camera_mode_fails_when_readback_does_not_match(self):
        client = FakeClient(record_mode=2)

        rc = _switch_camera_mode(client, build_args(), "photo")

        self.assertEqual(rc, 31)

    def test_switch_camera_mode_allows_none_readback_by_default(self):
        client = FakeClient(record_mode=0)

        rc = _switch_camera_mode(client, build_args(), "photo")

        self.assertEqual(rc, 0)

    def test_switch_camera_mode_can_require_record_mode_readback(self):
        client = FakeClient(record_mode=0)

        rc = _switch_camera_mode(client, build_args(require_record_mode=True), "photo")

        self.assertEqual(rc, 31)

    def test_latest_media_link_uses_timestamp_like_filename(self):
        urls = [
            "http://device/download/IMG_20260724_101500.jpg",
            "http://device/download/IMG_20260724_101400.jpg",
        ]

        self.assertEqual(latest_media_link(urls), urls[0])

    def test_latest_media_link_prefers_http_last_modified(self):
        urls = [
            "http://device/download/new_name_old_file.jpg",
            "http://device/download/old_name_new_file.jpg",
        ]
        modified = {
            urls[0]: 100.0,
            urls[1]: 200.0,
        }

        with patch("viewlink_media_download.remote_last_modified", side_effect=lambda url, timeout: modified[url]):
            self.assertEqual(latest_media_link_by_http_metadata(urls, 1.0), urls[1])

    def test_download_photo_waits_for_new_media_after_capture(self):
        args = build_args(
            media_url="http://device/download/",
            media_timeout=1.0,
            media_poll_timeout=0.0,
            media_poll_interval=0.1,
            download_media=False,
            media_max_depth=1,
        )
        old_url = "http://device/download/IMG_20260724_101400.jpg"
        new_url = "http://device/download/IMG_20260724_101500.jpg"

        with patch("mqtt_viewlink_camera_mode_test.discover_media_links", return_value=[old_url, new_url]):
            rc = _download_photo(args, {old_url})

        self.assertEqual(rc, 0)


if __name__ == "__main__":
    unittest.main()
