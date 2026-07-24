#!/usr/bin/env python3
"""Test ViewLink camera mode switching through Dock3 MQTT services."""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "tools"))

from mqtt_test_client import MqttRequestClient, result_code
from tool_config import add_config_argument, resolve_mqtt_settings
from viewlink_media_download import discover_media_links, download_file, latest_media_link_by_http_metadata


PHOTO_EXTENSIONS = (".jpg", ".jpeg", ".png", ".dng")
VIEWLINK_RECORD_MODES = {"photo": 1, "video": 2}


def _reply_data(reply: Any) -> dict[str, Any]:
    if not isinstance(reply, dict):
        return {}
    data = reply.get("data")
    return data if isinstance(data, dict) else {}


def _viewlink_result(reply: Any) -> dict[str, Any]:
    result = _reply_data(reply).get("viewlink_result")
    return result if isinstance(result, dict) else {}


def _print_reply(label: str, reply: Any) -> None:
    print(f"\n[{label}]")
    print(json.dumps(reply, ensure_ascii=False, indent=2))


def _request_service(
    client: MqttRequestClient,
    method: str,
    data: dict[str, Any],
    timeout: float,
    *,
    require_viewlink_success: bool = False,
    verbose: bool = False,
) -> tuple[int, Any]:
    rc, reply = client.request(
        method,
        data,
        timeout,
        channel="services",
        allowed_results={0},
    )
    if verbose or rc != 0:
        _print_reply(method, reply)
    if rc != 0:
        return rc, reply
    if result_code(reply) != 0:
        return 10, reply
    if require_viewlink_success:
        viewlink = _viewlink_result(reply)
        if not viewlink.get("success", False):
            print(f"{method} ViewLink SDK result is not success: {viewlink}", file=sys.stderr)
            return 11, reply
    return 0, reply


def _runtime_gimbal_control(client: MqttRequestClient, timeout: float) -> tuple[int, dict[str, Any]]:
    rc, reply = _request_service(client, "payload_status_query", {}, timeout)
    if rc != 0:
        return rc, {}

    payloads = _reply_data(reply).get("payloads")
    if not isinstance(payloads, list):
        print("payload_status_query did not return payloads[]", file=sys.stderr)
        return 21, {}

    for payload in payloads:
        if not isinstance(payload, dict) or payload.get("id") != "gimbal":
            continue
        channels = payload.get("channels")
        control = channels.get("control") if isinstance(channels, dict) else None
        if isinstance(control, dict):
            return 0, control

    print("payload_status_query did not return gimbal control configuration", file=sys.stderr)
    return 22, {}


def _check_bound(client: MqttRequestClient, timeout: float, *, skip: bool) -> int:
    if skip:
        return 0
    rc, reply = _request_service(client, "workspace_bind_status", {}, timeout)
    if rc != 0:
        return rc
    data = _reply_data(reply)
    if data.get("is_bind") not in {True, 1, "1"}:
        print("gateway is unbound; complete device binding before ViewLink actions", file=sys.stderr)
        return 23
    return 0


def _ensure_viewlink_connected(client: MqttRequestClient, args) -> int:
    if args.skip_viewlink_connect:
        return 0

    rc, runtime_control = _runtime_gimbal_control(client, args.timeout)
    if rc != 0:
        return rc

    sdk_dir = args.sdk_dir or str(runtime_control.get("sdk_dir") or "")
    gimbal_ip = args.gimbal_ip or str(runtime_control.get("host") or "")
    gimbal_port = args.gimbal_port or int(runtime_control.get("port") or 0)
    if not sdk_dir or not gimbal_ip or not gimbal_port:
        print(
            "incomplete gimbal control configuration; provide --sdk-dir, --gimbal-ip and --gimbal-port "
            "or fill payload.devices.gimbal.channels.control in config",
            file=sys.stderr,
        )
        return 24

    print(f"connect ViewLink: sdk_dir={sdk_dir}, ip={gimbal_ip}, port={gimbal_port}")
    steps = [
        ("viewlink_initialize", {"sdk_dir": sdk_dir}),
        ("viewlink_connect", {"ip": gimbal_ip, "port": gimbal_port, "conn_type": "tcp"}),
        ("viewlink_is_connected", {}),
    ]
    for method, data in steps:
        rc, reply = _request_service(
            client,
            method,
            data,
            args.timeout,
            require_viewlink_success=True,
            verbose=args.verbose,
        )
        if rc != 0:
            return rc
        if method == "viewlink_is_connected" and _viewlink_result(reply).get("connected") is not True:
            print("ViewLink reports connected=false", file=sys.stderr)
            return 25
    return 0


def _switch_camera_mode(client: MqttRequestClient, args, mode: str) -> int:
    mode_code = 0 if mode == "photo" else 1
    payload = {
        "payload_index": args.payload_index,
        "camera_type": args.camera_type,
        "camera_mode": mode_code,
        "mode": mode,
    }
    print(f"switch camera mode -> {mode}")
    rc, reply = _request_service(
        client,
        "camera_mode_switch",
        payload,
        args.timeout,
        require_viewlink_success=not args.allow_local_mode_only,
        verbose=True,
    )
    if rc != 0:
        return rc
    data = _reply_data(reply)
    if data.get("mode") != mode:
        print(f"camera_mode_switch replied unexpected mode: {data.get('mode')!r}", file=sys.stderr)
        return 30
    if args.verify_record_mode:
        return _verify_viewlink_record_mode(client, args, mode)
    return 0


def _verify_viewlink_record_mode(client: MqttRequestClient, args, mode: str) -> int:
    expected = VIEWLINK_RECORD_MODES.get(mode)
    if expected is None:
        return 0
    time.sleep(max(0.0, args.mode_settle))
    rc, reply = _request_service(
        client,
        "viewlink_get_record_mode",
        {},
        args.timeout,
        require_viewlink_success=True,
        verbose=True,
    )
    if rc != 0:
        return rc
    viewlink = _viewlink_result(reply)
    actual = viewlink.get("mode")
    mode_name = viewlink.get("mode_name", "")
    print(f"ViewLink record mode readback: mode={actual} {mode_name}")
    if actual != expected:
        if actual == 0 and not args.require_record_mode:
            print(
                "ViewLink SDK still reports record mode NONE after switching; "
                "continuing because some devices only expose an active mode during capture/recording."
            )
            return 0
        print(
            f"ViewLink record mode did not switch: expected {expected} for {mode}, actual {actual} ({mode_name})",
            file=sys.stderr,
        )
        return 31
    return 0


def _take_photo(client: MqttRequestClient, args) -> int:
    payload = {
        "payload_index": args.payload_index,
        "camera_index": args.camera_index,
        "camera_type": args.camera_type,
    }
    print("take independent ViewLink photo")
    rc, _reply = _request_service(client, "camera_photo_take", payload, args.timeout, verbose=True)
    if rc == 0:
        time.sleep(max(0.0, args.photo_wait))
    return rc


def _download_latest_photo(args) -> int:
    return _download_photo(args, previous_urls=None)


def _discover_photos(args) -> list[str]:
    return discover_media_links(
        args.media_url,
        args.media_timeout,
        PHOTO_EXTENSIONS,
        max_depth=max(0, args.media_max_depth),
    )


def _download_photo(args, previous_urls: set[str] | None) -> int:
    media_url = args.media_url
    if not media_url:
        return 0
    print(f"read ViewLink media page: {media_url}")
    deadline = time.time() + max(0.0, args.media_poll_timeout)
    photos: list[str] = []
    while True:
        try:
            photos = _discover_photos(args)
        except Exception as exc:
            print(f"failed to read ViewLink media page: {exc}", file=sys.stderr)
            return 40
        if previous_urls is None:
            break
        new_photos = [photo for photo in photos if photo not in previous_urls]
        if new_photos:
            photos = new_photos
            break
        if time.time() >= deadline:
            print("no new photo appeared on ViewLink media page after capture", file=sys.stderr)
            return 43
        time.sleep(max(0.1, args.media_poll_interval))
    if not photos:
        print("no photo links found on ViewLink media page", file=sys.stderr)
        return 41

    latest = latest_media_link_by_http_metadata(photos, args.media_timeout)
    print(f"latest_photo_url={latest}")
    if not args.download_media:
        return 0
    try:
        target = download_file(latest, Path(args.output_dir), args.media_timeout)
    except Exception as exc:
        print(f"download latest photo failed: {exc}", file=sys.stderr)
        return 42
    print(f"latest_photo_saved={target.resolve()}")
    return 0


def _mode_sequence(raw: str) -> list[str]:
    mode = raw.lower().strip()
    if mode == "both":
        return ["photo", "video", "photo"]
    if mode in {"photo", "video"}:
        return [mode]
    raise argparse.ArgumentTypeError("--mode must be photo, video, or both")


def _dry_run(args) -> None:
    steps: list[list[Any]] = []
    if not args.skip_bind_check:
        steps.append(["workspace_bind_status", {}])
    if not args.skip_viewlink_connect:
        steps.extend([
            ["payload_status_query", {}],
            ["viewlink_initialize", {"sdk_dir": args.sdk_dir or "<runtime sdk_dir>"}],
            [
                "viewlink_connect",
                {
                    "ip": args.gimbal_ip or "<runtime host>",
                    "port": args.gimbal_port or "<runtime port>",
                    "conn_type": "tcp",
                },
            ],
            ["viewlink_is_connected", {}],
        ])
    for mode in args.mode_sequence:
        steps.append([
            "camera_mode_switch",
            {
                "payload_index": args.payload_index,
                "camera_type": args.camera_type,
                "camera_mode": 0 if mode == "photo" else 1,
                "mode": mode,
            },
        ])
        if not args.skip_record_mode_verify and mode in VIEWLINK_RECORD_MODES:
            steps.append(["viewlink_get_record_mode", {}])
    if args.take_photo:
        steps.append([
            "camera_photo_take",
            {
                "payload_index": args.payload_index,
                "camera_index": args.camera_index,
                "camera_type": args.camera_type,
            },
        ])
    print(json.dumps({"dry_run": True, "mqtt_methods": steps}, ensure_ascii=False, indent=2))


def main() -> int:
    parser = argparse.ArgumentParser(description="Test ViewLink camera mode switching over MQTT")
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
    parser.add_argument("--mode-settle", type=float, default=0.5, help="seconds to wait before reading ViewLink mode")
    parser.add_argument(
        "--skip-record-mode-verify",
        action="store_true",
        help="do not read back ViewLink record mode after switching",
    )
    parser.add_argument(
        "--require-record-mode",
        action="store_true",
        help="fail when ViewLink readback stays NONE after setting photo/video mode",
    )
    parser.add_argument("--take-photo", action="store_true", help="take one independent photo after mode switching")
    parser.add_argument("--photo-wait", type=float, default=1.0)
    parser.add_argument("--media-url", default="", help="ViewLink media page, e.g. http://192.168.2.119:8554/download/")
    parser.add_argument("--download-media", action="store_true", help="download the latest discovered photo")
    parser.add_argument("--output-dir", default="logs/viewlink_media")
    parser.add_argument("--media-timeout", type=float, default=10.0)
    parser.add_argument("--media-poll-timeout", type=float, default=8.0)
    parser.add_argument("--media-poll-interval", type=float, default=1.0)
    parser.add_argument("--media-max-depth", type=int, default=4)
    parser.add_argument("--skip-bind-check", action="store_true")
    parser.add_argument("--skip-viewlink-connect", action="store_true")
    parser.add_argument(
        "--allow-local-mode-only",
        action="store_true",
        help="do not fail when camera_mode_switch only updates gateway state without SDK success",
    )
    parser.add_argument("--quiet-stale", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    args.host, args.port, args.sn = resolve_mqtt_settings(args)
    args.mode_sequence = args.mode
    args.verify_record_mode = not args.skip_record_mode_verify

    if args.photo_wait < 0:
        parser.error("--photo-wait must be non-negative")
    if args.mode_settle < 0:
        parser.error("--mode-settle must be non-negative")
    if args.media_poll_timeout < 0:
        parser.error("--media-poll-timeout must be non-negative")
    if args.media_poll_interval < 0:
        parser.error("--media-poll-interval must be non-negative")
    if args.download_media and not args.media_url:
        parser.error("--download-media requires --media-url")

    if args.dry_run:
        _dry_run(args)
        return 0

    client = MqttRequestClient(
        args.host,
        args.port,
        args.sn,
        qos=args.qos,
        quiet_stale=args.quiet_stale,
        client_id_prefix="edge_gateway_viewlink_camera_mode",
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
        for mode in args.mode_sequence:
            rc = _switch_camera_mode(client, args, mode)
            if rc != 0:
                return rc
        previous_photos = None
        if args.take_photo and args.media_url:
            try:
                previous_photos = set(_discover_photos(args))
                print(f"ViewLink media baseline photos={len(previous_photos)}")
            except Exception as exc:
                print(f"failed to read ViewLink media baseline: {exc}", file=sys.stderr)
                return 40
        if args.take_photo:
            rc = _take_photo(client, args)
            if rc != 0:
                return rc
        rc = _download_photo(args, previous_photos)
        if rc != 0:
            return rc
        print(
            "viewlink camera mode test ok "
            f"(modes={','.join(args.mode_sequence)}, take_photo={bool(args.take_photo)})"
        )
        return 0
    finally:
        client.disconnect()


if __name__ == "__main__":
    raise SystemExit(main())
