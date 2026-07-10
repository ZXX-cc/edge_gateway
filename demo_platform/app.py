#!/usr/bin/env python3
"""Dock-style local demo platform for Edge Gateway full-chain testing."""

from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading
import time
import uuid
from collections import deque
from pathlib import Path
from typing import Any

from flask import Flask, Response, jsonify, request, send_from_directory
import paho.mqtt.client as mqtt


APP_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = APP_DIR.parent
STATIC_DIR = APP_DIR / "static"
LOG_DIR = APP_DIR / "test_logs"
RETEST_LOG_PATH = LOG_DIR / "retest_log.jsonl"

DEMO_HOST = os.environ.get("DEMO_HOST", "127.0.0.1")
DEMO_PORT = int(os.environ.get("DEMO_PORT", "8095"))
MQTT_HOST = os.environ.get("MQTT_HOST", "192.168.2.188")
MQTT_PORT = int(os.environ.get("MQTT_PORT", "1883"))
GATEWAY_SN = os.environ.get("GATEWAY_SN", "EDGEGW001")
DRONE_SN = os.environ.get("DRONE_SN", "DRONESIM001")
REAL_HD_WAYPOINTS_PATH = os.environ.get(
    "REAL_HD_WAYPOINTS_PATH",
    "/home/fenghuolun/lxz/edge_gateway/tools/ardupilot_sitl/HD.original.waypoints",
)

CHANNELS = {
    "services": ("thing/product/{sn}/services", "thing/product/{sn}/services_reply"),
    "property": ("thing/product/{sn}/property/set", "thing/product/{sn}/property/set_reply"),
    "drc": ("thing/product/{sn}/drc/down", "thing/product/{sn}/drc/up"),
}

OBSERVE_TOPICS = [
    f"thing/product/{GATEWAY_SN}/#",
    f"thing/product/{DRONE_SN}/#",
    f"sys/product/{GATEWAY_SN}/#",
]

app = Flask(__name__, static_folder=str(STATIC_DIR), static_url_path="/static")

state_lock = threading.RLock()
subscribers: set[queue.Queue[str]] = set()

KEY_POINT_DEFS = [
    ("mqtt_link", "MQTT 基础链路", "P0", "订阅/发布、topic 分类、服务回包"),
    ("apidock3_cmd", "APIDock3 命令下发", "P0", "services/property/drc 直接下发和回包"),
    ("sitl_osd", "SITL 飞行遥测", "P0", "飞行器电量、高度、经纬度、GPS、模式"),
    ("real_wayline", "真实航线执行", "P0", "QGC 可见真实航线、flighttask_prepare/execute/progress"),
    ("flight_control", "基础飞控控制", "P0", "GUIDED、起飞、返航、降落"),
    ("dock_v13", "机场机构联动", "P1", "舱盖、推杆、充电、HMS、dock_operation"),
    ("drc", "DRC 控制链路", "P1", "进入、心跳、摇杆/指点飞行、退出"),
    ("hms_log", "HMS/运维日志", "P1", "HMS 告警、日志列表和上传"),
    ("trace_nonblock", "Trace/非阻塞", "P2", "tid、异步回包、长任务不卡链路"),
]


def build_key_points() -> dict[str, dict[str, Any]]:
    # 页面需要先看到完整测试清单，再用 MQTT/OSD 命中的证据逐项点亮。
    return {
        point_id: {
            "id": point_id,
            "title": title,
            "stage": stage,
            "status": "waiting",
            "summary": description,
            "last_seen": None,
            "evidence": [],
        }
        for point_id, title, stage, description in KEY_POINT_DEFS
    }


state: dict[str, Any] = {
    "gateway_sn": GATEWAY_SN,
    "drone_sn": DRONE_SN,
    "mqtt": {"host": MQTT_HOST, "port": MQTT_PORT, "connected": False},
    "topic_counts": {},
    "latest": {
        "dock_osd": {},
        "drone_osd": {},
        "state": {},
        "event": {},
        "reply": {},
        "status": {},
    },
    "messages": deque(maxlen=260),
    "requests": deque(maxlen=160),
    "workflow_runs": deque(maxlen=40),
    "retest_logs": deque(maxlen=300),
    "retest_log_path": str(RETEST_LOG_PATH),
    "key_points": build_key_points(),
    "key_info": deque(maxlen=240),
    "started_at": int(time.time() * 1000),
}

pending: dict[str, dict[str, Any]] = {}
mqtt_client: mqtt.Client | None = None

MQTT_SCRIPT_ARGS = ["--host", MQTT_HOST, "--port", str(MQTT_PORT), "--sn", GATEWAY_SN]
PERSON2_SCRIPT_ARGS = [*MQTT_SCRIPT_ARGS, "--drone-sn", DRONE_SN, "--timeout", "12", "--quiet-stale"]

RETEST_ACTIONS: dict[str, dict[str, Any]] = {
    "real-hd-wayline": {
        "title": "SITL/QGC 真实 HD 航线",
        "category": "SITL",
        "command": [sys.executable, "tools/mqtt_dock3_wayline_test.py", *MQTT_SCRIPT_ARGS, "--timeout", "20", "--quiet-stale"],
        "timeout": 60,
        "note": "以真实 SITL/QGC 航线执行为准，重点留存 flighttask_progress 与飞行器 OSD 经纬度/高度变化。",
    },
    "flight-mode-sitl": {
        "title": "SITL 飞行模式真实复测",
        "category": "飞控",
        "command": [sys.executable, "tools/person2_gap_test.py", "--phase", "flight-modes", *PERSON2_SCRIPT_ARGS],
        "timeout": 90,
        "note": "该按钮要求能收到飞行器 OSD，并验证 set_flight_mode 回包；没有真实 SITL OSD 会判失败。",
    },
    "basic-flight-sitl": {
        "title": "SITL 基础飞控动作",
        "category": "飞控",
        "command": [sys.executable, "tools/person2_gap_test.py", "--phase", "basic-flight", *PERSON2_SCRIPT_ARGS],
        "timeout": 120,
        "note": "该按钮会下发 GUIDED、take_off、go_home、landing；仅在 SITL 环境使用。",
    },
    "drc-mavlink-sitl": {
        "title": "DRC 到 MAVLink 语义复测",
        "category": "DRC",
        "command": [sys.executable, "tools/person2_gap_test.py", "--phase", "drc-sitl", *PERSON2_SCRIPT_ARGS],
        "timeout": 120,
        "note": "该按钮检查 DRC 心跳、速度/云台控制、takeoff_to_point 和取消事件。",
    },
    "non-blocking": {
        "title": "P0/P1 全链路回归",
        "category": "稳定性",
        "command": [sys.executable, "tools/e2e_full_chain_test.py", *MQTT_SCRIPT_ARGS, "--timeout", "12", "--quiet-stale"],
        "timeout": 180,
    },
    "non-blocking-person2": {
        "title": "MAVLink/状态机非阻塞探针",
        "category": "稳定性",
        "command": [sys.executable, "tools/person2_gap_test.py", "--phase", "non-blocking", *PERSON2_SCRIPT_ARGS, "--loop-count", "8"],
        "timeout": 90,
        "note": "该按钮连续探测 property_get 延迟，并要求压测期间仍能收到飞行器 OSD。",
    },
    "cmd-advanced": {
        "title": "APIDock3/CMD 远程调试",
        "category": "APIDock3",
        "command": [sys.executable, "tools/mqtt_dock3_cmd_test.py", *MQTT_SCRIPT_ARGS, "--timeout", "20", "--quiet-stale"],
        "timeout": 80,
    },
    "retention-records": {
        "title": "操作记录和短周期数据保留",
        "category": "运维",
        "command": [sys.executable, "tools/person2_gap_test.py", "--phase", "retention", *PERSON2_SCRIPT_ARGS],
        "timeout": 90,
        "note": "该按钮检查 HMS/log/任务进度入口和事件留证；DB 深度保留仍需结合数据库记录确认。",
    },
    "nest-gateway-live": {
        "title": "机巢网关联动复测",
        "category": "机巢",
        "command": [sys.executable, "tools/person2_gap_test.py", "--phase", "nest-gateway", *PERSON2_SCRIPT_ARGS],
        "timeout": 120,
        "note": "该按钮下发机巢/CMD 动作并要求 dock_operation 或 HMS 事件；无机巢模拟器实跑会失败。",
    },
}


def now_ms() -> int:
    return int(time.time() * 1000)


def json_safe(value: Any) -> Any:
    if isinstance(value, deque):
        return list(value)
    if isinstance(value, dict):
        return {key: json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [json_safe(item) for item in value]
    return value


def compact_value(value: Any, limit: int = 96) -> str:
    if value is None:
        return "--"
    if isinstance(value, (dict, list)):
        text = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    else:
        text = str(value)
    return text if len(text) <= limit else f"{text[:limit - 1]}..."


def unwrap_value(value: Any) -> Any:
    if isinstance(value, dict) and "value" in value:
        return value.get("value")
    return value


def pick_field(payload: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        current: Any = payload
        for part in key.split("."):
            current = unwrap_value(current)
            if not isinstance(current, dict) or part not in current:
                current = None
                break
            current = current.get(part)
        if current is not None:
            return unwrap_value(current)
    return None


def short_topic(topic: str) -> str:
    return (
        topic
        .replace(f"thing/product/{GATEWAY_SN}/", "")
        .replace(f"thing/product/{DRONE_SN}/", f"{DRONE_SN}/")
        .replace(f"sys/product/{GATEWAY_SN}/", "sys/")
    )


def point_from_method(method: str, topic: str) -> str | None:
    # method/topic 是最稳定的业务信号，用它把刷屏日志归并成测试证据。
    name = method.lower()
    topic_l = topic.lower()
    if any(key in name for key in ("flighttask", "wayline", "breakpoint", "return_home")):
        return "real_wayline"
    if any(key in name for key in ("drc", "heart_beat", "drone_control", "fly_to_point", "takeoff_to_point")) or "/drc/" in topic_l:
        return "drc"
    if any(key in name for key in ("cover", "charge", "putter", "dock", "debug_mode", "silent_mode")):
        return "dock_v13"
    if any(key in name for key in ("hms", "log", "alarm")):
        return "hms_log"
    if any(key in name for key in ("set_flight_mode", "take_off", "go_home", "landing", "mode")):
        return "flight_control"
    if name:
        return "apidock3_cmd"
    return None


def record_key_point(point_id: str, status: str, summary: str, source: str, detail: str, timestamp: int, min_interval_ms: int = 0) -> bool:
    # 高频 OSD 只更新卡片，按间隔写证据，避免关键信息列表再次被刷屏。
    point = state["key_points"].get(point_id)
    if not point:
        return False
    previous_seen = point.get("last_seen")
    previous_status = point.get("status")
    should_append = (
        min_interval_ms <= 0
        or not previous_seen
        or timestamp - int(previous_seen) >= min_interval_ms
        or previous_status != status
    )
    point["status"] = status
    point["summary"] = summary
    point["last_seen"] = timestamp
    evidence = {"timestamp": timestamp, "source": source, "detail": detail}
    if should_append:
        point["evidence"] = [evidence, *point.get("evidence", [])[:5]]
        state["key_info"].appendleft({
            "point_id": point_id,
            "title": point["title"],
            "stage": point["stage"],
            "status": status,
            "summary": summary,
            "source": source,
            "detail": detail,
            "timestamp": timestamp,
        })
    return True


def extract_key_info(item: dict[str, Any]) -> bool:
    # 从 MQTT 原始 payload 提取联调人员真正要盯的字段。
    payload = item.get("payload")
    if not isinstance(payload, dict):
        return False
    topic = str(item.get("topic", ""))
    kind = str(item.get("type", "other"))
    timestamp = int(item.get("timestamp") or now_ms())
    source = short_topic(topic)
    updated = False

    if kind == "osd":
        battery = pick_field(payload, "battery.capacity_percent", "battery.remain_percent", "capacity_percent", "battery_percent")
        gps = pick_field(payload, "position_state.gps_number", "gps_number", "satellites_visible")
        height = pick_field(payload, "height", "altitude", "relative_altitude")
        latitude = pick_field(payload, "latitude", "lat", "position.latitude")
        longitude = pick_field(payload, "longitude", "lng", "lon", "position.longitude")
        mode = pick_field(payload, "mode_code", "flight_mode")
        gear = pick_field(payload, "gear")
        summary = f"mode={compact_value(mode)}, 高度={compact_value(height)}m, 电量={compact_value(battery)}%, GPS={compact_value(gps)}"
        detail = f"gear={compact_value(gear)}, lat={compact_value(latitude, 32)}, lon={compact_value(longitude, 32)}"
        updated |= record_key_point("sitl_osd", "ok", summary, source, detail, timestamp, min_interval_ms=5000)
        if mode is not None or gear is not None:
            updated |= record_key_point("flight_control", "active", summary, source, "OSD 携带 mode/gear，可核对飞控状态", timestamp, min_interval_ms=15000)
        return updated

    method = str(payload.get("method") or "")
    tid = payload.get("tid") or payload.get("bid") or "--"
    data = payload.get("data")
    result = data.get("result") if isinstance(data, dict) else None

    if kind == "state":
        fields = ", ".join(f"{key}={compact_value(value, 48)}" for key, value in list(payload.items())[:6])
        updated |= record_key_point("apidock3_cmd", "active", "State 属性上报已收到", source, fields, timestamp)
        if any(key in payload for key in ("cover_state", "putter_state", "drone_charge_state", "emergency_stop_state")):
            updated |= record_key_point("dock_v13", "active", "State 命中机巢状态字段", source, fields, timestamp)
        if any(key in payload for key in ("drc_state", "drc_mode_enter_time")):
            updated |= record_key_point("drc", "active", "State 命中 DRC 状态字段", source, fields, timestamp)
        return updated

    if kind in {"services", "property", "drc", "services_reply", "property_reply", "drc_reply", "request_reply", "event", "status", "status_reply"}:
        if kind.endswith("reply") or kind in {"services", "property", "drc"}:
            updated |= record_key_point("mqtt_link", "ok", f"{kind} 收到 {method or '未命名方法'}", source, f"tid={compact_value(tid)}", timestamp)
            updated |= record_key_point("apidock3_cmd", "ok" if kind.endswith("reply") else "active", f"APIDock3 {method or kind} 已下发/回包", source, f"tid={compact_value(tid)}, result={compact_value(result)}", timestamp)
            updated |= record_key_point("trace_nonblock", "active", "tid/异步回包可追踪", source, f"method={method or '--'}, tid={compact_value(tid)}", timestamp)
        point_id = point_from_method(method, topic)
        if kind in {"status", "status_reply"} and not point_id:
            point_id = "apidock3_cmd"
        if kind == "event" and not point_id:
            event_text = json.dumps(payload, ensure_ascii=False)
            if "hms" in event_text.lower():
                point_id = "hms_log"
            elif "dock_operation" in event_text.lower():
                point_id = "dock_v13"
        if point_id:
            status = "ok" if kind.endswith("reply") and result in {0, "0", True, "ok", "success"} else "active"
            updated |= record_key_point(
                point_id,
                status,
                f"{method or kind} 已捕获",
                source,
                f"tid={compact_value(tid)}, result={compact_value(result)}, data={compact_value(data, 140)}",
                timestamp,
            )
    return updated


def load_retest_logs() -> None:
    if not RETEST_LOG_PATH.exists():
        return
    try:
        rows = RETEST_LOG_PATH.read_text(encoding="utf-8").splitlines()[-300:]
    except Exception:
        return
    with state_lock:
        state["retest_logs"].clear()
        for row in rows:
            try:
                state["retest_logs"].appendleft(json.loads(row))
            except Exception:
                continue


def append_retest_log(entry: dict[str, Any]) -> dict[str, Any]:
    item = {
        "id": entry.get("id") or str(uuid.uuid4()),
        "item_id": entry.get("item_id", ""),
        "timestamp": entry.get("timestamp") or now_ms(),
        "source": entry.get("source", "manual"),
        "category": entry.get("category", ""),
        "title": entry.get("title", ""),
        "status": entry.get("status", "needs_data"),
        "note": entry.get("note", ""),
        "method": entry.get("method", ""),
        "evidence": entry.get("evidence", {}),
    }
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    with RETEST_LOG_PATH.open("a", encoding="utf-8") as file:
        file.write(json.dumps(item, ensure_ascii=False) + "\n")
    with state_lock:
        state["retest_logs"].appendleft(item)
    broadcast("retest_log", item)
    return item


def run_retest_action(item_id: str) -> dict[str, Any]:
    action = RETEST_ACTIONS.get(item_id)
    if not action:
        raise ValueError(f"unknown retest item: {item_id}")

    if "manual_note" in action:
        return append_retest_log({
            "id": item_id,
            "item_id": item_id,
            "source": "retest_button",
            "category": action["category"],
            "title": action["title"],
            "status": "needs_data",
            "note": action["manual_note"],
            "method": "manual evidence required",
        })

    command = action["command"]
    started_at = now_ms()
    try:
        completed = subprocess.run(
            command,
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            timeout=float(action.get("timeout", 60)),
            check=False,
        )
        elapsed_ms = now_ms() - started_at
        status = "pass" if completed.returncode == 0 else "fail"
        note_parts = [f"returncode={completed.returncode}", f"elapsed_ms={elapsed_ms}"]
        if action.get("note"):
            note_parts.append(action["note"])
        return append_retest_log({
            "id": item_id,
            "item_id": item_id,
            "source": "retest_button",
            "category": action["category"],
            "title": action["title"],
            "status": status,
            "note": "；".join(note_parts),
            "method": " ".join(command),
            "evidence": {
                "returncode": completed.returncode,
                "elapsed_ms": elapsed_ms,
                "stdout": completed.stdout[-12000:],
                "stderr": completed.stderr[-12000:],
            },
        })
    except subprocess.TimeoutExpired as exc:
        return append_retest_log({
            "id": item_id,
            "item_id": item_id,
            "source": "retest_button",
            "category": action["category"],
            "title": action["title"],
            "status": "fail",
            "note": f"timeout after {action.get('timeout', 60)}s",
            "method": " ".join(command),
            "evidence": {
                "stdout": (exc.stdout or "")[-12000:],
                "stderr": (exc.stderr or "")[-12000:],
            },
        })


def broadcast(event: str, payload: Any) -> None:
    text = json.dumps({"event": event, "payload": json_safe(payload), "timestamp": now_ms()}, ensure_ascii=False)
    dead: list[queue.Queue[str]] = []
    for item in list(subscribers):
        try:
            item.put_nowait(text)
        except Exception:
            dead.append(item)
    for item in dead:
        subscribers.discard(item)


def classify_topic(topic: str) -> str:
    if topic.endswith("/osd"):
        return "osd"
    if topic.endswith("/state"):
        return "state"
    if topic.endswith("/events"):
        return "event"
    if topic.endswith("/services"):
        return "services"
    if topic.endswith("/services_reply"):
        return "services_reply"
    if topic.endswith("/property/set"):
        return "property"
    if topic.endswith("/property/set_reply"):
        return "property_reply"
    if topic.endswith("/drc/down"):
        return "drc"
    if topic.endswith("/drc/up"):
        return "drc_reply"
    if topic.endswith("/status"):
        return "status"
    if topic.endswith("/status_reply"):
        return "status_reply"
    if topic.endswith("/requests"):
        return "request"
    if topic.endswith("/requests_reply"):
        return "request_reply"
    return "other"


def build_client() -> mqtt.Client:
    client_id = f"edge_gateway_demo_{uuid.uuid4().hex[:8]}"
    if hasattr(mqtt, "CallbackAPIVersion"):
        try:
            return mqtt.Client(mqtt.CallbackAPIVersion.VERSION1, client_id=client_id)
        except TypeError:
            pass
    return mqtt.Client(client_id=client_id)


def on_connect(client, userdata, flags, rc):
    connected = rc == 0
    key_payload = None
    with state_lock:
        state["mqtt"].update({"connected": connected, "rc": rc, "updated_at": now_ms()})
        record_key_point(
            "mqtt_link",
            "ok" if connected else "fail",
            "MQTT 已连接并订阅观测 topic" if connected else f"MQTT 连接失败 rc={rc}",
            f"{MQTT_HOST}:{MQTT_PORT}",
            "订阅范围：" + ", ".join(OBSERVE_TOPICS),
            state["mqtt"]["updated_at"],
        )
        key_payload = {"key_points": state["key_points"], "key_info": state["key_info"]}
    if connected:
        for topic in OBSERVE_TOPICS:
            client.subscribe(topic, qos=1)
    broadcast("mqtt_status", state["mqtt"])
    broadcast("key_update", key_payload)


def on_disconnect(client, userdata, rc):
    key_payload = None
    with state_lock:
        state["mqtt"].update({"connected": False, "rc": rc, "updated_at": now_ms()})
        record_key_point(
            "mqtt_link",
            "warn",
            f"MQTT 已断开 rc={rc}",
            f"{MQTT_HOST}:{MQTT_PORT}",
            "等待重连或检查 broker",
            state["mqtt"]["updated_at"],
        )
        key_payload = {"key_points": state["key_points"], "key_info": state["key_info"]}
    broadcast("mqtt_status", state["mqtt"])
    broadcast("key_update", key_payload)


def on_message(client, userdata, msg):
    topic = msg.topic
    try:
        payload = json.loads(msg.payload.decode("utf-8"))
    except Exception:
        payload = {"raw": msg.payload.decode("utf-8", errors="replace")}

    kind = classify_topic(topic)
    item = {"topic": topic, "type": kind, "payload": payload, "timestamp": now_ms()}
    tid = payload.get("tid") if isinstance(payload, dict) else None

    key_payload = None
    with state_lock:
        counts = state["topic_counts"]
        counts[kind] = counts.get(kind, 0) + 1
        state["messages"].appendleft(item)

        if kind == "osd":
            if f"/{DRONE_SN}/" in topic:
                state["latest"]["drone_osd"] = payload
            else:
                state["latest"]["dock_osd"] = payload
        elif kind == "state":
            state["latest"]["state"] = payload
        elif kind == "event":
            state["latest"]["event"] = payload
        elif kind in {"services_reply", "property_reply", "drc_reply", "request_reply"}:
            state["latest"]["reply"] = payload
        elif kind in {"status", "status_reply"}:
            state["latest"]["status"] = payload

        if extract_key_info(item):
            key_payload = {"key_points": state["key_points"], "key_info": state["key_info"]}

    waiter = pending.get(tid)
    if waiter and kind == waiter.get("reply_kind"):
        waiter["reply"] = payload
        waiter["topic"] = topic
        waiter["event"].set()

    broadcast("mqtt_message", item)
    if key_payload is not None:
        broadcast("key_update", key_payload)


def start_mqtt() -> None:
    global mqtt_client
    mqtt_client = build_client()
    mqtt_client.on_connect = on_connect
    mqtt_client.on_disconnect = on_disconnect
    mqtt_client.on_message = on_message
    mqtt_client.connect_async(MQTT_HOST, MQTT_PORT, keepalive=30)
    mqtt_client.loop_start()


def build_message(method: str, data: Any, need_reply: int = 1, tid: str | None = None) -> dict[str, Any]:
    message = {
        "tid": tid or str(uuid.uuid4()),
        "bid": str(uuid.uuid4()),
        "timestamp": now_ms(),
        "gateway": GATEWAY_SN,
        "method": method,
        "data": data if data is not None else {},
    }
    if need_reply:
        message["need_reply"] = 1
    return message


def publish_command(method: str, data: Any, channel: str, timeout: float = 8.0, need_reply: int = 1) -> dict[str, Any]:
    if mqtt_client is None:
        raise RuntimeError("MQTT client is not initialized")
    if channel not in CHANNELS:
        raise ValueError(f"unsupported channel: {channel}")

    publish_tpl, reply_tpl = CHANNELS[channel]
    topic = publish_tpl.format(sn=GATEWAY_SN)
    reply_topic = reply_tpl.format(sn=GATEWAY_SN)
    reply_kind = {
        "services": "services_reply",
        "property": "property_reply",
        "drc": "drc_reply",
    }[channel]
    message = build_message(method, data, need_reply=need_reply)
    waiter = {"event": threading.Event(), "reply": None, "topic": "", "reply_kind": reply_kind}
    if need_reply:
        pending[message["tid"]] = waiter

    result = mqtt_client.publish(topic, json.dumps(message, ensure_ascii=False), qos=1)
    record = {
        "tid": message["tid"],
        "method": method,
        "channel": channel,
        "topic": topic,
        "reply_topic": reply_topic,
        "request": message,
        "publish_rc": result.rc,
        "timestamp": now_ms(),
        "status": "published",
    }

    if need_reply:
        if waiter["event"].wait(timeout):
            record["status"] = "replied"
            record["reply"] = waiter["reply"]
            record["reply_observed_topic"] = waiter["topic"]
        else:
            record["status"] = "timeout"
        pending.pop(message["tid"], None)
    else:
        record["status"] = "sent_no_reply"

    with state_lock:
        state["requests"].appendleft(record)
    broadcast("request", record)
    append_retest_log({
        "source": "command",
        "category": "页面命令",
        "title": method,
        "status": "pass" if record["status"] == "replied" else "fail",
        "method": method,
        "note": f"channel={channel}, request_status={record['status']}",
        "evidence": {
            "tid": record["tid"],
            "reply_topic": record.get("reply_observed_topic", ""),
            "result": (record.get("reply") or {}).get("data", {}).get("result"),
        },
    })
    return record


def workflow_steps() -> dict[str, list[dict[str, Any]]]:
    real_hd_wayline_file = {
        "path": REAL_HD_WAYPOINTS_PATH,
        "fingerprint": "hd-original-qgc-wpl",
        "name": "HD.original.waypoints",
    }
    return {
        "startup_check": [
            {"label": "读取核心属性", "method": "property_get", "data": {"properties": ["mode_code", "drc_state", "cover_state", "putter_state", "charging_state", "drone_in_dock"]}},
            {"label": "查询机巢状态", "method": "dock_status_query", "data": {}},
            {"label": "查询绑定状态", "method": "airport_bind_status", "data": {"devices": [{"sn": GATEWAY_SN}]}},
        ],
        "dock_cmd": [
            {"label": "开启远程调试", "method": "debug_mode_open", "data": {}},
            {"label": "开舱盖", "method": "cover_open", "data": {}},
            {"label": "断开充电", "method": "charge_close", "data": {}},
            {"label": "释放夹紧/推出", "method": "putter_open", "data": {}},
            {"label": "补光灯开", "method": "supplement_light_open", "data": {}},
            {"label": "补光灯关", "method": "supplement_light_close", "data": {}},
            {"label": "收回/夹紧", "method": "putter_close", "data": {}},
            {"label": "关闭远程调试", "method": "debug_mode_close", "data": {}},
        ],
        "drc_link": [
            {"label": "进入 DRC", "method": "drc_mode_enter", "data": {"mqtt_broker": {"address": MQTT_HOST, "port": MQTT_PORT}, "osd_frequency": 10, "hsi_frequency": 1}},
            {"label": "DRC 心跳", "method": "heart_beat", "channel": "drc", "data": {"timestamp": now_ms(), "seq": 1}},
            {"label": "速度控制", "method": "drc_speed_push", "channel": "drc", "data": {"vx": 0.5, "vy": 0, "vz": 0, "yaw_rate": 0}},
            {"label": "退出 DRC", "method": "drc_mode_exit", "data": {"restore_mission": False}},
        ],
        "real_hd_wayline": [
            {"label": "准备真实 HD 航线", "method": "flighttask_prepare", "data": {"flight_id": "demo-flight-001", "execute_time": now_ms(), "task_type": 0, "file": real_hd_wayline_file, "simulate_mission": {"is_enable": 0, "latitude": 44.0474824, "longitude": 125.3614503}}},
            {"label": "执行真实 HD 航线", "method": "flighttask_execute", "data": {"flight_id": "demo-flight-001"}},
            {"label": "查询真实任务进度", "method": "flighttask_progress", "data": {"flight_id": "demo-flight-001"}},
        ],
    }


def run_workflow(workflow_id: str, timeout: float = 8.0) -> dict[str, Any]:
    steps_by_id = workflow_steps()
    if workflow_id not in steps_by_id:
        raise ValueError(f"unknown workflow: {workflow_id}")

    run = {
        "id": str(uuid.uuid4()),
        "workflow": workflow_id,
        "status": "running",
        "started_at": now_ms(),
        "steps": [],
    }
    with state_lock:
        state["workflow_runs"].appendleft(run)
    broadcast("workflow", run)

    for step in steps_by_id[workflow_id]:
        channel = step.get("channel", "services")
        result = publish_command(step["method"], step.get("data", {}), channel, timeout=timeout)
        result["label"] = step.get("label", step["method"])
        run["steps"].append(result)
        broadcast("workflow", run)
        if result["status"] == "timeout":
            run["status"] = "timeout"
            break
    else:
        run["status"] = "ok"

    run["finished_at"] = now_ms()
    broadcast("workflow", run)
    append_retest_log({
        "source": "workflow",
        "category": "页面流程",
        "title": workflow_id,
        "status": "pass" if run["status"] == "ok" else "fail",
        "note": f"workflow_status={run['status']}, steps={len(run['steps'])}",
        "evidence": {
            "workflow": workflow_id,
            "run_id": run["id"],
            "failed_step": next((step.get("label") or step.get("method") for step in run["steps"] if step.get("status") != "replied"), ""),
        },
    })
    return run


@app.get("/")
def index() -> Response:
    return send_from_directory(STATIC_DIR, "index.html")


@app.get("/api/status")
def api_status():
    with state_lock:
        return jsonify(json_safe(state))


@app.get("/api/retest-log")
def api_retest_log():
    with state_lock:
        return jsonify({"logs": json_safe(state["retest_logs"]), "path": str(RETEST_LOG_PATH)})


@app.post("/api/retest-log")
def api_save_retest_log():
    body = request.get_json(force=True, silent=True) or {}
    try:
        return jsonify({"ok": True, "log": append_retest_log(body), "path": str(RETEST_LOG_PATH)})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@app.post("/api/retest/<item_id>/run")
def api_run_retest(item_id: str):
    try:
        return jsonify({"ok": True, "log": run_retest_action(item_id), "path": str(RETEST_LOG_PATH)})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@app.get("/api/workflows")
def api_workflows():
    meta = {
        "startup_check": "基础上线巡检",
        "dock_cmd": "APIDock3/CMD 动作",
        "drc_link": "DRC 链路",
        "real_hd_wayline": "真实 HD 航线",
    }
    return jsonify({"workflows": [{"id": key, "name": name, "steps": workflow_steps()[key]} for key, name in meta.items()]})


@app.get("/api/stream")
def api_stream() -> Response:
    q: queue.Queue[str] = queue.Queue(maxsize=500)
    subscribers.add(q)

    def generate():
        try:
            with state_lock:
                initial = json.dumps({"event": "snapshot", "payload": json_safe(state), "timestamp": now_ms()}, ensure_ascii=False)
            yield f"data: {initial}\n\n"
            while True:
                try:
                    yield f"data: {q.get(timeout=15)}\n\n"
                except queue.Empty:
                    yield ": ping\n\n"
        finally:
            subscribers.discard(q)

    return Response(generate(), mimetype="text/event-stream")


@app.post("/api/command")
def api_command():
    body = request.get_json(force=True, silent=True) or {}
    method = str(body.get("method", "")).strip()
    if not method:
        return jsonify({"ok": False, "error": "missing method"}), 400
    channel = str(body.get("channel", "services")).strip()
    try:
        record = publish_command(method, body.get("data", {}), channel, timeout=float(body.get("timeout", 8.0)))
        return jsonify({"ok": record["status"] == "replied", "record": record})
    except Exception as exc:
        append_retest_log({
            "source": "command",
            "category": "页面命令",
            "title": method,
            "status": "fail",
            "method": method,
            "note": f"channel={channel}, error={exc}",
        })
        return jsonify({"ok": False, "error": str(exc)}), 500


@app.post("/api/workflows/<workflow_id>/run")
def api_run_workflow(workflow_id: str):
    timeout = float((request.get_json(silent=True) or {}).get("timeout", 8.0))
    try:
        run = run_workflow(workflow_id, timeout=timeout)
        return jsonify({"ok": run["status"] == "ok", "run": run})
    except Exception as exc:
        append_retest_log({
            "source": "workflow",
            "category": "页面流程",
            "title": workflow_id,
            "status": "fail",
            "note": str(exc),
        })
        return jsonify({"ok": False, "error": str(exc)}), 500


if __name__ == "__main__":
    load_retest_logs()
    start_mqtt()
    print(f"Edge Gateway Demo Platform: http://{DEMO_HOST}:{DEMO_PORT}")
    print(f"MQTT: {MQTT_HOST}:{MQTT_PORT}, gateway={GATEWAY_SN}, drone={DRONE_SN}")
    app.run(host=DEMO_HOST, port=DEMO_PORT, threaded=True)
