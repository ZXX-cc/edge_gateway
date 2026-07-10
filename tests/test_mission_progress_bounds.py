import unittest
from types import SimpleNamespace

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

    def test_unknown_battery_default_does_not_trigger_real_mission_safety_action(self):
        manager = MissionManager()
        manager._status = manager.MISSION_RUNNING
        manager._current_wayline = [{"seq": 0}, {"seq": 1}]
        manager._get_telemetry_snapshot = lambda: {
            "batteryRemaining": 0,
            "battery_available": False,
        }

        self.assertFalse(manager._handle_battery_safety())
        self.assertEqual(manager._status, manager.MISSION_RUNNING)

    def test_monitor_reads_dispatcher_waypoint_cache_before_mavlink_socket(self):
        manager = MissionManager()
        manager.set_waypoint_manager(SimpleNamespace(
            current_waypoint=2,
            dispatcher=SimpleNamespace(_running=True),
        ))

        class SocketMustNotBeRead:
            def recv_match(self, **kwargs):
                raise AssertionError("dispatcher owns the MAVLink receive socket")

        self.assertEqual(manager._read_mission_current_index(SocketMustNotBeRead()), 2)

    def test_monitor_uses_direct_mavlink_only_without_running_dispatcher(self):
        manager = MissionManager()
        manager.set_waypoint_manager(SimpleNamespace(
            current_waypoint=2,
            dispatcher=SimpleNamespace(_running=False),
        ))

        class StandaloneMaster:
            def recv_match(self, **kwargs):
                return SimpleNamespace(seq=1)

        self.assertEqual(manager._read_mission_current_index(StandaloneMaster()), 1)


if __name__ == "__main__":
    unittest.main()
