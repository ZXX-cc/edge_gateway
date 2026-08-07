import json
import os
import sys
import threading
import time
import types
import unittest


PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

# 本专项只验证本地 MQTT 回调调度，不依赖实际 broker；保持在未安装
# paho-mqtt 的最小开发环境中也可运行。Ubuntu 集成环境仍使用 requirements.txt
# 中的 paho-mqtt 2.1.0。
try:
    import paho.mqtt.client  # noqa: F401,E402
except ModuleNotFoundError:
    fake_paho = types.ModuleType("paho")
    fake_mqtt = types.ModuleType("paho.mqtt")
    fake_client = types.ModuleType("paho.mqtt.client")
    fake_client.MQTT_ERR_SUCCESS = 0
    fake_client.Client = object
    fake_paho.mqtt = fake_mqtt
    fake_mqtt.client = fake_client
    sys.modules["paho"] = fake_paho
    sys.modules["paho.mqtt"] = fake_mqtt
    sys.modules["paho.mqtt.client"] = fake_client

from src.mqtt.client import MQTTClient  # noqa: E402
from src.utils.dji_error_codes import DEVICE_STATE_UNSUPPORTED  # noqa: E402
from src.utils.message import MessageBuilder, TopicBuilder  # noqa: E402


def mqtt_message(topic, payload):
    return types.SimpleNamespace(topic=topic, payload=json.dumps(payload).encode("utf-8"))


class _FakePublisher:
    def __init__(self):
        self.published = []
        self._lock = threading.Lock()

    def publish(self, topic, payload, qos=1):
        with self._lock:
            self.published.append((topic, json.loads(payload), qos))
        return types.SimpleNamespace(rc=0)

    def loop_stop(self):
        pass

    def disconnect(self):
        pass


class MQTTBackgroundHandlerTest(unittest.TestCase):
    def setUp(self):
        self.client = MQTTClient()
        self.client.connected = True
        self.client.client = _FakePublisher()
        self.topic = TopicBuilder.services(self.client.gateway_sn)

    def tearDown(self):
        self.client.disconnect()

    def test_background_io_handler_does_not_block_mqtt_callback_and_replies(self):
        started = threading.Event()
        release = threading.Event()

        def slow_log_scan(msg):
            started.set()
            self.assertTrue(release.wait(1), "test did not release background handler")
            return MessageBuilder.build_reply(msg, result=0, data={"status": "ok"})

        self.client.register_handler(
            "fileupload_list",
            slow_log_scan,
            background=True,
            reply_required=True,
        )
        payload = {
            "tid": "background-io-1",
            "method": "fileupload_list",
            "need_reply": 1,
            "data": {},
        }

        callback_started = time.perf_counter()
        self.client._on_message(None, None, mqtt_message(self.topic, payload))
        callback_elapsed = time.perf_counter() - callback_started

        self.assertLess(callback_elapsed, 0.1)
        self.assertTrue(started.wait(0.5))
        self.assertEqual(self.client.client.published, [])

        release.set()
        deadline = time.monotonic() + 1
        while not self.client.client.published and time.monotonic() < deadline:
            time.sleep(0.01)

        self.assertEqual(len(self.client.client.published), 1)
        reply_topic, reply, _ = self.client.client.published[0]
        self.assertEqual(reply_topic, TopicBuilder.services_reply(self.client.gateway_sn))
        self.assertEqual(reply["tid"], payload["tid"])
        self.assertEqual(reply["data"]["result"], 0)

    def test_control_handler_remains_inline_and_ordered(self):
        calls = []

        def control_handler(msg):
            calls.append(msg["tid"])
            return MessageBuilder.build_reply(msg, result=0)

        self.client.register_handler("cover_open", control_handler, reply_required=True)
        payload = {
            "tid": "control-1",
            "method": "cover_open",
            "need_reply": 1,
            "data": {},
        }

        self.client._on_message(None, None, mqtt_message(self.topic, payload))

        self.assertEqual(calls, ["control-1"])
        self.assertEqual(len(self.client.client.published), 1)

    def test_services_command_replies_without_need_reply_flag(self):
        def handler(msg):
            return MessageBuilder.build_reply(msg, result=0)

        self.client.register_handler("live_start_push", handler, reply_required=True)
        payload = {
            "tid": "services-no-need-reply",
            "method": "live_start_push",
            "data": {},
        }

        self.client._on_message(None, None, mqtt_message(self.topic, payload))

        self.assertEqual(len(self.client.client.published), 1)
        reply_topic, reply, _ = self.client.client.published[0]
        self.assertEqual(reply_topic, TopicBuilder.services_reply(self.client.gateway_sn))
        self.assertEqual(reply["tid"], payload["tid"])

    def test_services_command_without_confirmed_reply_policy_does_not_default_reply(self):
        def handler(msg):
            return MessageBuilder.build_reply(msg, result=0)

        self.client.register_handler("unconfirmed_service", handler)
        payload = {
            "tid": "services-unconfirmed",
            "method": "unconfirmed_service",
            "data": {},
        }

        self.client._on_message(None, None, mqtt_message(self.topic, payload))

        self.assertEqual(self.client.client.published, [])

    def test_services_allowlist_blocks_registered_non_dock3_method(self):
        calls = []

        def handler(msg):
            calls.append(msg["tid"])
            return MessageBuilder.build_reply(msg, result=0)

        self.client.set_allowed_service_methods({"cover_open"})
        self.client.register_handler("format_sd_card", handler, reply_required=True)
        payload = {
            "tid": "services-not-allowed",
            "method": "format_sd_card",
            "data": {},
        }

        self.client._on_message(None, None, mqtt_message(self.topic, payload))

        self.assertEqual(calls, [])
        self.assertEqual(len(self.client.client.published), 1)
        reply_topic, reply, _ = self.client.client.published[0]
        self.assertEqual(reply_topic, TopicBuilder.services_reply(self.client.gateway_sn))
        self.assertEqual(reply["tid"], payload["tid"])
        self.assertEqual(reply["method"], payload["method"])
        self.assertEqual(reply["data"]["result"], DEVICE_STATE_UNSUPPORTED)

    def test_services_allowlist_blocks_legacy_flighttask_return_alias(self):
        calls = []

        def handler(msg):
            calls.append(msg["tid"])
            return MessageBuilder.build_reply(msg, result=0)

        self.client.set_allowed_service_methods({"return_home", "return_home_cancel", "flighttask_stop"})
        self.client.register_handler("flighttask_return", handler, reply_required=True)
        payload = {
            "tid": "services-flighttask-return-not-allowed",
            "method": "flighttask_return",
            "data": {"flight_id": "flight-1", "return_type": 0},
        }

        self.client._on_message(None, None, mqtt_message(self.topic, payload))

        self.assertEqual(calls, [])
        reply_topic, reply, _ = self.client.client.published[0]
        self.assertEqual(reply_topic, TopicBuilder.services_reply(self.client.gateway_sn))
        self.assertEqual(reply["method"], "flighttask_return")
        self.assertEqual(reply["data"], {"result": DEVICE_STATE_UNSUPPORTED})

    def test_services_allowlist_ignores_known_non_services_method_without_reply(self):
        calls = []

        def handler(msg):
            calls.append(msg["tid"])
            return MessageBuilder.build_reply(msg, result=0)

        self.client.set_allowed_service_methods({"cover_open"})
        self.client.set_ignored_service_methods({"device_binding"})
        self.client.register_handler("device_binding", handler, reply_required=True)
        payload = {
            "tid": "services-non-services-method",
            "method": "device_binding",
            "data": {"workspace_id": "legacy"},
        }

        self.client._on_message(None, None, mqtt_message(self.topic, payload))

        self.assertEqual(calls, [])
        self.assertEqual(self.client.client.published, [])

    def test_services_allowlist_permits_dock3_method(self):
        calls = []

        def handler(msg):
            calls.append(msg["tid"])
            return MessageBuilder.build_reply(msg, result=0)

        self.client.set_allowed_service_methods({"cover_open"})
        self.client.register_handler("cover_open", handler, reply_required=True)
        payload = {
            "tid": "services-allowed",
            "method": "cover_open",
            "data": {},
        }

        self.client._on_message(None, None, mqtt_message(self.topic, payload))

        self.assertEqual(calls, ["services-allowed"])
        self.assertEqual(len(self.client.client.published), 1)
        reply_topic, reply, _ = self.client.client.published[0]
        self.assertEqual(reply_topic, TopicBuilder.services_reply(self.client.gateway_sn))
        self.assertEqual(reply["tid"], payload["tid"])
        self.assertEqual(reply["data"]["result"], 0)

    def test_services_reply_strips_result_only_payload_debug_fields(self):
        def handler(msg):
            return MessageBuilder.build_reply(msg, result=0, data={
                "viewlink_result": {"success": True},
                "software_pipeline": [{"step": "local"}],
                "photo": {"reply": {"data": {"result": 0}}},
            })

        self.client.register_handler("camera_photo_take", handler, reply_required=True)
        payload = {
            "tid": "payload-schema-1",
            "method": "camera_photo_take",
            "data": {},
        }

        self.client._on_message(None, None, mqtt_message(self.topic, payload))

        _, reply, _ = self.client.client.published[0]
        self.assertEqual(reply["data"], {"result": 0})

    def test_services_reply_strips_cmd_output_to_official_status_shape(self):
        def handler(msg):
            return MessageBuilder.build_reply(msg, result=0, data={
                "action": "cover_open",
                "ack": {"raw": 123},
                "output": {
                    "status": "success",
                    "progress": {"percent": 120, "step_key": "open_cover", "raw": "x"},
                    "ordinary_order_response": {"raw": 1},
                },
            })

        self.client.register_handler("cover_open", handler, reply_required=True)
        payload = {
            "tid": "cmd-schema-1",
            "method": "cover_open",
            "data": {},
        }

        self.client._on_message(None, None, mqtt_message(self.topic, payload))

        _, reply, _ = self.client.client.published[0]
        self.assertEqual(reply["data"], {
            "output": {
                "status": "failed",
                "progress": {"percent": 100, "step_key": "open_cover"},
            },
            "result": 0,
        })

    def test_services_reply_strips_status_service_output_debug_fields(self):
        def handler(msg):
            return MessageBuilder.build_reply(msg, result=0, data={
                "source": "viewlink",
                "output": {
                    "status": "ok",
                    "software_pipeline": [{"step": "push"}],
                    "rtmp_url": "rtmp://example/live",
                },
            })

        self.client.register_handler("live_start", handler, reply_required=True)
        payload = {
            "tid": "status-schema-1",
            "method": "live_start",
            "data": {},
        }

        self.client._on_message(None, None, mqtt_message(self.topic, payload))

        _, reply, _ = self.client.client.published[0]
        self.assertEqual(reply["data"], {
            "output": {"status": "ok"},
            "result": 0,
        })

    def test_services_reply_unknown_method_defaults_to_result_only(self):
        def handler(msg):
            return MessageBuilder.build_reply(msg, result=0, data={
                "output": {"status": "ok", "detail": {"debug": True}},
                "internal": "not-for-platform",
            })

        self.client.register_handler("project_internal_test", handler, reply_required=True)
        payload = {
            "tid": "unknown-schema-1",
            "method": "project_internal_test",
            "data": {},
        }

        self.client._on_message(None, None, mqtt_message(self.topic, payload))

        _, reply, _ = self.client.client.published[0]
        self.assertEqual(reply["data"], {"result": 0})

    def test_services_reply_keeps_allowlisted_query_fields(self):
        def handler(msg):
            return MessageBuilder.build_reply(msg, result=0, data={
                "output": {"status": "ok", "debug": "drop"},
                "files": [{"file_id": "a"}],
                "total": 1,
                "absolute_path": "/tmp/a.jpg",
            })

        self.client.register_handler("file_list_get", handler, reply_required=True)
        payload = {
            "tid": "query-schema-1",
            "method": "file_list_get",
            "data": {},
        }

        self.client._on_message(None, None, mqtt_message(self.topic, payload))

        _, reply, _ = self.client.client.published[0]
        self.assertEqual(reply["data"], {
            "output": {"status": "ok"},
            "files": [{"file_id": "a"}],
            "total": 1,
            "result": 0,
        })

    def test_remote_debug_admission_rejection_replies_without_need_reply_flag(self):
        class RejectDecision:
            accepted = False
            reason = "device_unbound"
            message = "设备未绑定，当前命令不能进入业务执行"
            result = 325100
            required_state = ["device.bound", "device.online"]
            snapshot = None

            def build_reply(self, request, gateway_sn):
                return {
                    "bid": request.get("bid", ""),
                    "data": {
                        "output": {
                            "status": "rejected",
                            "reason": self.reason,
                        },
                        "result": self.result,
                    },
                    "gateway": request.get("gateway", gateway_sn),
                    "tid": request.get("tid", ""),
                    "timestamp": 1,
                    "method": request.get("method", ""),
                    "msg": self.message,
                }

        class RejectAdmission:
            def check(self, method, request, source_topic):
                return RejectDecision()

        def handler(msg):
            return MessageBuilder.build_reply(msg, result=0)

        self.client.set_command_admission(RejectAdmission())
        self.client.register_handler("debug_mode_open", handler, reply_required=True)
        payload = {
            "tid": "remote-debug-no-need-reply",
            "bid": "remote-debug-bid",
            "method": "debug_mode_open",
            "data": {},
        }

        self.client._on_message(None, None, mqtt_message(self.topic, payload))

        self.assertEqual(len(self.client.client.published), 1)
        reply_topic, reply, _ = self.client.client.published[0]
        self.assertEqual(reply_topic, TopicBuilder.services_reply(self.client.gateway_sn))
        self.assertEqual(reply["tid"], payload["tid"])
        self.assertEqual(reply["method"], "debug_mode_open")
        self.assertEqual(reply["data"]["result"], 325100)

    def test_drc_down_can_still_omit_reply_with_need_reply_zero(self):
        def handler(msg):
            return MessageBuilder.build_reply(msg, result=0)

        self.client.register_handler("stick_control", handler)
        payload = {
            "tid": "drc-no-reply",
            "method": "stick_control",
            "need_reply": 0,
            "data": {},
        }

        self.client._on_message(
            None,
            None,
            mqtt_message(TopicBuilder.drc_down(self.client.gateway_sn), payload),
        )

        self.assertEqual(self.client.client.published, [])


if __name__ == "__main__":
    unittest.main()
