import threading
import time
import sys
import types
import unittest
from queue import Empty, Queue
from types import SimpleNamespace


class _MAVWPLoader:
    def __init__(self):
        self._items = []

    def clear(self):
        self._items.clear()

    def add(self, item):
        self._items.append(item)

    def count(self):
        return len(self._items)


if "pymavlink" not in sys.modules:
    pymavlink = types.ModuleType("pymavlink")
    pymavlink.mavutil = types.SimpleNamespace(mavlink=types.SimpleNamespace(
        MAV_CMD_NAV_WAYPOINT=16,
        MAV_FRAME_GLOBAL_RELATIVE_ALT=3,
        MAV_MISSION_TYPE_MISSION=0,
        MAV_CMD_DO_SET_MISSION_CURRENT=224,
        MAV_CMD_GET_HOME_POSITION=410,
    ))
    pymavlink.mavwp = types.SimpleNamespace(MAVWPLoader=_MAVWPLoader)
    sys.modules["pymavlink"] = pymavlink
    sys.modules["pymavlink.mavutil"] = pymavlink.mavutil
    sys.modules["pymavlink.mavwp"] = pymavlink.mavwp
else:
    pymavlink = sys.modules["pymavlink"]
    mavlink = getattr(getattr(pymavlink, "mavutil", None), "mavlink", None)
    if mavlink is None:
        pymavlink.mavutil = types.SimpleNamespace(mavlink=types.SimpleNamespace())
        mavlink = pymavlink.mavutil.mavlink
    for name, value in {
        "MAV_CMD_NAV_WAYPOINT": 16,
        "MAV_FRAME_GLOBAL_RELATIVE_ALT": 3,
        "MAV_MISSION_TYPE_MISSION": 0,
        "MAV_CMD_DO_SET_MISSION_CURRENT": 224,
        "MAV_CMD_GET_HOME_POSITION": 410,
    }.items():
        if not hasattr(mavlink, name):
            setattr(mavlink, name, value)
    if not hasattr(pymavlink, "mavwp"):
        pymavlink.mavwp = types.SimpleNamespace(MAVWPLoader=_MAVWPLoader)
        sys.modules["pymavlink.mavwp"] = pymavlink.mavwp
    elif not hasattr(pymavlink.mavwp, "MAVWPLoader"):
        pymavlink.mavwp.MAVWPLoader = _MAVWPLoader

from src.core.message_dispatcher import MessageDispatcher
from src.mission.manager import MissionManager
from src.modules.waypoint_manager import WaypointManager


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

    def test_zero_battery_available_sentinel_does_not_abort_sitl_mission(self):
        manager = MissionManager()
        manager._status = manager.MISSION_RUNNING
        manager._current_wayline = [{"seq": 0}, {"seq": 1}]
        manager._get_telemetry_snapshot = lambda: {
            "batteryRemaining": 0,
            "battery_available": True,
            "flight_mode": "AUTO",
        }

        self.assertFalse(manager._handle_battery_safety())
        self.assertEqual(manager._status, manager.MISSION_RUNNING)

    def test_low_battery_failure_is_non_retryable(self):
        failure = MissionManager._classify_task_failure("严重低电量自动降落: 0% <= 15%", "battery_safety")

        self.assertEqual(failure["category"], "hardware_or_rejected")
        self.assertFalse(failure["retryable"])

    def test_low_battery_return_releases_as_partially_done_after_drone_in_dock(self):
        events = []
        manager = MissionManager()
        manager.set_event_callback(events.append)
        manager._status = manager.MISSION_RUNNING
        manager._current_task_id = "flight-low-battery"
        manager._current_wayline = [{"seq": 0}, {"seq": 1}, {"seq": 2}]
        manager._current_waypoint_idx = 1
        manager._get_telemetry_snapshot = lambda: {
            "batteryRemaining": 20,
            "battery_available": True,
        }
        manager._get_nest_snapshot = lambda: {}
        manager._flight_control = SimpleNamespace(
            one_click_return=lambda: {"success": True, "message": "ok"}
        )
        manager.stop_mission_monitor = lambda: True
        manager._save_breakpoint_file = lambda reason="": None
        manager._run_db_coroutine = lambda coro: coro.close() if hasattr(coro, "close") else None

        self.assertTrue(manager._handle_battery_safety())
        self.assertEqual(manager._status, manager.MISSION_PAUSED)
        self.assertTrue(manager.is_busy)

        released = manager._maybe_finish_returned_task({
            "cloud_properties": {"drone_in_dock": 1},
            "landing_in_progress": False,
            "takeoff_in_progress": False,
        })

        self.assertTrue(released)
        self.assertEqual(manager._status, manager.MISSION_PARTIALLY_DONE)
        self.assertFalse(manager.is_busy)
        self.assertEqual(manager._wayline_mission_state, 9)
        self.assertEqual(manager._current_step, 33)
        final_events = [
            event for event in events
            if event.get("method") == "flighttask_progress"
            and event.get("data", {}).get("output", {}).get("status") == "partially_done"
        ]
        self.assertTrue(final_events)
        self.assertEqual(
            final_events[-1]["data"]["output"]["ext"]["break_point"]["break_reason"],
            773,
        )
        self.assertEqual(final_events[-1]["data"]["result"], 321773)
        reconnect_event = manager.build_current_wayline_progress_event()
        self.assertEqual(reconnect_event["data"]["output"]["status"], "partially_done")
        self.assertEqual(reconnect_event["data"]["result"], 321773)

    def test_low_battery_return_without_progress_releases_as_failed(self):
        events = []
        manager = MissionManager()
        manager.set_event_callback(events.append)
        manager._status = manager.MISSION_RUNNING
        manager._current_task_id = "flight-low-battery-no-progress"
        manager._current_wayline = [{"seq": 0}, {"seq": 1}, {"seq": 2}]
        manager._current_waypoint_idx = 0
        manager._get_telemetry_snapshot = lambda: {
            "batteryRemaining": 20,
            "battery_available": True,
        }
        manager._get_nest_snapshot = lambda: {}
        manager._flight_control = SimpleNamespace(
            one_click_return=lambda: {"success": True, "message": "ok"}
        )
        manager.stop_mission_monitor = lambda: True
        manager._save_breakpoint_file = lambda reason="": None
        manager._run_db_coroutine = lambda coro: coro.close() if hasattr(coro, "close") else None

        self.assertTrue(manager._handle_battery_safety())
        released = manager._maybe_finish_returned_task({
            "cloud_properties": {"drone_in_dock": 1},
            "landing_in_progress": False,
            "takeoff_in_progress": False,
        })

        self.assertTrue(released)
        self.assertEqual(manager._status, manager.MISSION_FAILED)
        self.assertFalse(manager.is_busy)
        final_events = [
            event for event in events
            if event.get("method") == "flighttask_progress"
            and event.get("data", {}).get("output", {}).get("status") == "failed"
        ]
        self.assertTrue(final_events)
        self.assertEqual(final_events[-1]["data"]["result"], 321773)

    def test_user_return_releases_with_user_return_result_code(self):
        events = []
        manager = MissionManager()
        manager.set_event_callback(events.append)
        manager._status = manager.MISSION_RUNNING
        manager._current_task_id = "flight-user-return"
        manager._current_wayline = [{"seq": 0}, {"seq": 1}, {"seq": 2}]
        manager._current_waypoint_idx = 1
        manager._get_telemetry_snapshot = lambda: {
            "armed": False,
            "altitudeRelative": 0.2,
        }
        manager._flight_control = SimpleNamespace(
            set_mode=lambda mode: {"success": True, "message": "ok"}
        )
        manager.stop_mission_monitor = lambda: True
        manager._save_breakpoint_file = lambda reason="": None
        manager._run_db_coroutine = lambda coro: coro.close() if hasattr(coro, "close") else None

        reply = manager.handle_return_home({"method": "return_home", "data": {}})

        self.assertEqual(reply["data"]["result"], 0)
        self.assertEqual(manager._status, manager.MISSION_PARTIALLY_DONE)
        self.assertFalse(manager.is_busy)
        final_events = [
            event for event in events
            if event.get("method") == "flighttask_progress"
            and event.get("data", {}).get("output", {}).get("status") == "partially_done"
        ]
        self.assertTrue(final_events)
        self.assertEqual(final_events[-1]["data"]["result"], 322283)
        self.assertTrue(any(event.get("method") == "return_home_info" for event in events))
        self.assertEqual(
            final_events[-1]["data"]["output"]["ext"]["break_point"]["break_reason"],
            1283,
        )

    def test_passive_rtl_mode_reports_return_events(self):
        events = []
        manager = MissionManager()
        manager.set_event_callback(events.append)
        manager._status = manager.MISSION_RUNNING
        manager._current_task_id = "flight-passive-rtl"
        manager._current_wayline = [{"seq": 0}, {"seq": 1}, {"seq": 2}]
        manager._current_waypoint_idx = 1
        manager._get_telemetry_snapshot = lambda: {"flight_mode": "RTL"}
        manager._save_breakpoint_file = lambda reason="": None
        manager._run_db_coroutine = lambda coro: coro.close() if hasattr(coro, "close") else None
        manager.stop_mission_monitor = lambda: True

        self.assertTrue(manager._handle_passive_return_mode())
        self.assertEqual(manager._status, manager.MISSION_PAUSED)
        self.assertEqual(manager._break_point["breakpoint_type"], "passive_auto_return")
        self.assertEqual(manager._break_point["break_reason"], 773)
        self.assertEqual(manager._break_point["flight_mode"], "RTL")

        methods = [event.get("method") for event in events]
        self.assertIn("flighttask_progress", methods)
        self.assertIn("return_home_info", methods)
        progress_events = [
            event for event in events
            if event.get("method") == "flighttask_progress"
        ]
        self.assertEqual(progress_events[-1]["data"]["result"], 321773)

    def test_passive_rtl_mode_does_not_duplicate_existing_return(self):
        events = []
        manager = MissionManager()
        manager.set_event_callback(events.append)
        manager._status = manager.MISSION_RUNNING
        manager._current_task_id = "flight-passive-rtl-duplicate"
        manager._current_wayline = [{"seq": 0}, {"seq": 1}]
        manager._current_waypoint_idx = 1
        manager._battery_safety_action = "rtl"
        manager._get_telemetry_snapshot = lambda: {"flight_mode": "RTL"}

        self.assertFalse(manager._handle_passive_return_mode())
        self.assertEqual(events, [])

    def test_return_release_can_use_disarmed_grounded_aircraft_when_dock_state_missing(self):
        events = []
        manager = MissionManager()
        manager.set_event_callback(events.append)
        manager._status = manager.MISSION_PAUSED
        manager._current_task_id = "flight-disarmed-return"
        manager._current_wayline = [{"seq": 0}, {"seq": 1}]
        manager._current_waypoint_idx = 1
        manager._break_point = {"index": 1, "break_reason": 773}
        manager._return_final_status = manager.MISSION_PARTIALLY_DONE
        manager._get_nest_snapshot = lambda: {}
        manager._get_telemetry_snapshot = lambda: {
            "armed": False,
            "altitudeRelative": 0.2,
        }
        manager.stop_mission_monitor = lambda: True
        manager._run_db_coroutine = lambda coro: coro.close() if hasattr(coro, "close") else None

        self.assertTrue(manager._maybe_finish_returned_task())
        self.assertEqual(manager._status, manager.MISSION_PARTIALLY_DONE)
        self.assertFalse(manager.is_busy)

    def test_return_release_does_not_use_dock_hint_while_aircraft_still_armed(self):
        manager = MissionManager()
        manager._status = manager.MISSION_PAUSED
        manager._current_task_id = "flight-still-flying"
        manager._current_wayline = [{"seq": 0}, {"seq": 1}]
        manager._current_waypoint_idx = 1
        manager._return_final_status = manager.MISSION_PARTIALLY_DONE
        manager._get_telemetry_snapshot = lambda: {
            "armed": True,
            "altitudeRelative": 12.0,
        }
        manager.stop_mission_monitor = lambda: True
        manager._run_db_coroutine = lambda coro: coro.close() if hasattr(coro, "close") else None

        released = manager._maybe_finish_returned_task({
            "cloud_properties": {"drone_in_dock": 1},
            "charging_state": True,
            "landing_in_progress": False,
            "takeoff_in_progress": False,
        })

        self.assertFalse(released)
        self.assertEqual(manager._status, manager.MISSION_PAUSED)
        self.assertTrue(manager.is_busy)

    def test_return_release_blocks_when_alternate_landing_is_active(self):
        manager = MissionManager()
        manager._status = manager.MISSION_PAUSED
        manager._current_task_id = "flight-alternate-landing"
        manager._current_wayline = [{"seq": 0}, {"seq": 1}]
        manager._current_waypoint_idx = 1
        manager._return_final_status = manager.MISSION_PARTIALLY_DONE
        manager._get_telemetry_snapshot = lambda: {
            "armed": False,
            "altitudeRelative": 0.0,
        }
        manager.stop_mission_monitor = lambda: True
        manager._run_db_coroutine = lambda coro: coro.close() if hasattr(coro, "close") else None

        released = manager._maybe_finish_returned_task({
            "alternate_landing_state": True,
            "emergency_landing": True,
            "cloud_properties": {"drone_in_dock": 1},
        })

        self.assertFalse(released)
        self.assertEqual(manager._status, manager.MISSION_PAUSED)
        self.assertTrue(manager.is_busy)

    def test_mission_current_revision_advances_for_repeated_sequence(self):
        waypoint_manager = WaypointManager()
        _, initial_revision = waypoint_manager.get_current_waypoint_snapshot()

        waypoint_manager._handle_mission_current(SimpleNamespace(seq=1))
        waypoint, first_revision = waypoint_manager.wait_for_current_waypoint(
            initial_revision,
            timeout=0,
        )
        self.assertEqual(waypoint, 1)
        self.assertGreater(first_revision, initial_revision)

        waypoint_manager._handle_mission_current(SimpleNamespace(seq=1))
        waypoint, second_revision = waypoint_manager.wait_for_current_waypoint(
            first_revision,
            timeout=0,
        )
        self.assertEqual(waypoint, 1)
        self.assertGreater(second_revision, first_revision)

    def test_mission_monitor_never_reads_mavlink_master(self):
        class CountingMaster:
            def __init__(self):
                self.recv_match_calls = 0

            def recv_match(self, *args, **kwargs):
                self.recv_match_calls += 1
                return SimpleNamespace(seq=1)

        master = CountingMaster()
        manager = MissionManager()
        manager.set_mavlink_connection(SimpleNamespace(master=master))
        waypoint_manager = WaypointManager()
        manager.set_waypoint_manager(waypoint_manager)
        manager._current_wayline = [{"seq": 0}, {"seq": 1}, {"seq": 2}]
        manager._status = manager.MISSION_RUNNING
        manager._handle_battery_safety = lambda: False
        manager._execute_waypoint_actions = lambda waypoint_index: None
        manager._save_breakpoint_file = lambda reason="": None

        progress_reported = threading.Event()
        manager._report_mission_progress = lambda status="executing": progress_reported.set()

        try:
            manager.start_mission_monitor()
            waypoint_manager._handle_mission_current(SimpleNamespace(seq=1))
            self.assertTrue(progress_reported.wait(2.0), "mission progress was not updated")
        finally:
            manager.stop_mission_monitor()

        self.assertEqual(master.recv_match_calls, 0)
        self.assertEqual(manager._current_waypoint_idx, 1)
        self.assertEqual(manager._breakpoint_waypoint_idx, 1)
        self.assertEqual(manager._break_point["index"], 1)
        self.assertAlmostEqual(manager._break_point["progress"], 1 / 3)

    def test_dispatcher_keeps_heartbeat_while_mission_monitor_runs(self):
        class RawMessage:
            def __init__(self, msg_type, seq=None):
                self._msg_type = msg_type
                self.seq = seq

            def get_type(self):
                return self._msg_type

            def get_msgId(self):
                return 0 if self._msg_type == "HEARTBEAT" else 42

            def get_srcSystem(self):
                return 1

            def get_srcComponent(self):
                return 1

        class QueueConnection:
            use_custom_dialect = False

            def __init__(self):
                self.messages = Queue()
                self.heartbeat_received = threading.Event()

            def is_connected(self):
                return True

            def recv_message(self, timeout=0.1):
                try:
                    return self.messages.get(timeout=timeout)
                except Empty:
                    return None

            def update_heartbeat(self):
                self.heartbeat_received.set()

            def check_heartbeat_timeout(self):
                return False

        connection = QueueConnection()
        dispatcher = MessageDispatcher(connection)
        waypoint_manager = WaypointManager()
        dispatcher.register_hook("*", waypoint_manager)

        manager = MissionManager()
        manager.set_waypoint_manager(waypoint_manager)
        manager._current_wayline = [{"seq": 0}, {"seq": 1}, {"seq": 2}]
        manager._status = manager.MISSION_RUNNING
        manager._handle_battery_safety = lambda: False
        manager._execute_waypoint_actions = lambda waypoint_index: None
        manager._save_breakpoint_file = lambda reason="": None
        progress_reported = threading.Event()
        manager._report_mission_progress = lambda status="executing": progress_reported.set()

        try:
            dispatcher.start()
            manager.start_mission_monitor()
            connection.messages.put(RawMessage("HEARTBEAT"))
            connection.messages.put(RawMessage("MISSION_CURRENT", seq=1))

            self.assertTrue(connection.heartbeat_received.wait(2.0))
            self.assertTrue(progress_reported.wait(2.0))
        finally:
            manager.stop_mission_monitor()
            dispatcher.stop()

        self.assertEqual(manager._current_waypoint_idx, 1)

    def test_mission_monitor_ignores_waypoint_snapshot_from_before_start(self):
        manager = MissionManager()
        waypoint_manager = WaypointManager()
        manager.set_waypoint_manager(waypoint_manager)
        manager._current_wayline = [{"seq": 0}, {"seq": 1}, {"seq": 2}]
        manager._status = manager.MISSION_RUNNING
        manager._handle_battery_safety = lambda: False

        waypoint_manager._handle_mission_current(SimpleNamespace(seq=2))
        progress_reported = threading.Event()
        manager._report_mission_progress = lambda status="executing": progress_reported.set()

        try:
            manager.start_mission_monitor()
            self.assertFalse(progress_reported.wait(0.2))
        finally:
            manager.stop_mission_monitor()

        self.assertEqual(manager._current_waypoint_idx, 0)
        self.assertEqual(manager._status, manager.MISSION_RUNNING)

    def test_prestarted_monitor_waits_until_mission_is_running(self):
        manager = MissionManager()
        waypoint_manager = WaypointManager()
        manager.set_waypoint_manager(waypoint_manager)
        manager._current_wayline = [{"seq": 0}, {"seq": 1}, {"seq": 2}]
        manager._status = manager.MISSION_PAUSED
        manager._handle_battery_safety = lambda: False
        manager._save_breakpoint_file = lambda reason="": None

        processed = threading.Event()
        manager._execute_waypoint_actions = lambda waypoint_index: processed.set()
        manager._report_mission_progress = lambda status="executing": None

        try:
            manager.start_mission_monitor()
            waypoint_manager._handle_mission_current(SimpleNamespace(seq=1))
            self.assertFalse(processed.wait(0.2))

            manager._status = manager.MISSION_RUNNING
            self.assertTrue(processed.wait(2.0))
        finally:
            manager.stop_mission_monitor()

        self.assertEqual(manager._current_waypoint_idx, 1)

    def test_mission_monitor_processes_burst_waypoints_in_order(self):
        manager = MissionManager()
        waypoint_manager = WaypointManager()
        manager.set_waypoint_manager(waypoint_manager)
        manager._current_wayline = [
            {"seq": 0},
            {"seq": 1},
            {"seq": 2},
            {"seq": 3},
        ]
        manager._status = manager.MISSION_RUNNING
        manager._handle_battery_safety = lambda: False
        manager._save_breakpoint_file = lambda reason="": None

        processed = []
        reached_second = threading.Event()
        manager._execute_waypoint_actions = lambda waypoint_index: processed.append(waypoint_index)

        def report_progress(status="executing"):
            if manager._current_waypoint_idx == 2:
                reached_second.set()

        manager._report_mission_progress = report_progress

        try:
            manager.start_mission_monitor()
            waypoint_manager._handle_mission_current(SimpleNamespace(seq=1))
            waypoint_manager._handle_mission_current(SimpleNamespace(seq=2))
            self.assertTrue(reached_second.wait(2.0))
        finally:
            manager.stop_mission_monitor()

        self.assertEqual(processed, [1, 2])
        self.assertEqual(manager._current_waypoint_idx, 2)

    def test_mission_monitor_does_not_start_duplicate_thread(self):
        manager = MissionManager()
        waypoint_manager = WaypointManager()
        manager.set_waypoint_manager(waypoint_manager)
        manager._current_wayline = [{"seq": 0}, {"seq": 1}]
        manager._handle_battery_safety = lambda: False

        try:
            self.assertTrue(manager.start_mission_monitor())
            monitor_thread = manager._mission_monitor_thread
            self.assertTrue(manager.start_mission_monitor())
            self.assertIs(manager._mission_monitor_thread, monitor_thread)
        finally:
            manager.stop_mission_monitor()

    def test_mission_monitor_restarts_after_stopping_owner_exits(self):
        manager = MissionManager()
        waypoint_manager = WaypointManager()
        manager.set_waypoint_manager(waypoint_manager)
        manager._current_wayline = [{"seq": 0}, {"seq": 1}, {"seq": 2}]
        manager._status = manager.MISSION_RUNNING
        manager._handle_battery_safety = lambda: False
        manager._save_breakpoint_file = lambda reason="": None
        manager._report_mission_progress = lambda status="executing": None

        action_started = threading.Event()
        release_action = threading.Event()

        def blocking_action(waypoint_index):
            action_started.set()
            release_action.wait(1.0)

        manager._execute_waypoint_actions = blocking_action

        try:
            self.assertTrue(manager.start_mission_monitor())
            old_thread = manager._mission_monitor_thread
            waypoint_manager._handle_mission_current(SimpleNamespace(seq=1))
            self.assertTrue(action_started.wait(1.0))

            manager._mission_monitor_stop_event.set()
            release_timer = threading.Timer(0.1, release_action.set)
            release_timer.start()
            try:
                self.assertTrue(manager.start_mission_monitor())
            finally:
                release_timer.cancel()

            self.assertIsNot(manager._mission_monitor_thread, old_thread)
            self.assertTrue(manager._mission_monitor_thread.is_alive())
        finally:
            release_action.set()
            manager.stop_mission_monitor()

    def test_mission_monitor_completion_cleans_up_its_own_thread(self):
        manager = MissionManager()
        waypoint_manager = WaypointManager()
        manager.set_waypoint_manager(waypoint_manager)
        manager._current_wayline = [{"seq": 0}, {"seq": 1}]
        manager._status = manager.MISSION_RUNNING
        manager._handle_battery_safety = lambda: False
        manager._execute_waypoint_actions = lambda waypoint_index: None
        manager._save_breakpoint_file = lambda reason="": None
        manager._clear_breakpoint_file = lambda: None
        manager._run_mission_completion_flow = lambda flight_id, source: True

        completed = threading.Event()

        def report_progress(status="executing"):
            if status == "completed":
                completed.set()

        manager._report_mission_progress = report_progress

        try:
            manager.start_mission_monitor()
            waypoint_manager._handle_mission_current(SimpleNamespace(seq=1))
            self.assertTrue(completed.wait(2.0), "mission did not complete")
            deadline = time.monotonic() + 1.0
            while manager._mission_monitor_thread is not None and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertIsNone(manager._mission_monitor_thread)
        finally:
            manager.stop_mission_monitor()

        self.assertEqual(manager._status, manager.MISSION_COMPLETED)

    def test_persisted_recovery_never_replaces_active_mqtt_task(self):
        manager = MissionManager()
        manager._current_task_id = "flight-live"
        manager._current_wayline = [{"seq": 0}, {"seq": 1}]
        manager._status = manager.MISSION_RUNNING

        installed = manager._install_recovered_mission_context(
            "flight-persisted",
            [{"seq": 0}],
        )

        self.assertFalse(installed)
        self.assertEqual(manager._current_task_id, "flight-live")
        self.assertEqual(len(manager._current_wayline), 2)
        self.assertEqual(manager._status, manager.MISSION_RUNNING)


if __name__ == "__main__":
    unittest.main()
