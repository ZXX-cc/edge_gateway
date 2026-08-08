from __future__ import annotations

import tempfile
import unittest
from argparse import Namespace
from pathlib import Path

import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "tools"))

from mqtt_payload_photo_control_deep_test import (
    request_stop,
    request_take,
    validate_media_truth,
)


class FakeClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict, str, set[int] | None]] = []

    def request(self, method, data, timeout, **kwargs):
        self.calls.append((method, data, kwargs.get("channel"), kwargs.get("allowed_results")))
        return 0, {"method": method, "data": {"result": 0}}


def build_args(**overrides):
    values = {
        "min_file_size": 4,
        "require_resolution": False,
        "require_capture_time": False,
        "require_local_file": True,
        "max_time_skew": 600.0,
    }
    values.update(overrides)
    return Namespace(**values)


class PayloadPhotoControlDeepToolTest(unittest.TestCase):
    def test_take_and_stop_use_only_dock3_services_methods(self):
        client = FakeClient()

        request_take(client, {"payload_index": "52-0-0"}, 5.0, allowed_results={0}, verbose=False)
        request_stop(client, {"payload_index": "52-0-0"}, 5.0, allowed_results={0}, verbose=False)

        self.assertEqual(
            client.calls,
            [
                ("camera_photo_take", {"payload_index": "52-0-0"}, "services", {0}),
                ("camera_photo_stop", {"payload_index": "52-0-0"}, "services", {0}),
            ],
        )

    def test_media_truth_rejects_fake_file_size(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "photo.jpg"
            path.write_bytes(b"real-photo-bytes")

            rc, media = validate_media_truth(
                [{"file_path": str(path), "file_size": 9999}],
                build_args(),
                label="unit",
            )

        self.assertEqual(rc, 22)
        self.assertEqual(media["file_size"], 9999)

    def test_media_truth_accepts_existing_file_with_matching_size(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "photo.jpg"
            body = b"real-photo-bytes"
            path.write_bytes(body)

            rc, media = validate_media_truth(
                [{"file_path": str(path), "file_size": len(body)}],
                build_args(),
                label="unit",
            )

        self.assertEqual(rc, 0)
        self.assertEqual(media["actual_file_size"], len(body))


if __name__ == "__main__":
    unittest.main()
