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

# MissionManager imports pymavlink at module import time, but these tests only
# exercise MQTT payload/state logic and do not need a real MAVLink dependency.
if "pymavlink" not in sys.modules:
    pymavlink = types.ModuleType("pymavlink")
    pymavlink.mavutil = types.SimpleNamespace()
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


class FailingFlightControl:
    def __init__(self):
        self.calls = []

    def set_mode(self, mode):
        self.calls.append(("set_mode", mode))
        return {"success": False, "message": f"模式切换超时，当前模式: MANUAL, target={mode}"}

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

    def clear_waypoints(self):
        self.clear_calls += 1

    def add_waypoint_from_dict(self, waypoint):
        self.added.append(waypoint)

    def upload_mission(self, timeout=15.0):
        self.upload_calls.append(timeout)
        return False


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
