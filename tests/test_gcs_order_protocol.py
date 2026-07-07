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
    def test_takeoff_land_commands_include_landing_start(self):
        codec = GCSOrderCodec()

        cases = {
            GCSCommand.TAKEOFF_START: 0x01,
            GCSCommand.TAKEOFF_ABORT: 0x02,
            GCSCommand.LANDING_START: 0x03,
            GCSCommand.LANDING_ABORT: 0x04,
        }
        for command, expected_value in cases.items():
            encoded = codec.encode_gcs_command(command)
            self.assertEqual(encoded & 0xFF, expected_value)

        self.assertEqual(NEST_COMMANDS["landing-start"], (0, 0x03))
        self.assertEqual(TAKEOFF_LAND_COMMAND_LABELS[0x03], "降落流程开始")


if __name__ == "__main__":
    unittest.main(verbosity=2)
