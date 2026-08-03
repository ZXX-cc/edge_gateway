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
