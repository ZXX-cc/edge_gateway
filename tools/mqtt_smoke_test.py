#!/usr/bin/env python3
"""Publish one DJI Cloud API-style MQTT message and wait for a reply."""

import argparse
import json
import sys
import time
import uuid
from threading import Event

try:
    import paho.mqtt.client as mqtt
except ImportError as exc:
    raise SystemExit("paho-mqtt is required. Install requirements-ubuntu.txt first.") from exc


CHANNELS = {
    "services": ("thing/product/{sn}/services", "thing/product/{sn}/services_reply"),
    "property": ("thing/product/{sn}/property/set", "thing/product/{sn}/property/set_reply"),
    "drc": ("thing/product/{sn}/drc/down", "thing/product/{sn}/drc/up"),
}

METHOD_CHANNEL_HINTS = {
    "property_set": "property",
    "drc_mode_enter": "services",
    "drc_mode_exit": "services",
    "takeoff_to_point": "services",
    "stick_control": "drc",
    "drone_control": "drc",
    "payload_control": "drc",
}


def build_client(client_id):
    if hasattr(mqtt, "CallbackAPIVersion"):
        try:
            return mqtt.Client(mqtt.CallbackAPIVersion.VERSION1, client_id=client_id)
        except TypeError:
            pass
    return mqtt.Client(client_id=client_id)


def parse_payload(raw):
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise argparse.ArgumentTypeError(f"payload must be JSON: {exc}") from exc


def main():
    parser = argparse.ArgumentParser(description="MQTT smoke test for edge_gateway")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=1883)
    parser.add_argument("--sn", default="EDGE-GW-001")
    parser.add_argument("--channel", choices=["auto", *sorted(CHANNELS)], default="auto")
    parser.add_argument("--method", default="dock_status_query")
    parser.add_argument("--payload", type=parse_payload, default={})
    parser.add_argument("--timeout", type=float, default=8.0)
    parser.add_argument("--qos", type=int, choices=[0, 1, 2], default=1)
    args = parser.parse_args()

    channel = args.channel
    if channel == "auto":
        channel = METHOD_CHANNEL_HINTS.get(args.method, "services")

    publish_tpl, reply_tpl = CHANNELS[channel]
    publish_topic = publish_tpl.format(sn=args.sn)
    reply_topic = reply_tpl.format(sn=args.sn)
    done = Event()
    received = []

    message = {
        "tid": str(uuid.uuid4()),
        "bid": str(uuid.uuid4()),
        "timestamp": int(time.time() * 1000),
        "gateway": args.sn,
        "method": args.method,
        "need_reply": 1,
        "data": args.payload,
    }

    client = build_client(f"edge_gateway_smoke_{uuid.uuid4().hex[:8]}")

    def on_connect(client, userdata, flags, rc):
        if rc != 0:
            print(f"connect failed: rc={rc}", file=sys.stderr)
            done.set()
            return
        client.subscribe(reply_topic, qos=args.qos)
        client.publish(publish_topic, json.dumps(message, ensure_ascii=False), qos=args.qos)
        print(f"published {args.method} -> {publish_topic}")
        print(f"waiting reply on {reply_topic}")

    def on_message(client, userdata, msg):
        try:
            payload = json.loads(msg.payload.decode("utf-8"))
        except Exception:
            payload = msg.payload.decode("utf-8", errors="replace")
        received.append((msg.topic, payload))
        done.set()

    client.on_connect = on_connect
    client.on_message = on_message
    client.connect(args.host, args.port, keepalive=30)
    client.loop_start()
    try:
        if not done.wait(args.timeout):
            print("timeout waiting for reply", file=sys.stderr)
            return 2
        if received:
            topic, payload = received[0]
            print(f"received <- {topic}")
            print(json.dumps(payload, ensure_ascii=False, indent=2) if isinstance(payload, dict) else payload)
            return 0
        return 1
    finally:
        client.loop_stop()
        client.disconnect()


if __name__ == "__main__":
    raise SystemExit(main())
