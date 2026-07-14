import os
import sys
import unittest


PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from src.cloud_api.dock3_cmd import (
    OFFICIAL_DOCK3_CMD_METHODS,
    TASK_STATUS_OK,
    command_output,
    progress_event_data,
    validate_action_payload,
)


class Dock3CmdTest(unittest.TestCase):
    def test_official_dock3_cmd_scope_excludes_legacy_internal_methods(self):
        self.assertIn("cover_open", OFFICIAL_DOCK3_CMD_METHODS)
        self.assertIn("sim_slot_switch", OFFICIAL_DOCK3_CMD_METHODS)
        self.assertNotIn("putter_open", OFFICIAL_DOCK3_CMD_METHODS)
        self.assertNotIn("device_reset", OFFICIAL_DOCK3_CMD_METHODS)
        self.assertNotIn("format_sd_card", OFFICIAL_DOCK3_CMD_METHODS)

    def test_action_switch_accepts_official_action_field(self):
        ok, value, field, errors = validate_action_payload(
            "air_conditioner_mode_switch",
            {"action": 3},
        )

        self.assertTrue(ok, errors)
        self.assertEqual(value, 3)
        self.assertEqual(field, "action")

    def test_action_switch_rejects_bool_and_out_of_range(self):
        ok, _, _, errors = validate_action_payload(
            "alarm_state_switch",
            {"action": True},
        )
        self.assertFalse(ok)
        self.assertTrue(any("int enum" in error for error in errors))

        ok, _, _, errors = validate_action_payload(
            "battery_store_mode_switch",
            {"action": 9},
        )
        self.assertFalse(ok)
        self.assertTrue(any("out of range" in error for error in errors))

    def test_battery_store_accepts_link_script_action_zero(self):
        ok, value, field, errors = validate_action_payload(
            "battery_store_mode_switch",
            {"action": 0},
        )

        self.assertTrue(ok, errors)
        self.assertEqual(value, 1)
        self.assertEqual(field, "action")

    def test_sdr_accepts_official_link_workmode_and_legacy_mode_alias(self):
        ok, value, field, errors = validate_action_payload(
            "sdr_workmode_switch",
            {"link_workmode": 1},
        )
        self.assertTrue(ok, errors)
        self.assertEqual(value, 1)
        self.assertEqual(field, "link_workmode")

        ok, value, field, errors = validate_action_payload(
            "sdr_workmode_switch",
            {"mode": 0},
        )
        self.assertTrue(ok, errors)
        self.assertEqual(value, 0)
        self.assertEqual(field, "mode")

        ok, value, field, errors = validate_action_payload(
            "sdr_workmode_switch",
            {"action": 0},
        )
        self.assertTrue(ok, errors)
        self.assertEqual(value, 0)
        self.assertEqual(field, "action")

    def test_command_output_matches_dock3_status_shape(self):
        data = command_output(TASK_STATUS_OK, extra={"action": 1})

        self.assertEqual(data["output"]["status"], "ok")
        self.assertEqual(data["action"], 1)

    def test_progress_event_contains_status_and_step(self):
        data = progress_event_data("cover_open", percent=100)

        self.assertEqual(data["result"], 0)
        self.assertEqual(data["output"]["status"], "ok")
        self.assertEqual(data["output"]["progress"]["percent"], 100)
        self.assertEqual(data["output"]["progress"]["step_key"], "open_cover")


if __name__ == "__main__":
    unittest.main()
