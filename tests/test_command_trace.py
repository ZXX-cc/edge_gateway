import os
import sys
import unittest


PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from src.utils.command_trace import _command_action_from_fields, _format_trace_line  # noqa: E402


class CommandTraceFormatTest(unittest.TestCase):
    def test_dock3_methods_have_human_readable_actions(self):
        self.assertEqual(_command_action_from_fields({"method": "property_set"}), "属性设置")
        self.assertEqual(_command_action_from_fields({"method": "firmware_upgrade"}), "固件升级任务创建")
        self.assertEqual(_command_action_from_fields({"method": "live_start"}), "开始直播")
        self.assertEqual(_command_action_from_fields({"method": "hms_get"}), "HMS 告警查询")

    def test_unknown_method_keeps_explicit_unknown_label(self):
        self.assertEqual(_command_action_from_fields({"method": "unknown_probe"}), "未识别命令")

    def test_no_handler_trace_line_includes_action_and_error(self):
        line = _format_trace_line(
            "2026-07-09T12:00:00.000",
            "mqtt_no_handler",
            "未注册业务处理器",
            {
                "method": "unknown_probe",
                "tid": "tid-1",
                "topic": "thing/product/EDGEGW001/services",
                "error": "No handler registered for method: unknown_probe",
            },
        )

        self.assertIn("未注册业务处理器", line)
        self.assertIn("未识别命令", line)
        self.assertIn("No handler registered", line)

    def test_invalid_topic_trace_line_includes_known_action(self):
        line = _format_trace_line(
            "2026-07-09T12:00:00.000",
            "mqtt_invalid_topic",
            "Topic不匹配",
            {
                "method": "property_set",
                "tid": "tid-2",
                "topic": "thing/product/EDGEGW001/services",
                "reason": "property_set must be sent to thing/product/{sn}/property/set",
            },
        )

        self.assertIn("Topic 不匹配", line)
        self.assertIn("属性设置", line)
        self.assertIn("property_set", line)


if __name__ == "__main__":
    unittest.main()
