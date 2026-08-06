#!/usr/bin/env python3
"""One-command local test for the ViewLink gimbal chain.

Checks:
- MQTT request/reply path to the gateway
- payload_status_query runtime gimbal config
- ViewLink SDK initialize/connect/is_connected
- visible gimbal motion: home, yaw, pitch, turn_to, zoom
- optional zoom/photo command
- optional HTTP media directory listing
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "tools"))

from mqtt_test_client import MqttRequestClient
from mqtt_viewlink_visible_test import _run_visible_motion
from mqtt_viewlink_zoom_photo_test import run_zoom_photo
from tool_config import add_config_argument, resolve_mqtt_settings
from viewlink_media_download import discover_page_links


DEFAULT_GIMBAL_TEST_CONFIG = "config/gateway.gimbal-test.yaml"


@dataclass
class StepResult:
    name: str
    ok: bool
    detail: str = ""


def _print_step(result: StepResult) -> None:
    status = "PASS" if result.ok else "FAIL"
    suffix = f" - {result.detail}" if result.detail else ""
    print(f"[{status}] {result.name}{suffix}")


def _wait_for_gateway(
    host: str,
    port: int,
    sn: str,
    timeout: float,
    quiet_stale: bool,
) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        client = MqttRequestClient(
            host,
            port,
            sn,
            quiet_stale=quiet_stale,
            client_id_prefix="edge_gateway_gimbal_wait",
        )
        if client.connect(2.0):
            try:
                rc, _ = client.request(
                    "gateway_state_query",
                    {"compact": True},
                    2.0,
                    channel="services",
                    allowed_results={0},
                )
                if rc == 0:
                    return True
            finally:
                client.disconnect()
        time.sleep(1.0)
    return False


def _start_gateway(args) -> subprocess.Popen:
    command = [
        sys.executable,
        str(PROJECT_ROOT / "src" / "main.py"),
        "-c",
        str((PROJECT_ROOT / args.config).resolve() if not Path(args.config).is_absolute() else Path(args.config)),
    ]
    print(f"starting gateway: {' '.join(command)}")
    return subprocess.Popen(
        command,
        cwd=str(PROJECT_ROOT),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _stop_gateway(process: subprocess.Popen | None) -> None:
    if not process:
        return
    if process.poll() is not None:
        return
    print(f"stopping gateway pid={process.pid}")
    process.terminate()
    try:
        process.wait(timeout=8.0)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5.0)


def _run_mqtt_steps(args) -> list[StepResult]:
    results: list[StepResult] = []
    client = MqttRequestClient(
        args.host,
        args.port,
        args.sn,
        qos=args.qos,
        quiet_stale=args.quiet_stale,
        client_id_prefix="edge_gateway_gimbal_chain",
    )
    if not client.connect(args.timeout):
        return [StepResult("MQTT connect", False, f"{args.host}:{args.port}")]

    try:
        results.append(StepResult("MQTT connect", True, f"{args.host}:{args.port}"))

        visible_rc = _run_visible_motion(client, args, time.sleep)
        results.append(
            StepResult(
                "ViewLink visible motion",
                visible_rc == 0,
                f"rc={visible_rc}",
            )
        )
        if visible_rc != 0:
            return results

        if args.skip_photo:
            results.append(StepResult("ViewLink zoom/photo", True, "skipped"))
        else:
            photo_rc = run_zoom_photo(client, args, sleep_fn=time.sleep)
            results.append(
                StepResult(
                    "ViewLink zoom/photo",
                    photo_rc == 0,
                    f"rc={photo_rc}",
                )
            )
    finally:
        client.disconnect()

    return results


def _run_media_check(args) -> StepResult:
    if args.skip_media:
        return StepResult("HTTP media directory", True, "skipped")
    try:
        links = discover_page_links(args.media_url, args.media_timeout)
    except Exception as exc:
        ok = not args.require_media
        status = "warning" if ok else "required"
        return StepResult(
            "HTTP media directory",
            ok,
            f"{status}: {args.media_url}: {exc}",
        )
    return StepResult("HTTP media directory", True, f"{len(links)} link(s)")


def _positive_float(value: str) -> float:
    parsed = float(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be positive")
    return parsed


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run a local ViewLink gimbal chain test")
    add_config_argument(parser)
    parser.set_defaults(config=DEFAULT_GIMBAL_TEST_CONFIG)
    parser.add_argument("--host", default=None, help="MQTT host; defaults to config nanomq.host")
    parser.add_argument("--port", type=int, default=None, help="MQTT port; defaults to config nanomq.port")
    parser.add_argument("--sn", default=None, help="Gateway SN; defaults to config gateway.sn")
    parser.add_argument("--qos", type=int, choices=[0, 1, 2], default=1)
    parser.add_argument("--timeout", type=_positive_float, default=10.0)
    parser.add_argument("--quiet-stale", action="store_true")
    parser.add_argument("--start-gateway", action="store_true", help="start src/main.py before testing")
    parser.add_argument("--gateway-wait", type=_positive_float, default=25.0)

    parser.add_argument("--sdk-dir", default="", help="override ViewLink SDK directory")
    parser.add_argument("--gimbal-ip", default="", help="override gimbal IP")
    parser.add_argument("--gimbal-port", type=int, default=0, help="override gimbal control port")
    parser.add_argument("--yaw-speed", type=int, default=500)
    parser.add_argument("--pitch-speed", type=int, default=500)
    parser.add_argument("--move-duration", type=float, default=0.8)
    parser.add_argument("--zoom-speed", type=int, default=2)
    parser.add_argument("--zoom-duration", type=float, default=0.8)
    parser.add_argument("--turn-yaw", type=float, default=0.0)
    parser.add_argument("--turn-pitch", type=float, default=0.0)
    parser.add_argument("--settle-seconds", type=float, default=1.0)
    parser.add_argument("--pause-between", type=float, default=0.4)

    parser.add_argument("--skip-photo", action="store_true")
    parser.add_argument("--photo-wait", type=float, default=1.0)
    parser.add_argument("--restore-zoom", action="store_true")
    parser.add_argument("--zoom-direction", choices=["in", "out"], default="in")
    parser.add_argument("--video-url", default="")
    parser.add_argument(
        "--snapshot-output",
        default=str(PROJECT_ROOT / "logs" / "viewlink_capture" / "gimbal_chain_snapshot.jpg"),
    )
    parser.add_argument("--ffmpeg", default="ffmpeg")
    parser.add_argument("--preview-timeout", type=float, default=20.0)

    parser.add_argument("--skip-media", action="store_true")
    parser.add_argument("--require-media", action="store_true")
    parser.add_argument("--media-url", default="http://192.168.2.119:8554/download/")
    parser.add_argument("--media-timeout", type=_positive_float, default=8.0)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    args.host, args.port, args.sn = resolve_mqtt_settings(args)

    gateway_process: subprocess.Popen | None = None
    results: list[StepResult] = []

    try:
        if args.start_gateway:
            gateway_process = _start_gateway(args)
            ready = _wait_for_gateway(
                args.host,
                args.port,
                args.sn,
                args.gateway_wait,
                args.quiet_stale,
            )
            results.append(
                StepResult(
                    "Gateway startup",
                    ready,
                    f"pid={gateway_process.pid}" if ready else f"timeout={args.gateway_wait}s",
                )
            )
            if not ready:
                for result in results:
                    _print_step(result)
                return 2

        results.extend(_run_mqtt_steps(args))
        results.append(_run_media_check(args))
    finally:
        _stop_gateway(gateway_process)

    print("\nsummary")
    for result in results:
        _print_step(result)

    failed = [result for result in results if not result.ok]
    if failed:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
