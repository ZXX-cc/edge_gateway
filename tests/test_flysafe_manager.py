import tempfile
import unittest

import src.modules.flysafe_manager as flysafe_module
from src.modules.flysafe_manager import FlySafeManager
from src.utils.dji_error_codes import (
    DEVICE_STATE_UNSUPPORTED,
    DOCK_SYSTEM_ERROR,
)


def base_msg(method: str, data: dict) -> dict:
    return {
        "bid": "bid-test",
        "tid": f"tid-{method}",
        "gateway": "GW-TEST",
        "timestamp": 1786000000000,
        "method": method,
        "data": data,
    }


class FlySafeManagerTest(unittest.TestCase):
    def make_manager(self, temp_dir: str):
        old_cache_dir = flysafe_module.LICENSE_CACHE_DIR
        flysafe_module.LICENSE_CACHE_DIR = temp_dir
        try:
            manager = FlySafeManager()
        finally:
            flysafe_module.LICENSE_CACHE_DIR = old_cache_dir
        manager._licenses = []
        manager._consistence = False
        events = []
        manager.set_event_callback(events.append)
        return manager, events

    def test_switch_missing_license_rejects_and_reports_standard_event(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            manager, events = self.make_manager(temp_dir)

            reply = manager.handle_unlock_license_switch(base_msg(
                "unlock_license_switch",
                {"license_id": 1001, "enabled": True},
            ))

            self.assertEqual(reply["data"]["result"], DEVICE_STATE_UNSUPPORTED)
            self.assertFalse(reply["data"]["found"])
            self.assertEqual(len(events), 1)
            event = events[0]
            self.assertEqual(event["method"], "unlock_license_switch_result")
            self.assertEqual(event["gateway"], manager.gateway_sn)
            self.assertIn("tid", event)
            self.assertIn("bid", event)
            self.assertIn("timestamp", event)
            self.assertEqual(event["data"]["reason"], "license_id_not_found")

    def test_switch_without_device_applier_rejects_and_keeps_cache_unchanged(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            manager, events = self.make_manager(temp_dir)
            manager._licenses = [{
                "common_fields": {
                    "license_id": 2001,
                    "enabled": False,
                    "license_type": 2,
                    "device_model_domain": 0,
                    "create_time": 1786000000000,
                    "update_time": 1786000000000,
                    "fingerprint": "fp",
                }
            }]

            reply = manager.handle_unlock_license_switch(base_msg(
                "unlock_license_switch",
                {"license_id": 2001, "enabled": True},
            ))

            self.assertEqual(reply["data"]["result"], DOCK_SYSTEM_ERROR)
            self.assertFalse(reply["data"]["applied"])
            self.assertFalse(manager._licenses[0]["common_fields"]["enabled"])
            self.assertEqual(events[-1]["data"]["reason"], "flysafe_device_applier_not_configured")

    def test_switch_with_device_applier_updates_cache_and_reports_event(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            manager, events = self.make_manager(temp_dir)
            manager._licenses = [{
                "common_fields": {
                    "license_id": 3001,
                    "enabled": False,
                    "license_type": 1,
                    "device_model_domain": 3,
                    "create_time": 1786000000000,
                    "update_time": 1786000000000,
                    "fingerprint": "fp",
                }
            }]
            manager.set_device_applier(lambda license_entry, enabled: {"success": True, "applied": True})

            reply = manager.handle_unlock_license_switch(base_msg(
                "unlock_license_switch",
                {"license_id": 3001, "enabled": True},
            ))

            self.assertEqual(reply["data"]["result"], 0)
            self.assertTrue(reply["data"]["applied"])
            self.assertTrue(manager._licenses[0]["common_fields"]["enabled"])
            self.assertEqual(events[-1]["data"]["status"], "ok")

    def test_update_records_license_metadata_and_standard_event(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            manager, events = self.make_manager(temp_dir)
            manager._download_license = lambda url, fingerprint: f"{temp_dir}/license.bin"

            reply = manager.handle_unlock_license_update(base_msg(
                "unlock_license_update",
                {
                    "device_model_domain": 3,
                    "license_id": 4001,
                    "license_type": 7,
                    "create_time": 1786000000123,
                    "file": {
                        "url": "https://example.invalid/license.bin",
                        "fingerprint": "abcd",
                    },
                },
            ))

            self.assertEqual(reply["data"]["result"], 0)
            common = manager._licenses[0]["common_fields"]
            self.assertEqual(common["license_id"], 4001)
            self.assertEqual(common["license_type"], 7)
            self.assertEqual(common["device_model_domain"], 3)
            self.assertEqual(common["create_time"], 1786000000123)
            self.assertFalse(common["enabled"])
            self.assertEqual(events[-1]["method"], "unlock_license_update_result")
            self.assertIn("gateway", events[-1])

    def test_list_filters_by_device_model_domain(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            manager, _ = self.make_manager(temp_dir)
            manager._licenses = [
                {"common_fields": {"license_id": 1, "device_model_domain": 0}},
                {"common_fields": {"license_id": 2, "device_model_domain": 3}},
            ]

            reply = manager.handle_unlock_license_list(base_msg(
                "unlock_license_list",
                {"device_model_domain": 3},
            ))

            licenses = reply["data"]["licenses"]
            self.assertEqual(len(licenses), 1)
            self.assertEqual(licenses[0]["common_fields"]["license_id"], 2)
            self.assertEqual(licenses[0]["common_fields"]["device_model_domain"], 3)


if __name__ == "__main__":
    unittest.main()
