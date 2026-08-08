#!/usr/bin/env python3
"""Visual real-gateway test for Dock3 camera_mode_switch.

The command path is intentionally the existing cloud-facing MQTT services
path.  The optional web page only pulls the configured RTSP stream so that the
operator can watch the real camera picture while mode switching is executed.
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import sys
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "tools"))

from dock3_payload_control_page import LocalRtspHlsProxy
from mqtt_camera_mode_switch_acceptance_test import (
    VIEWLINK_RECORD_MODES,
    _busy_case,
    _check_bound,
    _ensure_viewlink_connected,
    _negative_mode_case,
    _payload_status_snapshot,
    _print_summary,
    _read_record_mode,
    _switch_mode,
)
from mqtt_test_client import MqttRequestClient
from tool_config import add_config_argument, resolve_mqtt_settings


HTML = """<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>camera_mode_switch 真实画面观察</title>
  <style>
    body { margin: 0; background: #111; color: #f3f3f3; font-family: system-ui, sans-serif; }
    header { padding: 12px 16px; background: #1f2933; display: flex; gap: 16px; align-items: center; }
    main { height: calc(100vh - 56px); display: grid; place-items: center; }
    video { width: 100%; height: 100%; object-fit: contain; background: #000; }
    code { color: #a7f3d0; }
  </style>
  <script src="https://cdn.jsdelivr.net/npm/hls.js@1.5.17/dist/hls.min.js"></script>
</head>
<body>
  <header>
    <strong>camera_mode_switch 真实画面观察</strong>
    <span>HLS: <code>/hls/stream.m3u8</code></span>
  </header>
  <main><video id="video" controls autoplay muted playsinline></video></main>
  <script>
    const video = document.getElementById('video');
    const url = '/hls/stream.m3u8';
    if (video.canPlayType('application/vnd.apple.mpegurl')) {
      video.src = url;
    } else if (window.Hls && Hls.isSupported()) {
      const hls = new Hls({ lowLatencyMode: true, liveSyncDurationCount: 2 });
      hls.loadSource(url);
      hls.attachMedia(video);
    } else {
      document.body.insertAdjacentHTML('beforeend', '<p>当前浏览器不支持 HLS 播放，请用 VLC 打开 RTSP。</p>');
    }
  </script>
</body>
</html>
"""


def _jsonable_summary(item: dict[str, Any]) -> dict[str, Any]:
    return json.loads(json.dumps(item, ensure_ascii=False, default=str))


def make_visual_handler(proxy: LocalRtspHlsProxy):
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

        def do_GET(self):  # noqa: N802
            if self.path == "/":
                self._send(200, "text/html; charset=utf-8", HTML.encode("utf-8"))
                return
            if self.path.startswith("/hls/"):
                rel_path = urllib.parse.urlparse(self.path).path.removeprefix("/hls/")
                target = (proxy.root / rel_path).resolve()
                root = proxy.root.resolve()
                if root not in target.parents and target != root:
                    self._send(403, "application/json", b'{"ok":false,"error":"invalid hls path"}')
                    return
                if not target.is_file():
                    self._send(404, "application/json", b'{"ok":false,"error":"hls segment not ready"}')
                    return
                content_type = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
                if target.suffix == ".m3u8":
                    content_type = "application/vnd.apple.mpegurl"
                elif target.suffix == ".ts":
                    content_type = "video/mp2t"
                self._send(200, content_type, target.read_bytes())
                return
            self._send(404, "application/json", b'{"ok":false,"error":"not found"}')

    return Handler


def start_visual_page(args) -> tuple[ThreadingHTTPServer | None, LocalRtspHlsProxy | None]:
    if args.no_visual:
        return None, None

    proxy = LocalRtspHlsProxy(args.ffmpeg, args.local_codec, args.rtsp_transport)
    result = proxy.start("camera_mode_switch", args.rtsp_url)
    if not result.get("ok"):
        print(f"真实画面预览启动失败: {result}", file=sys.stderr)
        proxy.stop()
        return None, None

    server = ThreadingHTTPServer((args.listen_host, args.listen_port), make_visual_handler(proxy))
    thread = threading.Thread(target=server.serve_forever, name="camera-mode-visual-page", daemon=True)
    thread.start()
    print(f"真实画面观察页面: http://{args.listen_host}:{args.listen_port}")
    print(f"RTSP 源: {args.rtsp_url}")
    return server, proxy


def stop_visual_page(server: ThreadingHTTPServer | None, proxy: LocalRtspHlsProxy | None) -> None:
    if server:
        server.shutdown()
        server.server_close()
    if proxy:
        proxy.stop()


def observe(args, label: str) -> None:
    if args.no_visual or args.observe_seconds <= 0:
        return
    print(f"观察真实画面: {label}，等待 {args.observe_seconds:.1f}s")
    time.sleep(args.observe_seconds)


def run_visual_acceptance(client: MqttRequestClient, args) -> int:
    summaries: list[dict[str, Any]] = []
    rc = _check_bound(client, args.timeout, skip=args.skip_bind_check)
    if rc != 0:
        return rc
    rc = _ensure_viewlink_connected(client, args)
    if rc != 0:
        return rc

    summaries.append({"case": "payload_status_before", **_payload_status_snapshot(client, args)})
    rc, before = _read_record_mode(client, args)
    summaries.append({"case": "record_mode_before", **before})
    if rc != 0:
        for item in summaries:
            _print_summary(_jsonable_summary(item))
        return rc

    for label, mode in (
        ("switch_photo", "photo"),
        ("repeat_switch_photo", "photo"),
        ("switch_video", "video"),
        ("repeat_switch_video", "video"),
    ):
        observe(args, f"before {label}")
        rc, summary = _switch_mode(
            client,
            args,
            mode,
            expect_success=True,
            label=label,
            expect_record_mode=VIEWLINK_RECORD_MODES[mode],
        )
        summaries.append(summary)
        observe(args, f"after {label}")
        if rc != 0:
            break
        time.sleep(max(0.0, args.pause_between_steps))

    if rc == 0 and not args.skip_playback:
        observe(args, "before switch_playback")
        rc, summary = _switch_mode(
            client,
            args,
            "playback",
            expect_success=args.expect_playback_success,
            label="switch_playback",
            expect_record_mode=None,
        )
        summaries.append(summary)
        observe(args, "after switch_playback")

    stable_mode = None
    if rc == 0:
        read_rc, now = _read_record_mode(client, args)
        summaries.append({"case": "record_mode_before_negative_cases", **now})
        if read_rc != 0:
            rc = read_rc
        else:
            stable_mode = now.get("mode")

    if rc == 0 and not args.skip_illegal_mode:
        rc, summary = _negative_mode_case(client, args, stable_mode)
        summaries.append(summary)

    if rc == 0 and not args.skip_busy:
        rc, summary = _busy_case(client, args, stable_mode)
        summaries.append(summary)

    summaries.append({"case": "payload_status_after", **_payload_status_snapshot(client, args)})
    for item in summaries:
        _print_summary(_jsonable_summary(item))
    return rc


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="真实网关 camera_mode_switch 验收；命令走上云 services，页面显示真实 RTSP 画面。"
    )
    add_config_argument(parser)
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--sn", default=None)
    parser.add_argument("--timeout", type=float, default=15.0)
    parser.add_argument("--qos", type=int, choices=[0, 1, 2], default=1)
    parser.add_argument("--sdk-dir", default="")
    parser.add_argument("--gimbal-ip", default="")
    parser.add_argument("--gimbal-port", type=int, default=0)
    parser.add_argument("--payload-index", default="52-0-0")
    parser.add_argument("--camera-index", type=int, default=0)
    parser.add_argument("--camera-type", default="zoom")
    parser.add_argument("--mode-settle", type=float, default=0.5)
    parser.add_argument("--busy-settle", type=float, default=1.0)
    parser.add_argument("--invalid-mode", default="illegal_mode")
    parser.add_argument("--invalid-camera-mode", type=int, default=99)
    parser.add_argument("--skip-bind-check", action="store_true")
    parser.add_argument("--skip-viewlink-connect", action="store_true")
    parser.add_argument("--skip-playback", action="store_true")
    parser.add_argument("--expect-playback-success", action="store_true")
    parser.add_argument("--skip-illegal-mode", action="store_true")
    parser.add_argument("--skip-busy", action="store_true")
    parser.add_argument("--require-active-record-mode", action="store_true")
    parser.add_argument("--quiet-stale", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--rtsp-url", default="rtsp://192.168.1.119:554/main")
    parser.add_argument("--ffmpeg", default="ffmpeg")
    parser.add_argument("--rtsp-transport", choices=["tcp", "udp"], default="tcp")
    parser.add_argument("--local-codec", choices=["copy", "libx264"], default="copy")
    parser.add_argument("--listen-host", default="127.0.0.1")
    parser.add_argument("--listen-port", type=int, default=18081)
    parser.add_argument("--observe-seconds", type=float, default=3.0)
    parser.add_argument("--pause-between-steps", type=float, default=0.5)
    parser.add_argument("--no-visual", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    args.host, args.port, args.sn = resolve_mqtt_settings(args)

    methods = [
        "gateway_state_query",
        "payload_status_query",
        "viewlink_initialize",
        "viewlink_connect",
        "viewlink_is_connected",
        "viewlink_get_record_mode",
        "camera_mode_switch",
        "camera_recording_start",
        "camera_recording_stop",
    ]
    if args.dry_run:
        print(json.dumps({"dry_run": True, "services_methods": methods, "rtsp_preview": not args.no_visual}, ensure_ascii=False, indent=2))
        return 0

    server, proxy = start_visual_page(args)
    client = MqttRequestClient(
        args.host,
        args.port,
        args.sn,
        qos=args.qos,
        quiet_stale=args.quiet_stale,
        client_id_prefix="edge_gateway_camera_mode_switch_visual",
    )
    try:
        if not client.connect(args.timeout):
            print(f"MQTT connect timeout: host={args.host} port={args.port}", file=sys.stderr)
            return 2
        rc = run_visual_acceptance(client, args)
        if rc != 0:
            print("camera_mode_switch visual acceptance test failed", file=sys.stderr)
            return rc
        print(f"camera_mode_switch visual acceptance test ok (gateway={args.sn}, payload_index={args.payload_index})")
        return 0
    finally:
        client.disconnect()
        stop_visual_page(server, proxy)


if __name__ == "__main__":
    raise SystemExit(main())
