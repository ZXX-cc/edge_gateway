#!/usr/bin/env python3
"""One-command DJI Dock3 organization management acceptance test.

This script orchestrates the existing project MQTT cloud simulator and
organization request client to verify the Dock3 organization-management flow:

1. airport_organization_get success.
2. airport_organization_bind success.
3. device_binding and update_topo events are generated.
4. update_topo is published to sys/product/{sn}/status and acknowledged.
5. airport_organization_get failure is propagated.
6. airport_organization_bind partial failure is propagated through err_infos[].
7. request timeout is detected when the cloud reply is delayed.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

DEFAULT_SUB_DEVICE = (
    '{"sn":"DRONE-SIM-ORG-001","domain":"0","type":60,"sub_type":0,'
    '"index":"A","device_secret":"sim-secret","nonce":"sim-nonce",'
    '"thing_version":"1.1.2","online":true,"paired":true}'
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Dock3 organization management acceptance test"
    )
    parser.add_argument("--config", default="config/gateway.yaml")
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--sn", default=None)
    parser.add_argument("--timeout", type=float, default=20.0)
    parser.add_argument("--verify-timeout", type=float, default=8.0)
    parser.add_argument("--organization-id", default="org_sim_001")
    parser.add_argument("--organization-name", default="模拟组织")
    parser.add_argument("--binding-code", default="SIM-BIND-CODE")
    parser.add_argument("--device-callsign", default="EDGE-GW-001")
    parser.add_argument("--device-model-key", default="dock3-sim")
    parser.add_argument("--gateway-device-secret", default="sim-gateway-secret")
    parser.add_argument("--gateway-nonce", default="sim-gateway-nonce")
    parser.add_argument(
        "--sub-device-json",
        default=DEFAULT_SUB_DEVICE,
        help="JSON object for a simulated sub-device used by the topology branch.",
    )
    parser.add_argument(
        "--skip-negative",
        action="store_true",
        help="Only run the successful organization bind/topology flow.",
    )
    parser.add_argument(
        "--verify-db",
        action="store_true",
        help="Also verify SQLite device_binding persistence after bind.",
    )
    return parser.parse_args()


def resolve_gateway_sn(config_path: str) -> str:
    try:
        from src.utils.config import Config, load_config
    except ImportError as exc:
        raise SystemExit(
            "Project dependencies are missing. Activate .venv and install requirements first."
        ) from exc

    load_config(config_path)
    return Config.get_gateway_sn()


def resolve_sub_device_sn(raw_json: str) -> str:
    try:
        data = json.loads(raw_json)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"invalid --sub-device-json: {exc}") from exc
    if not isinstance(data, dict) or not data.get("sn"):
        raise SystemExit("--sub-device-json must be a JSON object with sn")
    return str(data["sn"])


def _common_setting_args(args: argparse.Namespace) -> list[str]:
    result = ["--config", args.config]
    if args.host:
        result.extend(["--host", args.host])
    if args.port is not None:
        result.extend(["--port", str(args.port)])
    return result


def _cloud_setting_args(args: argparse.Namespace) -> list[str]:
    result = _common_setting_args(args)
    if args.sn:
        result.extend(["--sn", args.sn])
    return result


def _run(cmd: list[str], timeout: float) -> subprocess.CompletedProcess[str]:
    print("+ " + " ".join(cmd))
    return subprocess.run(
        cmd,
        cwd=PROJECT_ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=timeout,
        check=False,
    )


def _start_cloud(args: argparse.Namespace, extra: list[str]) -> subprocess.Popen[str]:
    cmd = [
        sys.executable,
        "tools/mqtt_cloud_simulator.py",
        *_cloud_setting_args(args),
        "--timeout",
        str(args.timeout),
        *extra,
    ]
    print("+ " + " ".join(cmd))
    proc = subprocess.Popen(
        cmd,
        cwd=PROJECT_ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    time.sleep(1.0)
    return proc


def _finish_cloud(proc: subprocess.Popen[str], timeout: float) -> tuple[int, str]:
    try:
        output, _ = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.terminate()
        try:
            output, _ = proc.communicate(timeout=3.0)
        except subprocess.TimeoutExpired:
            proc.kill()
            output, _ = proc.communicate(timeout=3.0)
        return 124, output
    return proc.returncode or 0, output


def _print_output(title: str, output: str) -> None:
    print(f"\n===== {title} =====")
    print(output.rstrip() or "(no output)")


def run_case(
    name: str,
    args: argparse.Namespace,
    cloud_args: list[str],
    org_args: list[str],
    cloud_timeout: float | None = None,
) -> int:
    cloud = _start_cloud(args, cloud_args)
    org_cmd = [
        sys.executable,
        "tools/mqtt_organization_test.py",
        *_common_setting_args(args),
        "--timeout",
        str(args.timeout),
        "--verify-timeout",
        str(args.verify_timeout),
        *org_args,
    ]

    try:
        org = _run(org_cmd, timeout=args.timeout + args.verify_timeout + 10.0)
    except subprocess.TimeoutExpired as exc:
        cloud_rc, cloud_output = _finish_cloud(cloud, timeout=2.0)
        _print_output(f"{name} / organization client", exc.stdout or "")
        _print_output(f"{name} / cloud simulator", cloud_output)
        print(f"{name} failed: organization client timeout; cloud_rc={cloud_rc}")
        return 1

    cloud_rc, cloud_output = _finish_cloud(
        cloud,
        timeout=cloud_timeout if cloud_timeout is not None else args.timeout + 5.0,
    )
    _print_output(f"{name} / organization client", org.stdout)
    _print_output(f"{name} / cloud simulator", cloud_output)

    if org.returncode != 0:
        print(f"{name} failed: organization client rc={org.returncode}")
        return org.returncode
    if cloud_rc != 0:
        print(f"{name} failed: cloud simulator rc={cloud_rc}")
        return cloud_rc
    print(f"{name} ok")
    return 0


def main() -> int:
    args = parse_args()
    gateway_sn = resolve_gateway_sn(args.config)
    sub_device_sn = resolve_sub_device_sn(args.sub_device_json)
    cases: list[tuple[str, list[str], list[str], float | None]] = []

    cases.append(
        (
            "organization bind status",
            [
                "--once",
                "--bind-status",
                "unbound",
                "--organization-id",
                args.organization_id,
                "--organization-name",
                args.organization_name,
            ],
            [
                "--action",
                "status",
                "--sub-device-json",
                args.sub_device_json,
                "--expect-bind-status-sn",
                gateway_sn,
                "--expect-bind-status-sn",
                sub_device_sn,
            ],
            None,
        )
    )

    success_org_args = [
        "--action",
        "full",
        "--organization-id",
        args.organization_id,
        "--binding-code",
        args.binding_code,
        "--device-callsign",
        args.device_callsign,
        "--device-model-key",
        args.device_model_key,
        "--gateway-device-secret",
        args.gateway_device_secret,
        "--gateway-nonce",
        args.gateway_nonce,
        "--expect-organization-name",
        args.organization_name,
        "--expect-event",
        "device_binding",
        "--expect-event",
        "update_topo",
        "--expect-device-binding-sn",
        gateway_sn,
        "--expect-device-binding-sn",
        sub_device_sn,
        "--expect-device-binding-count",
        "2",
        "--expect-bound",
        "--expect-bound-sn",
        sub_device_sn,
        "--expect-sub-device-paired-sn",
        sub_device_sn,
        "--sub-device-json",
        args.sub_device_json,
        "--bind-sub-devices",
        "--expect-topo-sub-device-count",
        "1",
        "--expect-topo-sub-device-sn",
        sub_device_sn,
        "--validate-topo-schema",
        "--publish-events",
        "--expect-status-reply",
    ]
    if args.verify_db:
        success_org_args.append("--verify-db")

    cases.append(
        (
            "organization full bind/topology",
            [
                "--enable-status",
                "--max-requests",
                "3",
                "--organization-id",
                args.organization_id,
                "--organization-name",
                args.organization_name,
                "--device-callsign",
                args.device_callsign,
                "--expect-status-sub-device-count",
                "1",
                "--expect-status-sub-device-sn",
                sub_device_sn,
                "--validate-status-topo-schema",
            ],
            success_org_args,
            None,
        )
    )

    if not args.skip_negative:
        cases.extend(
            [
                (
                    "organization get failure",
                    [
                        "--once",
                        "--organization-get-result",
                        "327000",
                        "--organization-get-message",
                        "绑定码无效",
                    ],
                    [
                        "--action",
                        "get",
                        "--organization-id",
                        args.organization_id,
                        "--binding-code",
                        args.binding_code,
                        "--expect-get-result",
                        "327000",
                    ],
                    None,
                ),
                (
                    "organization bind partial failure",
                    [
                        "--once",
                        "--organization-bind-fail-sn",
                        gateway_sn,
                        "--organization-bind-err-code",
                        "327000",
                    ],
                    [
                        "--action",
                        "bind",
                        "--organization-id",
                        args.organization_id,
                        "--binding-code",
                        args.binding_code,
                        "--device-callsign",
                        args.device_callsign,
                        "--expect-bind-error-count",
                        "1",
                    ],
                    None,
                ),
                (
                    "organization request timeout",
                    [
                        "--once",
                        "--reply-delay",
                        str(args.timeout + 2.0),
                    ],
                    [
                        "--action",
                        "get",
                        "--organization-id",
                        args.organization_id,
                        "--binding-code",
                        args.binding_code,
                        "--expect-timeout",
                        "--timeout",
                        "2",
                    ],
                    args.timeout + 5.0,
                ),
            ]
        )

    for name, cloud_args, org_args, cloud_timeout in cases:
        rc = run_case(name, args, cloud_args, org_args, cloud_timeout)
        if rc != 0:
            return rc

    print("dock3 organization test ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
