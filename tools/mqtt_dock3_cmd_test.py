#!/usr/bin/env python3
"""Category smoke test for DJI Dock3 cmd / remote debugging methods."""

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
    output = message.get("data", {}).get("output")
    if not isinstance(output, dict):
        return None
    return output.get("status")


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


class Dock3CmdClient:
    def __init__(self, host, port, sn, qos, quiet_stale=False):
        self.host = host
        self.port = port
        self.sn = sn
        self.qos = qos
        self.quiet_stale = quiet_stale
        self.services_topic = SERVICES_TOPIC.format(sn=sn)
        self.services_reply_topic = SERVICES_REPLY_TOPIC.format(sn=sn)
        self.events_topic = EVENTS_TOPIC.format(sn=sn)
        self.connected = Event()
        self.pending = {}
        self.events_by_method = {}
        self.event_signal = Event()
        self.stats = {
            "accepted": 0,
            "events": 0,
            "ignored_late": 0,
            "ignored_mismatched": 0,
            "ignored_legacy": 0,
        }
        self.client = build_client(f"edge_gateway_dock3_cmd_{uuid.uuid4().hex[:8]}")

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
        client.subscribe(self.services_reply_topic, qos=self.qos)
        client.subscribe(self.events_topic, qos=self.qos)
        print(f"dock3 cmd subscribed -> {self.services_reply_topic}")
        print(f"dock3 cmd subscribed -> {self.events_topic}")
        self.connected.set()

    def _on_message(self, client, userdata, msg):
        try:
            payload = json.loads(msg.payload.decode("utf-8"))
        except Exception as exc:
            print(f"invalid JSON on {msg.topic}: {exc}", file=sys.stderr)
            return

        if msg.topic == self.events_topic:
            method = payload.get("method")
            self.events_by_method.setdefault(method, []).append(payload)
            self.stats["events"] += 1
            self.event_signal.set()
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

        expected_status = waiter.get("expected_status")
        if (
            expected_status is not None
            and output_status(payload) is None
            and result_code(payload) == 0
        ):
            self.stats["ignored_legacy"] += 1
            if not self.quiet_stale:
                print(
                    f"ignored legacy reply without data.output.status <- {msg.topic}, "
                    f"method={actual_method}, tid={tid}"
                )
                print(json.dumps(payload, ensure_ascii=False, indent=2))
            return

        self.stats["accepted"] += 1
        waiter["payload"] = payload
        waiter["event"].set()

    def request(
        self,
        method,
        data,
        timeout,
        allowed_results=None,
        expected_status="ok",
    ):
        message = {
            "tid": str(uuid.uuid4()),
            "bid": str(uuid.uuid4()),
            "timestamp": now_ms(),
            "gateway": self.sn,
            "method": method,
            "need_reply": 1,
            "data": data or {},
        }
        waiter = {
            "event": Event(),
            "payload": None,
            "method": method,
            "expected_status": expected_status,
        }
        self.pending[message["tid"]] = waiter
        self.client.publish(self.services_topic, json.dumps(message, ensure_ascii=False), qos=self.qos)
        print(f"published {method} -> {self.services_topic}, tid={message['tid']}")

        if not waiter["event"].wait(timeout):
            self.pending.pop(message["tid"], None)
            print(
                f"timeout waiting for {method}, tid={message['tid']}; "
                f"ignored_mismatched={self.stats['ignored_mismatched']}, "
                f"ignored_legacy={self.stats['ignored_legacy']}, "
                f"ignored_late={self.stats['ignored_late']}",
                file=sys.stderr,
            )
            return 2, None

        self.pending.pop(message["tid"], None)
        reply = waiter["payload"]
        print(f"received <- {self.services_reply_topic}, method={reply.get('method')}, tid={reply.get('tid')}")
        print(json.dumps(reply, ensure_ascii=False, indent=2))

        allowed = set([0] if allowed_results is None else allowed_results)
        actual_result = result_code(reply)
        if actual_result not in allowed:
            print(
                f"{method} unexpected result: expected={sorted(allowed)}, actual={actual_result}",
                file=sys.stderr,
            )
            return 3, reply

        if expected_status is not None:
            actual_status = output_status(reply)
            if actual_status != expected_status:
                print(
                    f"{method} unexpected output.status: expected={expected_status}, actual={actual_status}",
                    file=sys.stderr,
                )
                return 4, reply

        return 0, reply

    def wait_event(self, method, previous_count, timeout, expected_status="ok"):
        deadline = time.time() + timeout
        while time.time() < deadline:
            events = self.events_by_method.get(method, [])
            if len(events) > previous_count:
                event = events[-1]
                print(f"event observed: method={method}, tid={event.get('tid')}")
                if expected_status is not None:
                    actual_status = output_status(event)
                    if actual_status != expected_status:
                        print(
                            f"{method} event output.status mismatch: expected={expected_status}, actual={actual_status}",
                            file=sys.stderr,
                        )
                        return 5, event
                return 0, event
            self.event_signal.wait(max(0.05, min(0.5, deadline - time.time())))
            self.event_signal.clear()
        print(f"timeout waiting for event: method={method}", file=sys.stderr)
        return 6, None

    def property_get(self, names, timeout):
        rc, reply = self.request(
            "property_get",
            {"properties": names},
            timeout,
            allowed_results={0},
            expected_status=None,
        )
        if rc != 0:
            return rc, {}
        return 0, reply.get("data", {})

    def event_count(self, method):
        return len(self.events_by_method.get(method, []))


def require_property(client, args, name, expected):
    rc, data = client.property_get([name], args.timeout)
    if rc != 0:
        return rc
    actual = data.get(name)
    if actual != expected:
        print(f"property {name} mismatch: expected={expected!r}, actual={actual!r}", file=sys.stderr)
        return 7
    return 0


def require_nested_property(client, args, property_name, nested_path, expected):
    rc, data = client.property_get([property_name], args.timeout)
    if rc != 0:
        return rc
    value = data.get(property_name)
    for key in nested_path:
        if not isinstance(value, dict):
            value = None
            break
        value = value.get(key)
    if value != expected:
        joined = ".".join([property_name, *nested_path])
        print(f"property {joined} mismatch: expected={expected!r}, actual={value!r}", file=sys.stderr)
        return 7
    return 0


def run_switch_checks(client, args):
    cases = [
        ("debug_mode_open", {}, "mode_code", 2),
        ("debug_mode_close", {}, "mode_code", 0),
        ("supplement_light_open", {}, "supplement_light_state", 1),
        ("supplement_light_close", {}, "supplement_light_state", 0),
        ("battery_maintenance_switch", {"action": 1}, "battery_maintenance_mode", 1),
        ("battery_maintenance_switch", {"action": 0}, "battery_maintenance_mode", 0),
        ("air_conditioner_mode_switch", {"action": 2}, "air_conditioner_mode", 2),
        ("air_conditioner_mode_switch", {"action": 0}, "air_conditioner_mode", 0),
        ("alarm_state_switch", {"action": 1}, "alarm_state", 1),
        ("alarm_state_switch", {"action": 0}, "alarm_state", 0),
        ("battery_store_mode_switch", {"action": 2}, "battery_store_mode", 2),
        ("battery_store_mode_switch", {"action": 1}, "battery_store_mode", 1),
    ]

    for method, data, prop_name, expected in cases:
        rc, _ = client.request(method, data, args.timeout)
        if rc != 0:
            return rc
        rc = require_property(client, args, prop_name, expected)
        if rc != 0:
            return rc

    rc, _ = client.request("sdr_workmode_switch", {"link_workmode": 1}, args.timeout)
    if rc != 0:
        return rc
    rc = require_nested_property(client, args, "wireless_link", ["link_workmode"], 1)
    if rc != 0:
        return rc

    rc, _ = client.request("sdr_workmode_switch", {"mode": 0}, args.timeout)
    if rc != 0:
        return rc
    rc = require_nested_property(client, args, "wireless_link", ["link_workmode"], 0)
    if rc != 0:
        return rc

    invalid_cases = [
        ("battery_maintenance_switch", {"action": 2}),
        ("air_conditioner_mode_switch", {"action": 9}),
        ("alarm_state_switch", {"action": 2}),
        ("battery_store_mode_switch", {"action": 0}),
        ("sdr_workmode_switch", {"link_workmode": 9}),
    ]
    for method, data in invalid_cases:
        rc, _ = client.request(
            method,
            data,
            args.timeout,
            allowed_results={INVALID_COMMAND_FORMAT},
            expected_status="rejected",
        )
        if rc != 0:
            return rc

    return 0


def run_job_method(client, args, method, data=None, property_name=None, expected_value=None):
    before = client.event_count(method)
    rc, _ = client.request(method, data or {}, args.timeout)
    if rc != 0:
        return rc
    rc, _ = client.wait_event(method, before, args.timeout)
    if rc != 0:
        return rc
    if property_name is not None:
        rc = require_property(client, args, property_name, expected_value)
        if rc != 0:
            return rc
    return 0


def run_job_checks(client, args):
    jobs = [
        ("cover_open", {}, "cover_state", 1),
        ("cover_close", {}, "cover_state", 0),
        ("charge_open", {}, "charging_state", 1),
        ("charge_close", {}, "charging_state", 0),
        ("putter_open", {}, "putter_state", 1),
        ("putter_close", {}, "putter_state", 0),
        ("drone_open", {}, None, None),
        ("drone_close", {}, None, None),
        ("drone_format", {}, None, None),
        ("device_format", {}, None, None),
        ("format_sd_card", {}, None, None),
        ("device_reboot", {}, None, None),
        ("device_reset", {}, "mode_code", 0),
    ]
    for method, data, prop_name, expected in jobs:
        rc = run_job_method(client, args, method, data, prop_name, expected)
        if rc != 0:
            return rc

    rc, data = client.property_get(["drone_in_dock"], args.timeout)
    if rc != 0:
        return rc
    if data.get("drone_in_dock") == 1:
        rc, _ = client.request(
            "cover_force_close",
            {},
            args.timeout,
            allowed_results={DEVICE_STATE_UNSUPPORTED},
            expected_status="rejected",
        )
        if rc != 0:
            return rc
    else:
        rc = run_job_method(client, args, "cover_force_close", {}, "cover_state", 0)
        if rc != 0:
            return rc

    return 0


def main():
    global mqtt
    parser = argparse.ArgumentParser(description="Test DJI Dock3 cmd/remote-debug category")
    add_config_argument(parser)
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--sn", default=None)
    parser.add_argument("--timeout", type=float, default=12.0)
    parser.add_argument("--qos", type=int, choices=[0, 1, 2], default=1)
    parser.add_argument("--quiet-stale", action="store_true")
    parser.add_argument("--fail-on-stale", action="store_true")
    args = parser.parse_args()
    args.host, args.port, args.sn = resolve_mqtt_settings(args)

    try:
        import paho.mqtt.client as mqtt_module
    except ImportError as exc:
        raise SystemExit("paho-mqtt is required. Install requirements-ubuntu.txt first.") from exc
    mqtt = mqtt_module

    client = Dock3CmdClient(args.host, args.port, args.sn, args.qos, quiet_stale=args.quiet_stale)
    if not client.connect(args.timeout):
        print("MQTT connect timeout", file=sys.stderr)
        return 2

    try:
        rc = run_switch_checks(client, args)
        if rc != 0:
            return rc

        rc = run_job_checks(client, args)
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
            "dock3 cmd test ok "
            f"(accepted={client.stats['accepted']}, events={client.stats['events']}, "
            f"ignored_mismatched={client.stats['ignored_mismatched']}, "
            f"ignored_legacy={client.stats['ignored_legacy']}, "
            f"ignored_late={client.stats['ignored_late']}, ignored_total={stale_total})"
        )
        return 0
    finally:
        client.disconnect()


if __name__ == "__main__":
    raise SystemExit(main())
