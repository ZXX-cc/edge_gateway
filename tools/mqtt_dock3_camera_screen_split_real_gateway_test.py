#!/usr/bin/env python3
"""Real-gateway acceptance test for Dock3 camera_screen_split.

This tool publishes only the existing Dock3 Cloud API services method
``camera_screen_split``.  It verifies the real gateway reply and the gateway's
payload_status_notify event for open, close, and mode switch steps.
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

from mqtt_dock3_payload_control_test import Dock3PayloadControlClient  # noqa: E402
from mqtt_test_client import result_code  # noqa: E402
from src.payload.dock3_payload import payload_index_from_config  # noqa: E402
from tool_config import add_config_argument, load_config, resolve_mqtt_settings  # noqa: E402


CLOUD_METHOD = "camera_screen_split"
SUCCESS_RESULTS = {0}
PIP_MODES = {"visible_ir", "eo_ir", "ir_visible", "ir_eo"}
NON_PIP_MODES = {"visible", "normal", "eo", "ir", "infrared", "thermal", "fusion"}


@dataclass(frozen=True)
class SplitStep:
    label: str
    enable: bool
    mode: str
    expected_pip: bool


def _payload_index(args) -> str:
    if args.payload_index:
        return args.payload_index
    return payload_index_from_config(load_config(args.config))


def _data(reply: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(reply, dict):
        return {}
    data = reply.get("data")
    return data if isinstance(data, dict) else {}


def _viewlink_result(reply: dict[str, Any] | None) -> dict[str, Any]:
    viewlink = _data(reply).get("viewlink_result")
    return viewlink if isinstance(viewlink, dict) else {}


def _event_payloads(event: dict[str, Any] | None) -> list[dict[str, Any]]:
    data = event.get("data") if isinstance(event, dict) else None
    payloads = data.get("payloads") if isinstance(data, dict) else None
    return [item for item in payloads if isinstance(item, dict)] if isinstance(payloads, list) else []


def _event_last_control(event: dict[str, Any] | None, payload_index: str) -> dict[str, Any]:
    for payload in _event_payloads(event):
        if str(payload.get("payload_index") or "") != payload_index:
            continue
        last_control = payload.get("last_control")
        if isinstance(last_control, dict):
            return last_control
    return {}


def _expected_pip(enable: bool, mode: str) -> bool:
    mode_key = str(mode or "").strip().lower()
    if mode_key in PIP_MODES:
        return True
    if mode_key in NON_PIP_MODES:
        return False
    return bool(enable)


def build_steps(args) -> list[SplitStep]:
    return [
        SplitStep("close baseline", False, args.close_mode, _expected_pip(False, args.close_mode)),
        SplitStep("open PIP", True, args.open_mode, _expected_pip(True, args.open_mode)),
        SplitStep("switch PIP mode", True, args.switch_mode, _expected_pip(True, args.switch_mode)),
        SplitStep("close PIP", False, args.close_mode, _expected_pip(False, args.close_mode)),
    ]


def evaluate_step(
    step: SplitStep,
    payload_index: str,
    rc: int,
    reply: dict[str, Any] | None,
    event: dict[str, Any] | None,
) -> tuple[str, str]:
    if rc != 0:
        return "fail", f"mqtt request rc={rc}"
    if result_code(reply) not in SUCCESS_RESULTS:
        return "fail", f"unexpected services result={result_code(reply)}"

    data = _data(reply)
    mode = str(data.get("mode") or "").lower()
    if data.get("payload_index") != payload_index:
        return "fail", f"payload_index mismatch: {data.get('payload_index')}"
    if data.get("enable") is not step.enable:
        return "fail", f"enable mismatch: expected={step.enable}, actual={data.get('enable')}"
    if mode != step.mode.lower():
        return "fail", f"mode mismatch: expected={step.mode}, actual={mode}"
    if data.get("enable_pip") is not step.expected_pip:
        return "fail", f"enable_pip mismatch: expected={step.expected_pip}, actual={data.get('enable_pip')}"

    viewlink = _viewlink_result(reply)
    if viewlink.get("success") is not True:
        return "fail", f"real payload adapter did not confirm success: {viewlink}"

    last_control = _event_last_control(event, payload_index)
    if not last_control:
        return "fail", "payload_status_notify event missing target payload last_control"
    if last_control.get("method") != CLOUD_METHOD:
        return "fail", f"event method mismatch: {last_control.get('method')}"
    if last_control.get("enable") is not step.enable:
        return "fail", f"event enable mismatch: {last_control.get('enable')}"
    if str(last_control.get("mode") or "").lower() != step.mode.lower():
        return "fail", f"event mode mismatch: {last_control.get('mode')}"

    return "ok", f"enable={step.enable}, mode={mode}, enable_pip={data.get('enable_pip')}"


def request_split_step(
    client: Dock3PayloadControlClient,
    step: SplitStep,
    payload_index: str,
    timeout: float,
) -> tuple[int, dict[str, Any] | None, dict[str, Any] | None]:
    previous_events = client.event_count("payload_status_notify")
    rc, reply = client.request(
        CLOUD_METHOD,
        {"payload_index": payload_index, "enable": step.enable, "mode": step.mode},
        timeout,
        channel="services",
        allowed_results=None,
        expected_status=None,
    )
    if rc != 0 or result_code(reply) != 0:
        return rc, reply, None
    event_rc, event = client.wait_event(
        "payload_status_notify",
        previous_events,
        timeout,
        expected_status=None,
    )
    if event_rc != 0:
        return event_rc, reply, event
    return rc, reply, event


def run_real_screen_split_test(client: Dock3PayloadControlClient, args) -> int:
    payload_index = _payload_index(args)
    results = []
    for step in build_steps(args):
        rc, reply, event = request_split_step(client, step, payload_index, args.timeout)
        status, detail = evaluate_step(step, payload_index, rc, reply, event)
        results.append((step, status, detail, reply, event))
        print(f"[{status}] {step.label}: {detail}")
        if status != "ok" and args.stop_on_failure:
            break

    failures = [item for item in results if item[1] != "ok"]
    if failures:
        print("camera_screen_split real-gateway failures:", file=sys.stderr)
        for step, status, detail, reply, event in failures:
            focus = {
                "step": step.label,
                "status": status,
                "detail": detail,
                "reply": reply,
                "event": event,
            }
            print(json.dumps(focus, ensure_ascii=False, indent=2), file=sys.stderr)
        return 6

    print(
        "dock3 camera_screen_split real-gateway test ok "
        f"(steps={len(results)}, accepted={client.stats['accepted']}, "
        f"events={client.stats['events']}, ignored_total={client.stale_total()})"
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Test real gateway Dock3 camera_screen_split open, close, and PIP mode switch."
    )
    add_config_argument(parser)
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--sn", default=None)
    parser.add_argument("--timeout", type=float, default=12.0)
    parser.add_argument("--qos", type=int, choices=[0, 1, 2], default=1)
    parser.add_argument("--quiet-stale", action="store_true")
    parser.add_argument("--fail-on-stale", action="store_true")
    parser.add_argument("--stop-on-failure", action="store_true")
    parser.add_argument("--payload-index", default=None, help="Dock3 payload index, for example 52-0-0.")
    parser.add_argument("--open-mode", default="visible_ir", help="PIP mode used for the open step.")
    parser.add_argument("--switch-mode", default="ir_visible", help="Different PIP mode used for the switch step.")
    parser.add_argument("--close-mode", default="visible", help="Non-PIP mode used for close steps.")
    args = parser.parse_args()

    if CLOUD_METHOD != "camera_screen_split":
        raise AssertionError("this script must only publish the existing camera_screen_split method")

    args.host, args.port, args.sn = resolve_mqtt_settings(args)
    client = Dock3PayloadControlClient(
        args.host,
        args.port,
        args.sn,
        args.qos,
        quiet_stale=args.quiet_stale,
        client_id_prefix="edge_gateway_camera_screen_split_real",
        subscribe_events=True,
    )
    if not client.connect(args.timeout):
        print("MQTT connect timeout", file=sys.stderr)
        return 2

    try:
        rc = run_real_screen_split_test(client, args)
        if rc != 0:
            return rc
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
        return 0
    finally:
        client.disconnect()


if __name__ == "__main__":
    raise SystemExit(main())
