#!/usr/bin/env python3
"""Dock3 负载直播和云台控制本地页面。

页面只作为现场测试面板使用：浏览器按钮调用本机 HTTP 接口，本机接口再通过
MQTT services 下发 Dock3 Cloud API 指令，避免把 MQTT 连接暴露给浏览器。
"""

from __future__ import annotations

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import sys
import threading
import urllib.request
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from mqtt_test_client import MqttRequestClient, result_code  # noqa: E402
from src.utils.dji_error_codes import INVALID_COMMAND_FORMAT  # noqa: E402
from tool_config import add_config_argument, resolve_mqtt_settings  # noqa: E402


DEFAULT_PUSH_URL = "rtmp://39.185.72.155:22944/dock/NEST44202509U003?secret=44a6317ef7504d178faa077d70c0fb9dnorecord"
DEFAULT_PLAY_URL = "https://flysee.lclfyl.top:22943/dock/NEST44202509U003.flv"


HTML = """<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Dock3 负载控制</title>
  <script src="https://cdn.jsdelivr.net/npm/mpegts.js@1.7.3/dist/mpegts.min.js"></script>
  <style>
    :root { color-scheme: dark; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }
    body { margin: 0; background: #111; color: #f4f4f5; }
    main { display: grid; grid-template-columns: minmax(0, 1fr) 360px; height: 100vh; }
    .stage { display: grid; grid-template-rows: auto 1fr auto; min-width: 0; }
    header { padding: 14px 18px; border-bottom: 1px solid #2a2a2d; display: flex; justify-content: space-between; gap: 14px; }
    h1 { font-size: 18px; margin: 0; font-weight: 650; }
    .hint { color: #a1a1aa; font-size: 13px; }
    .video-wrap { position: relative; display: grid; place-items: center; background: #050505; min-height: 0; overflow: hidden; touch-action: none; }
    video { width: 100%; height: 100%; object-fit: contain; background: #000; }
    .select-layer { position: absolute; inset: 0; cursor: crosshair; }
    .select-box { position: absolute; border: 2px solid #22c55e; background: rgba(34, 197, 94, .16); box-shadow: 0 0 0 1px rgba(0,0,0,.55); display: none; pointer-events: none; }
    aside { border-left: 1px solid #2a2a2d; padding: 16px; overflow: auto; background: #18181b; }
    section { margin-bottom: 18px; }
    h2 { font-size: 14px; margin: 0 0 10px; color: #e4e4e7; }
    .grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 8px; }
    .pad { display: grid; grid-template-columns: repeat(3, 54px); gap: 8px; justify-content: center; }
    button { border: 1px solid #3f3f46; background: #27272a; color: #fff; border-radius: 7px; min-height: 40px; font-size: 14px; cursor: pointer; }
    button:hover { background: #3f3f46; }
    button.primary { background: #14532d; border-color: #16a34a; }
    button.warn { background: #5b211c; border-color: #dc2626; }
    button:active { transform: translateY(1px); }
    input[type="range"] { width: 100%; }
    .status { white-space: pre-wrap; background: #0b0b0c; border: 1px solid #303036; border-radius: 7px; padding: 10px; min-height: 130px; color: #d4d4d8; font-size: 12px; overflow: auto; }
    footer { padding: 10px 18px; border-top: 1px solid #2a2a2d; color: #a1a1aa; font-size: 12px; }
    @media (max-width: 900px) { main { grid-template-columns: 1fr; grid-template-rows: 55vh auto; height: auto; min-height: 100vh; } aside { border-left: 0; border-top: 1px solid #2a2a2d; } }
  </style>
</head>
<body>
  <main>
    <div class="stage">
      <header>
        <div><h1>Dock3 负载控制</h1><div class="hint" id="source">等待推流</div></div>
        <button onclick="reloadVideo()">刷新画面</button>
      </header>
      <div class="video-wrap" id="videoWrap">
        <video id="video" controls autoplay muted playsinline></video>
        <div class="select-layer" id="selectLayer"><div class="select-box" id="selectBox"></div></div>
      </div>
      <footer id="playUrl"></footer>
    </div>
    <aside>
      <section>
        <h2>直播源</h2>
        <div class="grid">
          <button class="primary" onclick="api('/api/live/gimbal')">推云台</button>
          <button class="primary" onclick="api('/api/live/mapping')">推正射</button>
          <button class="warn" onclick="api('/api/live/stop')">停止推流</button>
          <button onclick="api('/api/live/capability')">查询能力</button>
        </div>
      </section>
      <section>
        <h2>云台拖动</h2>
        <div class="pad">
          <span></span><button onpointerdown="startDrag(1,0)" onpointerup="stopDrag()" onpointerleave="stopDrag()">上</button><span></span>
          <button onpointerdown="startDrag(0,-1)" onpointerup="stopDrag()" onpointerleave="stopDrag()">左</button><button onclick="stopDrag()">停</button><button onpointerdown="startDrag(0,1)" onpointerup="stopDrag()" onpointerleave="stopDrag()">右</button>
          <span></span><button onpointerdown="startDrag(-1,0)" onpointerup="stopDrag()" onpointerleave="stopDrag()">下</button><span></span>
        </div>
      </section>
      <section>
        <h2>速度 <span id="speedText">2</span> degree/s</h2>
        <input id="speed" type="range" min="0.5" max="10" step="0.5" value="2" oninput="speedText.textContent=this.value">
      </section>
      <section>
        <h2>变焦/画质</h2>
        <div class="grid">
          <button onclick="api('/api/zoom/in')">变焦 +</button>
          <button onclick="api('/api/zoom/out')">变焦 -</button>
          <button onclick="api('/api/quality/3')">高清</button>
          <button onclick="api('/api/quality/4')">超清</button>
        </div>
      </section>
      <section>
        <h2>框选/AIM</h2>
        <div class="grid">
          <button class="primary" onclick="frameZoom()">框选变焦</button>
          <button onclick="aimLock()">AIM 锁定</button>
          <button class="warn" onclick="api('/api/track/stop')">停止跟踪</button>
          <button onclick="clearSelection()">清除框选</button>
        </div>
        <div class="hint" id="boxText" style="margin-top:8px">在画面上拖动鼠标框选目标</div>
      </section>
      <section>
        <h2>日志</h2>
        <div class="status" id="status">页面已就绪</div>
      </section>
    </aside>
  </main>
<script>
const playUrl = location.origin + '/live.flv';
document.getElementById('playUrl').textContent = playUrl;
let player = null;
let dragTimer = null;
let selection = null;
let selecting = false;
let startPoint = null;
const selectLayer = document.getElementById('selectLayer');
const selectBox = document.getElementById('selectBox');
const boxText = document.getElementById('boxText');
function log(obj) {
  const text = typeof obj === 'string' ? obj : JSON.stringify(obj, null, 2);
  status.textContent = new Date().toLocaleTimeString() + '\\n' + text;
}
function reloadVideo() {
  const video = document.getElementById('video');
  if (player) { player.destroy(); player = null; }
  if (window.mpegts && mpegts.getFeatureList().mseLivePlayback) {
    player = mpegts.createPlayer({ type: 'flv', isLive: true, url: playUrl });
    player.attachMediaElement(video);
    player.load();
    player.play().catch(() => {});
  } else {
    video.src = playUrl;
    video.play().catch(() => {});
  }
  log('播放器已刷新');
}
async function api(path, data = {}) {
  const res = await fetch(path, { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify(data) });
  const body = await res.json();
  log(body);
  if (body.source) document.getElementById('source').textContent = body.source;
  return body;
}
function speedValue() { return Number(document.getElementById('speed').value); }
function drag(pitchDir, yawDir) { return api('/api/drag', { pitch_speed: pitchDir * speedValue(), yaw_speed: yawDir * speedValue() }); }
function startDrag(pitchDir, yawDir) {
  if (dragTimer) clearInterval(dragTimer);
  drag(pitchDir, yawDir);
  dragTimer = setInterval(() => drag(pitchDir, yawDir), 250);
}
function stopDrag() {
  if (dragTimer) { clearInterval(dragTimer); dragTimer = null; }
  return api('/api/drag', { pitch_speed: 0, yaw_speed: 0 });
}
function clamp(value, min, max) { return Math.max(min, Math.min(max, value)); }
function pointFromEvent(ev) {
  const rect = selectLayer.getBoundingClientRect();
  return {
    x: clamp(ev.clientX - rect.left, 0, rect.width),
    y: clamp(ev.clientY - rect.top, 0, rect.height),
    w: rect.width,
    h: rect.height
  };
}
function drawSelection(a, b) {
  const left = Math.min(a.x, b.x);
  const top = Math.min(a.y, b.y);
  const width = Math.abs(a.x - b.x);
  const height = Math.abs(a.y - b.y);
  selectBox.style.display = 'block';
  selectBox.style.left = left + 'px';
  selectBox.style.top = top + 'px';
  selectBox.style.width = width + 'px';
  selectBox.style.height = height + 'px';
  return { left, top, width, height, layerWidth: a.w, layerHeight: a.h };
}
function saveSelection(box) {
  if (box.width < 8 || box.height < 8) {
    clearSelection();
    return;
  }
  selection = {
    x: box.left / box.layerWidth,
    y: box.top / box.layerHeight,
    width: box.width / box.layerWidth,
    height: box.height / box.layerHeight
  };
  boxText.textContent = `框选: x=${selection.x.toFixed(3)}, y=${selection.y.toFixed(3)}, w=${selection.width.toFixed(3)}, h=${selection.height.toFixed(3)}`;
}
function clearSelection() {
  selection = null;
  selecting = false;
  selectBox.style.display = 'none';
  boxText.textContent = '在画面上拖动鼠标框选目标';
}
function requireSelection() {
  if (!selection) {
    log('请先在画面上拖动鼠标框选目标');
    throw new Error('no selection');
  }
  return selection;
}
function frameZoom() { return api('/api/frame_zoom', requireSelection()); }
function aimLock() {
  const box = requireSelection();
  return api('/api/aim', {
    x: box.x + box.width / 2,
    y: box.y + box.height / 2,
    width: box.width,
    height: box.height,
    locked: true
  });
}
selectLayer.addEventListener('pointerdown', (ev) => {
  selecting = true;
  startPoint = pointFromEvent(ev);
  selectLayer.setPointerCapture(ev.pointerId);
  drawSelection(startPoint, startPoint);
});
selectLayer.addEventListener('pointermove', (ev) => {
  if (!selecting || !startPoint) return;
  drawSelection(startPoint, pointFromEvent(ev));
});
selectLayer.addEventListener('pointerup', (ev) => {
  if (!selecting || !startPoint) return;
  const box = drawSelection(startPoint, pointFromEvent(ev));
  saveSelection(box);
  selecting = false;
  startPoint = null;
});
selectLayer.addEventListener('dblclick', (ev) => {
  const p = pointFromEvent(ev);
  api('/api/aim', { x: p.x / p.w, y: p.y / p.h, width: 0.08, height: 0.08, locked: true });
});
window.addEventListener('keydown', (ev) => {
  if (ev.repeat) return;
  if (ev.key === 'w') startDrag(1,0);
  if (ev.key === 's') startDrag(-1,0);
  if (ev.key === 'a') startDrag(0,-1);
  if (ev.key === 'd') startDrag(0,1);
  if (ev.key === ' ') { ev.preventDefault(); stopDrag(); }
});
window.addEventListener('keyup', (ev) => {
  if (['w', 'a', 's', 'd'].includes(ev.key)) stopDrag();
});
reloadVideo();
</script>
</body>
</html>
"""


class Dock3PanelClient(MqttRequestClient):
    def services(self, method: str, data: dict[str, Any], timeout: float, allowed=None, status=None):
        return super().request(
            method,
            data,
            timeout,
            channel="services",
            allowed_results={0} if allowed is None else set(allowed),
            expected_status=status,
        )


def make_handler(args, client: Dock3PanelClient):
    lock = threading.Lock()
    state = {
        "active_label": "",
        "active_video_id": "",
        "quality": int(args.quality),
        "zoom_ratio": float(args.zoom_ratio),
        "gimbal_pitch": float(args.gimbal_pitch),
        "gimbal_yaw": float(args.gimbal_yaw),
    }

    def request_services(method: str, data: dict[str, Any], allowed=None, status=None):
        with lock:
            if not client.connected and not client.connect(args.timeout):
                return 1, {"error": f"MQTT 未连接: {args.host}:{args.port}"}
            rc, reply = client.services(method, data, args.timeout, allowed=allowed, status=status)
        return rc, reply

    def stop_video(video_id: str):
        if not video_id:
            return 0, None
        rc, reply = request_services("live_stop_push", {"video_id": video_id}, allowed={0, INVALID_COMMAND_FORMAT})
        if rc == 0 and reply and result_code(reply) == INVALID_COMMAND_FORMAT:
            return 0, reply
        return rc, reply

    def start_live(label: str, video_id: str):
        stop_video(state["active_video_id"])
        rc, reply = request_services(
            "live_start_push",
            {
                "url": args.url,
                "url_type": args.url_type,
                "video_id": video_id,
                "video_quality": state["quality"],
            },
            status="in_progress",
        )
        if rc == 0:
            state["active_label"] = label
            state["active_video_id"] = video_id
        return rc, reply

    def json_bytes(payload: dict[str, Any]) -> bytes:
        return json.dumps(payload, ensure_ascii=False).encode("utf-8")

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *values):
            print(f"{self.address_string()} - {fmt % values}")

        def _send(self, status: int, content_type: str, body: bytes) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _send_json(self, payload: dict[str, Any], status: int = 200) -> None:
            self._send(status, "application/json; charset=utf-8", json_bytes(payload))

        def _read_json(self) -> dict[str, Any]:
            length = int(self.headers.get("Content-Length", "0") or 0)
            if length <= 0:
                return {}
            raw = self.rfile.read(length)
            try:
                data = json.loads(raw.decode("utf-8"))
                return data if isinstance(data, dict) else {}
            except json.JSONDecodeError:
                return {}

        def do_GET(self):  # noqa: N802
            if self.path == "/":
                self._send(200, "text/html; charset=utf-8", HTML.encode("utf-8"))
                return
            if self.path == "/live.flv":
                self.send_response(200)
                self.send_header("Content-Type", "video/x-flv")
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                try:
                    with urllib.request.urlopen(args.play_url, timeout=10) as upstream:
                        while True:
                            chunk = upstream.read(64 * 1024)
                            if not chunk:
                                break
                            self.wfile.write(chunk)
                except Exception as exc:
                    print(f"FLV proxy stopped: {exc}")
                return
            self._send_json({"ok": False, "error": "not found"}, status=404)

        def do_POST(self):  # noqa: N802
            data = self._read_json()
            try:
                payload = route_post(self.path, data)
                self._send_json(payload)
            except Exception as exc:
                self._send_json({"ok": False, "error": str(exc)}, status=500)

    def route_post(path: str, data: dict[str, Any]) -> dict[str, Any]:
        if path == "/api/live/gimbal":
            rc, reply = start_live("云台", args.gimbal_video_id)
            return {"ok": rc == 0, "source": "云台", "reply": reply, "play_url": args.play_url}

        if path == "/api/live/mapping":
            rc, reply = start_live("正射相机", args.mapping_video_id)
            return {"ok": rc == 0, "source": "正射相机", "reply": reply, "play_url": args.play_url}

        if path == "/api/live/stop":
            rc, reply = stop_video(state["active_video_id"])
            if rc == 0:
                state["active_label"] = ""
                state["active_video_id"] = ""
            return {"ok": rc == 0, "source": "已停止", "reply": reply}

        if path == "/api/live/capability":
            rc, reply = request_services("live_capability_query", {}, status="ok")
            return {"ok": rc == 0, "reply": reply}

        if path == "/api/drag":
            payload = {
                "payload_index": args.payload_index,
                "locked": bool(data.get("locked", False)),
                "pitch_speed": float(data.get("pitch_speed", 0.0) or 0.0),
                "yaw_speed": float(data.get("yaw_speed", 0.0) or 0.0),
            }
            screen_rc, screen_reply = request_services("camera_screen_drag", payload)

            gimbal_reply = None
            gimbal_rc = 0
            if payload["pitch_speed"] or payload["yaw_speed"]:
                state["gimbal_pitch"] = max(
                    -90.0,
                    min(30.0, state["gimbal_pitch"] + payload["pitch_speed"] * args.gimbal_step_seconds),
                )
                state["gimbal_yaw"] = ((state["gimbal_yaw"] + payload["yaw_speed"] * args.gimbal_step_seconds + 180.0) % 360.0) - 180.0
                gimbal_payload = {
                    "payload_index": args.payload_index,
                    "pitch": state["gimbal_pitch"],
                    "yaw": state["gimbal_yaw"],
                }
                gimbal_rc, gimbal_reply = request_services("gimbal_rotate", gimbal_payload)
            return {
                "ok": screen_rc == 0 and gimbal_rc == 0,
                "request": payload,
                "gimbal_pose": {"pitch": state["gimbal_pitch"], "yaw": state["gimbal_yaw"]},
                "screen_drag_reply": screen_reply,
                "gimbal_rotate_reply": gimbal_reply,
            }

        if path.startswith("/api/zoom/"):
            direction = path.rsplit("/", 1)[-1]
            step = float(args.zoom_step)
            if direction == "in":
                state["zoom_ratio"] += step
            elif direction == "out":
                state["zoom_ratio"] = max(1.0, state["zoom_ratio"] - step)
            payload = {
                "payload_index": args.payload_index,
                "camera_index": 0,
                "camera_type": "zoom",
                "zoom_ratio": state["zoom_ratio"],
            }
            rc, reply = request_services("camera_focal_length_set", payload)
            return {"ok": rc == 0, "zoom_ratio": state["zoom_ratio"], "reply": reply}

        if path.startswith("/api/quality/"):
            quality = int(path.rsplit("/", 1)[-1])
            state["quality"] = max(0, min(4, quality))
            if not state["active_video_id"]:
                return {"ok": True, "quality": state["quality"], "note": "未推流，仅更新下次启动画质"}
            rc, reply = request_services(
                "live_set_quality",
                {"video_id": state["active_video_id"], "video_quality": state["quality"]},
                status="ok",
            )
            return {"ok": rc == 0, "quality": state["quality"], "reply": reply}

        if path == "/api/frame_zoom":
            # 页面框选是 0-1 归一化坐标，这里换算到默认画面坐标系再下发给负载服务。
            x = max(0.0, min(1.0, float(data.get("x", 0.0) or 0.0)))
            y = max(0.0, min(1.0, float(data.get("y", 0.0) or 0.0)))
            width = max(0.0, min(1.0 - x, float(data.get("width", 0.0) or 0.0)))
            height = max(0.0, min(1.0 - y, float(data.get("height", 0.0) or 0.0)))
            payload = {
                "payload_index": args.payload_index,
                "camera_type": "zoom",
                "locked": bool(data.get("locked", True)),
                "x": int(round(x * args.track_width)),
                "y": int(round(y * args.track_height)),
                "width": max(1, int(round(width * args.track_width))),
                "height": max(1, int(round(height * args.track_height))),
                "normalized": {"x": x, "y": y, "width": width, "height": height},
            }
            rc, reply = request_services("camera_frame_zoom", payload)
            return {"ok": rc == 0, "request": payload, "reply": reply}

        if path == "/api/aim":
            x = max(0.0, min(1.0, float(data.get("x", 0.5) or 0.5)))
            y = max(0.0, min(1.0, float(data.get("y", 0.5) or 0.5)))
            width = max(0.01, min(1.0, float(data.get("width", 0.08) or 0.08)))
            height = max(0.01, min(1.0, float(data.get("height", 0.08) or 0.08)))
            payload = {
                "payload_index": args.payload_index,
                "camera_type": "zoom",
                "locked": bool(data.get("locked", True)),
                "x": int(round(x * args.track_width)),
                "y": int(round(y * args.track_height)),
                "width": max(1, int(round(width * args.track_width))),
                "height": max(1, int(round(height * args.track_height))),
                "normalized": {"x": x, "y": y, "width": width, "height": height},
            }
            rc, reply = request_services("camera_aim", payload)
            return {"ok": rc == 0, "request": payload, "reply": reply}

        if path == "/api/track/stop":
            rc, reply = request_services("viewlink_stop_track", {"payload_index": args.payload_index})
            return {"ok": rc == 0, "reply": reply}

        return {"ok": False, "error": "not found"}

    return Handler


def main() -> int:
    parser = argparse.ArgumentParser(description="启动 Dock3 负载直播和云台控制页面")
    add_config_argument(parser)
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--sn", default=None)
    parser.add_argument("--mqtt-qos", type=int, choices=[0, 1, 2], default=1)
    parser.add_argument("--timeout", type=float, default=10.0)
    parser.add_argument("--url", default=DEFAULT_PUSH_URL)
    parser.add_argument("--play-url", default=DEFAULT_PLAY_URL)
    parser.add_argument("--url-type", type=int, default=1)
    parser.add_argument("--payload-index", default="52-0-0")
    parser.add_argument("--gimbal-video-id", default="")
    parser.add_argument("--mapping-video-id", default="")
    parser.add_argument("--quality", type=int, default=3)
    parser.add_argument("--zoom-ratio", type=float, default=1.0)
    parser.add_argument("--zoom-step", type=float, default=0.5)
    parser.add_argument("--gimbal-pitch", type=float, default=0.0)
    parser.add_argument("--gimbal-yaw", type=float, default=0.0)
    parser.add_argument("--gimbal-step-seconds", type=float, default=0.5)
    parser.add_argument("--track-width", type=int, default=1920)
    parser.add_argument("--track-height", type=int, default=1080)
    parser.add_argument("--listen-host", default="127.0.0.1")
    parser.add_argument("--listen-port", type=int, default=18080)
    parser.add_argument("--quiet-stale", action="store_true")
    args = parser.parse_args()
    args.host, args.port, args.sn = resolve_mqtt_settings(args)
    args.gimbal_video_id = args.gimbal_video_id or f"{args.sn}/52-0-0/normal-0"
    args.mapping_video_id = args.mapping_video_id or f"{args.sn}/52-0-1/mapping-0"

    client = Dock3PanelClient(
        args.host,
        args.port,
        args.sn,
        args.mqtt_qos,
        quiet_stale=args.quiet_stale,
        client_id_prefix="edge_gateway_control_page",
    )
    server = ThreadingHTTPServer((args.listen_host, args.listen_port), make_handler(args, client))
    print(f"控制页面: http://{args.listen_host}:{args.listen_port}")
    print(f"播放代理: http://{args.listen_host}:{args.listen_port}/live.flv")
    print(f"MQTT: {args.host}:{args.port}，首次点击按钮时连接")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print()
    finally:
        server.server_close()
        client.disconnect()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
