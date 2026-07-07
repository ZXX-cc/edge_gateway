#!/usr/bin/env python3
"""Category smoke test for DJI Dock3 DRC protocol."""

import argparse
import json
import sys
import time
import uuid
from pathlib import Path
from threading import Event

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from src.utils.dji_error_codes import INVALID_COMMAND_FORMAT
from tool_config import add_config_argument, resolve_mqtt_settings

mqtt = None

TOPICS = {
    "services": ("thing/product/{sn}/services", "thing/product/{sn}/services_reply"),
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
        if "msg" in data:
            return True
    return False


def now_ms():
    return int(time.time() * 1000)


def official_enter_payload(args):
    return {
        "mqtt_broker": {
            "address": args.drc_broker_address,
            "client_id": f"drc-{args.sn}",
            "username": args.drc_username,
            "password": args.drc_password,
            "expire_time": args.drc_expire_time,
            "enable_tls": args.drc_enable_tls,
        },
        "osd_frequency": args.osd_frequency,
        "hsi_frequency": args.hsi_frequency,
    }


class Dock3DrcClient:
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
        self.client = build_client(f"edge_gateway_dock3_drc_{uuid.uuid4().hex[:8]}")

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
            client.subscribe(topic, qos=self.qos)
            print(f"dock3 drc subscribed -> {topic}")
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
                print(f"ignored legacy stale reply <- {msg.topic}, method={actual_method}, tid={tid}")
                print(json.dumps(payload, ensure_ascii=False, indent=2))
            return

        self.stats["accepted"] += 1
        waiter["topic"] = msg.topic
        waiter["payload"] = payload
        waiter["event"].set()

    def request(self, channel, method, data, timeout, allowed_results=None, need_reply=1):
        publish_tpl, reply_tpl = TOPICS[channel]
        publish_topic = publish_tpl.format(sn=self.sn)
        expected_reply_topic = reply_tpl.format(sn=self.sn)
        tid = str(uuid.uuid4())
        message = {
            "tid": tid,
            "bid": str(uuid.uuid4()),
            "timestamp": now_ms(),
            "gateway": self.sn,
            "method": method,
            "data": data or {},
        }
        if need_reply:
            message["need_reply"] = 1

        waiter = {"event": Event(), "topic": "", "payload": None, "method": method}
        self.pending[tid] = waiter
        self.client.publish(publish_topic, json.dumps(message, ensure_ascii=False), qos=self.qos)
        print(f"published {method} -> {publish_topic}, tid={tid}, need_reply={need_reply}")

        if not need_reply:
            if waiter["event"].wait(timeout):
                self.pending.pop(tid, None)
                print(f"{method} unexpectedly received reply for no-reply request", file=sys.stderr)
                print(json.dumps(waiter["payload"], ensure_ascii=False, indent=2))
                return 8, waiter["payload"]
            self.pending.pop(tid, None)
            print(f"no reply observed as expected: method={method}, tid={tid}")
            return 0, None

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

        allowed = set([0] if allowed_results is None else allowed_results)
        actual = result_code(payload)
        if actual not in allowed:
            print(f"{method} unexpected result: expected={sorted(allowed)}, actual={actual}", file=sys.stderr)
            return 4, payload
        return 0, payload


def main():
    global mqtt
    parser = argparse.ArgumentParser(description="Test DJI Dock3 DRC category")
    add_config_argument(parser)
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--sn", default=None)
    parser.add_argument("--timeout", type=float, default=12.0)
    parser.add_argument("--qos", type=int, choices=[0, 1, 2], default=1)
    parser.add_argument("--quiet-stale", action="store_true")
    parser.add_argument("--fail-on-stale", action="store_true")
    parser.add_argument("--drc-broker-address", default="127.0.0.1:1883")
    parser.add_argument("--drc-username", default="sim-user")
    parser.add_argument("--drc-password", default="sim-token")
    parser.add_argument("--drc-expire-time", type=int, default=3600)
    parser.add_argument("--drc-enable-tls", action="store_true")
    parser.add_argument("--osd-frequency", type=int, default=10)
    parser.add_argument("--hsi-frequency", type=int, default=1)
    parser.add_argument("--no-reply-window", type=float, default=1.0)
    args = parser.parse_args()
    args.host, args.port, args.sn = resolve_mqtt_settings(args)

    try:
        import paho.mqtt.client as mqtt_module
    except ImportError as exc:
        raise SystemExit("paho-mqtt is required. Install requirements-ubuntu.txt first.") from exc
    mqtt = mqtt_module

    client = Dock3DrcClient(args.host, args.port, args.sn, args.qos, quiet_stale=args.quiet_stale)
    if not client.connect(args.timeout):
        print("MQTT connect timeout", file=sys.stderr)
        return 2

    try:
        rc, _ = client.request("services", "drc_mode_exit", {}, args.timeout, allowed_results={0, 319999})
        if rc != 0:
            return rc

        rc, _ = client.request("services", "drc_mode_enter", official_enter_payload(args), args.timeout)
        if rc != 0:
            return rc

        rc, state_reply = client.request(
            "services",
            "property_get",
            {"properties": ["drc_state"]},
            args.timeout,
        )
        if rc != 0:
            return rc
        if state_reply.get("data", {}).get("drc_state") != 2:
            print(f"expected drc_state=2 after enter, actual={state_reply.get('data', {}).get('drc_state')}", file=sys.stderr)
            return 5

        rc, _ = client.request(
            "drc",
            "stick_control",
            {"roll": 1024, "pitch": 1024, "throttle": 1024, "yaw": 1024, "gimbal_pitch": 1024},
            args.no_reply_window,
            need_reply=0,
        )
        if rc != 0:
            return rc

        rc, _ = client.request(
            "drc",
            "stick_control",
            {"roll": 1024, "pitch": 2000, "throttle": 1024, "yaw": 1024},
            args.timeout,
            allowed_results={INVALID_COMMAND_FORMAT},
        )
        if rc != 0:
            return rc

        rc, _ = client.request(
            "drc",
            "drone_control",
            {"seq": 1, "x": 0.1, "y": 0.0, "h": 0.0, "w": 0.0},
            args.timeout,
        )
        if rc != 0:
            return rc

        rc, _ = client.request(
            "drc",
            "drone_control",
            {"seq": 2, "x": 99.0, "y": 0.0, "h": 0.0, "w": 0.0},
            args.timeout,
            allowed_results={INVALID_COMMAND_FORMAT},
        )
        if rc != 0:
            return rc

        rc, _ = client.request(
            "drc",
            "drc_gimbal_speed_push",
            {"pitch_speed": 0.0, "yaw_speed": 0.0, "roll_speed": 0.0},
            args.timeout,
        )
        if rc != 0:
            return rc

        rc, _ = client.request("services", "drc_mode_exit", {}, args.timeout)
        if rc != 0:
            return rc

        rc, state_reply = client.request(
            "services",
            "property_get",
            {"properties": ["drc_state"]},
            args.timeout,
        )
        if rc != 0:
            return rc
        if state_reply.get("data", {}).get("drc_state") != 0:
            print(f"expected drc_state=0 after exit, actual={state_reply.get('data', {}).get('drc_state')}", file=sys.stderr)
            return 6

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
            "dock3 drc test ok "
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
