#!/usr/bin/env python3
"""Acceptance checks for the YCam multispectral payload."""

import argparse
import sys
import tempfile
import time
import zipfile
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from mqtt_test_client import MqttRequestClient
from src.cloud_api.dock3_wayline import parse_wayline_file
from tool_config import add_config_argument, load_config, resolve_mqtt_settings


SPECTRAL_PAYLOAD_INDEX = "52-0-2"
SPECTRAL_VIDEO_INDEX = "spectral-0"
SPECTRAL_SOURCE_KEY = "spectral_camera"
SPECTRAL_CAMERA_TYPE = "multispectral"


class SpectralCameraClient(MqttRequestClient):
    def request(self, method, data, timeout, allowed_results=None, expected_status=None):
        return super().request(
            method,
            data,
            timeout,
            channel="services",
            allowed_results={0} if allowed_results is None else set(allowed_results),
            expected_status=expected_status,
        )

    def ignored_total(self):
        return self.stale_total()


def spectral_video_id(gateway_sn: str) -> str:
    return f"{gateway_sn}/{SPECTRAL_PAYLOAD_INDEX}/{SPECTRAL_VIDEO_INDEX}"


def write_spectral_wpml(path: Path) -> None:
    wpml = f"""<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2" xmlns:wpml="http://www.dji.com/wpmz/1.0.2">
  <Document>
    <Placemark>
      <Point><coordinates>149.165237,-35.363262,120</coordinates></Point>
      <wpml:waypointSpeed>6</wpml:waypointSpeed>
      <wpml:actionGroup>
        <wpml:action>
          <wpml:actionActuatorFunc>takePhoto</wpml:actionActuatorFunc>
          <wpml:actionActuatorFuncParam>
            <wpml:payloadIndex>{SPECTRAL_PAYLOAD_INDEX}</wpml:payloadIndex>
            <wpml:cameraType>{SPECTRAL_CAMERA_TYPE}</wpml:cameraType>
          </wpml:actionActuatorFuncParam>
        </wpml:action>
      </wpml:actionGroup>
    </Placemark>
    <Placemark>
      <Point><coordinates>149.165537,-35.363562,130</coordinates></Point>
      <wpml:waypointSpeed>6</wpml:waypointSpeed>
    </Placemark>
  </Document>
</kml>
"""
    path.write_text(wpml, encoding="utf-8")


def write_spectral_kmz(path: Path) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        wpml_path = Path(tmp) / "template.wpml"
        write_spectral_wpml(wpml_path)
        with zipfile.ZipFile(path, "w") as archive:
            archive.write(wpml_path, "wpmz/template.wpml")


def file_url(path: Path) -> str:
    return path.expanduser().resolve().as_uri()


def prepare_payload(flight_id: str, wayline_file: Path) -> dict[str, Any]:
    return {
        "flight_id": flight_id,
        "task_type": 0,
        "execute_time": int(time.time() * 1000) + 30_000,
        "file": {
            "url": file_url(wayline_file),
            "fingerprint": "spectral-camera-wayline",
        },
        "ready_conditions": {},
        "executable_conditions": {"storage_capacity": 0},
        "rth_altitude": 80,
        "out_of_control_action": 0,
        "exit_wayline_when_rc_lost": 0,
        "simulate_mission": {
            "is_enable": 1,
            "latitude": -35.363262,
            "longitude": 149.165237,
        },
    }


def live_capacity_cameras(payload: dict) -> list[dict]:
    capacity = payload.get("data", {}).get("live_capacity", {})
    devices = capacity.get("device_list", []) if isinstance(capacity, dict) else []
    cameras: list[dict] = []
    for device in devices:
        if isinstance(device, dict):
            cameras.extend(item for item in device.get("camera_list", []) if isinstance(item, dict))
    return cameras


def files_from_reply(payload: dict) -> list[dict]:
    files = payload.get("data", {}).get("files", []) if isinstance(payload, dict) else []
    return files if isinstance(files, list) else []


def find_file(files: list[dict], name: str) -> dict | None:
    for item in files:
        if item.get("name") == name or item.get("file_name") == name:
            return item
    return None


def resolve_media_storage(config_path: str) -> Path:
    config = load_config(config_path)
    media_config = config.get("media") if isinstance(config, dict) else {}
    configured = str((media_config or {}).get("storage_path") or "./data/media")
    path = Path(configured).expanduser()
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return path


def run_video_pull_check(client: SpectralCameraClient, args) -> int:
    video_id = args.video_id or spectral_video_id(args.sn)
    rc, capability = client.request("live_capability_query", {}, args.timeout, [0], "ok")
    if rc != 0:
        return rc
    cameras = live_capacity_cameras(capability)
    spectral = next((item for item in cameras if item.get("camera_index") == SPECTRAL_PAYLOAD_INDEX), None)
    if not spectral:
        print(f"live_capability_query 未发现多光谱相机 {SPECTRAL_PAYLOAD_INDEX}", file=sys.stderr)
        return 20
    videos = spectral.get("video_list", [])
    if not any(item.get("video_index") == SPECTRAL_VIDEO_INDEX for item in videos if isinstance(item, dict)):
        print(f"多光谱相机缺少视频索引 {SPECTRAL_VIDEO_INDEX}: {spectral}", file=sys.stderr)
        return 21

    event_before = client.event_count("live_status_notify")
    rc, start = client.request(
        "live_start_push",
        {
            "url": args.live_url,
            "url_type": args.live_url_type,
            "video_id": video_id,
            "video_quality": args.video_quality,
            "video_type": "normal",
        },
        args.timeout,
        [0],
        "in_progress",
    )
    if rc != 0:
        return rc
    if not start.get("data", {}).get("session_id"):
        print(f"live_start_push 缺少 session_id: {start}", file=sys.stderr)
        return 22
    rc, _ = client.wait_event("live_status_notify", event_before, args.timeout, expected_status="in_progress")
    if rc != 0:
        return rc

    if args.keep_stream:
        print(f"多光谱视频拉流已保持运行: video_id={video_id}")
        return 0

    rc, _ = client.request("live_stop_push", {"video_id": video_id}, args.timeout, [0], "ok")
    if rc != 0:
        return rc
    print(f"多光谱视频拉流测试通过: video_id={video_id}")
    return 0


def run_photo_return_check(client: SpectralCameraClient, args) -> int:
    photo_name = args.photo_name or f"spectral-camera-{int(time.time() * 1000)}.jpg"
    rc, photo = client.request(
        "camera_photo_take",
        {
            "payload_index": SPECTRAL_PAYLOAD_INDEX,
            "camera_type": SPECTRAL_CAMERA_TYPE,
            "file_name": photo_name,
        },
        args.timeout,
        [0],
        "ok",
    )
    if rc != 0:
        return rc
    if photo.get("data", {}).get("payload_id") != SPECTRAL_SOURCE_KEY:
        print(f"camera_photo_take 未返回多光谱 payload: {photo}", file=sys.stderr)
        return 30

    # Give the media scanner a short, bounded window. The current gateway writes
    # the cache synchronously, while a native YCam receiver may finish shortly after ACK.
    deadline = time.monotonic() + args.photo_wait
    latest = None
    while time.monotonic() < deadline:
        rc, listed = client.request(
            "file_list_get",
            {"media_type": "photo", "page": 1, "page_size": 100},
            args.timeout,
            [0],
            "ok",
        )
        if rc != 0:
            return rc
        latest = listed
        item = find_file(files_from_reply(listed), photo_name)
        if item:
            print(f"多光谱相片回传测试通过: {photo_name}")
            return 0
        time.sleep(0.2)

    storage = resolve_media_storage(args.config)
    print(f"未在媒体列表发现多光谱照片: name={photo_name}, storage={storage}, latest={latest}", file=sys.stderr)
    return 31


def run_wayline_photo_point_check(client: SpectralCameraClient, args) -> int:
    with tempfile.TemporaryDirectory() as tmp:
        wayline_path = Path(tmp) / "spectral-camera-wayline.kmz"
        write_spectral_kmz(wayline_path)
        summary, waypoints = parse_wayline_file({"path": str(wayline_path)})
        if not summary.get("parsed") or len(waypoints) < 2:
            print(f"多光谱航线文件本地解析失败: {summary}", file=sys.stderr)
            return 40
        actions = waypoints[0].get("actions") or []
        photo_action = next((item for item in actions if item.get("action_type") == "photo"), None)
        if not photo_action or photo_action.get("payload_index") != SPECTRAL_PAYLOAD_INDEX:
            print(f"航线拍照点未携带多光谱 payload: {actions}", file=sys.stderr)
            return 41

        flight_id = args.flight_id or f"spectral-camera-{int(time.time())}"
        rc, prepared = client.request(
            "flighttask_prepare",
            prepare_payload(flight_id, wayline_path),
            args.timeout,
            [0],
            "sent",
        )
        if rc != 0:
            return rc
        file_summary = prepared.get("data", {}).get("output", {}).get("ext", {}).get("file", {})
        if not file_summary.get("parsed") or file_summary.get("waypoint_count", 0) < 2:
            print(f"网关未解析多光谱拍照航线: {file_summary}", file=sys.stderr)
            return 42
        print(f"多光谱航线拍照点测试通过: flight_id={flight_id}, waypoints={file_summary.get('waypoint_count')}")
        return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Test multispectral YCam payload functions")
    add_config_argument(parser)
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--sn", default=None)
    parser.add_argument("--timeout", type=float, default=12.0)
    parser.add_argument("--qos", type=int, choices=[0, 1, 2], default=1)
    parser.add_argument("--only", choices=["all", "video", "photo", "wayline"], default="all")
    parser.add_argument("--video-id", default="")
    parser.add_argument("--live-url", default="mock://spectral/live")
    parser.add_argument("--live-url-type", type=int, default=4)
    parser.add_argument("--video-quality", type=int, choices=[0, 1, 2, 3, 4], default=2)
    parser.add_argument("--keep-stream", action="store_true")
    parser.add_argument("--photo-name", default="")
    parser.add_argument("--photo-wait", type=float, default=3.0)
    parser.add_argument("--flight-id", default="")
    parser.add_argument("--quiet-stale", action="store_true")
    parser.add_argument("--fail-on-stale", action="store_true")
    args = parser.parse_args()
    args.host, args.port, args.sn = resolve_mqtt_settings(args)

    client = SpectralCameraClient(
        args.host,
        args.port,
        args.sn,
        args.qos,
        quiet_stale=args.quiet_stale,
        client_id_prefix="edge_gateway_spectral_camera",
        subscribe_events=True,
    )
    if not client.connect(args.timeout):
        print("timeout waiting for MQTT connection", file=sys.stderr)
        return 1

    try:
        checks = []
        if args.only in {"all", "video"}:
            checks.append(("video", run_video_pull_check))
        if args.only in {"all", "photo"}:
            checks.append(("photo", run_photo_return_check))
        if args.only in {"all", "wayline"}:
            checks.append(("wayline", run_wayline_photo_point_check))

        for name, check in checks:
            print(f"开始多光谱{name}功能测试")
            rc = check(client, args)
            if rc != 0:
                return rc

        ignored_total = client.ignored_total()
        if args.fail_on_stale and ignored_total:
            print(f"stale replies observed: {client.stats}", file=sys.stderr)
            return 9
        print(
            "多光谱相机功能测试通过 "
            f"(checks={','.join(name for name, _ in checks)}, "
            f"accepted={client.stats['accepted']}, events={client.stats['events']}, "
            f"ignored_total={ignored_total})"
        )
        return 0
    finally:
        client.disconnect()


if __name__ == "__main__":
    raise SystemExit(main())
