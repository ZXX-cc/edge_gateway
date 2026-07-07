#!/usr/bin/env python3
"""机巢起降命令编码映射测试。"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.generated_dialect.gcs_order_protocol import GCSCommand, GCSOrderCodec  # noqa: E402
from web.mavlink.nest.protocol import NEST_COMMANDS, TAKEOFF_LAND_COMMAND_LABELS  # noqa: E402


class GCSOrderProtocolTest(unittest.TestCase):
    def test_takeoff_land_commands_exclude_unsupported_landing_start(self):
        codec = GCSOrderCodec()

        cases = {
            GCSCommand.TAKEOFF_START: 0x01,
            GCSCommand.TAKEOFF_ABORT: 0x02,
            GCSCommand.LANDING_ABORT: 0x04,
        }
        for command, expected_value in cases.items():
            encoded = codec.encode_gcs_command(command)
            self.assertEqual(encoded & 0xFF, expected_value)

        self.assertFalse(hasattr(GCSCommand, "LANDING_START"))
        self.assertNotIn("landing-start", NEST_COMMANDS)
        self.assertNotIn(0x03, TAKEOFF_LAND_COMMAND_LABELS)

    def test_deprecated_calibrate_r_is_not_exposed(self):
        self.assertFalse(hasattr(GCSCommand, "CALIBRATE_R"))
        self.assertNotIn("calibrate-r", NEST_COMMANDS)

    def test_v13_ordinary_commands_are_exposed(self):
        codec = GCSOrderCodec()

        cases = {
            GCSCommand.LIGHT_ON: ("light-on", 0x0A06),
            GCSCommand.LIGHT_OFF: ("light-off", 0x0A07),
            GCSCommand.DRONE_POWER_ON: ("drone-power-on", 0x0B01),
            GCSCommand.DRONE_POWER_OFF: ("drone-power-off", 0x0B02),
        }
        for command, (name, expected_value) in cases.items():
            encoded = codec.encode_gcs_command(command)
            self.assertEqual((encoded >> 8) & 0xFFFF, expected_value)
            self.assertEqual(encoded & 0xFF, 0)
            self.assertEqual(NEST_COMMANDS[name], (expected_value, 0))


if __name__ == "__main__":
    unittest.main(verbosity=2)
