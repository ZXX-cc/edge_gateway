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
            "MAV_FRAME_GLOBAL_RELATIVE_ALT_INT",
            "MAV_FRAME_BODY_NED",
            "MAV_CMD_NAV_WAYPOINT",
        )
    )
except Exception:
    _needs_stub = True

if _needs_stub:
    pymavlink = types.ModuleType("pymavlink")
    mavutil = types.ModuleType("pymavlink.mavutil")
    mavutil.mavlink = types.SimpleNamespace(
        MAV_FRAME_GLOBAL_RELATIVE_ALT_INT=6,
        MAV_FRAME_BODY_NED=8,
        MAV_CMD_NAV_WAYPOINT=16,
    )
    pymavlink.mavutil = mavutil
    sys.modules["pymavlink"] = pymavlink
    sys.modules["pymavlink.mavutil"] = mavutil

from src.modules.mode_control import ModeControl  # noqa: E402


class RecordingMav:
    def __init__(self):
        self.mission_item_int_calls = []
        self.position_target_local_calls = []

    def mission_item_int_send(self, *args):
        self.mission_item_int_calls.append(args)

    def set_position_target_local_ned_send(self, *args):
        self.position_target_local_calls.append(args)


class ModeControlGuidedTest(unittest.TestCase):
    def test_guided_goto_uses_master_mav_connection_shape(self):
        mav = RecordingMav()
        mode = ModeControl()
        mode.dispatcher = types.SimpleNamespace(
            connection=types.SimpleNamespace(master=types.SimpleNamespace(mav=mav))
        )

        result = mode.guided_goto(-35.3632622, 149.1652375, 50)

        self.assertTrue(result["success"], result)
        self.assertEqual(len(mav.mission_item_int_calls), 1)
        call = mav.mission_item_int_calls[0]
        self.assertEqual(call[11], int(-35.3632622 * 1.0e7))
        self.assertEqual(call[12], int(149.1652375 * 1.0e7))
        self.assertEqual(call[13], 50)

    def test_guided_forward_uses_master_mav_connection_shape(self):
        mav = RecordingMav()
        mode = ModeControl()
        mode.dispatcher = types.SimpleNamespace(
            connection=types.SimpleNamespace(master=types.SimpleNamespace(mav=mav))
        )

        result = mode.guided_forward(5)

        self.assertTrue(result["success"], result)
        self.assertEqual(len(mav.position_target_local_calls), 1)

    def test_guided_goto_reports_failure_without_mav_sender(self):
        mode = ModeControl()
        mode.dispatcher = types.SimpleNamespace(connection=types.SimpleNamespace(master=object()))

        result = mode.guided_goto(-35.3632622, 149.1652375, 50)

        self.assertFalse(result["success"])
        self.assertIn("MAVLink", result["message"])


if __name__ == "__main__":
    unittest.main()
