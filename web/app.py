#!/usr/bin/env python3
"""Standalone web console for edge_gateway MQTT and ArduPilot SITL."""

from __future__ import annotations

import json
import os
import queue
import signal
import subprocess
import threading
import time
import uuid
from collections import deque
from pathlib import Path
from typing import Any

from flask import Flask, Response, jsonify, request, send_from_directory
import paho.mqtt.client as mqtt
from pymavlink import mavutil


APP_DIR = Path(__file__).resolve().parent
STATIC_DIR = APP_DIR / "static"

WEB_HOST = os.environ.get("WEB_HOST", "0.0.0.0")
WEB_PORT = int(os.environ.get("WEB_PORT", "8090"))
MQTT_HOST = os.environ.get("MQTT_HOST", "127.0.0.1")
MQTT_PORT = int(os.environ.get("MQTT_PORT", "1883"))
GATEWAY_SN = os.environ.get("GATEWAY_SN", "EDGE-GW-001")
MAVLINK_URL = os.environ.get("MAVLINK_URL", "udpout:127.0.0.1:14550")
MAVLINK_TARGET_SYSTEM = int(os.environ.get("MAVLINK_TARGET_SYSTEM", "1"))
MAVLINK_TARGET_COMPONENT = int(os.environ.get("MAVLINK_TARGET_COMPONENT", "1"))
ARDUPILOT_DIR = Path(os.path.expanduser(os.environ.get("ARDUPILOT_DIR", "~/lxz/ardupilot")))
SITL_PYTHON = os.path.expanduser(os.environ.get("SITL_PYTHON", "/usr/bin/python3"))

TOPICS = [
    f"thing/product/{GATEWAY_SN}/#",
    f"sys/product/{GATEWAY_SN}/#",
]

COPTER_MODES = {
    "STABILIZE": 0,
    "ACRO": 1,
    "ALT_HOLD": 2,
    "AUTO": 3,
    "GUIDED": 4,
    "LOITER": 5,
    "RTL": 6,
    "CIRCLE": 7,
    "LAND": 9,
    "DRIFT": 11,
    "SPORT": 13,
    "FLIP": 14,
    "AUTOTUNE": 15,
    "POSHOLD": 16,
    "BRAKE": 17,
    "THROW": 18,
    "AVOID_ADSB": 19,
    "GUIDED_NOGPS": 20,
    "SMART_RTL": 21,
    "FLOWHOLD": 22,
    "FOLLOW": 23,
    "ZIGZAG": 24,
    "SYSTEMID": 25,
    "AUTOROTATE": 26,
    "AUTO_RTL": 27,
}

app = Flask(__name__, static_folder=str(STATIC_DIR), static_url_path="/static")

state_lock = threading.RLock()
subscribers: set[queue.Queue[str]] = set()

TEST_POINT_DEFS = [
    ("mqtt_link", "MQTT 基础链路", "P0", "订阅/发布、topic 分类、服务回包是否通"),
    ("osd_state", "OSD/State 上报", "P0", "遥测、属性状态、时间戳、电量/GPS/位置"),
    ("flight_mode", "飞行模式映射", "P0", "mode_code、flight_mode、custom_mode 映射是否符合表"),
    ("flight_control", "基础飞控控制", "P0", "GUIDED/ARM/TAKEOFF/LAND/RTL/LOITER"),
    ("wayline_resume", "航线与断点续飞", "P0", "航线下发、暂停返航、断点保存、断点恢复"),
    ("dock_v13", "机巢 v1.3 命令", "P0", "舱门、推杆、充电、急停、机巢状态"),
    ("one_key_takeoff", "一键启航", "P1", "准备、起飞、航线启动、进度事件"),
    ("drc", "DRC 控制链路", "P1", "抢占、心跳、摇杆/飞向点、释放"),
    ("media_live", "媒体与直播", "P1", "文件上传、相机、直播启停和状态"),
    ("hms_log", "HMS/日志", "P1", "告警、设备日志、诊断信息"),
    ("firmware_config", "固件与配置", "P2", "固件升级、参数配置、远程配置"),
    ("topology_org", "拓扑/组织", "P2", "status、拓扑更新、机场/网关组织信息"),
    ("trace_nonblock", "非阻塞与 Trace", "P2", "事务号、异步回包、长任务不阻塞"),
]


def initial_key_points() -> dict[str, dict[str, Any]]:
    # 页面启动时先展示完整测试清单，后续 MQTT/OSD 命中后再刷新状态和证据。
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
        for point_id, title, stage, description in TEST_POINT_DEFS
    }


state: dict[str, Any] = {
    "gateway_sn": GATEWAY_SN,
    "mqtt": {"connected": False, "host": MQTT_HOST, "port": MQTT_PORT},
    "mavlink": {"url": MAVLINK_URL, "target_system": MAVLINK_TARGET_SYSTEM, "target_component": MAVLINK_TARGET_COMPONENT},
    "latest_osd": {},
    "latest_state": {},
    "topic_counts": {},
    "messages": deque(maxlen=120),
    "events": deque(maxlen=80),
    "osd_history": deque(maxlen=180),
    "command_log": deque(maxlen=80),
    "key_points": initial_key_points(),
    "key_info": deque(maxlen=240),
    "sitl": {"running": False, "pid": None, "python": SITL_PYTHON, "logs": deque(maxlen=300)},
    "started_at": int(time.time() * 1000),
}

mqtt_client: mqtt.Client | None = None
mavlink_conn: Any = None
mavlink_lock = threading.RLock()
sitl_process: subprocess.Popen[str] | None = None
sitl_lock = threading.RLock()


def now_ms() -> int:
    return int(time.time() * 1000)


def json_safe(value: Any) -> Any:
    if isinstance(value, deque):
        return list(value)
    if isinstance(value, dict):
        return {k: json_safe(v) for k, v in value.items()}
    if isinstance(value, list):
        return [json_safe(v) for v in value]
    return value


def broadcast(event: str, payload: Any) -> None:
    data = json.dumps({"event": event, "payload": json_safe(payload), "timestamp": now_ms()}, ensure_ascii=False)
    dead: list[queue.Queue[str]] = []
    for subscriber in list(subscribers):
        try:
            subscriber.put_nowait(data)
        except Exception:
            dead.append(subscriber)
    for subscriber in dead:
        subscribers.discard(subscriber)


def append_command(kind: str, label: str, status: str, detail: Any = None) -> None:
    item = {"kind": kind, "label": label, "status": status, "detail": detail, "timestamp": now_ms()}
    with state_lock:
        state["command_log"].appendleft(item)
    broadcast("command", item)


def classify_topic(topic: str) -> str:
    if topic.endswith("/osd"):
        return "osd"
    if topic.endswith("/state"):
        return "state"
    if topic.endswith("/events"):
        return "event"
    if topic.endswith("/services"):
        return "service"
    if topic.endswith("/services_reply"):
        return "service_reply"
    if topic.endswith("/requests"):
        return "request"
    if topic.endswith("/requests_reply"):
        return "request_reply"
    if topic.endswith("/status") or topic.endswith("/status_reply"):
        return "status"
    return "other"


def compact_value(value: Any, limit: int = 96) -> str:
    if value is None:
        return "--"
    if isinstance(value, (dict, list)):
        text = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    else:
        text = str(value)
    return text if len(text) <= limit else f"{text[:limit - 1]}…"


def method_point_id(method: str, topic: str) -> str | None:
    # 云端方法名和 topic 是联调时最稳定的信号，用它把原始日志归类到测试点。
    name = method.lower()
    topic_l = topic.lower()
    if any(key in name for key in ("flighttask", "wayline", "breakpoint", "return_home")):
        return "wayline_resume"
    if any(key in name for key in ("one_key_takeoff", "takeoff_to_point")):
        return "one_key_takeoff"
    if any(key in name for key in ("drc", "stick", "fly_to_point", "heart_beat")) or "/drc/" in topic_l:
        return "drc"
    if any(key in name for key in ("cover", "charge", "putter", "dock", "airport", "emergency", "silent_mode")):
        return "dock_v13"
    if any(key in name for key in ("live", "camera", "payload", "file", "media")):
        return "media_live"
    if any(key in name for key in ("hms", "log", "alarm")):
        return "hms_log"
    if any(key in name for key in ("firmware", "ota", "config", "parameter")):
        return "firmware_config"
    if any(key in name for key in ("topo", "organization", "workspace", "device_info")) or "status" in topic_l:
        return "topology_org"
    if any(key in name for key in ("take_off", "takeoff", "go_home", "landing", "set_flight_mode", "mode")):
        return "flight_control"
    return None


def state_point_ids(changes: dict[str, Any]) -> set[str]:
    # State 上报没有固定 method，通过字段名判断它能证明哪个模块工作正常。
    keys = {str(key).lower() for key in changes}
    points: set[str] = set()
    if keys & {"mode_code", "flight_mode", "custom_mode", "gear"}:
        points.add("flight_mode")
    if keys & {"cover_state", "putter_state", "drone_charge_state", "emergency_stop_state", "rainfall", "wind_speed"}:
        points.add("dock_v13")
    if keys & {"drc_state", "drc_mode_enter_time", "drc_switch_state"}:
        points.add("drc")
    if keys & {"live_status", "camera_mode", "payload_index", "camera_state"}:
        points.add("media_live")
    if keys & {"hms", "hms_key", "alarm_state", "log_state"}:
        points.add("hms_log")
    if keys & {"firmware_version", "firmware_status", "config_status"}:
        points.add("firmware_config")
    if keys & {"gateway_sn", "device_sn", "domain", "type", "sub_type"}:
        points.add("topology_org")
    return points


def record_key_point(
    point_id: str,
    status: str,
    summary: str,
    source: str,
    detail: str,
    timestamp: int,
    min_interval_ms: int = 0,
) -> bool:
    # 每个测试点只保留最近几条证据，避免页面像原始日志一样被高频消息刷屏。
    point = state["key_points"].get(point_id)
    if not point:
        return False

    previous_last_seen = point.get("last_seen")
    previous_status = point.get("status")
    should_record_evidence = (
        min_interval_ms <= 0
        or not previous_last_seen
        or timestamp - int(previous_last_seen) >= min_interval_ms
        or previous_status != status
    )

    evidence = {
        "timestamp": timestamp,
        "source": source,
        "detail": detail,
    }
    point["status"] = status
    point["summary"] = summary
    point["last_seen"] = timestamp
    if should_record_evidence:
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
    # 从原始 MQTT 消息中提炼人员联调真正关心的信息，供页面长期展示。
    payload = item.get("payload")
    topic = str(item.get("topic", ""))
    topic_type = str(item.get("type", "other"))
    timestamp = int(item.get("timestamp") or now_ms())
    updated = False

    if topic_type == "osd" and isinstance(payload, dict):
        battery = (payload.get("battery") or {}).get("capacity_percent")
        gps = (payload.get("position_state") or {}).get("gps_number")
        summary = (
            f"mode_code={compact_value(payload.get('mode_code'))}，"
            f"高度={compact_value(payload.get('height'))}m，"
            f"电量={compact_value(battery)}%，GPS={compact_value(gps)}"
        )
        detail = (
            f"gear={compact_value(payload.get('gear'))}，"
            f"lat={compact_value(payload.get('latitude'), 32)}，"
            f"lon={compact_value(payload.get('longitude'), 32)}，"
            f"垂直速度={compact_value(payload.get('vertical_speed'))}m/s"
        )
        updated |= record_key_point("osd_state", "ok", summary, topicShortBackend(topic), detail, timestamp, min_interval_ms=10000)
        if payload.get("mode_code") is not None:
            updated |= record_key_point(
                "flight_mode",
                "active",
                summary,
                topicShortBackend(topic),
                "OSD 携带 mode_code，可用于核对映射表",
                timestamp,
                min_interval_ms=15000,
            )
        return updated

    if not isinstance(payload, dict):
        return updated

    method = str(payload.get("method") or "")
    tid = payload.get("tid") or payload.get("bid") or "--"
    source = topicShortBackend(topic)

    if topic_type == "state":
        changes = payload.get("changes") if isinstance(payload.get("changes"), dict) else payload
        fields = ", ".join(f"{key}={compact_value(value, 48)}" for key, value in list(changes.items())[:6])
        updated |= record_key_point("osd_state", "ok", "State 属性上报已收到", source, fields, timestamp)
        for point_id in state_point_ids(changes):
            updated |= record_key_point(point_id, "active", "State 字段命中测试点", source, fields, timestamp)
        return updated

    if topic_type in {"service", "request", "service_reply", "request_reply", "event", "status"}:
        point_id = method_point_id(method, topic)
        if topic_type == "status" and point_id is None:
            point_id = "topology_org"
        if topic_type in {"service", "request", "service_reply", "request_reply"}:
            updated |= record_key_point("mqtt_link", "ok", f"{topic_type} 链路收到 {method or '未命名方法'}", source, f"tid={compact_value(tid)}", timestamp)
            updated |= record_key_point("trace_nonblock", "active", "事务号/异步消息可追踪", source, f"method={method or '--'}，tid={compact_value(tid)}", timestamp)
        if point_id:
            data = payload.get("data")
            result = data.get("result") if isinstance(data, dict) else None
            status = "ok" if topic_type.endswith("reply") and result in {0, "0", True, "success", "ok"} else "active"
            summary = f"{method or labels_backend(topic_type)} 已捕获"
            detail = f"tid={compact_value(tid)}，result={compact_value(result)}，data={compact_value(data, 140)}"
            updated |= record_key_point(point_id, status, summary, source, detail, timestamp)
        return updated

    return updated


def topicShortBackend(topic: str) -> str:
    return topic.replace(f"thing/product/{GATEWAY_SN}/", "").replace(f"sys/product/{GATEWAY_SN}/", "sys/")


def labels_backend(topic_type: str) -> str:
    return {
        "osd": "遥测",
        "state": "状态",
        "event": "事件",
        "service": "命令",
        "service_reply": "回包",
        "request": "请求",
        "request_reply": "请求回包",
        "status": "拓扑",
    }.get(topic_type, topic_type)


def on_mqtt_connect(client: mqtt.Client, userdata: Any, flags: dict[str, Any], rc: int) -> None:
    connected = rc == 0
    key_payload: dict[str, Any] | None = None
    with state_lock:
        state["mqtt"]["connected"] = connected
        state["mqtt"]["rc"] = rc
        state["mqtt"]["updated_at"] = now_ms()
        record_key_point(
            "mqtt_link",
            "ok" if connected else "fail",
            "MQTT 已连接并订阅网关 topic" if connected else f"MQTT 连接失败 rc={rc}",
            f"{MQTT_HOST}:{MQTT_PORT}",
            "订阅范围：" + ", ".join(TOPICS),
            state["mqtt"]["updated_at"],
        )
        key_payload = {"key_points": state["key_points"], "key_info": state["key_info"]}
    if connected:
        for topic in TOPICS:
            client.subscribe(topic, qos=0)
    broadcast("mqtt_status", state["mqtt"])
    broadcast("key_update", key_payload)


def on_mqtt_disconnect(client: mqtt.Client, userdata: Any, rc: int) -> None:
    key_payload: dict[str, Any] | None = None
    with state_lock:
        state["mqtt"]["connected"] = False
        state["mqtt"]["rc"] = rc
        state["mqtt"]["updated_at"] = now_ms()
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


def on_mqtt_message(client: mqtt.Client, userdata: Any, msg: mqtt.MQTTMessage) -> None:
    topic = msg.topic
    text = msg.payload.decode("utf-8", errors="replace")
    try:
        payload: Any = json.loads(text)
    except json.JSONDecodeError:
        payload = text

    topic_type = classify_topic(topic)
    item = {
        "topic": topic,
        "type": topic_type,
        "payload": payload,
        "timestamp": now_ms(),
    }

    key_payload: dict[str, Any] | None = None
    with state_lock:
        counts = state["topic_counts"]
        counts[topic_type] = counts.get(topic_type, 0) + 1
        state["messages"].appendleft(item)

        if topic_type == "osd" and isinstance(payload, dict):
            state["latest_osd"] = payload
            state["osd_history"].append({
                "timestamp": payload.get("timestamp", item["timestamp"]),
                "height": payload.get("height"),
                "altitude": payload.get("altitude"),
                "vertical_speed": payload.get("vertical_speed"),
                "battery": (payload.get("battery") or {}).get("capacity_percent"),
            })
        elif topic_type == "state" and isinstance(payload, dict):
            state["latest_state"] = payload
        elif topic_type == "event":
            state["events"].appendleft(item)

        if extract_key_info(item):
            key_payload = {"key_points": state["key_points"], "key_info": state["key_info"]}

    broadcast("mqtt_message", item)
    if key_payload is not None:
        broadcast("key_update", key_payload)


def build_mqtt_client() -> mqtt.Client:
    client_id = f"edge_gateway_web_{uuid.uuid4().hex[:8]}"
    if hasattr(mqtt, "CallbackAPIVersion"):
        try:
            client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION1, client_id=client_id)
        except TypeError:
            client = mqtt.Client(client_id=client_id)
    else:
        client = mqtt.Client(client_id=client_id)
    client.on_connect = on_mqtt_connect
    client.on_disconnect = on_mqtt_disconnect
    client.on_message = on_mqtt_message
    return client


def start_mqtt() -> None:
    global mqtt_client
    mqtt_client = build_mqtt_client()
    mqtt_client.connect_async(MQTT_HOST, MQTT_PORT, keepalive=30)
    mqtt_client.loop_start()


def get_mavlink():
    global mavlink_conn
    with mavlink_lock:
        if mavlink_conn is None:
            mavlink_conn = mavutil.mavlink_connection(
                MAVLINK_URL,
                source_system=254,
                source_component=190,
                autoreconnect=True,
            )
        return mavlink_conn


def send_mode(mode: str) -> dict[str, Any]:
    mode_name = mode.upper()
    if mode_name not in COPTER_MODES:
        raise ValueError(f"Unsupported Copter mode: {mode}")
    conn = get_mavlink()
    conn.mav.set_mode_send(
        MAVLINK_TARGET_SYSTEM,
        mavutil.mavlink.MAV_MODE_FLAG_CUSTOM_MODE_ENABLED,
        COPTER_MODES[mode_name],
    )
    return {"mode": mode_name, "mode_id": COPTER_MODES[mode_name]}


def send_arm(arm: bool) -> dict[str, Any]:
    conn = get_mavlink()
    conn.mav.command_long_send(
        MAVLINK_TARGET_SYSTEM,
        MAVLINK_TARGET_COMPONENT,
        mavutil.mavlink.MAV_CMD_COMPONENT_ARM_DISARM,
        0,
        1 if arm else 0,
        0,
        0,
        0,
        0,
        0,
        0,
    )
    return {"armed": arm}


def send_takeoff(altitude: float) -> dict[str, Any]:
    conn = get_mavlink()
    send_mode("GUIDED")
    time.sleep(0.2)
    send_arm(True)
    time.sleep(0.4)
    conn.mav.command_long_send(
        MAVLINK_TARGET_SYSTEM,
        MAVLINK_TARGET_COMPONENT,
        mavutil.mavlink.MAV_CMD_NAV_TAKEOFF,
        0,
        0,
        0,
        0,
        0,
        0,
        0,
        float(altitude),
    )
    return {"altitude": altitude}


def run_mavlink_action(action: str, payload: dict[str, Any]) -> dict[str, Any]:
    action = action.lower()
    if action == "mode":
        return send_mode(str(payload.get("mode", "GUIDED")))
    if action == "guided":
        return send_mode("GUIDED")
    if action == "land":
        return send_mode("LAND")
    if action == "rtl":
        return send_mode("RTL")
    if action == "loiter":
        return send_mode("LOITER")
    if action == "arm":
        return send_arm(True)
    if action == "disarm":
        return send_arm(False)
    if action == "takeoff":
        return send_takeoff(float(payload.get("altitude", 10)))
    raise ValueError(f"Unsupported action: {action}")


def sitl_log(line: str) -> None:
    item = {"line": line.rstrip(), "timestamp": now_ms()}
    with state_lock:
        state["sitl"]["logs"].append(item)
    broadcast("sitl_log", item)


def read_sitl_output(proc: subprocess.Popen[str]) -> None:
    assert proc.stdout is not None
    for line in proc.stdout:
        sitl_log(line)
    with sitl_lock:
        running = sitl_process is not None and sitl_process.poll() is None
        state["sitl"]["running"] = running
        state["sitl"]["returncode"] = proc.returncode
        state["sitl"]["updated_at"] = now_ms()
    broadcast("sitl_status", state["sitl"])


def build_sitl_env() -> dict[str, str]:
    env = os.environ.copy()
    venv = env.pop("VIRTUAL_ENV", None)
    env.pop("PYTHONPATH", None)

    existing_paths = env.get("PATH", "").split(os.pathsep)
    if venv:
        venv_bin = Path(venv).expanduser() / "bin"
        existing_paths = [
            path for path in existing_paths
            if Path(path).expanduser().resolve() != venv_bin.resolve()
        ]

    ardupilot_tools = ARDUPILOT_DIR / "Tools" / "autotest"
    preferred_paths = [str(Path.home() / ".local" / "bin"), str(ardupilot_tools)]
    env["PATH"] = os.pathsep.join([*preferred_paths, *existing_paths])
    return env


@app.get("/")
def index() -> Response:
    return send_from_directory(STATIC_DIR, "index.html")


@app.get("/api/status")
def api_status():
    with state_lock:
        snapshot = json_safe(state)
    return jsonify(snapshot)


@app.get("/api/stream")
def api_stream() -> Response:
    q: queue.Queue[str] = queue.Queue(maxsize=300)
    subscribers.add(q)

    def generate():
        try:
            with state_lock:
                initial = json.dumps({"event": "snapshot", "payload": json_safe(state), "timestamp": now_ms()}, ensure_ascii=False)
            yield f"data: {initial}\n\n"
            while True:
                try:
                    data = q.get(timeout=15)
                    yield f"data: {data}\n\n"
                except queue.Empty:
                    yield ": ping\n\n"
        finally:
            subscribers.discard(q)

    return Response(generate(), mimetype="text/event-stream")


@app.post("/api/mavlink")
def api_mavlink():
    payload = request.get_json(force=True, silent=True) or {}
    action = str(payload.get("action", "")).strip()
    if not action:
        return jsonify({"ok": False, "error": "missing action"}), 400
    try:
        detail = run_mavlink_action(action, payload)
        append_command("mavlink", action, "ok", detail)
        return jsonify({"ok": True, "detail": detail})
    except Exception as exc:
        append_command("mavlink", action, "error", str(exc))
        return jsonify({"ok": False, "error": str(exc)}), 500


@app.post("/api/mqtt-command")
def api_mqtt_command():
    if mqtt_client is None:
        return jsonify({"ok": False, "error": "MQTT client not initialized"}), 500
    payload = request.get_json(force=True, silent=True) or {}
    method = str(payload.get("method", "")).strip()
    data = payload.get("data", {})
    channel = str(payload.get("channel", "services")).strip()
    if channel not in {"services", "property", "drc"}:
        return jsonify({"ok": False, "error": "channel must be services, property, or drc"}), 400
    if not method:
        return jsonify({"ok": False, "error": "missing method"}), 400

    topic_map = {
        "services": f"thing/product/{GATEWAY_SN}/services",
        "property": f"thing/product/{GATEWAY_SN}/property/set",
        "drc": f"thing/product/{GATEWAY_SN}/drc/down",
    }
    message = {
        "tid": str(uuid.uuid4()),
        "bid": str(uuid.uuid4()),
        "timestamp": now_ms(),
        "gateway": GATEWAY_SN,
        "method": method,
        "need_reply": 1,
        "data": data,
    }
    topic = topic_map[channel]
    result = mqtt_client.publish(topic, json.dumps(message, ensure_ascii=False), qos=1)
    detail = {"topic": topic, "method": method, "mid": result.mid}
    append_command("mqtt", method, "ok" if result.rc == mqtt.MQTT_ERR_SUCCESS else f"rc={result.rc}", detail)
    return jsonify({"ok": result.rc == mqtt.MQTT_ERR_SUCCESS, "detail": detail, "message": message})


@app.post("/api/sitl/start")
def api_sitl_start():
    global sitl_process
    with sitl_lock:
        if sitl_process is not None and sitl_process.poll() is None:
            return jsonify({"ok": True, "detail": "SITL already running", "pid": sitl_process.pid})

        vehicle = str((request.get_json(silent=True) or {}).get("vehicle", "ArduCopter"))
        vehicle_dir = ARDUPILOT_DIR / vehicle
        sim_vehicle = ARDUPILOT_DIR / "Tools" / "autotest" / "sim_vehicle.py"
        if not vehicle_dir.exists():
            return jsonify({"ok": False, "error": f"vehicle directory not found: {vehicle_dir}"}), 404
        if not sim_vehicle.exists():
            return jsonify({"ok": False, "error": f"sim_vehicle.py not found: {sim_vehicle}"}), 404

        cmd = [SITL_PYTHON, str(sim_vehicle), "-v", vehicle, "--out=127.0.0.1:14550"]
        try:
            sitl_process = subprocess.Popen(
                cmd,
                cwd=str(vehicle_dir),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                preexec_fn=os.setsid,
                env=build_sitl_env(),
            )
        except Exception as exc:
            append_command("sitl", "start", "error", str(exc))
            return jsonify({"ok": False, "error": str(exc), "command": cmd}), 500
        state["sitl"]["running"] = True
        state["sitl"]["pid"] = sitl_process.pid
        state["sitl"]["python"] = SITL_PYTHON
        state["sitl"]["command"] = " ".join(cmd)
        state["sitl"]["updated_at"] = now_ms()
        threading.Thread(target=read_sitl_output, args=(sitl_process,), daemon=True).start()
        broadcast("sitl_status", state["sitl"])
        append_command("sitl", "start", "ok", {"pid": sitl_process.pid})
        return jsonify({"ok": True, "pid": sitl_process.pid})


@app.post("/api/sitl/stop")
def api_sitl_stop():
    global sitl_process
    with sitl_lock:
        if sitl_process is None or sitl_process.poll() is not None:
            state["sitl"]["running"] = False
            state["sitl"]["pid"] = None
            broadcast("sitl_status", state["sitl"])
            return jsonify({"ok": True, "detail": "SITL not running"})
        os.killpg(os.getpgid(sitl_process.pid), signal.SIGTERM)
        pid = sitl_process.pid
        sitl_process = None
        state["sitl"]["running"] = False
        state["sitl"]["pid"] = None
        state["sitl"]["updated_at"] = now_ms()
        broadcast("sitl_status", state["sitl"])
        append_command("sitl", "stop", "ok", {"pid": pid})
        return jsonify({"ok": True, "pid": pid})


if __name__ == "__main__":
    start_mqtt()
    print(f"Edge Gateway Web Console: http://{WEB_HOST}:{WEB_PORT}")
    print(f"MQTT: {MQTT_HOST}:{MQTT_PORT}, gateway={GATEWAY_SN}")
    print(f"MAVLink command URL: {MAVLINK_URL}")
    app.run(host=WEB_HOST, port=WEB_PORT, threaded=True)
