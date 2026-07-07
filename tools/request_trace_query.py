#!/usr/bin/env python3
"""Query request trace JSONL files by tid, method, event, or topic."""

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Iterable

from tool_config import add_config_argument, load_config


def nested(data: dict, *keys: str, default=None):
    value = data
    for key in keys:
        if not isinstance(value, dict):
            return default
        value = value.get(key)
    return default if value is None else value


def resolve_trace_dir(args) -> Path:
    if args.dir:
        return Path(args.dir)
    config = load_config(args.config)
    return Path(nested(config, "request_trace", "dir", default="./logs/request_trace"))


def iter_trace_files(root: Path, days: int) -> Iterable[Path]:
    if not root.exists():
        return []
    files = sorted(root.glob("*/*.jsonl"), reverse=True)
    if days <= 0:
        return files
    cutoff = time.time() - days * 24 * 3600
    return [path for path in files if path.stat().st_mtime >= cutoff]


def parse_record(line: str, path: Path, line_no: int):
    try:
        return json.loads(line)
    except json.JSONDecodeError as exc:
        print(f"skip invalid JSON: {path}:{line_no}: {exc}", file=sys.stderr)
        return None


def match_record(record: dict, args) -> bool:
    if args.tid and record.get("tid") != args.tid:
        return False
    if args.bid and record.get("bid") != args.bid:
        return False
    if args.method and record.get("method") != args.method:
        return False
    if args.event and record.get("event") != args.event:
        return False
    if args.topic and args.topic not in str(record.get("topic", "")):
        return False
    if args.result is not None and record.get("result") != args.result:
        return False
    return True


def format_ts(ts_ms):
    try:
        return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(int(ts_ms) / 1000))
    except Exception:
        return ""


def compact_payload(record: dict) -> str:
    payload = record.get("payload")
    if not isinstance(payload, dict):
        return ""
    if payload.get("_truncated"):
        return f"payload_truncated chars={payload.get('chars')} preview={payload.get('preview')!r}"
    data = payload.get("data")
    if isinstance(data, dict):
        keys = ",".join(list(data.keys())[:8])
        return f"data_keys=[{keys}]"
    return ""


def print_text(records: list[dict]):
    for item in records:
        parts = [
            format_ts(item.get("ts")),
            item.get("event", ""),
            item.get("direction", ""),
            item.get("topic", ""),
            f"method={item.get('method', '')}",
            f"tid={item.get('tid', '')}",
            f"result={item.get('result')}",
            f"elapsed_ms={item.get('elapsed_ms')}",
        ]
        if item.get("error"):
            parts.append(f"error={item.get('error')}")
        payload_hint = compact_payload(item)
        if payload_hint:
            parts.append(payload_hint)
        print(" | ".join(str(part) for part in parts if part is not None))


def print_summary(records: list[dict]):
    by_event = {}
    by_method = {}
    results = {}
    for item in records:
        by_event[item.get("event", "")] = by_event.get(item.get("event", ""), 0) + 1
        by_method[item.get("method", "")] = by_method.get(item.get("method", ""), 0) + 1
        result = item.get("result")
        if result is not None:
            results[result] = results.get(result, 0) + 1
    print(json.dumps({
        "total": len(records),
        "by_event": by_event,
        "by_method": by_method,
        "results": results,
    }, ensure_ascii=False, indent=2))


def main() -> int:
    parser = argparse.ArgumentParser(description="Query edge_gateway request trace JSONL files")
    add_config_argument(parser)
    parser.add_argument("--dir", default="")
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument("--tid", default="")
    parser.add_argument("--bid", default="")
    parser.add_argument("--method", default="")
    parser.add_argument("--event", default="")
    parser.add_argument("--topic", default="")
    parser.add_argument("--result", type=int, default=None)
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--summary", action="store_true")
    args = parser.parse_args()

    root = resolve_trace_dir(args)
    records = []
    for path in iter_trace_files(root, args.days):
        with path.open("r", encoding="utf-8") as fp:
            for line_no, line in enumerate(fp, start=1):
                record = parse_record(line, path, line_no)
                if record and match_record(record, args):
                    records.append(record)

    records.sort(key=lambda item: item.get("ts") or 0)
    if args.limit > 0:
        records = records[-args.limit:]

    if args.summary:
        print_summary(records)
    elif args.json:
        print(json.dumps(records, ensure_ascii=False, indent=2))
    else:
        print_text(records)
    return 0 if records else 1


if __name__ == "__main__":
    raise SystemExit(main())
