#!/usr/bin/env python3
"""通过 MQTT 验证单 ViewLink 云台的真实上云链路。"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from mqtt_test_client import MqttRequestClient, result_code
from src.utils.dji_error_codes import INVALID_COMMAND_FORMAT
from tool_config import add_config_argument, load_config, resolve_mqtt_settings


def files_from_reply(payload: dict | None) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    data = payload.get("data")
    if not isinstance(data, dict):
        return []
    files = data.get("files") or data.get("list") or []
    return files if isinstance(files, list) else []


def file_identity(item: dict[str, Any]) -> str:
    return str(
        item.get("file_id")
        or item.get("fingerprint")
        or item.get("download_url")
        or item.get("object_key")
        or item.get("name")
        or ""
    )


def media_ids(files: list[dict[str, Any]]) -> set[str]:
    return {file_identity(item) for item in files if file_identity(item)}


def newest_media(files: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not files:
        return None
    return sorted(files, key=lambda item: int(item.get("create_time", 0) or 0), reverse=True)[0]


class GimbalRealChainClient(MqttRequestClient):
    def service(self, method, data, timeout, allowed_results=None, expected_status=None):
        return self.request(
            method,
            data,
            timeout,
            channel="services",
            allowed_results={0} if allowed_results is None else set(allowed_results),
            expected_status=expected_status,
        )


def load_gimbal_control(args) -> dict[str, Any]:
    config = load_config(args.config)
    control = (
        config.get("payload", {})
        .get("devices", {})
        .get("gimbal", {})
        .get("channels", {})
        .get("control", {})
    )
    control = control if isinstance(control, dict) else {}
    return {
        "sdk_dir": args.sdk_dir or str(control.get("sdk_dir") or ""),
        "ip": args.gimbal_ip or str(control.get("host") or ""),
        "port": args.gimbal_port or int(control.get("port") or 0),
    }


def ensure_gateway_bound(client: GimbalRealChainClient, args) -> int:
    rc, workspace = client.service("workspace_get", {}, args.timeout, {0})
    if rc != 0:
        return rc
    data = workspace.get("data", {}) if isinstance(workspace, dict) else {}
    if data.get("is_bound") in {True, 1, "1", "true", "True"}:
        print(f"gateway already bound (workspace_id={data.get('workspace_id')})")
        return 0

    rc, _ = client.service(
        "device_binding",
        {"workspace_id": args.workspace_id, "workspace_name": args.workspace_name},
        args.timeout,
        {0},
    )
    if rc == 0:
        print(f"gateway bind ok (workspace_id={args.workspace_id})")
    return rc


def ensure_gimbal_connected(client: GimbalRealChainClient, args) -> int:
    rc, connected = client.service("viewlink_is_connected", {}, args.timeout, {0})
    if rc == 0:
        result = connected.get("data", {}).get("viewlink_result", {}) if isinstance(connected, dict) else {}
        if result.get("connected") is True:
            print("gimbal already connected")
            return 0

    control = load_gimbal_control(args)
    if not control["sdk_dir"] or not control["ip"] or not control["port"]:
        print("missing ViewLink config; pass --sdk-dir, --gimbal-ip and --gimbal-port", file=sys.stderr)
        return 11
    for method, data in [
        ("viewlink_initialize", {"sdk_dir": control["sdk_dir"]}),
        ("viewlink_connect", {"ip": control["ip"], "port": control["port"], "conn_type": "tcp"}),
        ("viewlink_is_connected", {}),
    ]:
        rc, reply = client.service(method, data, args.timeout, {0})
        if rc != 0:
            return rc
        if method == "viewlink_is_connected":
            result = reply.get("data", {}).get("viewlink_result", {}) if isinstance(reply, dict) else {}
            if result.get("connected") is not True:
                print(f"gimbal connect failed: {json.dumps(reply, ensure_ascii=False)}", file=sys.stderr)
                return 12
    print(f"gimbal connect ok (ip={control['ip']}, port={control['port']})")
    return 0


def list_media(client: GimbalRealChainClient, media_type: str, args) -> tuple[int, list[dict[str, Any]]]:
    rc, reply = client.service(
        "file_list_get",
        {"media_type": media_type, "page": 1, "page_size": args.page_size},
        args.timeout,
        {0},
        "ok",
    )
    return rc, files_from_reply(reply)


def wait_new_media(
    client: GimbalRealChainClient,
    media_type: str,
    before_ids: set[str],
    args,
) -> tuple[int, dict[str, Any] | None]:
    deadline = time.time() + args.media_wait
    last_files: list[dict[str, Any]] = []
    while time.time() < deadline:
        rc, files = list_media(client, media_type, args)
        if rc != 0:
            return rc, None
        last_files = files
        fresh = [item for item in files if file_identity(item) not in before_ids]
        if fresh:
            return 0, newest_media(fresh)
        time.sleep(args.poll_interval)
    print(
        f"timeout waiting for new {media_type}; last_count={len(last_files)}, before_count={len(before_ids)}",
        file=sys.stderr,
    )
    return 20, None


def download_media_item(client: GimbalRealChainClient, item: dict[str, Any], args) -> int:
    identity = file_identity(item)
    data = {"file_ids": [identity]} if identity else {"download_url": item.get("download_url", "")}
    rc, reply = client.service("file_download", data, args.timeout, {0}, "ok")
    if rc != 0:
        return rc
    files = files_from_reply(reply)
    if not files:
        print(f"file_download returned no files: {json.dumps(reply, ensure_ascii=False)}", file=sys.stderr)
        return 21
    downloaded = files[0]
    print(f"media cached locally: {json.dumps(downloaded, ensure_ascii=False)}")
    return 0


def verify_playback(play_url: str, ffmpeg_bin: str, timeout: float, output: Path) -> int:
    output.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        ffmpeg_bin,
        "-y",
        "-i",
        play_url,
        "-frames:v",
        "1",
        str(output),
    ]
    print(f"verify playback: {' '.join(cmd)}")
    try:
        result = subprocess.run(cmd, text=True, capture_output=True, timeout=timeout)
    except FileNotFoundError:
        print(f"ffmpeg not found: {ffmpeg_bin}", file=sys.stderr)
        return 30
    except subprocess.TimeoutExpired:
        print(f"playback verify timeout: {play_url}", file=sys.stderr)
        return 31
    if result.returncode != 0:
        print(result.stderr[-1000:], file=sys.stderr)
        return 32
    if not output.exists() or output.stat().st_size <= 0:
        print(f"playback frame not created: {output}", file=sys.stderr)
        return 33
    print(f"playback frame saved -> {output.resolve()}")
    return 0


def run_checks(client: GimbalRealChainClient, args) -> int:
    if not args.skip_bind_gateway:
        rc = ensure_gateway_bound(client, args)
        if rc != 0:
            return rc
    if not args.skip_connect_gimbal:
        rc = ensure_gimbal_connected(client, args)
        if rc != 0:
            return rc

    rc, photos_before = list_media(client, "photo", args)
    if rc != 0:
        return rc
    rc, videos_before = list_media(client, "video", args)
    if rc != 0:
        return rc
    print(f"baseline media: photos={len(photos_before)}, videos={len(videos_before)}")

    if not args.skip_photo:
        rc, _ = client.service("camera_photo_take", {"camera_index": 0, "camera_type": args.camera_type}, args.timeout, {0})
        if rc != 0:
            return rc
        rc, photo = wait_new_media(client, "photo", media_ids(photos_before), args)
        if rc != 0:
            return rc
        print(f"photo file observed: {json.dumps(photo, ensure_ascii=False)}")
        if args.download_media and photo:
            rc = download_media_item(client, photo, args)
            if rc != 0:
                return rc

    if not args.skip_record:
        rc, _ = client.service(
            "camera_recording_start",
            {
                "camera_index": 0,
                "camera_type": args.camera_type,
                "resolution": args.resolution,
                "framerate": args.framerate,
                "video_id": args.video_id,
            },
            args.timeout,
            {0},
        )
        if rc != 0:
            return rc
        time.sleep(args.record_seconds)
        rc, _ = client.service(
            "camera_recording_stop",
            {"camera_index": 0, "camera_type": args.camera_type, "video_id": args.video_id},
            args.timeout,
            {0},
        )
        if rc != 0:
            return rc
        rc, video = wait_new_media(client, "video", media_ids(videos_before), args)
        if rc != 0:
            return rc
        print(f"video file observed: {json.dumps(video, ensure_ascii=False)}")
        if args.download_media and video:
            rc = download_media_item(client, video, args)
            if rc != 0:
                return rc

    if args.push_url:
        video_id = args.live_video_id or f"{args.sn}/0/gimbal"
        rc, start = client.service(
            "live_start_push",
            {
                "url": args.push_url,
                "url_type": args.url_type,
                "video_id": video_id,
                "video_quality": args.video_quality,
                "video_type": "gimbal",
            },
            args.timeout,
            {0},
            "in_progress",
        )
        if rc != 0:
            return rc
        print(f"live push started: session_id={start.get('data', {}).get('session_id')}")
        if args.play_url and args.verify_playback:
            time.sleep(args.playback_delay)
            rc = verify_playback(args.play_url, args.ffmpeg_bin, args.playback_timeout, Path(args.playback_frame))
            if rc != 0:
                return rc
        if args.stop_live:
            rc, stop = client.service(
                "live_stop_push",
                {"video_id": video_id},
                args.timeout,
                {0, INVALID_COMMAND_FORMAT},
            )
            if rc != 0:
                return rc
            if result_code(stop) == INVALID_COMMAND_FORMAT:
                print(f"live_stop_push returned not-running state: {json.dumps(stop, ensure_ascii=False)}")
            else:
                print("live push stopped")
        else:
            print("live push kept running; pass --stop-live to stop during this test")

    print("dock3 gimbal real-chain test ok")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Test the real ViewLink gimbal chain over Dock3 MQTT")
    add_config_argument(parser)
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--sn", default=None)
    parser.add_argument("--timeout", type=float, default=12.0)
    parser.add_argument("--qos", type=int, choices=[0, 1, 2], default=1)
    parser.add_argument("--workspace-id", default="local_test_workspace")
    parser.add_argument("--workspace-name", default="本地测试工作空间")
    parser.add_argument("--skip-bind-gateway", action="store_true")
    parser.add_argument("--skip-connect-gimbal", action="store_true")
    parser.add_argument("--sdk-dir", default="")
    parser.add_argument("--gimbal-ip", default="")
    parser.add_argument("--gimbal-port", type=int, default=0)
    parser.add_argument("--camera-type", default="gimbal")
    parser.add_argument("--resolution", default="1080p")
    parser.add_argument("--framerate", type=int, default=30)
    parser.add_argument("--video-id", default="gimbal-real-chain-record")
    parser.add_argument("--record-seconds", type=float, default=4.0)
    parser.add_argument("--media-wait", type=float, default=30.0)
    parser.add_argument("--poll-interval", type=float, default=2.0)
    parser.add_argument("--page-size", type=int, default=100)
    parser.add_argument("--skip-photo", action="store_true")
    parser.add_argument("--skip-record", action="store_true")
    parser.add_argument("--download-media", action="store_true", help="发现照片/录像后调用 file_download 缓存到网关本地。")
    parser.add_argument("--push-url", default="")
    parser.add_argument("--play-url", default="")
    parser.add_argument("--url-type", type=int, default=1)
    parser.add_argument("--video-quality", type=int, default=3)
    parser.add_argument("--live-video-id", default="")
    parser.add_argument("--stop-live", action="store_true")
    parser.add_argument("--verify-playback", action="store_true")
    parser.add_argument("--playback-delay", type=float, default=3.0)
    parser.add_argument("--playback-timeout", type=float, default=20.0)
    parser.add_argument("--playback-frame", default="logs/gimbal_public_playback.jpg")
    parser.add_argument("--ffmpeg-bin", default="ffmpeg")
    parser.add_argument("--quiet-stale", action="store_true")
    parser.add_argument("--fail-on-stale", action="store_true")
    args = parser.parse_args()
    args.host, args.port, args.sn = resolve_mqtt_settings(args)

    client = GimbalRealChainClient(
        args.host,
        args.port,
        args.sn,
        args.qos,
        quiet_stale=args.quiet_stale,
        client_id_prefix="edge_gateway_dock3_gimbal_real_chain",
        subscribe_events=True,
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
            return 10
        return 0
    finally:
        client.disconnect()


if __name__ == "__main__":
    raise SystemExit(main())
