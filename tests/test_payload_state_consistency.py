import unittest

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
        gimbal.set_mavlink_connection(connection)
        gimbal.set_gimbal_control(direct_payload)

        reply = gimbal.handle_gimbal_rotate(message("gimbal_rotate", {"pitch": -30, "yaw": 45}))

        self.assertEqual(reply["data"]["result"], 0)
        self.assertEqual(direct_payload.calls, [("turn_to", 45, -30)])
        self.assertEqual(connection.master.mav.commands, [])

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


if __name__ == "__main__":
    unittest.main()
