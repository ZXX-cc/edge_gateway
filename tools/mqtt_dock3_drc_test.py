#!/usr/bin/env python3
"""Category smoke test for DJI Dock3 DRC protocol."""

import argparse
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from mqtt_test_client import MqttRequestClient
from src.utils.dji_error_codes import INVALID_COMMAND_FORMAT
from tool_config import add_config_argument, resolve_mqtt_settings


def official_enter_payload(args):
    return {
        "mqtt_broker": {
            "address": args.drc_broker_address,
            "client_id": f"drc-{args.sn}",
            "username": args.drc_username,
            "password": args.drc_password,
            "expire_time": args.drc_expire_time,
            "enable_tls": args.drc_enable_tls,
        },
        "osd_frequency": args.osd_frequency,
        "hsi_frequency": args.hsi_frequency,
    }


def main():
    parser = argparse.ArgumentParser(description="Test DJI Dock3 DRC category")
    add_config_argument(parser)
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--sn", default=None)
    parser.add_argument("--timeout", type=float, default=12.0)
    parser.add_argument("--qos", type=int, choices=[0, 1, 2], default=1)
    parser.add_argument("--quiet-stale", action="store_true")
    parser.add_argument("--fail-on-stale", action="store_true")
    parser.add_argument("--drc-broker-address", default="127.0.0.1:1883")
    parser.add_argument("--drc-username", default="sim-user")
    parser.add_argument("--drc-password", default="sim-token")
    parser.add_argument("--drc-expire-time", type=int, default=3600)
    parser.add_argument("--drc-enable-tls", action="store_true")
    parser.add_argument("--osd-frequency", type=int, default=10)
    parser.add_argument("--hsi-frequency", type=int, default=1)
    parser.add_argument("--no-reply-window", type=float, default=1.0)
    args = parser.parse_args()
    args.host, args.port, args.sn = resolve_mqtt_settings(args)

    client = MqttRequestClient(
        args.host,
        args.port,
        args.sn,
        args.qos,
        quiet_stale=args.quiet_stale,
        client_id_prefix="edge_gateway_dock3_drc",
        subscribe_events=True,
    )
    if not client.connect(args.timeout):
        print("MQTT connect timeout", file=sys.stderr)
        return 2

    try:
        rc, _ = client.request("drc_mode_exit", {}, args.timeout, channel="services", allowed_results={0, 319999})
        if rc != 0:
            return rc

        rc, _ = client.request(
            "drc_mode_enter",
            official_enter_payload(args),
            args.timeout,
            channel="services",
            allowed_results={0},
        )
        if rc != 0:
            return rc

        rc, state_reply = client.request(
            "property_get",
            {"properties": ["drc_state"]},
            args.timeout,
            channel="services",
            allowed_results={0},
        )
        if rc != 0:
            return rc
        if state_reply.get("data", {}).get("drc_state") != 2:
            print(f"expected drc_state=2 after enter, actual={state_reply.get('data', {}).get('drc_state')}", file=sys.stderr)
            return 5

        rc, _ = client.request(
            "heart_beat",
            {"seq": 1, "timestamp": int(time.time() * 1000)},
            args.timeout,
            channel="drc",
            allowed_results={0},
        )
        if rc != 0:
            return rc

        rc, _ = client.request(
            "stick_control",
            {"roll": 1024, "pitch": 1024, "throttle": 1024, "yaw": 1024, "gimbal_pitch": 1024},
            args.no_reply_window,
            channel="drc",
            need_reply=0,
        )
        if rc != 0:
            return rc

        rc, _ = client.request(
            "stick_control",
            {"roll": 1024, "pitch": 2000, "throttle": 1024, "yaw": 1024},
            args.timeout,
            channel="drc",
            allowed_results={INVALID_COMMAND_FORMAT},
        )
        if rc != 0:
            return rc

        rc, _ = client.request(
            "drone_control",
            {"seq": 1, "x": 0.1, "y": 0.0, "h": 0.0, "w": 0.0},
            args.timeout,
            channel="drc",
            allowed_results={0},
        )
        if rc != 0:
            return rc

        rc, _ = client.request(
            "drone_control",
            {"seq": 2, "x": 99.0, "y": 0.0, "h": 0.0, "w": 0.0},
            args.timeout,
            channel="drc",
            allowed_results={INVALID_COMMAND_FORMAT},
        )
        if rc != 0:
            return rc

        rc, _ = client.request(
            "drc_gimbal_speed_push",
            {"pitch_speed": 0.0, "yaw_speed": 0.0, "roll_speed": 0.0},
            args.timeout,
            channel="drc",
            allowed_results={0},
        )
        if rc != 0:
            return rc

        takeoff_events_before = client.event_count("takeoff_to_point_progress")
        rc, _ = client.request(
            "takeoff_to_point",
            {
                "flight_id": "drc-test-flight",
                "target_latitude": -35.3632622,
                "target_longitude": 149.1652375,
                "target_height": 50,
                "security_takeoff_height": 30,
                "rth_altitude": 80,
                "rc_lost_action": 0,
                "commander_mode_lost_action": 1,
                "commander_flight_height": 70,
                "max_speed": 12,
            },
            args.timeout,
            channel="services",
            allowed_results={0},
            expected_status="ok",
        )
        if rc != 0:
            return rc
        rc, event = client.wait_event(
            "takeoff_to_point_progress",
            takeoff_events_before,
            args.timeout,
            expected_status=None,
        )
        if rc != 0:
            return rc
        if event.get("data", {}).get("status") not in {"task_ready", "wayline_progress"}:
            print(f"unexpected takeoff_to_point_progress status: {event.get('data', {}).get('status')}", file=sys.stderr)
            return 7

        flyto_events_before = client.event_count("fly_to_point_progress")
        rc, _ = client.request(
            "fly_to_point_update",
            {
                "fly_to_id": "drc-flyto-001",
                "max_speed": 8,
                "points": [{
                    "latitude": -35.3632622,
                    "longitude": 149.1652375,
                    "height": 45,
                }],
            },
            args.timeout,
            channel="services",
            allowed_results={0},
            expected_status="ok",
        )
        if rc != 0:
            return rc
        rc, event = client.wait_event(
            "fly_to_point_progress",
            flyto_events_before,
            args.timeout,
            expected_status=None,
        )
        if rc != 0:
            return rc
        if event.get("data", {}).get("status") != "wayline_progress":
            print(f"unexpected fly_to_point_progress status: {event.get('data', {}).get('status')}", file=sys.stderr)
            return 8

        flyto_stop_events_before = client.event_count("fly_to_point_progress")
        takeoff_stop_events_before = client.event_count("takeoff_to_point_progress")
        rc, _ = client.request(
            "fly_to_point_stop",
            {},
            args.timeout,
            channel="services",
            allowed_results={0},
            expected_status="ok",
        )
        if rc != 0:
            return rc
        rc, event = client.wait_event(
            "fly_to_point_progress",
            flyto_stop_events_before,
            args.timeout,
            expected_status=None,
        )
        if rc != 0:
            return rc
        if event.get("data", {}).get("status") != "wayline_cancel":
            print(f"unexpected fly_to_point_stop status: {event.get('data', {}).get('status')}", file=sys.stderr)
            return 9
        rc, event = client.wait_event(
            "takeoff_to_point_progress",
            takeoff_stop_events_before,
            args.timeout,
            expected_status=None,
        )
        if rc != 0:
            return rc
        if event.get("data", {}).get("status") != "wayline_cancel":
            print(f"unexpected takeoff_to_point cancel status: {event.get('data', {}).get('status')}", file=sys.stderr)
            return 10

        rc, _ = client.request(
            "takeoff_to_point",
            {"target_latitude": 100, "target_longitude": 149.1652375, "target_height": 50},
            args.timeout,
            channel="services",
            allowed_results={INVALID_COMMAND_FORMAT},
        )
        if rc != 0:
            return rc

        rc, _ = client.request("drc_mode_exit", {}, args.timeout, channel="services", allowed_results={0})
        if rc != 0:
            return rc

        rc, state_reply = client.request(
            "property_get",
            {"properties": ["drc_state"]},
            args.timeout,
            channel="services",
            allowed_results={0},
        )
        if rc != 0:
            return rc
        if state_reply.get("data", {}).get("drc_state") != 0:
            print(f"expected drc_state=0 after exit, actual={state_reply.get('data', {}).get('drc_state')}", file=sys.stderr)
            return 6

        stale_total = client.stale_total()
        if args.fail_on_stale and stale_total:
            print(
                "stale duplicate replies observed "
                f"(ignored_mismatched={client.stats['ignored_mismatched']}, "
                f"ignored_legacy={client.stats['ignored_legacy']}, "
                f"ignored_late={client.stats['ignored_late']}, "
                f"ignored_wrong_topic={client.stats['ignored_wrong_topic']})",
                file=sys.stderr,
            )
            return 10

        print(
            "dock3 drc test ok "
            f"(accepted={client.stats['accepted']}, "
            f"events={client.stats['events']}, "
            f"ignored_mismatched={client.stats['ignored_mismatched']}, "
            f"ignored_legacy={client.stats['ignored_legacy']}, "
            f"ignored_late={client.stats['ignored_late']}, "
            f"ignored_wrong_topic={client.stats['ignored_wrong_topic']}, "
            f"ignored_total={stale_total})"
        )
        return 0
    finally:
        client.disconnect()


if __name__ == "__main__":
    raise SystemExit(main())
