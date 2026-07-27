import argparse
import unittest

from src.utils.config import Config
from src.utils.production_readiness import validate_production_config
from src.utils.runtime_overrides import build_runtime_overrides


class ProductionReadinessTest(unittest.TestCase):
    def tearDown(self):
        Config._data = {}

    def test_production_rejects_demo_identity_and_missing_topology(self):
        errors = validate_production_config({
            "runtime": {"mode": "production"},
            "gateway": {"sn": "EDGEGW001"},
            "nanomq": {"host": "127.0.0.1"},
            "device": {"topology": {}},
            "security": {"mqtt": {}},
        })

        self.assertTrue(any("test identity" in error for error in errors))
        self.assertIn("device.topology.device_secret is required in production mode", errors)
        self.assertIn("device.topology.nonce is required in production mode", errors)
        self.assertIn("security.mqtt.downstream_auth_enabled must be true in production mode", errors)
        self.assertIn("security.mqtt.sign_secret must be at least 16 characters in production mode", errors)

    def test_production_accepts_explicit_real_chain_metadata(self):
        errors = validate_production_config({
            "runtime": {"mode": "production"},
            "gateway": {"sn": "REALDOCK001"},
            "nanomq": {"host": "10.0.0.10"},
            "device": {
                "topology": {"device_secret": "gw-secret", "nonce": "gw-nonce"},
                "sub_devices": [
                    {
                        "sn": "AIRCRAFT001",
                        "device_secret": "aircraft-secret",
                        "nonce": "aircraft-nonce",
                    }
                ],
            },
            "security": {
                "mqtt": {
                    "downstream_auth_enabled": True,
                    "sign_secret": "real-chain-secret",
                }
            },
        })

        self.assertEqual(errors, [])

    def test_runtime_overrides_from_startup_args(self):
        args = argparse.Namespace(
            runtime_mode="production",
            gateway_sn="REALDOCK001",
            gateway_device_secret="gw-secret",
            gateway_nonce="gw-nonce",
            sub_device_json=[
                '{"sn":"AIRCRAFT001","domain":"0","type":60,"sub_type":0,'
                '"index":"A","device_secret":"air-secret","nonce":"air-nonce",'
                '"thing_version":"1.1.2","online":true}'
            ],
        )

        overrides = build_runtime_overrides(args)

        self.assertEqual(overrides["runtime"]["mode"], "production")
        self.assertEqual(overrides["gateway"]["sn"], "REALDOCK001")
        self.assertEqual(overrides["device"]["topology"]["device_secret"], "gw-secret")
        self.assertEqual(overrides["device"]["topology"]["nonce"], "gw-nonce")
        self.assertEqual(overrides["device"]["sub_devices"][0]["sn"], "AIRCRAFT001")

    def test_config_merge_applies_nested_overrides(self):
        Config._data = {
            "runtime": {"mode": "test"},
            "device": {"topology": {"device_secret": "", "nonce": ""}},
        }

        Config.merge({"runtime": {"mode": "production"}, "device": {"topology": {"nonce": "n"}}})

        self.assertEqual(Config.get("runtime.mode"), "production")
        self.assertEqual(Config.get("device.topology.device_secret"), "")
        self.assertEqual(Config.get("device.topology.nonce"), "n")


if __name__ == "__main__":
    unittest.main()
