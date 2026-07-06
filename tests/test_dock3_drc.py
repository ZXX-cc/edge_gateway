import os
import sys
import unittest


PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from src.cloud_api.dock3_drc import (
    STICK_CENTER,
    normalize_drone_control,
    normalize_stick_control,
    validate_drc_mode_enter,
)


class Dock3DrcTest(unittest.TestCase):
    def test_drc_mode_enter_accepts_official_broker_payload(self):
        ok, errors = validate_drc_mode_enter({
            "mqtt_broker": {
                "address": "127.0.0.1:8883",
                "client_id": "drc-EDGE-GW-001",
                "username": "user",
                "password": "token",
                "expire_time": 3600,
                "enable_tls": False,
            },
            "osd_frequency": 10,
            "hsi_frequency": 1,
        })

        self.assertTrue(ok, errors)

    def test_drc_mode_enter_rejects_bad_frequency(self):
        ok, errors = validate_drc_mode_enter({"osd_frequency": 0})

        self.assertFalse(ok)
        self.assertTrue(any("osd_frequency" in error for error in errors))

    def test_stick_control_accepts_official_center_channels(self):
        ok, normalized, errors = normalize_stick_control({
            "roll": STICK_CENTER,
            "pitch": STICK_CENTER,
            "throttle": STICK_CENTER,
            "yaw": STICK_CENTER,
            "gimbal_pitch": STICK_CENTER,
        })

        self.assertTrue(ok, errors)
        self.assertEqual(normalized, {"vx": 0.0, "vy": 0.0, "vz": -0.0, "yaw_rate": 0.0})

    def test_stick_control_rejects_out_of_range_channel(self):
        ok, _, errors = normalize_stick_control({
            "roll": 1024,
            "pitch": 2000,
            "throttle": 1024,
            "yaw": 1024,
        })

        self.assertFalse(ok)
        self.assertTrue(any("pitch" in error for error in errors))

    def test_drone_control_accepts_official_deprecated_fields(self):
        ok, normalized, errors = normalize_drone_control({
            "seq": 1,
            "x": 1.0,
            "y": -1.0,
            "h": 2.0,
            "w": 30.0,
        })

        self.assertTrue(ok, errors)
        self.assertEqual(normalized["vx"], 1.0)
        self.assertEqual(normalized["vy"], -1.0)
        self.assertEqual(normalized["vz"], -2.0)
        self.assertEqual(normalized["seq"], 1)

    def test_drone_control_rejects_out_of_range_fields(self):
        ok, _, errors = normalize_drone_control({
            "seq": 1,
            "x": 99.0,
            "y": 0.0,
            "h": 0.0,
            "w": 0.0,
        })

        self.assertFalse(ok)
        self.assertTrue(any("x" in error for error in errors))


if __name__ == "__main__":
    unittest.main()
