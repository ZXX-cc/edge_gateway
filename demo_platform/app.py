#!/usr/bin/env python3
"""Dock-style local demo platform for Edge Gateway full-chain testing."""

from __future__ import annotations

import json
import os
import queue
import threading
import time
import uuid
from collections import deque
from pathlib import Path
from typing import Any

from flask import Flask, Response, jsonify, request, send_from_directory
import paho.mqtt.client as mqtt


APP_DIR = Path(__file__).resolve().parent
STATIC_DIR = APP_DIR / "static"

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
    "started_at": int(time.time() * 1000),
}

pending: dict[str, dict[str, Any]] = {}
mqtt_client: mqtt.Client | None = None


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
    return run


@app.get("/")
def index() -> Response:
    return send_from_directory(STATIC_DIR, "index.html")


@app.get("/api/status")
def api_status():
    with state_lock:
        return jsonify(json_safe(state))


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
        return jsonify({"ok": False, "error": str(exc)}), 500


@app.post("/api/workflows/<workflow_id>/run")
def api_run_workflow(workflow_id: str):
    timeout = float((request.get_json(silent=True) or {}).get("timeout", 8.0))
    try:
        run = run_workflow(workflow_id, timeout=timeout)
        return jsonify({"ok": run["status"] == "ok", "run": run})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


if __name__ == "__main__":
    start_mqtt()
    print(f"Edge Gateway Demo Platform: http://{DEMO_HOST}:{DEMO_PORT}")
    print(f"MQTT: {MQTT_HOST}:{MQTT_PORT}, gateway={GATEWAY_SN}, drone={DRONE_SN}")
    app.run(host=DEMO_HOST, port=DEMO_PORT, threaded=True)
