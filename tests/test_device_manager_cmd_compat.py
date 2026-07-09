#!/usr/bin/env python3
"""DeviceManager command compatibility tests for external MQTT link scripts."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import ModuleType
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

if "src.database.connection" not in sys.modules:
    connection = ModuleType("src.database.connection")

    async def init_database_async():
        return None

    def get_async_session_local():
        return None

    connection.init_database_async = init_database_async
    connection.get_async_session_local = get_async_session_local
    sys.modules["src.database.connection"] = connection

if "src.database.device_db" not in sys.modules:
    device_db = ModuleType("src.database.device_db")
    device_db.DeviceDB = type("DeviceDB", (), {})
    sys.modules["src.database.device_db"] = device_db

if "src.database.alarm_db" not in sys.modules:
    alarm_db = ModuleType("src.database.alarm_db")
    alarm_db.AlarmDB = type("AlarmDB", (), {})
    sys.modules["src.database.alarm_db"] = alarm_db

from src.device.manager import DeviceManager  # noqa: E402


def msg(method: str, data: dict) -> dict:
    return {
        "tid": f"tid-{method}",
        "bid": f"bid-{method}",
        "gateway": "EDGEGW001",
        "method": method,
        "data": data,
    }


class DeviceManagerCommandCompatTest(unittest.TestCase):
    def setUp(self):
        self.manager = DeviceManager()
        self.manager._event_callback = lambda event: None
        self.manager._state_callback = lambda state: None

    def test_link_script_switch_payloads_return_status_and_state(self):
        battery = self.manager.handle_battery_store_mode_switch(
            msg("battery_store_mode_switch", {"action": 0})
        )
        self.assertEqual(battery["data"]["result"], 0)
        self.assertEqual(battery["data"]["output"]["status"], "ok")
        self.assertEqual(battery["data"]["action"], 0)
        self.assertEqual(battery["data"]["battery_store_mode"], 1)
        self.assertEqual(battery["data"]["normalized_action"], 1)
        self.assertEqual(self.manager.get_property("battery_store_mode"), 1)

        sdr = self.manager.handle_sdr_workmode_switch(
            msg("sdr_workmode_switch", {"action": 1})
        )
        self.assertEqual(sdr["data"]["result"], 0)
        self.assertEqual(sdr["data"]["output"]["status"], "ok")
        self.assertEqual(sdr["data"]["action"], 1)
        self.assertEqual(sdr["data"]["link_workmode"], 1)
        self.assertEqual(self.manager.get_property("wireless_link")["link_workmode"], 1)

    def test_link_script_network_and_rtk_methods_return_action_info(self):
        sim = self.manager.handle_sim_slot_switch(msg("sim_slot_switch", {"sim_slot": 1}))
        self.assertEqual(sim["data"]["result"], 0)
        self.assertEqual(sim["data"]["output"]["status"], "ok")
        self.assertEqual(sim["data"]["sim_slot"], 1)
        self.assertEqual(self.manager.get_property("wireless_link")["sim_slot"], 1)

        activated = self.manager.handle_esim_activate(msg("esim_activate", None))
        self.assertEqual(activated["data"]["result"], 0)
        self.assertEqual(activated["data"]["output"]["status"], "ok")
        self.assertEqual(activated["data"]["activated"], 1)

        operator = self.manager.handle_esim_operator_switch(
            msg("esim_operator_switch", {"operator": 2})
        )
        self.assertEqual(operator["data"]["result"], 0)
        self.assertEqual(operator["data"]["operator"], 2)
        self.assertEqual(self.manager.get_property("esim")["operator"], 2)

        with patch.object(self.manager, "_schedule_async"):
            rtk = self.manager.handle_rtk_calibration(
                msg("rtk_calibration", {"devices": [{"sn": "EDGEGW001"}]})
            )
        self.assertEqual(rtk["data"]["result"], 0)
        self.assertEqual(rtk["data"]["output"]["status"], "ok")
        self.assertEqual(rtk["data"]["calibration_status"], "started")
        self.assertEqual(rtk["data"]["devices_count"], 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
