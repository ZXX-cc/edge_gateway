#!/usr/bin/env python3
"""Test camera focus control through the existing Dock3 cloud API services path.

This script intentionally sends only the routed cloud API methods:

    camera_focus_mode_set
    camera_focus_value_set
    camera_point_focus_action

It does not call camera/controller internals and does not introduce any
gateway-only method. Use it against a running real gateway and MQTT broker.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "tools"))

from mqtt_test_client import MqttRequestClient, result_code
from src.payload.dock3_payload import payload_index_from_config
from src.utils.dji_error_codes import (
    DEVICE_STATE_UNSUPPORTED,
    DOCK_COMMUNICATION_ERROR,
    DOCK_SYSTEM_ERROR,
    INVALID_COMMAND_FORMAT,
    PARAMETER_SET_FAILED,
)
from tool_config import add_config_argument, load_config, resolve_mqtt_settings


FAILURE_RESULTS = {
    DEVICE_STATE_UNSUPPORTED,
    DOCK_COMMUNICATION_ERROR,
    DOCK_SYSTEM_ERROR,
    INVALID_COMMAND_FORMAT,
    PARAMETER_SET_FAILED,
}
FOCUS_MODES = ("manual_focus", "auto_focus", "continuous_auto_focus")
CLOUD_METHODS = {
    "camera_focus_mode_set",
    "camera_focus_value_set",
    "camera_point_focus_action",
}


@dataclass(frozen=True)
class FocusCase:
    label: str
    method: str
    data: dict[str, Any]
    expect_success: bool
    note: str = ""
    allowed_results: set[int] | None = None


def _payload_index(args: argparse.Namespace) -> str:
    if args.payload_index:
        return args.payload_index
    return payload_index_from_config(load_config(args.config))


def _base_payload(args: argparse.Namespace) -> dict[str, Any]:
    return {
        "payload_index": _payload_index(args),
        "camera_index": args.camera_index,
        "camera_type": args.camera_type,
    }


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
    for key in ("zoom", "normal", "gimbal", "default"):
        value = live.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()

    sources = config.get("video_sources") if isinstance(config.get("video_sources"), dict) else {}
    for key in ("zoom", "normal", "gimbal"):
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


def _focus_values(args: argparse.Namespace) -> list[float]:
    raw = str(args.focus_values or "").strip()
    if not raw:
        return [float(args.focus_distance)]
    values: list[float] = []
    for item in raw.split(","):
        text = item.strip()
        if text:
            values.append(float(text))
    return values or [float(args.focus_distance)]


def _focus_reset_value(args: argparse.Namespace) -> float:
    if args.focus_reset_distance is None:
        return float(args.focus_distance)
    return float(args.focus_reset_distance)


def build_cases(args: argparse.Namespace) -> list[FocusCase]:
    base = _base_payload(args)
    cases: list[FocusCase] = []

    cases.append(
        FocusCase(
            "对焦模式-manual_focus",
            "camera_focus_mode_set",
            {**base, "mode": "manual_focus"},
            expect_success=True,
            note="先切手动对焦，后续绝对对焦值才容易产生可观察的镜头动作",
            allowed_results={0},
        )
    )

    for value in _focus_values(args):
        cases.append(
            FocusCase(
                f"绝对对焦值-{value:g}",
                "camera_focus_value_set",
                {
                    **base,
                    "focus_distance": value,
                    "focus_speed": args.focus_speed,
                    "focus_duration": args.focus_duration,
                },
                expect_success=not args.allow_viewlink_focus_limitations,
                note=(
                    "验证绝对对焦值是否真实生效；不允许只做 focus_in/focus_out 方向微调假通过"
                ),
                allowed_results={DEVICE_STATE_UNSUPPORTED} if args.allow_viewlink_focus_limitations else {0},
            )
        )

    cases.append(
        FocusCase(
            "对焦复位-切换自动前",
            "camera_focus_value_set",
            {
                **base,
                "focus_distance": _focus_reset_value(args),
                "focus_speed": args.focus_speed,
                "focus_duration": args.focus_reset_wait,
            },
            expect_success=not args.allow_viewlink_focus_limitations,
            note=(
                "手动对焦套件结束后的复位隔离步；不能直接从 manual_focus 切到 auto_focus"
            ),
            allowed_results={DEVICE_STATE_UNSUPPORTED} if args.allow_viewlink_focus_limitations else {0},
        )
    )

    cases.append(
        FocusCase(
            "对焦模式-auto_focus",
            "camera_focus_mode_set",
            {**base, "mode": "auto_focus"},
            expect_success=True,
            note="切回自动对焦，后续点对焦坐标才会触发自动聚焦逻辑",
            allowed_results={0},
        )
    )

    cases.extend(
        [
            FocusCase(
                "点对焦坐标",
                "camera_point_focus_action",
                {**base, "x": args.focus_x, "y": args.focus_y},
                expect_success=not args.allow_viewlink_focus_limitations,
                note="验证点对焦坐标是否真正通过上云方法下发到负载",
                allowed_results={DEVICE_STATE_UNSUPPORTED} if args.allow_viewlink_focus_limitations else {0},
            ),
            FocusCase(
                "对焦模式-continuous_auto_focus",
                "camera_focus_mode_set",
                {**base, "mode": "continuous_auto_focus"},
                expect_success=False,
                note="验证连续自动对焦枚举；当前 ViewLink 设备无独立 AFC 指令则应明确返回不支持",
                allowed_results={DEVICE_STATE_UNSUPPORTED},
            ),
            FocusCase(
                "非法对焦模式",
                "camera_focus_mode_set",
                {**base, "mode": args.invalid_focus_mode},
                expect_success=False,
            ),
            FocusCase(
                "非法对焦值-过大",
                "camera_focus_value_set",
                {**base, "focus_distance": args.invalid_focus_distance},
                expect_success=False,
            ),
            FocusCase(
                "非法点对焦坐标-x越界",
                "camera_point_focus_action",
                {**base, "x": args.invalid_focus_x, "y": args.focus_y},
                expect_success=False,
            ),
            FocusCase(
                "非法点对焦坐标-y越界",
                "camera_point_focus_action",
                {**base, "x": args.focus_x, "y": args.invalid_focus_y},
                expect_success=False,
            ),
        ]
    )
    return cases


def _compact_reply(reply: dict[str, Any] | None) -> str:
    if not isinstance(reply, dict):
        return "null"
    return json.dumps(reply, ensure_ascii=False, separators=(",", ":"))


def run_case(client: MqttRequestClient, case: FocusCase, args: argparse.Namespace) -> tuple[bool, dict[str, Any]]:
    if case.method not in CLOUD_METHODS:
        raise ValueError(f"script may only publish existing cloud focus methods, got: {case.method}")

    allowed = set(case.allowed_results) if case.allowed_results is not None else ({0} if case.expect_success else FAILURE_RESULTS)
    started = time.monotonic()
    rc, reply = client.request(
        case.method,
        case.data,
        args.timeout,
        channel="services",
        allowed_results=allowed,
        verbose=args.verbose,
    )
    elapsed_ms = int((time.monotonic() - started) * 1000)
    code = result_code(reply)
    ok = rc == 0
    detail = {
        "label": case.label,
        "method": case.method,
        "expect_success": case.expect_success,
        "result": code,
        "elapsed_ms": elapsed_ms,
        "blocking_ok": elapsed_ms <= int(args.max_elapsed_ms),
        "note": case.note,
    }

    if rc != 0:
        ok = False
        detail["error"] = f"unexpected reply/result rc={rc}"
        detail["reply"] = reply
    if case.expect_success and code != 0:
        ok = False
        detail["error"] = "合法对焦命令未成功"
    if not case.expect_success and code == 0:
        ok = False
        detail["error"] = "非法参数被网关/设备成功接受"
        detail["reply"] = reply
    if elapsed_ms > int(args.max_elapsed_ms):
        ok = False
        detail["error"] = f"接口耗时超过阈值 {args.max_elapsed_ms}ms"

    if args.video_url and args.capture_snapshots:
        snapshot_dir = Path(args.snapshot_dir)
        if args.after_wait > 0:
            time.sleep(args.after_wait)
        snapshot_path = snapshot_dir / f"{len(list(snapshot_dir.glob('*.jpg'))) + 1:02d}_{_safe_name(case.label)}_after.jpg"
        snap_rc, snap_detail = _capture_snapshot(args.video_url, snapshot_path, args.ffmpeg, args.snapshot_timeout)
        detail["snapshot"] = snap_detail
        if snap_rc != 0:
            detail["snapshot_error"] = snap_detail
            if args.require_snapshots:
                ok = False
                detail["error"] = f"真实画面截图失败: {snap_detail}"

    status = "PASS" if ok else "FAIL"
    print(f"[{status}] {case.label}: method={case.method} result={code} elapsed={elapsed_ms}ms")
    if case.note:
        print(f"       {case.note}")
    if not ok and not args.verbose:
        print(f"       reply={_compact_reply(reply)}")
    if args.observe_wait > 0:
        print(f"       observe real video for {args.observe_wait:.1f}s")
        time.sleep(args.observe_wait)
    return ok, detail


def run_disconnected_probe(args: argparse.Namespace) -> bool:
    """Verify the tool reports MQTT/gateway unavailability instead of hanging."""
    bad_client = MqttRequestClient(
        args.disconnected_host,
        args.disconnected_port,
        args.sn,
        args.qos,
        quiet_stale=True,
        client_id_prefix="edge_gateway_camera_focus_disconnected_probe",
    )
    started = time.monotonic()
    connected = bad_client.connect(args.disconnected_timeout)
    elapsed_ms = int((time.monotonic() - started) * 1000)
    if connected:
        bad_client.disconnect()
        print(
            "[FAIL] 未连接探针: 意外连上了 "
            f"{args.disconnected_host}:{args.disconnected_port} elapsed={elapsed_ms}ms"
        )
        return False

    print(
        "[PASS] 未连接探针: MQTT 连接失败被正确返回 "
        f"host={args.disconnected_host} port={args.disconnected_port} elapsed={elapsed_ms}ms"
    )
    return True


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Test camera focus controls through the real gateway Dock3 services topic."
    )
    add_config_argument(parser)
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--sn", default=None)
    parser.add_argument("--timeout", type=float, default=20.0)
    parser.add_argument("--qos", type=int, choices=[0, 1, 2], default=1)
    parser.add_argument("--quiet-stale", action="store_true")
    parser.add_argument("--fail-on-stale", action="store_true")
    parser.add_argument("--verbose", action="store_true", help="Print full MQTT replies.")
    parser.add_argument("--payload-index", default="", help="Dock3 payload index, e.g. 52-0-0.")
    parser.add_argument("--camera-index", type=int, default=0)
    parser.add_argument("--camera-type", default="zoom")
    parser.add_argument("--focus-distance", type=float, default=5.0)
    parser.add_argument(
        "--focus-values",
        default="",
        help="Comma-separated absolute focus values for visual sweep, e.g. 0,32768,65535. Overrides --focus-distance.",
    )
    parser.add_argument("--focus-speed", type=int, default=2)
    parser.add_argument("--focus-duration", type=float, default=0.1)
    parser.add_argument(
        "--focus-reset-distance",
        type=float,
        default=None,
        help="Focus value used as the reset/isolation step before switching from manual to auto. Defaults to --focus-distance.",
    )
    parser.add_argument(
        "--focus-reset-wait",
        type=float,
        default=0.3,
        help="Duration passed to the reset/isolation focus value command before mode switching.",
    )
    parser.add_argument("--focus-x", type=float, default=0.5)
    parser.add_argument("--focus-y", type=float, default=0.5)
    parser.add_argument("--invalid-focus-mode", default="bad_focus_mode")
    parser.add_argument("--invalid-focus-distance", type=float, default=1000000.0)
    parser.add_argument("--invalid-focus-x", type=float, default=1.5)
    parser.add_argument("--invalid-focus-y", type=float, default=-0.2)
    parser.add_argument(
        "--allow-viewlink-focus-limitations",
        action="store_true",
        help="Treat current ViewLink absolute/point focus unsupported replies as expected instead of acceptance failures.",
    )
    parser.add_argument("--video-url", default="", help="Real visible/zoom stream, e.g. rtsp://192.168.1.119:554/main.")
    parser.add_argument("--snapshot-dir", default="logs/camera_focus_control")
    parser.add_argument("--capture-snapshots", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--require-snapshots", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--after-wait", type=float, default=0.8)
    parser.add_argument("--ffmpeg", default="ffmpeg")
    parser.add_argument("--snapshot-timeout", type=float, default=20.0)
    parser.add_argument(
        "--observe-wait",
        type=float,
        default=0.0,
        help="Sleep after each case so a human can observe the real RTSP picture.",
    )
    parser.add_argument(
        "--max-elapsed-ms",
        type=int,
        default=3000,
        help="Fail a case when services reply latency exceeds this threshold.",
    )
    parser.add_argument(
        "--skip-disconnected-probe",
        action="store_true",
        help="Skip the MQTT unreachable-host probe.",
    )
    parser.add_argument("--disconnected-host", default="127.0.0.1")
    parser.add_argument("--disconnected-port", type=int, default=9)
    parser.add_argument("--disconnected-timeout", type=float, default=2.0)
    args = parser.parse_args()
    args.host, args.port, args.sn = resolve_mqtt_settings(args)
    config = load_config(args.config)
    if not args.video_url:
        args.video_url = _config_video_url(config)

    if args.capture_snapshots and not args.video_url:
        print("real video URL is required for visual focus evidence; pass --video-url or configure payload.devices.gimbal.channels.video.endpoint", file=sys.stderr)
        return 2

    client = MqttRequestClient(
        args.host,
        args.port,
        args.sn,
        args.qos,
        quiet_stale=args.quiet_stale,
        client_id_prefix="edge_gateway_camera_focus_control",
    )
    if not client.connect(args.timeout):
        print("MQTT connect timeout; real gateway/broker is unavailable", file=sys.stderr)
        return 2

    details: list[dict[str, Any]] = []
    try:
        all_ok = True
        if args.video_url and args.capture_snapshots:
            snapshot_dir = Path(args.snapshot_dir)
            snap_rc, snap_detail = _capture_snapshot(args.video_url, snapshot_dir / "00_baseline_before.jpg", args.ffmpeg, args.snapshot_timeout)
            print(f"[{'PASS' if snap_rc == 0 else 'FAIL'}] baseline snapshot: {snap_detail}")
            if snap_rc != 0 and args.require_snapshots:
                all_ok = False
        for case in build_cases(args):
            ok, detail = run_case(client, case, args)
            details.append(detail)
            all_ok = all_ok and ok

        if not args.skip_disconnected_probe:
            all_ok = run_disconnected_probe(args) and all_ok

        stale_total = client.stale_total()
        if args.fail_on_stale and stale_total:
            all_ok = False
            print(f"[FAIL] stale replies observed: {client.stats}", file=sys.stderr)
            print(json.dumps(client.stale_sample_summary(), ensure_ascii=False, indent=2), file=sys.stderr)

        print("\nfocus control summary:")
        print(json.dumps(details, ensure_ascii=False, indent=2))
        if all_ok:
            print(
                "camera focus control test ok "
                f"(gateway={args.sn}, host={args.host}:{args.port}, payload_index={_payload_index(args)})"
            )
            return 0
        print("camera focus control test failed", file=sys.stderr)
        return 1
    finally:
        client.disconnect()


if __name__ == "__main__":
    raise SystemExit(main())
