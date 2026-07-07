import os
import sys
import types
import unittest


PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from src.cloud_api.dock3_drc import (
    STICK_CENTER,
    normalize_drone_control,
    normalize_stick_control,
    normalize_takeoff_to_point,
    validate_drc_heartbeat,
    validate_drc_mode_enter,
)

if "pymavlink" not in sys.modules:
    fake_mavlink = types.ModuleType("pymavlink")
    fake_mavutil = types.ModuleType("pymavlink.mavutil")
    fake_mavutil.mavlink = types.SimpleNamespace(
        MAV_FRAME_BODY_NED=8,
        MAV_FRAME_GLOBAL_RELATIVE_ALT_INT=6,
        MAV_CMD_CONDITION_YAW=115,
    )
    fake_mavlink.mavutil = fake_mavutil
    sys.modules["pymavlink"] = fake_mavlink
    sys.modules["pymavlink.mavutil"] = fake_mavutil

from src.drc.controller import DrcController
from src.utils.dji_error_codes import INVALID_COMMAND_FORMAT


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

    def test_drc_heartbeat_accepts_official_payload(self):
        ok, normalized, errors = validate_drc_heartbeat({"seq": 7, "timestamp": 1783416000000})

        self.assertTrue(ok, errors)
        self.assertEqual(normalized["seq"], 7)
        self.assertEqual(normalized["timestamp"], 1783416000000)

    def test_drc_heartbeat_rejects_invalid_seq(self):
        ok, _, errors = validate_drc_heartbeat({"seq": -1})

        self.assertFalse(ok)
        self.assertTrue(any("seq" in error for error in errors))

    def test_takeoff_to_point_normalizes_official_payload(self):
        ok, normalized, errors = normalize_takeoff_to_point({
            "flight_id": "flight-001",
            "target_latitude": -35.3632622,
            "target_longitude": 149.1652375,
            "target_height": 50,
            "security_takeoff_height": 30,
            "rth_altitude": 80,
            "rc_lost_action": 0,
            "commander_mode_lost_action": 1,
            "commander_flight_height": 70,
            "max_speed": 12,
        })

        self.assertTrue(ok, errors)
        self.assertEqual(normalized["flight_id"], "flight-001")
        self.assertEqual(normalized["target_height"], 50.0)
        self.assertEqual(normalized["max_speed"], 12.0)

    def test_takeoff_to_point_rejects_bad_latitude(self):
        ok, _, errors = normalize_takeoff_to_point({
            "target_latitude": 100,
            "target_longitude": 149.1652375,
            "target_height": 50,
        })

        self.assertFalse(ok)
        self.assertTrue(any("target_latitude" in error for error in errors))

    def test_controller_heartbeat_updates_cloud_timestamp(self):
        controller = DrcController()
        controller._drc_enabled = True
        before = controller._last_cloud_heartbeat

        reply = controller.handle_drc_heart_beat({
            "method": "heart_beat",
            "tid": "tid-heartbeat",
            "gateway": "EDGEGW001",
            "data": {"seq": 1, "timestamp": 1783416000000},
        })

        self.assertEqual(reply["data"]["result"], 0)
        self.assertGreater(controller._last_cloud_heartbeat, before)
        self.assertEqual(reply["data"]["drc_state"], 2)
        self.assertEqual(reply["data"]["seq"], 1)

    def test_controller_takeoff_to_point_reports_progress_event(self):
        controller = DrcController()
        events = []
        controller.set_event_callback(events.append)

        reply = controller.handle_takeoff_to_point({
            "method": "takeoff_to_point",
            "tid": "tid-takeoff",
            "gateway": "EDGEGW001",
            "data": {
                "flight_id": "flight-001",
                "target_latitude": -35.3632622,
                "target_longitude": 149.1652375,
                "target_height": 50,
                "max_speed": 12,
            },
        })

        self.assertEqual(reply["data"]["result"], 0)
        self.assertEqual(reply["data"]["flight_id"], "flight-001")
        self.assertEqual(reply["data"]["output"]["status"], "ok")
        self.assertEqual([event["method"] for event in events], [
            "takeoff_to_point_progress",
            "takeoff_to_point_progress",
        ])
        self.assertEqual(events[0]["data"]["status"], "task_ready")
        self.assertEqual(events[1]["data"]["status"], "wayline_progress")

    def test_controller_takeoff_to_point_rejects_bad_payload(self):
        controller = DrcController()

        reply = controller.handle_takeoff_to_point({
            "method": "takeoff_to_point",
            "tid": "tid-takeoff",
            "gateway": "EDGEGW001",
            "data": {"target_latitude": 100},
        })

        self.assertEqual(reply["data"]["result"], INVALID_COMMAND_FORMAT)


if __name__ == "__main__":
    unittest.main()
