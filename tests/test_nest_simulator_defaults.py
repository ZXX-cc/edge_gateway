"""Default state regression tests for the UDP nest simulator."""
from __future__ import annotations

import unittest

from src.nest_simulator import (
    BIT_ALLOW_TAKEOFF,
    BIT_DRONE_HEARTBEAT,
    BIT_NEST_HEARTBEAT,
    BIT_PLC_HEARTBEAT,
    BIT_R_AXIS_ZERO,
    BIT_READY,
    NestSimulator,
)
from src.generated_dialect.gcs_order_protocol import GCSCommand


class NestSimulatorDefaultStateTest(unittest.TestCase):
    def test_default_state_is_healthy_and_takeoff_ready(self):
        simulator = NestSimulator(command_log="")
        status1 = simulator.env_data["boolstate1"]
        status2 = simulator.env_data["boolstate2"]

        for bit in (BIT_NEST_HEARTBEAT, BIT_DRONE_HEARTBEAT, BIT_PLC_HEARTBEAT, BIT_READY, BIT_R_AXIS_ZERO):
            self.assertTrue(status1 & (1 << bit))
        self.assertTrue(status2 & (1 << BIT_ALLOW_TAKEOFF))

        ordinary, takeoff = simulator._evaluate_command_response(0, GCSCommand.TAKEOFF_START.value)
        self.assertEqual((ordinary, takeoff), (0, 0))

    def test_landing_abort_is_idempotent_when_not_landing(self):
        simulator = NestSimulator(command_log="")

        ordinary, takeoff = simulator._evaluate_command_response(0, 0x04)

        self.assertEqual((ordinary, takeoff), (0, 0))

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

        self.assertTrue(NestSimulator._is_gateway_standard_message(Message("COMMAND_LONG", 190)))
        self.assertTrue(NestSimulator._is_gateway_standard_message(Message("SET_MODE", 190)))
        self.assertFalse(NestSimulator._is_gateway_standard_message(Message("DRONE_NEST_COMMAND", 190)))
        self.assertFalse(NestSimulator._is_gateway_standard_message(Message("HEARTBEAT", 1)))


if __name__ == "__main__":
    unittest.main(verbosity=2)
