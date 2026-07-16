#!/usr/bin/env python3
"""Prepare the simulated Dock3 stack for platform wayline execution.

This script is intentionally platform-facing: it does not upload a wayline,
does not execute takeoff, and does not write mission/breakpoint state.  It only
clears simulator fault overrides, sends safe dock bootstrap commands through
the gateway, and waits until the gateway global state admits a real wayline
prepare/execute flow.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "tools"))

from mqtt_test_client import MqttRequestClient, result_code  # noqa: E402
from tool_config import add_config_argument, resolve_mqtt_settings  # noqa: E402


DEFAULT_FAULT_STATE_FILE = "/tmp/edge_gateway_nest_faults.json"
BOOTSTRAP_COMMANDS = ("drc_mode_exit", "supplement_light_close", "charge_close")


def reset_fault_file(path: str, enabled: bool) -> None:
    if not enabled or not path:
        return
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(
            {
                "active": False,
                "scenario": "platform_wayline_preflight_ready",
                "faults": {},
                "updated_at": int(time.time() * 1000),
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"simulator fault overrides cleared -> {target}")


def request_state(client: MqttRequestClient, timeout: float, compact: bool = False) -> tuple[int, dict[str, Any]]:
    rc, reply = client.request(
        "gateway_state_query",
        {"compact": compact},
        timeout,
        channel="services",
        allowed_results={0},
        verbose=False,
    )
    if rc != 0 or not isinstance(reply, dict):
        return rc, {}
    data = reply.get("data") if isinstance(reply.get("data"), dict) else {}
    state = data.get("state") if isinstance(data.get("state"), dict) else {}
    return 0, state


def state_ready(state: dict[str, Any]) -> bool:
    return (
        state.get("dock") == "ready"
        and state.get("aircraft") == "online"
        and state.get("mission") == "idle"
        and state.get("drc") in {"idle", "unknown"}
        and state.get("cloud") == "online"
        and state.get("device") in {"bound", "online"}
    )


def explain_state(state: dict[str, Any]) -> str:
    return (
        f"phase={state.get('gateway_phase')} cloud={state.get('cloud')} "
        f"device={state.get('device')} dock={state.get('dock')} "
        f"aircraft={state.get('aircraft')} mission={state.get('mission')} "
        f"drc={state.get('drc')} safety={state.get('safety')}"
    )


def print_diagnostics(state: dict[str, Any]) -> None:
    print("preflight ready timeout; latest gateway state:")
    print(json.dumps(state, ensure_ascii=False, indent=2))
    details = state.get("details") if isinstance(state.get("details"), dict) else {}
    dock = details.get("dock") if isinstance(details.get("dock"), dict) else {}
    if state.get("dock") == "offline":
        print(
            "diagnostic: dock=offline means gateway has not seen valid nest status, "
            "or nest/plc heartbeat is false."
        )
        print(
            "check nest simulator 12004/DRONE_NEST_RESPONSE logs and fault file; "
            f"nest_heartbeat={dock.get('nest_heartbeat')} "
            f"plc_heartbeat={dock.get('plc_heartbeat')} "
            f"takeoff_allowed={dock.get('takeoff_allowed')} "
            f"data_valid={dock.get('data_valid')}"
        )
    if state.get("aircraft") != "online":
        print("diagnostic: aircraft is not online; check SITL output to gateway MAVLink port.")
    if state.get("mission") != "idle":
        print("diagnostic: mission is not idle; run flighttask_undo/return cleanup before a new task.")
    if state.get("drc") not in {"idle", "unknown"}:
        print("diagnostic: DRC is active; run drc_mode_exit before wayline execution.")


def send_bootstrap_commands(client: MqttRequestClient, timeout: float) -> None:
    for method in BOOTSTRAP_COMMANDS:
        rc, reply = client.request(
            method,
            {},
            timeout,
            channel="services",
            allowed_results=None,
            verbose=True,
        )
        code = result_code(reply)
        if rc == 0:
            print(f"bootstrap command sent: method={method}, result={code}")
        else:
            print(f"bootstrap command failed/nonfatal: method={method}, rc={rc}, result={code}")


def wait_until_ready(client: MqttRequestClient, args: argparse.Namespace) -> tuple[int, dict[str, Any]]:
    deadline = time.time() + args.ready_timeout
    last_state: dict[str, Any] = {}
    next_bootstrap = 0.0
    while time.time() < deadline:
        rc, state = request_state(client, args.timeout, compact=False)
        if rc == 0 and state:
            last_state = state
            print(f"gateway state: {explain_state(state)}")
            if state_ready(state):
                return 0, state

            if time.time() >= next_bootstrap and state.get("dock") in {"offline", "unknown"}:
                send_bootstrap_commands(client, args.timeout)
                next_bootstrap = time.time() + args.bootstrap_interval
        else:
            print(f"gateway_state_query failed/nonfatal: rc={rc}")

        time.sleep(args.poll_interval)
    return 9, last_state


def main() -> int:
    parser = argparse.ArgumentParser(description="Bring gateway/dock/SITL into wayline-ready state without takeoff")
    add_config_argument(parser)
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--sn", default=None)
    parser.add_argument("--timeout", type=float, default=8.0)
    parser.add_argument("--ready-timeout", type=float, default=40.0)
    parser.add_argument("--poll-interval", type=float, default=2.0)
    parser.add_argument("--bootstrap-interval", type=float, default=6.0)
    parser.add_argument("--qos", type=int, choices=[0, 1, 2], default=1)
    parser.add_argument("--fault-state-file", default=DEFAULT_FAULT_STATE_FILE)
    parser.add_argument("--skip-fault-reset", action="store_true")
    parser.add_argument("--quiet-stale", action="store_true")
    parser.add_argument("--fail-on-stale", action="store_true")
    args = parser.parse_args()
    args.host, args.port, args.sn = resolve_mqtt_settings(args)

    reset_fault_file(args.fault_state_file, enabled=not args.skip_fault_reset)

    client = MqttRequestClient(
        args.host,
        args.port,
        args.sn,
        args.qos,
        quiet_stale=args.quiet_stale,
        client_id_prefix="edge_gateway_platform_wayline_preflight_ready",
    )
    if not client.connect(args.timeout):
        print("MQTT connect timeout", file=sys.stderr)
        return 2

    try:
        send_bootstrap_commands(client, args.timeout)
        rc, state = wait_until_ready(client, args)
        if rc != 0:
            print_diagnostics(state)
            return rc
        stale_total = client.stale_total()
        if args.fail_on_stale and stale_total:
            print(f"strict stale replies observed: stats={client.stats}", file=sys.stderr)
            return 41
        print("platform wayline preflight ready ok")
        print(json.dumps(state, ensure_ascii=False, indent=2))
        return 0
    finally:
        client.disconnect()


if __name__ == "__main__":
    raise SystemExit(main())
