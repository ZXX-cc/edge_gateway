#!/usr/bin/env python3
"""Category smoke test for DJI Dock3 media protocol."""

import argparse
import os
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from mqtt_test_client import MqttRequestClient, result_code
from src.utils.dji_error_codes import INVALID_COMMAND_FORMAT
from tool_config import add_config_argument, load_config, resolve_mqtt_settings


def resolve_media_storage(config_path: str, explicit: str = "") -> Path:
    candidates: list[Path] = []
    if explicit:
        candidates.append(Path(explicit).expanduser())
    config = load_config(config_path)
    media_config = config.get("media") if isinstance(config, dict) else {}
    configured = (media_config or {}).get("storage_path", "/data/media")
    configured_path = Path(str(configured)).expanduser()
    if not configured_path.is_absolute():
        configured_path = PROJECT_ROOT / configured_path
    candidates.append(configured_path)
    candidates.append(PROJECT_ROOT / "data" / "media")

    for path in candidates:
        try:
            path.mkdir(parents=True, exist_ok=True)
            probe = path / ".write_probe"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink(missing_ok=True)
            return path
        except Exception:
            continue
    fallback = PROJECT_ROOT / "data" / "media"
    fallback.mkdir(parents=True, exist_ok=True)
    return fallback


def seed_media_file(storage: Path, name: str = "") -> Path:
    if not name:
        name = f"dock3-media-{int(time.time() * 1000)}.jpg"
    path = storage / name
    path.write_bytes(b"\xff\xd8\xff\xe0dock3-media-test\xff\xd9")
    return path


def files_from_reply(payload: dict) -> list[dict]:
    if not isinstance(payload, dict):
        return []
    data = payload.get("data")
    if not isinstance(data, dict):
        return []
    files = data.get("files") or data.get("list") or []
    return files if isinstance(files, list) else []


def find_file(files: list[dict], name: str) -> dict | None:
    for item in files:
        if item.get("name") == name or item.get("file_name") == name:
            return item
    return None


class Dock3MediaClient(MqttRequestClient):
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


def run_media_checks(client: Dock3MediaClient, args) -> int:
    storage = resolve_media_storage(args.config, args.storage_path)
    seeded = seed_media_file(storage, args.seed_name)
    print(f"seed media file -> {seeded}")

    rc, listed = client.request(
        "file_list_get",
        {"media_type": "photo", "page": 1, "page_size": 20},
        args.timeout,
        [0],
        "ok",
    )
    if rc != 0:
        return rc
    item = find_file(files_from_reply(listed), seeded.name)
    if not item:
        print(f"seeded media file not found in file_list_get: {seeded.name}", file=sys.stderr)
        return 10
    file_id = item.get("file_id") or item.get("fingerprint") or item.get("name")
    if not file_id:
        print(f"file_list_get item missing file_id/fingerprint: {item}", file=sys.stderr)
        return 11

    progress_before = client.event_count("fileupload_progress")
    rc, _ = client.request(
        "adjust_upload_priority",
        {"files": [{"file_id": file_id, "priority": 10}]},
        args.timeout,
        [0],
        "ok",
    )
    if rc != 0:
        return rc
    rc, progress_event = client.wait_event(
        "fileupload_progress",
        progress_before,
        args.timeout,
        expected_status="in_progress",
    )
    if rc != 0:
        return rc
    event_files = progress_event.get("data", {}).get("output", {}).get("ext", {}).get("files", [])
    if not event_files:
        print("fileupload_progress event missing files", file=sys.stderr)
        return 12

    priority_before = client.event_count("highest_priority_upload_flighttask_media")
    rc, _ = client.request(
        "highest_priority_upload_flighttask_media",
        {"flighttask_id": args.flight_id},
        args.timeout,
        [0],
        "ok",
    )
    if rc != 0:
        return rc
    rc, priority_event = client.wait_event(
        "highest_priority_upload_flighttask_media",
        priority_before,
        args.timeout,
        expected_status=None,
    )
    if rc != 0:
        return rc
    if priority_event.get("data", {}).get("flight_id") != args.flight_id:
        print(f"highest priority event flight_id mismatch: {priority_event}", file=sys.stderr)
        return 13

    rc, deleted = client.request(
        "file_delete",
        {"file_ids": [file_id]},
        args.timeout,
        [0],
        "ok",
    )
    if rc != 0:
        return rc
    delete_items = deleted.get("data", {}).get("files", [])
    if not delete_items or result_code({"data": delete_items[0]}) != 0:
        print(f"file_delete missing per-file success: {deleted}", file=sys.stderr)
        return 14

    rc, relisted = client.request(
        "file_list_get",
        {"media_type": "photo", "page": 1, "page_size": 20},
        args.timeout,
        [0],
        "ok",
    )
    if rc != 0:
        return rc
    if find_file(files_from_reply(relisted), seeded.name):
        print(f"deleted media file still present: {seeded.name}", file=sys.stderr)
        return 15

    rc, invalid = client.request(
        "file_delete",
        {},
        args.timeout,
        [INVALID_COMMAND_FORMAT],
        "rejected",
    )
    if rc != 0:
        return rc
    if result_code(invalid) != INVALID_COMMAND_FORMAT:
        print(f"invalid file_delete did not return official error: {invalid}", file=sys.stderr)
        return 16
    return 0


def main():
    parser = argparse.ArgumentParser(description="Test DJI Dock3 media category")
    add_config_argument(parser)
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--sn", default=None)
    parser.add_argument("--timeout", type=float, default=12.0)
    parser.add_argument("--qos", type=int, choices=[0, 1, 2], default=1)
    parser.add_argument("--storage-path", default="")
    parser.add_argument("--seed-name", default="")
    parser.add_argument("--flight-id", default="dock3-media-flight-001")
    parser.add_argument("--quiet-stale", action="store_true")
    parser.add_argument("--fail-on-stale", action="store_true")
    args = parser.parse_args()
    args.host, args.port, args.sn = resolve_mqtt_settings(args)

    client = Dock3MediaClient(
        args.host,
        args.port,
        args.sn,
        args.qos,
        quiet_stale=args.quiet_stale,
        client_id_prefix="edge_gateway_dock3_media",
        subscribe_events=True,
    )
    if not client.connect(args.timeout):
        print("timeout waiting for MQTT connection", file=sys.stderr)
        return 1

    try:
        rc = run_media_checks(client, args)
        if rc != 0:
            return rc
        ignored_total = client.ignored_total()
        if args.fail_on_stale and ignored_total:
            print(f"stale replies observed: {client.stats}", file=sys.stderr)
            return 9
        print(
            "dock3 media test ok "
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
