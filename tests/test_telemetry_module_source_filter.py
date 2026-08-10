"""TelemetryModule source filtering tests."""
from __future__ import annotations

import sys
import time
import unittest
from pathlib import Path
from types import ModuleType


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

if "yaml" not in sys.modules:
    yaml = ModuleType("yaml")
    yaml.safe_load = lambda *_args, **_kwargs: {}
    sys.modules["yaml"] = yaml

from src.core.message_cache import MAVLinkMessage  # noqa: E402
from src.modules.telemetry_module import TelemetryModule  # noqa: E402


class FakeMavMsg:
    def __init__(self, msg_type: str, **fields):
        self._msg_type = msg_type
        for name, value in fields.items():
            setattr(self, name, value)

    def get_type(self) -> str:
        return self._msg_type


def mav_msg(msg_type: str, *, sysid: int, compid: int = 1, **fields) -> MAVLinkMessage:
    return MAVLinkMessage(FakeMavMsg(msg_type, **fields), time.time(), sysid, compid)


class TelemetryModuleSourceFilterTest(unittest.TestCase):
    def test_non_aircraft_sysid_does_not_overwrite_aircraft_osd_values(self):
        telemetry = TelemetryModule(mode="real", aircraft_system_id=1)

        telemetry.on_message(
            mav_msg(
                "GPS_RAW_INT",
                sysid=1,
                fix_type=3,
                satellites_visible=18,
                eph=80,
            )
        )
        telemetry.on_message(
            mav_msg(
                "BATTERY_STATUS",
                sysid=1,
                voltages=[12580, 12580],
                current_battery=10,
                battery_remaining=99,
            )
        )

        telemetry.on_message(
            mav_msg(
                "GPS_RAW_INT",
                sysid=101,
                fix_type=0,
                satellites_visible=0,
                eph=0,
            )
        )
        telemetry.on_message(
            mav_msg(
                "BATTERY_STATUS",
                sysid=101,
                voltages=[0, 65535],
                current_battery=0,
                battery_remaining=0,
            )
        )

        data = telemetry.get_telemetry_data()
        self.assertEqual(data["gps_fixed"], 1)
        self.assertEqual(data["satelliteNumber"], 18)
        self.assertEqual(data["gps_signal_level"], 4)
        self.assertEqual(data["batteryRemaining"], 99)
        self.assertEqual(data["voltage"], 25.16)

    def test_filter_can_be_disabled_for_single_source_test_links(self):
        telemetry = TelemetryModule(mode="real", aircraft_system_id=0)

        telemetry.on_message(mav_msg("GPS_RAW_INT", sysid=101, fix_type=3, satellites_visible=12))

        data = telemetry.get_telemetry_data()
        self.assertEqual(data["gps_fixed"], 1)
        self.assertEqual(data["satelliteNumber"], 12)


if __name__ == "__main__":
    unittest.main(verbosity=2)
