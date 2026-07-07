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
        }
        defaults.update(overrides)
        return argparse.Namespace(**defaults)

    def test_selected_steps_filters_phase_and_skip(self):
        p0_steps = selected_steps("p0", "", "device")

        self.assertEqual([step.key for step in p0_steps], ["properties", "osd-state"])

    def test_selected_steps_only_preserves_default_order(self):
        steps = selected_steps("all", "wayline,properties", "")

        self.assertEqual([step.key for step in steps], ["properties", "wayline"])

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

    def test_build_command_omits_fail_on_stale_for_osd_state(self):
        step = next(step for step in STEPS if step.key == "osd-state")
        command = build_command(step, self.args(fail_on_stale=True, osd_allow_invalid=True))

        self.assertIn("mqtt_dock3_osd_state_test.py", command[1])
        self.assertIn("--reply-timeout", command)
        self.assertIn("--allow-invalid", command)
        self.assertNotIn("--fail-on-stale", command)


if __name__ == "__main__":
    unittest.main()
