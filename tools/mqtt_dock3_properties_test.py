#!/usr/bin/env python3
"""Category smoke test for DJI Dock3 properties."""

import argparse
import json
import sys
import time
import uuid
from threading import Event

mqtt = None

TOPICS = {
    "services": ("thing/product/{sn}/services", "thing/product/{sn}/services_reply"),
    "property": ("thing/product/{sn}/property/set", "thing/product/{sn}/property/set_reply"),
}

WRITABLE_CASES = [
    ("silent_mode", 1),
    ("air_transfer_enable", True),
    ("user_experience_improvement", 2),
]

REJECT_CASES = [
    ("battery_store_mode", 2),
    ("alarm_state", 1),
    ("drc_state", 2),
    ("silent_mode", 9),
    ("air_transfer_enable", 1),
    ("unknown_property", 1),
]

READBACK_PROPERTIES = [
    "silent_mode",
    "air_transfer_enable",
    "user_experience_improvement",
    "battery_store_mode",
    "alarm_state",
    "drc_state",
    "cover_state",
    "drone_in_dock",
    "mode_code",
]


def build_client(client_id):
    if hasattr(mqtt, "CallbackAPIVersion"):
        try:
            return mqtt.Client(mqtt.CallbackAPIVersion.VERSION1, client_id=client_id)
        except TypeError:
            pass
    return mqtt.Client(client_id=client_id)


def result_code(message):
    if not isinstance(message, dict):
        return None
    return message.get("data", {}).get("result")


def property_result(message, name):
    if not isinstance(message, dict):
        return None
    node = message.get("data", {}).get(name)
    if not isinstance(node, dict):
        return None
    state = node.get("state")
    if not isinstance(state, dict):
        return None
    return state.get("result")


def looks_like_legacy_stale_reply(message):
    if not isinstance(message, dict):
        return False
    data = message.get("data")
    if not isinstance(data, dict):
        return False

    if "gateway" not in message and "msg" not in message:
        result = data.get("result")
        if isinstance(result, int) and result < 0:
            return True
        if any(isinstance(item, int) and item < 0 for item in data.values()):
            return True
        if "msg" in data:
            return True
    return False


class MqttRequestClient:
    def __init__(self, host, port, sn, qos, quiet_stale=False):
        self.host = host
        self.port = port
        self.sn = sn
        self.qos = qos
        self.quiet_stale = quiet_stale
        self.connected = Event()
        self.pending = {}
        self.stats = {
            "accepted": 0,
            "ignored_late": 0,
            "ignored_mismatched": 0,
            "ignored_legacy": 0,
        }
        self.client = build_client(f"edge_gateway_dock3_properties_{uuid.uuid4().hex[:8]}")

    def connect(self, timeout):
        self.client.on_connect = self._on_connect
        self.client.on_message = self._on_message
        self.client.connect(self.host, self.port, keepalive=30)
        self.client.loop_start()
        return self.connected.wait(timeout)

    def disconnect(self):
        self.client.loop_stop()
        self.client.disconnect()

    def _on_connect(self, client, userdata, flags, rc):
        if rc != 0:
            print(f"connect failed: rc={rc}", file=sys.stderr)
            self.connected.set()
            return
        for _, reply_tpl in TOPICS.values():
            topic = reply_tpl.format(sn=self.sn)
            self.client.subscribe(topic, qos=self.qos)
            print(f"dock3 properties subscribed -> {topic}")
        self.connected.set()

    def _on_message(self, client, userdata, msg):
        try:
            payload = json.loads(msg.payload.decode("utf-8"))
        except Exception as exc:
            print(f"invalid JSON on {msg.topic}: {exc}", file=sys.stderr)
            return

        tid = payload.get("tid")
        waiter = self.pending.get(tid)
        if not waiter:
            self.stats["ignored_late"] += 1
            if not self.quiet_stale:
                print(f"ignored <- {msg.topic}, method={payload.get('method')}, tid={tid}")
            return

        expected_method = waiter["method"]
        actual_method = payload.get("method")
        if actual_method != expected_method:
            self.stats["ignored_mismatched"] += 1
            if not self.quiet_stale:
                print(
                    f"ignored stale/mismatched reply <- {msg.topic}, "
                    f"expected_method={expected_method}, actual_method={actual_method}, tid={tid}"
                )
                print(json.dumps(payload, ensure_ascii=False, indent=2))
            return

        if looks_like_legacy_stale_reply(payload):
            self.stats["ignored_legacy"] += 1
            if not self.quiet_stale:
                print(
                    f"ignored legacy stale reply <- {msg.topic}, "
                    f"method={actual_method}, tid={tid}"
                )
                print(json.dumps(payload, ensure_ascii=False, indent=2))
            return

        self.stats["accepted"] += 1
        waiter["topic"] = msg.topic
        waiter["payload"] = payload
        waiter["event"].set()

    def request(self, channel, method, data, timeout, allowed_results=None):
        publish_tpl, reply_tpl = TOPICS[channel]
        publish_topic = publish_tpl.format(sn=self.sn)
        expected_reply_topic = reply_tpl.format(sn=self.sn)
        tid = str(uuid.uuid4())
        message = {
            "tid": tid,
            "bid": str(uuid.uuid4()),
            "timestamp": int(time.time() * 1000),
            "gateway": self.sn,
            "method": method,
            "need_reply": 1,
            "data": data or {},
        }
        waiter = {"event": Event(), "topic": "", "payload": None, "method": method}
        self.pending[tid] = waiter

        self.client.publish(publish_topic, json.dumps(message, ensure_ascii=False), qos=self.qos)
        print(f"published {method} -> {publish_topic}, tid={tid}")

        if not waiter["event"].wait(timeout):
            self.pending.pop(tid, None)
            print(f"timeout waiting for {method}, tid={tid}", file=sys.stderr)
            return 2, None

        self.pending.pop(tid, None)
        payload = waiter["payload"]
        print(f"received <- {waiter['topic']}, method={payload.get('method')}, tid={tid}")
        print(json.dumps(payload, ensure_ascii=False, indent=2))

        if waiter["topic"] != expected_reply_topic:
            print(
                f"{method} unexpected reply topic: expected={expected_reply_topic}, actual={waiter['topic']}",
                file=sys.stderr,
            )
            return 3, payload

        if allowed_results is not None:
            actual = result_code(payload)
            if actual not in set(allowed_results):
                print(f"{method} unexpected result: expected={sorted(set(allowed_results))}, actual={actual}", file=sys.stderr)
                return 4, payload
        return 0, payload


def assert_property_result(reply, property_name, expected_results):
    actual = property_result(reply, property_name)
    if actual not in set(expected_results):
        print(
            f"property_set {property_name} unexpected result: "
            f"expected={sorted(set(expected_results))}, actual={actual}",
            file=sys.stderr,
        )
        return False
    return True


def run_property_set_checks(client, args):
    for name, value in WRITABLE_CASES:
        rc, reply = client.request("property", "property_set", {name: value}, args.timeout)
        if rc != 0:
            return rc
        if not assert_property_result(reply, name, {0}):
            return 5

    for name, value in REJECT_CASES:
        rc, reply = client.request("property", "property_set", {name: value}, args.timeout)
        if rc != 0:
            return rc
        if not assert_property_result(reply, name, {1}):
            return 6

    return 0


def run_property_get_checks(client, args):
    rc, reply = client.request(
        "services",
        "property_get",
        {"properties": READBACK_PROPERTIES},
        args.timeout,
        allowed_results={0},
    )
    if rc != 0:
        return rc

    data = reply.get("data", {})
    expected_values = {
        "silent_mode": 1,
        "air_transfer_enable": True,
        "user_experience_improvement": 2,
    }
    for name, expected in expected_values.items():
        actual = data.get(name)
        if actual != expected:
            print(f"property_get {name} mismatch: expected={expected!r}, actual={actual!r}", file=sys.stderr)
            return 7

    missing = [name for name in READBACK_PROPERTIES if name not in data]
    if missing:
        print(f"property_get missing properties: {missing}", file=sys.stderr)
        return 8

    return 0


def main():
    global mqtt
    parser = argparse.ArgumentParser(description="Test DJI Dock3 properties category")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=1883)
    parser.add_argument("--sn", default="EDGE-GW-001")
    parser.add_argument("--timeout", type=float, default=12.0)
    parser.add_argument("--qos", type=int, choices=[0, 1, 2], default=1)
    parser.add_argument("--quiet-stale", action="store_true", help="Only count stale duplicate replies instead of printing their JSON payloads.")
    parser.add_argument("--fail-on-stale", action="store_true", help="Fail if duplicate legacy/stale replies are observed after the broker environment is cleaned.")
    args = parser.parse_args()

    try:
        import paho.mqtt.client as mqtt_module
    except ImportError as exc:
        raise SystemExit("paho-mqtt is required. Install requirements-ubuntu.txt first.") from exc
    mqtt = mqtt_module

    client = MqttRequestClient(args.host, args.port, args.sn, args.qos, quiet_stale=args.quiet_stale)
    if not client.connect(args.timeout):
        print("MQTT connect timeout", file=sys.stderr)
        return 2

    try:
        rc = run_property_set_checks(client, args)
        if rc != 0:
            return rc

        rc = run_property_get_checks(client, args)
        if rc != 0:
            return rc

        stale_total = (
            client.stats["ignored_mismatched"]
            + client.stats["ignored_legacy"]
            + client.stats["ignored_late"]
        )
        if args.fail_on_stale and stale_total:
            print(
                "stale duplicate replies observed "
                f"(ignored_mismatched={client.stats['ignored_mismatched']}, "
                f"ignored_legacy={client.stats['ignored_legacy']}, "
                f"ignored_late={client.stats['ignored_late']})",
                file=sys.stderr,
            )
            return 10

        print(
            "dock3 properties test ok "
            f"(accepted={client.stats['accepted']}, "
            f"ignored_mismatched={client.stats['ignored_mismatched']}, "
            f"ignored_legacy={client.stats['ignored_legacy']}, "
            f"ignored_late={client.stats['ignored_late']}, "
            f"ignored_total={stale_total})"
        )
        return 0
    finally:
        client.disconnect()


if __name__ == "__main__":
    raise SystemExit(main())
