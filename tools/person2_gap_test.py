#!/usr/bin/env python3
"""人员2设备侧缺口专项复测。

该脚本用于 demo 页面按钮触发，目标是把“MQTT 回包通过”和
“SITL/机巢/飞控侧确实有证据”分开记录。能自动判断的步骤会直接
返回非 0；需要外部环境的步骤会在 stdout 中写清楚缺少哪类证据。
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from mqtt_test_client import MqttRequestClient, output_status, result_code
from tool_config import add_config_argument, resolve_mqtt_settings


MODE_REQUESTS = [
    ("GUIDED", {"mode": "GUIDED"}),
    ("RTL", {"mode": "RTL"}),
    ("LAND", {"mode_code": 9}),
]


def _print_step(name: str, ok: bool, detail: dict[str, Any] | None = None) -> None:
    prefix = "PASS" if ok else "FAIL"
    print(f"[{prefix}] {name}")
    if detail:
        print(json.dumps(detail, ensure_ascii=False, indent=2))


def _reply_summary(reply: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(reply, dict):
        return {}
    return {
        "method": reply.get("method"),
        "result": result_code(reply),
        "output_status": output_status(reply),
        "data": reply.get("data", {}),
    }


def _connect(args, *, events: bool = False, observe_drone_osd: bool = False) -> MqttRequestClient | None:
    topics = []
    if observe_drone_osd:
        topics.append(f"thing/product/{args.drone_sn}/osd")
    client = MqttRequestClient(
        args.host,
        args.port,
        args.sn,
        qos=args.qos,
        quiet_stale=args.quiet_stale,
        client_id_prefix=f"edge_gateway_person2_{args.phase}",
        subscribe_events=events,
        observe_topics=topics,
    )
    if not client.connect(args.timeout):
        print("MQTT connect timeout", file=sys.stderr)
        return None
    return client


def _latest_observed(client: MqttRequestClient, topic: str) -> dict[str, Any]:
    messages = client.observed_by_topic.get(topic, [])
    return messages[-1] if messages else {}


def _field_value(payload: Any, key: str) -> Any:
    if not isinstance(payload, dict):
        return None
    data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    value = data.get(key) if isinstance(data, dict) else None
    if isinstance(value, dict) and "value" in value:
        return value.get("value")
    return value


def phase_flight_modes(args) -> int:
    client = _connect(args, observe_drone_osd=True)
    if client is None:
        return 2
    osd_topic = f"thing/product/{args.drone_sn}/osd"
    failures = 0
    try:
        first_count = client.observed_count(osd_topic)
        first_osd = client.wait_observed(osd_topic, first_count, args.osd_wait)
        if not first_osd:
            _print_step("SITL OSD 观察", False, {"reason": "未收到飞行器 OSD，不能证明真实 SITL 在线", "topic": osd_topic})
            failures += 1
        else:
            _print_step("SITL OSD 观察", True, {"mode_code": _field_value(first_osd, "mode_code"), "height": _field_value(first_osd, "height")})

        for label, payload in MODE_REQUESTS:
            rc, reply = client.request("set_flight_mode", payload, args.timeout, allowed_results={0})
            ok = rc == 0
            _print_step(f"飞行模式切换 {label}", ok, _reply_summary(reply))
            if not ok:
                failures += 1
            time.sleep(args.step_delay)

        latest = _latest_observed(client, osd_topic)
        if latest:
            _print_step("飞行模式 OSD 留证", True, {"mode_code": _field_value(latest, "mode_code"), "gear": _field_value(latest, "gear")})
        else:
            _print_step("飞行模式 OSD 留证", False, {"reason": "模式切换后没有可记录 OSD"})
            failures += 1
        return 0 if failures == 0 else 10
    finally:
        client.disconnect()


def phase_basic_flight(args) -> int:
    client = _connect(args, observe_drone_osd=True)
    if client is None:
        return 2
    osd_topic = f"thing/product/{args.drone_sn}/osd"
    steps = [
        ("切 GUIDED", "set_flight_mode", {"mode": "GUIDED"}),
        ("起飞", "take_off", {"height": args.takeoff_height}),
        ("返航", "go_home", {}),
        ("降落", "landing", {}),
    ]
    failures = 0
    try:
        before = client.observed_count(osd_topic)
        client.wait_observed(osd_topic, before, args.osd_wait)
        for label, method, payload in steps:
            rc, reply = client.request(method, payload, args.timeout, allowed_results={0})
            ok = rc == 0
            _print_step(label, ok, _reply_summary(reply))
            if not ok:
                failures += 1
            time.sleep(args.step_delay)

        after_osd = _latest_observed(client, osd_topic)
        if after_osd:
            _print_step("基础飞控 OSD 留证", True, {
                "height": _field_value(after_osd, "height"),
                "mode_code": _field_value(after_osd, "mode_code"),
                "latitude": _field_value(after_osd, "latitude"),
                "longitude": _field_value(after_osd, "longitude"),
            })
        else:
            _print_step("基础飞控 OSD 留证", False, {"reason": "没有飞行器 OSD，无法证明飞控真实动作"})
            failures += 1
        return 0 if failures == 0 else 11
    finally:
        client.disconnect()


def phase_drc_sitl(args) -> int:
    client = _connect(args, events=True, observe_drone_osd=True)
    if client is None:
        return 2
    failures = 0
    try:
        requests = [
            ("退出旧 DRC", "drc_mode_exit", {}, "services", {0, 319999}, None),
            ("进入 DRC", "drc_mode_enter", {"mqtt_broker": {"address": f"{args.host}:{args.port}", "client_id": f"drc-{args.sn}"}, "osd_frequency": 10, "hsi_frequency": 1}, "services", {0}, None),
            ("DRC 心跳", "heart_beat", {"seq": 1, "timestamp": int(time.time() * 1000)}, "drc", {0}, None),
            ("速度控制", "drone_control", {"seq": 2, "x": 0.2, "y": 0.0, "h": 0.0, "w": 0.0}, "drc", {0}, None),
            ("云台速度控制", "drc_gimbal_speed_push", {"pitch_speed": 0.0, "yaw_speed": 0.0, "roll_speed": 0.0}, "drc", {0}, None),
            ("起飞到点", "takeoff_to_point", {"flight_id": "person2-takeoff-point", "target_latitude": args.target_lat, "target_longitude": args.target_lon, "target_height": args.target_height, "security_takeoff_height": 20, "rth_altitude": 60, "max_speed": 8}, "services", {0}, "ok"),
            ("停止指点飞行", "fly_to_point_stop", {}, "services", {0}, "ok"),
            ("退出 DRC", "drc_mode_exit", {}, "services", {0}, None),
        ]
        for label, method, payload, channel, allowed, expected_status in requests:
            rc, reply = client.request(method, payload, args.timeout, channel=channel, allowed_results=allowed, expected_status=expected_status)
            ok = rc == 0
            _print_step(label, ok, _reply_summary(reply))
            if not ok:
                failures += 1
            time.sleep(args.step_delay)

        has_takeoff_event = client.has_event("takeoff_to_point_progress")
        has_flyto_event = client.has_event("fly_to_point_progress")
        _print_step("DRC 事件留证", has_takeoff_event or has_flyto_event, {
            "takeoff_to_point_progress": has_takeoff_event,
            "fly_to_point_progress": has_flyto_event,
            "events": client.stats["events"],
        })
        if not (has_takeoff_event or has_flyto_event):
            failures += 1
        return 0 if failures == 0 else 12
    finally:
        client.disconnect()


def phase_nest_gateway(args) -> int:
    client = _connect(args, events=True)
    if client is None:
        return 2
    failures = 0
    try:
        steps = [
            ("进入远程调试", "debug_mode_open", {}),
            ("打开舱盖", "cover_open", {}),
            ("关闭充电", "charge_close", {}),
            ("释放推杆/夹紧", "putter_open", {}),
            ("HMS 查询", "hms_get", {"page": 1, "page_size": 20}),
            ("收回推杆/夹紧", "putter_close", {}),
            ("关闭远程调试", "debug_mode_close", {}),
        ]
        for label, method, payload in steps:
            rc, reply = client.request(method, payload, args.timeout, allowed_results={0})
            ok = rc == 0
            _print_step(label, ok, _reply_summary(reply))
            if not ok:
                failures += 1
            time.sleep(args.step_delay)

        operation_events = client.events_by_method.get("dock_operation", [])
        hms_events = client.events_by_method.get("hms", [])
        has_evidence = bool(operation_events or hms_events)
        _print_step("机巢事件留证", has_evidence, {
            "dock_operation_events": len(operation_events),
            "hms_events": len(hms_events),
            "note": "没有事件时通常表示只跑了内存/模拟回包，缺少网关+机巢模拟器实跑证据",
        })
        if not has_evidence:
            failures += 1
        return 0 if failures == 0 else 13
    finally:
        client.disconnect()


def phase_non_blocking(args) -> int:
    client = _connect(args, observe_drone_osd=True)
    if client is None:
        return 2
    failures = 0
    latencies: list[float] = []
    try:
        osd_topic = f"thing/product/{args.drone_sn}/osd"
        osd_before = client.observed_count(osd_topic)
        for index in range(args.loop_count):
            started = time.monotonic()
            rc, reply = client.request("property_get", {"properties": ["mode_code", "drc_state", "cover_state"]}, args.timeout, allowed_results={0}, verbose=False)
            elapsed = time.monotonic() - started
            latencies.append(elapsed)
            ok = rc == 0 and elapsed <= args.max_latency
            _print_step(f"非阻塞探针 {index + 1}/{args.loop_count}", ok, {"elapsed_ms": int(elapsed * 1000), "reply": _reply_summary(reply)})
            if not ok:
                failures += 1
            time.sleep(args.step_delay)

        osd_after = client.wait_observed(osd_topic, osd_before, args.osd_wait)
        _print_step("压测期间 OSD 未断流", bool(osd_after), {"topic": osd_topic, "max_latency_ms": int(max(latencies or [0]) * 1000)})
        if not osd_after:
            failures += 1
        return 0 if failures == 0 else 14
    finally:
        client.disconnect()


def phase_payload(args) -> int:
    client = _connect(args, events=True)
    if client is None:
        return 2
    failures = 0
    try:
        steps = [
            ("相机能力查询", "camera_capability_query", {}),
            ("拍照", "camera_photo_take", {"camera_index": 0}),
            ("开始录像", "start_record", {"camera_index": 0, "resolution": "1080p", "framerate": 30}),
            ("停止录像", "stop_record", {"camera_index": 0}),
            ("云台状态查询", "gimbal_status_query", {}),
            ("云台转动", "gimbal_rotate", {"pitch": -10, "roll": 0, "yaw": 0, "duration": 1}),
        ]
        for label, method, payload in steps:
            rc, reply = client.request(method, payload, args.timeout, allowed_results={0})
            ok = rc == 0
            _print_step(label, ok, _reply_summary(reply))
            if not ok:
                failures += 1
            time.sleep(args.step_delay)
        return 0 if failures == 0 else 15
    finally:
        client.disconnect()


def phase_retention(args) -> int:
    client = _connect(args, events=True)
    if client is None:
        return 2
    failures = 0
    try:
        steps = [
            ("HMS 查询", "hms_get", {"page": 1, "page_size": 20}),
            ("日志列表", "device_log_list_get", {"page": 1, "page_size": 20}),
            ("日志上传", "device_log_upload", {"logs": [{"module": 0, "log_id": "person2-gap-log"}]}),
            ("当前任务进度", "flighttask_progress", {"flight_id": "person2-retention-check"}),
        ]
        for label, method, payload in steps:
            rc, reply = client.request(method, payload, args.timeout, allowed_results={0, 314004, 319999})
            ok = rc == 0
            _print_step(label, ok, _reply_summary(reply))
            if not ok:
                failures += 1
            time.sleep(args.step_delay)

        _print_step("操作/日志事件留证", client.stats["events"] > 0, {"events": client.stats["events"], "note": "若为 0，需要继续查 DB/operation record"})
        if client.stats["events"] == 0:
            failures += 1
        return 0 if failures == 0 else 16
    finally:
        client.disconnect()


PHASES = {
    "flight-modes": phase_flight_modes,
    "basic-flight": phase_basic_flight,
    "drc-sitl": phase_drc_sitl,
    "nest-gateway": phase_nest_gateway,
    "non-blocking": phase_non_blocking,
    "payload": phase_payload,
    "retention": phase_retention,
}


def main() -> int:
    parser = argparse.ArgumentParser(description="人员2 MAVLink/SITL/机巢缺口专项复测")
    add_config_argument(parser)
    parser.add_argument("--phase", choices=sorted(PHASES), required=True)
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--sn", default=None)
    parser.add_argument("--drone-sn", default="DRONESIM001")
    parser.add_argument("--timeout", type=float, default=12.0)
    parser.add_argument("--qos", type=int, choices=[0, 1, 2], default=1)
    parser.add_argument("--quiet-stale", action="store_true")
    parser.add_argument("--osd-wait", type=float, default=8.0)
    parser.add_argument("--step-delay", type=float, default=0.5)
    parser.add_argument("--loop-count", type=int, default=5)
    parser.add_argument("--max-latency", type=float, default=2.0)
    parser.add_argument("--takeoff-height", type=float, default=10.0)
    parser.add_argument("--target-lat", type=float, default=-35.3632622)
    parser.add_argument("--target-lon", type=float, default=149.1652375)
    parser.add_argument("--target-height", type=float, default=50.0)
    args = parser.parse_args()
    args.host, args.port, args.sn = resolve_mqtt_settings(args)

    print(
        "person2 gap test start "
        f"(phase={args.phase}, host={args.host}, port={args.port}, sn={args.sn}, drone_sn={args.drone_sn})"
    )
    rc = PHASES[args.phase](args)
    if rc == 0:
        print(f"person2 gap test ok (phase={args.phase})")
    else:
        print(f"person2 gap test failed (phase={args.phase}, rc={rc})", file=sys.stderr)
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
