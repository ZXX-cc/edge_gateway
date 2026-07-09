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

import mqtt_organization_test as org_tool  # noqa: E402


class FakeTopicBuilder:
    @staticmethod
    def requests(sn):
        return f"thing/product/{sn}/requests"

    @staticmethod
    def requests_reply(sn):
        return f"thing/product/{sn}/requests_reply"

    @staticmethod
    def events(sn):
        return f"thing/product/{sn}/events"

    @staticmethod
    def status(sn):
        return f"sys/product/{sn}/status"

    @staticmethod
    def status_reply(sn):
        return f"sys/product/{sn}/status_reply"


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
        if hasattr(self, "on_disconnect"):
            self.on_disconnect(self, None, 0)

    def subscribe(self, topic, qos=0):
        self.subscriptions.append((topic, qos))

    def publish(self, topic, payload, qos=0):
        self.published.append((topic, json.loads(payload), qos))
        return types.SimpleNamespace(rc=0)


class FakeMqttModule:
    Client = FakeMqttClient


class FakeSubDevice:
    def __init__(self, sn, domain="0", device_type=60, sub_type=0):
        self.sn = sn
        self.domain = domain
        self.type = device_type
        self.sub_type = sub_type


class FakeDevice:
    def __init__(self, sub_devices=None):
        self._sub_devices = sub_devices or []

    def get_all_sub_devices(self):
        return self._sub_devices


def mqtt_message(topic, payload):
    return types.SimpleNamespace(topic=topic, payload=json.dumps(payload).encode("utf-8"))


class OrganizationRequestClientTest(unittest.TestCase):
    def setUp(self):
        org_tool.TopicBuilder = FakeTopicBuilder
        self.client = org_tool.OrganizationRequestClient(
            "127.0.0.1",
            1883,
            "EDGEGW001",
            1,
            mqtt_module=FakeMqttModule,
        )

    def test_connect_subscribes_only_organization_topics(self):
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertTrue(self.client.connect(0.1))

        self.assertTrue(self.client.connected)
        self.assertEqual(
            sorted(self.client.client.subscriptions),
            [
                ("sys/product/EDGEGW001/status_reply", 1),
                ("thing/product/EDGEGW001/requests_reply", 1),
            ],
        )

    def test_publish_methods_use_expected_topics(self):
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertTrue(self.client.publish_requests({"method": "airport_organization_get", "tid": "tid-1"}))
            self.assertTrue(self.client.publish_event({"method": "device_binding", "tid": "tid-2"}))
            self.assertTrue(self.client.publish_status({"method": "update_topo", "tid": "tid-3"}))

        self.assertEqual(
            [topic for topic, _payload, _qos in self.client.client.published],
            [
                "thing/product/EDGEGW001/requests",
                "thing/product/EDGEGW001/events",
                "sys/product/EDGEGW001/status",
            ],
        )

    def test_observed_replies_dispatch_to_handlers(self):
        replies = []
        status_replies = []
        self.client.set_reply_handler(lambda method, msg: replies.append((method, msg)))
        self.client.set_status_reply_handler(lambda method, msg: status_replies.append((method, msg)))

        request_reply = {"method": "airport_organization_get", "tid": "tid-1", "data": {"result": 0}}
        status_reply = {"method": "update_topo", "tid": "tid-2", "data": {"result": 0}}

        with contextlib.redirect_stdout(io.StringIO()):
            self.client._on_message(
                None,
                None,
                mqtt_message("thing/product/EDGEGW001/requests_reply", request_reply),
            )
            self.client._on_message(
                None,
                None,
                mqtt_message("sys/product/EDGEGW001/status_reply", status_reply),
            )

        self.assertEqual(replies, [("airport_organization_get", request_reply)])
        self.assertEqual(status_replies, [("update_topo", status_reply)])

    def test_disconnect_marks_client_offline(self):
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertTrue(self.client.connect(0.1))

        self.client.disconnect()

        self.assertFalse(self.client.connected)

    def test_build_bind_devices_defaults_to_gateway_only(self):
        args = types.SimpleNamespace(
            binding_code="bind-code",
            organization_id="org-001",
            device_callsign="",
            device_model_key="dock3-sim",
            bind_sub_devices=False,
        )

        bind_devices = org_tool.build_bind_devices(
            FakeDevice([FakeSubDevice("DRONE-A")]),
            "EDGEGW001",
            args,
        )

        self.assertEqual(
            bind_devices,
            [
                {
                    "sn": "EDGEGW001",
                    "device_binding_code": "bind-code",
                    "organization_id": "org-001",
                    "device_callsign": "EDGEGW001",
                    "device_model_key": "dock3-sim",
                }
            ],
        )

    def test_build_bind_devices_includes_installed_sub_devices_when_requested(self):
        args = types.SimpleNamespace(
            binding_code="bind-code",
            organization_id="org-001",
            device_callsign="dock-name",
            device_model_key="dock3-sim",
            bind_sub_devices=True,
        )

        bind_devices = org_tool.build_bind_devices(
            FakeDevice([
                FakeSubDevice("DRONE-A", device_type=60),
                FakeSubDevice("PAYLOAD-A", domain="2", device_type=123, sub_type=4),
            ]),
            "EDGEGW001",
            args,
        )

        self.assertEqual([device["sn"] for device in bind_devices], ["EDGEGW001", "DRONE-A", "PAYLOAD-A"])
        self.assertEqual(bind_devices[0]["device_callsign"], "dock-name")
        self.assertEqual(bind_devices[1]["device_model_key"], "0-60-0")
        self.assertEqual(bind_devices[2]["device_model_key"], "2-123-4")
        self.assertTrue(all(device["organization_id"] == "org-001" for device in bind_devices))


if __name__ == "__main__":
    unittest.main()
