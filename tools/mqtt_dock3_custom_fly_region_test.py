#!/usr/bin/env python3
"""DJI Dock3 自定义飞行区 MQTT 专项验收脚本。

脚本模拟平台侧行为：
1. 向网关下发 flight_areas_update；
2. 监听网关发出的 requests/flight_areas_get；
3. 在 requests_reply 返回自定义飞行区文件列表或空列表；
4. 校验 flight_areas_sync_progress 和 services/flight_areas_get 查询结果。

默认要求设备侧生效成功；本地没有 MAVLink/FlySafe 生效器时，可加
--allow-apply-fail 验证平台协议、文件下载、缓存替换和清空语义。
"""

import argparse
import functools
import hashlib
import json
import sys
import threading
import time
import uuid
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Optional

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "tools"))

from mqtt_test_client import MqttRequestClient, result_code
from tool_config import add_config_argument, resolve_mqtt_credentials, resolve_mqtt_settings


FIXTURE_PATH = (
    PROJECT_ROOT
    / "tests"
    / "fixtures"
    / "custom_fly_region"
    / "geofence_1df20749f4117e1120b6176eca56004f.json"
)
REASON_AIRCRAFT_LOAD_FAILED = 8


class QuietRequestHandler(SimpleHTTPRequestHandler):
    def log_message(self, format, *args):  # noqa: A002
        return


class LocalFileServer:
    def __init__(self, directory: Path, host: str, port: int):
        self.directory = directory
        self.host = host
        self.port = int(port)
        self.server = None
        self.thread = None

    def __enter__(self):
        handler = functools.partial(QuietRequestHandler, directory=str(self.directory))
        self.server = ThreadingHTTPServer((self.host, self.port), handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        return self

    def __exit__(self, exc_type, exc, tb):
        if self.server:
            self.server.shutdown()
            self.server.server_close()
        if self.thread:
            self.thread.join(timeout=2.0)

    @property
    def base_url(self) -> str:
        host, port = self.server.server_address
        return f"http://{host}:{port}"


class Dock3CustomFlyRegionClient(MqttRequestClient):
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


def md5_file(path: Path) -> str:
    return hashlib.md5(path.read_bytes()).hexdigest()


def fixture_file_info(base_url: str) -> dict:
    return {
        "name": FIXTURE_PATH.name,
        "url": f"{base_url}/{FIXTURE_PATH.name}",
        "checksum": md5_file(FIXTURE_PATH),
        "size": FIXTURE_PATH.stat().st_size,
    }


def output_files(payload: dict) -> list[dict]:
    data = payload.get("data") if isinstance(payload, dict) else {}
    output = data.get("output") if isinstance(data, dict) else {}
    files = output.get("files") if isinstance(output, dict) else []
    return files if isinstance(files, list) else []


def event_status(event: dict) -> Optional[str]:
    data = event.get("data") if isinstance(event, dict) else {}
    return data.get("status") if isinstance(data, dict) else None


def event_reason(event: dict) -> Optional[int]:
    data = event.get("data") if isinstance(event, dict) else {}
    reason = data.get("reason") if isinstance(data, dict) else None
    return reason if isinstance(reason, int) else None


def build_requests_reply(request: dict, files: list[dict], sn: str) -> dict:
    return {
        "tid": request.get("tid", str(uuid.uuid4())),
        "bid": request.get("bid", str(uuid.uuid4())),
        "gateway": sn,
        "timestamp": int(time.time() * 1000),
        "method": "flight_areas_get",
        "data": {
            "result": 0,
            "output": {
                "files": files,
            },
        },
    }


def wait_flight_areas_get_request(client: Dock3CustomFlyRegionClient, request_topic: str,
                                  previous_count: int, timeout: float) -> Optional[dict]:
    deadline = time.time() + timeout
    seen = previous_count
    while time.time() < deadline:
        request = client.wait_observed(request_topic, seen, max(0.1, min(0.5, deadline - time.time())))
        if request is None:
            seen = client.observed_count(request_topic)
            continue
        seen = client.observed_count(request_topic)
        if isinstance(request, dict) and request.get("method") == "flight_areas_get":
            return request
    return None


def wait_sync_progress(client: Dock3CustomFlyRegionClient, previous_count: int, timeout: float,
                       allow_apply_fail: bool) -> tuple[int, Optional[dict]]:
    deadline = time.time() + timeout
    seen = previous_count
    last_event = None
    while time.time() < deadline:
        rc, event = client.wait_event(
            "flight_areas_sync_progress",
            seen,
            max(0.1, min(0.8, deadline - time.time())),
            expected_status=None,
        )
        if rc != 0:
            seen = client.event_count("flight_areas_sync_progress")
            continue
        seen = client.event_count("flight_areas_sync_progress")
        last_event = event
        status = event_status(event)
        reason = event_reason(event)
        if status == "synchronized":
            return 0, event
        if allow_apply_fail and status == "fail" and reason == REASON_AIRCRAFT_LOAD_FAILED:
            return 0, event
        if status == "fail":
            print(f"flight area sync failed: {json.dumps(event, ensure_ascii=False)}", file=sys.stderr)
            return 11, event
    print(f"timeout waiting flight_areas_sync_progress, last={last_event}", file=sys.stderr)
    return 12, last_event


def reply_to_next_get(client: Dock3CustomFlyRegionClient, args, request_topic: str,
                      reply_topic: str, previous_requests: int, files: list[dict]) -> int:
    request = wait_flight_areas_get_request(client, request_topic, previous_requests, args.timeout)
    if not request:
        print("timeout waiting gateway requests/flight_areas_get", file=sys.stderr)
        return 10
    reply = build_requests_reply(request, files, args.sn)
    client.publish_json(reply_topic, reply)
    print(f"published flight_areas_get requests_reply -> {reply_topic}, files={len(files)}")
    print(json.dumps(reply, ensure_ascii=False, indent=2))
    return 0


def run_update_round(client: Dock3CustomFlyRegionClient, args, files: list[dict]) -> int:
    request_topic = f"thing/product/{args.sn}/requests"
    reply_topic = f"thing/product/{args.sn}/requests_reply"
    previous_requests = client.observed_count(request_topic)
    previous_sync = client.event_count("flight_areas_sync_progress")

    rc, reply = client.request("flight_areas_update", None, args.timeout, [0], expected_status=None)
    if rc != 0:
        return rc
    if result_code(reply) != 0:
        print(f"flight_areas_update result mismatch: {reply}", file=sys.stderr)
        return 13

    rc = reply_to_next_get(client, args, request_topic, reply_topic, previous_requests, files)
    if rc != 0:
        return rc

    rc, _event = wait_sync_progress(client, previous_sync, args.timeout, args.allow_apply_fail)
    return rc


def run_custom_fly_region_checks(client: Dock3CustomFlyRegionClient, args, base_url: str) -> int:
    file_info = fixture_file_info(base_url)

    rc = run_update_round(client, args, [file_info])
    if rc != 0:
        return rc

    rc, listed = client.request("flight_areas_get", {}, args.timeout, [0], expected_status=None)
    if rc != 0:
        return rc
    files = output_files(listed)
    if len(files) != 1 or files[0].get("checksum") != file_info["checksum"]:
        print(f"flight_areas_get file list mismatch: {listed}", file=sys.stderr)
        return 14

    rc = run_update_round(client, args, [])
    if rc != 0:
        return rc

    rc, cleared = client.request("flight_areas_get", {}, args.timeout, [0], expected_status=None)
    if rc != 0:
        return rc
    if output_files(cleared):
        print(f"flight_areas_get should be empty after files=[]: {cleared}", file=sys.stderr)
        return 15

    return 0


def main():
    parser = argparse.ArgumentParser(description="Test DJI Dock3 custom fly region category")
    add_config_argument(parser)
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--sn", default=None)
    parser.add_argument("--username", default="")
    parser.add_argument("--password", default="")
    parser.add_argument("--timeout", type=float, default=20.0)
    parser.add_argument("--qos", type=int, choices=[0, 1, 2], default=1)
    parser.add_argument("--file-host", default="127.0.0.1", help="网关可访问的本地文件服务地址")
    parser.add_argument("--file-port", type=int, default=0, help="本地文件服务端口，0 表示自动分配")
    parser.add_argument("--file-base-url", default="", help="外部文件 URL；设置后不启动本地 HTTP 文件服务")
    parser.add_argument("--allow-apply-fail", action="store_true", help="允许无 MAVLink/FlySafe 生效器导致 reason=8")
    parser.add_argument("--quiet-stale", action="store_true")
    parser.add_argument("--fail-on-stale", action="store_true")
    args = parser.parse_args()
    args.host, args.port, args.sn = resolve_mqtt_settings(args)
    args.username, args.password = resolve_mqtt_credentials(args)

    if not FIXTURE_PATH.exists():
        print(f"fixture missing: {FIXTURE_PATH}", file=sys.stderr)
        return 2

    request_topic = f"thing/product/{args.sn}/requests"
    client = Dock3CustomFlyRegionClient(
        args.host,
        args.port,
        args.sn,
        args.qos,
        quiet_stale=args.quiet_stale,
        client_id_prefix="edge_gateway_dock3_custom_fly_region",
        subscribe_events=True,
        observe_topics={request_topic},
        username=args.username,
        password=args.password,
    )
    if not client.connect(args.timeout):
        print("timeout waiting for MQTT connection", file=sys.stderr)
        return 1

    try:
        if args.file_base_url:
            rc = run_custom_fly_region_checks(client, args, args.file_base_url.rstrip("/"))
        else:
            with LocalFileServer(FIXTURE_PATH.parent, args.file_host, args.file_port) as server:
                print(f"custom fly region file server -> {server.base_url}")
                rc = run_custom_fly_region_checks(client, args, server.base_url)
        if rc != 0:
            return rc
        ignored_total = client.ignored_total()
        if args.fail_on_stale and ignored_total:
            print(f"stale replies observed: {client.stats}", file=sys.stderr)
            print(json.dumps(client.stale_sample_summary(), ensure_ascii=False, indent=2), file=sys.stderr)
            return 9
        print(
            "dock3 custom fly region test ok "
            f"(accepted={client.stats['accepted']}, events={client.stats['events']}, "
            f"observed={client.stats['observed']}, ignored_total={ignored_total}, "
            f"allow_apply_fail={args.allow_apply_fail})"
        )
        return 0
    finally:
        client.disconnect()


if __name__ == "__main__":
    raise SystemExit(main())
