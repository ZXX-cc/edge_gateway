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

from src.mavlink.flight_modes import (  # noqa: E402
    mode_name_to_custom_mode,
    mode_to_dji_code,
    normalize_flight_mode_request,
    parse_flight_mode,
)
from src.mission.manager import MissionManager  # noqa: E402
from src.utils.dji_error_codes import DEVICE_COMMAND_TIMEOUT, DEVICE_STATE_UNSUPPORTED, INVALID_COMMAND_FORMAT  # noqa: E402


def base_msg(data):
    return {
        "tid": "tid-set-flight-mode",
        "bid": "bid-set-flight-mode",
        "gateway": "EDGEGW001",
        "method": "set_flight_mode",
        "data": data,
    }


class RecordingFlightControl:
    def __init__(self, success=True, message="ok"):
        self.success = success
        self.message = message
        self.calls = []
        self.timeouts = []
        self.armed = False

    def set_mode(self, mode, timeout=10.0):
        self.calls.append(mode)
        self.timeouts.append(timeout)
        return {"success": self.success, "message": self.message}

    def get_available_modes(self):
        return ["QSTABILIZE", "QHOVER", "QLOITER", "QLAND", "AUTO", "GUIDED", "MANUAL"]

    def is_armed_status(self):
        return self.armed

    def arm(self):
        self.armed = True
        return True

    def arm_and_wait(self, timeout=10.0, readiness_timeout=0.0):
        self.armed = True
        return {"success": True, "message": "ok"}

    def _wait_for_armed(self, target_state, timeout=10.0):
        return {"success": self.armed == target_state, "message": "ok"}


class ArmabilityGateFlightControl(RecordingFlightControl):
    def arm_and_wait(self, timeout=10.0, readiness_timeout=30.0):
        self.calls.append(("arm_and_wait", timeout, readiness_timeout))
        return {
            "success": False,
            "message": "等待飞控预解锁就绪超时: 飞控预解锁检查尚未通过",
        }


class FlightModeMappingTest(unittest.TestCase):
    def test_arducopter_and_plane_custom_mode_maps_are_separate(self):
        self.assertEqual(parse_flight_mode(9, mav_type=2), "LAND")
        self.assertEqual(parse_flight_mode(15, mav_type=1), "GUIDED")
        self.assertEqual(parse_flight_mode(9, mav_type=1), "UNKNOWN(9)")

    def test_mode_code_normalizes_to_stable_cloud_mode_name(self):
        ok, normalized, reason = normalize_flight_mode_request({"mode": 4})

        self.assertTrue(ok, reason)
        self.assertEqual(normalized.mode_name, "GUIDED")
        self.assertEqual(normalized.mode_code, 4)
        self.assertEqual(mode_to_dji_code("GUIDED"), 4)
        self.assertEqual(mode_name_to_custom_mode("GUIDED", "copter"), 4)
        self.assertEqual(mode_name_to_custom_mode("GUIDED", "plane"), 15)

    def test_person2_plane_quadplane_mode_code_table(self):
        expected = {
            0: ("MANUAL", 2),
            10: ("AUTO", 4),
            11: ("RTL", 6),
            12: ("LOITER", 9),
            13: ("TAKEOFF", 3),
            15: ("GUIDED", 4),
            20: ("QLAND", 7),
            21: ("QRTL", 6),
            6: ("FBWB", 2),
            5: ("FBWA", 2),
            17: ("QSTABILIZE", 2),
            18: ("QHOVER", 9),
            19: ("QLOITER", 9),
        }

        for custom_mode, (flight_mode, mode_code) in expected.items():
            with self.subTest(custom_mode=custom_mode):
                self.assertEqual(parse_flight_mode(custom_mode, mav_type=1), flight_mode)
                self.assertEqual(mode_to_dji_code(flight_mode), mode_code)

    def test_duplicate_mode_codes_use_stable_request_defaults(self):
        expected = {
            2: "MANUAL",
            3: "TAKEOFF",
            4: "GUIDED",
            6: "RTL",
            7: "QLAND",
            9: "LOITER",
        }

        for mode_code, flight_mode in expected.items():
            ok, normalized, reason = normalize_flight_mode_request({"mode_code": mode_code})
            with self.subTest(mode_code=mode_code):
                self.assertTrue(ok, reason)
                self.assertEqual(normalized.mode_name, flight_mode)
                self.assertEqual(normalized.mode_code, mode_code)

    def test_set_flight_mode_accepts_cloud_shapes_and_returns_mode(self):
        manager = MissionManager()
        flight_control = RecordingFlightControl()
        manager.set_flight_control(flight_control)

        for payload in (
            {"mode": 4},
            {"mode_code": 4},
            {"flight_mode": "GUIDED"},
            {"mode": "GUIDED"},
        ):
            reply = manager.handle_set_flight_mode(base_msg(payload))
            self.assertEqual(reply["data"]["result"], 0, payload)
            self.assertEqual(reply["data"]["mode"], "GUIDED")
            self.assertEqual(reply["data"]["mode_code"], 4)

        self.assertEqual(flight_control.calls, ["GUIDED", "GUIDED", "GUIDED", "GUIDED"])
        self.assertEqual(flight_control.timeouts, [10.0, 10.0, 10.0, 10.0])

    def test_set_flight_mode_passes_timeout_to_flight_control(self):
        manager = MissionManager()
        flight_control = RecordingFlightControl()
        manager.set_flight_control(flight_control)

        reply = manager.handle_set_flight_mode(base_msg({"flight_mode": "GUIDED", "timeout": 18}))

        self.assertEqual(reply["data"]["result"], 0)
        self.assertEqual(flight_control.calls, ["GUIDED"])
        self.assertEqual(flight_control.timeouts, [18.0])

    def test_set_flight_mode_rejects_invalid_payload(self):
        manager = MissionManager()
        manager.set_flight_control(RecordingFlightControl())

        reply = manager.handle_set_flight_mode(base_msg({"mode_code": 999}))

        self.assertEqual(reply["data"]["result"], INVALID_COMMAND_FORMAT)

    def test_set_flight_mode_does_not_report_success_when_mavlink_fails(self):
        manager = MissionManager()
        manager.set_flight_control(RecordingFlightControl(success=False, message="模式切换超时，当前模式: MANUAL"))

        reply = manager.handle_set_flight_mode(base_msg({"mode": 4}))

        self.assertEqual(reply["data"]["result"], DEVICE_COMMAND_TIMEOUT)

    def test_set_flight_mode_rejects_guided_inside_takeoff_protection_zone(self):
        manager = MissionManager()
        flight_control = RecordingFlightControl()
        flight_control.armed = True
        manager.set_flight_control(flight_control)
        manager._set_loiter_guided_release_altitude(100.0)
        manager.set_waypoint_manager(types.SimpleNamespace(
            home_position={"lat": 44.0474824, "lon": 125.3614503}
        ))
        manager.set_telemetry_module(types.SimpleNamespace(
            get_telemetry_data=lambda: {
                "latitude": 44.0475000,
                "longitude": 125.3614600,
                "altitudeRelative": 80.0,
            }
        ))

        reply = manager.handle_set_flight_mode(base_msg({"flight_mode": "GUIDED"}))

        self.assertEqual(reply["data"]["result"], DEVICE_STATE_UNSUPPORTED)
        self.assertIn("安全起飞保护区", reply["msg"])
        self.assertEqual(flight_control.calls, [])

    def test_set_flight_mode_allows_guided_after_original_first_waypoint_altitude(self):
        manager = MissionManager()
        flight_control = RecordingFlightControl()
        flight_control.armed = True
        manager.set_flight_control(flight_control)
        manager._set_loiter_guided_release_altitude(80.0)
        manager.set_waypoint_manager(types.SimpleNamespace(
            home_position={"lat": 44.0474824, "lon": 125.3614503}
        ))
        manager.set_telemetry_module(types.SimpleNamespace(
            get_telemetry_data=lambda: {
                "latitude": 44.0475000,
                "longitude": 125.3614600,
                "altitudeRelative": 80.0,
            }
        ))

        reply = manager.handle_set_flight_mode(base_msg({"flight_mode": "GUIDED"}))

        self.assertEqual(reply["data"]["result"], 0)
        self.assertEqual(flight_control.calls, ["GUIDED"])

    def test_set_flight_mode_rejects_missing_altitude_before_original_first_waypoint(self):
        manager = MissionManager()
        flight_control = RecordingFlightControl()
        flight_control.armed = True
        manager.set_flight_control(flight_control)
        manager._set_loiter_guided_release_altitude(100.0, waypoint_seq=1)
        manager._current_waypoint_idx = 0
        manager.set_waypoint_manager(types.SimpleNamespace(
            home_position={"lat": 44.0474824, "lon": 125.3614503}
        ))
        manager.set_telemetry_module(types.SimpleNamespace(
            get_telemetry_data=lambda: {
                "latitude": 44.0475000,
                "longitude": 125.3614600,
            }
        ))

        reply = manager.handle_set_flight_mode(base_msg({"flight_mode": "GUIDED"}))

        self.assertEqual(reply["data"]["result"], DEVICE_STATE_UNSUPPORTED)
        self.assertIn("信息不足", reply["msg"])
        self.assertEqual(flight_control.calls, [])

    def test_set_flight_mode_allows_missing_altitude_after_original_first_waypoint(self):
        manager = MissionManager()
        flight_control = RecordingFlightControl()
        flight_control.armed = True
        manager.set_flight_control(flight_control)
        manager._set_loiter_guided_release_altitude(100.0, waypoint_seq=1)
        manager._current_waypoint_idx = 1
        manager.set_waypoint_manager(types.SimpleNamespace(
            home_position={"lat": 44.0474824, "lon": 125.3614503}
        ))
        manager.set_telemetry_module(types.SimpleNamespace(
            get_telemetry_data=lambda: {
                "latitude": 44.0475000,
                "longitude": 125.3614600,
            }
        ))

        reply = manager.handle_set_flight_mode(base_msg({"flight_mode": "GUIDED"}))

        self.assertEqual(reply["data"]["result"], 0)
        self.assertEqual(flight_control.calls, ["GUIDED"])

    def test_set_flight_mode_arm_avoids_non_armable_qland(self):
        for payload in ({"mode": "QLAN", "arm": True}, {"flight_mode": "QLAND", "arm": True}, {"mode_code": 7, "arm": True}):
            with self.subTest(payload=payload):
                manager = MissionManager()
                flight_control = RecordingFlightControl()
                manager.set_flight_control(flight_control)

                reply = manager.handle_set_flight_mode(base_msg(payload))

                self.assertEqual(reply["data"]["result"], 0)
                self.assertEqual(reply["data"]["mode"], "QSTABILIZE")
                self.assertEqual(reply["data"]["armed"], True)
                self.assertEqual(flight_control.calls, ["QSTABILIZE"])

    def test_set_flight_mode_uses_armability_gate_before_arm(self):
        manager = MissionManager()
        flight_control = ArmabilityGateFlightControl()
        manager.set_flight_control(flight_control)

        reply = manager.handle_set_flight_mode(base_msg({
            "mode": "QLAN",
            "arm": True,
            "timeout": 7,
            "prearm_timeout": 12,
        }))

        self.assertEqual(reply["data"]["result"], DEVICE_COMMAND_TIMEOUT)
        self.assertEqual(flight_control.calls, [
            "QSTABILIZE",
            ("arm_and_wait", 7.0, 12.0),
        ])
        self.assertFalse(flight_control.armed)


if __name__ == "__main__":
    unittest.main()
