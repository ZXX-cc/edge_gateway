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
    .video-wrap { display: grid; place-items: center; background: #050505; min-height: 0; }
    video { width: 100%; height: 100%; object-fit: contain; background: #000; }
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
      <div class="video-wrap"><video id="video" controls autoplay muted playsinline></video></div>
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
          <span></span><button onmousedown="drag(1,0)" onmouseup="stopDrag()" onclick="drag(1,0)">上</button><span></span>
          <button onmousedown="drag(0,-1)" onmouseup="stopDrag()" onclick="drag(0,-1)">左</button><button onclick="stopDrag()">停</button><button onmousedown="drag(0,1)" onmouseup="stopDrag()" onclick="drag(0,1)">右</button>
          <span></span><button onmousedown="drag(-1,0)" onmouseup="stopDrag()" onclick="drag(-1,0)">下</button><span></span>
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
        <h2>日志</h2>
        <div class="status" id="status">页面已就绪</div>
      </section>
    </aside>
  </main>
<script>
const playUrl = location.origin + '/live.flv';
document.getElementById('playUrl').textContent = playUrl;
let player = null;
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
function stopDrag() { return api('/api/drag', { pitch_speed: 0, yaw_speed: 0 }); }
window.addEventListener('keydown', (ev) => {
  if (ev.repeat) return;
  if (ev.key === 'w') drag(1,0);
  if (ev.key === 's') drag(-1,0);
  if (ev.key === 'a') drag(0,-1);
  if (ev.key === 'd') drag(0,1);
  if (ev.key === ' ') { ev.preventDefault(); stopDrag(); }
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
            rc, reply = request_services("camera_screen_drag", payload)
            return {"ok": rc == 0, "request": payload, "reply": reply}

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
