import os
import sys
import unittest


PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from src.cloud_api.dock3_properties import (
    PROPERTY_SET_FAILED,
    PROPERTY_SET_SUCCESS,
    get_property_spec,
    validate_property_set,
    writable_property_names,
)


class Dock3PropertiesTest(unittest.TestCase):
    def test_writable_properties_are_official_dock3_rw_set(self):
        self.assertEqual(
            writable_property_names(),
            {"air_transfer_enable", "silent_mode", "user_experience_improvement"},
        )

    def test_silent_mode_accepts_documented_enum_values(self):
        result, value, reason = validate_property_set("silent_mode", 1)

        self.assertEqual(result, PROPERTY_SET_SUCCESS)
        self.assertEqual(value, 1)
        self.assertEqual(reason, "")

    def test_silent_mode_rejects_out_of_range_and_bool_values(self):
        for invalid_value in (2, 9, True, "1"):
            result, _, reason = validate_property_set("silent_mode", invalid_value)
            self.assertEqual(result, PROPERTY_SET_FAILED, invalid_value)
            self.assertNotEqual(reason, "")

    def test_air_transfer_enable_accepts_bool_only(self):
        result, value, reason = validate_property_set("air_transfer_enable", True)

        self.assertEqual(result, PROPERTY_SET_SUCCESS)
        self.assertIs(value, True)
        self.assertEqual(reason, "")

        for invalid_value in (1, 0, "true"):
            result, _, reason = validate_property_set("air_transfer_enable", invalid_value)
            self.assertEqual(result, PROPERTY_SET_FAILED, invalid_value)
            self.assertNotEqual(reason, "")

    def test_user_experience_improvement_accepts_documented_enum_values(self):
        for expected in (0, 1, 2):
            result, value, reason = validate_property_set("user_experience_improvement", expected)
            self.assertEqual(result, PROPERTY_SET_SUCCESS, expected)
            self.assertEqual(value, expected)
            self.assertEqual(reason, "")

    def test_readonly_and_unknown_properties_are_rejected(self):
        for name in ("battery_store_mode", "alarm_state", "drc_state", "unknown_property"):
            result, _, reason = validate_property_set(name, 1)
            self.assertEqual(result, PROPERTY_SET_FAILED, name)
            self.assertNotEqual(reason, "")

    def test_readonly_specs_are_still_available_for_property_get(self):
        spec = get_property_spec("drc_state")

        self.assertIsNotNone(spec)
        self.assertFalse(spec.writable)
        self.assertEqual(spec.label, "DRC链路状态")


if __name__ == "__main__":
    unittest.main()
