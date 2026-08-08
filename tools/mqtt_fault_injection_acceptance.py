#!/usr/bin/env python3
"""无实体机巢/负载条件下的故障注入与 HMS/MQTT 验收。"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import uuid
from pathlib import Path
from threading import Event


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "tools"))

from mqtt_test_client import MqttRequestClient, build_client, result_code
from src.utils.dji_error_codes import INVALID_COMMAND_FORMAT
from tool_config import add_config_argument, load_config, resolve_mqtt_settings


SCENARIOS = [
    {
        "name": "nest-heartbeat-lost",
        "faults": {"nest_heartbeat": False},
        "hms_key": "dock3.nest.nest_heartbeat.lost",
    },
    {
        "name": "drone-heartbeat-lost",
        "faults": {"drone_heartbeat": False},
        "hms_key": "dock3.nest.drone_heartbeat.lost",
        "osd": {"drone_in_dock": 0},
    },
    {
        "name": "estop-active",
        "faults": {"estop_state": True},
        "hms_key": "dock3.nest.estop.active",
        "osd": {"emergency_stop_state": 1},
    },
    {
        "name": "cover-abnormal",
        "faults": {"gate_state": 6},
        "hms_key": "dock3.nest.cover.abnormal",
        "osd": {"cover_state": 3},
    },
    {
        "name": "charging-abnormal",
        "faults": {
            "charging_state": True,
            "charge_volt": 0,
            "charge_current": 0,
        },
        "hms_key": "dock3.nest.charging.abnormal",
        "osd": {"charging_state": 1},
    },
    {
        "name": "nest-busy",
        "faults": {"system_busy": True},
        "hms_key": "dock3.nest.system_busy.active",
    },
    {
        "name": "nest-fault-error-code",
        "faults": {"fault_state": True, "error_code": 42},
        "hms_key": "dock3.nest.error_code.active",
        "osd": {"alarm_state": 1},
    },
]


def atomic_write_json(path: Path, document: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(document, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def nested_property(payload: dict, name: str):
    data = payload.get("data", {}) if isinstance(payload, dict) else {}
    properties = data.get("property", data) if isinstance(data, dict) else {}
    value = properties.get(name) if isinstance(properties, dict) else None
    # Dock3 OSD 使用 {"field": {"value": ...}}，state 使用裸值。
    if isinstance(value, dict) and set(value) == {"value"}:
        return value["value"]
    return value


def hms_keys(payload: dict) -> set[str]:
    found: set[str] = set()

    def visit(value):
        if isinstance(value, dict):
            key = value.get("hms_key")
            if key:
                found.add(str(key))
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(payload)
    return found


def wait_for_event(client, method: str, start: int, predicate, timeout: float):
    deadline = time.time() + timeout
    while time.time() < deadline:
        events = client.events_by_method.get(method, [])[start:]
        for event in events:
            if predicate(event):
                return event
        client.event_signal.wait(min(0.5, max(0.05, deadline - time.time())))
        client.event_signal.clear()
    return None


def wait_for_observed(client, topic: str, start: int, predicate, timeout: float):
    deadline = time.time() + timeout
    while time.time() < deadline:
        messages = client.observed_by_topic.get(topic, [])[start:]
        for message in messages:
            if predicate(message):
                return message
        client.observed_signal.wait(min(0.5, max(0.05, deadline - time.time())))
        client.observed_signal.clear()
    return None


def run_fault_scenarios(client, fault_file: Path, osd_topic: str, timeout: float):
    evidence = []
    for scenario in SCENARIOS:
        hms_start = client.event_count("hms")
        osd_start = client.observed_count(osd_topic)
        atomic_write_json(fault_file, {
            "active": True,
            "scenario": scenario["name"],
            "faults": scenario["faults"],
        })
        print(f"[注入] {scenario['name']}: {scenario['faults']}")

        hms = wait_for_event(
            client,
            "hms",
            hms_start,
            lambda item: scenario["hms_key"] in hms_keys(item),
            timeout,
        )
        if not hms:
            raise RuntimeError(f"{scenario['name']} 未生成 HMS: {scenario['hms_key']}")
        osd_evidence = None
        for name, expected in scenario.get("osd", {}).items():
            osd_evidence = wait_for_observed(
                client,
                osd_topic,
                osd_start,
                lambda item, key=name, value=expected: nested_property(item, key) == value,
                timeout,
            )
            if not osd_evidence:
                raise RuntimeError(f"{scenario['name']} OSD 缺少 {name}={expected!r}")

        clear_start = client.event_count("hms")
        atomic_write_json(fault_file, {
            "active": False,
            "scenario": f"recover-{scenario['name']}",
            "faults": {},
        })
        cleared = wait_for_event(
            client,
            "hms",
            clear_start,
            lambda item: scenario["hms_key"] not in hms_keys(item),
            timeout,
        )
        if not cleared:
            raise RuntimeError(f"{scenario['name']} 恢复后 HMS 未清除")
        evidence.append({
            "scenario": scenario["name"],
            "hms_key": scenario["hms_key"],
            "observed_hms_keys": sorted(hms_keys(hms)),
            "operation_id": operation.get("data", {}).get("operation_id"),
            "osd": scenario.get("osd", {}),
            "error_code": scenario["faults"].get("error_code", 0),
            "result": "passed",
        })
        print(f"[通过] {scenario['name']} HMS/events/OSD/恢复")
    return evidence


def run_state_and_duplicate_test(client, state_topic: str, timeout: float):
    state_start = client.observed_count(state_topic)
    duplicate_before = client.stats["ignored_duplicate"]
    tid = f"fault-duplicate-{uuid.uuid4()}"
    payload = {
        "tid": tid,
        "bid": tid,
        "timestamp": int(time.time() * 1000),
        "gateway": client.sn,
        "method": "property_set",
        "need_reply": 1,
        "data": {"silent_mode": 1},
    }
    waiter = {
        "event": Event(),
        "topic": "",
        "payload": None,
        "method": "property_set",
        "expected_topic": f"thing/product/{client.sn}/property/set_reply",
        "expected_status": None,
    }
    client.pending[tid] = waiter
    topic = f"thing/product/{client.sn}/property/set"
    client.publish_json(topic, payload)
    client.publish_json(topic, payload)
    if not waiter["event"].wait(timeout):
        client.pending.pop(tid, None)
        raise RuntimeError("重复消息测试未收到首个 property_set_reply")
    time.sleep(1.0)
    client.pending.pop(tid, None)
    client._mark_completed_tid(tid)
    if waiter["payload"] is None:
        raise RuntimeError("重复消息测试回包为空")
    node = waiter["payload"].get("data", {}).get("silent_mode", {})
    if node.get("state", {}).get("result") != 0:
        raise RuntimeError(f"重复消息 property_set 失败: {waiter['payload']}")
    state = wait_for_observed(
        client,
        state_topic,
        state_start,
        lambda item: nested_property(item, "silent_mode") == 1,
        timeout,
    )
    if not state:
        raise RuntimeError("pushMode=1 silent_mode 未生成 state")
    matching_states = [
        item for item in client.observed_by_topic.get(state_topic, [])[state_start:]
        if nested_property(item, "silent_mode") == 1
    ]
    if len(matching_states) != 1:
        raise RuntimeError(f"重复请求造成 state 重复上报: count={len(matching_states)}")
    if client.stats["ignored_duplicate"] <= duplicate_before:
        raise RuntimeError("网关未对重复请求重放缓存回包")
    print("[通过] 重复 property_set 仅执行一次、缓存回包重放、state 仅上报一次")
    return {"tid": tid, "state_count": 1, "reply_replayed": True}


def run_wrong_topic_test(client, timeout: float):
    rc, reply = client.request(
        "property_set",
        {"silent_mode": 0},
        timeout,
        channel="services",
        allowed_results={INVALID_COMMAND_FORMAT},
    )
    if rc != 0 or result_code(reply) != INVALID_COMMAND_FORMAT:
        raise RuntimeError(f"错误 topic 未返回 {INVALID_COMMAND_FORMAT}: {reply}")
    print(f"[通过] 错误 topic 返回官方错误码 {INVALID_COMMAND_FORMAT}")
    return {"result": INVALID_COMMAND_FORMAT}


def run_gateway_reconnect(args, client, osd_topic: str):
    import paho.mqtt.client as mqtt

    intruder = build_client(mqtt, f"edge_gateway_{args.sn}")
    config = load_config(args.config)
    mqtt_config = config.get("nanomq", {}) if isinstance(config, dict) else {}
    if mqtt_config.get("username"):
        intruder.username_pw_set(mqtt_config["username"], mqtt_config.get("password", ""))
    connected = Event()
    intruder.on_connect = lambda _client, _userdata, _flags, rc: connected.set() if rc == 0 else None
    intruder.connect(args.host, args.port, keepalive=10)
    intruder.loop_start()
    if not connected.wait(args.timeout):
        intruder.loop_stop()
        intruder.disconnect()
        raise RuntimeError("无法用同 client_id 触发网关 MQTT 断开")
    time.sleep(1.0)
    intruder.loop_stop()
    intruder.disconnect()
    after_disconnect = client.observed_count(osd_topic)
    resumed = client.wait_observed(osd_topic, after_disconnect, args.timeout)
    if not resumed:
        raise RuntimeError("网关 MQTT 断开后未在超时内恢复 OSD")
    print("[通过] 网关 MQTT 被动断开后自动重连并恢复 OSD")
    return {"client_id": f"edge_gateway_{args.sn}", "osd_resumed": True}


def parse_args():
    parser = argparse.ArgumentParser(description="机巢故障注入、HMS 与 MQTT 验收")
    add_config_argument(parser)
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--sn", default=None)
    parser.add_argument("--timeout", type=float, default=15.0)
    parser.add_argument("--qos", type=int, choices=[0, 1, 2], default=1)
    parser.add_argument("--fault-state-file", default="/tmp/edge_gateway_nest_faults.json")
    parser.add_argument("--evidence-dir", default="")
    parser.add_argument("--skip-gateway-reconnect", action="store_true")
    return parser.parse_args()


def main():
    args = parse_args()
    args.host, args.port, args.sn = resolve_mqtt_settings(args)
    started_ms = int(time.time() * 1000)
    fault_file = Path(args.fault_state_file)
    evidence_dir = Path(args.evidence_dir) if args.evidence_dir else (
        PROJECT_ROOT / "logs" / "acceptance_evidence" / time.strftime("%Y%m%d_%H%M%S")
    )
    evidence_dir.mkdir(parents=True, exist_ok=True)
    osd_topic = f"thing/product/{args.sn}/osd"
    state_topic = f"thing/product/{args.sn}/state"
    client = MqttRequestClient(
        args.host,
        args.port,
        args.sn,
        args.qos,
        quiet_stale=True,
        client_id_prefix="edge_gateway_fault_acceptance",
        subscribe_events=True,
        observe_topics={osd_topic, state_topic},
    )
    if not client.connect(args.timeout):
        print("MQTT 连接超时", file=sys.stderr)
        return 1

    summary = {
        "started_at_ms": started_ms,
        "gateway_sn": args.sn,
        "fault_state_file": str(fault_file),
    }
    try:
        summary["fault_scenarios"] = run_fault_scenarios(
            client, fault_file, osd_topic, args.timeout
        )
        summary["duplicate_and_state"] = run_state_and_duplicate_test(
            client, state_topic, args.timeout
        )
        summary["wrong_topic"] = run_wrong_topic_test(client, args.timeout)
        if not args.skip_gateway_reconnect:
            summary["mqtt_reconnect"] = run_gateway_reconnect(args, client, osd_topic)
        else:
            summary["mqtt_reconnect"] = {"skipped": True}
        summary["result"] = "passed"
        summary["finished_at_ms"] = int(time.time() * 1000)
        output = evidence_dir / "fault_injection_summary.json"
        output.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"fault injection acceptance ok; evidence -> {output}")
        return 0
    except Exception as exc:
        summary["result"] = "failed"
        summary["error"] = str(exc)
        summary["finished_at_ms"] = int(time.time() * 1000)
        output = evidence_dir / "fault_injection_summary.json"
        output.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"fault injection acceptance failed: {exc}", file=sys.stderr)
        print(f"failure evidence -> {output}", file=sys.stderr)
        return 2
    finally:
        atomic_write_json(fault_file, {
            "active": False,
            "scenario": "acceptance-final-recovery",
            "faults": {},
        })
        client.disconnect()


if __name__ == "__main__":
    raise SystemExit(main())
