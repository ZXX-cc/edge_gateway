import os
import sys
import unittest


PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOLS_DIR = os.path.join(PROJECT_ROOT, "tools")
if TOOLS_DIR not in sys.path:
    sys.path.insert(0, TOOLS_DIR)

from mqtt_dock3_wayline_test import validate_real_sitl_progress  # noqa: E402


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


if __name__ == "__main__":
    unittest.main()
