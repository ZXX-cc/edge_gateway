"""机巢 MAVLink 调试工具共享协议定义。"""
from __future__ import annotations

import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[3]
GENERATED_DIALECT_DIR = PROJECT_ROOT / "src" / "generated_dialect"

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
if str(GENERATED_DIALECT_DIR) not in sys.path:
    sys.path.insert(0, str(GENERATED_DIALECT_DIR))

import drone_nest
from src.generated_dialect.gcs_order_protocol import GCSCommand


BIT_NEST_HEARTBEAT = 0
BIT_DRONE_HEARTBEAT = 1
BIT_PLC_HEARTBEAT = 2
BIT_ESTOP = 3
BIT_CHARGING = 4
BIT_CALIBRATE = 5
BIT_PLC_READY = 6
BIT_INIT = 7
BIT_TAKEOFF_IN_PROGRESS = 0
BIT_LANDING_IN_PROGRESS = 1
BIT_TAKEOFF_ABORT_PROGRESS = 2
BIT_LANDING_ABORT_PROGRESS = 3
BIT_EMERGENCY_LANDING = 4
BIT_ALLOW_TAKEOFF = 5


NEST_COMMANDS = {
    "open-door": (GCSCommand.DOOR_OPEN.value, 0),
    "close-door": (GCSCommand.DOOR_CLOSE.value, 0),
    "door-stop": (GCSCommand.DOOR_STOP.value, 0),
    "charge-start": (GCSCommand.CHARGE_START.value, 0),
    "charge-stop": (GCSCommand.CHARGE_STOP.value, 0),
    "clamp-tighten": (GCSCommand.CLAMP_TIGHTEN.value, 0),
    "clamp-release": (GCSCommand.CLAMP_RELEASE.value, 0),
    "clamp-stop": (GCSCommand.CLAMP_STOP.value, 0),
    "takeoff-start": (0, GCSCommand.TAKEOFF_START.value),
    "takeoff-abort": (0, GCSCommand.TAKEOFF_ABORT.value),
    "landing-start": (0, GCSCommand.LANDING_START.value),
    "landing-abort": (0, GCSCommand.LANDING_ABORT.value),
    "estop-on": (GCSCommand.ESTOP_ACTIVATE.value, 0),
    "estop-off": (GCSCommand.ESTOP_DEACTIVATE.value, 0),
    "calibrate-r": (GCSCommand.CALIBRATE_R.value, 0),
    "initialize": (GCSCommand.INITIALIZE.value, 0),
    "fault-recovery": (GCSCommand.FAULT_RECOVERY.value, 0),
}


ORDINARY_COMMAND_VALUES = {command[0] for command in NEST_COMMANDS.values() if command[0]}


ORDINARY_COMMAND_LABELS = {
    GCSCommand.DOOR_OPEN.value: "舱门打开",
    GCSCommand.DOOR_CLOSE.value: "舱门关闭",
    GCSCommand.DOOR_STOP.value: "舱门停止",
    GCSCommand.CHARGE_START.value: "开始充电",
    GCSCommand.CHARGE_STOP.value: "停止充电",
    GCSCommand.CLAMP_TIGHTEN.value: "夹紧",
    GCSCommand.CLAMP_RELEASE.value: "夹紧释放",
    GCSCommand.CLAMP_STOP.value: "夹紧停止",
    GCSCommand.ESTOP_ACTIVATE.value: "急停触发",
    GCSCommand.ESTOP_DEACTIVATE.value: "急停解除",
    GCSCommand.CALIBRATE_R.value: "R轴校准",
    GCSCommand.INITIALIZE.value: "初始化",
    GCSCommand.FAULT_RECOVERY.value: "故障恢复",
}


TAKEOFF_LAND_COMMAND_LABELS = {
    GCSCommand.TAKEOFF_START.value: "起飞流程开始",
    GCSCommand.TAKEOFF_ABORT.value: "起飞流程终止",
    GCSCommand.LANDING_START.value: "降落流程开始",
    GCSCommand.LANDING_ABORT.value: "降落流程终止",
}
