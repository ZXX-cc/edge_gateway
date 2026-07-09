#!/usr/bin/env python3
"""DeviceManager nest state/HMS synchronization tests."""
from __future__ import annotations

import sys
import sqlite3
import unittest
from pathlib import Path
from types import ModuleType
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

if "aiosqlite" not in sys.modules:
    aiosqlite = ModuleType("aiosqlite")
    aiosqlite.connect = lambda *args, **kwargs: None
    for name in (
        "DatabaseError",
        "Error",
        "IntegrityError",
        "NotSupportedError",
        "OperationalError",
        "ProgrammingError",
        "sqlite_version",
        "sqlite_version_info",
    ):
        setattr(aiosqlite, name, getattr(sqlite3, name))
    sys.modules["aiosqlite"] = aiosqlite

from src.device.manager import DeviceManager  # noqa: E402
from src.device.nest_hms_mapper import build_nest_hms_alarms, is_nest_hms_alarm  # noqa: E402


def nest_snapshot(**overrides):
    data = {
        "data_valid": True,
        "ac_temp": 25.0,
        "nest_temperature": 26.0,
        "env_temperature": 24.0,
        "wind_speed": 3.0,
        "rainfall": 0.0,
        "charge_volt": 26.0,
        "charge_current": 0.5,
        "error_code": 0,
        "nest_heartbeat": True,
        "drone_heartbeat": True,
        "plc_heartbeat": True,
        "estop_state": False,
        "charging_state": True,
        "ready_state": True,
        "system_busy": False,
        "fault_state": False,
        "r_axis_zero": True,
        "takeoff_in_progress": False,
        "landing_in_progress": False,
        "emergency_landing": False,
        "takeoff_allowed": True,
        "clamp_state": 2,
        "gate_state": 1,
        "landing_light_state": 1,
    }
    data.update(overrides)
    return data


class DeviceManagerNestSyncTest(unittest.TestCase):
    def setUp(self):
        self.manager = DeviceManager()
        self.state_events = []
        self.events = []
        self.manager._state_callback = self.state_events.append
        self.manager._event_callback = self.events.append

    def test_nest_status_change_reports_state_hms_and_operation(self):
        with (
            patch.object(self.manager, "_save_alarm_to_db", new=lambda *args, **kwargs: None),
            patch.object(self.manager, "_clear_alarms_from_db", new=lambda *args, **kwargs: None),
            patch.object(self.manager, "_schedule_async"),
        ):
            self.manager.update_from_nest(nest_snapshot())

            self.assertTrue(self.state_events)
            first_state = self.state_events[-1]["property"]
            self.assertEqual(first_state["cover_state"], 1)
            self.assertEqual(first_state["charging_state"], 1)
            self.assertEqual(first_state["drone_charge_state"]["state"], 1)
            self.assertFalse([event for event in self.events if event["method"] == "hms"])

            self.manager.update_from_nest(nest_snapshot(
                estop_state=True,
                fault_state=True,
                error_code=12004,
                emergency_landing=True,
                nest_heartbeat=False,
            ))

            hms_events = [event for event in self.events if event["method"] == "hms"]
            self.assertTrue(hms_events)
            hms_alarms = hms_events[-1]["data"]["alarms"]
            hms_keys = {alarm["hms_key"] for alarm in hms_alarms}
            self.assertIn("dock3.nest.estop.active", hms_keys)
            self.assertIn("dock3.nest.fault.active", hms_keys)
            self.assertIn("dock3.nest.emergency_landing.active", hms_keys)
            self.assertIn("dock3.nest.error_code.active", hms_keys)
            self.assertIn("dock3.nest.nest_heartbeat.lost", hms_keys)

            operation_events = [
                event for event in self.events
                if event["method"] == "dock_operation"
            ]
            self.assertTrue(operation_events)
            operation = operation_events[-1]["data"]
            self.assertEqual(operation["command"], "nest_status_sync")
            self.assertTrue(operation["nest_status"]["estop_state"])
            self.assertTrue(operation["nest_status"]["fault_state"])

            self.manager.update_from_nest(nest_snapshot())
            cleared_hms = [event for event in self.events if event["method"] == "hms"][-1]
            self.assertEqual(cleared_hms["data"]["total"], 0)
            self.assertFalse([alarm for alarm in self.manager._hms_alarms if is_nest_hms_alarm(alarm)])

    def test_build_nest_hms_alarms_uses_stable_ids(self):
        alarms = build_nest_hms_alarms(
            nest_snapshot(fault_state=True, error_code=7, plc_heartbeat=False),
            "EDGEGW001",
        )
        by_key = {alarm["hms_key"]: alarm for alarm in alarms}
        self.assertEqual(by_key["dock3.nest.fault.active"]["alarm_id"], "nest-fault-active-7")
        self.assertEqual(by_key["dock3.nest.error_code.active"]["alarm_id"], "nest-error-code-7")
        self.assertEqual(by_key["dock3.nest.plc_heartbeat.lost"]["alarm_id"], "nest-plc-heartbeat-lost")
        self.assertTrue(all(is_nest_hms_alarm(alarm) for alarm in alarms))


if __name__ == "__main__":
    unittest.main(verbosity=2)
