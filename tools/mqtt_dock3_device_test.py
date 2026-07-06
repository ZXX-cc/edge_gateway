#!/usr/bin/env python3
"""Category smoke test for DJI Dock3 device topology."""

import argparse
import json
import sys
import time
import uuid
from pathlib import Path
from threading import Event

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from src.cloud_api.dock3_device import (
    GATEWAY_DOMAIN,
    GATEWAY_SUB_TYPE,
    GATEWAY_TYPE,
    THING_VERSION,
    build_sub_device_payload,
    build_update_topo_payload,
    validate_update_topo_payload,
)
from src.utils.dji_error_codes import INVALID_COMMAND_FORMAT

mqtt = None


def result_code(message):
    if not isinstance(message, dict):
        return None
    return message.get("data", {}).get("result")


def looks_like_legacy_stale_reply(message):
    """Detect old gateway/simulator replies that can share the same broker."""
    if not isinstance(message, dict):
        return False
    data = message.get("data")
    if not isinstance(data, dict):
        return False

    # Current replies include top-level gateway/msg and use DJI error codes.
    # Older fallback code can still emit data.result=-1/-2 or data.msg.
    if "gateway" not in message and "msg" not in message:
        result = data.get("result")
        if isinstance(result, int) and result < 0:
            return True
        if any(isinstance(item, int) and item < 0 for item in data.values()):
            return True
        if "msg" in data:
            return True
    return False


def build_client(client_id):
    if hasattr(mqtt, "CallbackAPIVersion"):
        try:
            return mqtt.Client(mqtt.CallbackAPIVersion.VERSION1, client_id=client_id)
        except TypeError:
            pass
    return mqtt.Client(client_id=client_id)


def now_ms():
    return int(time.time() * 1000)


def build_message(sn, method, data):
    return {
        "tid": str(uuid.uuid4()),
        "bid": str(uuid.uuid4()),
        "timestamp": now_ms(),
        "gateway": sn,
        "method": method,
        "need_reply": 1,
        "data": data,
    }


def build_status_reply(status_msg, sn, result=0):
    return {
        "bid": str(uuid.uuid4()),
        "data": {"result": result},
        "gateway": sn,
        "tid": status_msg.get("tid", ""),
        "timestamp": now_ms(),
        "method": status_msg.get("method", ""),
    }


class DeviceTopologyClient:
    def __init__(self, host, port, sn, qos):
        self.host = host
        self.port = port
        self.sn = sn
        self.qos = qos
        self.services_topic = f"thing/product/{sn}/services"
        self.services_reply_topic = f"thing/product/{sn}/services_reply"
        self.status_topic = f"sys/product/{sn}/status"
        self.status_reply_topic = f"sys/product/{sn}/status_reply"
        self.connected_event = Event()
        self.pending = {}
        self.status_messages = []
        self.status_event = Event()
        self.status_validation_errors = []
        self.stats = {
            "accepted": 0,
            "ignored_late": 0,
            "ignored_mismatched": 0,
            "ignored_legacy": 0,
        }
        self.client = build_client(f"edge_gateway_dock3_device_{uuid.uuid4().hex[:8]}")

    def connect(self, timeout):
        self.client.on_connect = self._on_connect
        self.client.on_message = self._on_message
        self.client.connect(self.host, self.port, keepalive=30)
        self.client.loop_start()
        return self.connected_event.wait(timeout)

    def disconnect(self):
        self.client.loop_stop()
        self.client.disconnect()

    def _on_connect(self, client, userdata, flags, rc):
        if rc != 0:
            print(f"connect failed: rc={rc}", file=sys.stderr)
            self.connected_event.set()
            return
        client.subscribe(self.services_reply_topic, qos=self.qos)
        client.subscribe(self.status_topic, qos=self.qos)
        print(f"dock3 device subscribed -> {self.services_reply_topic}")
        print(f"dock3 device subscribed -> {self.status_topic}")
        print(f"dock3 device replies  -> {self.status_reply_topic}")
        self.connected_event.set()

    def _on_message(self, client, userdata, msg):
        try:
            payload = json.loads(msg.payload.decode("utf-8"))
        except Exception as exc:
            print(f"invalid JSON on {msg.topic}: {exc}", file=sys.stderr)
            return

        if msg.topic == self.status_topic:
            self._handle_status(payload)
            return

        if msg.topic == self.services_reply_topic:
            tid = payload.get("tid")
            waiter = self.pending.get(tid)
            if not waiter:
                self.stats["ignored_late"] += 1
                print(f"ignored services_reply tid={tid}, method={payload.get('method')}")
                return
            if payload.get("method") != waiter["method"]:
                self.stats["ignored_mismatched"] += 1
                print(
                    f"ignored services_reply method mismatch: "
                    f"expected={waiter['method']}, actual={payload.get('method')}, tid={tid}"
                )
                return
            if looks_like_legacy_stale_reply(payload):
                self.stats["ignored_legacy"] += 1
                print(
                    f"ignored legacy services_reply: method={payload.get('method')}, "
                    f"tid={tid}, result={result_code(payload)}"
                )
                print(json.dumps(payload, ensure_ascii=False, indent=2))
                return
            self.stats["accepted"] += 1
            waiter["payload"] = payload
            waiter["event"].set()

    def _handle_status(self, payload):
        print(f"received <- {self.status_topic}, method={payload.get('method')}, tid={payload.get('tid')}")
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        if payload.get("method") == "update_topo":
            valid, errors = validate_update_topo_payload(payload.get("data", {}))
            if errors:
                self.status_validation_errors.extend(errors)
            self.status_messages.append(payload)
            reply = build_status_reply(payload, self.sn, result=0 if valid else INVALID_COMMAND_FORMAT)
            self.client.publish(self.status_reply_topic, json.dumps(reply, ensure_ascii=False), qos=self.qos)
            print(f"published -> {self.status_reply_topic}")
            print(json.dumps(reply, ensure_ascii=False, indent=2))
            self.status_event.set()

    def request(self, method, data, timeout, expected_result):
        message = build_message(self.sn, method, data)
        waiter = {"event": Event(), "payload": None, "method": method}
        self.pending[message["tid"]] = waiter
        self.client.publish(self.services_topic, json.dumps(message, ensure_ascii=False), qos=self.qos)
        print(f"published {method} -> {self.services_topic}, tid={message['tid']}")

        if not waiter["event"].wait(timeout):
            self.pending.pop(message["tid"], None)
            print(f"timeout waiting for services_reply: method={method}, tid={message['tid']}", file=sys.stderr)
            return 2, None

        self.pending.pop(message["tid"], None)
        reply = waiter["payload"]
        print(f"received <- {self.services_reply_topic}, method={reply.get('method')}, tid={reply.get('tid')}")
        print(json.dumps(reply, ensure_ascii=False, indent=2))
        result = result_code(reply)
        if result != expected_result:
            print(
                f"{method} unexpected result: expected={expected_result}, actual={result}",
                file=sys.stderr,
            )
            return 3, reply
        return 0, reply

    def wait_for_new_status(self, previous_count, timeout):
        deadline = time.time() + timeout
        while time.time() < deadline:
            if len(self.status_messages) > previous_count:
                return self.status_messages[-1]
            self.status_event.wait(max(0.05, min(0.5, deadline - time.time())))
            self.status_event.clear()
        return None


def assert_status_payload(status_msg, expected_count, expected_sn=None):
    if not status_msg:
        print("timeout waiting for update_topo status", file=sys.stderr)
        return 4

    data = status_msg.get("data", {})
    valid, errors = validate_update_topo_payload(data)
    if not valid:
        for error in errors:
            print(error, file=sys.stderr)
        return 5

    sub_devices = data.get("sub_devices", [])
    if len(sub_devices) != expected_count:
        print(
            f"update_topo sub_devices count mismatch: expected={expected_count}, actual={len(sub_devices)}",
            file=sys.stderr,
        )
        return 6

    if expected_sn:
        sns = {device.get("sn") for device in sub_devices}
        if expected_sn not in sns:
            print(f"missing expected sub-device sn={expected_sn}", file=sys.stderr)
            return 7
    return 0


def online_payload(args):
    return build_update_topo_payload(
        domain=GATEWAY_DOMAIN,
        device_type=GATEWAY_TYPE,
        sub_type=GATEWAY_SUB_TYPE,
        device_secret=args.gateway_secret,
        nonce=args.gateway_nonce,
        thing_version=THING_VERSION,
        sub_devices=[
            build_sub_device_payload(
                args.sub_device_sn,
                domain=args.sub_device_domain,
                device_type=args.sub_device_type,
                sub_type=args.sub_device_sub_type,
                index=args.sub_device_index,
                device_secret=args.sub_device_secret,
                nonce=args.sub_device_nonce,
                thing_version=args.sub_device_thing_version,
            )
        ],
    )


def offline_payload(args):
    return build_update_topo_payload(
        domain=GATEWAY_DOMAIN,
        device_type=GATEWAY_TYPE,
        sub_type=GATEWAY_SUB_TYPE,
        device_secret=args.gateway_secret,
        nonce=args.gateway_nonce,
        thing_version=THING_VERSION,
        sub_devices=[],
    )


def main():
    global mqtt
    parser = argparse.ArgumentParser(description="Test DJI Dock3 device/topology category")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=1883)
    parser.add_argument("--sn", default="EDGE-GW-001")
    parser.add_argument("--timeout", type=float, default=12.0)
    parser.add_argument("--qos", type=int, choices=[0, 1, 2], default=1)
    parser.add_argument("--gateway-secret", default="gw-secret")
    parser.add_argument("--gateway-nonce", default="gw-nonce")
    parser.add_argument("--sub-device-sn", default="DRONE-SIM-001")
    parser.add_argument("--sub-device-domain", default="0")
    parser.add_argument("--sub-device-type", type=int, default=60)
    parser.add_argument("--sub-device-sub-type", type=int, default=0)
    parser.add_argument("--sub-device-index", default="A")
    parser.add_argument("--sub-device-secret", default="drone-secret")
    parser.add_argument("--sub-device-nonce", default="drone-nonce")
    parser.add_argument("--sub-device-thing-version", default=THING_VERSION)
    parser.add_argument("--skip-offline-check", action="store_true")
    parser.add_argument("--skip-invalid-check", action="store_true")
    parser.add_argument(
        "--no-status-window",
        type=float,
        default=1.0,
        help="Seconds to watch for unexpected status after invalid update_topo.",
    )
    args = parser.parse_args()

    try:
        import paho.mqtt.client as mqtt_module
    except ImportError as exc:
        raise SystemExit("paho-mqtt is required. Install requirements-ubuntu.txt first.") from exc
    mqtt = mqtt_module

    client = DeviceTopologyClient(args.host, args.port, args.sn, args.qos)
    if not client.connect(args.timeout):
        print("MQTT connect timeout", file=sys.stderr)
        return 2

    try:
        before = len(client.status_messages)
        rc, _ = client.request("update_topo", online_payload(args), args.timeout, expected_result=0)
        if rc != 0:
            return rc
        rc = assert_status_payload(
            client.wait_for_new_status(before, args.timeout),
            expected_count=1,
            expected_sn=args.sub_device_sn,
        )
        if rc != 0:
            return rc

        if not args.skip_offline_check:
            before = len(client.status_messages)
            rc, _ = client.request("update_topo", offline_payload(args), args.timeout, expected_result=0)
            if rc != 0:
                return rc
            rc = assert_status_payload(
                client.wait_for_new_status(before, args.timeout),
                expected_count=0,
            )
            if rc != 0:
                return rc

        if not args.skip_invalid_check:
            before = len(client.status_messages)
            invalid_payload = {"sub_devices": []}
            rc, _ = client.request(
                "update_topo",
                invalid_payload,
                args.timeout,
                expected_result=INVALID_COMMAND_FORMAT,
            )
            if rc != 0:
                return rc
            unexpected = client.wait_for_new_status(before, args.no_status_window)
            if unexpected is not None:
                print("invalid update_topo unexpectedly produced a status update", file=sys.stderr)
                return 8

        if client.status_validation_errors:
            for error in client.status_validation_errors:
                print(error, file=sys.stderr)
            return 9

        print(
            "dock3 device topology test ok "
            f"(status_messages={len(client.status_messages)}, sub_device_sn={args.sub_device_sn}, "
            f"accepted={client.stats['accepted']}, ignored_legacy={client.stats['ignored_legacy']})"
        )
        return 0
    finally:
        client.disconnect()


if __name__ == "__main__":
    raise SystemExit(main())
