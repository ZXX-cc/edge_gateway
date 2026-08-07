#!/usr/bin/env python3
"""Simulate an active photo session and stop it with official camera_photo_stop.

This is a local controller-level retest.  It does not publish any custom MQTT
method.  The only command message used by the simulated cloud side is the
official Dock3 Cloud API method camera_photo_stop.
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from src.payload.camera import CameraController
from src.utils.dji_error_codes import DOCK_SYSTEM_ERROR


OFFICIAL_METHOD = "camera_photo_stop"


def build_message(payload_index: str) -> dict[str, Any]:
    return {
        "tid": "simulate-photo-session-stop",
        "bid": "simulate-photo-session-stop",
        "timestamp": int(time.time() * 1000),
        "gateway": "",
        "method": OFFICIAL_METHOD,
        "data": {
            "payload_index": payload_index,
        },
    }


def attach_simulated_photo_session(camera: CameraController, *, stuck: bool) -> threading.Thread:
    camera._photo_session_stop.clear()

    def run_stoppable_session() -> None:
        while not camera._photo_session_stop.is_set():
            time.sleep(0.05)

    def run_stuck_session() -> None:
        deadline = time.time() + 5.0
        while time.time() < deadline:
            time.sleep(0.05)

    thread = threading.Thread(
        target=run_stuck_session if stuck else run_stoppable_session,
        name="simulated-photo-session-stuck" if stuck else "simulated-photo-session",
        daemon=True,
    )
    camera._photo_session_thread = thread
    thread.start()
    return thread


def validate_reply(reply: dict[str, Any], *, stuck: bool) -> int:
    data = reply.get("data") if isinstance(reply, dict) else {}
    data = data if isinstance(data, dict) else {}
    result = data.get("result")
    running = data.get("running")
    stopped = data.get("stopped")

    if stuck:
        if result == 0:
            print("failed: stuck photo session returned success", file=sys.stderr)
            print(json.dumps(reply, ensure_ascii=False, indent=2), file=sys.stderr)
            return 4
        if result != DOCK_SYSTEM_ERROR or running is not True or stopped is not False:
            print("failed: stuck photo session did not report official failure state", file=sys.stderr)
            print(json.dumps(reply, ensure_ascii=False, indent=2), file=sys.stderr)
            return 5
        return 0

    if result != 0 or running is not False or stopped is not True:
        print("failed: stoppable photo session was not stopped cleanly", file=sys.stderr)
        print(json.dumps(reply, ensure_ascii=False, indent=2), file=sys.stderr)
        return 6
    return 0


def run_check(args: argparse.Namespace) -> int:
    camera = CameraController()
    thread = attach_simulated_photo_session(camera, stuck=args.stuck)
    time.sleep(0.1)

    reply = camera.handle_camera_photo_stop(build_message(args.payload_index))
    thread_alive_after = thread.is_alive()

    print(f"{OFFICIAL_METHOD} simulated reply:")
    print(json.dumps(reply, ensure_ascii=False, indent=2))
    print(f"simulated_thread_alive_after_stop={thread_alive_after}")

    rc = validate_reply(reply, stuck=args.stuck)
    if rc != 0:
        return rc

    if not args.stuck and thread_alive_after:
        print("failed: simulated stoppable thread is still alive", file=sys.stderr)
        return 7

    print(
        "simulated photo-session stop test ok "
        f"(payload_index={args.payload_index}, stuck={args.stuck})"
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Simulate an active photo session and stop it with official camera_photo_stop."
    )
    parser.add_argument("--payload-index", default="52-0-0")
    parser.add_argument(
        "--stuck",
        action="store_true",
        help="Simulate the regression case: photo session thread ignores stop and stays alive.",
    )
    return run_check(parser.parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
