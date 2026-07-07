#!/usr/bin/env python3
"""Category smoke test for DJI Dock3 HMS and remote-log protocol."""

import argparse
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from mqtt_test_client import MqttRequestClient, result_code
from src.utils.dji_error_codes import INVALID_COMMAND_FORMAT
from tool_config import add_config_argument, load_config, resolve_mqtt_settings


def resolve_log_dir(config_path: str, explicit: str = "") -> Path:
    if explicit:
        path = Path(explicit).expanduser()
    else:
        config = load_config(config_path)
        logging_config = config.get("logging") if isinstance(config, dict) else {}
        path = Path(str((logging_config or {}).get("dir", "./logs"))).expanduser()
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    path.mkdir(parents=True, exist_ok=True)
    return path


def seed_log_file(log_dir: Path, name: str = "") -> Path:
    if not name:
        name = f"edge_gateway_hms_log_test_{int(time.time() * 1000)}.log"
    path = log_dir / name
    path.write_text("dock3 hms/log mqtt validation\n", encoding="utf-8")
    return path


def list_payload_items(payload: dict, *keys: str) -> list[dict]:
    if not isinstance(payload, dict):
        return []
    data = payload.get("data")
    if not isinstance(data, dict):
        return []
    for key in keys:
        value = data.get(key)
        if isinstance(value, list):
            return value
    return []


def find_named(items: list[dict], name: str) -> dict | None:
    for item in items:
        if item.get("name") == name or item.get("file_name") == name:
            return item
    return None


def first_fileupload_item(payload: dict, name: str) -> tuple[dict, dict] | tuple[None, None]:
    groups = list_payload_items(payload, "files")
    for group in groups:
        items = group.get("list", [])
        if not isinstance(items, list):
            continue
        found = find_named(items, name)
        if found:
            return group, found
    return None, None


class Dock3HmsLogClient(MqttRequestClient):
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


def run_hms_log_checks(client: Dock3HmsLogClient, args) -> int:
    log_dir = resolve_log_dir(args.config, args.log_dir)
    seeded = seed_log_file(log_dir, args.seed_name)
    print(f"seed log file -> {seeded}")

    alarm_id = args.alarm_id
    hms_before = client.event_count("hms")
    rc, hms = client.request(
        "hms_get",
        {
            "simulate": True,
            "alarms": [{
                "id": alarm_id,
                "level": 2,
                "module": "dock",
                "hms_key": "dock3.hms.simulation.warning",
                "content": "Dock3 HMS simulation warning",
            }],
            "page": 1,
            "page_size": 20,
        },
        args.timeout,
        [0],
        "ok",
    )
    if rc != 0:
        return rc
    alarms = list_payload_items(hms, "alarms", "list")
    if not any(item.get("id") == alarm_id for item in alarms):
        print(f"hms_get missing simulated alarm: {hms}", file=sys.stderr)
        return 10
    rc, _ = client.wait_event("hms", hms_before, args.timeout, expected_status="ok")
    if rc != 0:
        return rc

    hms_before = client.event_count("hms")
    rc, cleared = client.request(
        "hms_clear",
        {"alarm_ids": [alarm_id]},
        args.timeout,
        [0],
        "ok",
    )
    if rc != 0:
        return rc
    if cleared.get("data", {}).get("cleared_count", 0) < 1:
        print(f"hms_clear did not clear alarm: {cleared}", file=sys.stderr)
        return 11
    rc, clear_event = client.wait_event("hms", hms_before, args.timeout, expected_status="ok")
    if rc != 0:
        return rc
    if clear_event.get("data", {}).get("total") != 0:
        print(f"hms clear event still has alarms: {clear_event}", file=sys.stderr)
        return 12

    rc, log_list = client.request(
        "device_log_list_get",
        {"page": 1, "page_size": 50},
        args.timeout,
        [0],
        "ok",
    )
    if rc != 0:
        return rc
    logs = list_payload_items(log_list, "logs", "list")
    log_item = find_named(logs, seeded.name)
    if not log_item:
        print(f"seeded log not found in device_log_list_get: {seeded.name}", file=sys.stderr)
        return 13

    progress_before = client.event_count("fileupload_progress")
    rc, _ = client.request(
        "device_log_upload",
        {"log_ids": [log_item.get("file_id")]},
        args.timeout,
        [0],
        "in_progress",
    )
    if rc != 0:
        return rc
    rc, _ = client.wait_event(
        "fileupload_progress",
        progress_before,
        args.timeout,
        expected_status="in_progress",
    )
    if rc != 0:
        return rc

    rc, fileupload_list = client.request(
        "fileupload_list",
        {"module_list": [3]},
        args.timeout,
        [0],
        "ok",
    )
    if rc != 0:
        return rc
    group, upload_item = first_fileupload_item(fileupload_list, seeded.name)
    if not group or not upload_item:
        print(f"seeded log not found in fileupload_list: {seeded.name}", file=sys.stderr)
        return 14

    progress_before = client.event_count("fileupload_progress")
    rc, _ = client.request(
        "fileupload_start",
        {
            "provider": "mock",
            "bucket": "dock3-hms-log-test",
            "region": "local",
            "endpoint": "mock://local-log-upload",
            "credentials": {
                "access_key_id": "mock",
                "access_key_secret": "mock",
                "security_token": "mock",
                "expire": int(time.time()) + 3600,
            },
            "params": {
                "files": [{
                    "module": group.get("module", "3"),
                    "object_key": upload_item.get("object_key", f"logs/{seeded.name}"),
                    "list": [{
                        "boot_index": upload_item.get("boot_index"),
                        "size": upload_item.get("size", 0),
                    }],
                }],
            },
        },
        args.timeout,
        [0],
        "in_progress",
    )
    if rc != 0:
        return rc
    rc, _ = client.wait_event(
        "fileupload_progress",
        progress_before,
        args.timeout,
        expected_status="in_progress",
    )
    if rc != 0:
        return rc

    progress_before = client.event_count("fileupload_progress")
    rc, _ = client.request(
        "fileupload_update",
        {"status": "cancel", "module_list": [3]},
        args.timeout,
        [0],
        "ok",
    )
    if rc != 0:
        return rc
    rc, _ = client.wait_event(
        "fileupload_progress",
        progress_before,
        args.timeout,
        expected_status="canceled",
    )
    if rc != 0:
        return rc

    rc, invalid = client.request(
        "fileupload_start",
        {},
        args.timeout,
        [INVALID_COMMAND_FORMAT],
        "rejected",
    )
    if rc != 0:
        return rc
    if result_code(invalid) != INVALID_COMMAND_FORMAT:
        print(f"invalid fileupload_start did not return official error: {invalid}", file=sys.stderr)
        return 15
    return 0


def main():
    parser = argparse.ArgumentParser(description="Test DJI Dock3 HMS/log category")
    add_config_argument(parser)
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--sn", default=None)
    parser.add_argument("--timeout", type=float, default=12.0)
    parser.add_argument("--qos", type=int, choices=[0, 1, 2], default=1)
    parser.add_argument("--log-dir", default="")
    parser.add_argument("--seed-name", default="")
    parser.add_argument("--alarm-id", default="dock3-sim-hms-001")
    parser.add_argument("--quiet-stale", action="store_true")
    parser.add_argument("--fail-on-stale", action="store_true")
    args = parser.parse_args()
    args.host, args.port, args.sn = resolve_mqtt_settings(args)

    client = Dock3HmsLogClient(
        args.host,
        args.port,
        args.sn,
        args.qos,
        quiet_stale=args.quiet_stale,
        client_id_prefix="edge_gateway_dock3_hms_log",
        subscribe_events=True,
    )
    if not client.connect(args.timeout):
        print("timeout waiting for MQTT connection", file=sys.stderr)
        return 1

    try:
        rc = run_hms_log_checks(client, args)
        if rc != 0:
            return rc
        ignored_total = client.ignored_total()
        if args.fail_on_stale and ignored_total:
            print(f"stale replies observed: {client.stats}", file=sys.stderr)
            return 9
        print(
            "dock3 hms/log test ok "
            f"(accepted={client.stats['accepted']}, events={client.stats['events']}, "
            f"ignored_mismatched={client.stats['ignored_mismatched']}, "
            f"ignored_legacy={client.stats['ignored_legacy']}, "
            f"ignored_late={client.stats['ignored_late']}, "
            f"ignored_wrong_topic={client.stats['ignored_wrong_topic']}, "
            f"ignored_total={ignored_total})"
        )
        return 0
    finally:
        client.disconnect()


if __name__ == "__main__":
    raise SystemExit(main())
