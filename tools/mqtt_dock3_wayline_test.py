#!/usr/bin/env python3
"""Category smoke test for DJI Dock3 wayline / flighttask protocol."""

import argparse
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from mqtt_test_client import MqttRequestClient
from src.utils.dji_error_codes import DEVICE_STATE_UNSUPPORTED, INVALID_COMMAND_FORMAT
from tool_config import add_config_argument, resolve_mqtt_settings


def now_ms():
    return int(time.time() * 1000)


def official_prepare_payload(flight_id):
    return {
        "flight_id": flight_id,
        "task_type": 0,
        "execute_time": now_ms() + 30_000,
        "file": {
            "url": "https://example.invalid/simulated-wayline.kmz",
            "fingerprint": "simulated-fingerprint",
        },
        "ready_conditions": {},
        "executable_conditions": {
            "storage_capacity": 0,
        },
        "rth_altitude": 80,
        "out_of_control_action": 0,
        "exit_wayline_when_rc_lost": 0,
        "simulate_mission": {
            "is_enable": 1,
            "latitude": -35.363262,
            "longitude": 149.165237,
        },
    }


class Dock3WaylineClient(MqttRequestClient):
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


def run_wayline_checks(client, args):
    flight_id = args.flight_id or f"dock3-wayline-{int(time.time())}"
    checks = [
        ("flighttask_prepare", official_prepare_payload(flight_id), [0], "sent"),
        ("flighttask_execute", {"flight_id": flight_id}, [0], "in_progress"),
        ("flighttask_progress", {"flight_id": flight_id}, [0], "in_progress"),
        ("flighttask_pause", {}, [0], "paused"),
        ("flighttask_progress", {"flight_id": flight_id}, [0], "paused"),
        ("flighttask_resume", {}, [0], "in_progress"),
        ("flighttask_return", {"flight_id": flight_id, "return_type": 0}, [0], "in_progress"),
        ("return_home_cancel", {}, [0], "paused"),
        ("flighttask_undo", {"flight_ids": [flight_id]}, [0], "canceled"),
        (
            "flighttask_prepare",
            {"flight_id": "", "task_type": 99, "execute_time": 1},
            [INVALID_COMMAND_FORMAT],
            None,
        ),
        ("flighttask_pause", {}, [DEVICE_STATE_UNSUPPORTED], "rejected"),
    ]
    for method, data, allowed, status in checks:
        rc, _ = client.request(method, data, args.timeout, allowed, status)
        if rc != 0:
            return rc
        time.sleep(args.step_delay)

    if not client.has_event("flighttask_progress"):
        print("expected flighttask_progress event was not observed", file=sys.stderr)
        return 6
    if not client.has_event("return_home_info"):
        print("expected return_home_info event was not observed", file=sys.stderr)
        return 7
    return 0


def main():
    parser = argparse.ArgumentParser(description="Test DJI Dock3 wayline category")
    add_config_argument(parser)
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--sn", default=None)
    parser.add_argument("--timeout", type=float, default=12.0)
    parser.add_argument("--qos", type=int, choices=[0, 1, 2], default=1)
    parser.add_argument("--flight-id", default="")
    parser.add_argument("--step-delay", type=float, default=0.1)
    parser.add_argument("--quiet-stale", action="store_true")
    parser.add_argument("--fail-on-stale", action="store_true")
    args = parser.parse_args()
    args.host, args.port, args.sn = resolve_mqtt_settings(args)

    client = Dock3WaylineClient(
        args.host,
        args.port,
        args.sn,
        args.qos,
        quiet_stale=args.quiet_stale,
        client_id_prefix="edge_gateway_dock3_wayline",
        subscribe_events=True,
    )
    if not client.connect(args.timeout):
        print("timeout waiting for MQTT connection", file=sys.stderr)
        return 1
    try:
        rc = run_wayline_checks(client, args)
        if rc != 0:
            return rc
        ignored_total = client.ignored_total()
        if args.fail_on_stale and ignored_total:
            print(f"stale replies observed: {client.stats}", file=sys.stderr)
            return 9
        print(
            "dock3 wayline test ok "
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
