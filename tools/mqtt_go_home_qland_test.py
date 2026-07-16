#!/usr/bin/env python3
"""Verify whether go_home automatically transitions into QLAND.

The gateway's go_home service only proves the command was accepted.  This
probe observes aircraft OSD after the command and passes only when telemetry
shows QLAND, or when the available Dock3 OSD layer at least reports landing
mode_code=7.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any, Iterable

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from mqtt_test_client import MqttRequestClient, result_code
from tool_config import add_config_argument, resolve_mqtt_settings


DEFAULT_DRONE_SN = "DRONESIM001"
QLAND_MODE_CODE = 7
RETURN_MODE_CODES = {6}


def _candidate_drone_sns(primary: str) -> list[str]:
    candidates = [primary, DEFAULT_DRONE_SN, "DRONE-SIM-001"]
    return list(dict.fromkeys(sn for sn in candidates if sn))


def osd_topics(drone_sn: str) -> list[str]:
    return [f"thing/product/{sn}/osd" for sn in _candidate_drone_sns(drone_sn)]


def print_step(label: str, ok: bool, detail: dict[str, Any] | None = None) -> None:
    print(f"[{'PASS' if ok else 'FAIL'}] {label}")
    if detail:
        print(json.dumps(detail, ensure_ascii=False, indent=2))


def _field_value(payload: Any, key: str) -> Any:
    if not isinstance(payload, dict):
        return None
    data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    if not isinstance(data, dict):
        return None
    value = data.get(key)
    return value.get("value") if isinstance(value, dict) and "value" in value else value


def _mode_name(payload: Any) -> str | None:
    for key in ("flight_mode", "mode_name", "mode"):
        value = _field_value(payload, key)
        if isinstance(value, str) and value.strip():
            return value.strip().upper()
    return None


def _mode_code(payload: Any) -> int | None:
    value = _field_value(payload, "mode_code")
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def osd_snapshot(topic: str | None, payload: Any) -> dict[str, Any]:
    if payload is None:
        return {"available": False, "topic": topic}
    return {
        "available": True,
        "topic": topic,
        "flight_mode": _mode_name(payload),
        "mode_code": _mode_code(payload),
        "height": _field_value(payload, "height"),
        "altitude": _field_value(payload, "altitude"),
        "vertical_speed": _field_value(payload, "vertical_speed"),
        "horizontal_speed": _field_value(payload, "horizontal_speed"),
        "gear": _field_value(payload, "gear"),
        "latitude": _field_value(payload, "latitude"),
        "longitude": _field_value(payload, "longitude"),
        "timestamp": _field_value(payload, "timestamp"),
    }


def _latest_observed(client: MqttRequestClient, topics: Iterable[str]) -> tuple[str | None, Any | None]:
    for topic in topics:
        messages = client.observed_by_topic.get(topic, [])
        if messages:
            return topic, messages[-1]
    return None, None


def has_osd_fields(payload: Any) -> bool:
    if not isinstance(payload, dict):
        return False
    data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    if not isinstance(data, dict):
        return False
    return any(key in data for key in ("mode_code", "flight_mode", "mode_name", "height"))


def wait_for_osd(
    client: MqttRequestClient,
    topics: list[str],
    timeout: float,
) -> tuple[str | None, Any | None]:
    topic, payload = _latest_observed(client, topics)
    if payload is not None and has_osd_fields(payload):
        return topic, payload

    counts = {topic: client.observed_count(topic) for topic in topics}
    deadline = time.monotonic() + max(0.0, timeout)
    while time.monotonic() < deadline:
        for topic in topics:
            messages = client.observed_by_topic.get(topic, [])
            if len(messages) <= counts[topic]:
                continue
            counts[topic] = len(messages)
            payload = messages[-1]
            if has_osd_fields(payload):
                return topic, payload

        remaining = deadline - time.monotonic()
        client.observed_signal.wait(max(0.05, min(0.5, remaining)))
        client.observed_signal.clear()

    return _latest_observed(client, topics)


def is_qland(payload: Any, *, strict_name: bool) -> tuple[bool, str]:
    mode_name = _mode_name(payload)
    mode_code = _mode_code(payload)
    if mode_name == "QLAND":
        return True, "flight_mode=QLAND"
    if strict_name:
        return False, f"strict mode requires flight_mode=QLAND, got {mode_name!r}"
    if mode_code == QLAND_MODE_CODE:
        return True, "Dock3 mode_code=7 (QLAND/LAND landing bucket)"
    return False, f"mode_name={mode_name!r}, mode_code={mode_code!r}"


def observe_qland(
    client: MqttRequestClient,
    topics: list[str],
    *,
    timeout: float,
    strict_name: bool,
) -> tuple[bool, dict[str, Any]]:
    counts = {topic: max(0, client.observed_count(topic) - 1) for topic in topics}
    samples: list[dict[str, Any]] = []
    saw_return_mode = False
    deadline = time.monotonic() + timeout

    while time.monotonic() < deadline:
        consumed = False
        for topic in topics:
            messages = client.observed_by_topic.get(topic, [])
            if len(messages) <= counts[topic]:
                continue
            counts[topic] = len(messages)
            payload = messages[-1]
            consumed = True
            snapshot = osd_snapshot(topic, payload)
            samples.append(snapshot)
            if _mode_code(payload) in RETURN_MODE_CODES or _mode_name(payload) in {"RTL", "QRTL"}:
                saw_return_mode = True

            ok, reason = is_qland(payload, strict_name=strict_name)
            print_step("OSD 模式样本", ok, {**snapshot, "match_reason": reason})
            if ok:
                return True, {
                    "reason": reason,
                    "saw_return_mode_before_qland": saw_return_mode,
                    "sample": snapshot,
                    "sample_count": len(samples),
                }

        if consumed:
            continue

        remaining = deadline - time.monotonic()
        client.observed_signal.wait(max(0.05, min(0.5, remaining)))
        client.observed_signal.clear()

    return False, {
        "reason": "timeout waiting for QLAND after go_home",
        "strict_name": strict_name,
        "saw_return_mode": saw_return_mode,
        "sample_count": len(samples),
        "last_samples": samples[-10:],
    }


def run(args) -> int:
    topics = osd_topics(args.drone_sn)
    client = MqttRequestClient(
        args.host,
        args.port,
        args.sn,
        qos=args.qos,
        quiet_stale=args.quiet_stale,
        client_id_prefix="edge_gateway_go_home_qland",
        observe_topics=topics,
    )
    if not client.connect(args.timeout):
        print("timeout waiting for MQTT connection", file=sys.stderr)
        return 1

    try:
        topic, initial_osd = wait_for_osd(client, topics, args.osd_wait)
        if initial_osd is None:
            print_step("返航前 OSD 观察", False, {"topics": topics, "reason": "未收到飞行器 OSD"})
            return 2
        print_step("返航前 OSD 观察", True, osd_snapshot(topic, initial_osd))

        if args.prime_mode:
            rc, reply = client.request(
                "set_flight_mode",
                {"flight_mode": args.prime_mode},
                args.timeout,
                channel="services",
                allowed_results={0},
            )
            print_step("预切飞行模式", rc == 0, {
                "requested": args.prime_mode,
                "result": result_code(reply),
                "data": reply.get("data", {}) if isinstance(reply, dict) else {},
            })
            if rc != 0:
                return rc
            time.sleep(args.step_delay)

        rc, reply = client.request(
            "go_home",
            {},
            args.timeout,
            channel="services",
            allowed_results={0},
        )
        print_step("下发返航 go_home", rc == 0, {
            "result": result_code(reply),
            "data": reply.get("data", {}) if isinstance(reply, dict) else {},
        })
        if rc != 0:
            return rc

        ok, detail = observe_qland(
            client,
            topics,
            timeout=args.observe_timeout,
            strict_name=args.strict_name,
        )
        print_step("返航后自动 QLAND 判定", ok, detail)
        return 0 if ok else 20
    finally:
        client.disconnect()


def main() -> int:
    parser = argparse.ArgumentParser(description="测试返航后是否自动触发 QLAND 模式")
    add_config_argument(parser)
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--sn", default=None, help="gateway SN")
    parser.add_argument("--drone-sn", default=DEFAULT_DRONE_SN)
    parser.add_argument("--timeout", type=float, default=12.0)
    parser.add_argument("--qos", type=int, choices=[0, 1, 2], default=1)
    parser.add_argument("--quiet-stale", action="store_true")
    parser.add_argument("--osd-wait", type=float, default=10.0)
    parser.add_argument("--observe-timeout", type=float, default=180.0)
    parser.add_argument("--step-delay", type=float, default=0.5)
    parser.add_argument(
        "--strict-name",
        action="store_true",
        help="只接受 OSD flight_mode/mode_name=QLAND；不把 mode_code=7 当作通过。",
    )
    parser.add_argument(
        "--prime-mode",
        default=None,
        help="返航前可选预切模式，例如 GUIDED、AUTO、QLOITER；默认不预切。",
    )
    args = parser.parse_args()
    args.host, args.port, args.sn = resolve_mqtt_settings(args)
    if args.prime_mode:
        args.prime_mode = args.prime_mode.strip().upper()

    print(
        "go_home QLAND test start "
        f"(host={args.host}, port={args.port}, sn={args.sn}, drone_sn={args.drone_sn}, "
        f"observe_timeout={args.observe_timeout}s)"
    )
    rc = run(args)
    if rc == 0:
        print("go_home QLAND test ok")
    else:
        print(f"go_home QLAND test failed (rc={rc})", file=sys.stderr)
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
