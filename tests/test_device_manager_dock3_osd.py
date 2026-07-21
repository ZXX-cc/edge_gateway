import unittest

from src.cloud_api.dock3_properties import official_property_names
from src.device.manager import DeviceManager


class DeviceManagerDock3OsdTest(unittest.TestCase):
    def setUp(self):
        self.manager = DeviceManager.__new__(DeviceManager)
        self.manager._properties = {
            name: f"marker:{name}" for name in official_property_names()
        }
        self.manager._properties["putter_state"] = 1
        self.manager._properties["electricity"] = 80

    def test_default_osd_contains_only_raw_push_mode_zero_properties(self):
        osd = self.manager.build_dock3_dock_osd()

        self.assertEqual(set(osd), official_property_names(push_mode=0))
        self.assertEqual(osd["mode_code"], "marker:mode_code")
        self.assertNotIn("putter_state", osd)
        self.assertNotIn("electricity", osd)
        self.assertTrue(official_property_names(push_mode=1).isdisjoint(osd))

    def test_legacy_value_wrapper_requires_explicit_option(self):
        osd = self.manager.build_dock3_dock_osd(wrap_value=True)

        self.assertEqual(osd["mode_code"], {"value": "marker:mode_code"})

    def test_payload_osd_extension_is_explicit(self):
        self.manager.gateway_sn = "EDGEGW001"
        self.manager._payload_state_provider = lambda: {
            "gimbal": {
                "payload_index": "52-0-0",
                "gimbal": {"pitch": -12.04, "roll": 3.02, "yaw": 190},
            }
        }

        osd = self.manager.build_dock3_dock_osd(include_payload=True)

        payload = osd["type_subtype_gimbalindex"]
        self.assertEqual(payload["payload_index"], "52-0-0")
        self.assertEqual(payload["gimbal_pitch"], -12.0)
        self.assertEqual(payload["gimbal_roll"], 3.0)
        self.assertEqual(payload["gimbal_yaw"], -170.0)
        self.assertEqual(payload["measure_target_error_state"], 3)


if __name__ == "__main__":
    unittest.main()
