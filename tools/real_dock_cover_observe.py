#!/usr/bin/env python3
"""Real dock command sender with MQTT observation.

Use this during physical dock testing. It publishes one dock command and
prints gateway replies/events/telemetry samples for a short observation window.
"""

from __future__ import annotations

import argparse
import json
import time
import uuid
from typing import Any

import paho.mqtt.client as mqtt


def make_client(client_id: str) -> mqtt.Client:
    if hasattr(mqtt, "CallbackAPIVersion"):
        try:
            return mqtt.Client(mqtt.CallbackAPIVersion.VERSION1, client_id=client_id)
        except TypeError:
            pass
    return mqtt.Client(client_id=client_id)


def now_ms() -> int:
    return int(time.time() * 1000)


def pretty_payload(raw: bytes) -> str:
    text = raw.decode("utf-8", errors="replace")
    try:
        return json.dumps(json.loads(text), ensure_ascii=False, indent=2)
    except Exception:
        return text


COMMAND_METHODS = {
    "open": "cover_open",
    "close": "cover_close",
    "charge-open": "charge_open",
    "charge-close": "charge_close",
    "light-on": "supplement_light_open",
    "light-off": "supplement_light_close",
}


def build_message(sn: str, method: str) -> dict[str, Any]:
    suffix = uuid.uuid4().hex[:8]
    tid = f"realtest_{method}_{suffix}"
    return {
        "bid": tid,
        "tid": tid,
        "timestamp": now_ms(),
        "gateway": sn,
        "method": method,
        "data": {},
    }


def build_property_get(sn: str) -> dict[str, Any]:
    suffix = uuid.uuid4().hex[:8]
    tid = f"realtest_get_cover_state_{suffix}"
    return {
        "bid": tid,
        "tid": tid,
        "timestamp": now_ms(),
        "gateway": sn,
        "method": "property_get",
        "data": {
            "properties": [
                "cover_state",
                "putter_state",
                "charging_state",
                "supplement_light_state",
                "drone_in_dock",
                "emergency_stop_state",
            ]
        },
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Send real dock command and observe MQTT output")
    parser.add_argument(
        "action",
        choices=[*COMMAND_METHODS.keys(), "status", "listen"],
        help="dock action",
    )
    parser.add_argument("--host", default="127.0.0.1", help="MQTT broker host")
    parser.add_argument("--port", type=int, default=1883, help="MQTT broker port")
    parser.add_argument("--sn", default="EDGEGW001", help="gateway SN")
    parser.add_argument("--observe-seconds", type=float, default=12.0, help="seconds to print observed messages")
    parser.add_argument("--qos", type=int, choices=[0, 1, 2], default=1)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    sn = args.sn
    command_topic = f"thing/product/{sn}/services"
    observe_topics = [
        f"thing/product/{sn}/services_reply",
        f"thing/product/{sn}/events",
        f"thing/product/{sn}/osd",
        f"thing/product/{sn}/state",
        f"sys/product/{sn}/status",
        f"sys/product/{sn}/status_reply",
    ]

    connected = False

    def on_connect(client, _userdata, _flags, rc):
        nonlocal connected
        if rc == 0:
            connected = True
            print(f"[mqtt] connected {args.host}:{args.port}")
            for topic in observe_topics:
                client.subscribe(topic, qos=args.qos)
                print(f"[mqtt] subscribed {topic}")
        else:
            print(f"[mqtt] connect failed rc={rc}")

    def on_message(_client, _userdata, msg):
        print("")
        print(f"[recv] {msg.topic}")
        print(pretty_payload(msg.payload))

    client = make_client(f"real_dock_cover_observe_{uuid.uuid4().hex[:8]}")
    client.on_connect = on_connect
    client.on_message = on_message

    client.connect(args.host, args.port, keepalive=30)
    client.loop_start()

    deadline = time.time() + 5
    while not connected and time.time() < deadline:
        time.sleep(0.05)
    if not connected:
        client.loop_stop()
        client.disconnect()
        print("[error] MQTT not connected")
        return 2

    time.sleep(0.5)

    if args.action == "listen":
        print(f"[info] listen only for {args.observe_seconds:.1f}s")
    else:
        if args.action == "status":
            payload = build_property_get(sn)
        else:
            method = COMMAND_METHODS[args.action]
            payload = build_message(sn, method)

        text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        client.publish(command_topic, text, qos=args.qos)
        print("")
        print(f"[send] {command_topic}")
        print(json.dumps(payload, ensure_ascii=False, indent=2))

    time.sleep(max(0.1, args.observe_seconds))
    client.loop_stop()
    client.disconnect()
    print("[done]")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
