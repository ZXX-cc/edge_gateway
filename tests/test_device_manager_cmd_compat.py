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

    def is_mongo_session(_session):
        return False

    def mongo_expired_filter(*_args, **_kwargs):
        return {}

    def mongo_model_from_document(_model, document):
        return document

    connection.init_database_async = init_database_async
    connection.get_async_session_local = get_async_session_local
    connection.is_mongo_session = is_mongo_session
    connection.mongo_expired_filter = mongo_expired_filter
    connection.mongo_model_from_document = mongo_model_from_document
    sys.modules["src.database.connection"] = connection

if "src.database.device_db" not in sys.modules:
    device_db = ModuleType("src.database.device_db")
    device_db.DeviceDB = type("DeviceDB", (), {})
    sys.modules["src.database.device_db"] = device_db

if "src.database.alarm_db" not in sys.modules:
    alarm_db = ModuleType("src.database.alarm_db")
    alarm_db.AlarmDB = type("AlarmDB", (), {})
    sys.modules["src.database.alarm_db"] = alarm_db

if "src.database.operation_db" not in sys.modules:
    operation_db = ModuleType("src.database.operation_db")
    operation_db.OperationDB = type("OperationDB", (), {})
    sys.modules["src.database.operation_db"] = operation_db

from src.device.manager import DeviceManager  # noqa: E402
from src.utils.dji_error_codes import DOCK_COMMUNICATION_ERROR  # noqa: E402


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

    def assert_real_adapter_missing(self, reply):
        self.assertEqual(reply["data"]["result"], DOCK_COMMUNICATION_ERROR)
        self.assertEqual(reply["data"]["output"]["status"], "failed")

    def test_link_script_switch_payloads_validate_but_do_not_fake_state(self):
        battery = self.manager.handle_battery_store_mode_switch(
            msg("battery_store_mode_switch", {"action": 0})
        )
        self.assert_real_adapter_missing(battery)
        self.assertEqual(self.manager.get_property("battery_store_mode"), 1)

        sdr = self.manager.handle_sdr_workmode_switch(
            msg("sdr_workmode_switch", {"action": 1})
        )
        self.assert_real_adapter_missing(sdr)
        self.assertNotEqual(self.manager.get_property("wireless_link").get("link_workmode"), 1)

    def test_link_script_network_and_rtk_methods_do_not_fake_success(self):
        sim = self.manager.handle_sim_slot_switch(msg("sim_slot_switch", {"sim_slot": 1}))
        self.assert_real_adapter_missing(sim)
        self.assertNotEqual(self.manager.get_property("wireless_link").get("sim_slot"), 1)

        activated = self.manager.handle_esim_activate(msg("esim_activate", None))
        self.assert_real_adapter_missing(activated)

        operator = self.manager.handle_esim_operator_switch(
            msg("esim_operator_switch", {"operator": 2})
        )
        self.assert_real_adapter_missing(operator)
        self.assertNotEqual(self.manager.get_property("esim").get("operator"), 2)

        with patch.object(self.manager, "_schedule_async"):
            rtk = self.manager.handle_rtk_calibration(
                msg("rtk_calibration", {"devices": [{"sn": "EDGEGW001"}]})
            )
        self.assert_real_adapter_missing(rtk)

    def test_storage_and_reboot_methods_do_not_fake_success(self):
        for method, handler in [
            ("drone_format", self.manager.handle_drone_format),
            ("device_format", self.manager.handle_device_format),
            ("format_sd_card", self.manager.handle_format_sd_card),
            ("device_reboot", self.manager.handle_device_reboot),
        ]:
            with self.subTest(method=method):
                reply = handler(msg(method, {}))
                self.assert_real_adapter_missing(reply)


if __name__ == "__main__":
    unittest.main(verbosity=2)
