#!/usr/bin/env python3
"""Deep MQTT acceptance test for Dock3 camera_photo_take/camera_photo_stop.

The script uses only the existing Dock3 Cloud API services path:

    thing/product/{sn}/services -> thing/product/{sn}/services_reply

and only the official camera photo methods:

    camera_photo_take
    camera_photo_stop

It verifies trigger/stop behavior, real media metadata, and "must not fake
success" negative cases.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.parse
from dataclasses import dataclass
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "tools"))

from mqtt_test_client import MqttRequestClient, result_code
from src.payload.dock3_payload import payload_index_from_config
from tool_config import (
    add_config_argument,
    load_config,
    resolve_mqtt_credentials,
    resolve_mqtt_settings,
)


TAKE_METHOD = "camera_photo_take"
STOP_METHOD = "camera_photo_stop"
PROGRESS_METHOD = "camera_photo_take_progress"


@dataclass(frozen=True)
class PhotoCase:
    name: str
    mode: int
    count: int
    interval: float
    require_multiple_events: bool
    stop_after_seconds: float


def _payload_index(args: argparse.Namespace) -> str:
    if args.payload_index:
        return args.payload_index
    return payload_index_from_config(load_config(args.config))


def _data(message: Any) -> dict[str, Any]:
    data = message.get("data") if isinstance(message, dict) else {}
    return data if isinstance(data, dict) else {}


def _output(message: Any) -> dict[str, Any]:
    data = _data(message)
    output = data.get("output")
    return output if isinstance(output, dict) else {}


def _ext(message: Any) -> dict[str, Any]:
    output = _output(message)
    ext = output.get("ext")
    return ext if isinstance(ext, dict) else {}


def _walk_dicts(value: Any):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk_dicts(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_dicts(child)


def _media_candidates(*messages: Any) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for message in messages:
        for node in _walk_dicts(message):
            if any(
                node.get(key)
                for key in (
                    "file_path",
                    "local_path",
                    "path",
                    "download_url",
                    "url",
                    "file_size",
                    "resolution",
                    "capture_time",
                    "file_name",
                )
            ):
                candidates.append(node)
    return candidates


def _resolve_local_path(raw_path: str) -> Path:
    path = Path(raw_path).expanduser()
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return path


def _extract_path(media: dict[str, Any]) -> Path | None:
    for key in ("file_path", "local_path", "path"):
        value = media.get(key)
        if isinstance(value, str) and value and not value.startswith(("http://", "https://")):
            return _resolve_local_path(value)
    return None


def _extract_url(media: dict[str, Any]) -> str:
    for key in ("download_url", "url"):
        value = media.get(key)
        if isinstance(value, str) and value.startswith(("http://", "https://")):
            return value
    return ""


def _probe_image_resolution(path: Path) -> str:
    try:
        with path.open("rb") as handle:
            header = handle.read(32)
            if header.startswith(b"\x89PNG\r\n\x1a\n") and len(header) >= 24:
                width = int.from_bytes(header[16:20], "big")
                height = int.from_bytes(header[20:24], "big")
                return f"{width}x{height}" if width and height else ""
            if not header.startswith(b"\xff\xd8"):
                return ""
            handle.seek(2)
            while True:
                marker_start = handle.read(1)
                if not marker_start:
                    return ""
                if marker_start != b"\xff":
                    continue
                marker = handle.read(1)
                while marker == b"\xff":
                    marker = handle.read(1)
                if marker in (b"\xd8", b"\xd9") or not marker:
                    continue
                length_bytes = handle.read(2)
                if len(length_bytes) != 2:
                    return ""
                length = int.from_bytes(length_bytes, "big")
                if marker in (
                    b"\xc0",
                    b"\xc1",
                    b"\xc2",
                    b"\xc3",
                    b"\xc5",
                    b"\xc6",
                    b"\xc7",
                    b"\xc9",
                    b"\xca",
                    b"\xcb",
                    b"\xcd",
                    b"\xce",
                    b"\xcf",
                ):
                    segment = handle.read(5)
                    if len(segment) != 5:
                        return ""
                    height = int.from_bytes(segment[1:3], "big")
                    width = int.from_bytes(segment[3:5], "big")
                    return f"{width}x{height}" if width and height else ""
                handle.seek(length - 2, os.SEEK_CUR)
    except OSError:
        return ""
    return ""


def _timestamp_seconds(value: Any) -> float | None:
    if isinstance(value, (int, float)):
        ts = float(value)
    elif isinstance(value, str) and value.strip().isdigit():
        ts = float(value.strip())
    else:
        return None
    if ts > 10_000_000_000:
        ts /= 1000.0
    return ts


def _media_capture_time(media: dict[str, Any]) -> float | None:
    for key in ("capture_time", "timestamp", "create_time", "created_at"):
        ts = _timestamp_seconds(media.get(key))
        if ts:
            return ts
    return None


def validate_media_truth(
    candidates: list[dict[str, Any]],
    args: argparse.Namespace,
    *,
    label: str,
) -> tuple[int, dict[str, Any]]:
    if not candidates:
        print(f"{label}: no media metadata found in reply/progress event", file=sys.stderr)
        return 20, {}

    now = time.time()
    best: dict[str, Any] = {}
    for media in candidates:
        path = _extract_path(media)
        url = _extract_url(media)
        if path and path.is_file():
            stat_size = path.stat().st_size
            if stat_size < args.min_file_size:
                print(f"{label}: media file too small: path={path} size={stat_size}", file=sys.stderr)
                return 21, media

            declared_size = media.get("file_size") or media.get("size")
            if declared_size is not None and int(declared_size) != stat_size:
                print(
                    f"{label}: file_size mismatch: declared={declared_size} actual={stat_size} path={path}",
                    file=sys.stderr,
                )
                return 22, media

            resolution = str(media.get("resolution") or "").strip()
            probed_resolution = _probe_image_resolution(path)
            if args.require_resolution and not resolution and not probed_resolution:
                print(f"{label}: missing real resolution: path={path}", file=sys.stderr)
                return 23, media
            if resolution and probed_resolution and resolution != probed_resolution:
                print(
                    f"{label}: resolution mismatch: declared={resolution} actual={probed_resolution}",
                    file=sys.stderr,
                )
                return 24, media

            capture_time = _media_capture_time(media)
            if args.require_capture_time and capture_time is None:
                print(f"{label}: missing capture_time/timestamp in media metadata: {media}", file=sys.stderr)
                return 25, media
            if capture_time is not None and not (now - args.max_time_skew <= capture_time <= now + args.max_time_skew):
                print(
                    f"{label}: capture timestamp out of range: capture={capture_time} now={now}",
                    file=sys.stderr,
                )
                return 26, media

            best = dict(media)
            best["validated_path"] = str(path)
            best["actual_file_size"] = stat_size
            best["actual_resolution"] = probed_resolution or resolution
            return 0, best

        if url:
            if args.require_local_file:
                print(f"{label}: only URL found but local file is required: {url}", file=sys.stderr)
                return 27, media
            best = dict(media)
            best["validated_url"] = url
            return 0, best

    print(f"{label}: media metadata found but no readable local file/url: {candidates}", file=sys.stderr)
    return 28, candidates[0]


def request_take(
    client: MqttRequestClient,
    payload: dict[str, Any],
    timeout: float,
    *,
    allowed_results: set[int] | None,
    verbose: bool,
) -> tuple[int, dict[str, Any] | None]:
    return client.request(
        TAKE_METHOD,
        payload,
        timeout,
        channel="services",
        allowed_results=allowed_results,
        verbose=verbose,
    )


def request_stop(
    client: MqttRequestClient,
    payload: dict[str, Any],
    timeout: float,
    *,
    allowed_results: set[int] | None,
    verbose: bool,
) -> tuple[int, dict[str, Any] | None]:
    return client.request(
        STOP_METHOD,
        payload,
        timeout,
        channel="services",
        allowed_results=allowed_results,
        verbose=verbose,
    )


def wait_progress_after(
    client: MqttRequestClient,
    previous_count: int,
    timeout: float,
    *,
    required: bool,
) -> tuple[int, dict[str, Any] | None]:
    rc, event = client.wait_event(PROGRESS_METHOD, previous_count, timeout, expected_status=None)
    if rc != 0 and not required:
        return 0, None
    return rc, event


def run_photo_case(client: MqttRequestClient, args: argparse.Namespace, case: PhotoCase) -> tuple[int, dict[str, Any]]:
    payload = {
        "payload_index": _payload_index(args),
        "camera_index": args.camera_index,
        "camera_type": args.camera_type,
        "mode": case.mode,
        "count": case.count,
        "interval": case.interval,
        "file_name": f"{args.file_prefix}-{case.name}-{int(time.time() * 1000)}.jpg",
    }
    if not args.include_file_name:
        payload.pop("file_name")

    print(f"\n=== {case.name}: {TAKE_METHOD} ===")
    previous = client.event_count(PROGRESS_METHOD)
    rc, reply = request_take(client, payload, args.timeout, allowed_results={0}, verbose=not args.quiet)
    if rc != 0:
        return rc, {"case": case.name, "reply": reply}

    rc, event = wait_progress_after(
        client,
        previous,
        args.progress_timeout,
        required=args.require_progress_event,
    )
    if rc != 0:
        return rc, {"case": case.name, "reply": reply, "event": event}

    media_rc, media = validate_media_truth(
        _media_candidates(reply, event),
        args,
        label=case.name,
    )
    if media_rc != 0:
        return media_rc, {"case": case.name, "reply": reply, "event": event, "media": media}

    if case.require_multiple_events:
        time.sleep(case.stop_after_seconds)
        event_count_before_stop = client.event_count(PROGRESS_METHOD)
        if event_count_before_stop <= previous + 1:
            print(
                f"{case.name}: expected repeated progress/media events before stop; "
                f"before={previous}, now={event_count_before_stop}",
                file=sys.stderr,
            )
            return 29, {"case": case.name, "reply": reply, "event": event}

    print(f"\n=== {case.name}: {STOP_METHOD} ===")
    stop_previous = client.event_count(PROGRESS_METHOD)
    rc, stop_reply = request_stop(
        client,
        {"payload_index": _payload_index(args)},
        args.timeout,
        allowed_results={0},
        verbose=not args.quiet,
    )
    if rc != 0:
        return rc, {"case": case.name, "reply": reply, "stop_reply": stop_reply}

    stop_data = _data(stop_reply)
    if stop_data.get("running") is True or stop_data.get("stopped") is False:
        print(f"{case.name}: stop returned success but session is still running: {stop_reply}", file=sys.stderr)
        return 30, {"case": case.name, "stop_reply": stop_reply}

    if case.require_multiple_events:
        time.sleep(args.stop_observe_seconds)
        after_stop = client.event_count(PROGRESS_METHOD)
        if after_stop > stop_previous:
            print(
                f"{case.name}: progress events still arrived after stop: "
                f"before_stop={stop_previous}, after_stop={after_stop}",
                file=sys.stderr,
            )
            return 31, {"case": case.name, "stop_reply": stop_reply}

    return 0, {
        "case": case.name,
        "reply": reply,
        "event": event,
        "stop_reply": stop_reply,
        "media": media,
    }


def run_negative_case(
    client: MqttRequestClient,
    args: argparse.Namespace,
    *,
    name: str,
    payload: dict[str, Any],
) -> tuple[int, dict[str, Any]]:
    print(f"\n=== negative: {name} ===")
    rc, reply = request_take(
        client,
        payload,
        args.timeout,
        allowed_results=None,
        verbose=not args.quiet,
    )
    if rc != 0:
        return rc, {"case": name, "reply": reply}
    code = result_code(reply)
    if code == 0:
        print(f"{name}: expected failure but got success: {json.dumps(reply, ensure_ascii=False)}", file=sys.stderr)
        return 40, {"case": name, "reply": reply}
    return 0, {"case": name, "reply": reply}


def run_checks(client: MqttRequestClient, args: argparse.Namespace) -> int:
    cases = [
        PhotoCase("single", 0, 1, args.interval, False, 0.0),
        PhotoCase("burst", 1, args.burst_count, args.interval, True, args.burst_observe_seconds),
        PhotoCase("timed", 2, 0, args.interval, True, args.timed_observe_seconds),
    ]
    if args.only:
        cases = [case for case in cases if case.name in set(args.only)]

    results: list[dict[str, Any]] = []
    for case in cases:
        rc, result = run_photo_case(client, args, case)
        results.append(result)
        if rc != 0:
            print("\nfailed case detail:")
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return rc

    if args.run_negative:
        base_payload = {
            "payload_index": _payload_index(args),
            "camera_index": args.camera_index,
            "camera_type": args.camera_type,
        }
        negative_cases = []
        if args.negative_payload_index:
            negative_cases.append((
                "invalid payload_index must not succeed",
                {**base_payload, "payload_index": args.negative_payload_index},
            ))
        if args.negative_camera_type:
            negative_cases.append((
                "unsupported/abnormal camera_type must not succeed",
                {**base_payload, "camera_type": args.negative_camera_type},
            ))

        for name, payload in negative_cases:
            rc, result = run_negative_case(client, args, name=name, payload=payload)
            results.append(result)
            if rc != 0:
                print("\nfailed negative case detail:")
                print(json.dumps(result, ensure_ascii=False, indent=2))
                return rc

    print("\nphoto control deep test ok")
    print(json.dumps(
        {
            "cases": [
                {
                    "case": item.get("case"),
                    "media": item.get("media"),
                    "result": result_code(item.get("reply")),
                    "stop_result": result_code(item.get("stop_reply")),
                }
                for item in results
            ],
            "mqtt_stats": client.stats,
        },
        ensure_ascii=False,
        indent=2,
    ))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Deep-test camera_photo_take/camera_photo_stop through Dock3 Cloud API services."
    )
    add_config_argument(parser)
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--sn", default=None)
    parser.add_argument("--username", default=None)
    parser.add_argument("--password", default=None)
    parser.add_argument("--timeout", type=float, default=20.0)
    parser.add_argument("--progress-timeout", type=float, default=12.0)
    parser.add_argument("--qos", type=int, choices=[0, 1, 2], default=1)
    parser.add_argument("--quiet-stale", action="store_true")
    parser.add_argument("--fail-on-stale", action="store_true")
    parser.add_argument("--quiet", action="store_true")
    parser.add_argument("--payload-index", default="", help="Dock3 payload index, e.g. 52-0-0.")
    parser.add_argument("--camera-index", type=int, default=0)
    parser.add_argument("--camera-type", default="zoom")
    parser.add_argument("--interval", type=float, default=0.5)
    parser.add_argument("--burst-count", type=int, default=3)
    parser.add_argument("--burst-observe-seconds", type=float, default=1.2)
    parser.add_argument("--timed-observe-seconds", type=float, default=1.2)
    parser.add_argument("--stop-observe-seconds", type=float, default=1.2)
    parser.add_argument("--file-prefix", default="photo-deep-test")
    parser.add_argument("--include-file-name", action="store_true")
    parser.add_argument("--min-file-size", type=int, default=1024)
    parser.add_argument("--max-time-skew", type=float, default=600.0)
    parser.add_argument("--require-local-file", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--require-resolution", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--require-capture-time", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--require-progress-event", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--run-negative", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--negative-payload-index", default="invalid-payload-index")
    parser.add_argument("--negative-camera-type", default="multispectral")
    parser.add_argument("--only", action="append", choices=["single", "burst", "timed"])
    args = parser.parse_args()
    args.host, args.port, args.sn = resolve_mqtt_settings(args)
    args.username, args.password = resolve_mqtt_credentials(args)

    client = MqttRequestClient(
        args.host,
        args.port,
        args.sn,
        args.qos,
        quiet_stale=args.quiet_stale or args.quiet,
        client_id_prefix="edge_gateway_payload_photo_deep",
        subscribe_events=True,
        username=args.username,
        password=args.password,
    )
    if not client.connect(args.timeout):
        print("MQTT connect timeout", file=sys.stderr)
        return 2

    try:
        rc = run_checks(client, args)
        if rc != 0:
            return rc
        stale_total = client.stale_total()
        if args.fail_on_stale and stale_total:
            print(f"stale replies observed: {client.stats}", file=sys.stderr)
            return 50
        return 0
    finally:
        client.disconnect()


if __name__ == "__main__":
    raise SystemExit(main())
