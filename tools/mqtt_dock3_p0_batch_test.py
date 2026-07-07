#!/usr/bin/env python3
"""Batch smoke test for Dock3 P0 MQTT compatibility."""

import argparse
import json
import sys
import time
import uuid
from threading import Event

from tool_config import add_config_argument, resolve_mqtt_settings

mqtt = None

TOPICS = {
    "services": ("thing/product/{sn}/services", "thing/product/{sn}/services_reply"),
    "property": ("thing/product/{sn}/property/set", "thing/product/{sn}/property/set_reply"),
    "drc": ("thing/product/{sn}/drc/down", "thing/product/{sn}/drc/up"),
}


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
    node = message.get("data", {}).get(name, {})
    if not isinstance(node, dict):
        return None
    state = node.get("state", {})
    if isinstance(state, dict):
        return state.get("result")
    return None


def looks_like_legacy_stale_reply(message):
    """Detect replies emitted by old gateway/simulator code sharing the broker."""
    if not isinstance(message, dict):
        return False
    data = message.get("data")
    if not isinstance(data, dict):
        return False

    # Current replies include top-level gateway/msg and never expose negative
    # MQTT result codes. Older code replied with data.result=-1/-2 or data.msg.
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
        self.stats = {
            "accepted": 0,
            "ignored_late": 0,
            "ignored_mismatched": 0,
            "ignored_legacy": 0,
        }
        self.connected = Event()
        self.pending = {}
        self.client = build_client(f"edge_gateway_dock3_p0_{uuid.uuid4().hex[:8]}")

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
            print(f"dock3 p0 subscribed -> {topic}")
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


def assert_property_reply(payload, property_name, expected_results):
    actual = property_result(payload, property_name)
    if actual not in set(expected_results):
        print(
            f"property_set {property_name} unexpected result: "
            f"expected={sorted(set(expected_results))}, actual={actual}",
            file=sys.stderr,
        )
        return False
    return True


def run_property_checks(client, args):
    checks = [
        ("silent_mode", 1, {0}),
        ("air_transfer_enable", True, {0}),
        ("user_experience_improvement", 2, {0}),
    ]
    for name, value, expected in checks:
        rc, reply = client.request("property", "property_set", {name: value}, args.timeout)
        if rc != 0:
            return rc
        if not assert_property_reply(reply, name, expected):
            return 5

    if not args.skip_readonly_property_check:
        readonly_checks = [
            ("battery_store_mode", 2),
            ("alarm_state", 1),
        ]
        for name, value in readonly_checks:
            rc, reply = client.request("property", "property_set", {name: value}, args.timeout)
            if rc != 0:
                return rc
            actual = property_result(reply, name)
            if actual in (None, 0):
                print(f"expected readonly property {name} to fail, actual={actual}", file=sys.stderr)
                return 6

    rc, reply = client.request(
        "services",
        "property_get",
        {"properties": ["silent_mode", "drc_state", "air_transfer_enable", "user_experience_improvement"]},
        args.timeout,
        allowed_results={0},
    )
    if rc != 0:
        return rc
    if reply.get("data", {}).get("silent_mode") != 1:
        print(f"expected silent_mode=1, actual={reply.get('data', {}).get('silent_mode')}", file=sys.stderr)
        return 7
    return 0


def run_status_checks(client, args):
    rc, _ = client.request("services", "dock_status_query", {}, args.timeout, allowed_results={0})
    if rc != 0:
        return rc

    if args.include_legacy_bind_status:
        rc, _ = client.request("services", "airport_bind_status", {}, args.timeout, allowed_results={0})
        if rc != 0:
            print(
                "legacy services airport_bind_status failed. "
                "Dock3 official organization validation uses requests/requests_reply; "
                "rerun without --include-legacy-bind-status or use mqtt_cloud_simulator.py for official validation.",
                file=sys.stderr,
            )
            return rc
    return 0


def run_drc_checks(client, args):
    rc, _ = client.request("services", "drc_mode_exit", {}, args.timeout, allowed_results={0, 319999})
    if rc != 0:
        return rc

    rc, _ = client.request("services", "drc_mode_enter", {}, args.timeout, allowed_results={0})
    if rc != 0:
        return rc

    rc, state_reply = client.request(
        "services",
        "property_get",
        {"properties": ["drc_state"]},
        args.timeout,
        allowed_results={0},
    )
    if rc != 0:
        return rc
    if state_reply.get("data", {}).get("drc_state") != 2:
        print(f"expected drc_state=2 after enter, actual={state_reply.get('data', {}).get('drc_state')}", file=sys.stderr)
        return 8

    if not args.skip_drc_control:
        control_cases = [
            ("stick_control", {"vx": 0.1, "vy": 0.0, "vz": 0.0, "yaw_rate": 0.0}),
            ("drone_control", {
                "horizontal_speed": 0.1,
                "horizontal_direction": 0,
                "vertical_speed": 0.0,
                "vertical_direction": 0,
                "yaw": 0.0,
            }),
            ("payload_control", {"payload_index": "0", "action": "photo_take", "params": {}}),
            ("drc_speed_push", {"vx": 0.0, "vy": 0.0, "vz": 0.0, "yaw_rate": 0.0}),
            ("drc_gimbal_speed_push", {"pitch_speed": 0.0, "yaw_speed": 0.0, "roll_speed": 0.0}),
        ]
        for method, payload in control_cases:
            rc, _ = client.request("drc", method, payload, args.timeout, allowed_results={0})
            if rc != 0:
                return rc

    rc, _ = client.request("services", "drc_mode_exit", {}, args.timeout, allowed_results={0})
    if rc != 0:
        return rc

    rc, state_reply = client.request(
        "services",
        "property_get",
        {"properties": ["drc_state"]},
        args.timeout,
        allowed_results={0},
    )
    if rc != 0:
        return rc
    if state_reply.get("data", {}).get("drc_state") != 0:
        print(f"expected drc_state=0 after exit, actual={state_reply.get('data', {}).get('drc_state')}", file=sys.stderr)
        return 9

    if not args.skip_wrong_topic_check:
        rc, _ = client.request("drc", "drc_mode_enter", {}, args.timeout, allowed_results={325001})
        if rc != 0:
            return rc
    return 0


def main():
    global mqtt
    parser = argparse.ArgumentParser(description="Batch test Dock3 P0 MQTT APIs")
    add_config_argument(parser)
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--sn", default=None)
    parser.add_argument("--timeout", type=float, default=12.0)
    parser.add_argument("--qos", type=int, choices=[0, 1, 2], default=1)
    parser.add_argument("--skip-status", action="store_true")
    parser.add_argument("--skip-properties", action="store_true")
    parser.add_argument("--skip-drc", action="store_true")
    parser.add_argument("--skip-drc-control", action="store_true")
    parser.add_argument("--skip-wrong-topic-check", action="store_true")
    parser.add_argument("--skip-readonly-property-check", action="store_true")
    parser.add_argument("--quiet-stale", action="store_true", help="Only count stale duplicate replies instead of printing their JSON payloads.")
    parser.add_argument("--fail-on-stale", action="store_true", help="Fail if duplicate legacy/stale replies are observed. Useful after the broker environment is cleaned.")
    parser.add_argument(
        "--include-legacy-bind-status",
        action="store_true",
        help="Also test the legacy services airport_bind_status compatibility path. Dock3 official path is requests/requests_reply.",
    )
    args = parser.parse_args()
    args.host, args.port, args.sn = resolve_mqtt_settings(args)

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
        if not args.skip_status:
            rc = run_status_checks(client, args)
            if rc != 0:
                return rc

        if not args.skip_properties:
            rc = run_property_checks(client, args)
            if rc != 0:
                return rc

        if not args.skip_drc:
            rc = run_drc_checks(client, args)
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
            "dock3 p0 batch test ok "
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
