import unittest
from copy import deepcopy

from src.cloud_api.dock3_properties import official_property_names
from src.device.manager import DeviceManager
from src.utils.config import Config


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
        self.assertEqual(osd["putter_state"], 1)
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

    def test_osd_uses_configured_nest_takeoff_point_as_position_defaults(self):
        original_config = deepcopy(Config._data)
        try:
            Config._data = {
                "mission": {
                    "wayline": {
                        "nest_takeoff_point": {
                            "latitude": 43.9759961,
                            "longitude": 125.3979932,
                            "altitude": 0.0,
                            "absolute_altitude": 194.92,
                        }
                    }
                }
            }
            manager = DeviceManager.__new__(DeviceManager)
            manager._properties = {name: 0 for name in official_property_names()}
            manager._properties["self_converge_coordinate"] = {
                "longitude": 0.0,
                "latitude": 0.0,
                "height": 0.0,
            }

            osd = manager.build_dock3_dock_osd()

            self.assertEqual(osd["latitude"], 43.9759961)
            self.assertEqual(osd["longitude"], 125.3979932)
            self.assertEqual(osd["height"], 194.92)
            self.assertEqual(osd["home_position_is_valid"], 3)
            self.assertEqual(
                osd["self_converge_coordinate"],
                {
                    "longitude": 125.3979932,
                    "latitude": 43.9759961,
                    "height": 194.92,
                },
            )
        finally:
            Config._data = original_config

    def test_configured_position_defaults_do_not_override_real_nest_position(self):
        original_config = deepcopy(Config._data)
        try:
            Config._data = {
                "mission": {
                    "wayline": {
                        "nest_takeoff_point": {
                            "latitude": 43.9759961,
                            "longitude": 125.3979932,
                            "absolute_altitude": 194.92,
                        }
                    }
                }
            }
            manager = DeviceManager.__new__(DeviceManager)
            manager._properties = {name: 0 for name in official_property_names()}
            manager._properties.update(
                {
                    "latitude": 43.817,
                    "longitude": 125.3235,
                    "height": 250.5,
                    "home_position_is_valid": 1,
                    "self_converge_coordinate": {
                        "longitude": 125.3235,
                        "latitude": 43.817,
                        "height": 250.5,
                    },
                }
            )

            osd = manager.build_dock3_dock_osd()

            self.assertEqual(osd["latitude"], 43.817)
            self.assertEqual(osd["longitude"], 125.3235)
            self.assertEqual(osd["height"], 250.5)
            self.assertEqual(osd["home_position_is_valid"], 1)
        finally:
            Config._data = original_config


if __name__ == "__main__":
    unittest.main()
