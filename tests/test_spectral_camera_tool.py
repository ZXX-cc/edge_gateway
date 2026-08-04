from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "tools"))

from mqtt_spectral_camera_test import (  # noqa: E402
    SPECTRAL_PAYLOAD_INDEX,
    SPECTRAL_VIDEO_INDEX,
    files_from_reply,
    find_file,
    spectral_video_id,
    write_spectral_kmz,
)
from src.cloud_api.dock3_wayline import parse_wayline_file  # noqa: E402


class SpectralCameraToolTest(unittest.TestCase):
    def test_spectral_video_id_uses_payload_and_video_index(self):
        self.assertEqual(
            spectral_video_id("EDGEGW001"),
            f"EDGEGW001/{SPECTRAL_PAYLOAD_INDEX}/{SPECTRAL_VIDEO_INDEX}",
        )

    def test_generated_wayline_contains_spectral_photo_action(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "spectral.kmz"
            write_spectral_kmz(path)

            summary, waypoints = parse_wayline_file({"path": str(path)})

        self.assertTrue(summary["parsed"])
        self.assertEqual(summary["waypoint_count"], 2)
        action = waypoints[0]["actions"][0]
        self.assertEqual(action["action_type"], "photo")
        self.assertEqual(action["payload_index"], SPECTRAL_PAYLOAD_INDEX)
        self.assertEqual(action["camera_type"], "multispectral")

    def test_find_file_matches_name_or_file_name(self):
        payload = {"data": {"files": [{"name": "a.jpg"}, {"file_name": "b.jpg"}]}}
        files = files_from_reply(payload)

        self.assertEqual(find_file(files, "a.jpg")["name"], "a.jpg")
        self.assertEqual(find_file(files, "b.jpg")["file_name"], "b.jpg")
        self.assertIsNone(find_file(files, "missing.jpg"))


if __name__ == "__main__":
    unittest.main()
