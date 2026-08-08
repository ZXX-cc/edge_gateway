import json
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path

from src.device.nest_hms_mapper import build_nest_hms_alarms
from src.mqtt.client import MQTTClient
from src.utils.config import Config
from src.utils.message import MessageBuilder
from src.nest_simulator import (
    BIT_BUSY,
    BIT_CHARGING,
    BIT_DRONE_HEARTBEAT,
    BIT_ESTOP,
    BIT_FAULT,
    BIT_NEST_HEARTBEAT,
    NestSimulator,
)
from tools.mqtt_fault_injection_acceptance import nested_property


class FaultInjectionSupportTest(unittest.TestCase):
    def test_mqtt_reconnect_removes_stale_upstream_subscriptions(self):
        class FakePahoClient:
            def __init__(self):
                self.subscribed = []
                self.unsubscribed = []

            def subscribe(self, topic, qos=0):
                self.subscribed.append((topic, qos))

            def unsubscribe(self, topic):
                self.unsubscribed.append(topic)

        client = MQTTClient()
        client.gateway_sn = "EDGEGW001"
        client.client = FakePahoClient()
        client.subscribe_downstream()

        self.assertIn("sys/product/EDGEGW001/status", client.client.unsubscribed)
        self.assertIn("thing/product/EDGEGW001/events", client.client.unsubscribed)
        self.assertIn(
            ("thing/product/EDGEGW001/services", 1),
            client.client.subscribed,
        )

    def test_mqtt_subscribes_configured_payload_device_services(self):
        original_config = deepcopy(Config._data)
        try:
            Config._data = {
                "gateway": {"sn": "EDGEGW001"},
                "payload": {
                    "devices": {
                        "gimbal": {
                            "enabled": True,
                            "cloud_api": {"device_sn": "PAYLOAD001"},
                        }
                    }
                },
            }

            class FakePahoClient:
                def __init__(self):
                    self.subscribed = []
                    self.unsubscribed = []

                def subscribe(self, topic, qos=0):
                    self.subscribed.append((topic, qos))

                def unsubscribe(self, topic):
                    self.unsubscribed.append(topic)

            client = MQTTClient()
            client.client = FakePahoClient()
            client.subscribe_downstream()

            self.assertIn(("thing/product/EDGEGW001/services", 1), client.client.subscribed)
            self.assertIn(("thing/product/PAYLOAD001/services", 1), client.client.subscribed)
            self.assertIn("thing/product/PAYLOAD001/events", client.client.unsubscribed)
        finally:
            Config._data = original_config

    def test_mqtt_replies_to_source_device_sn_for_payload_services(self):
        class FakePahoClient:
            def __init__(self):
                self.published = []

            def publish(self, topic, payload, qos=1):
                self.published.append((topic, json.loads(payload), qos))
                return type("PublishInfo", (), {"rc": 0})()

        client = MQTTClient()
        client.gateway_sn = "EDGEGW001"
        client.connected = True
        client.client = FakePahoClient()
        request = {
            "tid": "frame-zoom-1",
            "method": "camera_frame_zoom",
            "data": {"payload_index": "52-0-0"},
        }
        reply = MessageBuilder.build_reply(request, result=0)

        client._publish_reply_for_downstream_topic(
            "thing/product/PAYLOAD001/services",
            reply,
        )

        self.assertEqual(client.client.published[0][0], "thing/product/PAYLOAD001/services_reply")

    def test_acceptance_reads_osd_value_wrapper_and_state_raw_value(self):
        self.assertEqual(
            nested_property({"data": {"drone_in_dock": {"value": 0}}}, "drone_in_dock"),
            0,
        )
        self.assertEqual(
            nested_property({"data": {"property": {"silent_mode": 1}}}, "silent_mode"),
            1,
        )

    def test_simulator_applies_and_clears_runtime_fault_overrides(self):
        with tempfile.TemporaryDirectory() as directory:
            control = Path(directory) / "faults.json"
            simulator = NestSimulator(
                fault_state_file=str(control),
                fault_log=str(Path(directory) / "faults.log"),
            )
            control.write_text(json.dumps({
                "active": True,
                "scenario": "combined",
                "faults": {
                    "nest_heartbeat": False,
                    "drone_heartbeat": False,
                    "estop_state": True,
                    "charging_state": True,
                    "system_busy": True,
                    "fault_state": True,
                    "gate_state": 6,
                    "charge_volt": 0,
                    "error_code": 42,
                },
            }), encoding="utf-8")

            effective = simulator._effective_env_data()
            self.assertFalse(effective["boolstate1"] & (1 << BIT_NEST_HEARTBEAT))
            self.assertFalse(effective["boolstate1"] & (1 << BIT_DRONE_HEARTBEAT))
            self.assertTrue(effective["boolstate1"] & (1 << BIT_ESTOP))
            self.assertTrue(effective["boolstate1"] & (1 << BIT_CHARGING))
            self.assertTrue(effective["boolstate1"] & (1 << BIT_BUSY))
            self.assertTrue(effective["boolstate1"] & (1 << BIT_FAULT))
            self.assertEqual(effective["gate_state"], 6)
            self.assertEqual(effective["charge_volt"], 0)
            self.assertEqual(effective["error_code"], 42)

            control.write_text('{"active":false,"faults":{}}', encoding="utf-8")
            control.touch()
            recovered = simulator._effective_env_data()
            self.assertEqual(recovered, simulator.env_data)

    def test_hms_mapper_covers_simulated_faults(self):
        alarms = build_nest_hms_alarms({
            "data_valid": True,
            "nest_heartbeat": False,
            "plc_heartbeat": False,
            "drone_heartbeat": False,
            "estop_state": True,
            "system_busy": True,
            "fault_state": True,
            "gate_state": 6,
            "charging_state": True,
            "charge_volt": 0,
            "charge_current": 0,
            "error_code": 42,
        }, "EDGEGW001")
        keys = {alarm["hms_key"] for alarm in alarms}
        self.assertTrue({
            "dock3.nest.nest_heartbeat.lost",
            "dock3.nest.plc_heartbeat.lost",
            "dock3.nest.drone_heartbeat.lost",
            "dock3.nest.estop.active",
            "dock3.nest.system_busy.active",
            "dock3.nest.fault.active",
            "dock3.nest.cover.abnormal",
            "dock3.nest.charging.abnormal",
            "dock3.nest.error_code.active",
        }.issubset(keys))

    def test_mqtt_completed_request_reply_is_cached_by_topic_method_and_tid(self):
        client = MQTTClient()
        payload = {"tid": "same-tid", "method": "property_set"}
        reply = {"tid": "same-tid", "method": "property_set", "data": {"result": 0}}
        self.assertTrue(client._claim_downstream_request(
            "thing/product/EDGEGW001/property/set", payload
        ))
        self.assertFalse(client._claim_downstream_request(
            "thing/product/EDGEGW001/property/set", payload
        ))
        client._remember_downstream_reply("thing/product/EDGEGW001/property/set", payload, reply)

        cached = client._cached_downstream_reply(
            "thing/product/EDGEGW001/property/set", payload
        )
        self.assertEqual(cached, reply)
        cached["data"]["result"] = 1
        self.assertEqual(
            client._cached_downstream_reply(
                "thing/product/EDGEGW001/property/set", payload
            )["data"]["result"],
            0,
        )
        self.assertIsNone(client._cached_downstream_reply(
            "thing/product/EDGEGW001/services", payload
        ))


if __name__ == "__main__":
    unittest.main()
