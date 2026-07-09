import contextlib
import io
import json
import os
import sys
import types
import unittest


PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOLS_DIR = os.path.join(PROJECT_ROOT, "tools")
for path in (PROJECT_ROOT, TOOLS_DIR):
    if path not in sys.path:
        sys.path.insert(0, path)

from mqtt_dock3_osd_state_test import Dock3OsdStateClient  # noqa: E402


class FakeMqttClient:
    connect_rc = 0

    def __init__(self, *args, **kwargs):
        self.published = []
        self.subscriptions = []

    def connect(self, host, port, keepalive=30):
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


def envelope(sn, data):
    return {
        "tid": "tid-1",
        "bid": "bid-1",
        "gateway": sn,
        "timestamp": 1783420000000,
        "data": data,
    }


def valid_dock_osd(sn="EDGEGW001"):
    return envelope(
        sn,
        {
            "mode_code": 0,
            "flighttask_step_code": 0,
            "cover_state": 0,
            "putter_state": 0,
            "drone_in_dock": 1,
            "network_state": {"type": 2, "quality": 100, "rate": 10},
            "drone_charge_state": {"capacity_percent": 80, "state": 0},
            "position_state": {"is_fixed": 0, "gps_number": 0},
            "electricity": 90,
            "wind_speed": 0,
            "drc_state": 0,
            "maintain_status": {"maintain_status_array": []},
        },
    )


def valid_drone_osd(sn="EDGEGW001"):
    return envelope(
        sn,
        {
            "latitude": 0.0,
            "longitude": 0.0,
            "height": 0.0,
            "altitude": 0.0,
            "horizontal_speed": 0.0,
            "vertical_speed": 0.0,
            "attitude_pitch": 0.0,
            "attitude_roll": 0.0,
            "attitude_head": 0.0,
            "battery": {"capacity_percent": 80, "voltage": 24000},
            "position_state": {"gps_number": 0, "is_fixed": 0},
            "wind_speed": 0.0,
            "mode_code": 0,
            "gear": 0,
            "flight_time": 0,
            "timestamp": 1783420000000,
        },
    )


class Dock3OsdStateClientTest(unittest.TestCase):
    def setUp(self):
        self.client = Dock3OsdStateClient(
            "127.0.0.1",
            1883,
            "EDGEGW001",
            "DRONESIM001",
            1,
            "silent_mode",
            quiet_stale=True,
            mqtt_module=FakeMqttModule,
        )

    def test_connect_subscribes_replies_and_observed_topics(self):
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertTrue(self.client.connect(0.1))

        topics = {topic for topic, _ in self.client.client.subscriptions}
        self.assertIn("thing/product/EDGEGW001/services_reply", topics)
        self.assertIn("thing/product/EDGEGW001/property/set_reply", topics)
        self.assertIn("thing/product/EDGEGW001/drc/up", topics)
        self.assertIn("thing/product/EDGEGW001/osd", topics)
        self.assertIn("thing/product/DRONESIM001/osd", topics)
        self.assertIn("thing/product/EDGEGW001/state", topics)

    def test_observed_osd_and_state_samples_are_validated(self):
        state = envelope("EDGEGW001", {"property": {"silent_mode": 1}})

        with contextlib.redirect_stdout(io.StringIO()):
            self.client._on_message(None, None, mqtt_message(self.client.dock_osd_topic, valid_dock_osd()))
            self.client._on_message(None, None, mqtt_message(self.client.drone_osd_topic, valid_drone_osd()))
            self.client._on_message(None, None, mqtt_message(self.client.state_topic, state))

        self.assertEqual(self.client.counts(), (1, 1, 1))
        self.assertEqual(self.client.observed_count(self.client.dock_osd_topic), 1)
        self.assertEqual(self.client.stats["observed"], 3)
        self.assertEqual(self.client.invalid_samples, [])

    def test_invalid_dock_osd_is_recorded(self):
        payload = valid_dock_osd()
        payload["data"]["battery"] = {"capacity_percent": 80}

        with contextlib.redirect_stdout(io.StringIO()):
            self.client._on_message(None, None, mqtt_message(self.client.dock_osd_topic, payload))

        self.assertEqual(self.client.counts(), (0, 0, 0))
        self.assertEqual(len(self.client.invalid_samples), 1)
        self.assertIn("dock OSD must use Dock3 dock properties", self.client.invalid_samples[0])

    def test_non_object_observed_payload_is_recorded(self):
        self.client._on_message(None, None, mqtt_message(self.client.dock_osd_topic, ["not-object"]))

        self.assertEqual(self.client.counts(), (0, 0, 0))
        self.assertEqual(len(self.client.invalid_samples), 1)
        self.assertIn("payload must be object", self.client.invalid_samples[0])

    def test_legacy_property_changed_state_is_ignored(self):
        payload = {
            "tid": "old-tid",
            "timestamp": 1783420000000,
            "gateway": "EDGEGW001",
            "reason": "property_changed",
            "data": {"cover_state": 0, "drone_in_dock": 1},
        }

        self.client._on_message(None, None, mqtt_message(self.client.state_topic, payload))

        self.assertEqual(self.client.counts(), (0, 0, 0))
        self.assertEqual(self.client.invalid_samples, [])
        self.assertEqual(self.client.observed_count(self.client.state_topic), 1)
        self.assertEqual(self.client.stats["ignored_observed_legacy"], 1)
        self.assertEqual(self.client.stale_total(), 1)

    def test_legacy_partial_value_wrapped_dock_osd_is_ignored(self):
        payload = envelope(
            "EDGEGW001",
            {
                "cover_state": {"value": 0},
                "putter_state": {"value": 0},
                "charging_state": {"value": 0},
                "drone_in_dock": {"value": 1},
                "wind_speed": {"value": 28.6},
            },
        )

        self.client._on_message(None, None, mqtt_message(self.client.dock_osd_topic, payload))

        self.assertEqual(self.client.counts(), (0, 0, 0))
        self.assertEqual(self.client.invalid_samples, [])
        self.assertEqual(self.client.observed_count(self.client.dock_osd_topic), 1)
        self.assertEqual(self.client.stats["ignored_observed_legacy"], 1)
        self.assertEqual(self.client.stale_total(), 1)

    def test_legacy_partial_value_wrapped_drone_osd_is_ignored(self):
        payload = envelope(
            "EDGEGW001",
            {
                "latitude": {"value": 39.9042},
                "longitude": {"value": 116.3974},
                "height": {"value": 50.0},
                "horizontal_speed": {"value": 0.0},
                "vertical_speed": {"value": 0.0},
                "attitude_head": {"value": 0.0},
                "mode_code": {"value": 0},
                "gear": {"value": 1},
            },
        )

        self.client._on_message(None, None, mqtt_message(self.client.drone_osd_topic, payload))

        self.assertEqual(self.client.counts(), (0, 0, 0))
        self.assertEqual(self.client.invalid_samples, [])
        self.assertEqual(self.client.observed_count(self.client.drone_osd_topic), 1)
        self.assertEqual(self.client.stats["ignored_observed_legacy"], 1)
        self.assertEqual(self.client.stale_total(), 1)

    def test_property_set_uses_shared_request_and_checks_property_result(self):
        original_publish = self.client.client.publish

        def publish_and_reply(topic, payload, qos=0):
            original_publish(topic, payload, qos)
            request = json.loads(payload)
            reply = {
                "tid": request["tid"],
                "method": "property_set",
                "gateway": "EDGEGW001",
                "msg": "",
                "data": {"silent_mode": {"state": {"result": 0}}},
            }
            self.client._on_message(
                None,
                None,
                mqtt_message("thing/product/EDGEGW001/property/set_reply", reply),
            )

        self.client.client.publish = publish_and_reply

        with contextlib.redirect_stdout(io.StringIO()):
            rc, reply = self.client.publish_property_set("silent_mode", 1, 0.1)

        self.assertEqual(rc, 0)
        self.assertEqual(reply["data"]["silent_mode"]["state"]["result"], 0)
        self.assertEqual(self.client.client.published[-1][0], "thing/product/EDGEGW001/property/set")


if __name__ == "__main__":
    unittest.main()
