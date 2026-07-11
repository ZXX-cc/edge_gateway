#!/usr/bin/env python3
"""Real-SITL one-key takeoff and wayline acceptance probe.

This script prepares a real wayline file, starts the one_key_takeoff_flow
service, then verifies that QGC/SITL actually enters the wayline by polling
flighttask_progress and drone OSD.  It is intended to run on the same server
that has the SITL/QGC/gateway stack and the mission file.
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

from mqtt_test_client import MqttRequestClient, output_status, result_code
from tool_config import add_config_argument, resolve_mqtt_settings


DEFAULT_DRONE_SN = "DRONESIM001"
DEFAULT_WAYLINE_FILE = "/home/fenghuolun/zwz/HD.kml"
THING_VERSION = "1.1.2"
EXPECTED_START_STEPS = [
    "precheck",
    "charge_close",
    "cover_open",
    "putter_open",
    "nest_takeoff_request",
    "flight_takeoff",
    "flighttask_execute",
]


def now_ms() -> int:
    return int(time.time() * 1000)


def file_url(path: str) -> str:
    resolved = Path(path).expanduser().resolve()
    return resolved.as_uri() if resolved.exists() else path


def prepare_payload(flight_id: str, wayline_file: str) -> dict[str, Any]:
    return {
        "flight_id": flight_id,
        "task_type": 0,
        "execute_time": now_ms() + 30_000,
        "file": {
            "url": file_url(wayline_file),
            "fingerprint": "real-sitl-hd-kml",
        },
        "ready_conditions": {},
        "executable_conditions": {
            "storage_capacity": 0,
        },
        "rth_altitude": 80,
        "out_of_control_action": 0,
        "exit_wayline_when_rc_lost": 0,
        "simulate_mission": {
            "is_enable": 0,
        },
    }


def update_topo_payload(args) -> dict[str, Any]:
    return {
        "domain": args.gateway_domain,
        "type": args.gateway_type,
        "sub_type": args.gateway_sub_type,
        "device_secret": args.gateway_secret,
        "nonce": args.gateway_nonce,
        "thing_version": args.thing_version,
        "sub_devices": [
            {
                "sn": args.drone_sn,
                "domain": args.drone_domain,
                "type": args.drone_type,
                "sub_type": args.drone_sub_type,
                "index": args.drone_index,
                "device_secret": args.drone_secret,
                "nonce": args.drone_nonce,
                "thing_version": args.thing_version,
            }
        ],
    }


def _candidate_drone_sns(primary: str) -> list[str]:
    candidates = [primary, DEFAULT_DRONE_SN, "DRONE-SIM-001"]
    return list(dict.fromkeys(sn for sn in candidates if sn))


def osd_topics(drone_sn: str) -> list[str]:
    return [f"thing/product/{sn}/osd" for sn in _candidate_drone_sns(drone_sn)]


def _field_value(payload: Any, key: str) -> Any:
    if not isinstance(payload, dict):
        return None
    data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    if not isinstance(data, dict):
        return None
    value = data.get(key)
    return value.get("value") if isinstance(value, dict) and "value" in value else value


def _float_field(payload: Any, key: str) -> float | None:
    try:
        value = float(_field_value(payload, key))
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


def _latest_observed(client: MqttRequestClient, topics: Iterable[str]) -> tuple[str | None, Any | None]:
    for topic in topics:
        messages = client.observed_by_topic.get(topic, [])
        if messages:
            return topic, messages[-1]
    return None, None


def wait_for_osd(client: MqttRequestClient, topics: Iterable[str], timeout: float) -> tuple[str | None, Any | None]:
    topics = list(topics)
    topic, payload = _latest_observed(client, topics)
    if payload is not None and has_drone_osd_fields(payload):
        return topic, payload

    counts = {topic: client.observed_count(topic) for topic in topics}
    deadline = time.monotonic() + max(0.0, timeout)
    while time.monotonic() < deadline:
        for topic in topics:
            messages = client.observed_by_topic.get(topic, [])
            if len(messages) > counts[topic]:
                counts[topic] = len(messages)
                payload = messages[-1]
                if has_drone_osd_fields(payload):
                    return topic, payload
        remaining = deadline - time.monotonic()
        client.observed_signal.wait(max(0.05, min(0.5, remaining)))
        client.observed_signal.clear()
    return _latest_observed(client, topics)


def has_drone_osd_fields(payload: Any) -> bool:
    if not isinstance(payload, dict):
        return False
    data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    if not isinstance(data, dict):
        return False
    return all(field in data for field in ("height", "latitude", "longitude", "mode_code"))


def osd_snapshot(topic: str | None, payload: Any) -> dict[str, Any]:
    if payload is None:
        return {"available": False, "topic": topic}
    return {
        "available": True,
        "topic": topic,
        "height": _field_value(payload, "height"),
        "altitude": _field_value(payload, "altitude"),
        "vertical_speed": _field_value(payload, "vertical_speed"),
        "horizontal_speed": _field_value(payload, "horizontal_speed"),
        "mode_code": _field_value(payload, "mode_code"),
        "gear": _field_value(payload, "gear"),
        "battery": _field_value(payload, "battery"),
        "latitude": _field_value(payload, "latitude"),
        "longitude": _field_value(payload, "longitude"),
    }


def output(payload: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    node = payload.get("data", {}).get("output", {})
    return node if isinstance(node, dict) else {}


def output_ext(payload: dict[str, Any] | None) -> dict[str, Any]:
    ext = output(payload).get("ext", {})
    return ext if isinstance(ext, dict) else {}


def wayline_file_summary(payload: dict[str, Any] | None) -> dict[str, Any]:
    file_node = output_ext(payload).get("file", {})
    return file_node if isinstance(file_node, dict) else {}


def step_names(payload: dict[str, Any] | None) -> list[str]:
    steps = output(payload).get("steps", [])
    names = []
    for step in steps:
        if isinstance(step, dict):
            name = step.get("step") or step.get("name")
            if isinstance(name, str):
                names.append(name)
    return names


def print_probe(label: str, ok: bool, detail: dict[str, Any] | None = None) -> None:
    print(f"[{'PASS' if ok else 'FAIL'}] {label}")
    if detail:
        print(json.dumps(detail, ensure_ascii=False, indent=2))


def wait_for_climb(
    client: MqttRequestClient,
    topics: list[str],
    *,
    start_height: float,
    min_climb: float,
    timeout: float,
) -> tuple[bool, dict[str, Any]]:
    deadline = time.monotonic() + timeout
    target_height = start_height + min_climb
    max_height = start_height
    samples: list[dict[str, Any]] = []
    counts = {topic: max(0, client.observed_count(topic) - 1) for topic in topics}

    while time.monotonic() < deadline:
        consumed = False
        for topic in topics:
            messages = client.observed_by_topic.get(topic, [])
            if len(messages) <= counts[topic]:
                continue
            counts[topic] = len(messages)
            payload = messages[-1]
            consumed = True
            height = _float_field(payload, "height")
            if height is None:
                continue
            max_height = max(max_height, height)
            samples.append({
                "topic": topic,
                "height": height,
                "mode_code": _field_value(payload, "mode_code"),
                "gear": _field_value(payload, "gear"),
                "vertical_speed": _field_value(payload, "vertical_speed"),
            })
            if height >= target_height:
                return True, {
                    "start_height": start_height,
                    "target_height": target_height,
                    "max_height": max_height,
                    "last_sample": samples[-1],
                    "sample_count": len(samples),
                }

        if consumed:
            continue

        remaining = deadline - time.monotonic()
        client.observed_signal.wait(max(0.05, min(0.5, remaining)))
        client.observed_signal.clear()

    return False, {
        "start_height": start_height,
        "target_height": target_height,
        "max_height": max_height,
        "sample_count": len(samples),
        "last_samples": samples[-5:],
    }


def validate_wayline_file(payload: dict[str, Any] | None, min_waypoints: int) -> tuple[bool, dict[str, Any]]:
    summary = wayline_file_summary(payload)
    waypoint_count = summary.get("waypoint_count")
    ok = bool(summary.get("parsed")) and isinstance(waypoint_count, int) and waypoint_count >= min_waypoints
    return ok, summary


def wait_for_wayline_progress(client: MqttRequestClient, args, flight_id: str) -> tuple[bool, dict[str, Any]]:
    deadline = time.monotonic() + args.mission_observe_timeout
    samples: list[dict[str, Any]] = []
    max_waypoint_index = -1

    while time.monotonic() < deadline:
        rc, progress = client.request(
            "flighttask_progress",
            {"flight_id": flight_id},
            args.timeout,
            channel="services",
            allowed_results={0},
            expected_status=None,
            verbose=False,
        )
        if rc != 0:
            return False, {"reason": "flighttask_progress request failed", "rc": rc}

        status = output_status(progress)
        ext = output_ext(progress)
        waypoint_index = ext.get("current_waypoint_index")
        waypoint_count = ext.get("waypoint_count") or ext.get("total_waypoints")
        mission_state = ext.get("wayline_mission_state")
        if isinstance(waypoint_index, int):
            max_waypoint_index = max(max_waypoint_index, waypoint_index)
        sample = {
            "status": status,
            "flight_id": ext.get("flight_id"),
            "current_waypoint_index": waypoint_index,
            "waypoint_count": waypoint_count,
            "wayline_mission_state": mission_state,
            "percent": output(progress).get("progress", {}).get("percent"),
        }
        samples.append(sample)
        print_probe("航线进度轮询", True, sample)

        if ext.get("flight_id") != flight_id:
            return False, {"reason": "flight_id mismatch", "expected": flight_id, "actual": ext.get("flight_id")}
        if status == "ok":
            return True, {"reason": "mission completed", "sample": sample, "sample_count": len(samples)}
        if status == "in_progress" and max_waypoint_index >= args.min_waypoint_index:
            return True, {
                "reason": "waypoint advanced",
                "max_waypoint_index": max_waypoint_index,
                "sample": sample,
                "sample_count": len(samples),
            }
        if status not in {"in_progress", "ok"}:
            return False, {"reason": "unexpected mission status", "sample": sample, "samples": samples[-5:]}

        time.sleep(min(args.mission_poll_seconds, max(0.0, deadline - time.monotonic())))

    return False, {
        "reason": "mission observe timeout",
        "max_waypoint_index": max_waypoint_index,
        "required_min_waypoint_index": args.min_waypoint_index,
        "samples": samples[-8:],
    }


def pause_wayline(client: MqttRequestClient, args, flight_id: str) -> tuple[bool, dict[str, Any]]:
    before = client.event_count("flighttask_progress")
    rc, reply = client.request(
        "flighttask_pause",
        {},
        args.timeout,
        channel="services",
        allowed_results={0},
        expected_status="paused",
    )
    if rc != 0:
        return False, {
            "reason": "flighttask_pause request failed",
            "rc": rc,
            "result": result_code(reply),
            "status": output_status(reply),
            "msg": reply.get("msg") if isinstance(reply, dict) else None,
        }

    rc, progress = client.request(
        "flighttask_progress",
        {"flight_id": flight_id},
        args.timeout,
        channel="services",
        allowed_results={0},
        expected_status="paused",
    )
    if rc != 0:
        return False, {
            "reason": "flighttask_progress did not report paused",
            "rc": rc,
            "result": result_code(progress),
            "status": output_status(progress),
        }

    event_rc, event = client.wait_event(
        "flighttask_progress",
        before,
        args.event_timeout,
        expected_status="paused",
    )
    return True, {
        "reply_status": output_status(reply),
        "progress_status": output_status(progress),
        "event_observed": event_rc == 0,
        "event_status": output_status(event),
        "ext": output_ext(progress),
    }


def prime_guided_mode(client: MqttRequestClient, args) -> int:
    if args.skip_guided_prime:
        return 0
    rc, reply = client.request(
        "set_flight_mode",
        {"mode": "GUIDED"},
        args.timeout,
        channel="services",
        allowed_results={0},
        expected_status=None,
    )
    data = reply.get("data", {}) if isinstance(reply, dict) else {}
    print_probe("按 demo 流程预切 GUIDED", rc == 0, {
        "result": result_code(reply),
        "mode": data.get("mode"),
        "mode_code": data.get("mode_code"),
        "msg": reply.get("msg") if isinstance(reply, dict) else None,
    })
    return rc


def run(args) -> int:
    topics = osd_topics(args.drone_sn)
    client = MqttRequestClient(
        args.host,
        args.port,
        args.sn,
        args.qos,
        quiet_stale=args.quiet_stale,
        client_id_prefix="edge_gateway_one_key_takeoff_sitl",
        subscribe_events=True,
        observe_topics=topics,
    )
    if not client.connect(args.timeout):
        print("timeout waiting for MQTT connection", file=sys.stderr)
        return 1

    flight_id = args.flight_id or f"one-key-sitl-{int(time.time())}"
    try:
        if not args.skip_topo:
            rc, topo_reply = client.request(
                "update_topo",
                update_topo_payload(args),
                args.timeout,
                channel="services",
                allowed_results={0},
                expected_status=None,
            )
            print_probe("注册飞行器子设备拓扑", rc == 0, {
                "drone_sn": args.drone_sn,
                "result": result_code(topo_reply),
            })
            if rc != 0:
                return rc

        rc, prepare_reply = client.request(
            "flighttask_prepare",
            prepare_payload(flight_id, args.wayline_file),
            args.timeout,
            channel="services",
            allowed_results={0},
            expected_status="sent",
        )
        if rc != 0:
            return rc
        parsed, file_summary = validate_wayline_file(prepare_reply, args.expect_waypoint_count)
        print_probe("真实航线文件准备", parsed, {
            "wayline_file": args.wayline_file,
            "file_summary": file_summary,
        })
        if not parsed:
            return 11

        topic, initial_osd = wait_for_osd(client, topics, args.osd_wait)
        initial_height = _float_field(initial_osd, "height")
        if initial_height is None:
            print_probe("SITL OSD 起始高度", False, {"topics": topics, "snapshot": osd_snapshot(topic, initial_osd)})
            return 12
        print_probe("SITL OSD 起始高度", True, osd_snapshot(topic, initial_osd))

        if initial_height > args.max_start_height and not args.allow_airborne_start:
            print(
                "SITL already appears airborne; pass --allow-airborne-start to run anyway",
                file=sys.stderr,
            )
            return 13

        rc = prime_guided_mode(client, args)
        if rc != 0:
            return rc

        one_key_before = client.event_count("one_key_takeoff_progress")
        flighttask_before = client.event_count("flighttask_progress")
        request = {
            "flight_id": flight_id,
            "takeoff_height": args.takeoff_height,
            "auto_complete": False,
        }
        rc, reply = client.request(
            "one_key_takeoff_flow",
            request,
            args.timeout,
            channel="services",
            allowed_results={0},
            expected_status="in_progress",
        )
        if rc != 0:
            return rc

        actual_steps = step_names(reply)
        if actual_steps != EXPECTED_START_STEPS:
            print_probe(
                "一键启航业务步骤",
                False,
                {"expected": EXPECTED_START_STEPS, "actual": actual_steps},
            )
            return 14
        print_probe("一键启航业务步骤", True, {"steps": actual_steps})

        rc, one_key_event = client.wait_event(
            "one_key_takeoff_progress",
            one_key_before,
            args.event_timeout,
            expected_status=None,
        )
        if rc != 0:
            return rc
        print_probe("一键启航进度事件", True, {"status": output_status(one_key_event)})

        rc, flighttask_event = client.wait_event(
            "flighttask_progress",
            flighttask_before,
            args.event_timeout,
            expected_status="in_progress",
        )
        if rc != 0:
            return rc
        print_probe("航线任务进度事件", True, {"status": output_status(flighttask_event)})

        climbed, detail = wait_for_climb(
            client,
            topics,
            start_height=initial_height,
            min_climb=args.min_climb,
            timeout=args.climb_timeout,
        )
        print_probe("真实 SITL 起飞爬升", climbed, detail)
        if not climbed:
            return 15

        progressed, progress_detail = wait_for_wayline_progress(client, args, flight_id)
        print_probe("真实 SITL 航线执行", progressed, progress_detail)
        if not progressed:
            return 16
        if progress_detail.get("reason") == "mission completed":
            print_probe("暂停航线验证", False, {
                "reason": "mission completed before pause could be tested",
                "detail": progress_detail,
            })
            return 17

        paused, pause_detail = pause_wayline(client, args, flight_id)
        print_probe("暂停航线验证", paused, pause_detail)
        if not paused:
            return 18

        ignored_total = client.stale_total()
        if args.fail_on_stale and ignored_total:
            print(f"stale replies observed: {client.stats}", file=sys.stderr)
            print(json.dumps(client.stale_sample_summary(), ensure_ascii=False, indent=2), file=sys.stderr)
            return 9

        print(
            "one-key takeoff real SITL wayline test ok "
            f"(flight_id={flight_id}, accepted={client.stats['accepted']}, "
            f"events={client.stats['events']}, observed={client.stats['observed']}, "
            f"ignored_total={ignored_total})"
        )
        return 0
    finally:
        client.disconnect()


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify one-key takeoff executes a real wayline in QGC/SITL")
    add_config_argument(parser)
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--sn", default=None)
    parser.add_argument("--drone-sn", default=DEFAULT_DRONE_SN)
    parser.add_argument("--timeout", type=float, default=12.0)
    parser.add_argument("--event-timeout", type=float, default=5.0)
    parser.add_argument("--osd-wait", type=float, default=10.0)
    parser.add_argument("--climb-timeout", type=float, default=25.0)
    parser.add_argument("--qos", type=int, choices=[0, 1, 2], default=1)
    parser.add_argument("--flight-id", default="")
    parser.add_argument("--wayline-file", default=DEFAULT_WAYLINE_FILE)
    parser.add_argument("--expect-waypoint-count", type=int, default=3)
    parser.add_argument("--takeoff-height", type=float, default=20.0)
    parser.add_argument("--min-climb", type=float, default=3.0)
    parser.add_argument("--min-waypoint-index", type=int, default=1)
    parser.add_argument("--mission-observe-timeout", type=float, default=120.0)
    parser.add_argument("--mission-poll-seconds", type=float, default=5.0)
    parser.add_argument("--max-start-height", type=float, default=5.0)
    parser.add_argument("--allow-airborne-start", action="store_true")
    parser.add_argument("--skip-topo", action="store_true")
    parser.add_argument("--skip-guided-prime", action="store_true")
    parser.add_argument("--gateway-domain", default="3")
    parser.add_argument("--gateway-type", type=int, default=2)
    parser.add_argument("--gateway-sub-type", type=int, default=0)
    parser.add_argument("--gateway-secret", default="gw-secret")
    parser.add_argument("--gateway-nonce", default="gw-nonce")
    parser.add_argument("--thing-version", default=THING_VERSION)
    parser.add_argument("--drone-domain", default="0")
    parser.add_argument("--drone-type", type=int, default=91)
    parser.add_argument("--drone-sub-type", type=int, default=0)
    parser.add_argument("--drone-index", default="A")
    parser.add_argument("--drone-secret", default="drone-secret")
    parser.add_argument("--drone-nonce", default="drone-nonce")
    parser.add_argument("--quiet-stale", action="store_true")
    parser.add_argument("--fail-on-stale", action="store_true")
    args = parser.parse_args()
    if args.expect_waypoint_count <= 0:
        parser.error("--expect-waypoint-count must be positive")
    if args.min_waypoint_index < 0:
        parser.error("--min-waypoint-index must be non-negative")
    if args.mission_observe_timeout <= 0:
        parser.error("--mission-observe-timeout must be positive")
    if args.mission_poll_seconds <= 0:
        parser.error("--mission-poll-seconds must be positive")
    args.host, args.port, args.sn = resolve_mqtt_settings(args)
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
