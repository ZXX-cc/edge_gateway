#!/usr/bin/env python3
"""Category smoke test for DJI Dock3 live protocol."""

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from mqtt_test_client import MqttRequestClient, result_code
from src.utils.dji_error_codes import INVALID_COMMAND_FORMAT
from tool_config import add_config_argument, resolve_mqtt_settings


class Dock3LiveClient(MqttRequestClient):
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


def run_live_checks(client: Dock3LiveClient, args) -> int:
    video_id = args.video_id or f"{args.sn}/0/normal"

    rc, capability = client.request(
        "live_capability_query",
        {},
        args.timeout,
        [0],
        "ok",
    )
    if rc != 0:
        return rc
    protocols = capability.get("data", {}).get("supported_protocols", [])
    if "webrtc" not in protocols or "rtmp" not in protocols:
        print(f"live capability missing common protocols: {capability}", file=sys.stderr)
        return 10

    status_before = client.event_count("live_status_notify")
    rc, start = client.request(
        "live_start",
        {
            "url": args.url,
            "url_type": args.url_type,
            "video_id": video_id,
            "video_quality": 2,
        },
        args.timeout,
        [0],
        "in_progress",
    )
    if rc != 0:
        return rc
    session_id = start.get("data", {}).get("session_id")
    if not session_id:
        print(f"live_start missing session_id: {start}", file=sys.stderr)
        return 11
    rc, _ = client.wait_event(
        "live_status_notify",
        status_before,
        args.timeout,
        expected_status="in_progress",
    )
    if rc != 0:
        return rc

    status_before = client.event_count("live_status_notify")
    rc, _ = client.request(
        "live_set_quality",
        {"video_id": video_id, "video_quality": 3},
        args.timeout,
        [0],
        "ok",
    )
    if rc != 0:
        return rc
    rc, _ = client.wait_event("live_status_notify", status_before, args.timeout, expected_status="ok")
    if rc != 0:
        return rc

    status_before = client.event_count("live_status_notify")
    rc, _ = client.request(
        "live_lens_change",
        {"video_id": video_id, "video_type": "zoom"},
        args.timeout,
        [0],
        "ok",
    )
    if rc != 0:
        return rc
    rc, _ = client.wait_event("live_status_notify", status_before, args.timeout, expected_status="ok")
    if rc != 0:
        return rc

    status_before = client.event_count("live_status_notify")
    rc, _ = client.request(
        "live_stop",
        {"video_id": video_id},
        args.timeout,
        [0],
        "ok",
    )
    if rc != 0:
        return rc
    rc, _ = client.wait_event("live_status_notify", status_before, args.timeout, expected_status="ok")
    if rc != 0:
        return rc

    rc, invalid = client.request(
        "live_start",
        {"video_id": video_id},
        args.timeout,
        [INVALID_COMMAND_FORMAT],
        "rejected",
    )
    if rc != 0:
        return rc
    if result_code(invalid) != INVALID_COMMAND_FORMAT:
        print(f"invalid live_start did not return official error: {invalid}", file=sys.stderr)
        return 12
    return 0


def main():
    parser = argparse.ArgumentParser(description="Test DJI Dock3 live category")
    add_config_argument(parser)
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--sn", default=None)
    parser.add_argument("--timeout", type=float, default=12.0)
    parser.add_argument("--qos", type=int, choices=[0, 1, 2], default=1)
    parser.add_argument("--url", default="mock://dock3/live")
    parser.add_argument("--url-type", type=int, default=4)
    parser.add_argument("--video-id", default="")
    parser.add_argument("--quiet-stale", action="store_true")
    parser.add_argument("--fail-on-stale", action="store_true")
    args = parser.parse_args()
    args.host, args.port, args.sn = resolve_mqtt_settings(args)

    client = Dock3LiveClient(
        args.host,
        args.port,
        args.sn,
        args.qos,
        quiet_stale=args.quiet_stale,
        client_id_prefix="edge_gateway_dock3_live",
        subscribe_events=True,
    )
    if not client.connect(args.timeout):
        print("timeout waiting for MQTT connection", file=sys.stderr)
        return 1

    try:
        rc = run_live_checks(client, args)
        if rc != 0:
            return rc
        ignored_total = client.ignored_total()
        if args.fail_on_stale and ignored_total:
            print(f"stale replies observed: {client.stats}", file=sys.stderr)
            return 9
        print(
            "dock3 live test ok "
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
