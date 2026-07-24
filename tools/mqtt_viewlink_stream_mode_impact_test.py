#!/usr/bin/env python3
"""Test whether ViewLink photo mode/photo capture interrupts an RTSP preview stream."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "tools"))

from mqtt_test_client import MqttRequestClient
from mqtt_viewlink_camera_mode_test import (
    _check_bound,
    _download_photo,
    _ensure_viewlink_connected,
    _request_service,
    _switch_camera_mode,
    _take_photo,
    _discover_photos,
)
from tool_config import add_config_argument, resolve_mqtt_settings


@dataclass
class StreamEvent:
    ts: float
    kind: str
    detail: str


@dataclass
class RtspMonitor:
    ffmpeg: str
    rtsp_url: str
    rtsp_transport: str
    startup_timeout: float
    stall_threshold: float
    verbose: bool = False
    process: subprocess.Popen | None = None
    started_at: float = 0.0
    stopped_at: float = 0.0
    last_frame_at: float = 0.0
    last_frame: int = 0
    max_frame_gap: float = 0.0
    unexpected_exit: bool = False
    first_frame_event: threading.Event = field(default_factory=threading.Event)
    events: list[StreamEvent] = field(default_factory=list)
    _stop_event: threading.Event = field(default_factory=threading.Event)
    _reader_thread: threading.Thread | None = None

    def start(self) -> bool:
        executable = shutil.which(self.ffmpeg) or self.ffmpeg
        command = [
            executable,
            "-hide_banner",
            "-loglevel",
            "warning",
            "-nostdin",
            "-rtsp_transport",
            self.rtsp_transport,
            "-i",
            self.rtsp_url,
            "-an",
            "-f",
            "null",
            "-progress",
            "pipe:1",
            os.devnull,
        ]
        self.started_at = time.time()
        self.last_frame_at = self.started_at
        self._record("stream_start", " ".join(command))
        try:
            self.process = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
        except FileNotFoundError:
            self._record("stream_error", f"ffmpeg not found: {self.ffmpeg}")
            return False

        self._reader_thread = threading.Thread(target=self._read_output, name="rtsp-monitor-reader", daemon=True)
        self._reader_thread.start()
        if not self.first_frame_event.wait(self.startup_timeout):
            if self.process.poll() is not None:
                self._record("stream_exit", f"ffmpeg exited during startup rc={self.process.returncode}")
            else:
                self._record("stream_error", f"no frames within {self.startup_timeout:.1f}s")
            return False
        return True

    def stop(self) -> None:
        self.stopped_at = time.time()
        self._stop_event.set()
        if self.process and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=3.0)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=3.0)
        if self._reader_thread:
            self._reader_thread.join(timeout=1.0)
        self._record("stream_stop", f"frames={self.last_frame} max_gap={self.max_frame_gap:.3f}s")

    def snapshot(self) -> dict[str, Any]:
        now = time.time()
        return {
            "frames": self.last_frame,
            "seconds_since_last_frame": round(now - self.last_frame_at, 3),
            "max_frame_gap": round(self.max_frame_gap, 3),
            "ffmpeg_running": bool(self.process and self.process.poll() is None),
        }

    def affected(self) -> bool:
        if self.unexpected_exit:
            return True
        return self.max_frame_gap >= self.stall_threshold

    def _read_output(self) -> None:
        assert self.process is not None
        stdout_thread = threading.Thread(target=self._read_progress, daemon=True)
        stderr_thread = threading.Thread(target=self._read_stderr, daemon=True)
        stdout_thread.start()
        stderr_thread.start()
        while not self._stop_event.is_set():
            if self.process.poll() is not None:
                self.unexpected_exit = True
                self._record("stream_exit", f"ffmpeg exited rc={self.process.returncode}")
                return
            now = time.time()
            self.max_frame_gap = max(self.max_frame_gap, now - self.last_frame_at)
            time.sleep(0.2)

    def _read_progress(self) -> None:
        assert self.process is not None and self.process.stdout is not None
        for raw_line in self.process.stdout:
            line = raw_line.strip()
            if not line.startswith("frame="):
                continue
            try:
                frame = int(line.split("=", 1)[1])
            except ValueError:
                continue
            if frame > self.last_frame:
                now = time.time()
                self.max_frame_gap = max(self.max_frame_gap, now - self.last_frame_at)
                self.last_frame = frame
                self.last_frame_at = now
                self.first_frame_event.set()

    def _read_stderr(self) -> None:
        assert self.process is not None and self.process.stderr is not None
        for raw_line in self.process.stderr:
            line = raw_line.strip()
            if not line:
                continue
            if self.verbose:
                self._record("ffmpeg", line)
            elif any(token in line.lower() for token in ("error", "failed", "timeout", "end of file", "invalid")):
                self._record("ffmpeg", line)

    def _record(self, kind: str, detail: str) -> None:
        event = StreamEvent(time.time(), kind, detail)
        self.events.append(event)
        print(f"[stream] {kind}: {detail}")


def _mode_sequence(raw: str) -> list[str]:
    mode = raw.lower().strip()
    if mode == "both":
        return ["photo", "video", "photo"]
    if mode in {"photo", "video"}:
        return [mode]
    raise argparse.ArgumentTypeError("--mode must be photo, video, or both")


def _read_record_mode(client: MqttRequestClient, args) -> dict[str, Any]:
    rc, reply = _request_service(
        client,
        "viewlink_get_record_mode",
        {},
        args.timeout,
        require_viewlink_success=True,
        verbose=args.verbose,
    )
    if rc != 0:
        return {"ok": False, "rc": rc}
    data = reply.get("data", {}) if isinstance(reply, dict) else {}
    result = data.get("viewlink_result", {}) if isinstance(data, dict) else {}
    return {"ok": True, **result}


def _print_phase(label: str, monitor: RtspMonitor) -> None:
    print(f"[phase] {label}: {json.dumps(monitor.snapshot(), ensure_ascii=False)}")


def run_test(client: MqttRequestClient, args) -> int:
    monitor = RtspMonitor(
        ffmpeg=args.ffmpeg,
        rtsp_url=args.rtsp_url,
        rtsp_transport=args.rtsp_transport,
        startup_timeout=args.stream_startup_timeout,
        stall_threshold=args.stall_threshold,
        verbose=args.verbose_ffmpeg,
    )
    if not monitor.start():
        monitor.stop()
        return 50

    previous_photos = None
    try:
        print(f"warm up RTSP stream for {args.stream_warmup:.1f}s")
        time.sleep(args.stream_warmup)
        _print_phase("before_switch", monitor)

        if args.media_url and args.take_photo:
            try:
                previous_photos = set(_discover_photos(args))
                print(f"ViewLink media baseline photos={len(previous_photos)}")
            except Exception as exc:
                print(f"failed to read ViewLink media baseline: {exc}", file=sys.stderr)
                return 40

        before_mode = _read_record_mode(client, args)
        print(f"record_mode_before={json.dumps(before_mode, ensure_ascii=False)}")

        for mode in args.mode_sequence:
            rc = _switch_camera_mode(client, args, mode)
            if rc != 0:
                return rc
            _print_phase(f"after_switch_{mode}", monitor)

        after_mode = _read_record_mode(client, args)
        print(f"record_mode_after_switch={json.dumps(after_mode, ensure_ascii=False)}")

        if args.take_photo:
            rc = _take_photo(client, args)
            if rc != 0:
                return rc
            _print_phase("after_take_photo", monitor)

        print(f"observe RTSP stream for {args.stream_observe:.1f}s")
        time.sleep(args.stream_observe)
        _print_phase("after_observe", monitor)

        if args.media_url:
            rc = _download_photo(args, previous_photos)
            if rc != 0:
                return rc

        return 0
    finally:
        monitor.stop()
        summary = {
            "rtsp_url": args.rtsp_url,
            "frames": monitor.last_frame,
            "max_frame_gap_seconds": round(monitor.max_frame_gap, 3),
            "stall_threshold_seconds": args.stall_threshold,
            "unexpected_exit": monitor.unexpected_exit,
            "stream_affected": monitor.affected(),
            "events": [event.__dict__ for event in monitor.events],
        }
        print("\n[summary]")
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        if monitor.affected():
            print("result: RTSP stream was interrupted or stalled during the test")
        else:
            print("result: RTSP stream stayed continuous during the test")


def main() -> int:
    parser = argparse.ArgumentParser(description="Test whether ViewLink photo mode/capture affects RTSP stream")
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
    parser.add_argument("--mode", type=_mode_sequence, default=["photo"], help="photo, video, or both")
    parser.add_argument("--mode-settle", type=float, default=0.5)
    parser.add_argument("--require-record-mode", action="store_true")
    parser.add_argument("--take-photo", action="store_true")
    parser.add_argument("--photo-wait", type=float, default=3.0)
    parser.add_argument("--media-url", default="")
    parser.add_argument("--download-media", action="store_true")
    parser.add_argument("--output-dir", default="logs/viewlink_media")
    parser.add_argument("--media-timeout", type=float, default=10.0)
    parser.add_argument("--media-poll-timeout", type=float, default=15.0)
    parser.add_argument("--media-poll-interval", type=float, default=1.0)
    parser.add_argument("--media-max-depth", type=int, default=4)
    parser.add_argument("--rtsp-url", default="rtsp://192.168.1.119:554/main")
    parser.add_argument("--rtsp-transport", choices=["tcp", "udp"], default="tcp")
    parser.add_argument("--ffmpeg", default="ffmpeg")
    parser.add_argument("--stream-startup-timeout", type=float, default=8.0)
    parser.add_argument("--stream-warmup", type=float, default=5.0)
    parser.add_argument("--stream-observe", type=float, default=8.0)
    parser.add_argument("--stall-threshold", type=float, default=3.0)
    parser.add_argument("--skip-bind-check", action="store_true")
    parser.add_argument("--skip-viewlink-connect", action="store_true")
    parser.add_argument("--allow-local-mode-only", action="store_true")
    parser.add_argument("--quiet-stale", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--verbose-ffmpeg", action="store_true")
    args = parser.parse_args()

    args.host, args.port, args.sn = resolve_mqtt_settings(args)
    args.mode_sequence = args.mode
    args.verify_record_mode = True
    args.skip_record_mode_verify = False

    for name in (
        "mode_settle",
        "photo_wait",
        "media_poll_timeout",
        "media_poll_interval",
        "stream_startup_timeout",
        "stream_warmup",
        "stream_observe",
        "stall_threshold",
    ):
        if getattr(args, name) < 0:
            parser.error(f"--{name.replace('_', '-')} must be non-negative")
    if args.download_media and not args.media_url:
        parser.error("--download-media requires --media-url")

    client = MqttRequestClient(
        args.host,
        args.port,
        args.sn,
        qos=args.qos,
        quiet_stale=args.quiet_stale,
        client_id_prefix="edge_gateway_viewlink_stream_mode_impact",
    )
    if not client.connect(args.timeout):
        print(f"MQTT connect timeout: host={args.host} port={args.port}", file=sys.stderr)
        return 2

    try:
        rc = _check_bound(client, args.timeout, skip=args.skip_bind_check)
        if rc != 0:
            return rc
        rc = _ensure_viewlink_connected(client, args)
        if rc != 0:
            return rc
        return run_test(client, args)
    finally:
        client.disconnect()


if __name__ == "__main__":
    raise SystemExit(main())
