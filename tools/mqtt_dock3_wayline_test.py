#!/usr/bin/env python3
"""Category smoke test for DJI Dock3 wayline / flighttask protocol."""

import argparse
import json
import sys
import time
import uuid
from pathlib import Path
from threading import Event

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from src.utils.dji_error_codes import DEVICE_STATE_UNSUPPORTED, INVALID_COMMAND_FORMAT
from tool_config import add_config_argument, resolve_mqtt_settings

mqtt = None

SERVICES_TOPIC = "thing/product/{sn}/services"
SERVICES_REPLY_TOPIC = "thing/product/{sn}/services_reply"
EVENTS_TOPIC = "thing/product/{sn}/events"


def build_client(client_id):
    if hasattr(mqtt, "CallbackAPIVersion"):
        try:
            return mqtt.Client(mqtt.CallbackAPIVersion.VERSION1, client_id=client_id)
        except TypeError:
            pass
    return mqtt.Client(client_id=client_id)


def now_ms():
    return int(time.time() * 1000)


def result_code(message):
    if not isinstance(message, dict):
        return None
    return message.get("data", {}).get("result")


def output_status(message):
    if not isinstance(message, dict):
        return None
    return message.get("data", {}).get("output", {}).get("status")


def looks_like_legacy_wayline_reply(payload):
    if not isinstance(payload, dict):
        return False
    data = payload.get("data")
    if not isinstance(data, dict):
        return False
    if result_code(payload) == 0 and output_status(payload) is None:
        return True
    if result_code(payload) in (-1, -2, -3):
        return True
    return False


def official_prepare_payload(flight_id):
    return {
        "flight_id": flight_id,
        "task_type": 0,
        "execute_time": now_ms() + 30_000,
        "file": {
            "url": "https://example.invalid/simulated-wayline.kmz",
            "fingerprint": "simulated-fingerprint",
        },
        "ready_conditions": {},
        "executable_conditions": {
            "storage_capacity": 0,
        },
        "rth_altitude": 80,
        "out_of_control_action": 0,
        "exit_wayline_when_rc_lost": 0,
        "simulate_mission": {
            "is_enable": 1,
            "latitude": -35.363262,
            "longitude": 149.165237,
        },
    }


class Dock3WaylineClient:
    def __init__(self, host, port, sn, qos, quiet_stale=False):
        self.host = host
        self.port = port
        self.sn = sn
        self.qos = qos
        self.quiet_stale = quiet_stale
        self.connected = Event()
        self.pending = {}
        self.events = []
        self.stats = {
            "accepted": 0,
            "events": 0,
            "ignored_late": 0,
            "ignored_mismatched": 0,
            "ignored_legacy": 0,
        }
        self.client = build_client(f"edge_gateway_dock3_wayline_{uuid.uuid4().hex[:8]}")

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
        for topic in (
            SERVICES_REPLY_TOPIC.format(sn=self.sn),
            EVENTS_TOPIC.format(sn=self.sn),
        ):
            client.subscribe(topic, qos=self.qos)
            print(f"dock3 wayline subscribed -> {topic}")
        self.connected.set()

    def _on_message(self, client, userdata, msg):
        try:
            payload = json.loads(msg.payload.decode("utf-8"))
        except Exception as exc:
            print(f"invalid JSON on {msg.topic}: {exc}", file=sys.stderr)
            return

        if msg.topic == EVENTS_TOPIC.format(sn=self.sn):
            self.events.append(payload)
            self.stats["events"] += 1
            if not self.quiet_stale:
                print(f"event observed: method={payload.get('method')}")
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

        if looks_like_legacy_wayline_reply(payload):
            self.stats["ignored_legacy"] += 1
            if not self.quiet_stale:
                print(f"ignored legacy stale reply <- {msg.topic}, method={actual_method}, tid={tid}")
                print(json.dumps(payload, ensure_ascii=False, indent=2))
            return

        self.stats["accepted"] += 1
        waiter["topic"] = msg.topic
        waiter["payload"] = payload
        waiter["event"].set()

    def request(self, method, data, timeout, allowed_results=None, expected_status=None):
        publish_topic = SERVICES_TOPIC.format(sn=self.sn)
        expected_reply_topic = SERVICES_REPLY_TOPIC.format(sn=self.sn)
        tid = str(uuid.uuid4())
        message = {
            "tid": tid,
            "bid": str(uuid.uuid4()),
            "timestamp": now_ms(),
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

        allowed = set([0] if allowed_results is None else allowed_results)
        actual = result_code(payload)
        if actual not in allowed:
            print(f"{method} unexpected result: expected={sorted(allowed)}, actual={actual}", file=sys.stderr)
            return 4, payload

        if expected_status is not None:
            actual_status = output_status(payload)
            if actual_status != expected_status:
                print(
                    f"{method} unexpected output.status: expected={expected_status}, actual={actual_status}",
                    file=sys.stderr,
                )
                return 5, payload
        return 0, payload

    def has_event(self, method):
        return any(event.get("method") == method for event in self.events)

    def ignored_total(self):
        return self.stats["ignored_late"] + self.stats["ignored_mismatched"] + self.stats["ignored_legacy"]


def run_wayline_checks(client, args):
    flight_id = args.flight_id or f"dock3-wayline-{int(time.time())}"
    checks = [
        ("flighttask_prepare", official_prepare_payload(flight_id), [0], "sent"),
        ("flighttask_execute", {"flight_id": flight_id}, [0], "in_progress"),
        ("flighttask_progress", {"flight_id": flight_id}, [0], "in_progress"),
        ("flighttask_pause", {}, [0], "paused"),
        ("flighttask_progress", {"flight_id": flight_id}, [0], "paused"),
        ("flighttask_resume", {}, [0], "in_progress"),
        ("flighttask_return", {"flight_id": flight_id, "return_type": 0}, [0], "in_progress"),
        ("return_home_cancel", {}, [0], "paused"),
        ("flighttask_undo", {"flight_ids": [flight_id]}, [0], "canceled"),
        (
            "flighttask_prepare",
            {"flight_id": "", "task_type": 99, "execute_time": 1},
            [INVALID_COMMAND_FORMAT],
            None,
        ),
        ("flighttask_pause", {}, [DEVICE_STATE_UNSUPPORTED], "rejected"),
    ]
    for method, data, allowed, status in checks:
        rc, _ = client.request(method, data, args.timeout, allowed, status)
        if rc != 0:
            return rc
        time.sleep(args.step_delay)

    if not client.has_event("flighttask_progress"):
        print("expected flighttask_progress event was not observed", file=sys.stderr)
        return 6
    if not client.has_event("return_home_info"):
        print("expected return_home_info event was not observed", file=sys.stderr)
        return 7
    return 0


def main():
    global mqtt
    parser = argparse.ArgumentParser(description="Test DJI Dock3 wayline category")
    add_config_argument(parser)
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--sn", default=None)
    parser.add_argument("--timeout", type=float, default=12.0)
    parser.add_argument("--qos", type=int, choices=[0, 1, 2], default=1)
    parser.add_argument("--flight-id", default="")
    parser.add_argument("--step-delay", type=float, default=0.1)
    parser.add_argument("--quiet-stale", action="store_true")
    parser.add_argument("--fail-on-stale", action="store_true")
    args = parser.parse_args()
    args.host, args.port, args.sn = resolve_mqtt_settings(args)

    try:
        import paho.mqtt.client as mqtt_module
    except ImportError:
        print("paho-mqtt is required. Install project requirements first.", file=sys.stderr)
        return 1
    mqtt = mqtt_module

    client = Dock3WaylineClient(args.host, args.port, args.sn, args.qos, args.quiet_stale)
    if not client.connect(args.timeout):
        print("timeout waiting for MQTT connection", file=sys.stderr)
        return 1
    try:
        rc = run_wayline_checks(client, args)
        if rc != 0:
            return rc
        ignored_total = client.ignored_total()
        if args.fail_on_stale and ignored_total:
            print(f"stale replies observed: {client.stats}", file=sys.stderr)
            return 9
        print(
            "dock3 wayline test ok "
            f"(accepted={client.stats['accepted']}, events={client.stats['events']}, "
            f"ignored_mismatched={client.stats['ignored_mismatched']}, "
            f"ignored_legacy={client.stats['ignored_legacy']}, "
            f"ignored_late={client.stats['ignored_late']}, ignored_total={ignored_total})"
        )
        return 0
    finally:
        client.disconnect()


if __name__ == "__main__":
    raise SystemExit(main())
