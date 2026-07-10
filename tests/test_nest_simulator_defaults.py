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


if __name__ == "__main__":
    unittest.main(verbosity=2)
