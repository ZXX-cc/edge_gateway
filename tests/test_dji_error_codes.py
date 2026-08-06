import os
import sys
import unittest


PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from src.utils.dji_error_codes import (
    DEVICE_COMMAND_RESPONSE_FAILED,
    DOCK_BUSY,
    DOCK_SYSTEM_ERROR,
    DOCK_UNABLE_TO_RESPOND,
    INVALID_COMMAND_FORMAT,
    PARAMETER_SET_FAILED,
    describe_error,
    normalize_error_code,
    normalize_reply_error_codes,
)
from src.utils.message import MessageBuilder


class DjiErrorCodeTest(unittest.TestCase):
    def test_build_reply_normalizes_legacy_negative_result(self):
        reply = MessageBuilder.build_reply(
            {"tid": "tid-1", "bid": "bid-1", "gateway": "gw", "method": "x"},
            result=-1,
        )

        self.assertEqual(reply["data"]["result"], DOCK_SYSTEM_ERROR)
        self.assertNotEqual(reply["msg"], "")

    def test_property_set_reply_uses_property_error_context(self):
        reply = MessageBuilder.build_property_set_reply(
            {"tid": "tid-1", "bid": "bid-1", "gateway": "gw", "method": "property_set"},
            {"silent_mode": -1},
        )

        self.assertEqual(
            reply["data"]["silent_mode"]["state"]["result"],
            PARAMETER_SET_FAILED,
        )

    def test_build_state_flattens_legacy_property_wrapper(self):
        state = MessageBuilder.build_state(
            "gw",
            {"property": {"live_capacity": {"available_video_number": 1}}},
        )

        self.assertEqual(
            state["data"],
            {"live_capacity": {"available_video_number": 1}},
        )

    def test_invalid_request_context_uses_command_format_error(self):
        self.assertEqual(
            normalize_error_code(-3, context="invalid_request"),
            INVALID_COMMAND_FORMAT,
        )

    def test_dock3_device_command_error_descriptions(self):
        self.assertIn("指令响应失败", describe_error(DEVICE_COMMAND_RESPONSE_FAILED))
        self.assertIn("当前机场无法响应任务", describe_error(DOCK_UNABLE_TO_RESPOND))

    def test_recursive_reply_normalization(self):
        reply, conversions = normalize_reply_error_codes(
            {
                "data": {
                    "result": -2,
                    "nested": {"result": -3},
                }
            },
            context="generic",
        )

        self.assertEqual(reply["data"]["result"], DOCK_BUSY)
        self.assertEqual(reply["data"]["nested"]["result"], INVALID_COMMAND_FORMAT)
        self.assertEqual(len(conversions), 2)


if __name__ == "__main__":
    unittest.main()
