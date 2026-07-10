import threading
import time
import unittest
from types import SimpleNamespace

from pymavlink.mavutil import mavlink

from src.modules.flight_control import FlightControl


PREARM_CHECK = mavlink.MAV_SYS_STATUS_PREARM_CHECK
ARM_COMMAND = mavlink.MAV_CMD_COMPONENT_ARM_DISARM


def mavlink_message(msg_type, sysid=1, compid=1, **fields):
    """Build the small dispatcher envelope FlightControl.on_message consumes."""
    return SimpleNamespace(
        msg_type=msg_type,
        msg=SimpleNamespace(**fields),
        sysid=sysid,
        compid=compid,
    )


def emit_heartbeat(control, armed=False):
    control.on_message(
        mavlink_message(
            "HEARTBEAT",
            custom_mode=0,
            base_mode=mavlink.MAV_MODE_FLAG_SAFETY_ARMED if armed else 0,
            type=getattr(mavlink, "MAV_TYPE_FIXED_WING", 1),
            system_status=getattr(mavlink, "MAV_STATE_STANDBY", 3),
        )
    )


def emit_prearm_status(control, healthy):
    control.on_message(
        mavlink_message(
            "SYS_STATUS",
            onboard_control_sensors_present=PREARM_CHECK,
            onboard_control_sensors_enabled=PREARM_CHECK,
            onboard_control_sensors_health=PREARM_CHECK if healthy else 0,
        )
    )


def emit_ack(control, command, result):
    control.on_message(
        mavlink_message(
            "COMMAND_ACK",
            command=command,
            result=result,
        )
    )


class RecordingFlightControl(FlightControl):
    def __init__(self):
        super().__init__()
        self.command_calls = []
        self.on_command = None

    def _send_command_long(
        self,
        command,
        param1=0,
        param2=0,
        param3=0,
        param4=0,
        param5=0,
        param6=0,
        param7=0,
    ):
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
        if self.on_command:
            self.on_command(command)
        return True


class FlightControlArmabilityTest(unittest.TestCase):
    def setUp(self):
        self.control = RecordingFlightControl()

    def make_armable(self):
        emit_heartbeat(self.control)
        emit_prearm_status(self.control, healthy=True)

    def test_wait_until_armable_requires_fresh_heartbeat_and_healthy_prearm(self):
        emit_heartbeat(self.control)
        self.control._last_heartbeat_monotonic -= 2.0
        emit_prearm_status(self.control, healthy=True)
        stale_heartbeat = self.control.wait_until_armable(
            timeout=0.03,
            max_status_age=0.01,
        )
        self.assertFalse(stale_heartbeat["success"], stale_heartbeat)

        emit_heartbeat(self.control)
        emit_prearm_status(self.control, healthy=False)
        unhealthy = self.control.wait_until_armable(
            timeout=0.03,
            max_status_age=1.0,
        )
        self.assertFalse(unhealthy["success"], unhealthy)

        emit_prearm_status(self.control, healthy=True)
        ready = self.control.wait_until_armable(
            timeout=0.2,
            max_status_age=1.0,
        )
        self.assertTrue(ready["success"], ready)

    def test_arm_and_wait_does_not_send_command_when_prearm_is_unhealthy(self):
        emit_heartbeat(self.control)
        emit_prearm_status(self.control, healthy=False)

        result = self.control.arm_and_wait(timeout=1.0, readiness_timeout=0.04)

        self.assertFalse(result["success"], result)
        self.assertEqual(self.control.command_calls, [])

    def test_arm_and_wait_fails_immediately_on_failed_ack(self):
        self.make_armable()

        def fail_arm(command):
            emit_ack(self.control, command, mavlink.MAV_RESULT_FAILED)

        self.control.on_command = fail_arm
        started = time.monotonic()

        result = self.control.arm_and_wait(timeout=1.0, readiness_timeout=0.2)

        elapsed = time.monotonic() - started
        self.assertFalse(result["success"], result)
        self.assertEqual(
            [call["command"] for call in self.control.command_calls],
            [ARM_COMMAND],
        )
        self.assertLess(elapsed, 0.3, result)
        message = str(result.get("message", "")).lower()
        self.assertNotIn("超时", message)
        self.assertNotIn("timeout", message)

    def test_arm_and_wait_succeeds_after_matching_ack_and_armed_heartbeat(self):
        self.make_armable()
        timers = []

        def send_flight_controller_responses(command):
            # An ACK for another command must not satisfy the arm request.
            emit_ack(
                self.control,
                getattr(mavlink, "MAV_CMD_NAV_TAKEOFF", 22),
                mavlink.MAV_RESULT_FAILED,
            )

            matching_ack = threading.Timer(
                0.03,
                emit_ack,
                args=(self.control, command, mavlink.MAV_RESULT_ACCEPTED),
            )

            def confirm_armed():
                # FlightControl deliberately debounces the armed flag.
                for _ in range(4):
                    emit_heartbeat(self.control, armed=True)

            armed_heartbeat = threading.Timer(0.06, confirm_armed)
            for timer in (matching_ack, armed_heartbeat):
                timer.daemon = True
                timer.start()
                timers.append(timer)

        self.control.on_command = send_flight_controller_responses
        try:
            result = self.control.arm_and_wait(timeout=1.0, readiness_timeout=0.2)
        finally:
            for timer in timers:
                timer.join(timeout=1.0)

        self.assertTrue(result["success"], result)
        self.assertTrue(self.control.is_armed_status())
        self.assertEqual(
            [call["command"] for call in self.control.command_calls],
            [ARM_COMMAND],
        )

    def test_cached_armed_state_does_not_bypass_freshness_or_system_health(self):
        self.control.is_armed = True

        stale = self.control.arm_and_wait(timeout=1.0, readiness_timeout=0.0)
        self.assertFalse(stale["success"], stale)
        self.assertEqual(self.control.command_calls, [])

        emit_heartbeat(self.control, armed=True)
        emit_prearm_status(self.control, healthy=False)
        unhealthy = self.control.arm_and_wait(timeout=1.0, readiness_timeout=0.0)
        self.assertFalse(unhealthy["success"], unhealthy)

        self.control.on_message(
            mavlink_message(
                "HEARTBEAT",
                custom_mode=0,
                base_mode=mavlink.MAV_MODE_FLAG_SAFETY_ARMED,
                type=getattr(mavlink, "MAV_TYPE_FIXED_WING", 1),
                system_status=getattr(mavlink, "MAV_STATE_CRITICAL", 5),
            )
        )
        emit_prearm_status(self.control, healthy=True)
        critical = self.control.arm_and_wait(timeout=1.0, readiness_timeout=0.0)
        self.assertFalse(critical["success"], critical)
        self.assertEqual(self.control.command_calls, [])

    def test_invalid_arm_timeout_never_sends_command(self):
        self.make_armable()

        for invalid in (0, -1, float("nan"), float("inf"), 31):
            with self.subTest(timeout=invalid):
                result = self.control.arm_and_wait(
                    timeout=invalid,
                    readiness_timeout=0.0,
                )
                self.assertFalse(result["success"], result)

        self.assertEqual(self.control.command_calls, [])

    def test_armability_ignores_non_autopilot_component(self):
        self.control.on_message(
            mavlink_message(
                "HEARTBEAT",
                compid=100,
                custom_mode=0,
                base_mode=0,
                type=getattr(mavlink, "MAV_TYPE_CAMERA", 30),
                system_status=getattr(mavlink, "MAV_STATE_ACTIVE", 4),
            )
        )
        self.control.on_message(
            mavlink_message(
                "SYS_STATUS",
                compid=100,
                onboard_control_sensors_present=PREARM_CHECK,
                onboard_control_sensors_enabled=PREARM_CHECK,
                onboard_control_sensors_health=PREARM_CHECK,
            )
        )

        result = self.control.wait_until_armable(timeout=0.0)

        self.assertFalse(result["success"], result)


if __name__ == "__main__":
    unittest.main()
