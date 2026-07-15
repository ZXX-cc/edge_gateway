#!/usr/bin/env python3
"""Acceptance test for Dock3 command flight breakpoint/resume flow.

The tested chain is:

platform MQTT -> gateway -> nest simulator -> flight controller

The script covers the Dock3 command-flight controls shown in the UI:

1. takeoff_to_point
2. fly_to_point_update
3. fly_to_point_stop
4. fly_to_point_update again, using the stopped point as a breakpoint resume

The gateway currently models the resume step as a new fly_to_point_update with
the same fly_to_id and a new target point.  The script verifies replies, events,
DRC state, and optionally strict stale reply behavior.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
from pathlib import Path
from typing import Any, Iterable

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from mqtt_test_client import MqttRequestClient, output_status, result_code  # noqa: E402
from src.utils.dji_error_codes import DOCK_COMMUNICATION_ERROR, INVALID_COMMAND_FORMAT  # noqa: E402
from tool_config import add_config_argument, resolve_mqtt_settings  # noqa: E402


DEFAULT_DRONE_SN = "DRONESIM001"
FALLBACK_LATITUDE = -35.3632622
FALLBACK_LONGITUDE = 149.1652375


def official_enter_payload(args: argparse.Namespace) -> dict[str, Any]:
    return {
        "mqtt_broker": {
            "address": args.drc_broker_address,
            "client_id": f"drc-breakpoint-{args.sn}",
            "username": args.drc_username,
            "password": args.drc_password,
            "expire_time": args.drc_expire_time,
            "enable_tls": args.drc_enable_tls,
        },
        "osd_frequency": args.osd_frequency,
        "hsi_frequency": args.hsi_frequency,
    }


def field_value(payload: Any, key: str) -> Any:
    if not isinstance(payload, dict):
        return None
    data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    value = data.get(key) if isinstance(data, dict) else None
    if isinstance(value, dict) and "value" in value:
        return value.get("value")
    return value


def to_float(value: Any) -> float | None:
    try:
        if isinstance(value, bool):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def candidate_drone_sns(primary: str) -> list[str]:
    values = [primary, DEFAULT_DRONE_SN, "DRONE-SIM-001"]
    result: list[str] = []
    for value in values:
        if value and value not in result:
            result.append(value)
    return result


def osd_topics(drone_sn: str) -> list[str]:
    return [f"thing/product/{sn}/osd" for sn in candidate_drone_sns(drone_sn)]


def latest_observed(client: MqttRequestClient, topics: Iterable[str]) -> tuple[str | None, Any | None]:
    for topic in topics:
        messages = client.observed_by_topic.get(topic, [])
        if messages:
            return topic, messages[-1]
    return None, None


def wait_for_drone_osd(
    client: MqttRequestClient,
    topics: Iterable[str],
    timeout: float,
) -> tuple[str | None, Any | None]:
    topics = list(topics)
    topic, payload = latest_observed(client, topics)
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


def offset_longitude(latitude: float, longitude: float, offset_meters: float) -> float:
    meters_per_degree_lon = 111320.0 * max(0.1, abs(math.cos(math.radians(latitude))))
    return longitude + offset_meters / meters_per_degree_lon


def resolve_base_position(
    args: argparse.Namespace,
    telemetry_payload: Any | None = None,
    source: str = "property_get",
) -> tuple[dict[str, float], str]:
    if (args.target_lat is None) != (args.target_lon is None):
        raise ValueError("--target-lat and --target-lon must be provided together")
    if args.target_lat is not None and args.target_lon is not None:
        return {
            "latitude": float(args.target_lat),
            "longitude": float(args.target_lon),
            "height": float(args.target_height),
        }, "cli"

    latitude = to_float(field_value(telemetry_payload, "latitude"))
    longitude = to_float(field_value(telemetry_payload, "longitude"))
    height = to_float(field_value(telemetry_payload, "height"))
    if latitude is not None and longitude is not None:
        return {
            "latitude": latitude,
            "longitude": longitude,
            "height": float(args.target_height if height is None else max(args.target_height, height)),
        }, source

    return {
        "latitude": FALLBACK_LATITUDE,
        "longitude": FALLBACK_LONGITUDE,
        "height": float(args.target_height),
    }, "fallback"


def make_point(base: dict[str, float], offset_meters: float, height_delta: float = 0.0) -> dict[str, float]:
    return {
        "latitude": base["latitude"],
        "longitude": offset_longitude(base["latitude"], base["longitude"], offset_meters),
        "height": max(5.0, base["height"] + height_delta),
    }


def assert_output_simulated_false(payload: dict[str, Any] | None, method: str) -> int:
    if not isinstance(payload, dict):
        print(f"{method} missing reply payload", file=sys.stderr)
        return 20
    output = payload.get("data", {}).get("output", {})
    if not isinstance(output, dict):
        print(f"{method} missing data.output", file=sys.stderr)
        return 21
    if output.get("simulated") is not False:
        print(f"{method} did not reach the real guided adapter path: output={output}", file=sys.stderr)
        return 22
    return 0


def wait_progress_event(
    client: MqttRequestClient,
    method: str,
    previous_count: int,
    timeout: float,
    allowed_statuses: set[str],
) -> tuple[int, dict[str, Any] | None]:
    rc, event = client.wait_event(method, previous_count, timeout, expected_status=None)
    if rc != 0:
        return rc, event
    status = event.get("data", {}).get("status") if isinstance(event, dict) else None
    if status not in allowed_statuses:
        print(
            f"{method} event status mismatch: expected={sorted(allowed_statuses)}, actual={status}",
            file=sys.stderr,
        )
        if event:
            print(json.dumps(event, ensure_ascii=False, indent=2), file=sys.stderr)
        return 23, event
    return 0, event


def service(
    client: MqttRequestClient,
    method: str,
    data: dict[str, Any],
    args: argparse.Namespace,
    allowed_results: set[int] | None = None,
    expected_status: str | None = None,
) -> tuple[int, dict[str, Any] | None]:
    return client.request(
        method,
        data,
        args.timeout,
        channel="services",
        allowed_results={0} if allowed_results is None else allowed_results,
        expected_status=expected_status,
    )


def run_flow(client: MqttRequestClient, args: argparse.Namespace) -> int:
    rc, _ = service(client, "drc_mode_exit", {}, args, allowed_results={0, 319999})
    if rc != 0:
        return rc

    rc, _ = service(client, "drc_mode_enter", official_enter_payload(args), args, allowed_results={0})
    if rc != 0:
        return rc
    drc_entered = True

    try:
        rc, state_reply = service(client, "property_get", {"properties": ["drc_state"]}, args, allowed_results={0})
        if rc != 0:
            return rc
        if state_reply.get("data", {}).get("drc_state") != 2:
            print(
                f"expected drc_state=2 after enter, actual={state_reply.get('data', {}).get('drc_state')}",
                file=sys.stderr,
            )
            return 5

        osd_topic = None
        if args.target_lat is not None:
            base, source = resolve_base_position(args)
        else:
            rc, property_reply = service(
                client,
                "property_get",
                {"properties": ["latitude", "longitude", "height"]},
                args,
                allowed_results={0},
            )
            if rc != 0:
                return rc
            base, source = resolve_base_position(args, property_reply, "property_get")
            if source == "fallback":
                osd_topic, osd_payload = wait_for_drone_osd(client, osd_topics(args.drone_sn), args.osd_wait)
                base, source = resolve_base_position(args, osd_payload, "drone_osd")

        if source == "fallback" and not args.allow_fallback_target:
            print(
                "error: no property_get or drone OSD position received. "
                "Provide --target-lat/--target-lon or add --allow-fallback-target.",
                file=sys.stderr,
            )
            return 11

        point_a = make_point(base, args.first_offset_meters, 0.0)
        point_b = make_point(base, args.resume_offset_meters, args.resume_height_delta)
        flight_id = args.flight_id or f"takeoff-point-{int(time.time() * 1000)}"
        fly_to_id = args.fly_to_id or f"flyto-breakpoint-{int(time.time() * 1000)}"

        topic_info = f", topic={osd_topic}" if osd_topic else ""
        print(
            "command-flight target "
            f"source={source}{topic_info}, base=({base['latitude']:.7f}, {base['longitude']:.7f}), "
            f"point_a=({point_a['latitude']:.7f}, {point_a['longitude']:.7f}, {point_a['height']:.1f}), "
            f"point_b=({point_b['latitude']:.7f}, {point_b['longitude']:.7f}, {point_b['height']:.1f})"
        )

        takeoff_events = client.event_count("takeoff_to_point_progress")
        rc, takeoff_reply = service(
            client,
            "takeoff_to_point",
            {
                "flight_id": flight_id,
                "target_latitude": point_a["latitude"],
                "target_longitude": point_a["longitude"],
                "target_height": point_a["height"],
                "security_takeoff_height": args.security_takeoff_height,
                "rth_altitude": args.rth_altitude,
                "rc_lost_action": 0,
                "commander_mode_lost_action": 1,
                "commander_flight_height": max(args.rth_altitude, point_a["height"]),
                "max_speed": args.max_speed,
            },
            args,
            allowed_results={0, DOCK_COMMUNICATION_ERROR},
            expected_status=None,
        )
        if rc != 0:
            return rc
        if result_code(takeoff_reply) == DOCK_COMMUNICATION_ERROR:
            print(f"takeoff_to_point failed in current adapter state: msg={takeoff_reply.get('msg')!r}", file=sys.stderr)
            return 12
        rc = assert_output_simulated_false(takeoff_reply, "takeoff_to_point")
        if rc != 0:
            return rc
        rc, _ = wait_progress_event(
            client,
            "takeoff_to_point_progress",
            takeoff_events,
            args.timeout,
            {"task_ready", "wayline_progress"},
        )
        if rc != 0:
            return rc

        flyto_events = client.event_count("fly_to_point_progress")
        rc, flyto_reply = service(
            client,
            "fly_to_point_update",
            {
                "fly_to_id": fly_to_id,
                "max_speed": args.max_speed,
                "points": [point_a],
            },
            args,
            allowed_results={0},
            expected_status="ok",
        )
        if rc != 0:
            return rc
        rc = assert_output_simulated_false(flyto_reply, "fly_to_point_update(first)")
        if rc != 0:
            return rc
        rc, _ = wait_progress_event(
            client,
            "fly_to_point_progress",
            flyto_events,
            args.timeout,
            {"wayline_progress"},
        )
        if rc != 0:
            return rc

        stop_flyto_events = client.event_count("fly_to_point_progress")
        stop_takeoff_events = client.event_count("takeoff_to_point_progress")
        rc, stop_reply = service(
            client,
            "fly_to_point_stop",
            {"fly_to_id": fly_to_id, "breakpoint": point_a},
            args,
            allowed_results={0},
            expected_status="ok",
        )
        if rc != 0:
            return rc
        if output_status(stop_reply) != "ok":
            print(f"fly_to_point_stop expected output.status=ok, actual={output_status(stop_reply)}", file=sys.stderr)
            return 24
        rc, _ = wait_progress_event(
            client,
            "fly_to_point_progress",
            stop_flyto_events,
            args.timeout,
            {"wayline_cancel"},
        )
        if rc != 0:
            return rc
        rc, _ = wait_progress_event(
            client,
            "takeoff_to_point_progress",
            stop_takeoff_events,
            args.timeout,
            {"wayline_cancel"},
        )
        if rc != 0:
            return rc

        resume_events = client.event_count("fly_to_point_progress")
        rc, resume_reply = service(
            client,
            "fly_to_point_update",
            {
                "fly_to_id": fly_to_id,
                "max_speed": args.max_speed,
                "break_point": {
                    "latitude": point_a["latitude"],
                    "longitude": point_a["longitude"],
                    "height": point_a["height"],
                    "state": 1,
                    "progress": 0.5,
                },
                "points": [point_b],
            },
            args,
            allowed_results={0},
            expected_status="ok",
        )
        if rc != 0:
            return rc
        rc = assert_output_simulated_false(resume_reply, "fly_to_point_update(resume)")
        if rc != 0:
            return rc
        rc, _ = wait_progress_event(
            client,
            "fly_to_point_progress",
            resume_events,
            args.timeout,
            {"wayline_progress"},
        )
        if rc != 0:
            return rc

        invalid_rc, _ = service(
            client,
            "fly_to_point_update",
            {"fly_to_id": fly_to_id, "points": []},
            args,
            allowed_results={INVALID_COMMAND_FORMAT},
            expected_status=None,
        )
        if invalid_rc != 0:
            return invalid_rc

        cleanup_events = client.event_count("fly_to_point_progress")
        service(client, "fly_to_point_stop", {"fly_to_id": fly_to_id}, args, allowed_results={0}, expected_status="ok")
        wait_progress_event(client, "fly_to_point_progress", cleanup_events, min(args.timeout, 5.0), {"wayline_cancel"})

        rc, _ = service(client, "drc_mode_exit", {}, args, allowed_results={0})
        if rc != 0:
            return rc
        drc_entered = False

        rc, state_reply = service(client, "property_get", {"properties": ["drc_state"]}, args, allowed_results={0})
        if rc != 0:
            return rc
        if state_reply.get("data", {}).get("drc_state") != 0:
            print(
                f"expected drc_state=0 after exit, actual={state_reply.get('data', {}).get('drc_state')}",
                file=sys.stderr,
            )
            return 6

        stale_total = client.stale_total()
        if args.fail_on_stale and stale_total:
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
            samples = client.stale_sample_summary()
            if samples:
                print(json.dumps(samples, ensure_ascii=False, indent=2), file=sys.stderr)
            return 10

        print(
            "dock3 flyto breakpoint test ok "
            f"(flight_id={flight_id}, fly_to_id={fly_to_id}, accepted={client.stats['accepted']}, "
            f"events={client.stats['events']}, ignored_mismatched={client.stats['ignored_mismatched']}, "
            f"ignored_legacy={client.stats['ignored_legacy']}, ignored_late={client.stats['ignored_late']}, "
            f"ignored_duplicate={client.stats['ignored_duplicate']}, "
            f"ignored_unknown={client.stats['ignored_unknown']}, "
            f"ignored_wrong_topic={client.stats['ignored_wrong_topic']}, ignored_total={stale_total})"
        )
        return 0
    finally:
        if drc_entered:
            try:
                service(client, "drc_mode_exit", {}, args, allowed_results={0, 319999})
            except Exception as exc:
                print(f"warning: DRC cleanup failed: {exc}", file=sys.stderr)


def main() -> int:
    parser = argparse.ArgumentParser(description="Test DJI Dock3 flyto breakpoint/resume mode over MQTT")
    add_config_argument(parser)
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--sn", default=None)
    parser.add_argument("--timeout", type=float, default=12.0)
    parser.add_argument("--qos", type=int, choices=[0, 1, 2], default=1)
    parser.add_argument("--quiet-stale", action="store_true")
    parser.add_argument("--fail-on-stale", action="store_true")
    parser.add_argument("--drone-sn", default=DEFAULT_DRONE_SN)
    parser.add_argument("--osd-wait", type=float, default=4.0)
    parser.add_argument("--flight-id", default="")
    parser.add_argument("--fly-to-id", default="")
    parser.add_argument("--target-lat", type=float, default=None)
    parser.add_argument("--target-lon", type=float, default=None)
    parser.add_argument("--target-height", type=float, default=50.0)
    parser.add_argument("--first-offset-meters", type=float, default=25.0)
    parser.add_argument("--resume-offset-meters", type=float, default=55.0)
    parser.add_argument("--resume-height-delta", type=float, default=5.0)
    parser.add_argument("--security-takeoff-height", type=float, default=30.0)
    parser.add_argument("--rth-altitude", type=float, default=80.0)
    parser.add_argument("--max-speed", type=float, default=8.0)
    parser.add_argument("--allow-fallback-target", action="store_true")
    parser.add_argument("--drc-broker-address", default="127.0.0.1:1883")
    parser.add_argument("--drc-username", default="sim-user")
    parser.add_argument("--drc-password", default="sim-token")
    parser.add_argument("--drc-expire-time", type=int, default=3600)
    parser.add_argument("--drc-enable-tls", action="store_true")
    parser.add_argument("--osd-frequency", type=int, default=10)
    parser.add_argument("--hsi-frequency", type=int, default=1)
    args = parser.parse_args()
    args.host, args.port, args.sn = resolve_mqtt_settings(args)

    if args.timeout <= 0:
        parser.error("--timeout must be positive")
    if args.osd_wait < 0:
        parser.error("--osd-wait must be non-negative")
    if (args.target_lat is None) != (args.target_lon is None):
        parser.error("--target-lat and --target-lon must be provided together")
    if args.max_speed <= 0:
        parser.error("--max-speed must be positive")

    client = MqttRequestClient(
        args.host,
        args.port,
        args.sn,
        args.qos,
        quiet_stale=args.quiet_stale,
        client_id_prefix="edge_gateway_dock3_flyto_breakpoint",
        subscribe_events=True,
        observe_topics=osd_topics(args.drone_sn),
    )
    if not client.connect(args.timeout):
        print("MQTT connect timeout", file=sys.stderr)
        return 2
    try:
        return run_flow(client, args)
    finally:
        client.disconnect()


if __name__ == "__main__":
    raise SystemExit(main())
