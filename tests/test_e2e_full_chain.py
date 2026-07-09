import argparse
import os
import sys
import unittest


PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOLS_DIR = os.path.join(PROJECT_ROOT, "tools")
if TOOLS_DIR not in sys.path:
    sys.path.insert(0, TOOLS_DIR)

from e2e_full_chain_test import STEPS, build_command, selected_steps  # noqa: E402


class E2EFullChainTest(unittest.TestCase):
    def args(self, **overrides):
        defaults = {
            "python": "python",
            "config": "config/gateway.yaml",
            "timeout": 12.0,
            "reply_timeout": 8.0,
            "qos": 1,
            "host": None,
            "port": None,
            "sn": None,
            "quiet_stale": True,
            "fail_on_stale": False,
            "osd_allow_invalid": False,
            "device_extra_sub_device_json": [],
        }
        defaults.update(overrides)
        return argparse.Namespace(**defaults)

    def test_selected_steps_filters_phase_and_skip(self):
        p0_steps = selected_steps("p0", "", "device")

        self.assertEqual([step.key for step in p0_steps], ["properties", "osd-state"])

    def test_selected_steps_only_preserves_default_order(self):
        steps = selected_steps("all", "firmware-config,wayline,properties", "")

        self.assertEqual([step.key for step in steps], ["properties", "wayline", "firmware-config"])

    def test_selected_steps_rejects_unknown_key(self):
        with self.assertRaises(ValueError):
            selected_steps("all", "missing", "")

    def test_build_command_adds_common_args(self):
        step = next(step for step in STEPS if step.key == "wayline")
        command = build_command(
            step,
            self.args(host="127.0.0.1", port=1883, sn="EDGEGW001", fail_on_stale=True),
        )

        self.assertIn("mqtt_dock3_wayline_test.py", command[1])
        self.assertIn("--config", command)
        self.assertIn("--timeout", command)
        self.assertIn("--quiet-stale", command)
        self.assertIn("--fail-on-stale", command)
        self.assertIn("EDGEGW001", command)

    def test_build_command_passes_fail_on_stale_for_osd_state(self):
        step = next(step for step in STEPS if step.key == "osd-state")
        command = build_command(step, self.args(fail_on_stale=True, osd_allow_invalid=True))

        self.assertIn("mqtt_dock3_osd_state_test.py", command[1])
        self.assertIn("--reply-timeout", command)
        self.assertIn("--allow-invalid", command)
        self.assertIn("--fail-on-stale", command)

    def test_build_command_uses_step_timeout_override(self):
        step = next(step for step in STEPS if step.key == "firmware-config")
        command = build_command(step, self.args(timeout=12.0))

        timeout_index = command.index("--timeout")
        self.assertEqual(command[timeout_index + 1], "20.0")
        self.assertIn("mqtt_dock3_firmware_config_test.py", command[1])

    def test_build_command_passes_extra_sub_devices_only_to_device_step(self):
        extra = '{"sn":"DRONE-SIM-002","domain":"0","type":60,"sub_type":0,"index":"B"}'
        device_step = next(step for step in STEPS if step.key == "device")
        command = build_command(device_step, self.args(device_extra_sub_device_json=[extra]))

        self.assertIn("mqtt_dock3_device_test.py", command[1])
        self.assertIn("--extra-sub-device-json", command)
        self.assertIn(extra, command)

        wayline_step = next(step for step in STEPS if step.key == "wayline")
        wayline_command = build_command(wayline_step, self.args(device_extra_sub_device_json=[extra]))
        self.assertNotIn("--extra-sub-device-json", wayline_command)

    def test_takeoff_flow_step_is_p1_and_omits_stale_flag(self):
        step = next(step for step in STEPS if step.key == "takeoff-flow")
        command = build_command(step, self.args(fail_on_stale=True))

        self.assertEqual(step.phase, "p1")
        self.assertIn("e2e_takeoff_flow_test.py", command[1])
        self.assertNotIn("--fail-on-stale", command)


if __name__ == "__main__":
    unittest.main()
