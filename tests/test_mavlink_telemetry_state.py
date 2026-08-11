"""Dock3 state payload tests for MAVLink telemetry transitions."""
from __future__ import annotations

import sys
import time
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.mavlink.telemetry import TelemetryProcessor  # noqa: E402


class MavlinkTelemetryStateTest(unittest.TestCase):
    def test_osd_only_internal_fields_are_not_published_as_state(self):
        telemetry = TelemetryProcessor()

        self.assertEqual(
            telemetry.build_state_data({"flight_mode": "GUIDED"}),
            {"property": {}},
        )

    def test_official_state_property_container_is_preserved(self):
        telemetry = TelemetryProcessor()

        self.assertEqual(
            telemetry.build_state_data({"firmware_version": "1.2.3"}),
            {"property": {"firmware_version": "1.2.3"}},
        )

    def test_osd_rate_configures_two_second_interval(self):
        telemetry = TelemetryProcessor()

        telemetry.configure_osd_rate(0.5)

        self.assertEqual(telemetry._osd_interval, 2.0)

    def test_osd_generation_is_not_blocked_by_slow_callback(self):
        telemetry = TelemetryProcessor()
        telemetry.configure_osd_rate(20)
        build_times = []
        callback_times = []

        def build_osd_data():
            build_times.append(time.monotonic())
            return {"sequence": len(build_times)}

        def slow_callback(_osd_data):
            callback_times.append(time.monotonic())
            time.sleep(0.12)

        telemetry.build_osd_data = build_osd_data
        telemetry.set_callbacks(slow_callback)
        telemetry.start()
        try:
            time.sleep(0.32)
        finally:
            telemetry.stop()

        self.assertGreaterEqual(len(build_times), 4)
        self.assertGreaterEqual(len(callback_times), 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
