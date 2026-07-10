import os
import sys
import threading
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
            "MAV_CMD_DO_PAUSE_CONTINUE",
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
        MAV_CMD_DO_PAUSE_CONTINUE=193,
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
        self.mode_timeouts = []
        self.command_calls = []
        self.arm_wait_calls = []
        self.arm_wait_result = {"success": True, "message": "armed"}

    def set_mode(self, mode, timeout=10.0):
        self.mode_calls.append(mode)
        self.mode_timeouts.append(timeout)
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

    def arm_and_wait(self, timeout=10.0, readiness_timeout=30.0):
        self.arm_wait_calls.append((timeout, readiness_timeout))
        if self.arm_wait_result.get("success"):
            self.is_armed = True
        return dict(self.arm_wait_result)


class FlightControlP0ModeSelectionTest(unittest.TestCase):
    def test_copter_takeoff_uses_guided_then_nav_takeoff(self):
        control = RecordingFlightControl()
        control._set_mode_profile("copter")
        control.is_armed = True

        result = control.takeoff(altitude=30)

        self.assertTrue(result["success"], result)
        self.assertEqual(control.mode_calls, ["GUIDED"])
        self.assertEqual(control.arm_wait_calls, [])
        self.assertEqual(control.command_calls[0]["command"], 22)
        self.assertEqual(control.command_calls[0]["param7"], 30)

    def test_plane_takeoff_uses_guided_for_nav_takeoff_when_available(self):
        control = RecordingFlightControl()
        control._set_mode_profile("plane")
        control.is_armed = True

        result = control.takeoff(altitude=40)

        self.assertTrue(result["success"], result)
        self.assertEqual(control.mode_calls, ["GUIDED"])
        self.assertEqual(control.arm_wait_calls, [])
        self.assertEqual(control.command_calls[0]["param7"], 40)

    def test_takeoff_arms_before_nav_takeoff_when_disarmed(self):
        control = RecordingFlightControl()
        control._set_mode_profile("copter")

        result = control.takeoff(altitude=25)

        self.assertTrue(result["success"], result)
        self.assertEqual(control.mode_calls, ["GUIDED"])
        self.assertEqual(control.arm_wait_calls, [(10.0, 30.0)])
        self.assertEqual(control.command_calls[0]["command"], 22)
        self.assertEqual(control.command_calls[0]["param7"], 25)

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

    def test_plane_pause_uses_qloiter_without_native_command(self):
        control = RecordingFlightControl()
        control._set_mode_profile("plane")

        result = control.pause_mission()

        self.assertTrue(result["success"], result)
        self.assertEqual(control.command_calls, [])
        self.assertEqual(control.mode_calls, ["QLOITER"])
        self.assertEqual(result["pause_strategy"], "hold_mode")
        self.assertEqual(result["target_mode"], "QLOITER")
        self.assertFalse(result["native_pause"])
        self.assertIn("不发送原生暂停命令", result["fallback_reason"])

    def test_plane_pause_tries_each_hold_mode_until_confirmed(self):
        control = RecordingFlightControl()
        control._set_mode_profile("plane")

        def set_mode(mode, timeout=10.0):
            control.mode_calls.append(mode)
            control.mode_timeouts.append(timeout)
            if mode == "LOITER":
                return {"success": True, "message": "loiter confirmed"}
            return {"success": False, "message": f"{mode} unavailable"}

        control.set_mode = set_mode

        result = control.pause_mission()

        self.assertTrue(result["success"], result)
        self.assertEqual(control.command_calls, [])
        self.assertEqual(control.mode_calls, ["QLOITER", "LOITER"])
        self.assertEqual(control.mode_timeouts, [8.0, 8.0])
        self.assertEqual(result["target_mode"], "LOITER")
        self.assertEqual(
            [attempt["success"] for attempt in result["hold_attempts"]],
            [False, True],
        )

    def test_plane_pause_fails_when_no_hold_mode_is_confirmed(self):
        control = RecordingFlightControl()
        control._set_mode_profile("plane")

        def set_mode(mode, timeout=10.0):
            control.mode_calls.append(mode)
            control.mode_timeouts.append(timeout)
            return {"success": False, "message": f"{mode} timeout"}

        control.set_mode = set_mode

        result = control.pause_mission()

        self.assertFalse(result["success"], result)
        self.assertEqual(control.command_calls, [])
        self.assertEqual(control.mode_calls, ["QLOITER", "LOITER"])
        self.assertEqual(control.mode_timeouts, [8.0, 8.0])
        self.assertIsNone(result["target_mode"])
        self.assertFalse(result["native_pause"])
        self.assertEqual(len(result["hold_attempts"]), 2)

    def test_set_mode_uses_caller_timeout_for_heartbeat_confirmation(self):
        control = FlightControl()
        captured = {}
        control._send_message = lambda message: True

        def wait_for_mode(target_mode, timeout):
            captured["target_mode"] = target_mode
            captured["timeout"] = timeout
            return {"success": True, "message": "confirmed"}

        control._wait_for_mode = wait_for_mode

        result = control.set_mode("QLOITER", timeout=8.0)

        self.assertTrue(result["success"], result)
        self.assertEqual(captured, {"target_mode": 19, "timeout": 8.0})

    def test_copter_pause_uses_native_command(self):
        control = RecordingFlightControl()
        control._set_mode_profile("copter")
        control._wait_for_command_ack = lambda *args, **kwargs: {
            "result": 0,
            "result_text": "接受",
            "timestamp": 2.0,
        }

        result = control.pause_mission()

        self.assertTrue(result["success"], result)
        self.assertEqual(control.command_calls[0]["command"], 193)
        self.assertEqual(control.command_calls[0]["param1"], 0)
        self.assertEqual(control.mode_calls, [])
        self.assertEqual(result["pause_strategy"], "native_command")
        self.assertTrue(result["native_pause"])
        self.assertIsNone(result["target_mode"])

    def test_copter_pause_falls_back_to_confirmed_hold_mode(self):
        control = RecordingFlightControl()
        control._set_mode_profile("copter")
        control._wait_for_command_ack = lambda *args, **kwargs: {
            "result": 3,
            "result_text": "不支持",
            "timestamp": 2.0,
        }

        result = control.pause_mission()

        self.assertTrue(result["success"], result)
        self.assertEqual(control.command_calls[0]["command"], 193)
        self.assertEqual(control.mode_calls, ["LOITER"])
        self.assertEqual(result["pause_strategy"], "hold_mode")
        self.assertEqual(result["target_mode"], "LOITER")
        self.assertFalse(result["native_pause"])
        self.assertIn("不支持", result["fallback_reason"])

    def test_in_progress_ack_waits_for_final_success(self):
        control = RecordingFlightControl()
        acknowledgements = iter([
            {"result": 5, "result_text": "执行中", "timestamp": 2.0},
            {"result": 0, "result_text": "接受", "timestamp": 3.0},
        ])
        calls = []

        def wait_for_ack(*args, **kwargs):
            calls.append((args, kwargs))
            return next(acknowledgements)

        control._wait_for_command_ack = wait_for_ack

        result = control._send_pause_continue(pause=True)

        self.assertTrue(result["success"], result)
        self.assertEqual(result["ack_result"], 0)
        self.assertEqual(len(calls), 2)
        self.assertTrue(calls[1][1]["strictly_after"])

    def test_in_progress_ack_without_final_ack_is_not_success(self):
        control = RecordingFlightControl()
        acknowledgements = iter([
            {"result": 5, "result_text": "执行中", "timestamp": 2.0},
            None,
        ])
        control._wait_for_command_ack = lambda *args, **kwargs: next(acknowledgements)

        result = control._send_pause_continue(pause=True)

        self.assertFalse(result["success"], result)
        self.assertEqual(result["ack_result"], 5)
        self.assertIn("未收到最终 ACK", result["message"])

    def test_in_progress_ack_uses_final_rejection(self):
        control = RecordingFlightControl()
        acknowledgements = iter([
            {"result": 5, "result_text": "执行中", "timestamp": 2.0},
            {"result": 4, "result_text": "失败", "timestamp": 3.0},
        ])
        control._wait_for_command_ack = lambda *args, **kwargs: next(acknowledgements)

        result = control._send_pause_continue(pause=True)

        self.assertFalse(result["success"], result)
        self.assertEqual(result["ack_result"], 4)
        self.assertIn("失败", result["message"])

    def test_pause_and_resume_native_command_transactions_are_serialized(self):
        control = RecordingFlightControl()
        first_wait_started = threading.Event()
        release_first_wait = threading.Event()
        resume_call_started = threading.Event()
        second_command_sent = threading.Event()
        wait_count_lock = threading.Lock()
        wait_count = 0

        def send_command(command, param1=0, **kwargs):
            control.command_calls.append({"command": command, "param1": param1})
            if len(control.command_calls) == 2:
                second_command_sent.set()
            return True

        def wait_for_ack(*args, **kwargs):
            nonlocal wait_count
            with wait_count_lock:
                wait_count += 1
                call_number = wait_count
            if call_number == 1:
                first_wait_started.set()
                self.assertTrue(release_first_wait.wait(1.0))
            return {
                "result": 0,
                "result_text": "接受",
                "timestamp": float(call_number),
            }

        control._send_command_long = send_command
        control._wait_for_command_ack = wait_for_ack
        results = {}
        pause_thread = threading.Thread(
            target=lambda: results.setdefault("pause", control._send_pause_continue(True))
        )

        def resume_worker():
            resume_call_started.set()
            results["resume"] = control._send_pause_continue(False)

        resume_thread = threading.Thread(target=resume_worker)

        pause_thread.start()
        self.assertTrue(first_wait_started.wait(1.0))
        resume_thread.start()
        self.assertTrue(resume_call_started.wait(1.0))
        self.assertFalse(second_command_sent.wait(0.05))
        release_first_wait.set()
        pause_thread.join(1.0)
        resume_thread.join(1.0)

        self.assertFalse(pause_thread.is_alive())
        self.assertFalse(resume_thread.is_alive())
        self.assertTrue(results["pause"]["success"], results)
        self.assertTrue(results["resume"]["success"], results)
        self.assertEqual(
            [call["param1"] for call in control.command_calls],
            [0, 1],
        )

    def test_plane_resume_switches_directly_to_auto_without_native_command(self):
        control = RecordingFlightControl()
        control._set_mode_profile("plane")

        result = control.resume_mission()

        self.assertTrue(result["success"], result)
        self.assertEqual(control.command_calls, [])
        self.assertEqual(control.mode_calls, ["AUTO"])
        self.assertEqual(result["resume_strategy"], "auto_mode")
        self.assertEqual(result["target_mode"], "AUTO")
        self.assertFalse(result["native_resume"])

    def test_copter_resume_uses_native_command(self):
        control = RecordingFlightControl()
        control._set_mode_profile("copter")
        control._wait_for_command_ack = lambda *args, **kwargs: {
            "result": 0,
            "result_text": "接受",
            "timestamp": 2.0,
        }

        result = control.resume_mission()

        self.assertTrue(result["success"], result)
        self.assertEqual(control.command_calls[0]["command"], 193)
        self.assertEqual(control.command_calls[0]["param1"], 1)
        self.assertEqual(control.mode_calls, [])
        self.assertEqual(result["resume_strategy"], "native_command")
        self.assertTrue(result["native_resume"])
        self.assertIsNone(result["target_mode"])

    def test_copter_resume_falls_back_to_auto(self):
        control = RecordingFlightControl()
        control._set_mode_profile("copter")
        control._wait_for_command_ack = lambda *args, **kwargs: None

        result = control.resume_mission()

        self.assertTrue(result["success"], result)
        self.assertEqual(control.command_calls[0]["command"], 193)
        self.assertEqual(control.command_calls[0]["param1"], 1)
        self.assertEqual(control.mode_calls, ["AUTO"])
        self.assertEqual(result["resume_strategy"], "auto_mode")
        self.assertFalse(result["native_resume"])
        self.assertIn("未收到 ACK", result["fallback_reason"])


if __name__ == "__main__":
    unittest.main()
