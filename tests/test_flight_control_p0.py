import os
import sys
import types
import unittest


PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

try:
    from pymavlink import mavutil as _existing_mavutil  # noqa: F401
    _existing_mavlink = getattr(_existing_mavutil, "mavlink", None)
    _needs_stub = not all(
        hasattr(_existing_mavlink, name)
        for name in (
            "MAV_MODE_FLAG_CUSTOM_MODE_ENABLED",
            "MAV_CMD_NAV_TAKEOFF",
            "MAV_CMD_COMPONENT_ARM_DISARM",
        )
    )
except Exception:
    _needs_stub = True

if _needs_stub:
    pymavlink = types.ModuleType("pymavlink")
    mavutil = types.ModuleType("pymavlink.mavutil")

    class _TakeoffMessage:
        def __init__(self, *args):
            self.args = args

    mavutil.mavlink = types.SimpleNamespace(
        MAV_MODE_FLAG_CUSTOM_MODE_ENABLED=1,
        MAV_CMD_NAV_TAKEOFF=22,
        MAV_CMD_COMPONENT_ARM_DISARM=400,
        MAVLink_command_long_message=_TakeoffMessage,
        MAVLink_set_mode_message=_TakeoffMessage,
    )
    pymavlink.mavutil = mavutil
    sys.modules["pymavlink"] = pymavlink
    sys.modules["pymavlink.mavutil"] = mavutil

from src.modules.flight_control import FlightControl  # noqa: E402


class RecordingFlightControl(FlightControl):
    def __init__(self):
        super().__init__()
        self.mode_calls = []
        self.command_calls = []

    def set_mode(self, mode):
        self.mode_calls.append(mode)
        return {"success": True, "message": "ok", "elapsed_time": 0}

    def _send_command_long(self, command, param1=0, param2=0, param3=0, param4=0, param5=0, param6=0, param7=0):
        self.command_calls.append(
            {
                "command": command,
                "param1": param1,
                "param2": param2,
                "param3": param3,
                "param4": param4,
                "param5": param5,
                "param6": param6,
                "param7": param7,
            }
        )
        return True


class FlightControlP0ModeSelectionTest(unittest.TestCase):
    def test_copter_takeoff_uses_guided_then_nav_takeoff(self):
        control = RecordingFlightControl()
        control._set_mode_profile("copter")

        result = control.takeoff(altitude=30)

        self.assertTrue(result["success"], result)
        self.assertEqual(control.mode_calls, ["GUIDED"])
        self.assertEqual(control.command_calls[0]["command"], 22)
        self.assertEqual(control.command_calls[0]["param7"], 30)

    def test_plane_takeoff_prefers_takeoff_mode(self):
        control = RecordingFlightControl()
        control._set_mode_profile("plane")

        result = control.takeoff(altitude=40)

        self.assertTrue(result["success"], result)
        self.assertEqual(control.mode_calls, ["TAKEOFF"])
        self.assertEqual(control.command_calls[0]["param7"], 40)

    def test_copter_land_uses_land_mode(self):
        control = RecordingFlightControl()
        control._set_mode_profile("copter")

        result = control.land()

        self.assertTrue(result["success"], result)
        self.assertEqual(result["mode"], "LAND")
        self.assertEqual(control.mode_calls, ["LAND"])

    def test_plane_land_uses_qland_mode(self):
        control = RecordingFlightControl()
        control._set_mode_profile("plane")

        result = control.land()

        self.assertTrue(result["success"], result)
        self.assertEqual(result["mode"], "QLAND")
        self.assertEqual(control.mode_calls, ["QLAND"])


if __name__ == "__main__":
    unittest.main()
