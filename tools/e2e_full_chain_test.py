#!/usr/bin/env python3
"""Run a Dock3 P0/P1 full-chain regression from existing MQTT validators."""

import argparse
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List

from tool_config import add_config_argument


TOOLS_DIR = Path(__file__).resolve().parent


@dataclass(frozen=True)
class Step:
    key: str
    title: str
    phase: str
    script: str
    extra_args: tuple[str, ...] = ()
    supports_fail_on_stale: bool = True
    timeout: float | None = None


STEPS: tuple[Step, ...] = (
    Step("properties", "P0 Dock3 properties", "p0", "mqtt_dock3_properties_test.py"),
    Step("device", "P0 device topology", "p0", "mqtt_dock3_device_test.py"),
    Step(
        "osd-state",
        "P0 OSD/state",
        "p0",
        "mqtt_dock3_osd_state_test.py",
        supports_fail_on_stale=False,
    ),
    Step("cmd", "P1 command jobs", "p1", "mqtt_dock3_cmd_test.py"),
    Step("drc", "P1 DRC", "p1", "mqtt_dock3_drc_test.py"),
    Step("wayline", "P1 Wayline", "p1", "mqtt_dock3_wayline_test.py"),
    Step("media", "P1 Media", "p1", "mqtt_dock3_media_test.py"),
    Step("live", "P1 Live", "p1", "mqtt_dock3_live_test.py"),
    Step(
        "hms-log",
        "P1 HMS/log",
        "p1",
        "mqtt_dock3_hms_log_test.py",
        timeout=20.0,
    ),
    Step(
        "firmware-config",
        "P1 Firmware/config",
        "p1",
        "mqtt_dock3_firmware_config_test.py",
        timeout=20.0,
    ),
)


def parse_csv(value: str) -> set[str]:
    return {item.strip() for item in value.split(",") if item.strip()}


def selected_steps(phase: str, only: str, skip: str) -> List[Step]:
    only_keys = parse_csv(only)
    skip_keys = parse_csv(skip)
    known = {step.key for step in STEPS}
    unknown = (only_keys | skip_keys) - known
    if unknown:
        raise ValueError(f"unknown step(s): {', '.join(sorted(unknown))}; known={', '.join(sorted(known))}")

    steps: Iterable[Step] = STEPS
    if phase != "all":
        steps = [step for step in steps if step.phase == phase]
    if only_keys:
        steps = [step for step in steps if step.key in only_keys]
    if skip_keys:
        steps = [step for step in steps if step.key not in skip_keys]
    return list(steps)


def build_command(step: Step, args) -> List[str]:
    command = [
        args.python,
        str(TOOLS_DIR / step.script),
        "--config",
        args.config,
        "--timeout",
        str(step.timeout if step.timeout is not None else args.timeout),
        "--qos",
        str(args.qos),
    ]
    if args.host:
        command.extend(["--host", args.host])
    if args.port is not None:
        command.extend(["--port", str(args.port)])
    if args.sn:
        command.extend(["--sn", args.sn])
    if args.quiet_stale:
        command.append("--quiet-stale")
    if args.fail_on_stale and step.supports_fail_on_stale:
        command.append("--fail-on-stale")
    if step.key == "osd-state":
        command.extend(["--reply-timeout", str(args.reply_timeout)])
        if args.osd_allow_invalid:
            command.append("--allow-invalid")
    command.extend(step.extra_args)
    return command


def run_step(step: Step, command: List[str], process_timeout: float, dry_run: bool) -> int:
    print(f"\n=== {step.title} [{step.key}] ===")
    print("+ " + " ".join(command))
    if dry_run:
        return 0

    started = time.monotonic()
    try:
        completed = subprocess.run(command, cwd=str(TOOLS_DIR.parent), timeout=process_timeout)
    except subprocess.TimeoutExpired:
        elapsed = time.monotonic() - started
        print(f"step timeout after {elapsed:.1f}s: {step.key}", file=sys.stderr)
        return 124

    elapsed = time.monotonic() - started
    print(f"--- {step.key} exit={completed.returncode} elapsed={elapsed:.1f}s ---")
    return completed.returncode


def main() -> int:
    parser = argparse.ArgumentParser(description="Run Dock3 P0/P1 full-chain MQTT regression")
    add_config_argument(parser)
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--sn", default=None)
    parser.add_argument("--timeout", type=float, default=12.0, help="Per MQTT request timeout passed to child scripts.")
    parser.add_argument("--reply-timeout", type=float, default=8.0, help="Reply timeout passed to OSD/state script.")
    parser.add_argument("--process-timeout", type=float, default=300.0, help="Wall-clock timeout for each child script.")
    parser.add_argument("--qos", type=int, choices=[0, 1, 2], default=1)
    parser.add_argument("--phase", choices=["all", "p0", "p1"], default="all")
    parser.add_argument("--only", default="", help="Comma-separated step keys to run.")
    parser.add_argument("--skip", default="", help="Comma-separated step keys to skip.")
    parser.add_argument("--continue-on-fail", action="store_true")
    parser.add_argument("--quiet-stale", action="store_true")
    parser.add_argument("--fail-on-stale", action="store_true")
    parser.add_argument("--osd-allow-invalid", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--python", default=sys.executable)
    args = parser.parse_args()

    try:
        steps = selected_steps(args.phase, args.only, args.skip)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    if not steps:
        print("no e2e steps selected", file=sys.stderr)
        return 2

    print(
        "dock3 e2e selected steps: "
        + ", ".join(f"{step.key}({step.phase})" for step in steps)
    )

    failures: list[tuple[str, int]] = []
    started = time.monotonic()
    for step in steps:
        command = build_command(step, args)
        rc = run_step(step, command, args.process_timeout, args.dry_run)
        if rc != 0:
            failures.append((step.key, rc))
            if not args.continue_on_fail:
                break

    elapsed = time.monotonic() - started
    if failures:
        print(
            "dock3 e2e failed: "
            + ", ".join(f"{key}=exit{rc}" for key, rc in failures)
            + f"; elapsed={elapsed:.1f}s",
            file=sys.stderr,
        )
        return failures[0][1] or 1

    print(f"dock3 e2e full-chain test ok (steps={len(steps)}, elapsed={elapsed:.1f}s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
