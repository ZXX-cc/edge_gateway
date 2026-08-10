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

from mqtt_dock3_osd_state_test import Dock3OsdStateClient, build_update_topo_payload  # noqa: E402
from src.cloud_api.dock3_device import GATEWAY_DOMAIN, GATEWAY_SUB_TYPE, GATEWAY_TYPE, THING_VERSION  # noqa: E402


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
            "home_position_is_valid": 0,
            "heading": 0.0,
            "air_conditioner": {"air_conditioner_state": 0, "switch_time": 0},
            "drone_battery_maintenance_info": {
                "maintenance_state": 0,
                "maintenance_time_left": 0,
                "heat_state": 0,
                "batteries": [],
            },
            "maintain_status": {"maintain_status_array": []},
            "position_state": {
                "is_calibration": 0,
                "is_fixed": 0,
                "quality": 0,
                "gps_number": 0,
                "rtk_number": 0,
            },
            "emergency_stop_state": 0,
            "drone_charge_state": {"capacity_percent": 80, "state": 0},
            "backup_battery": {"switch": 0, "voltage": 0, "temperature": 0.0},
            "alarm_state": 0,
            "battery_store_mode": 1,
            "activation_time": 0,
            "height": 0.0,
            "alternate_land_point": {
                "longitude": 0.0,
                "latitude": 0.0,
                "safe_land_height": 0.0,
                "is_configured": 0,
                "height": 0.0,
            },
            "first_power_on": 1783420000000,
            "storage": {"total": 1000, "used": 100},
            "working_current": 0.0,
            "working_voltage": 0,
            "humidity": 50.0,
            "temperature": 25.0,
            "environment_temperature": 24.0,
            "wind_speed": 0.0,
            "rainfall": 0,
            "wireless_link": {},
            "media_file_detail": {"remain_upload": 0},
            "job_number": 0,
            "drone_in_dock": 1,
            "network_state": {"type": 2, "quality": 5, "rate": 10},
            "supplement_light_state": 0,
            "cover_state": 0,
            "sub_device": {
                "device_sn": "DRONESIM001",
                "product_type": "91",
                "device_online_status": 1,
                "device_paired": 1,
            },
            "flighttask_step_code": 5,
            "mode_code": 0,
            "latitude": 0.0,
            "longitude": 0.0,
            "drc_state": 0,
            "self_converge_coordinate": {
                "longitude": 0.0,
                "latitude": 0.0,
                "height": 0.0,
            },
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
            "battery": {
                "capacity_percent": 80,
                "voltage": 24000,
                "remain_flight_time": 0,
                "return_home_power": 30,
                "landing_power": 15,
                "batteries": [{"capacity_percent": 80, "index": 0, "sn": "BAT001"}],
            },
            "position_state": {"gps_number": 0, "is_fixed": 0, "quality": 0},
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

    def test_topology_seed_uses_dock3_gateway_constants(self):
        args = types.SimpleNamespace(
            gateway_domain=GATEWAY_DOMAIN,
            gateway_type=GATEWAY_TYPE,
            gateway_sub_type=GATEWAY_SUB_TYPE,
            gateway_secret="gw-secret",
            gateway_nonce="gw-nonce",
            thing_version=THING_VERSION,
            drone_sn="DRONESIM001",
            drone_domain="0",
            drone_type=91,
            drone_sub_type=0,
            drone_index="A",
            drone_secret="drone-secret",
            drone_nonce="drone-nonce",
        )

        payload = build_update_topo_payload(args)

        self.assertEqual(payload["domain"], GATEWAY_DOMAIN)
        self.assertEqual(payload["type"], GATEWAY_TYPE)
        self.assertEqual(payload["sub_type"], GATEWAY_SUB_TYPE)
        self.assertEqual(payload["thing_version"], THING_VERSION)

    def test_drone_osd_observation_can_be_disabled_for_dock_only_acceptance(self):
        client = Dock3OsdStateClient(
            "127.0.0.1",
            1883,
            "EDGEGW001",
            "DRONESIM001",
            1,
            "",
            quiet_stale=True,
            observe_drone_osd=False,
            mqtt_module=FakeMqttModule,
        )

        with contextlib.redirect_stdout(io.StringIO()):
            self.assertTrue(client.connect(0.1))

        topics = {topic for topic, _ in client.client.subscriptions}
        self.assertIn("thing/product/EDGEGW001/osd", topics)
        self.assertIn("thing/product/EDGEGW001/state", topics)
        self.assertNotIn("thing/product/DRONESIM001/osd", topics)

    def test_observed_osd_and_state_samples_are_validated(self):
        state = envelope("EDGEGW001", {"silent_mode": 1})

        with contextlib.redirect_stdout(io.StringIO()):
            self.client._on_message(None, None, mqtt_message(self.client.dock_osd_topic, valid_dock_osd()))
            self.client._on_message(None, None, mqtt_message(self.client.drone_osd_topic, valid_drone_osd()))
            self.client._on_message(None, None, mqtt_message(self.client.state_topic, state))

        self.assertEqual(self.client.counts(), (1, 1, 1))
        self.assertEqual(self.client.observed_count(self.client.dock_osd_topic), 1)
        self.assertEqual(self.client.stats["observed"], 3)
        self.assertEqual(self.client.invalid_samples, [])

    def test_drone_osd_accepts_real_payload_cameras(self):
        payload = valid_drone_osd()
        payload["data"]["cameras"] = [
            {
                "payload_index": "52-0-0",
                "camera_mode": 1,
                "photo_state": 0,
                "recording_state": 1,
                "record_time": 12,
                "zoom_factor": 3.5,
            }
        ]

        with contextlib.redirect_stdout(io.StringIO()):
            self.client._on_message(None, None, mqtt_message(self.client.drone_osd_topic, payload))

        self.assertEqual(self.client.counts()[1], 1)
        self.assertEqual(self.client.invalid_samples, [])

    def test_invalid_dock_osd_is_recorded(self):
        payload = valid_dock_osd()
        payload["data"]["battery"] = {"capacity_percent": 80}

        with contextlib.redirect_stdout(io.StringIO()):
            self.client._on_message(None, None, mqtt_message(self.client.dock_osd_topic, payload))

        self.assertEqual(self.client.counts(), (0, 0, 0))
        self.assertEqual(len(self.client.invalid_samples), 1)
        self.assertIn("dock OSD must use Dock3 dock properties", self.client.invalid_samples[0])

    def test_live_drone_gate_waits_past_placeholder_until_dynamic_osd(self):
        client = Dock3OsdStateClient(
            "127.0.0.1",
            1883,
            "EDGEGW001",
            "DRONESIM001",
            1,
            "silent_mode",
            quiet_stale=True,
            require_live_drone=True,
            mqtt_module=FakeMqttModule,
        )
        placeholder = valid_drone_osd()
        live = valid_drone_osd()
        live["data"]["latitude"] = 43.817
        live["data"]["longitude"] = 125.3235
        live["data"]["battery"]["voltage"] = 24000
        live["data"]["position_state"].update({"gps_number": 15, "is_fixed": 2})

        with contextlib.redirect_stdout(io.StringIO()):
            client._on_message(None, None, mqtt_message(client.drone_osd_topic, placeholder))
            self.assertEqual(client.counts()[1], 0)
            client._on_message(None, None, mqtt_message(client.drone_osd_topic, live))

        self.assertEqual(client.counts()[1], 1)
        self.assertEqual(len(client.placeholder_drone_samples), 1)
        self.assertEqual(client.invalid_samples, [])

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
        self.assertEqual(self.client.ignored_legacy_samples[0]["message_reason"], "property_changed")

    def test_unrelated_current_property_state_is_not_stale(self):
        payload = envelope("EDGEGW001", {"firmware_version": "1.2.3"})

        with contextlib.redirect_stdout(io.StringIO()):
            self.client._on_message(None, None, mqtt_message(self.client.state_topic, payload))

        self.assertEqual(self.client.counts(), (0, 0, 0))
        self.assertEqual(len(self.client.valid_state), 1)
        self.assertEqual(self.client.invalid_samples, [])
        self.assertEqual(self.client.stale_total(), 0)

    def test_pushmode_zero_property_is_rejected_on_state(self):
        payload = envelope("EDGEGW001", {"mode_code": 4})

        with contextlib.redirect_stdout(io.StringIO()):
            self.client._on_message(None, None, mqtt_message(self.client.state_topic, payload))

        self.assertEqual(self.client.counts(), (0, 0, 0))
        self.assertEqual(len(self.client.invalid_samples), 1)
        self.assertIn("state contains internal telemetry fields", self.client.invalid_samples[0])

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
