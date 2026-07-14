import unittest
from types import SimpleNamespace

from src.utils.runtime_overrides import build_runtime_overrides


class RuntimeOverridesTest(unittest.TestCase):
    def test_mission_completion_cli_overrides_are_applied(self):
        args = SimpleNamespace(
            runtime_mode="production",
            gateway_sn="REALDOCK001",
            gateway_device_secret="secret",
            gateway_nonce="nonce",
            sub_device_json=[],
            mission_completion_enabled="true",
            mission_completion_retry_count=5,
            mission_completion_retry_interval=1.5,
        )

        overrides = build_runtime_overrides(args)

        self.assertEqual(overrides["runtime"]["mode"], "production")
        self.assertEqual(overrides["gateway"]["sn"], "REALDOCK001")
        self.assertEqual(overrides["device"]["topology"]["device_secret"], "secret")
        self.assertEqual(overrides["device"]["topology"]["nonce"], "nonce")
        self.assertEqual(overrides["mission"]["completion"]["enabled"], True)
        self.assertEqual(overrides["mission"]["completion"]["retry_count"], 5)
        self.assertEqual(overrides["mission"]["completion"]["retry_interval_seconds"], 1.5)

    def test_empty_mission_completion_overrides_are_not_emitted(self):
        args = SimpleNamespace(
            runtime_mode=None,
            gateway_sn=None,
            gateway_device_secret=None,
            gateway_nonce=None,
            sub_device_json=[],
            mission_completion_enabled=None,
            mission_completion_retry_count=None,
            mission_completion_retry_interval=None,
        )

        self.assertEqual(build_runtime_overrides(args), {})


if __name__ == "__main__":
    unittest.main()
