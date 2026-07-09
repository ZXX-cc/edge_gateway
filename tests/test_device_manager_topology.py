#!/usr/bin/env python3
"""DeviceManager topology lifecycle tests."""
from __future__ import annotations

import sys
import tempfile
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

from src.cloud_api.dock3_device import build_sub_device_payload, build_update_topo_payload  # noqa: E402
from src.device.manager import DeviceManager, SubDevice  # noqa: E402


def msg(method: str, data: dict) -> dict:
    return {
        "tid": f"tid-{method}",
        "bid": f"bid-{method}",
        "gateway": "EDGEGW001",
        "method": method,
        "data": data,
    }


class DeviceManagerTopologyTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.events = []
        self.states = []
        self.manager = DeviceManager()
        self.manager._org_data_path = str(Path(self.tmp.name) / "organization.json")
        self.manager._event_callback = self.events.append
        self.manager._state_callback = self.states.append

    def tearDown(self):
        self.tmp.cleanup()

    def update_topo_events(self):
        return [event for event in self.events if event.get("method") == "update_topo"]

    def test_empty_topology_marks_known_sub_devices_offline_without_dropping_cache(self):
        online_payload = build_update_topo_payload(
            device_secret="gw-secret",
            nonce="gw-nonce",
            sub_devices=[
                build_sub_device_payload("DRONE-A", device_type=60, index="A"),
                build_sub_device_payload("DRONE-B", device_type=60, index="B"),
            ],
        )

        reply = self.manager.handle_update_topo(msg("update_topo", online_payload))

        self.assertEqual(reply["data"]["result"], 0)
        devices = {device.sn: device for device in self.manager.get_all_sub_devices()}
        self.assertEqual(set(devices), {"DRONE-A", "DRONE-B"})
        self.assertTrue(all(device.online for device in devices.values()))
        self.assertEqual(len(self.update_topo_events()[-1]["data"]["sub_devices"]), 2)

        offline_payload = build_update_topo_payload(
            device_secret="gw-secret",
            nonce="gw-nonce",
            sub_devices=[],
        )
        reply = self.manager.handle_update_topo(msg("update_topo", offline_payload))

        self.assertEqual(reply["data"]["result"], 0)
        devices = {device.sn: device for device in self.manager.get_all_sub_devices()}
        self.assertEqual(set(devices), {"DRONE-A", "DRONE-B"})
        self.assertFalse(any(device.online for device in devices.values()))
        self.assertEqual(self.update_topo_events()[-1]["data"]["sub_devices"], [])
        sub_device_state = self.states[-1]["property"]["sub_device"]
        self.assertIn(sub_device_state["device_sn"], {"DRONE-A", "DRONE-B"})
        self.assertEqual(sub_device_state["device_online_status"], 0)

    def test_reconnect_preserves_existing_pairing_state(self):
        self.manager.add_sub_device(SubDevice("DRONE-A", device_type=60, index="A"))
        self.manager._sub_devices["DRONE-A"].paired = True

        offline_payload = build_update_topo_payload(sub_devices=[])
        self.manager.handle_update_topo(msg("update_topo", offline_payload))
        self.assertFalse(self.manager.get_sub_device("DRONE-A").online)

        reconnect_payload = build_update_topo_payload(
            sub_devices=[
                build_sub_device_payload("DRONE-A", device_type=60, index="A"),
            ],
        )
        self.manager.handle_update_topo(msg("update_topo", reconnect_payload))

        device = self.manager.get_sub_device("DRONE-A")
        self.assertTrue(device.online)
        self.assertTrue(device.paired)
        self.assertEqual(self.update_topo_events()[-1]["data"]["sub_devices"][0]["sn"], "DRONE-A")
        self.assertEqual(self.manager.get_property("sub_device")["device_paired"], 1)

    def test_unbind_clears_sub_devices_and_reports_empty_topology(self):
        sub_device = SubDevice("DRONE-A", device_type=60, index="A")
        sub_device.online = True
        sub_device.paired = True
        self.manager.add_sub_device(sub_device)

        with patch.object(
            self.manager,
            "_schedule_async",
            new=lambda coro: coro.close() if hasattr(coro, "close") else None,
        ):
            reply = self.manager.handle_device_unbinding(msg("device_unbinding", {}))

        self.assertEqual(reply["data"]["result"], 0)
        self.assertEqual(self.manager.get_all_sub_devices(), [])
        self.assertEqual(self.manager.get_property("sub_device")["device_sn"], "")
        self.assertEqual(self.update_topo_events()[-1]["data"]["sub_devices"], [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
