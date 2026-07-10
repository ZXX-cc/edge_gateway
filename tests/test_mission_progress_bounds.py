import unittest

from src.mission.manager import MissionManager


class MissionProgressBoundsTest(unittest.TestCase):
    def test_waypoint_index_validation_rejects_stale_or_out_of_range_values(self):
        manager = MissionManager()
        manager._current_wayline = [{"seq": 0}, {"seq": 1}, {"seq": 2}]

        self.assertTrue(manager._is_valid_current_waypoint_index(0))
        self.assertTrue(manager._is_valid_current_waypoint_index("2"))
        self.assertFalse(manager._is_valid_current_waypoint_index(-1))
        self.assertFalse(manager._is_valid_current_waypoint_index(3))
        self.assertFalse(manager._is_valid_current_waypoint_index("bad"))

    def test_completed_mission_reports_full_progress(self):
        manager = MissionManager()
        manager._current_wayline = [{"seq": 0}, {"seq": 1}, {"seq": 2}]
        manager._current_waypoint_idx = 2
        manager._status = manager.MISSION_COMPLETED

        self.assertEqual(manager._current_progress_percent(), 100)


if __name__ == "__main__":
    unittest.main()
