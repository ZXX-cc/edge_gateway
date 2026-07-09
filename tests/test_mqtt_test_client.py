import contextlib
import io
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
    output_status,
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
        self.assertEqual(resolve_channel("heart_beat"), "drc")
        self.assertEqual(resolve_channel("fly_to_point_update"), "services")
        self.assertEqual(resolve_channel("dock_status_query"), "services")
        self.assertEqual(resolve_channel("dock_status_query", "property"), "property")

    def test_resolve_channel_rejects_unknown_explicit_channel(self):
        with self.assertRaises(ValueError):
            resolve_channel("dock_status_query", "unknown")

    def test_result_helpers_extract_top_level_and_property_results(self):
        reply = {
            "data": {
                "result": 0,
                "output": {"status": "ok"},
                "silent_mode": {"state": {"result": 0}},
                "battery_store_mode": {"state": {"result": 1}},
                "ignored": {"state": {"result": "0"}},
            }
        }

        self.assertEqual(result_code(reply), 0)
        self.assertEqual(output_status(reply), "ok")
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
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertTrue(self.client.connect(0.1))
        self.assertEqual(
            sorted(topic for topic, _ in self.client.client.subscriptions),
            [
                "thing/product/EDGEGW001/drc/up",
                "thing/product/EDGEGW001/property/set_reply",
                "thing/product/EDGEGW001/services_reply",
            ],
        )

    def test_connect_can_skip_default_reply_topics(self):
        quiet_client = MqttRequestClient(
            "127.0.0.1",
            1883,
            "EDGEGW001",
            quiet_stale=True,
            subscribe_default_replies=False,
            observe_topics={"thing/product/EDGEGW001/requests_reply"},
            mqtt_module=FakeMqttModule,
        )

        with contextlib.redirect_stdout(io.StringIO()):
            self.assertTrue(quiet_client.connect(0.1))
        self.assertEqual(quiet_client.client.subscriptions, [("thing/product/EDGEGW001/requests_reply", 1)])

    def test_connect_subscribes_events_when_requested(self):
        event_client = MqttRequestClient(
            "127.0.0.1",
            1883,
            "EDGEGW001",
            quiet_stale=True,
            subscribe_events=True,
            mqtt_module=FakeMqttModule,
        )

        with contextlib.redirect_stdout(io.StringIO()):
            self.assertTrue(event_client.connect(0.1))
        self.assertIn(("thing/product/EDGEGW001/events", 1), event_client.client.subscriptions)

    def test_connect_subscribes_status_when_requested(self):
        status_client = MqttRequestClient(
            "127.0.0.1",
            1883,
            "EDGEGW001",
            quiet_stale=True,
            subscribe_status=True,
            mqtt_module=FakeMqttModule,
        )

        with contextlib.redirect_stdout(io.StringIO()):
            self.assertTrue(status_client.connect(0.1))
        self.assertIn(("sys/product/EDGEGW001/status", 1), status_client.client.subscriptions)

    def test_connect_subscribes_observed_topics_when_requested(self):
        observed_client = MqttRequestClient(
            "127.0.0.1",
            1883,
            "EDGEGW001",
            quiet_stale=True,
            observe_topics={"thing/product/EDGEGW001/osd", "thing/product/EDGEGW001/state"},
            mqtt_module=FakeMqttModule,
        )

        with contextlib.redirect_stdout(io.StringIO()):
            self.assertTrue(observed_client.connect(0.1))
        self.assertIn(("thing/product/EDGEGW001/osd", 1), observed_client.client.subscriptions)
        self.assertIn(("thing/product/EDGEGW001/state", 1), observed_client.client.subscriptions)

    def test_connect_returns_false_on_nonzero_rc(self):
        FakeMqttClient.connect_rc = 5
        failed_client = MqttRequestClient(
            "127.0.0.1",
            1883,
            "EDGEGW001",
            quiet_stale=True,
            mqtt_module=FakeMqttModule,
        )

        with contextlib.redirect_stderr(io.StringIO()):
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
        self.assertEqual(
            self.client.stale_sample_summary()[-1],
            {
                "category": "wrong_topic",
                "topic": "thing/product/EDGEGW001/property/set_reply",
                "method": "dock_status_query",
                "tid": self.tid,
                "result": 0,
                "expected_topic": self.reply_topic,
            },
        )

    def test_wrong_method_is_ignored_even_when_tid_matches(self):
        payload = {"tid": self.tid, "method": "property_get", "data": {"result": 0}}

        self.client._on_message(None, None, mqtt_message(self.reply_topic, payload))

        self.assertFalse(self.client.pending[self.tid]["event"].is_set())
        self.assertEqual(self.client.stats["ignored_mismatched"], 1)
        self.assertEqual(
            self.client.stale_sample_summary()[-1],
            {
                "category": "mismatched",
                "topic": self.reply_topic,
                "method": "property_get",
                "tid": self.tid,
                "result": 0,
                "expected_method": "dock_status_query",
            },
        )

    def test_legacy_negative_reply_is_ignored(self):
        payload = {"tid": self.tid, "method": "dock_status_query", "data": {"result": -2}}

        self.client._on_message(None, None, mqtt_message(self.reply_topic, payload))

        self.assertFalse(self.client.pending[self.tid]["event"].is_set())
        self.assertEqual(self.client.stats["ignored_legacy"], 1)
        self.assertEqual(self.client.stale_sample_summary()[-1]["category"], "legacy")
        self.assertEqual(self.client.stale_sample_summary()[-1]["result"], -2)

    def test_completed_tid_reply_is_counted_as_duplicate(self):
        self.client.pending.clear()
        self.client._mark_completed_tid(self.tid)
        payload = {
            "tid": self.tid,
            "method": "dock_status_query",
            "gateway": "EDGEGW001",
            "data": {"result": 0},
        }

        self.client._on_message(None, None, mqtt_message(self.reply_topic, payload))

        self.assertEqual(self.client.stats["ignored_late"], 1)
        self.assertEqual(self.client.stats["ignored_duplicate"], 1)
        self.assertEqual(self.client.stats["ignored_unknown"], 0)
        self.assertEqual(self.client.stale_sample_summary()[-1]["category"], "duplicate")
        self.assertEqual(self.client.stale_sample_summary()[-1]["tid"], self.tid)

    def test_unknown_tid_reply_is_counted_as_unknown_late(self):
        self.client.pending.clear()
        payload = {
            "tid": "unknown-tid",
            "method": "dock_status_query",
            "gateway": "EDGEGW001",
            "data": {"result": 0},
        }

        self.client._on_message(None, None, mqtt_message(self.reply_topic, payload))

        self.assertEqual(self.client.stats["ignored_late"], 1)
        self.assertEqual(self.client.stats["ignored_duplicate"], 0)
        self.assertEqual(self.client.stats["ignored_unknown"], 1)
        self.assertEqual(self.client.stale_sample_summary()[-1]["category"], "unknown")
        self.assertEqual(self.client.stale_sample_summary()[-1]["tid"], "unknown-tid")

    def test_second_reply_while_pending_is_counted_as_duplicate(self):
        self.client.pending[self.tid]["event"].set()
        payload = {
            "tid": self.tid,
            "method": "dock_status_query",
            "gateway": "EDGEGW001",
            "data": {"result": 0},
        }

        self.client._on_message(None, None, mqtt_message(self.reply_topic, payload))

        self.assertEqual(self.client.stats["ignored_late"], 1)
        self.assertEqual(self.client.stats["ignored_duplicate"], 1)
        self.assertEqual(self.client.stats["ignored_unknown"], 0)
        self.assertEqual(self.client.stale_sample_summary()[-1]["category"], "duplicate_pending")

    def test_expected_status_ignores_legacy_reply_without_output_status(self):
        self.client.pending[self.tid]["expected_status"] = "ok"
        legacy = {
            "tid": self.tid,
            "method": "dock_status_query",
            "gateway": "EDGEGW001",
            "msg": "",
            "data": {"result": 0},
        }
        current = {
            "tid": self.tid,
            "method": "dock_status_query",
            "gateway": "EDGEGW001",
            "msg": "",
            "data": {"result": 0, "output": {"status": "ok"}},
        }

        self.client._on_message(None, None, mqtt_message(self.reply_topic, legacy))
        self.assertFalse(self.client.pending[self.tid]["event"].is_set())
        self.assertEqual(self.client.stats["ignored_legacy"], 1)
        self.assertEqual(self.client.stale_sample_summary()[-1]["category"], "legacy_no_status")

        self.client._on_message(None, None, mqtt_message(self.reply_topic, current))
        waiter = self.client.pending[self.tid]
        self.assertTrue(waiter["event"].is_set())
        self.assertEqual(waiter["payload"], current)

    def test_stale_sample_summary_is_bounded_and_limited(self):
        self.client.pending.clear()

        for index in range(15):
            self.client._on_message(
                None,
                None,
                mqtt_message(
                    self.reply_topic,
                    {
                        "tid": f"unknown-{index}",
                        "method": "dock_status_query",
                        "gateway": "EDGEGW001",
                        "data": {"result": 0},
                    },
                ),
            )

        self.assertEqual(len(self.client.stale_samples), 12)
        self.assertEqual(self.client.stale_samples[0]["tid"], "unknown-3")
        self.assertEqual(
            [item["tid"] for item in self.client.stale_sample_summary(limit=3)],
            ["unknown-12", "unknown-13", "unknown-14"],
        )

    def test_event_topic_is_recorded_and_waitable(self):
        event_client = MqttRequestClient(
            "127.0.0.1",
            1883,
            "EDGEGW001",
            quiet_stale=True,
            subscribe_events=True,
            mqtt_module=FakeMqttModule,
        )
        event = {
            "tid": "event-tid",
            "method": "cover_open",
            "data": {"result": 0, "output": {"status": "ok"}},
        }

        event_client._on_message(None, None, mqtt_message("thing/product/EDGEGW001/events", event))

        self.assertTrue(event_client.has_event("cover_open"))
        self.assertEqual(event_client.event_count("cover_open"), 1)
        self.assertEqual(event_client.stats["events"], 1)
        with contextlib.redirect_stdout(io.StringIO()):
            rc, observed = event_client.wait_event("cover_open", 0, 0.1, expected_status="ok")
        self.assertEqual(rc, 0)
        self.assertEqual(observed, event)

    def test_status_topic_is_recorded_and_waitable(self):
        status_client = MqttRequestClient(
            "127.0.0.1",
            1883,
            "EDGEGW001",
            quiet_stale=True,
            subscribe_status=True,
            mqtt_module=FakeMqttModule,
        )
        status = {
            "tid": "status-tid",
            "method": "update_topo",
            "data": {"sub_devices": []},
        }

        status_client._on_message(None, None, mqtt_message("sys/product/EDGEGW001/status", status))

        self.assertEqual(status_client.status_count("update_topo"), 1)
        self.assertEqual(status_client.stats["status"], 1)
        observed = status_client.wait_status("update_topo", 0, 0.1)
        self.assertEqual(observed, status)

    def test_observed_topic_is_recorded_and_waitable(self):
        observed_client = MqttRequestClient(
            "127.0.0.1",
            1883,
            "EDGEGW001",
            quiet_stale=True,
            observe_topics={"thing/product/EDGEGW001/osd"},
            mqtt_module=FakeMqttModule,
        )
        payload = {
            "tid": "osd-tid",
            "method": "osd",
            "data": {"mode_code": 0},
        }

        observed_client._on_message(None, None, mqtt_message("thing/product/EDGEGW001/osd", payload))

        self.assertEqual(observed_client.observed_count("thing/product/EDGEGW001/osd"), 1)
        self.assertEqual(observed_client.stats["observed"], 1)
        observed = observed_client.wait_observed("thing/product/EDGEGW001/osd", 0, 0.1)
        self.assertEqual(observed, payload)

    def test_no_reply_request_omits_need_reply_and_succeeds_without_reply(self):
        with contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()):
            rc, payload = self.client.request(
                "stick_control",
                {"roll": 1024, "pitch": 1024},
                0,
                channel="drc",
                need_reply=0,
                verbose=False,
            )

        self.assertEqual(rc, 0)
        self.assertIsNone(payload)
        topic, message, qos = self.client.client.published[-1]
        self.assertEqual(topic, "thing/product/EDGEGW001/drc/down")
        self.assertEqual(qos, 1)
        self.assertNotIn("need_reply", message)

    def test_no_reply_request_fails_if_exact_reply_arrives(self):
        original_publish = self.client.client.publish

        def publish_and_reply(topic, payload, qos=0):
            original_publish(topic, payload, qos)
            message = json.loads(payload)
            reply = {
                "tid": message["tid"],
                "method": message["method"],
                "gateway": "EDGEGW001",
                "msg": "",
                "data": {"result": 0},
            }
            self.client._on_message(None, None, mqtt_message("thing/product/EDGEGW001/drc/up", reply))

        self.client.client.publish = publish_and_reply

        with contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()):
            rc, payload = self.client.request(
                "stick_control",
                {"roll": 1024, "pitch": 1024},
                0,
                channel="drc",
                need_reply=0,
                verbose=False,
            )

        self.assertEqual(rc, 8)
        self.assertEqual(payload["method"], "stick_control")


if __name__ == "__main__":
    unittest.main()
