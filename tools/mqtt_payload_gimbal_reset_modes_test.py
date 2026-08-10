#!/usr/bin/env python3
"""Test Dock3 Cloud API gimbal_reset reset_mode values on a real gateway.

This tool only publishes the existing Dock3 Cloud API service method:

    gimbal_reset

It does not instantiate the gateway, call ViewLink SDK helpers, or use locally
invented methods. The Edge Gateway and real gimbal chain must already be
running.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "tools"))

from mqtt_test_client import MqttRequestClient, result_code
from src.payload.dock3_payload import payload_index_from_config
from tool_config import add_config_argument, load_config, resolve_mqtt_settings


RESET_MODE_LABELS = {
    0: "回中",
    1: "向下",
    2: "偏航回中",
    3: "俯仰向下",
}


@dataclass(frozen=True)
class ResetModeResult:
    reset_mode: int
    label: str
    rc: int
    reply: dict[str, Any] | None
    manual_ok: bool | None = None


def _payload_index(args: argparse.Namespace) -> str:
    if args.payload_index:
        return args.payload_index
    return payload_index_from_config(load_config(args.config))


def parse_reset_modes(raw_modes: list[str]) -> list[int]:
    modes: list[int] = []
    for raw in raw_modes:
        for item in str(raw).split(","):
            item = item.strip()
            if not item:
                continue
            mode = int(item)
            if mode not in RESET_MODE_LABELS:
                raise argparse.ArgumentTypeError(
                    f"unsupported reset_mode={mode}; expected one of {sorted(RESET_MODE_LABELS)}"
                )
            modes.append(mode)
    return modes or sorted(RESET_MODE_LABELS)


def build_gimbal_reset_payload(payload_index: str, reset_mode: int) -> dict[str, Any]:
    return {
        "payload_index": payload_index,
        "reset_mode": reset_mode,
    }


def request_services(
    client: MqttRequestClient,
    method: str,
    data: dict[str, Any],
    timeout: float,
    allowed_results: set[int] | None = None,
) -> tuple[int, dict[str, Any] | None]:
    return client.request(
        method,
        data,
        timeout,
        channel="services",
        allowed_results={0} if allowed_results is None else allowed_results,
    )


def manual_confirm(reset_mode: int, label: str) -> bool:
    answer = input(f"确认 reset_mode={reset_mode} ({label}) 真实云台动作符合预期？[y/N] ").strip().lower()
    return answer in {"y", "yes"}


def wait_operator_ready(reset_mode: int, label: str) -> None:
    input(f"请先人工确认云台处于可观察姿态，然后按 Enter 下发 reset_mode={reset_mode} ({label}) ...")


def run_reset_mode_checks(client: MqttRequestClient, args: argparse.Namespace) -> int:
    payload_index = _payload_index(args)
    modes = parse_reset_modes(args.reset_mode)
    results: list[ResetModeResult] = []

    print(
        "real gateway gimbal_reset reset_mode test "
        f"(sn={args.sn}, payload_index={payload_index}, modes={modes})"
    )

    for reset_mode in modes:
        label = RESET_MODE_LABELS[reset_mode]

        if args.wait_before_reset:
            wait_operator_ready(reset_mode, label)

        payload = build_gimbal_reset_payload(payload_index, reset_mode)
        rc, reply = request_services(client, "gimbal_reset", payload, args.timeout)
        if rc != 0:
            results.append(ResetModeResult(reset_mode, label, rc, reply))
            if args.stop_on_failure:
                break
            continue

        code = result_code(reply)
        if code != 0:
            results.append(ResetModeResult(reset_mode, label, 4, reply))
            if args.stop_on_failure:
                break
            continue

        manual_ok = manual_confirm(reset_mode, label) if args.manual_confirm else None
        results.append(ResetModeResult(reset_mode, label, 0, reply, manual_ok))
        print(f"[ok] reset_mode={reset_mode} ({label}) reply result=0")

    failures = [item for item in results if item.rc != 0 or item.manual_ok is False]
    print_summary(results)
    if failures:
        return 6
    return 0


def print_summary(results: list[ResetModeResult]) -> None:
    print("gimbal_reset reset_mode summary:")
    for item in results:
        status = "ok" if item.rc == 0 and item.manual_ok is not False else f"failed rc={item.rc}"
        if item.manual_ok is True:
            status += ", manual=ok"
        elif item.manual_ok is False:
            status += ", manual=failed"
        print(f"- [{status}] reset_mode={item.reset_mode} ({item.label})")
        if item.reply is not None:
            print("  reply=" + json.dumps(item.reply, ensure_ascii=False))


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Test gimbal_reset reset_mode values through Dock3 Cloud API services on a real gateway."
    )
    add_config_argument(parser)
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--sn", default=None)
    parser.add_argument("--timeout", type=float, default=20.0)
    parser.add_argument("--qos", type=int, choices=[0, 1, 2], default=1)
    parser.add_argument("--quiet-stale", action="store_true")
    parser.add_argument("--fail-on-stale", action="store_true")
    parser.add_argument("--payload-index", default="", help="Dock3 payload index, e.g. 52-0-0.")
    parser.add_argument(
        "--reset-mode",
        action="append",
        default=[],
        help="reset_mode to test. Repeatable or comma-separated. Defaults to 0,1,2,3.",
    )
    parser.add_argument("--manual-confirm", action="store_true", help="Ask operator to confirm the real physical gimbal action.")
    parser.add_argument("--wait-before-reset", action="store_true", help="Pause before each reset so the operator can manually position the real gimbal.")
    parser.add_argument("--stop-on-failure", action="store_true")
    args = parser.parse_args()
    args.host, args.port, args.sn = resolve_mqtt_settings(args)

    client = MqttRequestClient(
        args.host,
        args.port,
        args.sn,
        args.qos,
        quiet_stale=args.quiet_stale,
        client_id_prefix="edge_gateway_payload_gimbal_reset_modes",
    )
    if not client.connect(args.timeout):
        print("MQTT connect timeout; make sure the real Edge Gateway and broker are running.", file=sys.stderr)
        return 2

    try:
        rc = run_reset_mode_checks(client, args)
        if rc != 0:
            return rc

        stale_total = client.stale_total()
        if args.fail_on_stale and stale_total:
            print(f"stale replies observed: {client.stats}", file=sys.stderr)
            return 10

        print(
            "dock3 payload gimbal-reset reset_mode test ok "
            f"(accepted={client.stats['accepted']}, ignored_total={stale_total})"
        )
        return 0
    finally:
        client.disconnect()


if __name__ == "__main__":
    raise SystemExit(main())
