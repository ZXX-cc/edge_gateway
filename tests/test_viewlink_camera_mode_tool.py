from __future__ import annotations

import sys
import unittest
from argparse import Namespace
from pathlib import Path
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "tools"))

from mqtt_viewlink_camera_mode_test import _switch_camera_mode
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
                        "mode_name": "拍照模式" if self.record_mode == 1 else "录像模式",
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


if __name__ == "__main__":
    unittest.main()
