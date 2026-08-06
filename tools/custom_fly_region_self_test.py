#!/usr/bin/env python3
"""自定义飞行区本地自测脚本。

该脚本不依赖真机、不依赖网关进程：
- 启动本地 HTTP 服务模拟平台文件下载地址；
- 直接调用 FlightAreaManager 模拟 flight_areas_get requests_reply；
- 用假的设备生效器接收 apply_files，避免要求 MAVLink/FlySafe 设备在线；
- 覆盖文件同步、空列表清空、checksum 失败、Polygon/MultiPolygon/Circle 解析和边界状态变化告警。
"""

import argparse
import functools
import hashlib
import json
import sys
import tempfile
import threading
import time
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

import src.modules.flight_area_manager as flight_area_module
from src.modules.flight_area_device_applier import FlightAreaDeviceApplier
from src.modules.flight_area_manager import FlightAreaManager, REASON_CHECKSUM_FAILED


FIXTURE_PATH = (
    PROJECT_ROOT
    / "tests"
    / "fixtures"
    / "custom_fly_region"
    / "geofence_1df20749f4117e1120b6176eca56004f.json"
)


class SelfTestFailure(Exception):
    """自测失败。"""


class QuietRequestHandler(SimpleHTTPRequestHandler):
    """压低 HTTP 文件服务日志，避免测试日志被请求明细刷屏。"""

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


class FakeDeviceApplier:
    def __init__(self):
        self.real_parser = FlightAreaDeviceApplier()
        self.applied_files: list[list[dict]] = []

    def apply_files(self, files: list[dict]) -> dict:
        self.applied_files.append(list(files))
        return {
            "success": True,
            "applied": True,
            "fence_item_count": 0,
        }

    def extract_area_polygons(self, files: list[dict]) -> list[dict]:
        return self.real_parser.extract_area_polygons(files)


def assert_true(value: Any, message: str):
    if not value:
        raise SelfTestFailure(message)


def assert_equal(actual: Any, expected: Any, message: str):
    if actual != expected:
        raise SelfTestFailure(f"{message}: expected={expected!r}, actual={actual!r}")


def md5_file(path: Path) -> str:
    return hashlib.md5(path.read_bytes()).hexdigest()


def fixture_file_info(base_url: str) -> dict:
    checksum = md5_file(FIXTURE_PATH)
    return {
        "name": FIXTURE_PATH.name,
        "url": f"{base_url}/{FIXTURE_PATH.name}",
        "checksum": checksum,
        "size": FIXTURE_PATH.stat().st_size,
    }


def build_reply(files: list[dict]) -> dict:
    return {
        "method": "flight_areas_get",
        "tid": f"self-test-{int(time.time() * 1000)}",
        "data": {
            "result": 0,
            "output": {
                "files": files,
            },
        },
    }


def make_manager(cache_dir: Path) -> tuple[FlightAreaManager, FakeDeviceApplier, list[dict]]:
    old_cache_dir = flight_area_module.FLIGHT_AREA_CACHE_DIR
    flight_area_module.FLIGHT_AREA_CACHE_DIR = str(cache_dir)
    try:
        manager = FlightAreaManager()
    finally:
        flight_area_module.FLIGHT_AREA_CACHE_DIR = old_cache_dir
    applier = FakeDeviceApplier()
    events: list[dict] = []
    manager.set_device_applier(applier)
    manager.set_event_callback(events.append)
    return manager, applier, events


def test_file_sync_and_services_get(cache_dir: Path, base_url: str) -> dict:
    manager, applier, events = make_manager(cache_dir)

    ok = manager._sync_from_cloud_reply(build_reply([fixture_file_info(base_url)]))

    assert_true(ok, "文件同步应成功")
    assert_equal(len(manager._files), 1, "本地缓存文件数量应为 1")
    assert_equal(len(applier.applied_files), 1, "设备生效器应被调用 1 次")
    assert_equal(len(applier.applied_files[-1]), 1, "设备生效器应收到 1 个文件")
    assert_true(manager._monitored_areas, "同步后应刷新边界监控区域")
    assert_true(any(e.get("method") == "flight_areas_sync_progress" for e in events), "应上报同步进度事件")

    reply = manager.handle_flight_areas_get({
        "tid": "self-test-services-get",
        "bid": "self-test",
        "method": "flight_areas_get",
        "data": {},
    })
    files = reply.get("data", {}).get("output", {}).get("files", [])
    assert_equal(len(files), 1, "services 兼容查询应返回 1 个文件")
    assert_equal(files[0].get("checksum"), md5_file(FIXTURE_PATH), "services 查询 checksum 应一致")
    return {"files": len(files), "events": len(events)}


def test_empty_files_clear(cache_dir: Path, base_url: str) -> dict:
    manager, applier, _events = make_manager(cache_dir)
    assert_true(manager._sync_from_cloud_reply(build_reply([fixture_file_info(base_url)])), "预置同步应成功")

    ok = manager._sync_from_cloud_reply(build_reply([]))

    assert_true(ok, "空列表清空应成功")
    assert_equal(manager._files, [], "files: [] 后本地缓存应清空")
    assert_equal(applier.applied_files[-1], [], "files: [] 后应向设备侧应用空列表")
    assert_equal(manager._monitored_areas, [], "files: [] 后边界监控区域应清空")
    return {"applied_calls": len(applier.applied_files)}


def test_checksum_failure(cache_dir: Path, base_url: str) -> dict:
    manager, _applier, events = make_manager(cache_dir)
    bad_info = fixture_file_info(base_url)
    bad_info["checksum"] = "0" * 32

    ok = manager._sync_from_cloud_reply(build_reply([bad_info]))

    assert_true(not ok, "checksum 错误时同步应失败")
    assert_equal(manager._last_sync.get("reason"), REASON_CHECKSUM_FAILED, "失败 reason 应为 checksum 失败")
    assert_true(any(e.get("data", {}).get("reason") == REASON_CHECKSUM_FAILED for e in events), "应上报 checksum 失败事件")
    return {"reason": manager._last_sync.get("reason")}


def test_parser_shapes() -> dict:
    applier = FlightAreaDeviceApplier()
    document = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "id": "polygon-nfz",
                "geofence_type": "nfz",
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[[120.0, 30.0], [120.001, 30.0], [120.001, 30.001], [120.0, 30.001], [120.0, 30.0]]],
                },
                "properties": {},
            },
            {
                "type": "Feature",
                "id": "multi-dfence",
                "geofence_type": "dfence",
                "geometry": {
                    "type": "MultiPolygon",
                    "coordinates": [[[[121.0, 31.0], [121.001, 31.0], [121.001, 31.001], [121.0, 31.001], [121.0, 31.0]]]],
                },
                "properties": {},
            },
            {
                "type": "Feature",
                "id": "circle-dfence",
                "geofence_type": "dfence",
                "geometry": {
                    "type": "Point",
                    "coordinates": [122.0, 32.0],
                },
                "properties": {
                    "subType": "Circle",
                    "radius": 100,
                },
            },
        ],
    }

    polygons = applier._extract_polygons(document, "self-test.json")

    assert_equal(len(polygons), 3, "应解析 Polygon/MultiPolygon/Circle 三类区域")
    assert_equal(polygons[0]["mode"], "exclusion", "nfz 应映射为禁飞区")
    assert_equal(polygons[1]["mode"], "inclusion", "dfence 应映射为飞行围栏")
    assert_equal(len(polygons[2]["points"]), 36, "圆形区域应转换为 36 个顶点")
    assert_equal(polygons[0]["area_id"], "polygon-nfz", "应保留 Feature.id 作为 area_id")
    return {"polygons": len(polygons)}


def test_boundary_state_events(cache_dir: Path) -> dict:
    manager, _applier, events = make_manager(cache_dir)
    manager._monitored_areas = [{
        "area_id": "boundary-test",
        "mode": "exclusion",
        "points": [
            {"latitude": 0.0, "longitude": 0.0},
            {"latitude": 0.0, "longitude": 0.001},
            {"latitude": 0.001, "longitude": 0.001},
            {"latitude": 0.001, "longitude": 0.0},
        ],
    }]

    manager.update_aircraft_location(0.0020, 0.0005)
    assert_equal(len(events), 0, "初始远离不应上报")

    manager.update_aircraft_location(0.0013, 0.0005)
    manager.update_aircraft_location(0.0013, 0.0005)
    manager.update_aircraft_location(0.0005, 0.0005)
    manager.update_aircraft_location(0.0013, 0.0005)
    manager.update_aircraft_location(0.0020, 0.0005)

    observed = [
        event["data"]["drone_locations"][0]["event"]
        for event in events
        if event.get("method") == "flight_areas_drone_location"
    ]
    assert_equal(
        observed,
        ["near_boundary", "enter_area", "exit_area", "leave_boundary"],
        "边界状态变化事件顺序不正确",
    )
    return {"events": observed}


def run_self_tests(args) -> dict:
    results = {}
    with tempfile.TemporaryDirectory() as temp_root:
        temp_root_path = Path(temp_root)
        with LocalFileServer(FIXTURE_PATH.parent, args.http_host, args.http_port) as file_server:
            results["file_sync"] = test_file_sync_and_services_get(
                temp_root_path / "sync",
                file_server.base_url,
            )
            results["empty_clear"] = test_empty_files_clear(
                temp_root_path / "clear",
                file_server.base_url,
            )
            results["checksum_failure"] = test_checksum_failure(
                temp_root_path / "checksum",
                file_server.base_url,
            )
            results["parser_shapes"] = test_parser_shapes()
            results["boundary_events"] = test_boundary_state_events(temp_root_path / "boundary")
            results["http_base_url"] = file_server.base_url
    return results


def write_report(path: Path, results: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description="自定义飞行区本地自测")
    parser.add_argument("--http-host", default="127.0.0.1", help="本地文件服务监听地址")
    parser.add_argument("--http-port", type=int, default=0, help="本地文件服务端口，0 表示自动分配")
    parser.add_argument("--report", default="", help="保存 JSON 测试报告的路径")
    args = parser.parse_args()

    if not FIXTURE_PATH.exists():
        print(f"自定义飞行区 fixture 不存在: {FIXTURE_PATH}", file=sys.stderr)
        return 2

    try:
        results = run_self_tests(args)
    except SelfTestFailure as exc:
        print(f"custom fly region self test failed: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"custom fly region self test error: {exc}", file=sys.stderr)
        return 3

    if args.report:
        write_report(Path(args.report), results)
    print(json.dumps(results, ensure_ascii=False, indent=2))
    print("custom fly region self test ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
