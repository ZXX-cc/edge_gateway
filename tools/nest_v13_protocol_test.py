#!/usr/bin/env python3
"""Offline DJI Dock v1.3 nest MAVLink protocol acceptance checks.

This script validates the device-side adapter contract without MQTT or a real
nest controller.  It covers the three custom MAVLink messages used by the
gateway, command words, status bits, rejection replies, and cloud-property
mapping.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

if "pymavlink" not in sys.modules:
    pymavlink = ModuleType("pymavlink")
    pymavlink.mavutil = SimpleNamespace()
    sys.modules["pymavlink"] = pymavlink
    sys.modules["pymavlink.mavutil"] = pymavlink.mavutil

from src.generated_dialect.gcs_order_protocol import GCSCommand, REJECT_REASONS  # noqa: E402
from src.device.nest_hms_mapper import build_nest_hms_alarms  # noqa: E402
from src.modules.drone_nest_module import DroneNestModule  # noqa: E402
from src.nest_simulator import (  # noqa: E402
    BIT_ALLOW_TAKEOFF,
    BIT_BUSY,
    BIT_ESTOP,
    BIT_FAULT,
    BIT_LANDING_IN_PROGRESS,
    BIT_READY,
    BIT_R_AXIS_ZERO,
    BIT_TAKEOFF_IN_PROGRESS,
    REJECT_COMMAND_CONFLICT,
    REJECT_DRONE_TIMEOUT,
    REJECT_PLC_TIMEOUT,
    NestSimulator,
)
from web.mavlink.nest.protocol import NEST_COMMANDS  # noqa: E402


def bit(bit_index: int) -> int:
    return 1 << bit_index


def fail(message: str) -> int:
    print(message, file=sys.stderr)
    return 1


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def decode_reply(raw: int) -> dict:
    return {
        "raw": raw,
        "reply_status": raw & 0xFF,
        "reject_reason_code": (raw >> 8) & 0xFF,
        "command_code": (raw >> 16) & 0xFF,
        "device_code": (raw >> 24) & 0xFF,
    }


def check_message_ids() -> None:
    from src.generated_dialect import drone_nest

    require(drone_nest.MAVLink_drone_nest_command_message.id == 12003, "DRONE_NEST_COMMAND id mismatch")
    require(drone_nest.MAVLink_drone_nest_response_message.id == 12004, "DRONE_NEST_RESPONSE id mismatch")
    require(drone_nest.MAVLink_drone_nest_order_response_message.id == 12005, "DRONE_NEST_ORDER_RESPONSE id mismatch")


def check_command_table() -> None:
    expected = {
        "open-door": (0x1101, 0),
        "close-door": (0x1102, 0),
        "charge-start": (0x0F01, 0),
        "charge-stop": (0x0F02, 0),
        "clamp-tighten": (0x1401, 0),
        "clamp-release": (0x1402, 0),
        "takeoff-start": (0, 0x01),
        "takeoff-abort": (0, 0x02),
        "landing-abort": (0, 0x04),
        "estop-on": (0x0A01, 0),
        "fault-recovery": (0x0A05, 0),
        "drone-power-on": (0x0B01, 0),
        "drone-power-off": (0x0B02, 0),
    }
    for name, command in expected.items():
        require(NEST_COMMANDS.get(name) == command, f"{name} command mismatch")
    require("landing-start" not in NEST_COMMANDS, "v1.3 must not expose unsupported landing-start")


def check_simulator_state_and_rejections() -> None:
    sim = NestSimulator(command_log="")

    ordinary, takeoff = sim._evaluate_command_response(GCSCommand.CLAMP_TIGHTEN.value, 0)
    require(ordinary == 0 and takeoff == 0, "clamp tighten should be accepted in idle state")
    sim._update_state_from_command(GCSCommand.CLAMP_RELEASE.value, 0)
    require(sim.env_data["clamp_state"] == 1, "clamp release did not update clamp_state")
    sim._update_state_from_command(GCSCommand.CLAMP_TIGHTEN.value, 0)
    require(sim.env_data["clamp_state"] == 2, "clamp tighten did not update clamp_state")

    ordinary, takeoff = sim._evaluate_command_response(GCSCommand.DOOR_OPEN.value, GCSCommand.TAKEOFF_START.value)
    require(decode_reply(ordinary)["reject_reason_code"] == REJECT_COMMAND_CONFLICT, "ordinary conflict not rejected")
    require(decode_reply(takeoff)["reject_reason_code"] == REJECT_COMMAND_CONFLICT, "takeoff conflict not rejected")

    sim.env_data["boolstate1"] = bit(BIT_ESTOP)
    ordinary, takeoff = sim._evaluate_command_response(GCSCommand.DOOR_OPEN.value, 0)
    require(decode_reply(ordinary)["reject_reason_code"] == REJECT_PLC_TIMEOUT, "estop should reject door open")
    ordinary, takeoff = sim._evaluate_command_response(GCSCommand.ESTOP_DEACTIVATE.value, 0)
    require(ordinary == 0 and takeoff == 0, "estop off should be accepted while estopped")

    sim.env_data["boolstate1"] = bit(BIT_FAULT)
    ordinary, takeoff = sim._evaluate_command_response(GCSCommand.CHARGE_START.value, 0)
    require(decode_reply(ordinary)["reject_reason_code"] == REJECT_PLC_TIMEOUT, "fault should reject charging")
    ordinary, takeoff = sim._evaluate_command_response(GCSCommand.FAULT_RECOVERY.value, 0)
    require(ordinary == 0 and takeoff == 0, "fault recovery should be accepted while faulted")

    sim.env_data["boolstate1"] = bit(BIT_BUSY)
    ordinary, takeoff = sim._evaluate_command_response(GCSCommand.CLAMP_RELEASE.value, 0)
    require(decode_reply(ordinary)["reject_reason_code"] == REJECT_PLC_TIMEOUT, "busy should reject clamp release")

    sim.env_data["boolstate1"] = bit(BIT_READY) | bit(BIT_R_AXIS_ZERO)
    sim.env_data["boolstate2"] = bit(BIT_ALLOW_TAKEOFF)
    ordinary, takeoff = sim._evaluate_command_response(0, GCSCommand.TAKEOFF_START.value)
    require(ordinary == 0 and takeoff == 0, "takeoff should be accepted when ready and allowed")
    sim._update_state_from_command(0, GCSCommand.TAKEOFF_START.value)
    require(sim.env_data["boolstate2"] & bit(BIT_TAKEOFF_IN_PROGRESS), "takeoff start did not set progress bit")

    sim.env_data["boolstate1"] = bit(BIT_READY)
    sim.env_data["boolstate2"] = 0
    ordinary, takeoff = sim._evaluate_command_response(0, GCSCommand.TAKEOFF_START.value)
    require(decode_reply(takeoff)["reject_reason_code"] == REJECT_DRONE_TIMEOUT, "takeoff without allow flag should reject")

    sim.env_data["boolstate1"] = 0
    sim.env_data["boolstate2"] = 0
    ordinary, takeoff = sim._evaluate_command_response(0, GCSCommand.LANDING_ABORT.value)
    require(decode_reply(takeoff)["reject_reason_code"] == REJECT_DRONE_TIMEOUT, "landing abort without landing should reject")
    sim.env_data["boolstate2"] = bit(BIT_LANDING_IN_PROGRESS)
    ordinary, takeoff = sim._evaluate_command_response(0, GCSCommand.LANDING_ABORT.value)
    require(ordinary == 0 and takeoff == 0, "landing abort should be accepted during landing")


def check_module_parsing_and_cloud_fields() -> None:
    module = DroneNestModule()
    response = SimpleNamespace(
        ac_temp=250,
        nest_temperature=260,
        env_temperature=280,
        wind_direction=9000,
        wind_speed=12,
        atmospheric_pressure=1013,
        rainfall=0,
        charge_volt=2520,
        charge_current=40,
        error_code=0,
        boolstate1=bit(0) | bit(1) | bit(2) | bit(BIT_READY) | bit(BIT_R_AXIS_ZERO),
        boolstate2=bit(BIT_ALLOW_TAKEOFF),
        clamp_state=2,
        gate_state=2,
        landing_light_state=2,
    )
    module._handle_nest_response(response)
    env = module.get_environment_data()
    props = module.get_cloud_properties()
    require(env["nest_heartbeat"] is True, "nest heartbeat not parsed")
    require(env["takeoff_allowed"] is True, "takeoff allowed bit not parsed")
    require(env["r_axis_zero"] is True, "R axis zero bit not parsed")
    require(props["environment_temperature"] == 28.0, "environment temperature mapping mismatch")
    require(props["cover_state"] == 0, "gate closed should map to cover_state=0")
    require(props["putter_state"] == 0, "clamp tightened should map to putter_state=0")

    rejected = SimpleNamespace(
        ordinary_order_response=(0x11 << 24) | (0x01 << 16) | (REJECT_PLC_TIMEOUT << 8) | 0x01,
        takeoff_land_order_response=0,
    )
    module._handle_order_response(rejected)
    cloud_reply = module.get_cloud_order_response()
    require(cloud_reply["status"] == "rejected", "rejected order response status mismatch")
    require(cloud_reply["result"] == REJECT_PLC_TIMEOUT, "rejected order response result mismatch")
    require(
        cloud_reply["rejections"][0]["reason"] == REJECT_REASONS[11][REJECT_PLC_TIMEOUT],
        "rejected order response reason mismatch",
    )


def check_nest_hms_alarm_mapping() -> None:
    alarms = build_nest_hms_alarms(
        {
            "data_valid": True,
            "estop_state": True,
            "fault_state": True,
            "emergency_landing": True,
            "nest_heartbeat": False,
            "plc_heartbeat": False,
            "drone_heartbeat": True,
            "error_code": 12004,
        },
        "EDGEGW001",
    )
    by_key = {alarm["hms_key"]: alarm for alarm in alarms}
    require("dock3.nest.estop.active" in by_key, "estop HMS alarm missing")
    require("dock3.nest.fault.active" in by_key, "fault HMS alarm missing")
    require("dock3.nest.emergency_landing.active" in by_key, "emergency landing HMS alarm missing")
    require("dock3.nest.error_code.active" in by_key, "error code HMS alarm missing")
    require("dock3.nest.nest_heartbeat.lost" in by_key, "nest heartbeat HMS alarm missing")
    require("dock3.nest.plc_heartbeat.lost" in by_key, "PLC heartbeat HMS alarm missing")
    require(by_key["dock3.nest.error_code.active"]["alarm_id"] == "nest-error-code-12004", "error alarm id mismatch")


def run(verbose: bool = False) -> int:
    checks = [
        ("message ids 12003/12004/12005", check_message_ids),
        ("command table", check_command_table),
        ("simulator state and rejections", check_simulator_state_and_rejections),
        ("module parsing and cloud fields", check_module_parsing_and_cloud_fields),
        ("nest HMS alarm mapping", check_nest_hms_alarm_mapping),
    ]
    for title, fn in checks:
        if verbose:
            print(f"checking {title}...")
        fn()
    print(f"nest v1.3 protocol test ok (checks={len(checks)})")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate nest v1.3 MAVLink protocol adapter offline")
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument(
        "--timeout",
        type=float,
        default=None,
        help="Accepted for runbook compatibility; offline checks do not need a timeout.",
    )
    args = parser.parse_args()
    try:
        return run(verbose=args.verbose)
    except AssertionError as exc:
        return fail(str(exc))


if __name__ == "__main__":
    raise SystemExit(main())
