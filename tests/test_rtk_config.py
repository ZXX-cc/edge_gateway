"""RTK 配置兼容性测试。"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.modules.rtk_module import RTKModule  # noqa: E402


class RTKConfigTest(unittest.TestCase):
    def test_legacy_ntrip_config_normalizes_to_named_source(self):
        module = RTKModule({
            "enabled": True,
            "host": "caster.example.com",
            "port": 8002,
            "mountpoint": "RTCM33",
            "username": "user",
            "password": "pass",
        })

        config = module.get_config()

        self.assertTrue(config["enabled"])
        self.assertIn("NTRIP", config["sources"])
        self.assertEqual(config["sources"]["NTRIP"]["type"], "ntrip")
        self.assertEqual(config["sources"]["NTRIP"]["host"], "caster.example.com")

    def test_auto_selects_highest_priority_configured_source(self):
        module = RTKModule({
            "enabled": True,
            "active_source": "auto",
            "sources": {
                "F9P": {"type": "tcp", "host": "192.168.1.13", "port": 8600, "priority": 10},
                "NTRIP": {
                    "type": "ntrip",
                    "host": "caster.example.com",
                    "port": 8002,
                    "mountpoint": "RTCM33",
                    "username": "user",
                    "password": "pass",
                    "priority": 5,
                },
            },
        })

        name, source = module._select_source_config()

        self.assertEqual(name, "F9P")
        self.assertEqual(source["type"], "tcp")

    def test_explicit_active_source_overrides_priority(self):
        module = RTKModule({
            "enabled": True,
            "active_source": "NTRIP",
            "sources": {
                "F9P": {"type": "tcp", "host": "192.168.1.13", "port": 8600, "priority": 10},
                "NTRIP": {
                    "type": "ntrip",
                    "host": "caster.example.com",
                    "port": 8002,
                    "mountpoint": "RTCM33",
                    "username": "user",
                    "password": "pass",
                    "priority": 5,
                },
            },
        })

        name, source = module._select_source_config()

        self.assertEqual(name, "NTRIP")
        self.assertEqual(source["type"], "ntrip")


if __name__ == "__main__":
    unittest.main(verbosity=2)
