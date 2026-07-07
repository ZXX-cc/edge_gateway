import json
import os
import sys
import types
import unittest
from threading import Event


PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOLS_DIR = os.path.join(PROJECT_ROOT, "tools")
if TOOLS_DIR not in sys.path:
    sys.path.insert(0, TOOLS_DIR)

from mqtt_test_client import (  # noqa: E402
    MqttRequestClient,
    property_result,
    property_results,
    resolve_channel,
    result_code,
    looks_like_legacy_stale_reply,
)


class FakeMqttClient:
    connect_rc = 0

    def __init__(self, *args, **kwargs):
        self.published = []
        self.subscriptions = []

    def connect(self, host, port, keepalive=30):
        self.host = host
        self.port = port
        self.keepalive = keepalive
        if hasattr(self, "on_connect"):
            self.on_connect(self, None, None, self.connect_rc)

    def loop_start(self):
        pass

    def loop_stop(self):
        pass

    def disconnect(self):
        pass

    def subscribe(self, topic, qos=0):
        self.subscriptions.append((topic, qos))

    def publish(self, topic, payload, qos=0):
        self.published.append((topic, json.loads(payload), qos))


class FakeMqttModule:
    Client = FakeMqttClient


def mqtt_message(topic, payload):
    return types.SimpleNamespace(topic=topic, payload=json.dumps(payload).encode("utf-8"))


class MqttTestClientHelpersTest(unittest.TestCase):
    def test_resolve_channel_uses_method_hints(self):
        self.assertEqual(resolve_channel("property_set"), "property")
        self.assertEqual(resolve_channel("stick_control"), "drc")
        self.assertEqual(resolve_channel("dock_status_query"), "services")
        self.assertEqual(resolve_channel("dock_status_query", "property"), "property")

    def test_resolve_channel_rejects_unknown_explicit_channel(self):
        with self.assertRaises(ValueError):
            resolve_channel("dock_status_query", "unknown")

    def test_result_helpers_extract_top_level_and_property_results(self):
        reply = {
            "data": {
                "result": 0,
                "silent_mode": {"state": {"result": 0}},
                "battery_store_mode": {"state": {"result": 1}},
                "ignored": {"state": {"result": "0"}},
            }
        }

        self.assertEqual(result_code(reply), 0)
        self.assertEqual(property_result(reply, "silent_mode"), 0)
        self.assertEqual(
            property_results(reply),
            {
                "silent_mode": 0,
                "battery_store_mode": 1,
            },
        )

    def test_legacy_stale_reply_detection(self):
        self.assertTrue(looks_like_legacy_stale_reply({"data": {"result": -2}}))
        self.assertTrue(looks_like_legacy_stale_reply({"data": {"silent_mode": -1}}))
        self.assertTrue(looks_like_legacy_stale_reply({"data": {"msg": "old handler"}}))
        self.assertFalse(looks_like_legacy_stale_reply({"gateway": "EDGEGW001", "msg": "", "data": {"result": 0}}))


class MqttRequestClientMatchingTest(unittest.TestCase):
    def tearDown(self):
        FakeMqttClient.connect_rc = 0

    def test_connect_subscribes_all_reply_topics(self):
        self.assertTrue(self.client.connect(0.1))
        self.assertEqual(
            sorted(topic for topic, _ in self.client.client.subscriptions),
            [
                "thing/product/EDGEGW001/drc/up",
                "thing/product/EDGEGW001/property/set_reply",
                "thing/product/EDGEGW001/services_reply",
            ],
        )

    def test_connect_returns_false_on_nonzero_rc(self):
        FakeMqttClient.connect_rc = 5
        failed_client = MqttRequestClient(
            "127.0.0.1",
            1883,
            "EDGEGW001",
            quiet_stale=True,
            mqtt_module=FakeMqttModule,
        )

        self.assertFalse(failed_client.connect(0.1))

    def setUp(self):
        self.client = MqttRequestClient(
            "127.0.0.1",
            1883,
            "EDGEGW001",
            quiet_stale=True,
            mqtt_module=FakeMqttModule,
        )
        self.tid = "tid-1"
        self.reply_topic = "thing/product/EDGEGW001/services_reply"
        self.client.pending[self.tid] = {
            "event": Event(),
            "topic": "",
            "payload": None,
            "method": "dock_status_query",
            "expected_topic": self.reply_topic,
        }

    def test_exact_tid_method_and_topic_reply_is_accepted(self):
        payload = {
            "tid": self.tid,
            "method": "dock_status_query",
            "gateway": "EDGEGW001",
            "msg": "",
            "data": {"result": 0},
        }

        self.client._on_message(None, None, mqtt_message(self.reply_topic, payload))

        waiter = self.client.pending[self.tid]
        self.assertTrue(waiter["event"].is_set())
        self.assertEqual(waiter["payload"], payload)
        self.assertEqual(self.client.stats["accepted"], 1)

    def test_wrong_topic_is_ignored_even_when_tid_matches(self):
        payload = {"tid": self.tid, "method": "dock_status_query", "data": {"result": 0}}

        self.client._on_message(None, None, mqtt_message("thing/product/EDGEGW001/property/set_reply", payload))

        self.assertFalse(self.client.pending[self.tid]["event"].is_set())
        self.assertEqual(self.client.stats["ignored_wrong_topic"], 1)

    def test_wrong_method_is_ignored_even_when_tid_matches(self):
        payload = {"tid": self.tid, "method": "property_get", "data": {"result": 0}}

        self.client._on_message(None, None, mqtt_message(self.reply_topic, payload))

        self.assertFalse(self.client.pending[self.tid]["event"].is_set())
        self.assertEqual(self.client.stats["ignored_mismatched"], 1)

    def test_legacy_negative_reply_is_ignored(self):
        payload = {"tid": self.tid, "method": "dock_status_query", "data": {"result": -2}}

        self.client._on_message(None, None, mqtt_message(self.reply_topic, payload))

        self.assertFalse(self.client.pending[self.tid]["event"].is_set())
        self.assertEqual(self.client.stats["ignored_legacy"], 1)


if __name__ == "__main__":
    unittest.main()
