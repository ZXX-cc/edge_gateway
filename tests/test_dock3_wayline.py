import os
import sys
import threading
import time
import types
import unittest
import tempfile
import zipfile
from pathlib import Path


PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

# MissionManager imports pymavlink at module import time, but these tests only
# exercise MQTT payload/state logic and do not need a real MAVLink dependency.
if "pymavlink" not in sys.modules:
    pymavlink = types.ModuleType("pymavlink")
    pymavlink.mavutil = types.SimpleNamespace()
    pymavlink.mavutil.mavlink = types.SimpleNamespace(
        MAV_CMD_DO_CHANGE_SPEED=178,
        MAV_CMD_DO_PAUSE_CONTINUE=193,
    )
    sys.modules["pymavlink"] = pymavlink
    sys.modules["pymavlink.mavutil"] = pymavlink.mavutil

if "src.database.connection" not in sys.modules:
    connection = types.ModuleType("src.database.connection")

    async def init_database_async():
        return None

    def get_async_session_local():
        return None

    connection.init_database_async = init_database_async
    connection.get_async_session_local = get_async_session_local
    sys.modules["src.database.connection"] = connection

if "src.database.mission_db" not in sys.modules:
    mission_db = types.ModuleType("src.database.mission_db")
    mission_db.MissionDB = types.SimpleNamespace()
    sys.modules["src.database.mission_db"] = mission_db

if "src.database.waypoint_db" not in sys.modules:
    waypoint_db = types.ModuleType("src.database.waypoint_db")
    waypoint_db.WaypointDB = types.SimpleNamespace()
    sys.modules["src.database.waypoint_db"] = waypoint_db

if "src.database.schemas" not in sys.modules:
    schemas = types.ModuleType("src.database.schemas")

    class _Schema:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    schemas.MissionCreate = _Schema
    schemas.WaypointCreate = _Schema
    sys.modules["src.database.schemas"] = schemas

from src.cloud_api.dock3_wayline import normalize_prepare_payload, parse_wayline_file
from src.mission.manager import MissionManager
from src.utils.config import Config
from src.utils.dji_error_codes import (
    DEVICE_STATE_UNSUPPORTED,
    DOCK_BUSY,
    INVALID_COMMAND_FORMAT,
    WAYLINE_EXECUTION_FAILED,
    WAYLINE_PAUSE_STATE_INVALID,
    WAYLINE_RESUME_STATE_INVALID,
)


def base_msg(method, data):
    return {
        "tid": f"tid-{method}",
        "bid": f"bid-{method}",
        "gateway": "EDGE-GW-001",
        "method": method,
        "data": data,
    }


def write_mock_kmz(path: Path):
    kml = """<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2">
  <Document>
    <Placemark>
      <LineString>
        <coordinates>
          149.165237,-35.363262,50
          149.165537,-35.363562,60
        </coordinates>
      </LineString>
    </Placemark>
  </Document>
</kml>
"""
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("wpmz/template.kml", kml)


def write_mock_wpml_actions(path: Path):
    wpml = """<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2" xmlns:wpml="http://www.dji.com/wpmz/1.0.2">
  <Document>
    <Placemark>
      <Point><coordinates>149.165237,-35.363262,50</coordinates></Point>
      <wpml:waypointSpeed>8</wpml:waypointSpeed>
      <wpml:hoverTime>4</wpml:hoverTime>
      <wpml:actionGroup>
        <wpml:action>
          <wpml:actionActuatorFunc>takePhoto</wpml:actionActuatorFunc>
        </wpml:action>
        <wpml:action>
          <wpml:actionActuatorFunc>gimbalRotate</wpml:actionActuatorFunc>
          <wpml:actionActuatorFuncParam>
            <wpml:gimbalPitchRotateAngle>-35</wpml:gimbalPitchRotateAngle>
            <wpml:gimbalYawRotateAngle>90</wpml:gimbalYawRotateAngle>
          </wpml:actionActuatorFuncParam>
        </wpml:action>
        <wpml:action>
          <wpml:actionActuatorFunc>changeSpeed</wpml:actionActuatorFunc>
          <wpml:actionActuatorFuncParam>
            <wpml:speed>6</wpml:speed>
          </wpml:actionActuatorFuncParam>
        </wpml:action>
      </wpml:actionGroup>
    </Placemark>
  </Document>
</kml>
"""
    path.write_text(wpml, encoding="utf-8")


class FailingFlightControl:
    def __init__(self):
        self.calls = []

    def set_mode(self, mode):
        self.calls.append(("set_mode", mode))
        return {"success": False, "message": f"模式切换超时，当前模式: MANUAL, target={mode}"}

    def is_armed_status(self):
        return True

    def arm_and_wait(self, timeout=10.0, readiness_timeout=0.0):
        return {"success": True, "message": "already armed"}

    def pause_mission(self):
        self.calls.append(("pause_mission", None))
        return {"success": False, "message": "暂停命令未收到 ACK"}

    def resume_mission(self):
        self.calls.append(("resume_mission", None))
        return {"success": False, "message": "恢复命令未收到 ACK"}

    def one_click_return(self):
        self.calls.append(("one_click_return", None))
        return {"success": False, "message": "返航超时"}

    def land(self):
        self.calls.append(("land", None))
        return {"success": False, "message": "降落超时"}


class RecordingWaypointManager:
    def __init__(self):
        self.clear_calls = 0
        self.added = []
        self.upload_calls = []
        self.current_waypoints = []

    def clear_waypoints(self):
        self.clear_calls += 1

    def add_waypoint_from_dict(self, waypoint):
        self.added.append(waypoint)

    def upload_mission(self, timeout=15.0):
        self.upload_calls.append(timeout)
        return True

    def set_current_waypoint(self, wp_num: int, reset: bool = False):
        self.current_waypoints.append((wp_num, reset))
        return True


class RecordingCamera:
    def __init__(self):
        self.photos = 0

    def handle_camera_photo_take(self, msg):
        self.photos += 1
        return {"data": {"result": 0}}


class RecordingGimbal:
    def __init__(self):
        self.turns = []

    def turn_to(self, yaw, pitch):
        self.turns.append((yaw, pitch))
        return {"success": True}


class RecordingActionFlightControl:
    def __init__(self):
        self.commands = []

    def _send_command_long(self, command, **kwargs):
        self.commands.append((command, kwargs))
        return True


class RecordingRealWaylineFlightControl:
    def __init__(self):
        self.calls = []
        self.armed = False

    def get_available_modes(self):
        return ["QSTABILIZE", "QHOVER", "QLOITER", "QLAND", "AUTO", "GUIDED", "MANUAL"]

    def is_armed_status(self):
        return self.armed

    def set_mode(self, mode):
        self.calls.append(("set_mode", mode))
        return {"success": True, "message": "ok"}

    def one_click_return(self):
        self.calls.append(("one_click_return", None))
        return {"success": True, "message": "ok"}

    def land(self):
        self.calls.append(("land", None))
        return {"success": True, "message": "ok"}

    def arm(self):
        self.calls.append(("arm", None))
        self.armed = True
        return True

    def arm_and_wait(self, timeout=10.0, readiness_timeout=0.0):
        self.calls.append(("arm_and_wait", readiness_timeout))
        self.armed = True
        return {"success": True, "message": "ok"}

    def _wait_for_armed(self, target_state, timeout=10.0):
        self.calls.append(("wait_for_armed", target_state))
        return {"success": self.armed == target_state, "message": "ok"}


class UnreadyRealWaylineFlightControl(RecordingRealWaylineFlightControl):
    def arm_and_wait(self, timeout=10.0, readiness_timeout=30.0):
        self.calls.append(("arm_and_wait", readiness_timeout))
        return {
            "success": False,
            "message": "等待飞控预解锁就绪超时: 飞控预解锁检查尚未通过",
        }


class RecordingNest:
    def __init__(self):
        self.calls = []
        self.environment_data = {
            "nest_heartbeat": True,
            "plc_heartbeat": True,
            "drone_heartbeat": True,
            "estop_state": False,
            "fault_state": False,
            "system_busy": False,
            "emergency_landing": False,
            "takeoff_allowed": True,
            "gate_state": 1,
            "wind_speed": 1.0,
            "rainfall": 0.0,
            "error_code": 0,
            "last_update": time.time(),
        }

    def get_environment_data(self):
        return self.environment_data

    def send_gcs_command(self, command):
        self.calls.append(getattr(command, "name", str(command)))

    def request_takeoff(self):
        self.calls.append("request_takeoff")

    def abort_takeoff(self):
        self.calls.append("abort_takeoff")

    def abort_landing(self):
        self.calls.append("abort_landing")


class RecordingTelemetry:
    def __init__(self):
        self.data = {
            "connection_status": "connected",
            "batteryRemaining": 88,
            "satelliteNumber": 16,
            "gps_signal_level": 5,
            "gps_fixed": True,
            "armed": False,
            "flight_mode": "GUIDED",
        }

    def get_telemetry_data(self):
        return self.data


class Dock3WaylineTest(unittest.TestCase):
    def test_parse_local_kmz_extracts_waypoints(self):
        with tempfile.TemporaryDirectory() as tmp:
            kmz_path = Path(tmp) / "mission.kmz"
            write_mock_kmz(kmz_path)

            summary, waypoints = parse_wayline_file({"url": kmz_path.as_uri()})

        self.assertTrue(summary["available"])
        self.assertTrue(summary["parsed"])
        self.assertEqual(summary["format"], "kmz")
        self.assertEqual(summary["waypoint_count"], 2)
        self.assertEqual(len(waypoints), 2)
        self.assertAlmostEqual(waypoints[0]["latitude"], -35.363262)
        self.assertAlmostEqual(waypoints[0]["longitude"], 149.165237)

    def test_parse_wpml_extracts_waypoint_actions(self):
        with tempfile.TemporaryDirectory() as tmp:
            wpml_path = Path(tmp) / "template.wpml"
            write_mock_wpml_actions(wpml_path)

            summary, waypoints = parse_wayline_file({"path": str(wpml_path)})

        self.assertTrue(summary["parsed"])
        self.assertEqual(len(waypoints), 1)
        self.assertEqual(waypoints[0]["speed"], 8.0)
        self.assertEqual(waypoints[0]["hover_time"], 4.0)
        self.assertTrue(waypoints[0]["photo"])
        self.assertEqual(
            [action["action_type"] for action in waypoints[0]["actions"]],
            ["photo", "gimbal", "speed"],
        )

    def test_parse_qgc_wpl_waypoints_preserves_commands(self):
        with tempfile.TemporaryDirectory() as tmp:
            wpl_path = Path(tmp) / "HD.waypoints"
            wpl_path.write_text(
                "\n".join([
                    "QGC WPL 110",
                    "0\t1\t0\t16\t0\t0\t0\t0\t44.0474824\t125.3614503\t190.100000\t1",
                    "1\t0\t3\t84\t0\t0\t0\t0\t44.04750440\t125.36144360\t96.000000\t1",
                    "2\t0\t3\t18\t1\t1\t200\t1\t44.04087010\t125.33994320\t126.000000\t1",
                    "3\t0\t0\t177\t2\t5\t0\t0\t0\t0\t0\t1",
                ]),
                encoding="utf-8",
            )

            summary, waypoints = parse_wayline_file({"path": str(wpl_path)})

        self.assertTrue(summary["parsed"])
        self.assertEqual(summary["format"], "waypoints")
        self.assertEqual(len(waypoints), 4)
        self.assertEqual([wp["command"] for wp in waypoints], [16, 84, 18, 177])
        self.assertEqual(waypoints[1]["frame"], 3)
        self.assertAlmostEqual(waypoints[1]["latitude"], 44.04750440)

    def test_mission_manager_executes_waypoint_actions_once(self):
        manager = MissionManager()
        camera = RecordingCamera()
        gimbal = RecordingGimbal()
        flight_control = RecordingActionFlightControl()
        manager.set_camera_controller(camera)
        manager.set_gimbal_control(gimbal)
        manager.set_flight_control(flight_control)
        manager._current_wayline = [{
            "seq": 0,
            "actions": [
                {"action_type": "photo"},
                {"action_type": "gimbal", "pitch": "-35", "yaw": "90"},
                {"action_type": "speed", "param": "6"},
            ],
        }]

        manager._execute_waypoint_actions(0)
        manager._execute_waypoint_actions(0)

        self.assertEqual(camera.photos, 1)
        self.assertEqual(gimbal.turns, [(90.0, -35.0)])
        self.assertEqual(len(flight_control.commands), 1)
        self.assertEqual(flight_control.commands[0][1]["param2"], 6.0)

    def test_breakpoint_file_saved_loaded_and_cleared(self):
        with tempfile.TemporaryDirectory() as tmp:
            breakpoint_path = Path(tmp) / "breakpoint.json"
            manager = MissionManager()
            manager._breakpoint_file_path = lambda: str(breakpoint_path)
            manager._current_task_id = "flight-001"
            manager._current_wayline = [{"seq": 0}, {"seq": 1}, {"seq": 2}]
            manager._current_waypoint_idx = 1
            manager._status = manager.MISSION_RUNNING
            manager.set_flight_control(RecordingRealWaylineFlightControl())
            manager.stop_mission_monitor = lambda: None
            manager._run_db_coroutine = lambda coro: coro.close() if hasattr(coro, "close") else None

            pause = manager.handle_pause_wayline(base_msg("flighttask_pause", {}))
            self.assertEqual(pause["data"]["result"], 0)
            self.assertTrue(breakpoint_path.exists())

            manager._breakpoint_waypoint_idx = 0
            manager.start_mission_monitor = lambda: None
            resume = manager.handle_resume_wayline(base_msg("flighttask_resume", {}))
            self.assertEqual(resume["data"]["result"], 0)
            self.assertEqual(manager._breakpoint_waypoint_idx, 1)

            undo = manager.handle_flighttask_undo(base_msg("flighttask_undo", {
                "flight_ids": ["flight-001"],
            }))
            self.assertEqual(undo["data"]["result"], 0)
            self.assertFalse(breakpoint_path.exists())

    def test_flighttask_undo_current_alias_releases_recovered_task(self):
        with tempfile.TemporaryDirectory() as tmp:
            breakpoint_path = Path(tmp) / "breakpoint.json"
            manager = MissionManager()
            events = []
            manager.set_event_callback(events.append)
            manager._breakpoint_file_path = lambda: str(breakpoint_path)
            manager._current_task_id = "recovered-flight-001"
            manager._current_wayline = [{"seq": 0}, {"seq": 1}, {"seq": 2}]
            manager._current_waypoint_idx = 1
            manager._breakpoint_waypoint_idx = 1
            manager._status = manager.MISSION_PAUSED
            manager.set_flight_control(RecordingRealWaylineFlightControl())
            manager.stop_mission_monitor = lambda: True
            manager._run_db_coroutine = lambda coro: coro.close() if hasattr(coro, "close") else None
            manager._save_breakpoint_file("test persisted mission")

            undo = manager.handle_flighttask_undo(base_msg("flighttask_undo", {
                "flight_ids": ["current"],
            }))

            self.assertEqual(undo["data"]["result"], 0)
            self.assertEqual(undo["data"]["canceled_flight_id"], "recovered-flight-001")
            self.assertEqual(undo["data"]["output"]["ext"]["flight_id"], "recovered-flight-001")
            self.assertEqual(undo["data"]["output"]["status"], "canceled")
            self.assertFalse(breakpoint_path.exists())
            self.assertIsNone(manager._current_task_id)
            self.assertEqual(manager._current_wayline, [])
            self.assertEqual(manager._status, manager.MISSION_CANCELED)
            self.assertTrue(any(event.get("method") == "flighttask_progress" for event in events))

    def test_flighttask_undo_rejects_unknown_task_without_clearing_active_task(self):
        manager = MissionManager()
        manager._current_task_id = "flight-active"
        manager._current_wayline = [{"seq": 0}, {"seq": 1}]
        manager._status = manager.MISSION_RUNNING

        undo = manager.handle_flighttask_undo(base_msg("flighttask_undo", {
            "flight_ids": ["flight-other"],
        }))

        self.assertEqual(undo["data"]["result"], DEVICE_STATE_UNSUPPORTED)
        self.assertEqual(undo["data"]["output"]["status"], "rejected")
        self.assertEqual(manager._current_task_id, "flight-active")

    def test_flighttask_return_saves_breakpoint_for_later_resume(self):
        with tempfile.TemporaryDirectory() as tmp:
            breakpoint_path = Path(tmp) / "breakpoint.json"
            manager = MissionManager()
            waypoint_manager = RecordingWaypointManager()
            manager.set_waypoint_manager(waypoint_manager)
            manager._breakpoint_file_path = lambda: str(breakpoint_path)
            manager._run_db_coroutine = lambda coro: coro.close() if hasattr(coro, "close") else None
            manager.stop_mission_monitor = lambda: None
            manager.start_mission_monitor = lambda: None
            manager.set_flight_control(RecordingRealWaylineFlightControl())

            manager._current_task_id = "flight-001"
            manager._current_wayline = [{"seq": 0}, {"seq": 1}, {"seq": 2}, {"seq": 3}]
            manager._current_waypoint_idx = 2
            manager._status = manager.MISSION_RUNNING

            ret = manager.handle_flighttask_return(base_msg("flighttask_return", {
                "flight_id": "flight-001",
                "return_type": 0,
            }))

            self.assertEqual(ret["data"]["result"], 0)
            self.assertEqual(manager._status, manager.MISSION_PAUSED)
            self.assertEqual(manager._breakpoint_waypoint_idx, 2)
            self.assertTrue(breakpoint_path.exists())

            manager._breakpoint_waypoint_idx = 0
            resume = manager.handle_flighttask_resume(base_msg("flighttask_resume", {}))

            self.assertEqual(resume["data"]["result"], 0)
            self.assertEqual(manager._breakpoint_waypoint_idx, 2)
            self.assertEqual(waypoint_manager.current_waypoints, [(2, False)])

    def test_real_flighttask_pause_does_not_report_success_when_fc_fails(self):
        manager = MissionManager()
        flight_control = FailingFlightControl()
        manager.set_flight_control(flight_control)
        manager._current_task_id = "flight-001"
        manager._current_wayline = [{"seq": 0}, {"seq": 1}]
        manager._status = manager.MISSION_RUNNING
        manager._simulate_mission = {"is_enable": 0}

        pause = manager.handle_flighttask_pause(base_msg("flighttask_pause", {}))

        self.assertEqual(pause["data"]["result"], WAYLINE_EXECUTION_FAILED)
        self.assertEqual(pause["data"]["output"]["status"], "rejected")
        self.assertEqual(manager._status, manager.MISSION_RUNNING)
        self.assertEqual(flight_control.calls, [("pause_mission", None)])

    def test_pause_does_not_overwrite_completion_already_being_processed(self):
        class SuccessfulPauseFlightControl:
            def __init__(self):
                self.calls = []

            def pause_mission(self):
                self.calls.append("pause")
                return {"success": True, "message": "paused"}

        manager = MissionManager()
        flight_control = SuccessfulPauseFlightControl()
        manager.set_flight_control(flight_control)
        manager._current_wayline = [{"seq": 0}, {"seq": 1}]
        manager._status = manager.MISSION_RUNNING
        manager._simulate_mission = {"is_enable": 0}
        manager._save_breakpoint_file = lambda reason="": None
        manager._clear_breakpoint_file = lambda: None
        manager._report_mission_progress = lambda status="executing": None
        manager._run_mission_completion_flow = lambda flight_id, source: True
        manager.stop_mission_monitor = lambda: True

        action_started = threading.Event()
        release_action = threading.Event()

        def blocking_action(_waypoint_index):
            action_started.set()
            release_action.wait(1.0)

        manager._execute_waypoint_actions = blocking_action
        completion_thread = threading.Thread(
            target=manager._process_mission_current,
            args=(1,),
        )
        completion_thread.start()
        self.assertTrue(action_started.wait(1.0))

        pause_reply = {}

        def pause_task():
            pause_reply.update(manager.handle_flighttask_pause(
                base_msg("flighttask_pause", {}),
            ))

        pause_thread = threading.Thread(target=pause_task)
        pause_thread.start()
        time.sleep(0.05)
        self.assertEqual(flight_control.calls, [])

        release_action.set()
        completion_thread.join(1.0)
        pause_thread.join(1.0)

        self.assertFalse(completion_thread.is_alive())
        self.assertFalse(pause_thread.is_alive())
        self.assertEqual(manager._status, manager.MISSION_COMPLETED)
        self.assertEqual(pause_reply["data"]["result"], DEVICE_STATE_UNSUPPORTED)
        self.assertEqual(flight_control.calls, [])

    def test_mission_completion_runs_real_return_land_and_dock_recovery(self):
        manager = MissionManager()
        flight_control = RecordingRealWaylineFlightControl()
        nest = RecordingNest()
        events = []
        manager.set_flight_control(flight_control)
        manager.set_nest_module(nest)
        manager.set_event_callback(events.append)
        manager._current_task_id = "flight-complete"
        manager._current_wayline = [{"seq": 0}, {"seq": 1}]
        manager._status = manager.MISSION_RUNNING
        manager._simulate_mission = {"is_enable": 0}
        manager._save_breakpoint_file = lambda reason="": None
        manager._clear_breakpoint_file = lambda: None
        manager._run_db_coroutine = lambda coro: coro.close() if hasattr(coro, "close") else None
        manager._flow_retry_settings = lambda: (2, 0.0)

        completed = manager._process_mission_current(1)

        self.assertTrue(completed)
        self.assertEqual(manager._status, manager.MISSION_COMPLETED)
        self.assertIn(("one_click_return", None), flight_control.calls)
        self.assertIn(("land", None), flight_control.calls)
        self.assertEqual(nest.calls[-3:], ["CLAMP_TIGHTEN", "DOOR_CLOSE", "CHARGE_START"])
        self.assertTrue(any(event.get("method") == "flighttask_progress" for event in events))

    def test_mission_completion_retries_and_reports_failure_without_fake_success(self):
        manager = MissionManager()
        flight_control = FailingFlightControl()
        events = []
        manager.set_flight_control(flight_control)
        manager.set_event_callback(events.append)
        manager._current_task_id = "flight-fail-return"
        manager._current_wayline = [{"seq": 0}, {"seq": 1}]
        manager._status = manager.MISSION_RUNNING
        manager._simulate_mission = {"is_enable": 0}
        manager._save_breakpoint_file = lambda reason="": None
        manager._clear_breakpoint_file = lambda: None
        manager._run_db_coroutine = lambda coro: coro.close() if hasattr(coro, "close") else None
        manager._flow_retry_settings = lambda: (2, 0.0)

        processed = manager._process_mission_current(1)

        self.assertTrue(processed)
        self.assertEqual(manager._status, manager.MISSION_FAILED)
        self.assertEqual(
            flight_control.calls,
            [("one_click_return", None), ("one_click_return", None)],
        )
        failed_events = [
            event for event in events
            if event.get("method") == "flighttask_progress"
            and event.get("data", {}).get("output", {}).get("status") == "failed"
        ]
        self.assertTrue(failed_events)

    def test_waypoint_update_waiting_on_pause_cannot_run_actions_after_hold(self):
        pause_started = threading.Event()
        release_pause = threading.Event()

        class BlockingPauseFlightControl:
            def pause_mission(self):
                pause_started.set()
                release_pause.wait(1.0)
                return {
                    "success": True,
                    "message": "paused",
                    "pause_strategy": "hold_mode",
                    "target_mode": "QLOITER",
                    "native_pause": False,
                }

        manager = MissionManager()
        manager.set_flight_control(BlockingPauseFlightControl())
        manager._current_wayline = [{"seq": 0}, {"seq": 1}, {"seq": 2}]
        manager._status = manager.MISSION_RUNNING
        manager._simulate_mission = {"is_enable": 0}
        manager.stop_mission_monitor = lambda: True
        manager._record_breakpoint = lambda reason: None
        manager._emit_wayline_progress = lambda *args, **kwargs: None

        pause_reply = {}
        pause_thread = threading.Thread(
            target=lambda: pause_reply.update(
                manager.handle_pause_wayline(base_msg("flighttask_pause", {}))
            )
        )
        pause_thread.start()
        self.assertTrue(pause_started.wait(1.0))

        actions = []
        manager._execute_waypoint_actions = actions.append
        waypoint_thread = threading.Thread(
            target=manager._process_mission_current,
            args=(1,),
        )
        waypoint_thread.start()
        time.sleep(0.05)
        self.assertTrue(waypoint_thread.is_alive())

        release_pause.set()
        pause_thread.join(1.0)
        waypoint_thread.join(1.0)

        self.assertEqual(pause_reply["data"]["result"], 0)
        self.assertEqual(manager._status, manager.MISSION_PAUSED)
        self.assertEqual(actions, [])
        self.assertEqual(manager._current_waypoint_idx, 0)

    def test_real_flighttask_execute_reports_auto_stage_and_skips_nest_when_fc_fails(self):
        manager = MissionManager()
        flight_control = FailingFlightControl()
        manager.set_flight_control(flight_control)
        manager._current_task_id = "flight-001"
        manager._current_wayline = [{"seq": 0}, {"seq": 1}]
        manager._status = manager.MISSION_IDLE
        manager._simulate_mission = {"is_enable": 0}
        monitor_calls = []
        manager.start_mission_monitor = lambda: monitor_calls.append("start")
        manager.stop_mission_monitor = lambda: monitor_calls.append("stop")

        execute = manager.handle_flighttask_execute(base_msg("flighttask_execute", {"flight_id": "flight-001"}))

        self.assertEqual(execute["data"]["result"], 319999)
        self.assertEqual(execute["data"]["output"]["status"], "failed")
        self.assertEqual(execute["data"]["flight_control"]["stage"], "auto_mode")
        self.assertEqual(manager._status, manager.MISSION_FAILED)
        self.assertEqual(monitor_calls, ["start", "stop"])
        self.assertEqual(flight_control.calls, [("set_mode", "AUTO")])

    def test_real_flighttask_execute_reserves_monitor_before_flight_control(self):
        manager = MissionManager()
        flight_control = RecordingRealWaylineFlightControl()
        manager.set_flight_control(flight_control)
        manager._current_task_id = "flight-001"
        manager._current_wayline = [{"seq": 0}, {"seq": 1}]
        manager._status = manager.MISSION_IDLE
        manager._simulate_mission = {"is_enable": 0}
        monitor_calls = []
        manager.start_mission_monitor = lambda: monitor_calls.append("start") or False
        manager.stop_mission_monitor = lambda: monitor_calls.append("stop")

        execute = manager.handle_flighttask_execute(base_msg(
            "flighttask_execute",
            {"flight_id": "flight-001"},
        ))

        self.assertEqual(execute["data"]["result"], 319999)
        self.assertEqual(execute["data"]["flight_control"]["stage"], "mission_monitor")
        self.assertEqual(monitor_calls, ["start", "stop"])
        self.assertEqual(flight_control.calls, [])
        self.assertFalse(flight_control.armed)

    def test_real_flighttask_execute_fails_closed_without_flight_control(self):
        manager = MissionManager()
        manager._current_task_id = "flight-001"
        manager._current_wayline = [{"seq": 0}, {"seq": 1}]
        manager._status = manager.MISSION_IDLE
        manager._simulate_mission = {"is_enable": 0}
        monitor_calls = []
        nest_calls = []
        manager.start_mission_monitor = lambda: monitor_calls.append("start") or True
        manager.stop_mission_monitor = lambda: monitor_calls.append("stop") or True
        manager._call_nest_action = lambda action, context: nest_calls.append((action, context))

        execute = manager.handle_flighttask_execute(base_msg(
            "flighttask_execute",
            {"flight_id": "flight-001"},
        ))

        self.assertEqual(execute["data"]["result"], 319999)
        self.assertEqual(execute["data"]["flight_control"]["stage"], "flight_control")
        self.assertEqual(manager._status, manager.MISSION_FAILED)
        self.assertEqual(monitor_calls, ["start", "stop"])
        self.assertEqual(nest_calls, [])

    def test_real_resume_rejects_monitor_failure_before_fc_and_breakpoint_commands(self):
        manager = MissionManager()
        flight_control = RecordingRealWaylineFlightControl()
        waypoint_manager = RecordingWaypointManager()
        manager.set_flight_control(flight_control)
        manager.set_waypoint_manager(waypoint_manager)
        manager._current_task_id = "flight-001"
        manager._current_wayline = [{"seq": 0}, {"seq": 1}]
        manager._breakpoint_waypoint_idx = 1
        manager._status = manager.MISSION_PAUSED
        manager._simulate_mission = {"is_enable": 0}
        manager._load_breakpoint_file = lambda: None
        manager.start_mission_monitor = lambda: False
        db_updates = []
        manager._update_mission_status = lambda *args: db_updates.append(args)

        resume = manager.handle_resume_wayline(base_msg("resume_wayline", {}))

        self.assertEqual(resume["data"]["result"], WAYLINE_EXECUTION_FAILED)
        self.assertEqual(resume["data"]["output"]["status"], "rejected")
        self.assertEqual(manager._status, manager.MISSION_PAUSED)
        self.assertEqual(flight_control.calls, [])
        self.assertEqual(waypoint_manager.current_waypoints, [])
        self.assertEqual(db_updates, [])

    def test_resume_requires_mission_monitor_when_simulation_disabled(self):
        manager = MissionManager()
        manager._current_task_id = "flight-001"
        manager._current_wayline = [{"seq": 0}, {"seq": 1}]
        manager._status = manager.MISSION_PAUSED
        manager.set_flight_control(RecordingRealWaylineFlightControl())
        manager._load_breakpoint_file = lambda: None
        manager.start_mission_monitor = lambda: False
        manager._run_db_coroutine = lambda coro: coro.close() if hasattr(coro, "close") else None

        resume = manager.handle_resume_wayline(base_msg("resume_wayline", {}))

        self.assertEqual(resume["data"]["result"], WAYLINE_EXECUTION_FAILED)
        self.assertEqual(resume["data"]["output"]["status"], "rejected")
        self.assertEqual(manager._status, manager.MISSION_PAUSED)

    def test_prepare_payload_accepts_official_shape(self):
        ok, normalized, errors = normalize_prepare_payload({
            "flight_id": "flight-001",
            "task_type": 0,
            "execute_time": int(time.time() * 1000),
            "file": {
                "url": "https://example.invalid/flight.kmz",
                "fingerprint": "fingerprint",
            },
            "rth_altitude": 80,
            "out_of_control_action": 0,
            "exit_wayline_when_rc_lost": 0,
            "simulate_mission": {
                "is_enable": 1,
                "latitude": -35.36,
                "longitude": 149.16,
            },
        })

        self.assertTrue(ok, errors)
        self.assertEqual(normalized["flight_id"], "flight-001")

    def test_prepare_payload_rejects_bad_timestamp(self):
        ok, _, errors = normalize_prepare_payload({
            "flight_id": "flight-001",
            "task_type": 0,
            "execute_time": 123,
        })

        self.assertFalse(ok)
        self.assertTrue(any("execute_time" in error for error in errors))

    def test_mission_manager_wayline_lifecycle_outputs_dock3_status(self):
        events = []
        flight_control = RecordingRealWaylineFlightControl()
        manager = MissionManager()
        manager.set_event_callback(events.append)
        manager.set_flight_control(flight_control)
        waypoint_manager = RecordingWaypointManager()
        manager.set_waypoint_manager(waypoint_manager)
        manager.start_mission_monitor = lambda: None
        manager.stop_mission_monitor = lambda: None
        manager._run_db_coroutine = lambda coro: coro.close() if hasattr(coro, "close") else None

        with tempfile.TemporaryDirectory() as tmp:
            kmz_path = Path(tmp) / "mission.kmz"
            write_mock_kmz(kmz_path)
            prepare = manager.handle_flighttask_prepare(base_msg("flighttask_prepare", {
                "flight_id": "flight-001",
                "task_type": 0,
                "execute_time": int(time.time() * 1000),
                "file": {"url": kmz_path.as_uri(), "fingerprint": "fp"},
            }))
        self.assertEqual(prepare["data"]["result"], 0)
        self.assertEqual(prepare["data"]["output"]["status"], "sent")
        file_summary = prepare["data"]["output"]["ext"]["file"]
        self.assertTrue(file_summary["parsed"])
        self.assertEqual(file_summary["waypoint_count"], 2)
        self.assertTrue(any(event.get("method") == "flighttask_ready" for event in events))
        self.assertEqual(waypoint_manager.clear_calls, 1)
        self.assertEqual(len(waypoint_manager.added), 2)
        self.assertEqual(waypoint_manager.upload_calls, [15.0])

        execute = manager.handle_flighttask_execute(base_msg("flighttask_execute", {
            "flight_id": "flight-001",
        }))
        self.assertEqual(execute["data"]["result"], 0)
        self.assertEqual(execute["data"]["output"]["status"], "in_progress")

        pause = manager.handle_flighttask_pause(base_msg("flighttask_pause", {}))
        self.assertEqual(pause["data"]["result"], 0)
        self.assertEqual(pause["data"]["output"]["status"], "paused")

        progress = manager.handle_flighttask_progress(base_msg("flighttask_progress", {
            "flight_id": "flight-001",
        }))
        self.assertEqual(progress["data"]["result"], 0)
        self.assertEqual(progress["data"]["output"]["status"], "paused")

        resume = manager.handle_flighttask_resume(base_msg("flighttask_resume", {}))
        self.assertEqual(resume["data"]["result"], 0)
        self.assertEqual(resume["data"]["output"]["status"], "in_progress")

        ret = manager.handle_flighttask_return(base_msg("flighttask_return", {
            "flight_id": "flight-001",
            "return_type": 0,
        }))
        self.assertEqual(ret["data"]["result"], 0)
        self.assertEqual(ret["data"]["output"]["status"], "in_progress")

        cancel_return = manager.handle_return_home_cancel(base_msg("return_home_cancel", {}))
        self.assertEqual(cancel_return["data"]["result"], 0)
        self.assertEqual(cancel_return["data"]["output"]["status"], "paused")

        undo = manager.handle_flighttask_undo(base_msg("flighttask_undo", {
            "flight_ids": ["flight-001"],
        }))
        self.assertEqual(undo["data"]["result"], 0)
        self.assertEqual(undo["data"]["output"]["status"], "canceled")
        self.assertIn(("set_mode", "AUTO"), flight_control.calls)
        self.assertIn(("set_mode", "LOITER"), flight_control.calls)
        self.assertIn(("one_click_return", None), flight_control.calls)
        self.assertTrue(any(event.get("method") == "flighttask_progress" for event in events))

    def test_real_flighttask_execute_prearms_before_auto(self):
        manager = MissionManager()
        flight_control = RecordingRealWaylineFlightControl()
        manager.set_flight_control(flight_control)
        manager.start_mission_monitor = lambda: None
        manager._simulate_mission = {"is_enable": 0}
        manager._current_task_id = "flight-001"
        manager._current_wayline = [{"seq": 0}, {"seq": 1}]

        execute = manager.handle_flighttask_execute(base_msg("flighttask_execute", {
            "flight_id": "flight-001",
        }))

        self.assertEqual(execute["data"]["result"], 0)
        self.assertEqual(execute["data"]["output"]["status"], "in_progress")
        self.assertEqual(flight_control.calls, [
            ("set_mode", "QSTABILIZE"),
            ("arm_and_wait", 0.0),
            ("set_mode", "AUTO"),
        ])

    def test_flighttask_prepare_delegates_wayline_to_nest_when_main_mavlink_disabled(self):
        original_config = Config._data
        Config._data = {"mavlink": {"enabled": False}, "nest_mavlink": {"enabled": True}}
        try:
            manager = MissionManager()
            nest = RecordingNest()
            waypoint_manager = RecordingWaypointManager()
            events = []
            manager.set_nest_module(nest)
            manager.set_waypoint_manager(waypoint_manager)
            manager.set_event_callback(events.append)

            with tempfile.TemporaryDirectory() as tmp:
                kmz_path = Path(tmp) / "mission.kmz"
                write_mock_kmz(kmz_path)
                prepare = manager.handle_flighttask_prepare(base_msg("flighttask_prepare", {
                    "flight_id": "delegated-flight-001",
                    "task_type": 0,
                    "execute_time": int(time.time() * 1000),
                    "file": {"url": kmz_path.as_uri(), "fingerprint": "fp"},
                    "simulate_mission": {"is_enable": 0},
                }))

            self.assertEqual(prepare["data"]["result"], 0)
            self.assertEqual(prepare["data"]["output"]["status"], "sent")
            self.assertEqual(prepare["data"]["output"]["ext"]["file"]["delegated_to"], "nest")
            self.assertFalse(prepare["data"]["output"]["ext"]["file"]["parsed"])
            self.assertEqual(waypoint_manager.clear_calls, 0)
            self.assertEqual(waypoint_manager.added, [])
            self.assertEqual(waypoint_manager.upload_calls, [])

            execute = manager.handle_flighttask_execute(base_msg("flighttask_execute", {
                "flight_id": "delegated-flight-001",
            }))
            self.assertEqual(execute["data"]["result"], 0)
            self.assertEqual(execute["data"]["output"]["status"], "in_progress")
            self.assertEqual(nest.calls, ["request_takeoff"])
            self.assertTrue(any(event.get("method") == "flighttask_ready" for event in events))
        finally:
            Config._data = original_config

    def test_flighttask_prepare_does_not_upload_when_nest_link_configured_before_module_injection(self):
        original_config = Config._data
        Config._data = {"mavlink": {"enabled": False}, "nest_mavlink": {"enabled": True}}
        try:
            manager = MissionManager()
            waypoint_manager = RecordingWaypointManager()
            manager.set_waypoint_manager(waypoint_manager)

            with tempfile.TemporaryDirectory() as tmp:
                kmz_path = Path(tmp) / "mission.kmz"
                write_mock_kmz(kmz_path)
                prepare = manager.handle_flighttask_prepare(base_msg("flighttask_prepare", {
                    "flight_id": "delegated-flight-no-nest-module",
                    "task_type": 0,
                    "execute_time": int(time.time() * 1000),
                    "file": {"url": kmz_path.as_uri(), "fingerprint": "fp"},
                    "simulate_mission": {"is_enable": 0},
                }))

            self.assertEqual(prepare["data"]["result"], 0)
            self.assertEqual(prepare["data"]["output"]["ext"]["file"]["delegated_to"], "nest")
            self.assertFalse(prepare["data"]["output"]["ext"]["file"]["parsed"])
            self.assertEqual(waypoint_manager.upload_calls, [])
        finally:
            Config._data = original_config

    def test_real_flighttask_execute_stops_before_auto_when_prearm_is_unhealthy(self):
        manager = MissionManager()
        flight_control = UnreadyRealWaylineFlightControl()
        manager.set_flight_control(flight_control)
        manager.start_mission_monitor = lambda: True
        manager.stop_mission_monitor = lambda: True
        manager._simulate_mission = {"is_enable": 0}
        manager._current_task_id = "flight-001"
        manager._current_wayline = [{"seq": 0}, {"seq": 1}]

        execute = manager.handle_flighttask_execute(base_msg("flighttask_execute", {
            "flight_id": "flight-001",
        }))

        self.assertEqual(execute["data"]["result"], 319999)
        self.assertEqual(execute["data"]["flight_control"]["stage"], "pre_arm")
        self.assertEqual(flight_control.calls, [
            ("set_mode", "QSTABILIZE"),
            ("arm_and_wait", 0.0),
        ])
        self.assertFalse(flight_control.armed)

    def test_running_task_cannot_be_replaced_by_another_prepare_request(self):
        manager = MissionManager()
        manager._current_task_id = "flight-active"
        manager._current_wayline = [{"seq": 0}, {"seq": 1}, {"seq": 2}]
        manager._task_file_summary = {"source": "active.kmz", "waypoint_count": 3}
        manager._status = manager.MISSION_RUNNING

        prepare = manager.handle_flighttask_prepare(base_msg("flighttask_prepare", {
            "flight_id": "flight-demo",
            "task_type": 0,
            "execute_time": int(time.time() * 1000),
            "file": {"url": "file:///tmp/demo.waypoints", "fingerprint": "demo"},
            "simulate_mission": {"is_enable": 0, "latitude": -35.36, "longitude": 149.16},
        }))

        self.assertEqual(prepare["data"]["result"], DOCK_BUSY)
        self.assertEqual(prepare["data"]["output"]["status"], "rejected")
        self.assertEqual(prepare["data"]["active_flight_id"], "flight-active")
        self.assertEqual(manager._current_task_id, "flight-active")
        self.assertEqual(len(manager._current_wayline), 3)
        self.assertEqual(manager._task_file_summary["source"], "active.kmz")

    def test_execute_and_progress_reject_flight_id_not_matching_prepared_task(self):
        manager = MissionManager()
        manager._current_task_id = "flight-active"
        manager._current_wayline = [{"seq": 0}, {"seq": 1}, {"seq": 2}]
        manager._task_file_summary = {"source": "active.kmz", "waypoint_count": 3}
        manager._status = manager.MISSION_IDLE
        manager._simulate_mission = {"is_enable": 1}

        execute = manager.handle_flighttask_execute(base_msg("flighttask_execute", {
            "flight_id": "flight-demo",
        }))
        self.assertEqual(execute["data"]["result"], DEVICE_STATE_UNSUPPORTED)
        self.assertEqual(execute["data"]["output"]["status"], "rejected")
        self.assertEqual(manager._status, manager.MISSION_IDLE)

        manager._status = manager.MISSION_RUNNING
        progress = manager.handle_flighttask_progress(base_msg("flighttask_progress", {
            "flight_id": "flight-demo",
        }))
        self.assertEqual(progress["data"]["result"], DEVICE_STATE_UNSUPPORTED)
        self.assertEqual(progress["data"]["output"]["status"], "rejected")
        self.assertEqual(progress["data"]["active_flight_id"], "flight-active")
        self.assertEqual(progress["data"]["output"]["ext"]["flight_id"], "flight-active")

    def test_simulated_wayline_prepare_and_completion_are_disabled(self):
        events = []
        manager = MissionManager()
        nest = RecordingNest()
        manager.set_event_callback(events.append)
        manager.set_nest_module(nest)
        manager.set_telemetry_module(RecordingTelemetry())
        manager._run_db_coroutine = lambda coro: coro.close() if hasattr(coro, "close") else None

        with tempfile.TemporaryDirectory() as tmp:
            manager._breakpoint_file_path = lambda: str(Path(tmp) / "breakpoint.json")
            kmz_path = Path(tmp) / "mission.kmz"
            write_mock_kmz(kmz_path)
            prepare = manager.handle_flighttask_prepare(base_msg("flighttask_prepare", {
                "flight_id": "takeoff-flow-001",
                "task_type": 0,
                "execute_time": int(time.time() * 1000),
                "file": {"url": kmz_path.as_uri(), "fingerprint": "fp"},
                "simulate_mission": {
                    "is_enable": 1,
                    "latitude": -35.36,
                    "longitude": 149.16,
                },
            }))
            self.assertNotEqual(prepare["data"]["result"], 0)

            completed = manager.complete_simulated_mission()
            self.assertFalse(completed)
            self.assertEqual(nest.calls, [])

        self.assertFalse(any(event.get("method") == "flighttask_progress" for event in events))

    def test_invalid_wayline_requests_return_dji_error_codes(self):
        manager = MissionManager()
        bad_prepare = manager.handle_flighttask_prepare(base_msg("flighttask_prepare", {
            "flight_id": "",
            "task_type": 9,
            "execute_time": 1,
        }))
        self.assertEqual(bad_prepare["data"]["result"], INVALID_COMMAND_FORMAT)

        bad_pause = manager.handle_flighttask_pause(base_msg("flighttask_pause", {}))
        self.assertEqual(bad_pause["data"]["result"], DEVICE_STATE_UNSUPPORTED)

        bad_resume = manager.handle_flighttask_resume(base_msg("flighttask_resume", {}))
        self.assertEqual(bad_resume["data"]["result"], WAYLINE_RESUME_STATE_INVALID)

    def test_official_wayline_progress_get_shape(self):
        manager = MissionManager()
        manager._current_task_id = "flight-active"
        manager._current_wayline = [{"seq": 0}, {"seq": 1}]
        manager._current_waypoint_idx = 1
        manager._current_step = 23
        manager._status = manager.MISSION_RUNNING

        reply = manager.handle_flighttask_progress_get(base_msg("flighttask_progress_get", {
            "sn": manager.gateway_sn,
        }))

        self.assertEqual(reply["data"]["result"], 0)
        self.assertEqual(reply["data"]["output"]["flight_id"], "flight-active")
        self.assertEqual(reply["data"]["output"]["progress"]["current_step"], 23)
        self.assertEqual(reply["data"]["output"]["progress"]["percent"], 50)
        self.assertEqual(reply["data"]["output"]["status"], "in_progress")

    def test_return_specific_home_rejects_remote_home_without_adapter(self):
        manager = MissionManager()
        reply = manager.handle_return_specific_home(base_msg("return_specific_home", {
            "home_dock_sn": "REMOTE-DOCK-001",
        }))

        self.assertEqual(reply["data"]["result"], DEVICE_STATE_UNSUPPORTED)
        self.assertEqual(reply["data"]["output"]["status"], "rejected")

    def test_in_flight_wayline_validates_fields_and_rejects_without_adapter(self):
        manager = MissionManager()
        invalid = manager.handle_in_flight_wayline_deliver(base_msg("in_flight_wayline_deliver", {
            "in_flight_wayline_id": "air-001",
            "file": {"url": "", "fingerprint": ""},
        }))
        self.assertEqual(invalid["data"]["result"], INVALID_COMMAND_FORMAT)

        rejected = manager.handle_in_flight_wayline_deliver(base_msg("in_flight_wayline_deliver", {
            "in_flight_wayline_id": "air-001",
            "file": {"url": "https://example.invalid/mission.kmz", "fingerprint": "md5"},
            "out_of_control_action": 0,
            "exit_wayline_when_rc_lost": 0,
            "rth_altitude": 120,
            "rth_mode": 1,
            "wayline_precision_type": 0,
        }))

        self.assertEqual(rejected["data"]["result"], DEVICE_STATE_UNSUPPORTED)
        self.assertEqual(rejected["data"]["output"]["status"], "rejected")
        self.assertEqual(rejected["data"]["in_flight_wayline_id"], "air-001")

    def test_flighttask_stop_uses_official_fields(self):
        manager = MissionManager()
        manager._current_task_id = "flight-active"
        manager._current_wayline = [{"seq": 0}]
        manager._status = manager.MISSION_RUNNING
        manager._run_db_coroutine = lambda coro: coro.close() if hasattr(coro, "close") else None

        reply = manager.handle_flighttask_stop(base_msg("flighttask_stop", {
            "flight_id": "flight-active",
            "reason": 0,
        }))

        self.assertEqual(reply["method"], "flighttask_stop")
        self.assertEqual(reply["data"]["result"], 0)
        self.assertEqual(reply["data"]["reason"], 0)
        self.assertEqual(reply["data"]["canceled_flight_id"], "flight-active")


if __name__ == "__main__":
    unittest.main()
