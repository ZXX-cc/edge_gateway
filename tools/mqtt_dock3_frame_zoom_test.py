#!/usr/bin/env python3
"""Dock3 camera_frame_zoom field test over the gateway services topic.

This tool intentionally uses only the retained cloud-facing services method
``camera_frame_zoom``.  The gateway then routes the request to the current
ViewLink SDK adapter, which should center the selected box with target tracking
and then call ``zoom_to``.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "tools"))

from mqtt_test_client import MqttRequestClient, result_code
from src.payload.dock3_payload import payload_index_from_config
from src.utils.dji_error_codes import DOCK_SYSTEM_ERROR
from tool_config import add_config_argument, load_config, resolve_mqtt_settings


def _reply_data(reply: Any) -> dict[str, Any]:
    if not isinstance(reply, dict):
        return {}
    data = reply.get("data")
    return data if isinstance(data, dict) else {}


def _pipeline_steps(reply: Any) -> list[dict[str, Any]]:
    pipeline = _reply_data(reply).get("software_pipeline")
    return pipeline if isinstance(pipeline, list) else []


def _capture_snapshot(video_url: str, output_path: Path, ffmpeg_bin: str, timeout: float) -> int:
    executable = shutil.which(ffmpeg_bin)
    if not executable and Path(ffmpeg_bin).is_file():
        executable = ffmpeg_bin
    if not executable:
        print(f"ffmpeg executable not found: {ffmpeg_bin}", file=sys.stderr)
        return 30

    output_path.parent.mkdir(parents=True, exist_ok=True)
    command = [executable, "-hide_banner", "-loglevel", "error", "-y"]
    if video_url.lower().startswith("rtsp://"):
        command.extend(["-rtsp_transport", "tcp"])
    command.extend(["-i", video_url, "-frames:v", "1", "-q:v", "2", str(output_path)])
    try:
        completed = subprocess.run(command, timeout=timeout, check=False)
    except subprocess.TimeoutExpired:
        print(f"snapshot capture timed out after {timeout:.1f}s: {output_path}", file=sys.stderr)
        return 31
    if completed.returncode != 0 or not output_path.is_file():
        print(f"snapshot capture failed: ffmpeg exit={completed.returncode}, output={output_path}", file=sys.stderr)
        return 32
    print(f"snapshot={output_path.resolve()}")
    return 0


def _payload_index(args) -> str:
    if args.payload_index:
        return args.payload_index
    return payload_index_from_config(load_config(args.config))


def build_frame_zoom_payload(args) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "payload_index": _payload_index(args),
        "camera_index": args.camera_index,
        "camera_type": args.camera_type,
        "locked": args.locked,
        "zoom_ratio": args.zoom_ratio,
        "centering_enabled": args.centering_enabled,
        "allow_zoom_without_center": args.allow_zoom_without_center,
    }

    if args.center_mode:
        payload["center_mode"] = args.center_mode

    if args.normalized:
        payload["normalized_box"] = {
            "x": args.x,
            "y": args.y,
            "width": args.width,
            "height": args.height,
        }
    else:
        payload.update({
            "x": int(round(args.x)),
            "y": int(round(args.y)),
            "width": int(round(args.width)),
            "height": int(round(args.height)),
            "frame_size": {
                "width": args.frame_width,
                "height": args.frame_height,
            },
        })
    return payload


def _is_connecting(reply: Any) -> bool:
    data = _reply_data(reply)
    viewlink = data.get("viewlink_result")
    return (
        result_code(reply) == DOCK_SYSTEM_ERROR
        and isinstance(viewlink, dict)
        and bool(viewlink.get("connecting") or viewlink.get("stage") == "connecting")
    )


def _validate_frame_zoom_reply(reply: Any, args) -> int:
    data = _reply_data(reply)
    actual = result_code(reply)
    if actual != 0:
        message = ""
        if isinstance(reply, dict):
            message = str(data.get("failure_message") or reply.get("msg") or "")
        print(
            f"camera_frame_zoom failed: result={actual}, "
            f"failure_step={data.get('failure_step')}, message={message}",
            file=sys.stderr,
        )
        return 4

    steps = _pipeline_steps(reply)
    step_names = [str(step.get("step")) for step in steps if isinstance(step, dict)]
    if args.require_center_track and "gimbal_center_track" not in step_names:
        print(f"missing gimbal_center_track in software_pipeline: {step_names}", file=sys.stderr)
        return 5
    if args.require_zoom_to and "camera_zoom_to" not in step_names:
        print(f"missing camera_zoom_to in software_pipeline: {step_names}", file=sys.stderr)
        return 6

    zoom_step = next((step for step in steps if isinstance(step, dict) and step.get("step") == "camera_zoom_to"), {})
    if args.require_zoom_to and zoom_step.get("status") != "ok":
        print(f"camera_zoom_to did not report ok: {zoom_step}", file=sys.stderr)
        return 7

    print(
        "camera_frame_zoom ok "
        f"(zoom_ratio={data.get('zoom_ratio')}, center_result={data.get('center_result')}, "
        f"steps={step_names})"
    )
    return 0


def run_frame_zoom(
    client,
    args,
    *,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> int:
    payload = build_frame_zoom_payload(args)
    allowed = {0, DOCK_SYSTEM_ERROR} if args.retry_connecting else {0}
    snapshot_dir = Path(args.snapshot_dir)

    if args.video_url and args.capture_before:
        rc = _capture_snapshot(
            args.video_url,
            snapshot_dir / f"{args.snapshot_prefix}_before.jpg",
            args.ffmpeg,
            args.snapshot_timeout,
        )
        if rc != 0 and args.require_snapshots:
            return rc

    rc, reply = client.request(
        "camera_frame_zoom",
        payload,
        args.timeout,
        channel="services",
        allowed_results=allowed,
    )
    if rc != 0:
        return rc

    if args.retry_connecting and _is_connecting(reply):
        print(
            "ViewLink is connecting in background; waiting "
            f"{args.connect_retry_delay:.1f}s before retry"
        )
        sleep_fn(args.connect_retry_delay)
        rc, reply = client.request(
            "camera_frame_zoom",
            payload,
            args.timeout,
            channel="services",
            allowed_results={0},
        )
        if rc != 0:
            return rc

    rc = _validate_frame_zoom_reply(reply, args)
    if rc != 0:
        return rc

    if args.video_url and args.after_wait > 0:
        sleep_fn(args.after_wait)
    if args.video_url and args.capture_after:
        rc = _capture_snapshot(
            args.video_url,
            snapshot_dir / f"{args.snapshot_prefix}_after.jpg",
            args.ffmpeg,
            args.snapshot_timeout,
        )
        if rc != 0 and args.require_snapshots:
            return rc
    if args.video_url:
        print(f"snapshot_dir={snapshot_dir.resolve()}")
    return 0


def _dry_run(args) -> None:
    print(json.dumps({
        "dry_run": True,
        "channel": "services",
        "method": "camera_frame_zoom",
        "data": build_frame_zoom_payload(args),
        "video_url": args.video_url,
        "snapshots": {
            "before": str(Path(args.snapshot_dir) / f"{args.snapshot_prefix}_before.jpg"),
            "after": str(Path(args.snapshot_dir) / f"{args.snapshot_prefix}_after.jpg"),
        } if args.video_url else None,
        "expected_pipeline": [
            "normalize_selection",
            "compute_center_offset",
            "gimbal_center_track",
            "camera_zoom_to",
        ],
    }, ensure_ascii=False, indent=2))


def main() -> int:
    parser = argparse.ArgumentParser(description="测试 Dock3 camera_frame_zoom 框选变焦")
    add_config_argument(parser)
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--sn", default=None)
    parser.add_argument("--timeout", type=float, default=20.0)
    parser.add_argument("--qos", type=int, choices=[0, 1, 2], default=1)
    parser.add_argument("--payload-index", default="")
    parser.add_argument("--camera-index", type=int, default=0)
    parser.add_argument("--camera-type", default="zoom")
    parser.add_argument("--normalized", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--x", type=float, default=0.35, help="box left: normalized 0..1 or pixels")
    parser.add_argument("--y", type=float, default=0.30, help="box top: normalized 0..1 or pixels")
    parser.add_argument("--width", type=float, default=0.20, help="box width: normalized 0..1 or pixels")
    parser.add_argument("--height", type=float, default=0.20, help="box height: normalized 0..1 or pixels")
    parser.add_argument("--frame-width", type=int, default=1920)
    parser.add_argument("--frame-height", type=int, default=1080)
    parser.add_argument("--zoom-ratio", type=float, default=4.0)
    parser.add_argument("--locked", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--centering-enabled", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--allow-zoom-without-center", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--center-mode", default="", help="override gateway center_mode, e.g. track or move")
    parser.add_argument("--video-url", default="", help="RTSP/HTTP video URL used to capture real before/after frames")
    parser.add_argument("--snapshot-dir", default=str(PROJECT_ROOT / "logs" / "viewlink_frame_zoom"))
    parser.add_argument("--snapshot-prefix", default="frame_zoom")
    parser.add_argument("--capture-before", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--capture-after", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--require-snapshots", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--after-wait", type=float, default=1.0)
    parser.add_argument("--ffmpeg", default="ffmpeg")
    parser.add_argument("--snapshot-timeout", type=float, default=20.0)
    parser.add_argument("--retry-connecting", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--connect-retry-delay", type=float, default=4.0)
    parser.add_argument("--require-center-track", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--require-zoom-to", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--quiet-stale", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    args.host, args.port, args.sn = resolve_mqtt_settings(args)

    if args.normalized:
        for name in ("x", "y", "width", "height"):
            value = getattr(args, name)
            if not 0.0 <= value <= 1.0:
                parser.error(f"--{name} must be between 0 and 1 when --normalized is used")
    if args.width <= 0 or args.height <= 0:
        parser.error("--width and --height must be positive")
    if args.zoom_ratio < 1.0:
        parser.error("--zoom-ratio must be >= 1.0")
    if args.connect_retry_delay < 0:
        parser.error("--connect-retry-delay must be non-negative")
    if args.after_wait < 0 or args.snapshot_timeout <= 0:
        parser.error("--after-wait must be non-negative and --snapshot-timeout must be positive")

    if args.dry_run:
        _dry_run(args)
        return 0

    client = MqttRequestClient(
        args.host,
        args.port,
        args.sn,
        qos=args.qos,
        quiet_stale=args.quiet_stale,
        client_id_prefix="edge_gateway_frame_zoom",
    )
    if not client.connect(args.timeout):
        print("MQTT connect timeout", file=sys.stderr)
        return 2
    try:
        return run_frame_zoom(client, args)
    finally:
        client.disconnect()


if __name__ == "__main__":
    raise SystemExit(main())
