#!/usr/bin/env python3
"""Real-gateway Dock3 camera recording acceptance test.

This tool publishes only existing Dock3 cloud API services methods:

    camera_recording_start
    camera_recording_stop

It does not start or talk to a virtual gateway.  Optional media verification
uses the existing ViewLink HTTP media page plus ffprobe/ffmpeg against the
recorded file.
"""

from __future__ import annotations

import argparse
import json
import os
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
from viewlink_media_download import (
    discover_media_links,
    download_file,
    latest_media_link_by_http_metadata,
)


VIDEO_EXTENSIONS = (".mp4", ".mov", ".avi", ".mkv")


def _payload_index(args: argparse.Namespace) -> str:
    if args.payload_index:
        return args.payload_index
    return payload_index_from_config(load_config(args.config))


def _recording_payload(args: argparse.Namespace) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "payload_index": _payload_index(args),
        "camera_index": args.camera_index,
        "camera_type": args.camera_type,
        "resolution": args.resolution,
        "framerate": args.framerate,
        "encoding": args.encoding,
    }
    if args.video_id:
        payload["video_id"] = args.video_id
    if args.file_name:
        payload["file_name"] = args.file_name
    return payload


def _stop_payload(args: argparse.Namespace) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "payload_index": _payload_index(args),
        "camera_index": args.camera_index,
        "camera_type": args.camera_type,
    }
    if args.video_id:
        payload["video_id"] = args.video_id
    if args.file_name:
        payload["file_name"] = args.file_name
    return payload


def _is_success(reply: dict[str, Any] | None) -> bool:
    return result_code(reply) == 0


def _reply_summary(reply: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(reply, dict):
        return {"reply": None}
    data = reply.get("data") if isinstance(reply.get("data"), dict) else {}
    return {
        "method": reply.get("method"),
        "tid": reply.get("tid"),
        "result": result_code(reply),
        "msg": reply.get("msg") or data.get("msg") or "",
        "data": data,
    }


def _request(
    client: MqttRequestClient,
    method: str,
    payload: dict[str, Any],
    args: argparse.Namespace,
    *,
    allowed_results: set[int] | None = None,
) -> tuple[int, dict[str, Any] | None]:
    return client.request(
        method,
        payload,
        args.timeout,
        channel="services",
        allowed_results=allowed_results,
        verbose=args.verbose,
    )


def _discover_videos(args: argparse.Namespace) -> list[str]:
    return discover_media_links(
        args.media_url,
        args.media_timeout,
        VIDEO_EXTENSIONS,
        max_depth=max(0, args.media_max_depth),
    )


def _wait_for_new_video(args: argparse.Namespace, baseline: set[str]) -> str:
    deadline = time.time() + max(0.0, args.media_poll_timeout)
    last_count = 0
    while True:
        videos = _discover_videos(args)
        last_count = len(videos)
        candidates = [url for url in videos if url not in baseline]
        if candidates:
            return latest_media_link_by_http_metadata(candidates, args.media_timeout)
        if time.time() >= deadline:
            raise RuntimeError(f"no new video found, current_video_count={last_count}")
        time.sleep(max(0.1, args.media_poll_interval))


def _ffprobe_json(ffprobe: str, media: str, timeout: float) -> dict[str, Any]:
    executable = shutil.which(ffprobe) or ffprobe
    command = [
        executable,
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-show_entries",
        "stream=codec_name,width,height,avg_frame_rate,r_frame_rate",
        "-of",
        "json",
        media,
    ]
    completed = subprocess.run(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=timeout,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr.strip() or f"ffprobe exit={completed.returncode}")
    return json.loads(completed.stdout or "{}")


def _fraction_to_float(value: str) -> float:
    if "/" not in value:
        return float(value or 0)
    numerator, denominator = value.split("/", 1)
    denominator_value = float(denominator or 0)
    return 0.0 if denominator_value == 0 else float(numerator or 0) / denominator_value


def _expected_size(resolution: str) -> tuple[int, int] | None:
    text = str(resolution or "").strip().lower()
    aliases = {
        "720p": (1280, 720),
        "1080p": (1920, 1080),
        "2k": (2560, 1440),
        "4k": (3840, 2160),
    }
    if text in aliases:
        return aliases[text]
    if "x" in text:
        left, right = text.split("x", 1)
        try:
            return int(left), int(right)
        except ValueError:
            return None
    return None


def _validate_media_params(probe: dict[str, Any], args: argparse.Namespace) -> list[str]:
    streams = probe.get("streams") if isinstance(probe, dict) else None
    stream = streams[0] if isinstance(streams, list) and streams else {}
    errors: list[str] = []

    expected = _expected_size(args.resolution)
    if expected:
        actual = (stream.get("width"), stream.get("height"))
        if actual != expected:
            errors.append(f"resolution expected={expected[0]}x{expected[1]} actual={actual[0]}x{actual[1]}")

    actual_fps = _fraction_to_float(str(stream.get("avg_frame_rate") or stream.get("r_frame_rate") or "0"))
    if args.framerate_tolerance >= 0 and abs(actual_fps - args.framerate) > args.framerate_tolerance:
        errors.append(f"framerate expected={args.framerate} actual={actual_fps:.3f}")

    codec = str(stream.get("codec_name") or "").lower()
    expected_codec = str(args.encoding or "").lower().replace("-", "")
    codec_aliases = {
        "h264": {"h264", "avc1"},
        "h265": {"hevc", "h265"},
        "hevc": {"hevc", "h265"},
    }
    allowed = codec_aliases.get(expected_codec, {expected_codec})
    if expected_codec and codec not in allowed:
        errors.append(f"encoding expected={args.encoding} actual={codec or 'unknown'}")

    return errors


def _verify_file_generated(args: argparse.Namespace, baseline: set[str]) -> tuple[int, dict[str, Any]]:
    if not args.media_url:
        return 0, {"skipped": True, "reason": "--media-url not provided"}

    video_url = _wait_for_new_video(args, baseline)
    evidence: dict[str, Any] = {"video_url": video_url}
    media_for_probe = video_url

    if args.download_recording or args.verify_media_params:
        target = download_file(video_url, Path(args.output_dir), args.media_timeout)
        evidence["downloaded_file"] = str(target.resolve())
        evidence["downloaded_size"] = target.stat().st_size
        media_for_probe = str(target)
        if target.stat().st_size <= 0:
            return 41, evidence

    if args.verify_media_params:
        probe = _ffprobe_json(args.ffprobe, media_for_probe, args.ffprobe_timeout)
        evidence["ffprobe"] = probe
        errors = _validate_media_params(probe, args)
        if errors:
            evidence["param_errors"] = errors
            return 42, evidence

    return 0, evidence


def _append_result(results: list[dict[str, Any]], name: str, ok: bool, detail: dict[str, Any]) -> None:
    results.append({"name": name, "ok": ok, "detail": detail})
    print(f"[{'PASS' if ok else 'FAIL'}] {name}")
    print(json.dumps(detail, ensure_ascii=False, indent=2))


def run_recording_acceptance(client: MqttRequestClient, args: argparse.Namespace) -> int:
    results: list[dict[str, Any]] = []
    start_payload = _recording_payload(args)
    stop_payload = _stop_payload(args)

    baseline: set[str] = set()
    if args.media_url:
        try:
            baseline = set(_discover_videos(args))
            _append_result(results, "media_baseline", True, {"video_count": len(baseline), "media_url": args.media_url})
        except Exception as exc:
            _append_result(results, "media_baseline", False, {"error": str(exc), "media_url": args.media_url})
            return 40

    rc, initial_stop = _request(client, "camera_recording_stop", stop_payload, args, allowed_results=None)
    if rc == 0 and not _is_success(initial_stop):
        _append_result(results, "stop_when_idle_returns_error", True, _reply_summary(initial_stop))
    elif rc == 0:
        _append_result(
            results,
            "stop_when_idle_returns_error",
            False,
            {**_reply_summary(initial_stop), "note": "stop succeeded; device may have been recording before this test"},
        )
        if args.strict_initial_idle:
            return 11
    else:
        _append_result(results, "stop_when_idle_returns_error", False, {"client_rc": rc})
        return rc

    rc, start_reply = _request(client, "camera_recording_start", start_payload, args, allowed_results=None)
    _append_result(
        results,
        "camera_recording_start_can_start",
        rc == 0 and _is_success(start_reply),
        {"request_data": start_payload, "reply": _reply_summary(start_reply)},
    )
    if rc != 0 or not _is_success(start_reply):
        return rc or 12

    rc, repeat_start = _request(client, "camera_recording_start", start_payload, args, allowed_results=None)
    _append_result(
        results,
        "repeat_start_returns_error",
        rc == 0 and not _is_success(repeat_start),
        {"request_data": start_payload, "reply": _reply_summary(repeat_start)},
    )
    if rc != 0 or _is_success(repeat_start):
        return rc or 13

    print(f"recording for {args.record_duration:.1f}s")
    time.sleep(max(0.0, args.record_duration))

    rc, stop_reply = _request(client, "camera_recording_stop", stop_payload, args, allowed_results=None)
    stop_data = stop_reply.get("data", {}) if isinstance(stop_reply, dict) else {}
    stop_checks: dict[str, Any] = {
        "result_zero": rc == 0 and _is_success(stop_reply),
        "schema_note": (
            "Dock3 services_reply may contain only data.result; "
            "record_time/recording are optional because gateway schema sanitization can remove them."
        ),
    }
    stop_ok = bool(stop_checks["result_zero"])
    if "recording" in stop_data:
        stop_checks["recording_false"] = stop_data.get("recording") is False
        stop_ok = stop_ok and bool(stop_checks["recording_false"])
    else:
        stop_checks["recording_false"] = "not_present_in_reply"
    if "record_time" in stop_data:
        actual_record_time = int(stop_data.get("record_time") or 0)
        stop_checks["record_time_seconds"] = actual_record_time
        stop_checks["record_time_meets_minimum"] = actual_record_time >= max(0, int(args.min_record_time))
        stop_ok = stop_ok and bool(stop_checks["record_time_meets_minimum"])
    else:
        stop_checks["record_time_seconds"] = "not_present_in_reply"
    _append_result(
        results,
        "camera_recording_stop_can_stop",
        stop_ok,
        {
            "request_data": stop_payload,
            "reply": _reply_summary(stop_reply),
            "min_record_time": args.min_record_time,
            "checks": stop_checks,
        },
    )
    if not stop_ok:
        return rc or 14

    rc, repeat_stop = _request(client, "camera_recording_stop", stop_payload, args, allowed_results=None)
    _append_result(
        results,
        "repeat_stop_returns_error",
        rc == 0 and not _is_success(repeat_stop),
        {"request_data": stop_payload, "reply": _reply_summary(repeat_stop)},
    )
    if rc != 0 or _is_success(repeat_stop):
        return rc or 15

    try:
        file_rc, file_evidence = _verify_file_generated(args, baseline)
    except Exception as exc:
        file_rc, file_evidence = 41, {"error": str(exc)}
    _append_result(results, "recording_file_generated", file_rc == 0, file_evidence)
    if file_rc != 0:
        return file_rc

    param_ok = True
    param_detail: dict[str, Any] = {
        "request_data": {
            "resolution": start_payload["resolution"],
            "framerate": start_payload["framerate"],
            "encoding": start_payload["encoding"],
        },
        "note": "strict media parameter validation requires --verify-media-params",
    }
    if args.verify_media_params:
        param_detail = file_evidence
        param_ok = "param_errors" not in file_evidence
    _append_result(results, "resolution_framerate_encoding_effective", param_ok, param_detail)

    summary = {
        "scenario": "real_gateway_camera_recording",
        "cloud_methods_used": ["camera_recording_start", "camera_recording_stop"],
        "mqtt": {"host": args.host, "port": args.port, "sn": args.sn},
        "accepted_replies": client.stats["accepted"],
        "stale_total": client.stale_total(),
        "results": results,
    }
    print("\n[summary]")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


def run_disconnected_expectation(client: MqttRequestClient, args: argparse.Namespace) -> int:
    results: list[dict[str, Any]] = []
    for method, payload in (
        ("camera_recording_start", _recording_payload(args)),
        ("camera_recording_stop", _stop_payload(args)),
    ):
        rc, reply = _request(client, method, payload, args, allowed_results=None)
        ok = rc == 0 and not _is_success(reply)
        _append_result(results, f"{method}_returns_error_when_device_disconnected", ok, _reply_summary(reply))
        if not ok:
            return rc or 30
    print("\n[summary]")
    print(json.dumps({"scenario": "device_disconnected", "results": results}, ensure_ascii=False, indent=2))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run real-gateway camera recording acceptance checks through Dock3 cloud API services."
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
    parser.add_argument("--payload-index", default="", help="Dock3 payload index, for example 52-0-0.")
    parser.add_argument("--camera-index", type=int, default=0)
    parser.add_argument("--camera-type", default="zoom")
    parser.add_argument("--resolution", default="1080p", help="Expected recording resolution, e.g. 1080p or 1920x1080.")
    parser.add_argument("--framerate", type=int, default=30)
    parser.add_argument("--encoding", default="h264")
    parser.add_argument("--video-id", default="")
    parser.add_argument("--file-name", default="")
    parser.add_argument("--record-duration", type=float, default=8.0)
    parser.add_argument("--min-record-time", type=float, default=1.0)
    parser.add_argument("--strict-initial-idle", action="store_true")
    parser.add_argument(
        "--scenario",
        choices=["normal", "disconnected"],
        default="normal",
        help="Use disconnected after physically disconnecting the payload/device link.",
    )
    parser.add_argument("--media-url", default="", help="Real ViewLink media page, e.g. http://192.168.1.119:8554/download/.")
    parser.add_argument("--media-timeout", type=float, default=10.0)
    parser.add_argument("--media-poll-timeout", type=float, default=30.0)
    parser.add_argument("--media-poll-interval", type=float, default=1.0)
    parser.add_argument("--media-max-depth", type=int, default=4)
    parser.add_argument("--download-recording", action="store_true")
    parser.add_argument("--output-dir", default="logs/viewlink_media")
    parser.add_argument("--verify-media-params", action="store_true")
    parser.add_argument("--ffprobe", default="ffprobe")
    parser.add_argument("--ffprobe-timeout", type=float, default=30.0)
    parser.add_argument("--framerate-tolerance", type=float, default=1.0)
    args = parser.parse_args()
    args.host, args.port, args.sn = resolve_mqtt_settings(args)

    if args.framerate <= 0:
        parser.error("--framerate must be positive")
    if args.record_duration < 0 or args.min_record_time < 0:
        parser.error("--record-duration and --min-record-time must be non-negative")
    if args.verify_media_params and not args.media_url:
        parser.error("--verify-media-params requires --media-url")
    if args.verify_media_params and not shutil.which(args.ffprobe) and not os.path.exists(args.ffprobe):
        parser.error(f"ffprobe not found: {args.ffprobe}")

    client = MqttRequestClient(
        args.host,
        args.port,
        args.sn,
        args.qos,
        quiet_stale=args.quiet_stale,
        client_id_prefix="edge_gateway_camera_recording_real",
    )
    if not client.connect(args.timeout):
        print(f"MQTT connect timeout: host={args.host} port={args.port}", file=sys.stderr)
        return 2

    try:
        if args.scenario == "disconnected":
            rc = run_disconnected_expectation(client, args)
        else:
            rc = run_recording_acceptance(client, args)
        if rc != 0:
            return rc
        if args.fail_on_stale and client.stale_total():
            print(f"stale replies observed: {client.stats}", file=sys.stderr)
            return 9
        print(
            "real gateway camera recording test ok "
            f"(accepted={client.stats['accepted']}, stale_total={client.stale_total()}, "
            f"unrelated_replies={client.unrelated_reply_total()})"
        )
        return 0
    finally:
        client.disconnect()


if __name__ == "__main__":
    raise SystemExit(main())
