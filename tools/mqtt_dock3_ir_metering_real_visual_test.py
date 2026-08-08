#!/usr/bin/env python3
"""Real-gateway IR metering acceptance with visual RTSP evidence.

This tool only publishes existing cloud API service methods:
- ir_display_mode_set (optional, to switch the real stream to IR)
- ir_metering_mode_set
- ir_metering_point_set
- ir_metering_area_set
"""

from __future__ import annotations

import argparse
import html
import json
import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "tools"))

from mqtt_dock3_ir_metering_test import (  # noqa: E402
    Dock3IrMeteringClient,
    _data,
    _viewlink_result,
)
from mqtt_test_client import MqttRequestClient, result_code  # noqa: E402
from src.payload.dock3_payload import payload_index_from_config  # noqa: E402
from tool_config import add_config_argument, load_config, resolve_mqtt_settings  # noqa: E402


CLOUD_METHODS = {
    "ir_display_mode_set",
    "ir_metering_mode_set",
    "ir_metering_point_set",
    "ir_metering_area_set",
}


@dataclass(frozen=True)
class VisualCase:
    label: str
    method: str
    data: dict[str, Any]
    expect_success: bool
    expect_validation_reject: bool = False


@dataclass
class VisualResult:
    case: VisualCase
    rc: int
    reply: dict[str, Any] | None
    status: str
    detail: str
    snapshot: Path | None = None


class RealIrVisualClient(Dock3IrMeteringClient):
    def service(self, method: str, data: dict[str, Any], timeout: float):
        if method not in CLOUD_METHODS:
            raise ValueError(f"script may only publish existing cloud IR methods, got: {method}")
        return MqttRequestClient.request(
            self,
            method,
            data,
            timeout,
            channel="services",
            allowed_results=None,
            expected_status=None,
        )


def _payload_index(args) -> str:
    if args.payload_index:
        return args.payload_index
    return payload_index_from_config(load_config(args.config))


def _config_video_url(config: dict[str, Any]) -> str:
    payload = config.get("payload") if isinstance(config.get("payload"), dict) else {}
    devices = payload.get("devices") if isinstance(payload.get("devices"), dict) else {}
    gimbal = devices.get("gimbal") if isinstance(devices.get("gimbal"), dict) else {}
    channels = gimbal.get("channels") if isinstance(gimbal.get("channels"), dict) else {}
    video = channels.get("video") if isinstance(channels.get("video"), dict) else {}
    endpoint = str(video.get("endpoint") or "").strip()
    if endpoint:
        return endpoint

    live = config.get("livestream") if isinstance(config.get("livestream"), dict) else {}
    for key in ("ir", "infrared", "gimbal", "default"):
        value = live.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()

    sources = config.get("video_sources") if isinstance(config.get("video_sources"), dict) else {}
    for key in ("ir", "infrared", "gimbal"):
        value = sources.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _capture_snapshot(video_url: str, output_path: Path, ffmpeg_bin: str, timeout: float) -> tuple[int, str]:
    executable = shutil.which(ffmpeg_bin)
    if not executable and Path(ffmpeg_bin).is_file():
        executable = ffmpeg_bin
    if not executable:
        return 30, f"ffmpeg executable not found: {ffmpeg_bin}"

    output_path.parent.mkdir(parents=True, exist_ok=True)
    command = [executable, "-hide_banner", "-loglevel", "error", "-y"]
    if video_url.lower().startswith("rtsp://"):
        command.extend(["-rtsp_transport", "tcp"])
    command.extend(["-i", video_url, "-frames:v", "1", "-q:v", "2", str(output_path)])
    try:
        completed = subprocess.run(command, timeout=timeout, check=False)
    except subprocess.TimeoutExpired:
        return 31, f"snapshot capture timed out after {timeout:.1f}s"
    if completed.returncode != 0 or not output_path.is_file():
        return 32, f"snapshot capture failed: ffmpeg exit={completed.returncode}"
    return 0, str(output_path.resolve())


def _safe_name(value: str) -> str:
    keep = []
    for char in value.lower():
        keep.append(char if char.isalnum() else "_")
    return "".join(keep).strip("_") or "case"


def _frame_size(args) -> dict[str, int]:
    return {"width": args.frame_width, "height": args.frame_height}


def build_visual_cases(args) -> list[VisualCase]:
    payload_index = _payload_index(args)
    base = {
        "payload_index": payload_index,
        "camera_type": args.camera_type,
        "frame_size": _frame_size(args),
    }
    cases: list[VisualCase] = []

    for mode in args.metering_modes:
        payload = {**base, "mode": mode}
        if mode in {"periodic", "interval", "continuous"}:
            payload["interval_frames"] = args.interval_frames
        cases.append(VisualCase(f"metering mode {mode}", "ir_metering_mode_set", payload, True))

    cases.extend(
        [
            VisualCase(
                "point metering center",
                "ir_metering_point_set",
                {**base, "x": args.point_x, "y": args.point_y},
                True,
            ),
            VisualCase(
                "area metering rectangle",
                "ir_metering_area_set",
                {
                    **base,
                    "x": args.area_x,
                    "y": args.area_y,
                    "width": args.area_width,
                    "height": args.area_height,
                },
                True,
            ),
        ]
    )

    if not args.skip_invalid:
        cases.extend(
            [
                VisualCase(
                    "invalid point out of bounds",
                    "ir_metering_point_set",
                    {**base, "x": args.invalid_x, "y": args.invalid_y},
                    False,
                    True,
                ),
                VisualCase(
                    "invalid area out of bounds",
                    "ir_metering_area_set",
                    {
                        **base,
                        "x": args.invalid_area_x,
                        "y": args.invalid_area_y,
                        "width": args.invalid_area_width,
                        "height": args.invalid_area_height,
                    },
                    False,
                    True,
                ),
                VisualCase(
                    "invalid area non-positive size",
                    "ir_metering_area_set",
                    {
                        **base,
                        "x": args.area_x,
                        "y": args.area_y,
                        "width": args.invalid_small_area_width,
                        "height": args.invalid_small_area_height,
                    },
                    False,
                    True,
                ),
            ]
        )

    for extra in args.extra_payload_index:
        cases.append(
            VisualCase(
                f"multi-payload isolation {extra}",
                "ir_metering_point_set",
                {**base, "payload_index": extra, "x": args.point_x, "y": args.point_y},
                args.extra_payload_should_succeed,
            )
        )

    return cases


def evaluate_visual_case(case: VisualCase, rc: int, reply: dict[str, Any] | None) -> tuple[str, str]:
    if rc != 0:
        return "fail", f"mqtt client rc={rc}"

    code = result_code(reply)
    data = _data(reply)
    viewlink = _viewlink_result(reply)
    success = code == 0 and bool(viewlink.get("success", False))

    if case.expect_validation_reject:
        if code == 0:
            return "fail", "invalid input was accepted by the cloud method"
        return "ok", f"invalid input rejected, result={code}"

    if case.expect_success and not success:
        return "fail", f"expected gateway+ViewLink success, result={code}, viewlink={viewlink}"

    frames = viewlink.get("frame_hex")
    if case.expect_success and not (isinstance(frames, list) and frames):
        return "fail", "ViewLink success reply did not include raw protocol frame_hex"

    if case.method == "ir_metering_point_set" and case.expect_success and not isinstance(data.get("protocol_point"), dict):
        return "fail", "protocol_point missing"
    if case.method == "ir_metering_area_set" and case.expect_success and not isinstance(data.get("protocol_area"), dict):
        return "fail", "protocol_area missing"
    return "ok", f"result={code}, viewlink_success={viewlink.get('success')}"


def _write_report(output_dir: Path, video_url: str, results: list[VisualResult]) -> Path:
    rows = []
    for item in results:
        img = ""
        if item.snapshot:
            img = f'<img src="{html.escape(item.snapshot.name)}" alt="{html.escape(item.case.label)}">'
        focus = {
            "method": item.case.method,
            "data": item.case.data,
            "result": result_code(item.reply),
            "viewlink_result": _viewlink_result(item.reply),
            "protocol_point": _data(item.reply).get("protocol_point"),
            "protocol_area": _data(item.reply).get("protocol_area"),
        }
        rows.append(
            "<section>"
            f"<h2>{html.escape(item.status.upper())}: {html.escape(item.case.label)}</h2>"
            f"<p>{html.escape(item.detail)}</p>"
            f"{img}"
            f"<pre>{html.escape(json.dumps(focus, ensure_ascii=False, indent=2))}</pre>"
            "</section>"
        )
    document = """<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>Dock3 IR Metering Real Visual Report</title>
<style>
body { font-family: Arial, sans-serif; margin: 24px; color: #202124; }
section { border-top: 1px solid #d0d7de; padding: 16px 0; }
h1 { font-size: 22px; margin: 0 0 8px; }
h2 { font-size: 16px; margin: 0 0 8px; }
img { display: block; max-width: 960px; width: 100%; border: 1px solid #d0d7de; }
pre { overflow: auto; background: #f6f8fa; padding: 12px; font-size: 12px; }
</style>
</head>
<body>
"""
    document += f"<h1>Dock3 IR Metering Real Visual Report</h1><p>video_url={html.escape(video_url)}</p>"
    document += "\n".join(rows)
    document += "\n</body></html>\n"
    report = output_dir / "ir_metering_real_visual_report.html"
    report.write_text(document, encoding="utf-8")
    return report


def run_real_visual_ir_test(
    client: RealIrVisualClient,
    args,
    *,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> int:
    config = load_config(args.config)
    video_url = args.video_url or _config_video_url(config)
    if not video_url:
        print("real visual evidence requires --video-url or a configured payload video endpoint", file=sys.stderr)
        return 11

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    results: list[VisualResult] = []

    if args.switch_ir_display:
        display = VisualCase(
            "switch real stream to IR display",
            "ir_display_mode_set",
            {"payload_index": _payload_index(args), "mode": args.display_mode, "palette": args.palette},
            True,
        )
        print(f"running {display.label}: {display.method}")
        rc, reply = client.service(display.method, display.data, args.timeout)
        status, detail = evaluate_visual_case(display, rc, reply)
        print(f"[{status}] {display.label}: {detail}")
        results.append(VisualResult(display, rc, reply, status, detail))
        if status == "fail" and args.stop_on_failure:
            report = _write_report(output_dir, video_url, results)
            print(f"visual_report={report.resolve()}")
            return 6
        sleep_fn(args.after_wait)

    rc, detail = _capture_snapshot(video_url, output_dir / "00_before_ir_metering.jpg", args.ffmpeg, args.snapshot_timeout)
    before_snapshot = output_dir / "00_before_ir_metering.jpg" if rc == 0 else None
    print(f"[{'ok' if rc == 0 else 'fail'}] before snapshot: {detail}")
    if args.require_snapshots and rc != 0:
        return rc

    cases = build_visual_cases(args)
    for index, case in enumerate(cases, start=1):
        print(f"running {case.label}: {case.method}")
        rc, reply = client.service(case.method, case.data, args.timeout)
        status, detail = evaluate_visual_case(case, rc, reply)
        sleep_fn(args.after_wait)

        snapshot = None
        if args.capture_after_each:
            filename = f"{index:02d}_{_safe_name(case.label)}.jpg"
            snap_rc, snap_detail = _capture_snapshot(video_url, output_dir / filename, args.ffmpeg, args.snapshot_timeout)
            print(f"[{'ok' if snap_rc == 0 else 'fail'}] snapshot {case.label}: {snap_detail}")
            if snap_rc == 0:
                snapshot = output_dir / filename
            elif args.require_snapshots:
                status = "fail"
                detail = f"{detail}; snapshot failed: {snap_detail}"

        results.append(VisualResult(case, rc, reply, status, detail, snapshot))
        print(f"[{status}] {case.label}: {detail}")
        if args.verbose_reply:
            print(json.dumps(reply, ensure_ascii=False, indent=2))
        if status == "fail" and args.stop_on_failure:
            break

    if before_snapshot:
        results.insert(
            0,
            VisualResult(
                VisualCase("before IR metering baseline", "snapshot_only", {}, True),
                0,
                {"data": {"result": 0}},
                "ok",
                "baseline real IR/video frame captured before commands",
                before_snapshot,
            ),
        )

    report = _write_report(output_dir, video_url, results)
    failed = [item for item in results if item.status == "fail"]
    print(f"visual_report={report.resolve()}")
    print(
        "dock3 ir-metering real visual summary "
        f"(ok={sum(1 for item in results if item.status == 'ok')}, "
        f"fail={len(failed)}, total={len(results)}, video_url={video_url})"
    )

    if args.open_report:
        os.startfile(str(report.resolve()))  # type: ignore[attr-defined]

    if failed:
        print("ir-metering real visual failures:", file=sys.stderr)
        for item in failed:
            print(f"- {item.case.label} / {item.case.method}: {item.detail}", file=sys.stderr)
        return 6
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Test Dock3 IR metering on a real gateway and capture real visual evidence")
    add_config_argument(parser)
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--sn", default=None)
    parser.add_argument("--timeout", type=float, default=15.0)
    parser.add_argument("--qos", type=int, choices=[0, 1, 2], default=1)
    parser.add_argument("--quiet-stale", action="store_true")
    parser.add_argument("--fail-on-stale", action="store_true")
    parser.add_argument("--payload-index", default=None, help="Dock3 payload index, for example 52-0-0.")
    parser.add_argument("--extra-payload-index", action="append", default=[], help="Repeat for second/third payload isolation checks.")
    parser.add_argument("--extra-payload-should-succeed", action="store_true")
    parser.add_argument("--camera-type", default="ir")
    parser.add_argument("--frame-width", type=int, default=1280)
    parser.add_argument("--frame-height", type=int, default=720)
    parser.add_argument(
        "--metering-modes",
        nargs="+",
        default=["point", "area", "full_screen", "periodic", "stop"],
        help="Values sent to existing ir_metering_mode_set; no synthetic method is used.",
    )
    parser.add_argument("--interval-frames", type=int, default=4)
    parser.add_argument("--point-x", type=float, default=0.5)
    parser.add_argument("--point-y", type=float, default=0.5)
    parser.add_argument("--area-x", type=float, default=0.25)
    parser.add_argument("--area-y", type=float, default=0.25)
    parser.add_argument("--area-width", type=float, default=0.5)
    parser.add_argument("--area-height", type=float, default=0.5)
    parser.add_argument("--invalid-x", type=float, default=1.2)
    parser.add_argument("--invalid-y", type=float, default=-0.1)
    parser.add_argument("--invalid-area-x", type=float, default=0.8)
    parser.add_argument("--invalid-area-y", type=float, default=0.8)
    parser.add_argument("--invalid-area-width", type=float, default=0.5)
    parser.add_argument("--invalid-area-height", type=float, default=0.5)
    parser.add_argument("--invalid-small-area-width", type=float, default=0.0)
    parser.add_argument("--invalid-small-area-height", type=float, default=-0.1)
    parser.add_argument("--skip-invalid", action="store_true")
    parser.add_argument("--video-url", default="", help="Real IR/thermal stream, e.g. rtsp://192.168.1.119:554/main")
    parser.add_argument("--ffmpeg", default="ffmpeg")
    parser.add_argument("--snapshot-timeout", type=float, default=20.0)
    parser.add_argument("--output-dir", default=str(PROJECT_ROOT / "logs" / "ir_metering_real_visual"))
    parser.add_argument("--after-wait", type=float, default=1.0)
    parser.add_argument("--capture-after-each", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--require-snapshots", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--switch-ir-display", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--display-mode", default="ir")
    parser.add_argument("--palette", default="white_hot")
    parser.add_argument("--stop-on-failure", action="store_true")
    parser.add_argument("--verbose-reply", action="store_true")
    parser.add_argument("--open-report", action="store_true")
    args = parser.parse_args()
    args.host, args.port, args.sn = resolve_mqtt_settings(args)

    if args.after_wait < 0:
        parser.error("--after-wait must be non-negative")
    if args.interval_frames <= 0:
        parser.error("--interval-frames must be positive")

    client = RealIrVisualClient(
        args.host,
        args.port,
        args.sn,
        args.qos,
        quiet_stale=args.quiet_stale,
        client_id_prefix="edge_gateway_ir_metering_real_visual",
        subscribe_events=True,
    )
    if not client.connect(args.timeout):
        print("MQTT connect timeout", file=sys.stderr)
        return 2

    try:
        rc = run_real_visual_ir_test(client, args)
        if rc != 0:
            return rc

        stale_total = client.stale_total()
        if args.fail_on_stale and stale_total:
            print(f"stale replies observed: {client.stats}", file=sys.stderr)
            return 10
        print(
            "dock3 ir-metering real visual test ok "
            f"(accepted={client.stats['accepted']}, events={client.stats['events']}, "
            f"ignored_total={stale_total})"
        )
        return 0
    finally:
        client.disconnect()


if __name__ == "__main__":
    raise SystemExit(main())
