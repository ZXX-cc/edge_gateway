#!/usr/bin/env python3
"""Category smoke test for DJI Dock3 DRC protocol."""

import argparse
import json
import math
import sys
import time
from pathlib import Path
from typing import Any, Iterable

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from mqtt_test_client import MqttRequestClient
from src.utils.dji_error_codes import DOCK_COMMUNICATION_ERROR, INVALID_COMMAND_FORMAT
from tool_config import add_config_argument, resolve_mqtt_settings

DEFAULT_DRONE_SN = "DRONESIM001"
FALLBACK_TARGET_LATITUDE = -35.3632622
FALLBACK_TARGET_LONGITUDE = 149.1652375


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


def _field_value(payload: Any, key: str) -> Any:
    if not isinstance(payload, dict):
        return None
    data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    value = data.get(key) if isinstance(data, dict) else None
    if isinstance(value, dict) and "value" in value:
        return value.get("value")
    return value


def _to_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _candidate_drone_sns(primary: str) -> list[str]:
    candidates = [primary, DEFAULT_DRONE_SN, "DRONE-SIM-001"]
    result: list[str] = []
    for sn in candidates:
        if sn and sn not in result:
            result.append(sn)
    return result


def _osd_topics(drone_sn: str) -> list[str]:
    return [f"thing/product/{sn}/osd" for sn in _candidate_drone_sns(drone_sn)]


def _latest_observed(client: MqttRequestClient, topics: Iterable[str]) -> tuple[str | None, Any | None]:
    for topic in topics:
        messages = client.observed_by_topic.get(topic, [])
        if messages:
            return topic, messages[-1]
    return None, None


def wait_for_drone_osd(client: MqttRequestClient, topics: Iterable[str], timeout: float) -> tuple[str | None, Any | None]:
    topics = list(topics)
    topic, payload = _latest_observed(client, topics)
    if payload is not None:
        return topic, payload

    counts = {topic: client.observed_count(topic) for topic in topics}
    deadline = time.time() + timeout
    while time.time() < deadline:
        for topic in topics:
            messages = client.observed_by_topic.get(topic, [])
            if len(messages) > counts[topic]:
                return topic, messages[-1]

        remaining = deadline - time.time()
        if remaining <= 0:
            break
        client.observed_signal.wait(max(0.05, min(0.5, remaining)))
        client.observed_signal.clear()
    return None, None


def _offset_longitude(latitude: float, longitude: float, offset_meters: float) -> float:
    meters_per_degree_lon = 111320.0 * max(0.1, abs(math.cos(math.radians(latitude))))
    return longitude + offset_meters / meters_per_degree_lon


def has_cli_takeoff_target(args) -> bool:
    return args.target_lat is not None and args.target_lon is not None


def resolve_takeoff_target(
    args,
    telemetry_payload: Any | None = None,
    telemetry_source: str = "drone_osd",
) -> tuple[dict[str, float], str]:
    if (args.target_lat is None) != (args.target_lon is None):
        raise ValueError("--target-lat and --target-lon must be provided together")

    if has_cli_takeoff_target(args):
        return {
            "target_latitude": float(args.target_lat),
            "target_longitude": float(args.target_lon),
            "target_height": float(args.target_height),
        }, "cli"

    latitude = _to_float(_field_value(telemetry_payload, "latitude"))
    longitude = _to_float(_field_value(telemetry_payload, "longitude"))
    if latitude is not None and longitude is not None:
        return {
            "target_latitude": latitude,
            "target_longitude": _offset_longitude(latitude, longitude, float(args.target_offset_meters)),
            "target_height": float(args.target_height),
        }, telemetry_source

    return {
        "target_latitude": FALLBACK_TARGET_LATITUDE,
        "target_longitude": FALLBACK_TARGET_LONGITUDE,
        "target_height": float(args.target_height),
    }, "fallback"


def is_fallback_target_allowed(args, target_source: str) -> bool:
    return target_source != "fallback" or bool(getattr(args, "allow_fallback_target", False))


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
    parser.add_argument("--drone-sn", default=DEFAULT_DRONE_SN)
    parser.add_argument("--osd-wait", type=float, default=4.0)
    parser.add_argument("--target-lat", type=float, default=None)
    parser.add_argument("--target-lon", type=float, default=None)
    parser.add_argument("--target-height", type=float, default=50.0)
    parser.add_argument("--target-offset-meters", type=float, default=30.0)
    parser.add_argument(
        "--allow-fallback-target",
        action="store_true",
        help="Allow the built-in fallback target when no property_get/drone OSD position is received. Prefer --target-lat/--target-lon for strict tests.",
    )
    args = parser.parse_args()
    args.host, args.port, args.sn = resolve_mqtt_settings(args)
    if (args.target_lat is None) != (args.target_lon is None):
        parser.error("--target-lat and --target-lon must be provided together")

    osd_topics = _osd_topics(args.drone_sn)

    client = MqttRequestClient(
        args.host,
        args.port,
        args.sn,
        args.qos,
        quiet_stale=args.quiet_stale,
        client_id_prefix="edge_gateway_dock3_drc",
        subscribe_events=True,
        observe_topics=osd_topics,
    )
    if not client.connect(args.timeout):
        print("MQTT connect timeout", file=sys.stderr)
        return 2

    drc_entered = False
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
        drc_entered = True

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
            allowed_results={0, DOCK_COMMUNICATION_ERROR},
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

        osd_topic = None
        if has_cli_takeoff_target(args):
            target, target_source = resolve_takeoff_target(args)
        else:
            rc, property_reply = client.request(
                "property_get",
                {"properties": ["latitude", "longitude", "height"]},
                args.timeout,
                channel="services",
                allowed_results={0},
            )
            if rc != 0:
                return rc
            target, target_source = resolve_takeoff_target(
                args,
                property_reply,
                telemetry_source="property_get",
            )
            if target_source == "fallback":
                osd_topic, osd_payload = wait_for_drone_osd(client, osd_topics, args.osd_wait)
                target, target_source = resolve_takeoff_target(
                    args,
                    osd_payload,
                    telemetry_source="drone_osd",
                )
        if not is_fallback_target_allowed(args, target_source):
            print(
                "error: no gateway property_get or drone OSD position received before takeoff_to_point; "
                f"topics={','.join(osd_topics)}. "
                "Provide --target-lat/--target-lon or rerun with --allow-fallback-target.",
                file=sys.stderr,
            )
            return 11
        if target_source == "fallback":
            print(
                "warning: no gateway property_get or drone OSD position received before takeoff_to_point; "
                f"fallback target is used, topics={','.join(osd_topics)}",
                file=sys.stderr,
            )
        else:
            topic_info = f", topic={osd_topic}" if osd_topic else ""
            print(
                "takeoff_to_point target "
                f"source={target_source}{topic_info}, "
                f"lat={target['target_latitude']:.7f}, lon={target['target_longitude']:.7f}, "
                f"height={target['target_height']:.1f}"
            )

        takeoff_events_before = client.event_count("takeoff_to_point_progress")
        rc, _ = client.request(
            "takeoff_to_point",
            {
                "flight_id": "drc-test-flight",
                **target,
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
                    "latitude": target["target_latitude"],
                    "longitude": target["target_longitude"],
                    "height": max(5.0, target["target_height"] - 5.0),
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
        drc_entered = False

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
            stale_samples = client.stale_sample_summary()
            print(
                "stale/duplicate replies observed "
                f"(ignored_mismatched={client.stats['ignored_mismatched']}, "
                f"ignored_legacy={client.stats['ignored_legacy']}, "
                f"ignored_late={client.stats['ignored_late']}, "
                f"ignored_duplicate={client.stats['ignored_duplicate']}, "
                f"ignored_unknown={client.stats['ignored_unknown']}, "
                f"ignored_wrong_topic={client.stats['ignored_wrong_topic']})",
                file=sys.stderr,
            )
            if stale_samples:
                print("stale_samples:", file=sys.stderr)
                print(json.dumps(stale_samples, ensure_ascii=False, indent=2), file=sys.stderr)
            return 10

        print(
            "dock3 drc test ok "
            f"(accepted={client.stats['accepted']}, "
            f"events={client.stats['events']}, "
            f"ignored_mismatched={client.stats['ignored_mismatched']}, "
            f"ignored_legacy={client.stats['ignored_legacy']}, "
            f"ignored_late={client.stats['ignored_late']}, "
            f"ignored_duplicate={client.stats['ignored_duplicate']}, "
            f"ignored_unknown={client.stats['ignored_unknown']}, "
            f"ignored_wrong_topic={client.stats['ignored_wrong_topic']}, "
            f"ignored_total={stale_total})"
        )
        stale_samples = client.stale_sample_summary()
        if stale_samples:
            print("stale_samples:")
            print(json.dumps(stale_samples, ensure_ascii=False, indent=2))
        return 0
    finally:
        if drc_entered:
            try:
                client.request(
                    "drc_mode_exit",
                    {},
                    min(args.timeout, 5.0),
                    channel="services",
                    allowed_results={0, 319999},
                    verbose=False,
                )
            except Exception as exc:
                print(f"warning: DRC cleanup failed: {exc}", file=sys.stderr)
        client.disconnect()


if __name__ == "__main__":
    raise SystemExit(main())
