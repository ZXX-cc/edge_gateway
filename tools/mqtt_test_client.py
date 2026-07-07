#!/usr/bin/env python3
"""Shared MQTT request client for gateway validation tools."""

import json
import sys
import time
import uuid
from threading import Event
from typing import Any, Iterable, Optional


CHANNELS = {
    "services": ("thing/product/{sn}/services", "thing/product/{sn}/services_reply"),
    "property": ("thing/product/{sn}/property/set", "thing/product/{sn}/property/set_reply"),
    "drc": ("thing/product/{sn}/drc/down", "thing/product/{sn}/drc/up"),
}
EVENTS_TOPIC = "thing/product/{sn}/events"
STATUS_TOPIC = "sys/product/{sn}/status"
STATUS_REPLY_TOPIC = "sys/product/{sn}/status_reply"

METHOD_CHANNEL_HINTS = {
    "property_set": "property",
    "drc_mode_enter": "services",
    "drc_mode_exit": "services",
    "takeoff_to_point": "services",
    "stick_control": "drc",
    "drone_control": "drc",
    "payload_control": "drc",
    "drc_speed_push": "drc",
    "drc_heading_push": "drc",
    "drc_altitude_push": "drc",
    "drc_flyto_point_push": "drc",
    "drc_gimbal_speed_push": "drc",
}


def resolve_channel(method: str, channel: str = "auto") -> str:
    if channel == "auto":
        return METHOD_CHANNEL_HINTS.get(method, "services")
    if channel not in CHANNELS:
        raise ValueError(f"unsupported channel: {channel}")
    return channel


def build_client(mqtt_module, client_id: str):
    if hasattr(mqtt_module, "CallbackAPIVersion"):
        try:
            return mqtt_module.Client(mqtt_module.CallbackAPIVersion.VERSION1, client_id=client_id)
        except TypeError:
            pass
    return mqtt_module.Client(client_id=client_id)


def result_code(message: Any) -> Optional[int]:
    if not isinstance(message, dict):
        return None
    data = message.get("data")
    if not isinstance(data, dict):
        return None
    result = data.get("result")
    return result if isinstance(result, int) else None


def property_result(message: Any, name: str) -> Optional[int]:
    if not isinstance(message, dict):
        return None
    node = message.get("data", {}).get(name)
    if not isinstance(node, dict):
        return None
    state = node.get("state")
    if not isinstance(state, dict):
        return None
    result = state.get("result")
    return result if isinstance(result, int) else None


def property_results(message: Any) -> dict[str, int]:
    if not isinstance(message, dict):
        return {}
    data = message.get("data")
    if not isinstance(data, dict):
        return {}
    results = {}
    for name, value in data.items():
        if not isinstance(value, dict):
            continue
        state = value.get("state")
        if not isinstance(state, dict):
            continue
        result = state.get("result")
        if isinstance(result, int):
            results[name] = result
    return results


def output_status(message: Any) -> Optional[str]:
    if not isinstance(message, dict):
        return None
    data = message.get("data")
    if not isinstance(data, dict):
        return None
    output = data.get("output")
    if not isinstance(output, dict):
        return None
    status = output.get("status")
    return status if isinstance(status, str) else None


def looks_like_legacy_stale_reply(message: Any) -> bool:
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
    def __init__(
        self,
        host: str,
        port: int,
        sn: str,
        qos: int = 1,
        quiet_stale: bool = False,
        client_id_prefix: str = "edge_gateway_mqtt_test",
        subscribe_events: bool = False,
        subscribe_status: bool = False,
        observe_topics: Optional[Iterable[str]] = None,
        subscribe_default_replies: bool = True,
        mqtt_module=None,
    ):
        if mqtt_module is None:
            try:
                import paho.mqtt.client as mqtt_module
            except ImportError as exc:
                raise SystemExit("paho-mqtt is required. Install requirements-ubuntu.txt first.") from exc

        self.host = host
        self.port = int(port)
        self.sn = sn
        self.qos = qos
        self.quiet_stale = quiet_stale
        self.subscribe_default_replies = subscribe_default_replies
        self.event_topics = {EVENTS_TOPIC.format(sn=self.sn)} if subscribe_events else set()
        self.events_by_method: dict[str, list[dict[str, Any]]] = {}
        self.event_signal = Event()
        self.status_topic = STATUS_TOPIC.format(sn=self.sn)
        self.status_reply_topic = STATUS_REPLY_TOPIC.format(sn=self.sn)
        self.status_topics = {self.status_topic} if subscribe_status else set()
        self.status_messages_by_method: dict[str, list[dict[str, Any]]] = {}
        self.status_signal = Event()
        self.observed_topics = set(observe_topics or ())
        self.observed_by_topic: dict[str, list[Any]] = {}
        self.observed_signal = Event()
        self.connected = False
        self.connected_event = Event()
        self.connect_rc: Optional[int] = None
        self.pending: dict[str, dict[str, Any]] = {}
        self.stats = {
            "accepted": 0,
            "ignored_late": 0,
            "ignored_mismatched": 0,
            "ignored_legacy": 0,
            "ignored_wrong_topic": 0,
            "events": 0,
            "status": 0,
            "observed": 0,
        }
        self.client = build_client(mqtt_module, f"{client_id_prefix}_{uuid.uuid4().hex[:8]}")

    def connect(self, timeout: float) -> bool:
        self.client.on_connect = self._on_connect
        self.client.on_message = self._on_message
        self.client.connect(self.host, self.port, keepalive=30)
        self.client.loop_start()
        return self.connected_event.wait(timeout) and self.connect_rc == 0

    def disconnect(self) -> None:
        self.client.loop_stop()
        self.client.disconnect()
        self.connected = False

    def _on_connect(self, client, userdata, flags, rc):
        self.connect_rc = rc
        if rc != 0:
            self.connected = False
            print(f"connect failed: rc={rc}", file=sys.stderr)
            self.connected_event.set()
            return
        if self.subscribe_default_replies:
            for _, reply_tpl in CHANNELS.values():
                topic = reply_tpl.format(sn=self.sn)
                self.client.subscribe(topic, qos=self.qos)
                print(f"mqtt test subscribed -> {topic}")
        for topic in sorted(self.event_topics):
            self.client.subscribe(topic, qos=self.qos)
            print(f"mqtt test subscribed -> {topic}")
        for topic in sorted(self.status_topics):
            self.client.subscribe(topic, qos=self.qos)
            print(f"mqtt test subscribed -> {topic}")
        for topic in sorted(self.observed_topics):
            self.client.subscribe(topic, qos=self.qos)
            print(f"mqtt test subscribed -> {topic}")
        self.connected = True
        self.connected_event.set()

    def _on_message(self, client, userdata, msg):
        try:
            payload = json.loads(msg.payload.decode("utf-8"))
        except Exception as exc:
            self._handle_invalid_json(msg.topic, exc)
            return

        if msg.topic in self.event_topics:
            method = payload.get("method") if isinstance(payload, dict) else None
            self.events_by_method.setdefault(method, []).append(payload)
            self.stats["events"] += 1
            self.event_signal.set()
            if not self.quiet_stale:
                print(f"event observed: method={method}, tid={payload.get('tid') if isinstance(payload, dict) else None}")
            return

        if msg.topic in self.status_topics:
            self._handle_status_message(payload)
            return

        if msg.topic in self.observed_topics:
            self._handle_observed_message(msg.topic, payload)
            return

        tid = payload.get("tid") if isinstance(payload, dict) else None
        waiter = self.pending.get(tid)
        if not waiter:
            self.stats["ignored_late"] += 1
            if not self.quiet_stale:
                method = payload.get("method") if isinstance(payload, dict) else None
                print(f"ignored <- {msg.topic}, method={method}, tid={tid}")
            return

        expected_topic = waiter["expected_topic"]
        expected_method = waiter["method"]
        actual_method = payload.get("method")
        if msg.topic != expected_topic:
            self.stats["ignored_wrong_topic"] += 1
            if not self.quiet_stale:
                print(
                    f"ignored wrong topic reply <- {msg.topic}, "
                    f"expected_topic={expected_topic}, method={actual_method}, tid={tid}"
                )
            return

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
        if expected_status is not None and output_status(payload) is None and result_code(payload) == 0:
            self.stats["ignored_legacy"] += 1
            if not self.quiet_stale:
                print(
                    f"ignored legacy reply without data.output.status <- {msg.topic}, "
                    f"method={actual_method}, tid={tid}"
                )
                print(json.dumps(payload, ensure_ascii=False, indent=2))
            return

        self.stats["accepted"] += 1
        waiter["topic"] = msg.topic
        waiter["payload"] = payload
        waiter["event"].set()

    def request(
        self,
        method: str,
        data: Optional[dict],
        timeout: float,
        channel: str = "auto",
        allowed_results: Optional[set[int]] = None,
        verbose: bool = True,
        need_reply: int = 1,
        expected_status: Optional[str] = None,
    ) -> tuple[int, Optional[dict]]:
        selected_channel = resolve_channel(method, channel)
        publish_tpl, reply_tpl = CHANNELS[selected_channel]
        publish_topic = publish_tpl.format(sn=self.sn)
        reply_topic = reply_tpl.format(sn=self.sn)
        tid = str(uuid.uuid4())
        message = {
            "tid": tid,
            "bid": str(uuid.uuid4()),
            "timestamp": int(time.time() * 1000),
            "gateway": self.sn,
            "method": method,
            "data": data or {},
        }
        if need_reply:
            message["need_reply"] = 1
        waiter = {
            "event": Event(),
            "topic": "",
            "payload": None,
            "method": method,
            "expected_topic": reply_topic,
            "expected_status": expected_status,
        }
        self.pending[tid] = waiter

        self.client.publish(publish_topic, json.dumps(message, ensure_ascii=False), qos=self.qos)
        if verbose:
            print(f"published {method} -> {publish_topic}, tid={tid}, need_reply={need_reply}")
            print(f"waiting reply on {reply_topic}")

        if not need_reply:
            if waiter["event"].wait(timeout):
                self.pending.pop(tid, None)
                payload = waiter["payload"]
                print(f"{method} unexpectedly received reply for no-reply request", file=sys.stderr)
                print(json.dumps(payload, ensure_ascii=False, indent=2))
                return 8, payload
            self.pending.pop(tid, None)
            if verbose:
                print(f"no reply observed as expected: method={method}, tid={tid}")
            return 0, None

        if not waiter["event"].wait(timeout):
            self.pending.pop(tid, None)
            print(f"timeout waiting for {method}, tid={tid}", file=sys.stderr)
            return 2, None

        self.pending.pop(tid, None)
        payload = waiter["payload"]
        if verbose:
            print(f"received <- {waiter['topic']}, method={payload.get('method')}, tid={tid}")
            print(json.dumps(payload, ensure_ascii=False, indent=2))

        if allowed_results is not None:
            allowed_results = set(allowed_results)
            actual = result_code(payload)
            if actual not in allowed_results:
                print(f"{method} unexpected result: expected={sorted(allowed_results)}, actual={actual}", file=sys.stderr)
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

    def event_count(self, method: str) -> int:
        return len(self.events_by_method.get(method, []))

    def has_event(self, method: str) -> bool:
        return bool(self.events_by_method.get(method))

    def wait_event(
        self,
        method: str,
        previous_count: int,
        timeout: float,
        expected_status: Optional[str] = "ok",
    ) -> tuple[int, Optional[dict]]:
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
                            f"{method} event output.status mismatch: "
                            f"expected={expected_status}, actual={actual_status}",
                            file=sys.stderr,
                        )
                        return 6, event
                return 0, event

            remaining = deadline - time.time()
            if remaining <= 0:
                break
            self.event_signal.wait(max(0.05, min(0.5, remaining)))
            self.event_signal.clear()

        print(f"timeout waiting for event: method={method}", file=sys.stderr)
        return 7, None

    def _handle_status_message(self, payload: dict) -> None:
        method = payload.get("method") if isinstance(payload, dict) else None
        self.status_messages_by_method.setdefault(method, []).append(payload)
        self.stats["status"] += 1
        self.status_signal.set()
        if not self.quiet_stale:
            print(f"status observed: method={method}, tid={payload.get('tid') if isinstance(payload, dict) else None}")

    def _handle_invalid_json(self, topic: str, exc: Exception) -> None:
        print(f"invalid JSON on {topic}: {exc}", file=sys.stderr)

    def _handle_observed_message(self, topic: str, payload: Any) -> None:
        self.observed_by_topic.setdefault(topic, []).append(payload)
        self.stats["observed"] += 1
        self.observed_signal.set()
        if not self.quiet_stale:
            tid = payload.get("tid") if isinstance(payload, dict) else None
            method = payload.get("method") if isinstance(payload, dict) else None
            print(f"observed <- {topic}, method={method}, tid={tid}")

    def observed_count(self, topic: str) -> int:
        return len(self.observed_by_topic.get(topic, []))

    def wait_observed(self, topic: str, previous_count: int, timeout: float) -> Optional[Any]:
        deadline = time.time() + timeout
        while time.time() < deadline:
            messages = self.observed_by_topic.get(topic, [])
            if len(messages) > previous_count:
                return messages[-1]

            remaining = deadline - time.time()
            if remaining <= 0:
                break
            self.observed_signal.wait(max(0.05, min(0.5, remaining)))
            self.observed_signal.clear()
        return None

    def status_count(self, method: str) -> int:
        return len(self.status_messages_by_method.get(method, []))

    def wait_status(self, method: str, previous_count: int, timeout: float) -> Optional[dict]:
        deadline = time.time() + timeout
        while time.time() < deadline:
            messages = self.status_messages_by_method.get(method, [])
            if len(messages) > previous_count:
                return messages[-1]

            remaining = deadline - time.time()
            if remaining <= 0:
                break
            self.status_signal.wait(max(0.05, min(0.5, remaining)))
            self.status_signal.clear()
        return None

    def publish_json(self, topic: str, payload: dict) -> None:
        return self.client.publish(topic, json.dumps(payload, ensure_ascii=False), qos=self.qos)

    def stale_total(self) -> int:
        return (
            self.stats["ignored_mismatched"]
            + self.stats["ignored_legacy"]
            + self.stats["ignored_late"]
            + self.stats["ignored_wrong_topic"]
        )
