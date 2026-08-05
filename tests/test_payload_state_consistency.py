import unittest
import sys
import types
import os
import tempfile
import threading
from copy import deepcopy


if "src.database.connection" not in sys.modules:
    connection = types.ModuleType("src.database.connection")
    try:
        from sqlalchemy.orm import declarative_base

        Base = declarative_base()
    except Exception:
        Base = type("Base", (), {})

    async def init_database_async():
        return None

    def get_async_session_local():
        return None

    def is_mongo_session(*args, **kwargs):
        return False

    def mongo_model_from_document(*args, **kwargs):
        return None

    def mongo_expired_filter(*args, **kwargs):
        return {}

    connection.Base = Base
    connection.init_database_async = init_database_async
    connection.get_async_session_local = get_async_session_local
    connection.is_mongo_session = is_mongo_session
    connection.mongo_model_from_document = mongo_model_from_document
    connection.mongo_expired_filter = mongo_expired_filter
    sys.modules["src.database.connection"] = connection

if "src.database.media_db" not in sys.modules:
    media_db = types.ModuleType("src.database.media_db")

    class _MediaDB:
        @staticmethod
        async def update_upload_status(*args, **kwargs):
            return True

    media_db.MediaDB = _MediaDB
    sys.modules["src.database.media_db"] = media_db

from src.payload.camera import CameraController
from src.payload.gimbal import GimbalController
from src.modules.gimbal_control import GimbalControl
from src.payload.spectral_camera import SpectralCameraAdapter
from src.utils.config import Config
from src.utils.dji_error_codes import DOCK_SYSTEM_ERROR
from web.mavlink.camera.mapping import build_camera_setting


def message(method, data=None):
    return {"method": method, "gateway": "EDGEGW001", "tid": "payload-test", "data": data or {}}


class FakeMav:
    def __init__(self):
        self.commands = []

    def command_long_send(self, *args):
        self.commands.append(args)


class FakeConnection:
    def __init__(self):
        self.master = type("Master", (), {"mav": FakeMav(), "target_system": 1, "target_component": 1})()


class FakeDirectPayload:
    def __init__(self):
        self.calls = []
        self.record_mode = 1
        self.recording = False

    def is_connected(self):
        return True

    def take_photo(self):
        self.calls.append(("take_photo",))
        return {"success": True}

    def start_recording(self):
        self.calls.append(("start_recording",))
        self.recording = True
        return {"success": True}

    def stop_recording(self):
        self.calls.append(("stop_recording",))
        self.recording = False
        return {"success": True}

    def set_record_mode(self, mode):
        self.calls.append(("set_record_mode", int(mode)))
        self.record_mode = int(mode)
        return {"success": True}

    def get_record_mode(self):
        return {"success": True, "mode": self.record_mode}

    def is_recording(self):
        return self.recording

    def turn_to(self, yaw, pitch):
        self.calls.append(("turn_to", yaw, pitch))
        return {"success": True}

    def home(self):
        self.calls.append(("home",))
        return {"success": True}

    def move(self, yaw_speed, pitch_speed):
        self.calls.append(("move", yaw_speed, pitch_speed))
        return {"success": True}

    def stop(self):
        self.calls.append(("stop",))
        return {"success": True}

    def zoom_to(self, magnification):
        self.calls.append(("zoom_to", magnification))
        return {"success": True}

    def set_focus_mode(self, mode):
        self.calls.append(("set_focus_mode", int(mode)))
        return {"success": True}

    def set_image_color(self, image_type, enable_pip, ir_color):
        self.calls.append(("set_image_color", int(image_type), bool(enable_pip), int(ir_color)))
        return {"success": True}

    def focus_in(self, speed):
        self.calls.append(("focus_in", speed))
        return {"success": True}

    def focus_out(self, speed):
        self.calls.append(("focus_out", speed))
        return {"success": True}

    def stop_focus(self):
        self.calls.append(("stop_focus",))
        return {"success": True}

    def start_track(self, x, y, width, height, template_size=None, sensor=None):
        self.calls.append(("start_track", x, y, width, height))
        return {"success": True}

    def stop_track(self):
        self.calls.append(("stop_track",))
        return {"success": True}

    def send_protocol_frames(self, frames, timeout=1.0):
        hex_frames = [bytes(frame).hex(" ").upper() for frame in frames]
        self.calls.append(("send_protocol_frames", hex_frames, timeout))
        return {
            "success": True,
            "message": "协议帧全部发送",
            "frames": [{"success": True, "frame_hex": item} for item in hex_frames],
        }


class FakeRawProtocolPayload(FakeDirectPayload):
    def is_connected(self):
        return False


class FakeDisconnectedDirectPayload(FakeDirectPayload):
    def is_connected(self):
        return False


class FakeLazyConnectPayload(FakeDirectPayload):
    def __init__(self):
        super().__init__()
        self.connected = False

    def is_connected(self):
        return self.connected

    def ensure_connected_async(self, timeout=2.0, retry_interval=2.0):
        self.calls.append(("ensure_connected_async", timeout, retry_interval))
        return {
            "success": False,
            "message": "ViewLink 正在后台连接，请稍后重试",
            "stage": "connecting",
            "connecting": True,
        }


class FakeSlowGimbalControl(GimbalControl):
    def __init__(self):
        super().__init__(sdk_dir="/tmp", gimbal_ip="127.0.0.1", gimbal_port=37260)
        self.connect_started = threading.Event()
        self.connect_release = threading.Event()
        self.connect_calls = 0

    def initialize(self):
        self._initialized = True
        self._sdk_available = True
        return {"success": True, "message": "SDK 初始化成功"}

    def connect(self, timeout=2.0, **kwargs):
        self.connect_calls += 1
        self.connect_started.set()
        self.connect_release.wait(timeout=1.0)
        self._connected = True
        return {"success": True, "message": "成功连接到 127.0.0.1:37260"}


class FakeViewLinkLib:
    def __init__(self):
        self.register_dev_status_calls = 0
        self.connect_calls = 0

    def VLK_RegisterDevStatusCB(self, callback, user_param):
        self.register_dev_status_calls += 1

    def VLK_Connect(self, param, callback, user_param):
        self.connect_calls += 1
        return 0

    def VLK_IsConnected(self):
        return 1


class FakeFlightControl:
    def __init__(self, armed=False):
        self.armed = armed

    def is_armed_status(self):
        return self.armed


class PayloadStateConsistencyTest(unittest.TestCase):
    def setUp(self):
        self._config = deepcopy(Config._data)

    def tearDown(self):
        Config._data = self._config

    def test_camera_prefers_direct_payload_sdk_when_connected(self):
        camera = CameraController()
        connection = FakeConnection()
        direct_payload = FakeDirectPayload()
        camera.set_mavlink_connection(connection)
        camera.set_gimbal_control(direct_payload)

        reply = camera.handle_camera_photo_take(message("camera_photo_take"))

        self.assertEqual(reply["data"]["result"], 0)
        self.assertEqual(direct_payload.calls, [("take_photo",)])
        self.assertEqual(connection.master.mav.commands, [])

    def test_camera_photo_take_applies_viewlink_camera_type_before_capture(self):
        camera = CameraController()
        direct_payload = FakeDirectPayload()
        camera.set_gimbal_control(direct_payload)

        reply = camera.handle_camera_photo_take(message("camera_photo_take", {
            "camera_index": 0,
            "camera_type": "ir",
        }))

        self.assertEqual(reply["data"]["result"], 0)
        self.assertEqual(reply["data"]["camera_type"], "ir")
        self.assertEqual(reply["data"]["camera_type_result"]["image_type"], 2)
        self.assertEqual(direct_payload.calls, [
            ("set_image_color", 2, False, 0),
            ("take_photo",),
        ])

    def test_camera_photo_take_mavlink_fallback_does_not_require_viewlink_result(self):
        camera = CameraController()
        connection = FakeConnection()
        camera.set_mavlink_connection(connection)

        reply = camera.handle_camera_photo_take(message("camera_photo_take", {
            "camera_index": 0,
        }))

        self.assertEqual(reply["data"]["result"], 0)
        self.assertEqual(reply["data"]["photo_source"], "mavlink")
        self.assertEqual(len(connection.master.mav.commands), 1)

    def test_camera_recording_prefers_direct_payload_sdk_when_connected(self):
        camera = CameraController()
        connection = FakeConnection()
        direct_payload = FakeDirectPayload()
        camera.set_mavlink_connection(connection)
        camera.set_gimbal_control(direct_payload)

        started = camera.handle_camera_recording_start(message("camera_recording_start"))
        stopped = camera.handle_camera_recording_stop(message("camera_recording_stop"))

        self.assertEqual(started["data"]["result"], 0)
        self.assertEqual(stopped["data"]["result"], 0)
        self.assertEqual(direct_payload.calls, [("set_record_mode", 2), ("start_recording",), ("stop_recording",)])
        self.assertEqual(connection.master.mav.commands, [])

    def test_camera_mode_switch_maps_photo_mode_to_viewlink_record_mode(self):
        camera = CameraController()
        direct_payload = FakeDirectPayload()
        camera.set_gimbal_control(direct_payload)

        reply = camera.handle_camera_mode_switch(message("camera_mode_switch", {"camera_mode": 0}))

        self.assertEqual(reply["data"]["result"], 0)
        self.assertEqual(reply["data"]["mode"], "photo")
        self.assertEqual(reply["data"]["viewlink_result"]["success"], True)
        self.assertEqual(direct_payload.calls, [("set_record_mode", 1)])

    def test_aircraft_osd_cameras_use_real_payload_readback(self):
        camera = CameraController()
        direct_payload = FakeDirectPayload()
        camera.set_gimbal_control(direct_payload)

        mode_reply = camera.handle_camera_mode_switch(message("camera_mode_switch", {"camera_mode": 1}))
        zoom_reply = camera.handle_camera_focal_length_set(
            message("camera_focal_length_set", {"zoom_factor": 3.5})
        )

        cameras = camera.build_aircraft_osd_cameras()

        self.assertEqual(mode_reply["data"]["result"], 0)
        self.assertEqual(zoom_reply["data"]["result"], 0)
        self.assertEqual(cameras[0]["payload_index"], "52-0-0")
        self.assertEqual(cameras[0]["camera_mode"], 1)
        self.assertEqual(cameras[0]["recording_state"], 0)
        self.assertEqual(cameras[0]["photo_state"], 0)
        self.assertEqual(cameras[0]["zoom_factor"], 3.5)
        self.assertNotIn("remain_photo_num", cameras[0])
        self.assertNotIn("remain_record_duration", cameras[0])

    def test_aircraft_osd_cameras_skip_disconnected_payload(self):
        class DisconnectedPayload(FakeDirectPayload):
            def is_connected(self):
                return False

        camera = CameraController()
        camera.set_gimbal_control(DisconnectedPayload())

        self.assertEqual(camera.build_aircraft_osd_cameras(), [])

    def test_camera_photo_session_uses_viewlink_photograph_not_live_frame_capture(self):
        camera = CameraController()
        direct_payload = FakeDirectPayload()
        camera.set_gimbal_control(direct_payload)

        reply = camera._start_photo(message("camera_photo_take", {"count": 1, "interval": 0.1}))
        thread = camera._photo_session_thread
        if thread:
            thread.join(timeout=2.0)

        self.assertEqual(reply["data"]["result"], 0)
        self.assertEqual(reply["data"]["photo_source"], "viewlink_sdk")
        self.assertEqual(direct_payload.calls, [("set_record_mode", 1), ("take_photo",)])

    def test_gimbal_prefers_direct_payload_sdk_when_connected(self):
        gimbal = GimbalController()
        connection = FakeConnection()
        direct_payload = FakeDirectPayload()
        events = []
        gimbal.set_mavlink_connection(connection)
        gimbal.set_gimbal_control(direct_payload)
        gimbal.set_event_callback(events.append)

        reply = gimbal.handle_gimbal_rotate(message("gimbal_rotate", {"pitch": -30, "yaw": 45}))

        self.assertEqual(reply["data"]["result"], 0)
        self.assertEqual(direct_payload.calls, [("turn_to", 45, -30)])
        self.assertEqual(connection.master.mav.commands, [])
        self.assertEqual(events[-1]["method"], "payload_status_notify")
        self.assertEqual(events[-1]["data"]["payloads"][0]["gimbal"]["pitch"], -30)
        self.assertEqual(events[-1]["data"]["payloads"][0]["gimbal"]["yaw"], 45)

    def test_gimbal_reset_prefers_direct_payload_sdk_when_connected(self):
        gimbal = GimbalController()
        connection = FakeConnection()
        direct_payload = FakeDirectPayload()
        gimbal.set_mavlink_connection(connection)
        gimbal.set_gimbal_control(direct_payload)

        reply = gimbal.handle_gimbal_reset(message("gimbal_reset"))

        self.assertEqual(reply["data"]["result"], 0)
        self.assertEqual(direct_payload.calls, [("home",)])
        self.assertEqual(connection.master.mav.commands, [])

    def test_camera_screen_drag_uses_gimbal_viewlink_move(self):
        gimbal = GimbalController()
        direct_payload = FakeDirectPayload()
        events = []
        gimbal.set_gimbal_control(direct_payload)
        gimbal.set_event_callback(events.append)

        reply = gimbal.handle_camera_screen_drag(message("camera_screen_drag", {
            "payload_index": "52-0-0",
            "locked": True,
            "pitch_speed": 1.5,
            "yaw_speed": -2.0,
        }))

        self.assertEqual(reply["data"]["result"], 0)
        self.assertEqual(direct_payload.calls, [("move", -200, 150)])
        self.assertEqual(events[-1]["method"], "payload_status_notify")
        self.assertEqual(events[-1]["data"]["payloads"][0]["last_control"]["method"], "camera_screen_drag")

    def test_camera_screen_drag_accepts_params_and_camel_case_speed(self):
        gimbal = GimbalController()
        direct_payload = FakeDirectPayload()
        gimbal.set_gimbal_control(direct_payload)

        reply = gimbal.handle_camera_screen_drag(message("camera_screen_drag", {
            "params": {
                "pitchSpeed": -1.25,
                "yawSpeed": 2.5,
            },
        }))

        self.assertEqual(reply["data"]["result"], 0)
        self.assertEqual(direct_payload.calls, [("move", 250, -125)])

    def test_gimbal_rotate_speed_fields_use_viewlink_move(self):
        gimbal = GimbalController()
        direct_payload = FakeDirectPayload()
        gimbal.set_gimbal_control(direct_payload)

        reply = gimbal.handle_gimbal_rotate(message("gimbal_rotate", {
            "params": {
                "pitchSpeed": 1.5,
                "yawSpeed": -2.0,
            },
        }))

        self.assertEqual(reply["data"]["result"], 0)
        self.assertEqual(direct_payload.calls, [("move", -200, 150)])

    def test_camera_screen_drag_starts_viewlink_background_connect_when_disconnected(self):
        gimbal = GimbalController()
        direct_payload = FakeLazyConnectPayload()
        gimbal.set_gimbal_control(direct_payload)

        reply = gimbal.handle_camera_screen_drag(message("camera_screen_drag", {
            "pitch_speed": 1.0,
            "yaw_speed": 0.5,
        }))

        self.assertEqual(reply["data"]["result"], DOCK_SYSTEM_ERROR)
        self.assertEqual(direct_payload.calls, [
            ("ensure_connected_async", 2.0, 2.0),
        ])
        self.assertEqual(reply["data"]["viewlink_result"]["stage"], "connecting")
        self.assertTrue(reply["data"]["viewlink_result"]["connecting"])

    def test_gimbal_rotate_zero_speed_stops_viewlink(self):
        gimbal = GimbalController()
        direct_payload = FakeDirectPayload()
        gimbal.set_gimbal_control(direct_payload)

        reply = gimbal.handle_gimbal_rotate(message("gimbal_rotate", {
            "pitch_speed": 0.0,
            "yaw_speed": 0.0,
        }))

        self.assertEqual(reply["data"]["result"], 0)
        self.assertEqual(direct_payload.calls, [("stop",)])

    def test_gimbal_rotate_speed_does_not_fallback_to_mavlink_when_viewlink_disconnected(self):
        gimbal = GimbalController()
        connection = FakeConnection()
        direct_payload = FakeDisconnectedDirectPayload()
        gimbal.set_mavlink_connection(connection)
        gimbal.set_gimbal_control(direct_payload)

        reply = gimbal.handle_gimbal_rotate(message("gimbal_rotate", {
            "pitch_speed": 1.0,
            "yaw_speed": 1.0,
        }))

        self.assertEqual(reply["data"]["result"], DOCK_SYSTEM_ERROR)
        self.assertEqual(direct_payload.calls, [])
        self.assertEqual(connection.master.mav.commands, [])

    def test_gimbal_rotate_angle_does_not_fallback_when_viewlink_config_disables_flight_controller(self):
        Config.merge({"payload": {"flight_controller_fallback_enabled": False}})
        gimbal = GimbalController()
        connection = FakeConnection()
        direct_payload = FakeDisconnectedDirectPayload()
        gimbal.set_mavlink_connection(connection)
        gimbal.set_gimbal_control(direct_payload)

        reply = gimbal.handle_gimbal_rotate(message("gimbal_rotate", {"pitch": -30, "yaw": 45}))

        self.assertEqual(reply["data"]["result"], DOCK_SYSTEM_ERROR)
        self.assertEqual(direct_payload.calls, [])
        self.assertEqual(connection.master.mav.commands, [])

    def test_camera_aim_uses_viewlink_track_target_position(self):
        gimbal = GimbalController()
        direct_payload = FakeDirectPayload()
        gimbal.set_gimbal_control(direct_payload)

        reply = gimbal.handle_camera_aim(message("camera_aim", {
            "payload_index": "52-0-0",
            "locked": True,
            "x": 0.25,
            "y": 0.75,
            "frame_size": {"width": 1280, "height": 720},
        }))

        self.assertEqual(reply["data"]["result"], 0)
        self.assertEqual(direct_payload.calls, [("start_track", 320, 540, 1280, 720)])

    def test_camera_frame_zoom_centers_selection_before_viewlink_zoom(self):
        camera = CameraController()
        direct_payload = FakeDirectPayload()
        camera.set_gimbal_control(direct_payload)

        reply = camera.handle_camera_frame_zoom(message("camera_frame_zoom", {
            "payload_index": "52-0-0",
            "x": 384,
            "y": 216,
            "width": 640,
            "height": 360,
            "zoom_ratio": 4.0,
            "normalized": {"x": 0.2, "y": 0.2, "width": 0.333, "height": 0.333},
        }))

        self.assertEqual(reply["data"]["result"], 0)
        self.assertEqual(direct_payload.calls, [
            ("start_track", 703, 396, 1920, 1080),
            ("zoom_to", 4.0),
        ])
        self.assertEqual(reply["data"]["zoom_ratio"], 4.0)
        self.assertEqual(reply["data"]["center_result"]["method"], "target_track")
        self.assertEqual(reply["data"]["center_result"]["target"], {"x": 703, "y": 396})
        self.assertEqual(reply["data"]["viewlink_result"]["success"], True)
        self.assertEqual(
            [step["step"] for step in reply["data"]["software_pipeline"]],
            [
                "normalize_selection",
                "compute_center_offset",
                "gimbal_center_track",
                "camera_zoom_to",
            ],
        )

    def test_camera_frame_zoom_starts_viewlink_background_connect_when_disconnected(self):
        camera = CameraController()
        direct_payload = FakeLazyConnectPayload()
        camera.set_gimbal_control(direct_payload)

        reply = camera.handle_camera_frame_zoom(message("camera_frame_zoom", {
            "payload_index": "52-0-0",
            "x": 384,
            "y": 216,
            "width": 640,
            "height": 360,
            "zoom_ratio": 4.0,
            "normalized": {"x": 0.2, "y": 0.2, "width": 0.333, "height": 0.333},
        }))

        self.assertEqual(reply["data"]["result"], DOCK_SYSTEM_ERROR)
        self.assertEqual(direct_payload.calls, [
            ("ensure_connected_async", 2.0, 2.0),
        ])
        self.assertEqual(reply["data"]["viewlink_result"]["stage"], "connecting")

    def test_viewlink_background_connect_deduplicates_repeated_attempts(self):
        direct_payload = FakeSlowGimbalControl()

        first = direct_payload.ensure_connected_async(timeout=2.0, retry_interval=2.0)
        direct_payload.connect_started.wait(timeout=1.0)
        second = direct_payload.ensure_connected_async(timeout=2.0, retry_interval=2.0)
        direct_payload.connect_release.set()
        if direct_payload._connect_thread:
            direct_payload._connect_thread.join(timeout=2.0)

        self.assertEqual(first["stage"], "connecting")
        self.assertEqual(second["stage"], "connecting")
        self.assertEqual(direct_payload.connect_calls, 1)
        self.assertTrue(direct_payload.is_connected())

    def test_viewlink_connect_skips_device_status_callback_by_default(self):
        direct_payload = GimbalControl("/tmp", "127.0.0.1", 37260)
        fake_lib = FakeViewLinkLib()
        direct_payload._initialized = True
        direct_payload._lib = fake_lib

        result = direct_payload.connect(timeout=0.1)

        self.assertTrue(result["success"])
        self.assertEqual(fake_lib.connect_calls, 1)
        self.assertEqual(fake_lib.register_dev_status_calls, 0)

    def test_viewlink_connect_can_enable_device_status_callback_explicitly(self):
        direct_payload = GimbalControl(
            "/tmp",
            "127.0.0.1",
            37260,
            register_dev_status_callback=True,
        )
        fake_lib = FakeViewLinkLib()
        direct_payload._initialized = True
        direct_payload._lib = fake_lib

        result = direct_payload.connect(timeout=0.1)

        self.assertTrue(result["success"])
        self.assertEqual(fake_lib.connect_calls, 1)
        self.assertEqual(fake_lib.register_dev_status_calls, 1)

    def test_camera_frame_zoom_skips_center_move_when_selection_aligned(self):
        camera = CameraController()
        direct_payload = FakeDirectPayload()
        camera.set_gimbal_control(direct_payload)

        reply = camera.handle_camera_frame_zoom(message("camera_frame_zoom", {
            "payload_index": "52-0-0",
            "locked": True,
            "x": 0.4,
            "y": 0.4,
            "width": 0.2,
            "height": 0.2,
            "frame_size": {"width": 1280, "height": 720},
            "zoom_ratio": 2.0,
        }))

        self.assertEqual(reply["data"]["result"], 0)
        self.assertEqual(direct_payload.calls, [
            ("zoom_to", 2.0),
        ])
        self.assertEqual(reply["data"]["center_result"]["message"], "frame center already aligned")

    def test_camera_frame_zoom_accepts_normalized_box_payload(self):
        camera = CameraController()
        direct_payload = FakeDirectPayload()
        camera.set_gimbal_control(direct_payload)

        reply = camera.handle_camera_frame_zoom(message("camera_frame_zoom", {
            "payload_index": "52-0-0",
            "camera_type": "zoom",
            "normalized_box": {"x": 0.35, "y": 0.30, "width": 0.20, "height": 0.20},
            "zoom_ratio": 4.0,
        }))

        self.assertEqual(reply["data"]["result"], 0)
        self.assertEqual(reply["data"]["normalized"], {
            "x": 0.35,
            "y": 0.30,
            "width": 0.20,
            "height": 0.20,
        })
        self.assertEqual(reply["data"]["x"], 672)
        self.assertEqual(reply["data"]["y"], 324)
        self.assertEqual(reply["data"]["width"], 384)
        self.assertEqual(reply["data"]["height"], 216)
        self.assertEqual(direct_payload.calls, [
            ("start_track", 864, 432, 1920, 1080),
            ("zoom_to", 4.0),
        ])

    def test_camera_frame_zoom_infers_zoom_ratio_from_selection_size(self):
        camera = CameraController()
        direct_payload = FakeDirectPayload()
        camera.set_gimbal_control(direct_payload)

        reply = camera.handle_camera_frame_zoom(message("camera_frame_zoom", {
            "payload_index": "52-0-0",
            "x": 0.35,
            "y": 0.35,
            "width": 0.25,
            "height": 0.25,
        }))

        self.assertEqual(reply["data"]["result"], 0)
        self.assertEqual(reply["data"]["zoom_ratio"], 4.0)
        self.assertEqual(direct_payload.calls[-1], ("zoom_to", 4.0))
        self.assertEqual(reply["data"]["software_pipeline"][1]["zoom_ratio_source"], "selection_box")

    def test_media_metadata_uses_real_file_size_and_resolution(self):
        with tempfile.TemporaryDirectory() as tmp:
            file_path = os.path.join(tmp, "capture.png")
            with open(file_path, "wb") as handle:
                handle.write(
                    b"\x89PNG\r\n\x1a\n"
                    b"\x00\x00\x00\rIHDR"
                    + (640).to_bytes(4, "big")
                    + (480).to_bytes(4, "big")
                    + b"\x08\x02\x00\x00\x00"
                )

            camera = CameraController()
            metadata, media_data = camera._build_media_metadata_record("photo", file_path=file_path)
            expected_size = os.path.getsize(file_path)

            self.assertEqual(media_data["file_name"], "capture.png")
            self.assertEqual(media_data["file_path"], file_path)
            self.assertEqual(media_data["file_size"], metadata["file_size"])
            self.assertEqual(media_data["file_size"], expected_size)
            self.assertEqual(media_data["resolution"], "640x480")
            self.assertEqual(metadata["resolution"], "640x480")

    def test_camera_photo_stop_returns_failure_when_viewlink_thread_stays_alive(self):
        class StuckThread:
            def __init__(self):
                self.join_timeout = None

            def is_alive(self):
                return True

            def join(self, timeout=None):
                self.join_timeout = timeout

        camera = CameraController()
        thread = StuckThread()
        camera._photo_session_thread = thread

        reply = camera.handle_camera_photo_stop(message("camera_photo_stop", {
            "payload_index": "52-0-0",
        }))

        self.assertEqual(reply["data"]["result"], DOCK_SYSTEM_ERROR)
        self.assertTrue(camera._photo_session_stop.is_set())
        self.assertEqual(thread.join_timeout, 2.0)
        self.assertTrue(reply["data"]["running"])
        self.assertFalse(reply["data"]["stopped"])

    def test_camera_zoom_and_focus_use_direct_payload_sdk_when_connected(self):
        camera = CameraController()
        direct_payload = FakeDirectPayload()
        camera.set_gimbal_control(direct_payload)

        zoom = camera.handle_camera_focal_length_set(message("camera_focal_length_set", {"zoom_factor": 3.5}))
        focus_mode = camera.handle_camera_focus_mode_set(message("camera_focus_mode_set", {"mode": "manual_focus"}))
        focus_value = camera.handle_camera_focus_value_set(message("camera_focus_value_set", {
            "focus_distance": -1,
            "focus_speed": 2,
            "focus_duration": 0,
        }))

        self.assertEqual(zoom["data"]["result"], 0)
        self.assertEqual(focus_mode["data"]["result"], 0)
        self.assertEqual(focus_value["data"]["result"], 0)
        self.assertEqual(direct_payload.calls, [
            ("zoom_to", 3.5),
            ("set_focus_mode", 1),
            ("focus_in", 2),
            ("stop_focus",),
        ])

    def test_camera_focal_length_set_keeps_legacy_zoom_ratio_compatibility(self):
        camera = CameraController()
        direct_payload = FakeDirectPayload()
        camera.set_gimbal_control(direct_payload)

        zoom = camera.handle_camera_focal_length_set(message("camera_focal_length_set", {"zoom_ratio": 2.5}))

        self.assertEqual(zoom["data"]["result"], 0)
        self.assertEqual(direct_payload.calls, [("zoom_to", 2.5)])

    def test_camera_focal_length_set_mapping_uses_cloud_zoom_factor(self):
        mapping = build_camera_setting("camera_focal_length_set", {
            "payload_index": "39-0-7",
            "camera_type": "zoom",
            "zoom_factor": 5,
        })

        self.assertEqual(mapping["canonical_method"], "zoom")
        self.assertEqual(mapping["setting"], {
            "payload_index": "39-0-7",
            "camera_type": "zoom",
            "zoom_factor": 5,
        })

    def test_ir_display_mode_set_uses_viewlink_image_color(self):
        gimbal = GimbalController()
        direct_payload = FakeDirectPayload()
        events = []
        gimbal.set_gimbal_control(direct_payload)
        gimbal.set_event_callback(events.append)

        reply = gimbal.handle_ir_display_mode_set(message("ir_display_mode_set", {
            "mode": "ir_visible",
            "palette": "black_hot",
        }))

        self.assertEqual(reply["data"]["result"], 0)
        self.assertEqual(reply["data"]["mode"], "ir_visible")
        self.assertEqual(reply["data"]["image_type"], 2)
        self.assertEqual(reply["data"]["enable_pip"], True)
        self.assertEqual(reply["data"]["ir_color"], 1)
        self.assertEqual(direct_payload.calls, [("set_image_color", 2, True, 1)])
        self.assertEqual(events[-1]["data"]["payloads"][0]["last_control"]["method"], "ir_display_mode_set")

    def test_screen_split_uses_sdk_when_connected(self):
        gimbal = GimbalController()
        direct_payload = FakeDirectPayload()
        gimbal.set_gimbal_control(direct_payload)

        reply = gimbal.handle_camera_screen_split(message("camera_screen_split", {
            "payload_index": "52-0-0",
            "enable": True,
            "mode": "visible_ir",
        }))

        self.assertEqual(reply["data"]["result"], 0)
        self.assertEqual(reply["data"]["mode"], "visible_ir")
        self.assertEqual(reply["data"]["image_type"], 0)
        self.assertEqual(reply["data"]["enable_pip"], True)
        self.assertEqual(direct_payload.calls, [("set_image_color", 0, True, 0)])

    def test_screen_split_falls_back_to_raw_viewlink_protocol(self):
        gimbal = GimbalController()
        raw_payload = FakeRawProtocolPayload()
        gimbal.set_gimbal_control(raw_payload)

        reply = gimbal.handle_camera_screen_split(message("camera_screen_split", {
            "payload_index": "52-0-0",
            "enable": True,
        }))

        self.assertEqual(reply["data"]["result"], 0)
        self.assertEqual(reply["data"]["viewlink_result"]["version"], "3.4.9")
        self.assertEqual(raw_payload.calls[0][0], "send_protocol_frames")
        self.assertEqual(raw_payload.calls[0][1][0], reply["data"]["viewlink_result"]["frame_hex"][0])

    def test_ir_metering_point_and_area_send_raw_protocol_frames(self):
        gimbal = GimbalController()
        raw_payload = FakeRawProtocolPayload()
        gimbal.set_gimbal_control(raw_payload)

        point = gimbal.handle_ir_metering_point_set(message("ir_metering_point_set", {
            "payload_index": "52-0-0",
            "x": 0.75,
            "y": 0.25,
            "frame_size": {"width": 1280, "height": 720},
        }))
        area = gimbal.handle_ir_metering_area_set(message("ir_metering_area_set", {
            "payload_index": "52-0-0",
            "x": 0.25,
            "y": 0.25,
            "width": 0.5,
            "height": 0.5,
            "frame_size": {"width": 1280, "height": 720},
        }))

        self.assertEqual(point["data"]["result"], 0)
        self.assertEqual(point["data"]["protocol_point"], {"x": 480, "y": -270})
        self.assertEqual(len(point["data"]["viewlink_result"]["frame_hex"]), 2)
        self.assertEqual(area["data"]["result"], 0)
        self.assertEqual(area["data"]["protocol_area"], {
            "left": -480,
            "top": -270,
            "right": 480,
            "bottom": 270,
        })
        self.assertEqual(len(area["data"]["viewlink_result"]["frame_hex"]), 3)
        self.assertEqual([call[0] for call in raw_payload.calls], ["send_protocol_frames", "send_protocol_frames"])

    def test_ir_metering_mode_builds_temperature_protocol_frame(self):
        gimbal = GimbalController()
        raw_payload = FakeRawProtocolPayload()
        gimbal.set_gimbal_control(raw_payload)

        reply = gimbal.handle_ir_metering_mode_set(message("ir_metering_mode_set", {
            "mode": "periodic",
            "interval_frames": 4,
        }))

        self.assertEqual(reply["data"]["result"], 0)
        self.assertEqual(reply["data"]["interval_frames"], 4)
        self.assertIn("2C 1E 00 04", reply["data"]["viewlink_result"]["frame_hex"][0])

    def test_failed_camera_record_start_does_not_set_recording(self):
        camera = CameraController()

        reply = camera.handle_camera_recording_start(message("camera_recording_start"))

        self.assertEqual(reply["data"]["result"], DOCK_SYSTEM_ERROR)
        self.assertFalse(camera._is_recording)

    def test_camera_record_state_changes_only_after_commands_are_sent(self):
        camera = CameraController()
        connection = FakeConnection()
        camera.set_mavlink_connection(connection)

        started = camera.handle_camera_recording_start(message("camera_recording_start"))
        stopped = camera.handle_camera_recording_stop(message("camera_recording_stop"))

        self.assertEqual(started["data"]["result"], 0)
        self.assertEqual(stopped["data"]["result"], 0)
        self.assertFalse(camera._is_recording)
        self.assertFalse(stopped["data"]["recording"])
        self.assertEqual(len(connection.master.mav.commands), 2)

    def test_mapping_camera_disarmed_uses_gateway_direct_cache(self):
        with tempfile.TemporaryDirectory() as tmp:
            Config.merge({
                "media": {"storage_path": tmp},
                "payload": {
                    "devices": {
                        "mapping_camera": {
                            "channels": {
                                "post_landing_raw_data": {"endpoint": "disabled://camera/DCIM"}
                            }
                        }
                    }
                },
            })
            camera = CameraController()
            connection = FakeConnection()
            camera.set_mavlink_connection(connection)
            camera.set_flight_control(FakeFlightControl(armed=False))

            reply = camera.handle_camera_photo_take(message("camera_photo_take", {
                "camera_index": "52-0-1",
            }))

            self.assertEqual(reply["data"]["result"], 0)
            self.assertEqual(reply["data"]["control_route"], "gateway_direct")
            self.assertFalse(reply["data"]["armed"])
            self.assertEqual(connection.master.mav.commands, [])
            self.assertTrue(os.path.exists(reply["data"]["local_cache"]["file_path"]))

    def test_mapping_camera_armed_uses_flight_controller_ttl_and_cache(self):
        with tempfile.TemporaryDirectory() as tmp:
            Config.merge({
                "media": {"storage_path": tmp},
                "payload": {
                    "devices": {
                        "mapping_camera": {
                            "channels": {
                                "post_landing_raw_data": {"endpoint": "disabled://camera/DCIM"}
                            }
                        }
                    }
                },
            })
            camera = CameraController()
            connection = FakeConnection()
            camera.set_mapping_camera_flight_controller_connection(connection)
            camera.set_flight_control(FakeFlightControl(armed=True))

            reply = camera.handle_camera_photo_take(message("camera_photo_take", {
                "camera_index": "52-0-1",
            }))

            self.assertEqual(reply["data"]["result"], 0)
            self.assertEqual(reply["data"]["control_route"], "flight_controller_ttl")
            self.assertTrue(reply["data"]["armed"])
            self.assertEqual(len(connection.master.mav.commands), 1)
            self.assertTrue(os.path.exists(reply["data"]["local_cache"]["file_path"]))

    def test_mapping_camera_cache_downloads_latest_ftp_photo(self):
        class FakeFTP:
            def __init__(self, host, timeout):
                self.cwd_path = "/"

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def login(self, username, password):
                return None

            def pwd(self):
                return self.cwd_path

            def cwd(self, path):
                directories = {"/", "/DCIM", "/DCIM/00001", "/DCIM/00002"}
                target = path if path.startswith("/") else f"{self.cwd_path.rstrip('/')}/{path}"
                if target in directories:
                    self.cwd_path = target
                    return None
                raise OSError("not a directory")

            def nlst(self, path=None):
                path = path or self.cwd_path
                if path == "/DCIM":
                    return ["/DCIM/00001", "/DCIM/00002"]
                if path == "/DCIM/00002":
                    return ["/DCIM/00002/Riebo000001.jpg", "/DCIM/00002/Riebo000003.jpg"]
                return []

            def size(self, path):
                return len(b"real-jpeg")

            def retrbinary(self, command, callback):
                callback(b"real-jpeg")

        from src.payload import mapping_camera_ftp

        old_ftp = mapping_camera_ftp.FTP
        mapping_camera_ftp.FTP = FakeFTP
        try:
            with tempfile.TemporaryDirectory() as tmp:
                Config.merge({
                    "media": {"storage_path": tmp},
                    "payload": {
                        "devices": {
                            "mapping_camera": {
                                "channels": {
                                    "post_landing_raw_data": {
                                        "endpoint": "ftp://192.168.5.74/DCIM",
                                        "timeout_seconds": 1,
                                        "download_jobs": 4,
                                    }
                                }
                            }
                        }
                    },
                })
                camera = CameraController()

                cached = camera._cache_photo_locally({}, "gateway_direct")
                with open(cached["file_path"], "rb") as handle:
                    cached_bytes = handle.read()
        finally:
            mapping_camera_ftp.FTP = old_ftp

        self.assertEqual(cached["file_name"], "Riebo000003.jpg")
        self.assertEqual(cached_bytes, b"real-jpeg")
        self.assertEqual(cached["remote_path"], "/DCIM/00002/Riebo000003.jpg")
        self.assertEqual(cached["download_jobs"], 4)

    def test_mapping_camera_cache_downloads_requested_ftp_photo_path(self):
        class FakeFTP:
            def __init__(self, host, timeout):
                self.cwd_path = "/"

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def login(self, username, password):
                return None

            def pwd(self):
                return self.cwd_path

            def cwd(self, path):
                directories = {"/", "/DCIM", "/DCIM/00002"}
                target = path if path.startswith("/") else f"{self.cwd_path.rstrip('/')}/{path}"
                if target in directories:
                    self.cwd_path = target
                    return None
                raise OSError("not a directory")

            def nlst(self, path=None):
                path = path or self.cwd_path
                if path == "/DCIM":
                    return ["/DCIM/00002"]
                if path == "/DCIM/00002":
                    return [
                        "/DCIM/00002/Riebo000003.JPG",
                        "/DCIM/00002/Riebo000004.JPG",
                    ]
                return []

            def size(self, path):
                return len(path.encode("utf-8"))

            def retrbinary(self, command, callback):
                callback(command.encode("utf-8"))

        from src.payload import mapping_camera_ftp

        old_ftp = mapping_camera_ftp.FTP
        mapping_camera_ftp.FTP = FakeFTP
        try:
            with tempfile.TemporaryDirectory() as tmp:
                Config.merge({
                    "media": {"storage_path": tmp},
                    "payload": {
                        "devices": {
                            "mapping_camera": {
                                "channels": {
                                    "post_landing_raw_data": {
                                        "endpoint": "ftp://192.168.5.74/DCIM",
                                        "timeout_seconds": 1,
                                        "download_jobs": 4,
                                    }
                                }
                            }
                        }
                    },
                })
                camera = CameraController()

                cached = camera._cache_photo_locally(
                    {"file_path": r"/data/media\Riebo000004.JPG"},
                    "gateway_direct",
                )
                with open(cached["file_path"], "rb") as handle:
                    cached_bytes = handle.read()
        finally:
            mapping_camera_ftp.FTP = old_ftp

        self.assertEqual(cached["file_name"], "Riebo000004.JPG")
        self.assertIn(b"Riebo000004.JPG", cached_bytes)
        self.assertEqual(cached["remote_path"], "/DCIM/00002/Riebo000004.JPG")

    def test_services_payload_methods_do_not_depend_on_drc_placeholders(self):
        camera = CameraController()
        gimbal = GimbalController()

        checks = [
            camera.handle_camera_frame_zoom(message("camera_frame_zoom", {
                "payload_index": "52-0-0",
                "x": 0.5,
                "y": 0.5,
                "width": 0.2,
                "height": 0.2,
            })),
            camera.handle_camera_mode_switch(message("camera_mode_switch", {"camera_mode": 0})),
            camera.handle_video_storage_set(message("video_storage_set", {"video_storage_settings": []})),
            camera.handle_camera_exposure_set(message("camera_exposure_set", {"exposure_value": 1})),
            gimbal.handle_camera_aim(message("camera_aim", {"payload_index": "52-0-0", "locked": False})),
            gimbal.handle_camera_screen_split(message("camera_screen_split", {"payload_index": "52-0-0", "enable": True})),
            gimbal.handle_ir_metering_point_set(message("ir_metering_point_set", {"x": 0.4, "y": 0.6})),
            gimbal.handle_ir_metering_area_set(message("ir_metering_area_set", {"width": 0.2, "height": 0.3})),
        ]

        for reply in checks:
            self.assertEqual(reply["data"]["result"], 0)

    def test_mapping_camera_payload_state_exposes_gateway_binding(self):
        Config.merge({
            "media": {
                "video_sources": {
                    "mapping_camera": "rtsp://192.168.5.74:8554/LIVE",
                }
            }
        })
        camera = CameraController()
        camera.gateway_sn = "EDGEGW001"
        camera.set_flight_control(FakeFlightControl(armed=False))
        camera._last_mapping_camera_cache = {"file_path": "/data/media/Riebo000003.jpg"}
        old_probe = CameraController._tcp_endpoint_reachable
        CameraController._tcp_endpoint_reachable = staticmethod(
            lambda endpoint, default_port, timeout=0.3: str(endpoint).startswith("rtsp://")
        )
        try:
            state = camera.build_mapping_camera_payload_state()
        finally:
            CameraController._tcp_endpoint_reachable = old_probe

        self.assertEqual(state["connection_state"], "connected")
        self.assertEqual(state["payload_type"], "mapping_camera")
        self.assertEqual(state["payload_index"], "52-0-1")
        self.assertEqual(state["control_route"], "gateway_direct")
        self.assertEqual(state["video"]["video_id"], "EDGEGW001/52-0-1/mapping-0")
        self.assertTrue(state["video"]["reachable"])
        self.assertFalse(state["data_link"]["reachable"])
        self.assertEqual(state["last_photo_cache"]["file_path"], "/data/media/Riebo000003.jpg")

    def test_spectral_camera_payload_state_exposes_ycam_gateway_binding(self):
        adapter = SpectralCameraAdapter(Config.get_payload_config()["devices"]["spectral_camera"])
        old_probe = SpectralCameraAdapter._tcp_host_reachable
        SpectralCameraAdapter._tcp_host_reachable = staticmethod(
            lambda host, port, timeout=0.3: host == "192.168.1.115" and port == 80
        )
        try:
            state = adapter.build_payload_state()
        finally:
            SpectralCameraAdapter._tcp_host_reachable = old_probe

        self.assertEqual(state["connection_state"], "connected")
        self.assertEqual(state["payload_type"], "spectral_camera")
        self.assertEqual(state["payload_index"], "52-0-2")
        self.assertEqual(state["host"], "192.168.1.115")
        self.assertEqual(state["control"]["command_port"], 23001)
        self.assertEqual(state["control"]["file_port"], 23002)
        self.assertEqual(state["control"]["reachable_ports"], [80])
        self.assertEqual(state["video"]["endpoint"], "rtp://0.0.0.0:23008")
        self.assertEqual(state["video"]["video_id"], "UNKNOWN/52-0-2/spectral-0")
        self.assertTrue(state["video"]["reachable"])
        self.assertEqual(state["data_link"]["listen_port"], 23002)

    def test_spectral_camera_photo_take_creates_return_media_cache(self):
        with tempfile.TemporaryDirectory() as tmp:
            Config.merge({"media": {"storage_path": tmp}})
            camera = CameraController()
            reply = camera.handle_camera_photo_take(message("camera_photo_take", {
                "payload_index": "52-0-2",
                "camera_type": "multispectral",
                "file_name": "spectral-test.jpg",
            }))

            media = reply["data"]["media"]
            self.assertEqual(reply["data"]["result"], 0)
            self.assertEqual(reply["data"]["payload_id"], "spectral_camera")
            self.assertEqual(reply["data"]["photo_source"], "ycam_sdk")
            self.assertEqual(media["payload_index"], "52-0-2")
            self.assertTrue(os.path.exists(media["file_path"]))
            self.assertEqual(os.path.basename(media["file_path"]), "spectral-test.jpg")
            self.assertEqual(camera._last_spectral_camera_cache["file_name"], "spectral-test.jpg")

    def test_failed_gimbal_commands_do_not_change_cached_pose_or_mode(self):
        gimbal = GimbalController()

        rotate = gimbal.handle_gimbal_rotate(message("gimbal_rotate", {"pitch": -30, "yaw": 45}))
        mode = gimbal.handle_set_gimbal_mode(message("set_gimbal_mode", {"mode": "FREE"}))
        reset = gimbal.handle_gimbal_reset(message("gimbal_reset"))

        self.assertEqual(rotate["data"]["result"], DOCK_SYSTEM_ERROR)
        self.assertEqual(mode["data"]["result"], DOCK_SYSTEM_ERROR)
        self.assertEqual(reset["data"]["result"], DOCK_SYSTEM_ERROR)
        self.assertEqual((gimbal._pitch, gimbal._roll, gimbal._yaw, gimbal._mode), (0.0, 0.0, 0.0, gimbal.MODE_FOLLOW))

    def test_successful_gimbal_rotate_updates_cached_pose(self):
        gimbal = GimbalController()
        connection = FakeConnection()
        gimbal.set_mavlink_connection(connection)

        reply = gimbal.handle_gimbal_rotate(message("gimbal_rotate", {"pitch": -30, "roll": 2, "yaw": 45}))

        self.assertEqual(reply["data"]["result"], 0)
        self.assertEqual((gimbal._pitch, gimbal._roll, gimbal._yaw), (-30, 2, 45))
        self.assertEqual(len(connection.master.mav.commands), 1)

    def test_gimbal_status_query_includes_payload_state_snapshot(self):
        gimbal = GimbalController()
        connection = FakeConnection()
        gimbal.set_mavlink_connection(connection)
        gimbal.handle_gimbal_rotate(message("gimbal_rotate", {"pitch": -25, "roll": 1, "yaw": 55}))

        reply = gimbal.handle_gimbal_status_query(message("gimbal_status_query"))

        payload = reply["data"]["payload"]
        self.assertEqual(payload["payload_type"], "gimbal")
        self.assertEqual(payload["payload_index"], "52-0-0")
        self.assertEqual(payload["type_subtype_gimbalindex"]["payload_index"], "52-0-0")
        self.assertEqual(payload["type_subtype_gimbalindex"]["gimbal_pitch"], -25.0)
        self.assertEqual(payload["type_subtype_gimbalindex"]["gimbal_yaw"], 55.0)
        self.assertEqual(payload["gimbal"]["pitch"], -25)
        self.assertEqual(payload["gimbal"]["roll"], 1)
        self.assertEqual(payload["gimbal"]["yaw"], 55)
        self.assertEqual(payload["gimbal"]["mode_text"], "FOLLOW")


if __name__ == "__main__":
    unittest.main()
