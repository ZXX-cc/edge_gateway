import os
import sys
import unittest


PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)
TOOLS_DIR = os.path.join(PROJECT_ROOT, "tools")
if TOOLS_DIR not in sys.path:
    sys.path.insert(0, TOOLS_DIR)

from src.cloud_api.dock3_device import (
    GATEWAY_DOMAIN,
    GATEWAY_SUB_TYPE,
    GATEWAY_TYPE,
    THING_VERSION,
    build_sub_device_payload,
    build_update_topo_payload,
    bx160_model_sub_type,
    validate_update_topo_payload,
)
from mqtt_dock3_device_test import build_extra_sub_device


class Dock3DeviceTopologyTest(unittest.TestCase):
    def test_build_update_topo_payload_matches_required_schema(self):
        payload = build_update_topo_payload(
            device_secret="gw-secret",
            nonce="gw-nonce",
            sub_devices=[
                build_sub_device_payload(
                    "DRONE-SIM-001",
                    domain="0",
                    device_type=60,
                    sub_type=0,
                    index="A",
                    device_secret="drone-secret",
                    nonce="drone-nonce",
                )
            ],
        )

        ok, errors = validate_update_topo_payload(payload)

        self.assertTrue(ok, errors)
        self.assertEqual(payload["domain"], GATEWAY_DOMAIN)
        self.assertEqual(payload["type"], GATEWAY_TYPE)
        self.assertEqual(payload["sub_type"], GATEWAY_SUB_TYPE)
        self.assertEqual(payload["thing_version"], THING_VERSION)
        self.assertEqual(payload["sub_devices"][0]["sn"], "DRONE-SIM-001")

        strict_ok, strict_errors = validate_update_topo_payload(payload, strict=True)
        self.assertTrue(strict_ok, strict_errors)

    def test_empty_sub_devices_is_valid_offline_topology(self):
        payload = build_update_topo_payload(sub_devices=[])

        ok, errors = validate_update_topo_payload(payload)

        self.assertTrue(ok, errors)
        self.assertEqual(payload["sub_devices"], [])

    def test_strict_topology_rejects_empty_secret_and_nonce(self):
        payload = build_update_topo_payload(sub_devices=[])

        ok, errors = validate_update_topo_payload(payload, strict=True)

        self.assertFalse(ok)
        self.assertIn("device_secret must not be empty", errors)
        self.assertIn("nonce must not be empty", errors)

    def test_strict_topology_rejects_unknown_device_enum(self):
        payload = build_update_topo_payload(
            device_secret="gw-secret",
            nonce="gw-nonce",
            sub_devices=[
                build_sub_device_payload(
                    "DRONE-SIM-001",
                    domain="0",
                    device_type=9999,
                    sub_type=0,
                    device_secret="drone-secret",
                    nonce="drone-nonce",
                )
            ],
        )

        ok, errors = validate_update_topo_payload(payload, strict=True)

        self.assertFalse(ok)
        self.assertTrue(any("unsupported topology" in error for error in errors))

    def test_missing_required_keys_are_rejected(self):
        ok, errors = validate_update_topo_payload({"sub_devices": []})

        self.assertFalse(ok)
        self.assertTrue(any("missing update_topo keys" in error for error in errors))

    def test_invalid_sub_device_is_rejected(self):
        payload = build_update_topo_payload()
        payload["sub_devices"] = [{"sn": "", "domain": "0", "type": "60"}]

        ok, errors = validate_update_topo_payload(payload)

        self.assertFalse(ok)
        self.assertTrue(any("sn must not be empty" in error for error in errors))
        self.assertTrue(any("type must be int" in error for error in errors))

    def test_extra_sub_device_json_normalizes_to_schema(self):
        device = build_extra_sub_device(
            '{"sn":"DRONE-SIM-002","domain":"0","type":"60","sub_type":"0","index":"B"}'
        )

        self.assertEqual(device["sn"], "DRONE-SIM-002")
        self.assertEqual(device["type"], 60)
        self.assertEqual(device["sub_type"], 0)
        self.assertEqual(device["index"], "B")

    def test_bx160_model_codes_map_to_gateway_sub_type(self):
        self.assertEqual(bx160_model_sub_type("01"), 0)
        self.assertEqual(bx160_model_sub_type("02"), 1)
        self.assertEqual(bx160_model_sub_type("03"), 2)
        self.assertEqual(bx160_model_sub_type("04"), 3)
        self.assertEqual(bx160_model_sub_type("05"), 4)
        self.assertEqual(bx160_model_sub_type("unknown", default=9), 9)


if __name__ == "__main__":
    unittest.main()
