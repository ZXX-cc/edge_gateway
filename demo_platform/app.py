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
    "started_at": int(time.time() * 1000),
}

pending: dict[str, dict[str, Any]] = {}
mqtt_client: mqtt.Client | None = None

MQTT_SCRIPT_ARGS = ["--host", MQTT_HOST, "--port", str(MQTT_PORT), "--sn", GATEWAY_SN]
PERSON2_SCRIPT_ARGS = [*MQTT_SCRIPT_ARGS, "--drone-sn", DRONE_SN, "--timeout", "12", "--quiet-stale"]

RETEST_ACTIONS: dict[str, dict[str, Any]] = {
    "one-key-mqtt": {
        "title": "一键启航 MQTT 实跑",
        "category": "一键启航",
        "command": [sys.executable, "tools/mqtt_one_key_takeoff_flow_test.py", *MQTT_SCRIPT_ARGS, "--simulate", "--timeout", "60", "--quiet-stale"],
        "timeout": 90,
    },
    "sitl-wayline": {
        "title": "Wayline/SITL 航线复测入口",
        "category": "SITL",
        "command": [sys.executable, "tools/mqtt_dock3_wayline_test.py", *MQTT_SCRIPT_ARGS, "--timeout", "20", "--quiet-stale"],
        "timeout": 60,
        "note": "该按钮跑 Wayline MQTT 专项；若环境仍是 simulate_mission，需要另补真实 SITL 航点日志。",
    },
    "nest-v13-live": {
        "title": "机巢 v1.3 协议复测",
        "category": "机巢",
        "command": [sys.executable, "tools/nest_v13_protocol_test.py", "--verbose"],
        "timeout": 30,
        "note": "该按钮跑离线协议专项；真实网关 + 机巢模拟器联调日志仍需另存。",
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
    "payload-camera-gimbal": {
        "title": "相机/云台基础控制",
        "category": "负载",
        "command": [sys.executable, "tools/person2_gap_test.py", "--phase", "payload", *PERSON2_SCRIPT_ARGS],
        "timeout": 90,
        "note": "该按钮验证相机/云台基础 method 回包；无真实负载时只能证明 adapter/mock 入口可用。",
    },
    "multi-device-offline": {
        "title": "设备/组织基础复测",
        "category": "设备",
        "command": [sys.executable, "tools/mqtt_dock3_device_test.py", *MQTT_SCRIPT_ARGS, "--timeout", "20", "--quiet-stale"],
        "timeout": 80,
        "note": "该按钮先跑 device/topology 专项；多子设备、解绑、离线、重连仍需扩展脚本或真实日志。",
    },
    "cmd-advanced": {
        "title": "CMD 远程调试复测",
        "category": "CMD",
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
    "real-adapters": {
        "title": "真实 OTA/OSS/直播 Adapter",
        "category": "真实 Adapter",
        "manual_note": "真实 OTA、OSS、直播推流依赖外部服务和凭证；页面先记录待测，不能用本地 mock 判定通过。",
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
    if topic.endswith("/services_reply"):
        return "services_reply"
    if topic.endswith("/property/set_reply"):
        return "property_reply"
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
    with state_lock:
        state["mqtt"].update({"connected": connected, "rc": rc, "updated_at": now_ms()})
    if connected:
        for topic in OBSERVE_TOPICS:
            client.subscribe(topic, qos=1)
    broadcast("mqtt_status", state["mqtt"])


def on_disconnect(client, userdata, rc):
    with state_lock:
        state["mqtt"].update({"connected": False, "rc": rc, "updated_at": now_ms()})
    broadcast("mqtt_status", state["mqtt"])


def on_message(client, userdata, msg):
    topic = msg.topic
    try:
        payload = json.loads(msg.payload.decode("utf-8"))
    except Exception:
        payload = {"raw": msg.payload.decode("utf-8", errors="replace")}

    kind = classify_topic(topic)
    item = {"topic": topic, "type": kind, "payload": payload, "timestamp": now_ms()}
    tid = payload.get("tid") if isinstance(payload, dict) else None

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

    waiter = pending.get(tid)
    if waiter and kind == waiter.get("reply_kind"):
        waiter["reply"] = payload
        waiter["topic"] = topic
        waiter["event"].set()

    broadcast("mqtt_message", item)


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
    wayline_file = {
        "url": "",
        "fingerprint": "demo",
        "name": "demo.wpml",
        "waypoints": [
            {"latitude": 39.9042, "longitude": 116.3970, "altitude": 50.0},
            {"latitude": 39.9045, "longitude": 116.3974, "altitude": 55.0},
        ],
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
        "wayline_demo": [
            {"label": "任务准备", "method": "flighttask_prepare", "data": {"flight_id": "demo-flight-001", "execute_time": now_ms(), "task_type": 0, "file": wayline_file, "simulate_mission": {"is_enable": 1, "latitude": 39.9042, "longitude": 116.3970}}},
            {"label": "任务执行", "method": "flighttask_execute", "data": {"flight_id": "demo-flight-001"}},
            {"label": "进度查询", "method": "flighttask_progress", "data": {"flight_id": "demo-flight-001"}},
            {"label": "暂停", "method": "flighttask_pause", "data": {}},
            {"label": "恢复", "method": "flighttask_resume", "data": {}},
            {"label": "返航", "method": "flighttask_return", "data": {"flight_id": "demo-flight-001", "return_type": 0}},
            {"label": "取消任务", "method": "flighttask_undo", "data": {"flight_ids": ["demo-flight-001"]}},
        ],
        "takeoff_flow_demo": [
            {"label": "断开充电", "method": "charge_close", "data": {}},
            {"label": "开舱盖", "method": "cover_open", "data": {}},
            {"label": "释放夹紧/推出", "method": "putter_open", "data": {}},
            {"label": "准备航线任务", "method": "flighttask_prepare", "data": {"flight_id": "demo-takeoff-001", "execute_time": now_ms(), "task_type": 0, "file": wayline_file, "simulate_mission": {"is_enable": 1, "latitude": 39.9042, "longitude": 116.3970}}},
            {"label": "执行航线任务", "method": "flighttask_execute", "data": {"flight_id": "demo-takeoff-001"}},
            {"label": "查询进度", "method": "flighttask_progress", "data": {"flight_id": "demo-takeoff-001"}},
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
        "dock_cmd": "机巢/CMD 动作",
        "drc_link": "DRC 链路",
        "wayline_demo": "Wayline 模拟任务",
        "takeoff_flow_demo": "一键启航演示",
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
