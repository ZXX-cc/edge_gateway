import os
import sys
import types
import unittest


PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

if "pymavlink" not in sys.modules:
    pymavlink = types.ModuleType("pymavlink")
    pymavlink.mavutil = types.SimpleNamespace(
        mavlink=types.SimpleNamespace(MAV_CMD_DO_CHANGE_SPEED=178)
    )
    sys.modules["pymavlink"] = pymavlink
    sys.modules["pymavlink.mavutil"] = pymavlink.mavutil

if "src.database.connection" not in sys.modules:
    connection = types.ModuleType("src.database.connection")

    async def init_database_async():
        return None

    def get_async_session_local():
        return None

    def get_database_backend():
        return "sqlite"

    def get_mongo_database():
        raise RuntimeError("MongoDB not available in unit stub")

    connection.init_database_async = init_database_async
    connection.get_async_session_local = get_async_session_local
    connection.get_database_backend = get_database_backend
    connection.get_mongo_database = get_mongo_database
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

from src.mission.manager import MissionManager  # noqa: E402


def base_msg(method, data):
    return {
        "tid": f"tid-{method}",
        "bid": f"bid-{method}",
        "gateway": "EDGE-GW-001",
        "method": method,
        "data": data,
    }


class RecordingNest:
    def __init__(self):
        self.commands = []
        self.takeoff_requests = 0

    def send_gcs_command(self, command):
        self.commands.append(getattr(command, "name", str(command)))

    def request_takeoff(self):
        self.takeoff_requests += 1


class RecordingFlightControl:
    def __init__(self):
        self.calls = []

    def takeoff(self, height):
        self.calls.append(("takeoff", height))
        return {"success": True, "message": "takeoff ok"}

    def one_click_return(self):
        self.calls.append(("one_click_return", None))
        return {"success": True, "message": "return ok"}

    def land(self):
        self.calls.append(("land", None))
        return {"success": True, "message": "land ok"}

    def set_mode(self, mode):
        self.calls.append(("set_mode", mode))
        return {"success": True, "message": f"set mode {mode}"}


class OneKeyTakeoffFlowTest(unittest.TestCase):
    def test_one_key_takeoff_flow_closes_full_real_adapter_loop(self):
        manager = MissionManager()
        nest = RecordingNest()
        flight = RecordingFlightControl()
        events = []
        manager.set_nest_module(nest)
        manager.set_flight_control(flight)
        manager.set_event_callback(events.append)
        manager._current_task_id = "flight-001"
        manager._current_wayline = [{"seq": 0}, {"seq": 1}]
        manager.start_mission_monitor = lambda: True

        reply = manager.handle_one_key_takeoff_flow(base_msg("one_key_takeoff_flow", {
            "flight_id": "flight-001",
            "takeoff_height": 35,
            "auto_complete": True,
        }))

        self.assertEqual(reply["data"]["result"], 0)
        self.assertEqual(reply["data"]["output"]["status"], "ok")
        self.assertEqual(
            nest.commands,
            ["CHARGE_STOP", "DOOR_OPEN", "CLAMP_RELEASE", "CLAMP_TIGHTEN", "DOOR_CLOSE", "CHARGE_START"],
        )
        self.assertEqual(nest.takeoff_requests, 1)
        self.assertEqual(
            flight.calls,
            [("takeoff", 35.0), ("set_mode", "AUTO"), ("one_click_return", None), ("land", None)],
        )
        step_names = [step["name"] for step in reply["data"]["output"]["steps"]]
        self.assertEqual(
            step_names,
            [
                "precheck",
                "charge_close",
                "cover_open",
                "putter_open",
                "nest_takeoff_request",
                "flight_takeoff",
                "flighttask_execute",
                "return_home",
                "landing",
                "putter_close",
                "cover_close",
                "charge_open",
                "completed",
            ],
        )
        self.assertTrue(any(event.get("method") == "one_key_takeoff_progress" for event in events))
        self.assertTrue(any(event.get("method") == "flighttask_progress" for event in events))

    def test_one_key_takeoff_flow_rejects_simulate_mission(self):
        manager = MissionManager()

        reply = manager.handle_one_key_takeoff_flow(base_msg("one_key_takeoff_flow", {
            "flight_id": "flight-sim-disabled",
            "simulate_mission": {"is_enable": 1},
        }))

        self.assertNotEqual(reply["data"]["result"], 0)
        self.assertEqual(reply["data"]["output"]["status"], "rejected")

    def test_one_key_takeoff_flow_fails_without_nest_module(self):
        manager = MissionManager()
        manager.set_flight_control(RecordingFlightControl())

        reply = manager.handle_one_key_takeoff_flow(base_msg("one_key_takeoff_flow", {
            "flight_id": "flight-no-nest",
        }))

        self.assertNotEqual(reply["data"]["result"], 0)
        self.assertEqual(reply["data"]["output"]["status"], "failed")

    def test_one_key_takeoff_flow_can_stop_after_execution_started(self):
        manager = MissionManager()
        nest = RecordingNest()
        flight = RecordingFlightControl()
        manager.set_nest_module(nest)
        manager.set_flight_control(flight)

        reply = manager.handle_one_key_takeoff_flow(base_msg("one_key_takeoff_flow", {
            "flight_id": "flight-002",
            "auto_complete": False,
        }))

        self.assertEqual(reply["data"]["result"], 0)
        self.assertEqual(reply["data"]["output"]["status"], "in_progress")
        self.assertEqual(nest.commands, ["CHARGE_STOP", "DOOR_OPEN", "CLAMP_RELEASE"])
        self.assertEqual(flight.calls, [("takeoff", 30.0)])

    def test_real_one_key_takeoff_flow_switches_auto_for_prepared_wayline(self):
        manager = MissionManager()
        nest = RecordingNest()
        flight = RecordingFlightControl()
        events = []
        manager.set_nest_module(nest)
        manager.set_flight_control(flight)
        manager.set_event_callback(events.append)
        manager.start_mission_monitor = lambda: True
        manager._current_task_id = "flight-real-001"
        manager._current_wayline = [{"seq": 0}, {"seq": 1}, {"seq": 2}]
        manager._simulate_mission = {"is_enable": 0}

        reply = manager.handle_one_key_takeoff_flow(base_msg("one_key_takeoff_flow", {
            "flight_id": "flight-real-001",
            "takeoff_height": 25,
            "auto_complete": False,
        }))

        self.assertEqual(reply["data"]["result"], 0)
        self.assertEqual(reply["data"]["output"]["status"], "in_progress")
        self.assertEqual(flight.calls, [("takeoff", 25.0), ("set_mode", "AUTO")])
        step_names = [step["name"] for step in reply["data"]["output"]["steps"]]
        self.assertEqual(step_names[-1], "flighttask_execute")
        self.assertEqual(manager._status, manager.MISSION_RUNNING)
        self.assertTrue(any(event.get("method") == "flighttask_progress" for event in events))


if __name__ == "__main__":
    unittest.main()
