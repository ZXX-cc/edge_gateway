import os
import sys
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOLS_DIR = os.path.join(PROJECT_ROOT, "tools")
if TOOLS_DIR not in sys.path:
    sys.path.insert(0, TOOLS_DIR)

from mqtt_dock3_wayline_test import (  # noqa: E402
    validate_real_sitl_progress,
    write_real_sitl_waypoints,
)
from src.cloud_api.dock3_wayline import parse_wayline_file  # noqa: E402


def progress_payload(*, flight_id="flight-001", source="/tmp/test.kmz", count=3, index=1, status="in_progress"):
    return {
        "data": {
            "output": {
                "status": status,
                "ext": {
                    "flight_id": flight_id,
                    "current_waypoint_index": index,
                    "waypoint_count": count,
                    "file": {"source": source, "waypoint_count": count},
                },
            },
        },
    }


class Dock3WaylineToolTest(unittest.TestCase):
    def setUp(self):
        self.expected_file = {"source": "/tmp/test.kmz", "waypoint_count": 3}

    def test_real_progress_accepts_matching_running_task(self):
        rc, status, ext = validate_real_sitl_progress(
            progress_payload(), "flight-001", self.expected_file
        )
        self.assertEqual((rc, status), (0, "in_progress"))
        self.assertEqual(ext["current_waypoint_index"], 1)

    def test_real_progress_rejects_another_task_file(self):
        rc, _, _ = validate_real_sitl_progress(
            progress_payload(source="/tmp/other.waypoints", count=7), "flight-001", self.expected_file
        )
        self.assertEqual(rc, 20)

    def test_completed_real_progress_must_end_at_last_waypoint(self):
        rc, _, _ = validate_real_sitl_progress(
            progress_payload(index=1, status="ok"), "flight-001", self.expected_file
        )
        self.assertEqual(rc, 22)

    def test_real_sitl_fixture_starts_with_takeoff_command(self):
        with tempfile.TemporaryDirectory() as tmp:
            mission_path = Path(tmp) / "sitl.waypoints"
            write_real_sitl_waypoints(mission_path)
            summary, waypoints = parse_wayline_file({"url": mission_path.as_uri()})

        self.assertEqual(summary["format"], "waypoints")
        self.assertEqual([waypoint["command"] for waypoint in waypoints], [22, 16, 16])
        self.assertEqual([waypoint["seq"] for waypoint in waypoints], [0, 1, 2])


if __name__ == "__main__":
    unittest.main()
