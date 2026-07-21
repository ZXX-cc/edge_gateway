#!/usr/bin/env python3
"""Real-device oriented DRC control smoke test.

The existing Dock3 DRC category test covers many protocol branches, including
point flight.  This tool is intentionally narrower and safer for bench or
field bring-up: by default it only enters DRC, validates state, sends heartbeat
and zero-motion control frames, then exits DRC.
"""

import argparse
import sys
import time
from pathlib import Path

from mqtt_test_client import MqttRequestClient
from tool_config import add_config_argument, resolve_mqtt_settings

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from src.utils.dji_error_codes import (  # noqa: E402
    DEVICE_STATE_UNSUPPORTED,
    DOCK_COMMUNICATION_ERROR,
    DOCK_SYSTEM_ERROR,
)


def official_enter_payload(args):
    return {
        "mqtt_broker": {
            "address": args.drc_broker_address,
            "client_id": f"drc-real-{args.sn}",
            "username": args.drc_username,
            "password": args.drc_password,
            "expire_time": args.drc_expire_time,
            "enable_tls": args.drc_enable_tls,
        },
        "osd_frequency": args.osd_frequency,
        "hsi_frequency": args.hsi_frequency,
    }


def clamp_motion_args(args) -> None:
    limits = {
        "vx": 0.5,
        "vy": 0.5,
        "h": 0.3,
        "w": 10.0,
    }
    for name, limit in limits.items():
        value = float(getattr(args, name))
        if abs(value) > limit:
            raise SystemExit(
                f"--{name}={value} is above the real-device smoke-test limit "
                f"+/-{limit}. Use a smaller value for this script."
            )
    if args.motion_duration <= 0:
        raise SystemExit("--motion-duration must be > 0")
    if args.motion_duration > 5.0:
        raise SystemExit("--motion-duration is limited to 5 seconds for this real-device smoke test")
    if args.command_interval < 0.1:
        raise SystemExit("--command-interval must be >= 0.1 seconds")


def require_drc_state(client: MqttRequestClient, args, expected: int) -> int:
    rc, reply = client.request(
        "property_get",
        {"properties": ["drc_state"]},
        args.timeout,
        channel="services",
        allowed_results={0},
    )
    if rc != 0:
        return rc
    actual = reply.get("data", {}).get("drc_state") if isinstance(reply, dict) else None
    if actual != expected:
        print(f"expected drc_state={expected}, actual={actual}", file=sys.stderr)
        return 5
    return 0


def send_heartbeat(client: MqttRequestClient, args, seq: int) -> int:
    rc, _ = client.request(
        "heart_beat",
        {"seq": seq, "timestamp": int(time.time() * 1000)},
        args.timeout,
        channel="drc",
        allowed_results={0},
    )
    return rc


def send_zero_controls(client: MqttRequestClient, args, seq: int) -> int:
    rc, _ = client.request(
        "stick_control",
        {"roll": 1024, "pitch": 1024, "throttle": 1024, "yaw": 1024},
        args.no_reply_window,
        channel="drc",
        need_reply=0,
    )
    if rc != 0:
        return rc

    rc, _ = client.request(
        "drone_control",
        {"seq": seq, "x": 0.0, "y": 0.0, "h": 0.0, "w": 0.0},
        args.timeout,
        channel="drc",
        allowed_results={0},
        expected_status="ok",
    )
    if rc != 0:
        return rc

    rc, _ = client.request(
        "drc_gimbal_speed_push",
        {"pitch_speed": 0.0, "yaw_speed": 0.0, "roll_speed": 0.0},
        args.timeout,
        channel="drc",
        allowed_results={0},
    )
    return rc


def send_motion_pulse(client: MqttRequestClient, args, start_seq: int) -> int:
    clamp_motion_args(args)
    print(
        "motion enabled: "
        f"x={args.vx:.2f}m/s y={args.vy:.2f}m/s h={args.h:.2f}m/s "
        f"w={args.w:.1f}deg/s duration={args.motion_duration:.1f}s"
    )
    deadline = time.time() + args.motion_duration
    seq = start_seq
    while time.time() < deadline:
        rc, _ = client.request(
            "drone_control",
            {"seq": seq, "x": args.vx, "y": args.vy, "h": args.h, "w": args.w},
            args.timeout,
            channel="drc",
            allowed_results={0},
            expected_status="ok",
        )
        if rc != 0:
            return rc
        seq += 1
        time.sleep(args.command_interval)

    rc, _ = client.request(
        "drone_control",
        {"seq": seq, "x": 0.0, "y": 0.0, "h": 0.0, "w": 0.0},
        args.timeout,
        channel="drc",
        allowed_results={0},
        expected_status="ok",
    )
    return rc


def preflight_delay(args) -> None:
    if args.preflight_delay <= 0:
        return
    print(
        "real DRC test will start after "
        f"{args.preflight_delay:.1f}s. Press Ctrl+C to abort."
    )
    time.sleep(args.preflight_delay)


def main():
    parser = argparse.ArgumentParser(description="Safely smoke-test real-device Dock3 DRC control")
    add_config_argument(parser)
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--sn", default=None)
    parser.add_argument("--timeout", type=float, default=8.0)
    parser.add_argument("--qos", type=int, choices=[0, 1, 2], default=1)
    parser.add_argument("--quiet-stale", action="store_true")
    parser.add_argument("--fail-on-stale", action="store_true")
    parser.add_argument("--no-reset-first", action="store_true")
    parser.add_argument("--skip-authority-grab", action="store_true")
    parser.add_argument("--skip-state-check", action="store_true")
    parser.add_argument("--skip-exit", action="store_true")
    parser.add_argument("--preflight-delay", type=float, default=5.0)
    parser.add_argument("--no-reply-window", type=float, default=1.0)
    parser.add_argument("--drc-broker-address", default="127.0.0.1:1883")
    parser.add_argument("--drc-username", default="real-test-user")
    parser.add_argument("--drc-password", default="real-test-token")
    parser.add_argument("--drc-expire-time", type=int, default=3600)
    parser.add_argument("--drc-enable-tls", action="store_true")
    parser.add_argument("--osd-frequency", type=int, default=10)
    parser.add_argument("--hsi-frequency", type=int, default=1)
    parser.add_argument("--enable-motion", action="store_true")
    parser.add_argument("--motion-duration", type=float, default=1.0)
    parser.add_argument("--command-interval", type=float, default=0.5)
    parser.add_argument("--vx", type=float, default=0.0, help="Forward speed, m/s, limited to +/-0.5")
    parser.add_argument("--vy", type=float, default=0.0, help="Right speed, m/s, limited to +/-0.5")
    parser.add_argument("--h", type=float, default=0.0, help="Up speed, m/s, limited to +/-0.3")
    parser.add_argument("--w", type=float, default=0.0, help="Yaw speed, deg/s, limited to +/-10")
    args = parser.parse_args()
    args.host, args.port, args.sn = resolve_mqtt_settings(args)

    if args.enable_motion:
        clamp_motion_args(args)
    elif any(abs(float(getattr(args, name))) > 1e-9 for name in ("vx", "vy", "h", "w")):
        raise SystemExit("non-zero motion values require --enable-motion")

    client = MqttRequestClient(
        args.host,
        args.port,
        args.sn,
        args.qos,
        quiet_stale=args.quiet_stale,
        client_id_prefix="edge_gateway_real_drc",
        subscribe_events=True,
    )
    if not client.connect(args.timeout):
        print("MQTT connect timeout", file=sys.stderr)
        return 2

    drc_entered = False
    try:
        preflight_delay(args)

        if not args.no_reset_first:
            rc, _ = client.request(
                "drc_mode_exit",
                {},
                args.timeout,
                channel="services",
                allowed_results={0, DOCK_SYSTEM_ERROR, DOCK_COMMUNICATION_ERROR, DEVICE_STATE_UNSUPPORTED},
            )
            if rc != 0:
                return rc

        rc, _ = client.request(
            "drc_mode_enter",
            official_enter_payload(args),
            args.timeout,
            channel="services",
            allowed_results={0},
        )
        if rc != 0:
            return rc
        drc_entered = True

        if not args.skip_state_check:
            rc = require_drc_state(client, args, expected=2)
            if rc != 0:
                return rc

        if not args.skip_authority_grab:
            rc, _ = client.request(
                "flight_authority_grab",
                {},
                args.timeout,
                channel="drc",
                allowed_results={0},
                expected_status="ok",
            )
            if rc != 0:
                return rc

        rc = send_heartbeat(client, args, seq=1)
        if rc != 0:
            return rc

        rc = send_zero_controls(client, args, seq=2)
        if rc != 0:
            return rc

        if args.enable_motion:
            rc = send_motion_pulse(client, args, start_seq=3)
            if rc != 0:
                return rc

        rc = send_zero_controls(client, args, seq=9000)
        if rc != 0:
            return rc

        if not args.skip_exit:
            rc, _ = client.request(
                "drc_mode_exit",
                {},
                args.timeout,
                channel="services",
                allowed_results={0},
            )
            if rc != 0:
                return rc
            drc_entered = False
            if not args.skip_state_check:
                rc = require_drc_state(client, args, expected=0)
                if rc != 0:
                    return rc

        stale_total = client.stale_total()
        if args.fail_on_stale and stale_total:
            print(
                "stale duplicate replies observed "
                f"(ignored_mismatched={client.stats['ignored_mismatched']}, "
                f"ignored_legacy={client.stats['ignored_legacy']}, "
                f"ignored_duplicate={client.stats['ignored_duplicate']}, "
                f"ignored_wrong_topic={client.stats['ignored_wrong_topic']})",
                file=sys.stderr,
            )
            return 10

        print(
            "real drc control smoke test ok "
            f"(accepted={client.stats['accepted']}, events={client.stats['events']}, "
            f"ignored_total={stale_total}, unrelated_replies={client.unrelated_reply_total()})"
        )
        return 0
    except KeyboardInterrupt:
        print("aborted by user", file=sys.stderr)
        return 130
    finally:
        if drc_entered and not args.skip_exit:
            try:
                client.request(
                    "drone_control",
                    {"seq": 9998, "x": 0.0, "y": 0.0, "h": 0.0, "w": 0.0},
                    min(args.timeout, 3.0),
                    channel="drc",
                    allowed_results={0},
                    expected_status="ok",
                    verbose=False,
                )
                client.request(
                    "drc_mode_exit",
                    {},
                    min(args.timeout, 5.0),
                    channel="services",
                    allowed_results={0, DOCK_SYSTEM_ERROR, DOCK_COMMUNICATION_ERROR, DEVICE_STATE_UNSUPPORTED},
                    verbose=False,
                )
            except Exception as exc:
                print(f"warning: DRC cleanup failed: {exc}", file=sys.stderr)
        client.disconnect()


if __name__ == "__main__":
    raise SystemExit(main())
