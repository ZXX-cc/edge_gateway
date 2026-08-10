#!/usr/bin/env python3
"""Category smoke test for DJI Dock3 live protocol."""

import argparse
import json
import sys
import time
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

    def wait_live_state(self, previous_count: int, timeout: float, *, video_id: str, expected_status: int) -> int:
        topic = f"thing/product/{self.sn}/state"
        deadline = time.time() + timeout
        seen = previous_count
        while time.time() < deadline:
            payload = self.wait_observed(topic, seen, max(0.05, deadline - time.time()))
            if payload is None:
                break
            seen = self.observed_count(topic)
            data = payload.get("data", {}) if isinstance(payload, dict) else {}
            properties = data.get("property", {}) if isinstance(data, dict) else {}
            live_status = properties.get("live_status") if isinstance(properties, dict) else None
            if not isinstance(live_status, list):
                continue
            for item in live_status:
                if not isinstance(item, dict):
                    continue
                if item.get("video_id") != video_id:
                    continue
                if item.get("status") != expected_status:
                    continue
                if not isinstance(item.get("error_status"), int):
                    continue
                return 0

        latest = self.observed_by_topic.get(topic, [])[-3:]
        print(
            f"timeout waiting live_status state: video_id={video_id}, status={expected_status}",
            file=sys.stderr,
        )
        if latest:
            print(json.dumps(latest, ensure_ascii=False, indent=2), file=sys.stderr)
        return 14


def run_live_checks(client: Dock3LiveClient, args) -> int:
    video_type = args.video_type
    video_id = args.video_id or f"{args.sn}/0/{video_type}"

    if args.stop_only:
        rc, stop = client.request(
            "live_stop_push",
            {"video_id": video_id},
            args.timeout,
            [0, INVALID_COMMAND_FORMAT],
        )
        if rc != 0:
            return rc
        if result_code(stop) == INVALID_COMMAND_FORMAT:
            reason = str(stop.get("data", {}).get("reason", "") or stop.get("msg", "")).lower()
            if "not streaming" not in reason:
                print(f"live_stop_push rejected unexpectedly: {stop}", file=sys.stderr)
                return 13
            print("live_stop_push skipped: already not streaming")
        else:
            print("live_stop_push ok")
        return 0

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
    if "rtmp" not in protocols:
        print(f"live capability missing RTMP protocol: {capability}", file=sys.stderr)
        return 10

    status_before = client.event_count("live_status_notify")
    state_topic = f"thing/product/{args.sn}/state"
    state_before = client.observed_count(state_topic)
    rc, start = client.request(
        "live_start_push",
        {
            "url": args.url,
            "url_type": args.url_type,
            "video_id": video_id,
            "video_quality": 2,
            "video_type": video_type,
        },
        args.timeout,
        [0],
        "in_progress",
    )
    if rc != 0:
        return rc
    rc = client.wait_live_state(state_before, args.timeout, video_id=video_id, expected_status=1)
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
    state_before = client.observed_count(state_topic)
    rc, _ = client.request(
        "live_set_quality",
        {"video_id": video_id, "video_quality": 3},
        args.timeout,
        [0],
        "ok",
    )
    if rc != 0:
        return rc
    rc = client.wait_live_state(state_before, args.timeout, video_id=video_id, expected_status=1)
    if rc != 0:
        return rc
    rc, _ = client.wait_event("live_status_notify", status_before, args.timeout, expected_status="ok")
    if rc != 0:
        return rc

    status_before = client.event_count("live_status_notify")
    rc, _ = client.request(
        "live_lens_change",
        {"video_id": video_id, "video_type": video_type},
        args.timeout,
        [0],
        "ok",
    )
    if rc != 0:
        return rc
    rc, _ = client.wait_event("live_status_notify", status_before, args.timeout, expected_status="ok")
    if rc != 0:
        return rc

    if args.keep_stream:
        print("live stream kept running; use --stop-only when you want to stop it")
        return 0

    status_before = client.event_count("live_status_notify")
    state_before = client.observed_count(state_topic)
    rc, _ = client.request(
        "live_stop_push",
        {"video_id": video_id},
        args.timeout,
        [0],
        "ok",
    )
    if rc != 0:
        return rc
    rc = client.wait_live_state(state_before, args.timeout, video_id=video_id, expected_status=0)
    if rc != 0:
        return rc
    rc, _ = client.wait_event("live_status_notify", status_before, args.timeout, expected_status="ok")
    if rc != 0:
        return rc

    rc, invalid = client.request(
        "live_start_push",
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
    parser.add_argument("--url-type", type=int, default=1)
    parser.add_argument("--video-id", default="")
    parser.add_argument("--video-type", choices=["normal", "wide", "zoom", "ir", "fpv", "gimbal"], default="gimbal")
    parser.add_argument("--keep-stream", action="store_true")
    parser.add_argument("--stop-only", action="store_true")
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
        observe_topics={f"thing/product/{args.sn}/state"},
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
