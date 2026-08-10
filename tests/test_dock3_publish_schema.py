import unittest
import json
from copy import deepcopy

from src.cloud_api.dock3_publish_schema import sanitize_upstream_publish
from src.mqtt.client import MQTTClient
from src.utils.config import Config


class FakePublishInfo:
    rc = 0


class FakePahoClient:
    def __init__(self):
        self.calls = []

    def publish(self, topic, message, qos=1):
        self.calls.append((topic, json.loads(message), qos))
        return FakePublishInfo()


class Dock3PublishSchemaTest(unittest.TestCase):
    def setUp(self):
        self._config = deepcopy(Config._data)
        Config._data = {"gateway": {"sn": "BXD001"}, "mqtt": {}}

    def tearDown(self):
        Config._data = self._config

    def test_gateway_osd_keeps_dock_fields_and_payload_index_only(self):
        payload = {
            "bid": "b",
            "tid": "t",
            "timestamp": 1,
            "gateway": "BXD001",
            "data": {
                "cover_state": 1,
                "mode_code": 0,
                "putter_state": 1,
                "type_subtype_gimbalindex": "0-42-0",
                "live_capacity": {},
                "software_pipeline": [{"step": "local"}],
            },
            "extra": "debug",
        }

        sanitized, removed = sanitize_upstream_publish(
            "thing/product/BXD001/osd", payload, "BXD001"
        )

        self.assertEqual(
            set(sanitized["data"]),
            {"cover_state", "mode_code", "putter_state", "type_subtype_gimbalindex"},
        )
        self.assertIn("data.live_capacity", removed)
        self.assertIn("data.software_pipeline", removed)
        self.assertIn("extra", removed)

    def test_aircraft_osd_keeps_aircraft_fields_and_drops_internal_fields(self):
        payload = {
            "bid": "b",
            "tid": "t",
            "timestamp": 1,
            "gateway": "BXD001",
            "data": {
                "latitude": 30.0,
                "longitude": 120.0,
                "battery": {"return_home_power": 38},
                "cameras": [{"payload_index": "0-42-0", "zoom_factor": 2.0}],
                "cover_state": 1,
                "viewlink_result": {"success": True},
            },
        }

        sanitized, removed = sanitize_upstream_publish(
            "thing/product/DRONE001/osd", payload, "BXD001"
        )

        self.assertEqual(
            set(sanitized["data"]),
            {"latitude", "longitude", "battery", "cameras"},
        )
        self.assertIn("data.cover_state", removed)
        self.assertIn("data.viewlink_result", removed)

    def test_state_only_keeps_push_mode_one_properties(self):
        payload = {
            "bid": "b",
            "tid": "t",
            "timestamp": 1,
            "gateway": "BXD001",
            "data": {
                "property": {
                    "live_capacity": {"available": True},
                    "wireless_link_topo": {},
                    "cover_state": 1,
                    "mode_code": 0,
                }
            },
        }

        sanitized, removed = sanitize_upstream_publish(
            "thing/product/BXD001/state", payload, "BXD001"
        )

        self.assertEqual(set(sanitized["data"]), {"live_capacity", "wireless_link_topo"})
        self.assertIn("data.cover_state", removed)
        self.assertIn("data.mode_code", removed)

    def test_events_keep_protocol_envelope_and_strip_internal_debug_fields(self):
        payload = {
            "bid": "b",
            "tid": "t",
            "timestamp": 1,
            "gateway": "BXD001",
            "method": "live_status_notify",
            "need_reply": 1,
            "data": {
                "result": 0,
                "output": {
                    "status": "locally_done",
                    "progress": {"percent": 50, "step_key": "push"},
                    "reply": {"internal": True},
                },
                "software_pipeline": [{"step": "local"}],
                "viewlink_result": {"success": True},
                "items": [{"raw": 1, "name": "kept"}],
            },
            "publisher_instance": "local-debug",
        }

        sanitized, removed = sanitize_upstream_publish(
            "thing/product/BXD001/events", payload, "BXD001"
        )

        self.assertEqual(sanitized["data"]["output"]["status"], "failed")
        self.assertEqual(sanitized["data"]["items"], [{"name": "kept"}])
        self.assertNotIn("software_pipeline", sanitized["data"])
        self.assertNotIn("viewlink_result", sanitized["data"])
        self.assertIn("data.output.reply", removed)
        self.assertIn("data.items[0].raw", removed)
        self.assertIn("publisher_instance", removed)

    def test_mqtt_publish_sanitizes_upstream_payload_at_final_exit(self):
        client = MQTTClient()
        fake = FakePahoClient()
        client.client = fake
        client.connected = True

        ok = client.publish("thing/product/BXD001/state", {
            "bid": "b",
            "tid": "t",
            "timestamp": 1,
            "gateway": "BXD001",
            "data": {
                "property": {
                    "live_capacity": {"available": True},
                    "cover_state": 1,
                }
            },
        })

        self.assertTrue(ok)
        self.assertEqual(fake.calls[0][0], "thing/product/BXD001/state")
        self.assertEqual(fake.calls[0][1]["data"], {"live_capacity": {"available": True}})


if __name__ == "__main__":
    unittest.main()
