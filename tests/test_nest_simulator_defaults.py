"""Default state regression tests for the UDP nest simulator."""
from __future__ import annotations

import sys
import types
import unittest

if "pymavlink.dialects.v20.ardupilotmega" not in sys.modules:
    pymavlink = sys.modules.setdefault("pymavlink", types.ModuleType("pymavlink"))
    mavutil = sys.modules.setdefault("pymavlink.mavutil", types.ModuleType("pymavlink.mavutil"))
    dialects = sys.modules.setdefault("pymavlink.dialects", types.ModuleType("pymavlink.dialects"))
    v20 = sys.modules.setdefault("pymavlink.dialects.v20", types.ModuleType("pymavlink.dialects.v20"))
    ardupilotmega = types.ModuleType("pymavlink.dialects.v20.ardupilotmega")
    ardupilotmega.MAV_MODE_FLAG_SAFETY_ARMED = 128
    sys.modules["pymavlink.dialects.v20.ardupilotmega"] = ardupilotmega
    pymavlink.mavutil = mavutil
    pymavlink.dialects = dialects
    dialects.v20 = v20
    v20.ardupilotmega = ardupilotmega

from src.nest_simulator import (
    BIT_ALLOW_TAKEOFF,
    BIT_DRONE_HEARTBEAT,
    BIT_NEST_HEARTBEAT,
    BIT_PLC_HEARTBEAT,
    BIT_R_AXIS_ZERO,
    BIT_CALIBRATE,
    BIT_INIT,
    NestSimulator,
)
from src.generated_dialect.gcs_order_protocol import GCSCommand


class NestSimulatorDefaultStateTest(unittest.TestCase):
    def test_default_state_is_healthy_and_takeoff_ready(self):
        simulator = NestSimulator(command_log="")
        status1 = simulator.env_data["boolstate1"]
        status2 = simulator.env_data["boolstate2"]

        for bit in (
            BIT_NEST_HEARTBEAT,
            BIT_DRONE_HEARTBEAT,
            BIT_PLC_HEARTBEAT,
            BIT_CALIBRATE,
            BIT_INIT,
            BIT_R_AXIS_ZERO,
        ):
            self.assertTrue(status1 & (1 << bit))
        self.assertTrue(status2 & (1 << BIT_ALLOW_TAKEOFF))

        ordinary, takeoff = simulator._evaluate_command_response(0, GCSCommand.TAKEOFF_START.value)
        self.assertEqual((ordinary, takeoff), (0, 0))

    def test_v17_ignores_removed_boolstate1_bits_11_and_12(self):
        simulator = NestSimulator(command_log="", protocol_version="v1.7")
        simulator.env_data["boolstate1"] |= (1 << 11) | (1 << 12)

        ordinary, takeoff = simulator._evaluate_command_response(0, GCSCommand.TAKEOFF_START.value)

        self.assertEqual((ordinary, takeoff), (0, 0))

    def test_v17_state_is_healthy_when_explicitly_enabled(self):
        simulator = NestSimulator(command_log="", protocol_version="v1.7")
        status1 = simulator.env_data["boolstate1"]

        for bit in (BIT_CALIBRATE, BIT_INIT, BIT_R_AXIS_ZERO):
            self.assertTrue(status1 & (1 << bit))

        ordinary, takeoff = simulator._evaluate_command_response(0, GCSCommand.TAKEOFF_START.value)
        self.assertEqual((ordinary, takeoff), (0, 0))

    def test_landing_abort_rejects_when_not_landing(self):
        simulator = NestSimulator(command_log="")

        ordinary, takeoff = simulator._evaluate_command_response(0, 0x04)

        self.assertEqual(ordinary, 0)
        self.assertNotEqual(takeoff, 0)

    def test_zero_mavlink_fields_are_preserved(self):
        class Message:
            seq = 0
            type = 0

        self.assertEqual(NestSimulator._message_int_attr(Message(), "seq", -1), 0)
        self.assertEqual(NestSimulator._message_int_attr(Message(), "type", -1), 0)

    def test_gateway_link_filter_rejects_sitl_mirror_heartbeat(self):
        class Message:
            def __init__(self, message_type, component):
                self.message_type = message_type
                self.component = component

            def get_type(self):
                return self.message_type

            def get_srcComponent(self):
                return self.component

        self.assertTrue(NestSimulator._is_gateway_link_message(Message("HEARTBEAT", 190)))
        self.assertTrue(
            NestSimulator._is_gateway_link_message(Message("DRONE_NEST_COMMAND", 1))
        )
        self.assertFalse(NestSimulator._is_gateway_link_message(Message("HEARTBEAT", 1)))

    def test_gateway_standard_filter_forwards_aircraft_commands_only(self):
        class Message:
            def __init__(self, message_type, component):
                self.message_type = message_type
                self.component = component

            def get_type(self):
                return self.message_type

            def get_srcComponent(self):
                return self.component

        simulator = NestSimulator(command_log="")
        self.assertTrue(simulator._is_gateway_standard_message(Message("COMMAND_LONG", 190)))
        self.assertTrue(simulator._is_gateway_standard_message(Message("SET_MODE", 190)))
        self.assertFalse(simulator._is_gateway_standard_message(Message("DRONE_NEST_COMMAND", 190)))
        self.assertFalse(simulator._is_gateway_standard_message(Message("HEARTBEAT", 1)))


if __name__ == "__main__":
    unittest.main(verbosity=2)
