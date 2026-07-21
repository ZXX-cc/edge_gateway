import unittest
import sys
import types


if "src.database.connection" not in sys.modules:
    connection = types.ModuleType("src.database.connection")

    async def init_database_async():
        return None

    def get_async_session_local():
        return None

    connection.init_database_async = init_database_async
    connection.get_async_session_local = get_async_session_local
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
from src.utils.dji_error_codes import DOCK_SYSTEM_ERROR


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

    def is_connected(self):
        return True

    def take_photo(self):
        self.calls.append(("take_photo",))
        return {"success": True}

    def start_recording(self):
        self.calls.append(("start_recording",))
        return {"success": True}

    def stop_recording(self):
        self.calls.append(("stop_recording",))
        return {"success": True}

    def turn_to(self, yaw, pitch):
        self.calls.append(("turn_to", yaw, pitch))
        return {"success": True}

    def home(self):
        self.calls.append(("home",))
        return {"success": True}

    def zoom_to(self, magnification):
        self.calls.append(("zoom_to", magnification))
        return {"success": True}

    def set_focus_mode(self, mode):
        self.calls.append(("set_focus_mode", int(mode)))
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


class PayloadStateConsistencyTest(unittest.TestCase):
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

    def test_camera_recording_prefers_direct_payload_sdk_when_connected(self):
        camera = CameraController()
        connection = FakeConnection()
        direct_payload = FakeDirectPayload()
        camera.set_mavlink_connection(connection)
        camera.set_gimbal_control(direct_payload)

        started = camera.handle_start_record(message("camera_recording_start"))
        stopped = camera.handle_stop_record(message("camera_recording_stop"))

        self.assertEqual(started["data"]["result"], 0)
        self.assertEqual(stopped["data"]["result"], 0)
        self.assertEqual(direct_payload.calls, [("start_recording",), ("stop_recording",)])
        self.assertEqual(connection.master.mav.commands, [])

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

    def test_camera_zoom_and_focus_use_direct_payload_sdk_when_connected(self):
        camera = CameraController()
        direct_payload = FakeDirectPayload()
        camera.set_gimbal_control(direct_payload)

        zoom = camera.handle_camera_focal_length_set(message("camera_focal_length_set", {"zoom_ratio": 3.5}))
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

    def test_failed_camera_record_start_does_not_set_recording(self):
        camera = CameraController()

        reply = camera.handle_start_record(message("camera_recording_start"))

        self.assertEqual(reply["data"]["result"], DOCK_SYSTEM_ERROR)
        self.assertFalse(camera._is_recording)

    def test_camera_record_state_changes_only_after_commands_are_sent(self):
        camera = CameraController()
        connection = FakeConnection()
        camera.set_mavlink_connection(connection)

        started = camera.handle_start_record(message("camera_recording_start"))
        stopped = camera.handle_stop_record(message("camera_recording_stop"))

        self.assertEqual(started["data"]["result"], 0)
        self.assertEqual(stopped["data"]["result"], 0)
        self.assertFalse(camera._is_recording)
        self.assertFalse(stopped["data"]["recording"])
        self.assertEqual(len(connection.master.mav.commands), 2)

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
        self.assertEqual(payload["payload_index"], "0")
        self.assertEqual(payload["gimbal"]["pitch"], -25)
        self.assertEqual(payload["gimbal"]["roll"], 1)
        self.assertEqual(payload["gimbal"]["yaw"], 55)
        self.assertEqual(payload["gimbal"]["mode_text"], "FOLLOW")


if __name__ == "__main__":
    unittest.main()
