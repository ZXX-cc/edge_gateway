"""Dock3 state payload tests for MAVLink telemetry transitions."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.mavlink.telemetry import TelemetryProcessor  # noqa: E402


class MavlinkTelemetryStateTest(unittest.TestCase):
    def test_state_data_uses_dock3_property_container(self):
        telemetry = TelemetryProcessor()

        self.assertEqual(
            telemetry.build_state_data({"flight_mode": "GUIDED"}),
            {"property": {"flight_mode": "GUIDED"}},
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
