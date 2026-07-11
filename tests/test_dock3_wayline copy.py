import os
import sys
import time
import types
import unittest
import tempfile
import zipfile
from pathlib import Path


PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

try:
    import yaml  # noqa: F401
except Exception:
    yaml_stub = types.ModuleType("yaml")
    yaml_stub.safe_load = lambda *_args, **_kwargs: {}
    sys.modules["yaml"] = yaml_stub

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
from src.utils.dji_error_codes import DEVICE_STATE_UNSUPPORTED, INVALID_COMMAND_FORMAT


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
        return False

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

    def arm(self):
        self.calls.append(("arm", None))
        self.armed = True
        return True

    def _wait_for_armed(self, target_state, timeout=10.0):
        self.calls.append(("wait_for_armed", target_state))
        return {"success": self.armed == target_state, "message": "ok"}


class AutoMissionFlightControl:
    def __init__(self):
        self.current_mode_name = "AUTO"
        self.current_waypoint = 2
        self.mission_count = 5
        self.calls = []

    def pause_mission(self):
        self.calls.append(("pause_mission", None))
        return {"success": True, "message": "ok"}


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
            manager._simulate_mission = {"is_enable": 1}
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

            manager._current_task_id = "flight-001"
            manager._current_wayline = [{"seq": 0}, {"seq": 1}, {"seq": 2}, {"seq": 3}]
            manager._current_waypoint_idx = 2
            manager._status = manager.MISSION_RUNNING
            manager._simulate_mission = {"is_enable": 1}

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

        self.assertEqual(pause["data"]["result"], DEVICE_STATE_UNSUPPORTED)
        self.assertEqual(pause["data"]["output"]["status"], "rejected")
        self.assertEqual(manager._status, manager.MISSION_RUNNING)
        self.assertEqual(flight_control.calls, [("pause_mission", None)])

    def test_pause_recovers_running_state_from_auto_flight_controller(self):
        manager = MissionManager()
        flight_control = AutoMissionFlightControl()
        manager.set_flight_control(flight_control)
        manager._status = manager.MISSION_IDLE
        manager._simulate_mission = {"is_enable": 0}
        manager.stop_mission_monitor = lambda: None
        manager._run_db_coroutine = lambda coro: coro.close() if hasattr(coro, "close") else None

        pause = manager.handle_flighttask_pause(base_msg("flighttask_pause", {}))

        self.assertEqual(pause["data"]["result"], 0)
        self.assertEqual(pause["data"]["output"]["status"], "paused")
        self.assertEqual(manager._status, manager.MISSION_PAUSED)
        self.assertEqual(manager._current_waypoint_idx, 2)
        self.assertEqual(flight_control.calls, [("pause_mission", None)])

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
        self.assertEqual(monitor_calls, ["stop"])
        self.assertEqual(flight_control.calls, [("set_mode", "AUTO")])

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
        flight_control = FailingFlightControl()
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
                "simulate_mission": {
                    "is_enable": 1,
                    "latitude": -35.36,
                    "longitude": 149.16,
                },
            }))
        self.assertEqual(prepare["data"]["result"], 0)
        self.assertEqual(prepare["data"]["output"]["status"], "sent")
        file_summary = prepare["data"]["output"]["ext"]["file"]
        self.assertTrue(file_summary["parsed"])
        self.assertEqual(file_summary["waypoint_count"], 2)
        self.assertTrue(any(event.get("method") == "flighttask_ready" for event in events))
        self.assertEqual(waypoint_manager.clear_calls, 0)
        self.assertEqual(waypoint_manager.added, [])
        self.assertEqual(waypoint_manager.upload_calls, [])

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
        self.assertEqual(flight_control.calls, [])
        self.assertTrue(any(event.get("method") == "flighttask_progress" for event in events))

    def test_real_flighttask_execute_prearms_before_auto(self):
        manager = MissionManager()
        flight_control = RecordingRealWaylineFlightControl()
        manager.set_flight_control(flight_control)
        manager.start_mission_monitor = lambda: None
        manager._simulate_mission = {"is_enable": 0}
        manager._current_wayline = [{"seq": 0}, {"seq": 1}]

        execute = manager.handle_flighttask_execute(base_msg("flighttask_execute", {
            "flight_id": "flight-001",
        }))

        self.assertEqual(execute["data"]["result"], 0)
        self.assertEqual(execute["data"]["output"]["status"], "in_progress")
        self.assertEqual(flight_control.calls, [
            ("set_mode", "QSTABILIZE"),
            ("arm", None),
            ("wait_for_armed", True),
            ("set_mode", "AUTO"),
        ])

    def test_simulated_takeoff_flow_requests_nest_and_completes(self):
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
            self.assertEqual(prepare["data"]["result"], 0)

            execute = manager.handle_flighttask_execute(base_msg("flighttask_execute", {
                "flight_id": "takeoff-flow-001",
            }))
            self.assertEqual(execute["data"]["result"], 0)
            self.assertEqual(nest.calls, ["request_takeoff"])

            completed = manager.complete_simulated_mission()
            self.assertTrue(completed)
            self.assertEqual(manager._status, manager.MISSION_COMPLETED)
            self.assertEqual(manager._current_step, 33)
            self.assertEqual(nest.calls, ["request_takeoff"])

        statuses = [
            event.get("data", {}).get("output", {}).get("status")
            for event in events
            if event.get("method") == "flighttask_progress"
        ]
        self.assertIn("sent", statuses)
        self.assertIn("in_progress", statuses)
        self.assertIn("ok", statuses)

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


if __name__ == "__main__":
    unittest.main()
