#!/usr/bin/env python3
"""Validate Dock3 OSD and state reports published by the running gateway."""

import argparse
import json
import sys
import time
import uuid
from threading import Event, Lock
from typing import Any, Dict, Iterable, List, Optional, Tuple

from tool_config import add_config_argument, resolve_mqtt_settings

mqtt = None


DEFAULT_DRONE_SN = "DRONESIM001"
THING_VERSION = "1.1.2"


def build_client(client_id):
    if hasattr(mqtt, "CallbackAPIVersion"):
        try:
            return mqtt.Client(mqtt.CallbackAPIVersion.VERSION1, client_id=client_id)
        except TypeError:
            pass
    return mqtt.Client(client_id=client_id)


def now_ms() -> int:
    return int(time.time() * 1000)


def is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def unwrap_value(node: Any) -> Any:
    if isinstance(node, dict) and "value" in node:
        return node.get("value")
    return node


def get_path(data: Dict[str, Any], path: str) -> Tuple[bool, Any]:
    node: Any = data
    for part in path.split("."):
        if not isinstance(node, dict) or part not in node:
            return False, None
        node = unwrap_value(node[part])
    return True, node


def type_label(expected: Any) -> str:
    if isinstance(expected, tuple):
        return "/".join(type_label(item) for item in expected)
    if expected == "number":
        return "number"
    if hasattr(expected, "__name__"):
        return expected.__name__
    return str(expected)


def check_field(
    data: Dict[str, Any],
    path: str,
    expected_type: Any,
    errors: List[str],
    *,
    allowed: Optional[Iterable[Any]] = None,
    min_value: Optional[float] = None,
    max_value: Optional[float] = None,
) -> None:
    exists, value = get_path(data, path)
    if not exists:
        errors.append(f"missing data.{path}")
        return

    if expected_type == "number":
        ok = is_number(value)
    elif isinstance(expected_type, tuple):
        ok = isinstance(value, expected_type)
    else:
        ok = isinstance(value, expected_type)

    if not ok:
        errors.append(f"data.{path} expected {type_label(expected_type)}, actual={type(value).__name__}")
        return

    if allowed is not None and value not in set(allowed):
        errors.append(f"data.{path} expected one of {sorted(set(allowed))}, actual={value}")
    if min_value is not None and value < min_value:
        errors.append(f"data.{path} expected >= {min_value}, actual={value}")
    if max_value is not None and value > max_value:
        errors.append(f"data.{path} expected <= {max_value}, actual={value}")


def envelope_errors(payload: Dict[str, Any], expected_gateway: str) -> List[str]:
    errors: List[str] = []
    for name in ("tid", "bid", "gateway", "timestamp", "data"):
        if name not in payload:
            errors.append(f"missing {name}")
    if payload.get("gateway") != expected_gateway:
        errors.append(f"gateway mismatch: expected={expected_gateway}, actual={payload.get('gateway')}")
    if "timestamp" in payload and not isinstance(payload.get("timestamp"), int):
        errors.append("timestamp must be int milliseconds")
    if "data" in payload and not isinstance(payload.get("data"), dict):
        errors.append("data must be object")
    return errors


def validate_dock_osd(payload: Dict[str, Any], gateway_sn: str) -> List[str]:
    errors = envelope_errors(payload, gateway_sn)
    data = payload.get("data")
    if not isinstance(data, dict):
        return errors

    check_field(data, "mode_code", int, errors, min_value=0)
    check_field(data, "flighttask_step_code", int, errors, min_value=0)
    check_field(data, "cover_state", int, errors, min_value=0)
    check_field(data, "putter_state", int, errors, min_value=0)
    check_field(data, "drone_in_dock", int, errors, allowed={0, 1})
    check_field(data, "network_state.type", int, errors, min_value=0)
    check_field(data, "network_state.quality", "number", errors, min_value=0)
    check_field(data, "network_state.rate", "number", errors, min_value=0)
    check_field(data, "drone_charge_state.capacity_percent", "number", errors, min_value=0, max_value=100)
    check_field(data, "drone_charge_state.state", int, errors, min_value=0)
    check_field(data, "position_state.is_fixed", int, errors, min_value=0)
    check_field(data, "position_state.gps_number", int, errors, min_value=0)
    check_field(data, "electricity", "number", errors, min_value=0, max_value=100)
    check_field(data, "wind_speed", "number", errors, min_value=0)
    check_field(data, "drc_state", int, errors, min_value=0)
    check_field(data, "maintain_status.maintain_status_array", list, errors)

    if "battery" in data:
        errors.append("dock OSD must use Dock3 dock properties, not drone battery OSD")
    return errors


def validate_drone_osd(payload: Dict[str, Any], gateway_sn: str) -> List[str]:
    errors = envelope_errors(payload, gateway_sn)
    data = payload.get("data")
    if not isinstance(data, dict):
        return errors

    for field in (
        "latitude",
        "longitude",
        "height",
        "altitude",
        "horizontal_speed",
        "vertical_speed",
        "attitude_pitch",
        "attitude_roll",
        "attitude_head",
        "wind_speed",
    ):
        check_field(data, field, "number", errors)

    check_field(data, "battery.capacity_percent", "number", errors, min_value=0, max_value=100)
    check_field(data, "battery.voltage", "number", errors, min_value=0)
    check_field(data, "position_state.gps_number", int, errors, min_value=0)
    check_field(data, "position_state.is_fixed", int, errors, min_value=0)
    check_field(data, "mode_code", int, errors, min_value=0)
    check_field(data, "gear", int, errors, min_value=0)
    check_field(data, "flight_time", int, errors, min_value=0)
    check_field(data, "timestamp", int, errors, min_value=0)

    if "drone_charge_state" in data:
        errors.append("drone OSD must use drone telemetry, not dock drone_charge_state")
    return errors


def validate_state(payload: Dict[str, Any], gateway_sn: str, expected_property: str) -> List[str]:
    errors = envelope_errors(payload, gateway_sn)
    data = payload.get("data")
    if not isinstance(data, dict):
        return errors

    properties = data.get("property")
    if expected_property:
        if not isinstance(properties, dict):
            errors.append(f"ignored state without property.{expected_property}")
            return errors
        if expected_property not in properties:
            errors.append(f"ignored state without property.{expected_property}")
            return errors
        if expected_property == "silent_mode" and properties[expected_property] not in {0, 1}:
            errors.append(f"state data.property.silent_mode expected 0/1, actual={properties[expected_property]}")
        return errors

    if not isinstance(properties, dict) and "sub_device" not in data:
        errors.append("state data must contain property object or sub_device")
    return errors


def property_result(reply: Dict[str, Any], property_name: str) -> Optional[int]:
    node = reply.get("data", {}).get(property_name)
    if not isinstance(node, dict):
        return None
    state = node.get("state")
    if not isinstance(state, dict):
        return None
    return state.get("result")


def build_update_topo_payload(args) -> Dict[str, Any]:
    return {
        "domain": args.gateway_domain,
        "type": args.gateway_type,
        "sub_type": args.gateway_sub_type,
        "device_secret": args.gateway_secret,
        "nonce": args.gateway_nonce,
        "thing_version": args.thing_version,
        "sub_devices": [
            {
                "sn": args.drone_sn,
                "domain": args.drone_domain,
                "type": args.drone_type,
                "sub_type": args.drone_sub_type,
                "index": args.drone_index,
                "device_secret": args.drone_secret,
                "nonce": args.drone_nonce,
                "thing_version": args.thing_version,
            }
        ],
    }


class Dock3OsdStateClient:
    def __init__(self, host: str, port: int, sn: str, drone_sn: str, qos: int, expected_state_property: str):
        self.host = host
        self.port = port
        self.sn = sn
        self.drone_sn = drone_sn
        self.qos = qos
        self.expected_state_property = expected_state_property
        self.connected = Event()
        self.changed = Event()
        self.lock = Lock()
        self.dock_osd_topic = f"thing/product/{sn}/osd"
        self.drone_osd_topic = f"thing/product/{drone_sn}/osd"
        self.state_topic = f"thing/product/{sn}/state"
        self.services_topic = f"thing/product/{sn}/services"
        self.services_reply_topic = f"thing/product/{sn}/services_reply"
        self.property_topic = f"thing/product/{sn}/property/set"
        self.property_reply_topic = f"thing/product/{sn}/property/set_reply"
        self.pending_replies: Dict[str, Dict[str, Any]] = {}
        self.valid_dock_osd: List[Dict[str, Any]] = []
        self.valid_drone_osd: List[Dict[str, Any]] = []
        self.valid_state: List[Dict[str, Any]] = []
        self.invalid_samples: List[str] = []
        self.client = build_client(f"edge_gateway_dock3_osd_state_{uuid.uuid4().hex[:8]}")

    def connect(self, timeout: float) -> bool:
        self.client.on_connect = self._on_connect
        self.client.on_message = self._on_message
        self.client.connect(self.host, self.port, keepalive=30)
        self.client.loop_start()
        return self.connected.wait(timeout)

    def disconnect(self) -> None:
        self.client.loop_stop()
        self.client.disconnect()

    def _on_connect(self, client, userdata, flags, rc):
        if rc != 0:
            print(f"connect failed: rc={rc}", file=sys.stderr)
            self.connected.set()
            return
        for topic in (
            self.dock_osd_topic,
            self.drone_osd_topic,
            self.state_topic,
            self.services_reply_topic,
            self.property_reply_topic,
        ):
            client.subscribe(topic, qos=self.qos)
            print(f"dock3 osd/state subscribed -> {topic}")
        self.connected.set()

    def _record_invalid(self, topic: str, errors: List[str], payload: Dict[str, Any]) -> None:
        preview = json.dumps(payload, ensure_ascii=False)[:500]
        self.invalid_samples.append(f"{topic}: {'; '.join(errors)}; payload={preview}")

    def _on_message(self, client, userdata, msg):
        try:
            payload = json.loads(msg.payload.decode("utf-8"))
        except Exception as exc:
            with self.lock:
                self.invalid_samples.append(f"{msg.topic}: invalid JSON: {exc}")
                self.changed.set()
            return

        with self.lock:
            if msg.topic == self.dock_osd_topic:
                errors = validate_dock_osd(payload, self.sn)
                if errors:
                    self._record_invalid(msg.topic, errors, payload)
                else:
                    self.valid_dock_osd.append(payload)
                    print(f"valid dock osd <- {msg.topic}, count={len(self.valid_dock_osd)}")

            elif msg.topic == self.drone_osd_topic:
                errors = validate_drone_osd(payload, self.sn)
                if errors:
                    self._record_invalid(msg.topic, errors, payload)
                else:
                    self.valid_drone_osd.append(payload)
                    print(f"valid drone osd <- {msg.topic}, count={len(self.valid_drone_osd)}")

            elif msg.topic == self.state_topic:
                errors = validate_state(payload, self.sn, self.expected_state_property)
                if errors and all(error.startswith("ignored state ") for error in errors):
                    pass
                elif errors:
                    self._record_invalid(msg.topic, errors, payload)
                else:
                    self.valid_state.append(payload)
                    print(f"valid state <- {msg.topic}, count={len(self.valid_state)}")

            elif msg.topic in (self.services_reply_topic, self.property_reply_topic):
                tid = payload.get("tid")
                waiter = self.pending_replies.get(tid)
                if waiter is not None:
                    waiter["topic"] = msg.topic
                    waiter["payload"] = payload
                    waiter["event"].set()

            self.changed.set()

    def publish_services_request(self, method: str, data: Dict[str, Any], timeout: float) -> Tuple[int, Optional[Dict[str, Any]]]:
        tid = str(uuid.uuid4())
        message = {
            "tid": tid,
            "bid": str(uuid.uuid4()),
            "timestamp": now_ms(),
            "gateway": self.sn,
            "method": method,
            "need_reply": 1,
            "data": data,
        }
        waiter = {"event": Event(), "payload": None, "topic": ""}
        with self.lock:
            self.pending_replies[tid] = waiter

        self.client.publish(self.services_topic, json.dumps(message, ensure_ascii=False), qos=self.qos)
        print(f"published {method} -> {self.services_topic}, tid={tid}")

        deadline = time.time() + timeout
        last_reply = None
        while time.time() < deadline:
            if not waiter["event"].wait(max(0.05, deadline - time.time())):
                break
            reply = waiter["payload"]
            waiter["event"].clear()
            last_reply = reply
            print(f"received <- {waiter['topic']}, method={reply.get('method')}, tid={tid}")
            print(json.dumps(reply, ensure_ascii=False, indent=2))
            result = reply.get("data", {}).get("result")
            if result == 0:
                with self.lock:
                    self.pending_replies.pop(tid, None)
                return 0, reply
            print(f"ignored non-success {method} reply while waiting for result=0: result={result}")

        with self.lock:
            self.pending_replies.pop(tid, None)
        print(f"timeout waiting for successful {method} reply, tid={tid}", file=sys.stderr)
        return 2, last_reply

    def publish_property_set(self, property_name: str, value: Any, timeout: float) -> Tuple[int, Optional[Dict[str, Any]]]:
        tid = str(uuid.uuid4())
        message = {
            "tid": tid,
            "bid": str(uuid.uuid4()),
            "timestamp": now_ms(),
            "gateway": self.sn,
            "method": "property_set",
            "need_reply": 1,
            "data": {property_name: value},
        }
        waiter = {"event": Event(), "payload": None, "topic": ""}
        with self.lock:
            self.pending_replies[tid] = waiter

        self.client.publish(self.property_topic, json.dumps(message, ensure_ascii=False), qos=self.qos)
        print(f"published property_set {property_name}={value} -> {self.property_topic}, tid={tid}")

        deadline = time.time() + timeout
        last_reply = None
        while time.time() < deadline:
            if not waiter["event"].wait(max(0.05, deadline - time.time())):
                break
            reply = waiter["payload"]
            waiter["event"].clear()
            last_reply = reply
            print(f"received <- {waiter['topic']}, method={reply.get('method')}, tid={tid}")
            print(json.dumps(reply, ensure_ascii=False, indent=2))
            result = property_result(reply, property_name)
            if result == 0:
                with self.lock:
                    self.pending_replies.pop(tid, None)
                return 0, reply
            print(f"ignored non-success property_set reply while waiting for state.result=0: result={result}")

        with self.lock:
            self.pending_replies.pop(tid, None)
        print(f"timeout waiting for successful property_set reply, tid={tid}", file=sys.stderr)
        return 2, last_reply

    def counts(self) -> Tuple[int, int, int]:
        with self.lock:
            return len(self.valid_dock_osd), len(self.valid_drone_osd), len(self.valid_state)

    def has_counts(self, dock_count: int, drone_count: int, state_count: int) -> bool:
        dock_actual, drone_actual, state_actual = self.counts()
        return dock_actual >= dock_count and drone_actual >= drone_count and state_actual >= state_count

    def wait_for_counts(self, dock_count: int, drone_count: int, state_count: int, timeout: float) -> bool:
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.has_counts(dock_count, drone_count, state_count):
                return True
            self.changed.wait(max(0.05, min(0.5, deadline - time.time())))
            self.changed.clear()
        return self.has_counts(dock_count, drone_count, state_count)

    def print_summary(self) -> None:
        dock_count, drone_count, state_count = self.counts()
        print(f"dock_osd={dock_count}, drone_osd={drone_count}, state={state_count}, invalid={len(self.invalid_samples)}")
        if self.valid_dock_osd:
            data = self.valid_dock_osd[-1].get("data", {})
            summary = {
                "mode_code": unwrap_value(data.get("mode_code")),
                "cover_state": unwrap_value(data.get("cover_state")),
                "putter_state": unwrap_value(data.get("putter_state")),
                "drone_in_dock": unwrap_value(data.get("drone_in_dock")),
                "drc_state": unwrap_value(data.get("drc_state")),
            }
            print("dock_osd_summary:")
            print(json.dumps(summary, ensure_ascii=False, indent=2))
        if self.valid_drone_osd:
            data = self.valid_drone_osd[-1].get("data", {})
            summary = {
                "latitude": data.get("latitude"),
                "longitude": data.get("longitude"),
                "height": data.get("height"),
                "battery": data.get("battery"),
                "position_state": data.get("position_state"),
            }
            print("drone_osd_summary:")
            print(json.dumps(summary, ensure_ascii=False, indent=2))
        if self.valid_state:
            print("state_summary:")
            print(json.dumps(self.valid_state[-1].get("data", {}), ensure_ascii=False, indent=2))


def trigger_state(client: Dock3OsdStateClient, args) -> int:
    if args.skip_state:
        return 0
    if args.no_trigger_state:
        return 0

    before_state = client.counts()[2]
    for value in (0, 1):
        rc, _reply = client.publish_property_set(args.state_property, value, args.reply_timeout)
        if rc != 0:
            return rc
        if client.wait_for_counts(args.dock_samples, 0 if args.skip_drone else args.drone_samples, before_state + 1, 2.0):
            return 0
    return 0


def seed_topology(client: Dock3OsdStateClient, args) -> int:
    if args.skip_topo or args.skip_drone:
        return 0
    return client.publish_services_request("update_topo", build_update_topo_payload(args), args.reply_timeout)[0]


def main() -> int:
    global mqtt

    parser = argparse.ArgumentParser(description="Validate Dock3 OSD/state MQTT reports from a running gateway")
    add_config_argument(parser)
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--sn", default=None)
    parser.add_argument("--drone-sn", default=DEFAULT_DRONE_SN)
    parser.add_argument("--timeout", type=float, default=15.0)
    parser.add_argument("--reply-timeout", type=float, default=8.0)
    parser.add_argument("--qos", type=int, choices=[0, 1, 2], default=1)
    parser.add_argument("--dock-samples", type=int, default=1)
    parser.add_argument("--drone-samples", type=int, default=1)
    parser.add_argument("--skip-topo", action="store_true", help="Do not publish update_topo before waiting for drone OSD.")
    parser.add_argument("--skip-drone", action="store_true")
    parser.add_argument("--skip-state", action="store_true")
    parser.add_argument("--no-trigger-state", action="store_true", help="Only listen for state messages; do not publish property_set.")
    parser.add_argument("--state-property", default="silent_mode")
    parser.add_argument("--allow-invalid", action="store_true", help="Do not fail when invalid samples are observed before valid samples.")
    parser.add_argument("--gateway-domain", default="3")
    parser.add_argument("--gateway-type", type=int, default=2)
    parser.add_argument("--gateway-sub-type", type=int, default=0)
    parser.add_argument("--gateway-secret", default="gw-secret")
    parser.add_argument("--gateway-nonce", default="gw-nonce")
    parser.add_argument("--thing-version", default=THING_VERSION)
    parser.add_argument("--drone-domain", default="0")
    parser.add_argument("--drone-type", type=int, default=91)
    parser.add_argument("--drone-sub-type", type=int, default=0)
    parser.add_argument("--drone-index", default="A")
    parser.add_argument("--drone-secret", default="drone-secret")
    parser.add_argument("--drone-nonce", default="drone-nonce")
    args = parser.parse_args()
    args.host, args.port, args.sn = resolve_mqtt_settings(args)

    try:
        import paho.mqtt.client as mqtt_module
    except ImportError as exc:
        raise SystemExit("paho-mqtt is required. Install requirements-ubuntu.txt first.") from exc
    mqtt = mqtt_module

    expected_state_property = "" if args.skip_state else args.state_property
    client = Dock3OsdStateClient(args.host, args.port, args.sn, args.drone_sn, args.qos, expected_state_property)
    if not client.connect(args.timeout):
        print("MQTT connect timeout", file=sys.stderr)
        return 2

    try:
        rc = seed_topology(client, args)
        if rc != 0:
            return rc

        rc = trigger_state(client, args)
        if rc != 0:
            return rc

        expected_drone = 0 if args.skip_drone else args.drone_samples
        expected_state = 0 if args.skip_state else 1
        if not client.wait_for_counts(args.dock_samples, expected_drone, expected_state, args.timeout):
            dock_count, drone_count, state_count = client.counts()
            print(
                "timeout waiting for osd/state: "
                f"dock_osd={dock_count}/{args.dock_samples}, "
                f"drone_osd={drone_count}/{expected_drone}, "
                f"state={state_count}/{expected_state}",
                file=sys.stderr,
            )
            for sample in client.invalid_samples[:5]:
                print(f"invalid sample: {sample}", file=sys.stderr)
            return 4

        if client.invalid_samples and not args.allow_invalid:
            for sample in client.invalid_samples[:5]:
                print(f"invalid sample: {sample}", file=sys.stderr)
            print("invalid osd/state samples observed", file=sys.stderr)
            return 5

        client.print_summary()
        print("dock3 osd/state test ok")
        return 0
    finally:
        client.disconnect()


if __name__ == "__main__":
    raise SystemExit(main())
