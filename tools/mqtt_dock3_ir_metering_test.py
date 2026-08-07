#!/usr/bin/env python3
"""Validate Dock3 IR metering payload-control methods over MQTT services.

Only the existing cloud API service methods are published:
- ir_metering_mode_set
- ir_metering_point_set
- ir_metering_area_set
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

from mqtt_test_client import MqttRequestClient, result_code  # noqa: E402
from src.payload.dock3_payload import payload_index_from_config  # noqa: E402
from tool_config import add_config_argument, load_config, resolve_mqtt_settings  # noqa: E402


IR_METHODS = {
    "ir_metering_mode_set",
    "ir_metering_point_set",
    "ir_metering_area_set",
}


@dataclass(frozen=True)
class IrMeteringCase:
    label: str
    method: str
    data: dict[str, Any]
    expect_success: bool
    expect_protocol: bool = False
    expect_validation_error: bool = False


class Dock3IrMeteringClient(MqttRequestClient):
    def service(self, method: str, data: dict[str, Any], timeout: float):
        if method not in IR_METHODS:
            raise ValueError(f"script may only publish existing IR metering methods, got: {method}")
        return super().request(
            method,
            data,
            timeout,
            channel="services",
            allowed_results=None,
            expected_status=None,
        )


def _payload_index(args) -> str:
    if args.payload_index:
        return args.payload_index
    config = load_config(args.config)
    return payload_index_from_config(config)


def _frame_size(args) -> dict[str, int]:
    return {"width": args.frame_width, "height": args.frame_height}


def build_cases(args) -> list[IrMeteringCase]:
    payload_index = _payload_index(args)
    frame_size = _frame_size(args)
    valid_base = {
        "payload_index": payload_index,
        "camera_type": args.camera_type,
        "frame_size": frame_size,
    }

    cases = [
        IrMeteringCase(
            "mode once/full-frame temperature output",
            "ir_metering_mode_set",
            {**valid_base, "mode": args.once_mode},
            expect_success=True,
            expect_protocol=True,
        ),
        IrMeteringCase(
            "mode periodic temperature output",
            "ir_metering_mode_set",
            {**valid_base, "mode": args.periodic_mode, "interval_frames": args.interval_frames},
            expect_success=True,
            expect_protocol=True,
        ),
        IrMeteringCase(
            "mode stop temperature output",
            "ir_metering_mode_set",
            {**valid_base, "mode": args.stop_mode},
            expect_success=True,
            expect_protocol=True,
        ),
        IrMeteringCase(
            "point metering center coordinate",
            "ir_metering_point_set",
            {**valid_base, "x": args.point_x, "y": args.point_y},
            expect_success=True,
            expect_protocol=True,
        ),
        IrMeteringCase(
            "area metering normalized rectangle",
            "ir_metering_area_set",
            {
                **valid_base,
                "x": args.area_x,
                "y": args.area_y,
                "width": args.area_width,
                "height": args.area_height,
            },
            expect_success=True,
            expect_protocol=True,
        ),
    ]

    if not args.skip_invalid:
        cases.extend(
            [
                IrMeteringCase(
                    "invalid point coordinate should be rejected",
                    "ir_metering_point_set",
                    {**valid_base, "x": args.invalid_x, "y": args.invalid_y},
                    expect_success=False,
                    expect_validation_error=True,
                ),
                IrMeteringCase(
                    "invalid area outside frame should be rejected",
                    "ir_metering_area_set",
                    {
                        **valid_base,
                        "x": args.invalid_area_x,
                        "y": args.invalid_area_y,
                        "width": args.invalid_area_width,
                        "height": args.invalid_area_height,
                    },
                    expect_success=False,
                    expect_validation_error=True,
                ),
            ]
        )

    if args.extra_payload_index:
        other_base = {**valid_base, "payload_index": args.extra_payload_index}
        cases.append(
            IrMeteringCase(
                "multi-payload payload_index isolation",
                "ir_metering_point_set",
                {**other_base, "x": args.point_x, "y": args.point_y},
                expect_success=args.extra_payload_should_succeed,
                expect_protocol=True,
            )
        )

    return cases


def _data(reply: dict | None) -> dict[str, Any]:
    data = reply.get("data", {}) if isinstance(reply, dict) else {}
    return data if isinstance(data, dict) else {}


def _viewlink_result(reply: dict | None) -> dict[str, Any]:
    result = _data(reply).get("viewlink_result", {})
    return result if isinstance(result, dict) else {}


def _has_protocol_frames(reply: dict | None) -> bool:
    frames = _viewlink_result(reply).get("frame_hex")
    return isinstance(frames, list) and all(isinstance(item, str) and item for item in frames)


def _is_success(reply: dict | None) -> bool:
    return result_code(reply) == 0


def evaluate(case: IrMeteringCase, rc: int, reply: dict | None, *, strict_validation: bool) -> tuple[str, str]:
    if rc != 0:
        return "fail", f"mqtt client rc={rc}"

    code = result_code(reply)
    data = _data(reply)
    viewlink = _viewlink_result(reply)

    if case.expect_validation_error:
        if code == 0:
            detail = "invalid input was accepted with result=0"
            return ("fail" if strict_validation else "validation_gap"), detail
        return "ok", f"invalid input rejected or adapter failed, result={code}"

    if case.expect_success and code != 0:
        message = viewlink.get("message") or data.get("reason") or ""
        return "fail", f"expected result=0, got result={code}, message={message}"

    if case.expect_protocol and code == 0 and not _has_protocol_frames(reply):
        return "fail", "result=0 but data.viewlink_result.frame_hex is missing"

    if case.method == "ir_metering_point_set" and code == 0:
        point = data.get("protocol_point")
        if not isinstance(point, dict) or not {"x", "y"}.issubset(point):
            return "fail", "result=0 but protocol_point is missing"

    if case.method == "ir_metering_area_set" and code == 0:
        area = data.get("protocol_area")
        if not isinstance(area, dict) or not {"left", "top", "right", "bottom"}.issubset(area):
            return "fail", "result=0 but protocol_area is missing"

    if case.method == "ir_metering_mode_set" and code == 0 and case.data.get("interval_frames") is not None:
        if data.get("interval_frames") != case.data["interval_frames"]:
            return "fail", f"interval_frames mismatch: {data.get('interval_frames')}"

    return "ok", f"result={code}"


def print_reply(case: IrMeteringCase, reply: dict | None, *, verbose: bool) -> None:
    if not verbose:
        return
    data = _data(reply)
    focus = {
        "method": case.method,
        "result": result_code(reply),
        "payload_index": case.data.get("payload_index"),
        "mode": data.get("mode"),
        "protocol_point": data.get("protocol_point"),
        "protocol_area": data.get("protocol_area"),
        "frame_hex": _viewlink_result(reply).get("frame_hex"),
    }
    print(json.dumps(focus, ensure_ascii=False, indent=2))


def run_ir_metering_checks(client: Dock3IrMeteringClient, args) -> int:
    results: list[tuple[IrMeteringCase, int, dict | None, str, str]] = []

    for case in build_cases(args):
        print(f"running {case.label}: {case.method}")
        rc, reply = client.service(case.method, case.data, args.timeout)
        status, detail = evaluate(case, rc, reply, strict_validation=args.strict_validation)
        results.append((case, rc, reply, status, detail))
        print(f"[{status}] {case.label}: {detail}")
        print_reply(case, reply, verbose=args.verbose_reply)
        if status == "fail" and args.stop_on_failure:
            break

    counts: dict[str, int] = {}
    for _, _, _, status, _ in results:
        counts[status] = counts.get(status, 0) + 1

    print(
        "dock3 ir-metering summary "
        f"(ok={counts.get('ok', 0)}, validation_gap={counts.get('validation_gap', 0)}, "
        f"fail={counts.get('fail', 0)}, total={len(results)})"
    )

    failed = [(case, detail) for case, _, _, status, detail in results if status == "fail"]
    if failed:
        print("ir-metering failures:", file=sys.stderr)
        for case, detail in failed:
            print(f"- {case.label} / {case.method}: {detail}", file=sys.stderr)
        return 6

    gaps = [(case, detail) for case, _, _, status, detail in results if status == "validation_gap"]
    if gaps:
        print("ir-metering validation gaps:", file=sys.stderr)
        for case, detail in gaps:
            print(f"- {case.label} / {case.method}: {detail}", file=sys.stderr)
        return 7 if args.fail_on_validation_gap else 0

    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Test Dock3 IR metering service methods")
    add_config_argument(parser)
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--sn", default=None)
    parser.add_argument("--timeout", type=float, default=12.0)
    parser.add_argument("--qos", type=int, choices=[0, 1, 2], default=1)
    parser.add_argument("--quiet-stale", action="store_true")
    parser.add_argument("--fail-on-stale", action="store_true")
    parser.add_argument("--payload-index", default=None, help="Dock3 payload index, for example 52-0-0.")
    parser.add_argument("--extra-payload-index", default="", help="Optional second payload_index used for isolation check.")
    parser.add_argument("--extra-payload-should-succeed", action="store_true")
    parser.add_argument("--camera-type", default="ir")
    parser.add_argument("--frame-width", type=int, default=1280)
    parser.add_argument("--frame-height", type=int, default=720)
    parser.add_argument("--once-mode", default="once")
    parser.add_argument("--periodic-mode", default="periodic")
    parser.add_argument("--stop-mode", default="stop")
    parser.add_argument("--interval-frames", type=int, default=4)
    parser.add_argument("--point-x", type=float, default=0.5)
    parser.add_argument("--point-y", type=float, default=0.5)
    parser.add_argument("--area-x", type=float, default=0.25)
    parser.add_argument("--area-y", type=float, default=0.25)
    parser.add_argument("--area-width", type=float, default=0.5)
    parser.add_argument("--area-height", type=float, default=0.5)
    parser.add_argument("--invalid-x", type=float, default=1.2)
    parser.add_argument("--invalid-y", type=float, default=-0.1)
    parser.add_argument("--invalid-area-x", type=float, default=0.8)
    parser.add_argument("--invalid-area-y", type=float, default=0.8)
    parser.add_argument("--invalid-area-width", type=float, default=0.5)
    parser.add_argument("--invalid-area-height", type=float, default=0.5)
    parser.add_argument("--skip-invalid", action="store_true")
    parser.add_argument("--strict-validation", action="store_true", help="Fail when invalid coordinates return result=0.")
    parser.add_argument("--fail-on-validation-gap", action="store_true")
    parser.add_argument("--stop-on-failure", action="store_true")
    parser.add_argument("--verbose-reply", action="store_true")
    args = parser.parse_args()
    args.host, args.port, args.sn = resolve_mqtt_settings(args)

    client = Dock3IrMeteringClient(
        args.host,
        args.port,
        args.sn,
        args.qos,
        quiet_stale=args.quiet_stale,
        client_id_prefix="edge_gateway_ir_metering",
        subscribe_events=True,
    )
    if not client.connect(args.timeout):
        print("MQTT connect timeout", file=sys.stderr)
        return 2

    try:
        rc = run_ir_metering_checks(client, args)
        if rc != 0:
            return rc

        stale_total = client.stale_total()
        if args.fail_on_stale and stale_total:
            print(f"stale replies observed: {client.stats}", file=sys.stderr)
            return 10

        print(
            "dock3 ir-metering test ok "
            f"(accepted={client.stats['accepted']}, events={client.stats['events']}, "
            f"ignored_total={stale_total})"
        )
        return 0
    finally:
        client.disconnect()


if __name__ == "__main__":
    raise SystemExit(main())
