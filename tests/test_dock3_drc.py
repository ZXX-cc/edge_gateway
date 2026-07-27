import os
import sys
import time
import types
import unittest


PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from src.cloud_api.dock3_drc import (
    OFFICIAL_DOCK3_DRC_METHODS,
    STICK_CENTER,
    normalize_fly_to_point_update,
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
from src.utils.config import Config
from src.utils.dji_error_codes import (
    DEVICE_COMMAND_TIMEOUT,
    DOCK_COMMUNICATION_ERROR,
    INVALID_COMMAND_FORMAT,
)


class FakeModeControl:
    def __init__(self, *, set_mode_success=True, goto_success=True):
        self.set_mode_success = set_mode_success
        self.goto_success = goto_success
        self.calls = []

    def set_mode(self, mode):
        self.calls.append(("set_mode", mode))
        return {"success": self.set_mode_success, "message": mode}

    def guided_goto(self, latitude, longitude, height):
        self.calls.append(("guided_goto", latitude, longitude, height))
        return {"success": self.goto_success, "message": "guided target sent"}

    def guided_loiter_at_point(self, latitude, longitude, height, radius=80.0):
        self.calls.append(("guided_loiter_at_point", latitude, longitude, height, radius))
        return {"success": self.goto_success, "message": "guided loiter target sent"}


class FakeCameraController:
    def __init__(self):
        self.calls = []

    def handle_camera_photo_take(self, msg):
        self.calls.append(("photo", msg["data"]["camera_index"]))
        return {"data": {"result": 0}}

    def handle_camera_recording_start(self, msg):
        self.calls.append(("record_start", msg["data"]["camera_index"]))
        return {"data": {"result": 0}}

    def handle_camera_recording_stop(self, msg):
        self.calls.append(("record_stop", msg["data"]["camera_index"]))
        return {"data": {"result": 0}}


class Dock3DrcTest(unittest.TestCase):
    def tearDown(self):
        Config._data = {}

    def test_official_dock3_drc_scope_contains_core_methods(self):
        for method in [
            "drc_mode_enter",
            "drc_mode_exit",
            "takeoff_to_point",
            "fly_to_point",
            "fly_to_point_update",
            "stick_control",
            "drone_control",
            "drone_emergency_stop",
            "heart_beat",
            "payload_authority_grab",
            "camera_photo_take",
            "osd_info_push",
        ]:
            self.assertIn(method, OFFICIAL_DOCK3_DRC_METHODS)

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

    def test_drc_mode_enter_rejects_expired_broker_epoch_time(self):
        ok, errors = validate_drc_mode_enter({
            "mqtt_broker": {
                "address": "127.0.0.1:8883",
                "client_id": "drc-EDGE-GW-001",
                "username": "user",
                "password": "token",
                "expire_time": int(time.time()) - 1,
                "enable_tls": False,
            },
        })

        self.assertFalse(ok)
        self.assertTrue(any("expired" in error for error in errors))

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

    def test_drc_heartbeat_rejects_old_timestamp_and_non_increasing_seq(self):
        Config._data = {
            "security": {
                "drc": {
                    "heartbeat_timestamp_window_seconds": 30,
                    "require_heartbeat_seq_increasing": True,
                }
            }
        }
        controller = DrcController()
        controller._drc_enabled = True

        first = controller.handle_drc_heart_beat({
            "method": "heart_beat",
            "data": {"seq": 1, "timestamp": int(time.time() * 1000)},
        })
        repeated = controller.handle_drc_heart_beat({
            "method": "heart_beat",
            "data": {"seq": 1, "timestamp": int(time.time() * 1000)},
        })
        old = controller.handle_drc_heart_beat({
            "method": "heart_beat",
            "data": {"seq": 2, "timestamp": int((time.time() - 60) * 1000)},
        })

        self.assertEqual(first["data"]["result"], 0)
        self.assertEqual(repeated["data"]["result"], INVALID_COMMAND_FORMAT)
        self.assertEqual(old["data"]["result"], INVALID_COMMAND_FORMAT)

    def test_flight_authority_grab_returns_status_info(self):
        controller = DrcController()
        controller.set_mode_control(FakeModeControl())

        reply = controller.handle_flight_authority_grab({
            "method": "flight_authority_grab",
            "tid": "tid-authority",
            "gateway": "EDGEGW001",
            "data": {},
        })

        self.assertEqual(reply["data"]["result"], 0)
        self.assertEqual(reply["data"]["output"]["status"], "ok")
        self.assertEqual(reply["data"]["authority"], "flight")
        self.assertEqual(reply["data"]["flight_authority"], 1)

    def test_flight_authority_grab_fails_without_real_adapter(self):
        controller = DrcController()

        reply = controller.handle_flight_authority_grab({
            "method": "flight_authority_grab",
            "tid": "tid-authority",
            "gateway": "EDGEGW001",
            "data": {},
        })

        self.assertEqual(reply["data"]["result"], DOCK_COMMUNICATION_ERROR)
        self.assertEqual(reply["data"]["output"]["status"], "failed")

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

    def test_fly_to_point_update_normalizes_points(self):
        ok, normalized, errors = normalize_fly_to_point_update({
            "fly_to_id": "flyto-001",
            "max_speed": 8,
            "points": [{
                "latitude": -35.3632622,
                "longitude": 149.1652375,
                "height": 45,
            }],
        })

        self.assertTrue(ok, errors)
        self.assertEqual(normalized["fly_to_id"], "flyto-001")
        self.assertEqual(normalized["max_speed"], 8.0)
        self.assertEqual(normalized["points"][0]["height"], 45.0)

    def test_fly_to_point_update_rejects_empty_points(self):
        ok, _, errors = normalize_fly_to_point_update({"points": []})

        self.assertFalse(ok)
        self.assertTrue(any("points" in error for error in errors))

    def test_controller_heartbeat_updates_cloud_timestamp(self):
        controller = DrcController()
        controller._drc_enabled = True
        before = controller._last_cloud_heartbeat

        reply = controller.handle_drc_heart_beat({
            "method": "heart_beat",
            "tid": "tid-heartbeat",
            "gateway": "EDGEGW001",
            "data": {"seq": 1, "timestamp": int(time.time() * 1000)},
        })

        self.assertEqual(reply["data"]["result"], 0)
        self.assertGreater(controller._last_cloud_heartbeat, before)
        self.assertEqual(reply["data"]["drc_state"], 2)
        self.assertEqual(reply["data"]["seq"], 1)

    def test_controller_heartbeat_timeout_auto_exits_and_reports(self):
        controller = DrcController()
        events = []
        controller.set_event_callback(events.append)
        controller._drc_enabled = True
        controller._heartbeat_running = True
        controller._heartbeat_timeout = 10
        controller._last_cloud_heartbeat = 100
        controller._session_id = "session-timeout"

        timed_out = controller._check_heartbeat_timeout_once(current_time=111)

        self.assertTrue(timed_out)
        self.assertFalse(controller.get_drc_status()["enabled"])
        self.assertEqual(events[-1]["method"], "drc_status_notify")
        self.assertEqual(events[-1]["data"]["drc_state"], 0)
        self.assertEqual(events[-1]["data"]["result"], DEVICE_COMMAND_TIMEOUT)

    def test_controller_drc_mode_enter_holds_fixed_wing_in_loiter(self):
        mode = FakeModeControl()
        controller = DrcController()
        controller.set_mode_control(mode)
        events = []
        controller.set_event_callback(events.append)

        reply = controller.handle_drc_mode_enter({
            "method": "drc_mode_enter",
            "tid": "tid-enter",
            "gateway": "EDGEGW001",
            "data": {},
        })
        controller._heartbeat_running = False

        self.assertEqual(reply["data"]["result"], 0)
        self.assertTrue(controller.get_drc_status()["enabled"])
        self.assertIn(("set_mode", "LOITER"), mode.calls)
        self.assertNotIn(("set_mode", "GUIDED"), mode.calls)
        self.assertEqual(events[-1]["method"], "drc_status_notify")
        self.assertEqual(events[-1]["data"]["drc_state"], 2)

    def test_controller_takeoff_to_point_reports_progress_event(self):
        controller = DrcController()
        mode = FakeModeControl()
        controller.set_mode_control(mode)
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
        self.assertEqual(reply["data"]["output"]["simulated"], False)
        self.assertIn(("set_mode", "GUIDED"), mode.calls)
        self.assertIn(("guided_loiter_at_point", -35.3632622, 149.1652375, 50.0, 80.0), mode.calls)

    def test_controller_takeoff_to_point_fails_without_guided_adapter(self):
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

        self.assertEqual(reply["data"]["result"], DOCK_COMMUNICATION_ERROR)
        self.assertEqual(reply["data"]["output"]["status"], "failed")
        self.assertEqual(events[-1]["data"]["status"], "wayline_failed")
        self.assertIsNone(controller.get_drc_status()["active_takeoff_to_point"])

    def test_controller_fly_to_point_update_and_stop_report_events(self):
        controller = DrcController()
        mode = FakeModeControl()
        controller.set_mode_control(mode)
        events = []
        controller.set_event_callback(events.append)

        update = controller.handle_fly_to_point_update({
            "method": "fly_to_point_update",
            "tid": "tid-flyto",
            "gateway": "EDGEGW001",
            "data": {
                "fly_to_id": "flyto-001",
                "max_speed": 8,
                "points": [{
                    "latitude": -35.3632622,
                    "longitude": 149.1652375,
                    "height": 45,
                }],
            },
        })

        self.assertEqual(update["data"]["result"], 0)
        self.assertEqual(update["data"]["fly_to_id"], "flyto-001")
        self.assertEqual(events[-1]["method"], "fly_to_point_progress")
        self.assertEqual(events[-1]["data"]["status"], "wayline_progress")
        self.assertTrue(controller.get_drc_status()["enabled"])
        self.assertIn(("set_mode", "GUIDED"), mode.calls)
        self.assertIn(("guided_loiter_at_point", -35.3632622, 149.1652375, 45.0, 80.0), mode.calls)

        stop = controller.handle_fly_to_point_stop({
            "method": "fly_to_point_stop",
            "tid": "tid-stop",
            "gateway": "EDGEGW001",
            "data": {},
        })

        self.assertEqual(stop["data"]["result"], 0)
        self.assertTrue(stop["data"]["output"]["stopped"])
        self.assertEqual(events[-1]["data"]["status"], "wayline_cancel")

    def test_controller_fly_to_point_update_fails_without_guided_adapter(self):
        controller = DrcController()
        events = []
        controller.set_event_callback(events.append)
        controller._drc_enabled = True
        controller._flight_authority_grabbed = True

        reply = controller.handle_fly_to_point_update({
            "method": "fly_to_point_update",
            "tid": "tid-flyto",
            "gateway": "EDGEGW001",
            "data": {
                "fly_to_id": "flyto-001",
                "max_speed": 8,
                "points": [{
                    "latitude": -35.3632622,
                    "longitude": 149.1652375,
                    "height": 45,
                }],
            },
        })

        self.assertEqual(reply["data"]["result"], DOCK_COMMUNICATION_ERROR)
        self.assertEqual(reply["data"]["output"]["status"], "failed")
        self.assertEqual(events[-1]["data"]["status"], "wayline_failed")
        self.assertIsNone(controller.get_drc_status()["active_fly_to_point"])

    def test_controller_fly_to_point_update_rejects_when_auto_drc_enter_fails(self):
        controller = DrcController()
        mode = FakeModeControl(set_mode_success=False)
        controller.set_mode_control(mode)

        reply = controller.handle_fly_to_point_update({
            "method": "fly_to_point_update",
            "tid": "tid-flyto",
            "gateway": "EDGEGW001",
            "data": {
                "fly_to_id": "flyto-001",
                "max_speed": 8,
                "points": [{
                    "latitude": -35.3632622,
                    "longitude": 149.1652375,
                    "height": 45,
                }],
            },
        })

        self.assertEqual(reply["data"]["result"], DOCK_COMMUNICATION_ERROR)
        self.assertEqual(reply["data"]["output"]["status"], "failed")
        self.assertFalse(controller.get_drc_status()["enabled"])
        self.assertIsNone(controller.get_drc_status()["active_fly_to_point"])
        self.assertNotIn(("guided_loiter_at_point", -35.3632622, 149.1652375, 45.0, 80.0), mode.calls)

    def test_controller_fly_to_point_update_grabs_authority_when_drc_already_active(self):
        controller = DrcController()
        mode = FakeModeControl()
        controller.set_mode_control(mode)
        controller._drc_enabled = True
        controller._flight_authority_grabbed = False

        reply = controller.handle_fly_to_point_update({
            "method": "fly_to_point_update",
            "tid": "tid-flyto",
            "gateway": "EDGEGW001",
            "data": {
                "fly_to_id": "flyto-001",
                "max_speed": 8,
                "points": [{
                    "latitude": -35.3632622,
                    "longitude": 149.1652375,
                    "height": 45,
                }],
            },
        })

        self.assertEqual(reply["data"]["result"], 0)
        self.assertTrue(controller._flight_authority_grabbed)
        self.assertIn(("guided_loiter_at_point", -35.3632622, 149.1652375, 45.0, 80.0), mode.calls)

    def test_payload_control_accepts_official_three_part_payload_index(self):
        controller = DrcController()
        camera = FakeCameraController()
        controller.set_camera_controller(camera)
        controller._drc_enabled = True

        reply = controller.handle_payload_control({
            "method": "payload_control",
            "tid": "tid-payload",
            "gateway": "EDGEGW001",
            "data": {
                "payload_index": "52-0-0",
                "action": "photo_take",
                "params": {},
            },
        })

        self.assertEqual(reply["data"]["result"], 0)
        self.assertEqual(camera.calls, [("photo", 0)])

    def test_payload_control_record_actions_use_camera_recording_handlers(self):
        controller = DrcController()
        camera = FakeCameraController()
        controller.set_camera_controller(camera)
        controller._drc_enabled = True

        for action in ("record_start", "record_stop"):
            reply = controller.handle_payload_control({
                "method": "payload_control",
                "tid": f"tid-{action}",
                "gateway": "EDGEGW001",
                "data": {
                    "payload_index": "52-0-0",
                    "action": action,
                    "params": {},
                },
            })
            self.assertEqual(reply["data"]["result"], 0)

        self.assertEqual(camera.calls, [("record_start", 0), ("record_stop", 0)])

    def test_payload_todo_methods_fail_without_real_adapter(self):
        controller = DrcController()
        for method, handler in [
            ("payload_authority_grab", controller.handle_payload_authority_grab),
            ("camera_screen_drag", controller.handle_camera_screen_drag),
            ("camera_photo_take", controller.handle_camera_photo_take),
            ("video_storage_set", controller.handle_video_storage_set),
            ("ir_metering_point_set", controller.handle_ir_metering_point_set),
        ]:
            with self.subTest(method=method):
                reply = handler({
                    "method": method,
                    "tid": f"tid-{method}",
                    "gateway": "EDGEGW001",
                    "data": {},
                })
                self.assertEqual(reply["data"]["result"], DOCK_COMMUNICATION_ERROR)
                self.assertEqual(reply["data"]["output"]["status"], "failed")

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
