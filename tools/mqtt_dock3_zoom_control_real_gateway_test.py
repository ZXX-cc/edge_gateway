#!/usr/bin/env python3
"""Real-gateway Dock3 zoom control acceptance test.

This tool is intentionally limited to existing Dock3 cloud API services
methods:

    camera_focal_length_set
    camera_frame_zoom

It publishes to the running gateway's MQTT services topic and waits for the
matching services_reply.  It must not call internal ViewLink diagnostic methods
such as viewlink_zoom_in or viewlink_connect.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "tools"))

from mqtt_test_client import MqttRequestClient, result_code
from src.payload.dock3_payload import payload_index_from_config
from tool_config import add_config_argument, load_config, resolve_mqtt_settings


CLOUD_METHODS = {"camera_focal_length_set", "camera_frame_zoom"}


def _payload_index(args: argparse.Namespace) -> str:
    if args.payload_index:
        return args.payload_index
    return payload_index_from_config(load_config(args.config))


def _reply_data(reply: Any) -> dict[str, Any]:
    if not isinstance(reply, dict):
        return {}
    data = reply.get("data")
    return data if isinstance(data, dict) else {}


def _reply_summary(reply: Any) -> dict[str, Any]:
    data = _reply_data(reply)
    return {
        "method": reply.get("method") if isinstance(reply, dict) else None,
        "tid": reply.get("tid") if isinstance(reply, dict) else None,
        "result": result_code(reply),
        "msg": (reply.get("msg") if isinstance(reply, dict) else "") or data.get("failure_message") or data.get("msg") or "",
        "data": data,
    }


def _base_camera_payload(args: argparse.Namespace, camera_type: str | None = None) -> dict[str, Any]:
    return {
        "payload_index": _payload_index(args),
        "camera_index": args.camera_index,
        "camera_type": camera_type or args.camera_type,
    }


def build_focal_length_payload(args: argparse.Namespace, *, ratio: float | None = None, camera_type: str | None = None) -> dict[str, Any]:
    target_ratio = args.zoom_ratio if ratio is None else ratio
    return {
        **_base_camera_payload(args, camera_type),
        "zoom_factor": target_ratio,
        "zoom_ratio": target_ratio,
    }


def build_frame_zoom_payload(args: argparse.Namespace, *, camera_type: str | None = None) -> dict[str, Any]:
    return {
        **_base_camera_payload(args, camera_type),
        "locked": args.locked,
        "zoom_ratio": args.zoom_ratio,
        "centering_enabled": args.centering_enabled,
        "allow_zoom_without_center": args.allow_zoom_without_center,
        "normalized_box": {
            "x": args.x,
            "y": args.y,
            "width": args.width,
            "height": args.height,
        },
    }


def _request_cloud_method(
    client: MqttRequestClient,
    method: str,
    payload: dict[str, Any],
    args: argparse.Namespace,
) -> tuple[int, dict[str, Any] | None]:
    if method not in CLOUD_METHODS:
        raise ValueError(f"method is not allowed for this acceptance test: {method}")
    return client.request(
        method,
        payload,
        args.timeout,
        channel="services",
        allowed_results=None,
        verbose=args.verbose,
    )


def _pipeline_steps(reply: Any) -> list[str]:
    pipeline = _reply_data(reply).get("software_pipeline")
    if not isinstance(pipeline, list):
        return []
    return [str(step.get("step")) for step in pipeline if isinstance(step, dict)]


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


def _append_result(results: list[dict[str, Any]], name: str, ok: bool, detail: dict[str, Any]) -> None:
    results.append({"name": name, "ok": ok, "detail": detail})
    print(f"[{'PASS' if ok else 'FAIL'}] {name}")
    print(json.dumps(detail, ensure_ascii=False, indent=2))


def _run_focal_check(
    client: MqttRequestClient,
    args: argparse.Namespace,
    results: list[dict[str, Any]],
    *,
    camera_type: str,
    ratio: float,
    expect_success: bool,
    name: str,
) -> int:
    method = "camera_focal_length_set"
    payload = build_focal_length_payload(args, ratio=ratio, camera_type=camera_type)
    rc, reply = _request_cloud_method(client, method, payload, args)
    success = rc == 0 and result_code(reply) == 0
    ok = success if expect_success else (rc == 0 and result_code(reply) != 0)
    _append_result(
        results,
        name,
        ok,
        {
            "cloud_method": method,
            "request_data": payload,
            "reply": _reply_summary(reply),
            "expect_success": expect_success,
        },
    )
    if not ok:
        return rc or 10
    return 0


def _run_frame_zoom_check(client: MqttRequestClient, args: argparse.Namespace, results: list[dict[str, Any]]) -> int:
    method = "camera_frame_zoom"
    payload = build_frame_zoom_payload(args)
    snapshot_dir = Path(args.snapshot_dir)

    if args.video_url and args.capture_snapshots:
        rc = _capture_snapshot(args.video_url, snapshot_dir / "frame_zoom_before.jpg", args.ffmpeg, args.snapshot_timeout)
        if rc != 0 and args.require_snapshots:
            return rc

    rc, reply = _request_cloud_method(client, method, payload, args)
    success = rc == 0 and result_code(reply) == 0
    steps = _pipeline_steps(reply)
    required_steps_ok = True
    if args.require_center_track:
        required_steps_ok = required_steps_ok and "gimbal_center_track" in steps
    if args.require_zoom_to:
        required_steps_ok = required_steps_ok and "camera_zoom_to" in steps
    ok = success and required_steps_ok
    _append_result(
        results,
        "camera_frame_zoom_box_controls_gimbal_and_zoom",
        ok,
        {
            "cloud_method": method,
            "request_data": payload,
            "reply": _reply_summary(reply),
            "pipeline_steps": steps,
            "required_center_track": args.require_center_track,
            "required_zoom_to": args.require_zoom_to,
        },
    )
    if not ok:
        return rc or 20

    if args.video_url and args.after_wait > 0:
        time.sleep(args.after_wait)
    if args.video_url and args.capture_snapshots:
        rc = _capture_snapshot(args.video_url, snapshot_dir / "frame_zoom_after.jpg", args.ffmpeg, args.snapshot_timeout)
        if rc != 0 and args.require_snapshots:
            return rc
    return 0


def run_zoom_acceptance(client: MqttRequestClient, args: argparse.Namespace) -> int:
    results: list[dict[str, Any]] = []

    if args.scenario in {"normal", "focal"}:
        rc = _run_focal_check(
            client,
            args,
            results,
            camera_type=args.camera_type,
            ratio=args.zoom_ratio,
            expect_success=True,
            name="camera_focal_length_set_zoom_factor_effective",
        )
        if rc != 0:
            return rc

    if args.scenario in {"normal", "frame"}:
        rc = _run_frame_zoom_check(client, args, results)
        if rc != 0:
            return rc

    if args.scenario in {"normal", "camera-type"}:
        for camera_type in args.camera_types:
            rc = _run_focal_check(
                client,
                args,
                results,
                camera_type=camera_type,
                ratio=args.camera_type_zoom_ratio,
                expect_success=True,
                name=f"camera_type_{camera_type}_routes_via_cloud_method",
            )
            if rc != 0:
                return rc

    if args.scenario in {"overrange", "normal-overrange"}:
        rc = _run_focal_check(
            client,
            args,
            results,
            camera_type=args.camera_type,
            ratio=args.overrange_zoom_ratio,
            expect_success=False,
            name="overrange_zoom_factor_returns_failure",
        )
        if rc != 0:
            return rc

    if args.scenario == "disconnected":
        for method, payload in (
            ("camera_focal_length_set", build_focal_length_payload(args)),
            ("camera_frame_zoom", build_frame_zoom_payload(args)),
        ):
            rc, reply = _request_cloud_method(client, method, payload, args)
            ok = rc == 0 and result_code(reply) != 0
            _append_result(
                results,
                f"{method}_returns_failure_when_payload_disconnected",
                ok,
                {"cloud_method": method, "request_data": payload, "reply": _reply_summary(reply)},
            )
            if not ok:
                return rc or 40

    summary = {
        "scenario": args.scenario,
        "cloud_methods_used": sorted(CLOUD_METHODS),
        "forbidden_methods_not_used": ["viewlink_initialize", "viewlink_connect", "viewlink_zoom_in", "viewlink_zoom_out", "viewlink_stop_zoom"],
        "mqtt": {"host": args.host, "port": args.port, "sn": args.sn},
        "accepted_replies": client.stats["accepted"],
        "stale_total": client.stale_total(),
        "results": results,
    }
    print("\n[summary]")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


def _parse_camera_types(text: str) -> list[str]:
    values = [item.strip().lower() for item in str(text or "").split(",") if item.strip()]
    return values or ["zoom", "infrared"]


def _dry_run(args: argparse.Namespace) -> None:
    methods: list[list[Any]] = []
    if args.scenario in {"normal", "focal"}:
        methods.append(["camera_focal_length_set", build_focal_length_payload(args)])
    if args.scenario in {"normal", "frame"}:
        methods.append(["camera_frame_zoom", build_frame_zoom_payload(args)])
    if args.scenario in {"normal", "camera-type"}:
        for camera_type in args.camera_types:
            methods.append([
                "camera_focal_length_set",
                build_focal_length_payload(args, ratio=args.camera_type_zoom_ratio, camera_type=camera_type),
            ])
    if args.scenario in {"overrange", "normal-overrange"}:
        methods.append([
            "camera_focal_length_set",
            build_focal_length_payload(args, ratio=args.overrange_zoom_ratio),
        ])
    if args.scenario == "disconnected":
        methods.extend([
            ["camera_focal_length_set", build_focal_length_payload(args)],
            ["camera_frame_zoom", build_frame_zoom_payload(args)],
        ])
    print(json.dumps({
        "dry_run": True,
        "mqtt": {"host": args.host, "port": args.port, "sn": args.sn},
        "channel": "services",
        "cloud_methods": methods,
        "forbidden_methods_not_used": [
            "viewlink_initialize",
            "viewlink_connect",
            "viewlink_zoom_in",
            "viewlink_zoom_out",
            "viewlink_stop_zoom",
        ],
    }, ensure_ascii=False, indent=2))


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run real-gateway zoom acceptance checks through Dock3 cloud API services only."
    )
    add_config_argument(parser)
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--sn", default=None)
    parser.add_argument("--timeout", type=float, default=20.0)
    parser.add_argument("--qos", type=int, choices=[0, 1, 2], default=1)
    parser.add_argument("--quiet-stale", action="store_true")
    parser.add_argument("--fail-on-stale", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--payload-index", default="", help="Dock3 payload index, for example 52-0-0.")
    parser.add_argument("--camera-index", type=int, default=0)
    parser.add_argument("--camera-type", default="zoom")
    parser.add_argument("--zoom-ratio", type=float, default=4.0)
    parser.add_argument("--camera-types", default="zoom,infrared")
    parser.add_argument("--camera-type-zoom-ratio", type=float, default=2.0)
    parser.add_argument("--overrange-zoom-ratio", type=float, default=999.0)
    parser.add_argument("--x", type=float, default=0.35)
    parser.add_argument("--y", type=float, default=0.30)
    parser.add_argument("--width", type=float, default=0.20)
    parser.add_argument("--height", type=float, default=0.20)
    parser.add_argument("--locked", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--centering-enabled", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--allow-zoom-without-center", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument(
        "--scenario",
        choices=["normal", "focal", "frame", "camera-type", "overrange", "normal-overrange", "disconnected"],
        default="normal",
    )
    parser.add_argument("--video-url", default="", help="Real RTSP/HTTP stream used to capture before/after evidence.")
    parser.add_argument("--snapshot-dir", default=str(PROJECT_ROOT / "logs" / "dock3_zoom_control"))
    parser.add_argument("--capture-snapshots", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--require-snapshots", action=argparse.BooleanOptionalAction, default=False)
    parser.add_argument("--after-wait", type=float, default=1.0)
    parser.add_argument("--ffmpeg", default="ffmpeg")
    parser.add_argument("--snapshot-timeout", type=float, default=20.0)
    parser.add_argument("--require-center-track", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--require-zoom-to", action=argparse.BooleanOptionalAction, default=True)
    args = parser.parse_args()
    args.host, args.port, args.sn = resolve_mqtt_settings(args)
    args.camera_types = _parse_camera_types(args.camera_types)

    if args.zoom_ratio < 1.0:
        parser.error("--zoom-ratio must be >= 1.0")
    if args.camera_type_zoom_ratio < 1.0:
        parser.error("--camera-type-zoom-ratio must be >= 1.0")
    if args.overrange_zoom_ratio <= args.zoom_ratio:
        parser.error("--overrange-zoom-ratio must be greater than --zoom-ratio")
    for name in ("x", "y", "width", "height"):
        value = getattr(args, name)
        if not 0.0 <= value <= 1.0:
            parser.error(f"--{name} must be between 0 and 1")
    if args.width <= 0 or args.height <= 0:
        parser.error("--width and --height must be positive")
    if args.after_wait < 0 or args.snapshot_timeout <= 0:
        parser.error("--after-wait must be non-negative and --snapshot-timeout must be positive")

    if args.dry_run:
        _dry_run(args)
        return 0

    client = MqttRequestClient(
        args.host,
        args.port,
        args.sn,
        args.qos,
        quiet_stale=args.quiet_stale,
        client_id_prefix="edge_gateway_dock3_zoom_control_real",
    )
    if not client.connect(args.timeout):
        print(f"MQTT connect timeout: host={args.host} port={args.port}", file=sys.stderr)
        return 2

    try:
        rc = run_zoom_acceptance(client, args)
        if rc != 0:
            return rc
        if args.fail_on_stale and client.stale_total():
            print(f"stale replies observed: {client.stats}", file=sys.stderr)
            return 9
        print(
            "real gateway Dock3 zoom control test ok "
            f"(accepted={client.stats['accepted']}, stale_total={client.stale_total()}, "
            f"unrelated_replies={client.unrelated_reply_total()})"
        )
        return 0
    finally:
        client.disconnect()


if __name__ == "__main__":
    raise SystemExit(main())
