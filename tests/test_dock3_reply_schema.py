import unittest

from src.cloud_api.dock3_reply_schema import sanitize_services_reply
from src.utils.dji_error_codes import DEVICE_STATE_UNSUPPORTED


class Dock3ReplySchemaTest(unittest.TestCase):
    def test_payload_failure_keeps_compact_output_reason(self):
        reply = {
            "method": "camera_photo_take",
            "msg": "YCamSDK native helper is not integrated",
            "data": {
                "result": DEVICE_STATE_UNSUPPORTED,
                "payload_id": "spectral_camera",
                "required_sdk_entrypoints": ["YUS_DLL_CameraTakePic"],
            },
        }

        sanitized, removed = sanitize_services_reply(reply)

        self.assertEqual(sanitized["data"]["result"], DEVICE_STATE_UNSUPPORTED)
        self.assertEqual(sanitized["data"]["output"]["status"], "failed")
        self.assertIn("YCamSDK", sanitized["data"]["output"]["reason"])
        self.assertNotIn("payload_id", sanitized["data"])
        self.assertIn("payload_id", removed)

    def test_payload_success_stays_result_only(self):
        reply = {
            "method": "camera_photo_take",
            "data": {
                "result": 0,
                "viewlink_result": {"success": True},
            },
        }

        sanitized, _ = sanitize_services_reply(reply)

        self.assertEqual(sanitized["data"], {"result": 0})


if __name__ == "__main__":
    unittest.main()
