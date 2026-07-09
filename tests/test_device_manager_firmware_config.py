#!/usr/bin/env python3
"""DeviceManager firmware/config Dock3 contract tests."""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from types import ModuleType


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

from src.cloud_api.dock3_cmd import (  # noqa: E402
    TASK_STATUS_CANCELED,
    TASK_STATUS_IN_PROGRESS,
    TASK_STATUS_OK,
    TASK_STATUS_REJECTED,
)
from src.device.manager import DeviceManager  # noqa: E402
from src.utils.dji_error_codes import INVALID_COMMAND_FORMAT  # noqa: E402


def msg(method: str, data: dict | None) -> dict:
    return {
        "tid": f"tid-{method}",
        "bid": f"bid-{method}",
        "gateway": "EDGEGW001",
        "method": method,
        "data": data or {},
    }


class DeviceManagerFirmwareConfigTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.events = []
        self.states = []
        self.manager = DeviceManager()
        self.manager._config_data_path = str(Path(self.tmp.name) / "device_config.json")
        self.manager._config_store = {}
        self.manager._config_revision = 0
        self.manager._config_history = []
        self.manager._event_callback = self.events.append
        self.manager._state_callback = self.states.append

    def tearDown(self):
        self.tmp.cleanup()

    def test_ota_simulation_can_be_created_and_cancelled(self):
        reply = self.manager.handle_ota_create(msg("ota_create", {
            "simulate": True,
            "auto_complete": False,
            "devices": [{
                "sn": "EDGEGW001",
                "product_version": "9.9.9-test",
                "file_url": "mock://firmware.bin",
                "file_size": 1024,
                "md5": "mock-md5",
                "file_name": "firmware.bin",
                "firmware_upgrade_type": 3,
            }],
        }))

        self.assertEqual(reply["data"]["result"], 0)
        self.assertEqual(reply["data"]["output"]["status"], TASK_STATUS_IN_PROGRESS)
        self.assertEqual(reply["data"]["target_version"], "9.9.9-test")
        self.assertTrue(any(event.get("method") == "ota_progress" for event in self.events))

        cancel = self.manager.handle_ota_cancel(msg("ota_cancel", {}))
        self.assertEqual(cancel["data"]["result"], 0)
        self.assertEqual(cancel["data"]["output"]["status"], TASK_STATUS_CANCELED)
        self.assertTrue(any(
            event.get("method") == "ota_result"
            and event.get("data", {}).get("output", {}).get("status") == TASK_STATUS_CANCELED
            for event in self.events
        ))

    def test_ota_rejects_invalid_payload(self):
        reply = self.manager.handle_ota_create(msg("ota_create", {}))

        self.assertEqual(reply["data"]["result"], INVALID_COMMAND_FORMAT)
        self.assertEqual(reply["data"]["output"]["status"], TASK_STATUS_REJECTED)

    def test_config_update_get_and_rollback(self):
        update = self.manager.handle_config_update(msg("config_update", {
            "config": {
                "dock3_test_config": "enabled",
                "silent_mode": 1,
            }
        }))

        self.assertEqual(update["data"]["result"], 0)
        self.assertEqual(update["data"]["output"]["status"], TASK_STATUS_OK)
        self.assertEqual(update["data"]["config_revision"], 1)
        self.assertEqual(self.manager.get_property("silent_mode"), 1)
        self.assertTrue(any(event.get("method") == "config_update_result" for event in self.events))

        fetched = self.manager.handle_config_get(msg("config_get", {}))
        self.assertEqual(fetched["data"]["result"], 0)
        self.assertEqual(fetched["data"]["output"]["status"], TASK_STATUS_OK)
        self.assertEqual(fetched["data"]["config"]["dock3_test_config"], "enabled")
        self.assertEqual(fetched["data"]["config"]["silent_mode"], 1)

        rollback = self.manager.handle_config_rollback(msg("config_rollback", {}))
        self.assertEqual(rollback["data"]["result"], 0)
        self.assertEqual(rollback["data"]["output"]["status"], TASK_STATUS_OK)
        self.assertEqual(rollback["data"]["rolled_back_to_revision"], 0)
        self.assertEqual(self.manager.get_property("silent_mode"), 0)
        self.assertEqual(self.manager.handle_config_get(msg("config_get", {}))["data"]["config"], {})
        self.assertTrue(any(event.get("method") == "config_rollback_result" for event in self.events))

    def test_config_rejects_invalid_and_readonly_payloads(self):
        invalid = self.manager.handle_config_update(msg("config_update", {}))
        self.assertEqual(invalid["data"]["result"], INVALID_COMMAND_FORMAT)
        self.assertEqual(invalid["data"]["output"]["status"], TASK_STATUS_REJECTED)

        readonly = self.manager.handle_config_update(msg("config_update", {
            "config": {"firmware_version": "2.0.0"}
        }))
        self.assertEqual(readonly["data"]["result"], INVALID_COMMAND_FORMAT)
        self.assertEqual(readonly["data"]["output"]["status"], "failed")
        self.assertEqual(readonly["data"]["failed_keys"], ["firmware_version"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
