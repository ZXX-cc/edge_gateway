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


def on_mqtt_connect(client: mqtt.Client, userdata: Any, flags: dict[str, Any], rc: int) -> None:
    connected = rc == 0
    with state_lock:
        state["mqtt"]["connected"] = connected
        state["mqtt"]["rc"] = rc
        state["mqtt"]["updated_at"] = now_ms()
    if connected:
        for topic in TOPICS:
            client.subscribe(topic, qos=0)
    broadcast("mqtt_status", state["mqtt"])


def on_mqtt_disconnect(client: mqtt.Client, userdata: Any, rc: int) -> None:
    with state_lock:
        state["mqtt"]["connected"] = False
        state["mqtt"]["rc"] = rc
        state["mqtt"]["updated_at"] = now_ms()
    broadcast("mqtt_status", state["mqtt"])


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

    broadcast("mqtt_message", item)


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
