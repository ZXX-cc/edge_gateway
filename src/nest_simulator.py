#!/usr/bin/env python3
"""
MAVLink 机巢模拟器 (UDP 版)
支持标准心跳 + 自定义消息 DRONE_NEST_COMMAND / RESPONSE / ORDER_RESPONSE
"""
import sys
import time
import socket
import threading
import struct
import argparse
import json
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
GENERATED_DIALECT_DIR = PROJECT_ROOT / 'src' / 'generated_dialect'
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(GENERATED_DIALECT_DIR))
from pymavlink.dialects.v20 import ardupilotmega
from src.cloud_api.dock3_wayline import parse_wayline_file
from src.mavlink.flight_modes import flight_mode_profile, mode_name_to_custom_mode, parse_flight_mode
import drone_nest


_RESET = "\033[0m"
_COLORS = {
    "nest": "\033[35m",
    "ok": "\033[32m",
    "warn": "\033[33m",
}
_ORDINARY_COMMAND_LABELS = {
    0x1101: "舱门打开",
    0x1102: "舱门关闭",
    0x1103: "舱门停止",
    0x0F01: "开始充电",
    0x0F02: "停止充电",
    0x1401: "夹紧",
    0x1402: "夹紧释放",
    0x1403: "夹紧停止",
    0x0A01: "急停触发",
    0x0A02: "急停解除",
    0x0A04: "初始化",
    0x0A05: "故障恢复",
    0x0A06: "大灯开",
    0x0A07: "大灯关",
    0x0B01: "飞机上电",
    0x0B02: "飞机下电",
}
_TAKEOFF_LAND_COMMAND_LABELS = {
    0x01: "起飞流程开始",
    0x02: "起飞流程终止",
    0x04: "降落流程终止",
}

BIT_NEST_HEARTBEAT = 0
BIT_DRONE_HEARTBEAT = 1
BIT_PLC_HEARTBEAT = 2
BIT_ESTOP = 3
BIT_CHARGING = 4
BIT_READY = 6
BIT_BUSY = 8
BIT_FAULT = 9
BIT_R_AXIS_ZERO = 10
BIT_TAKEOFF_IN_PROGRESS = 0
BIT_LANDING_IN_PROGRESS = 1
BIT_TAKEOFF_ABORT_PROGRESS = 2
BIT_LANDING_ABORT_PROGRESS = 3
BIT_ALLOW_TAKEOFF = 5

DEFAULT_HEALTHY_BOOLSTATE1 = (
    (1 << BIT_NEST_HEARTBEAT)
    | (1 << BIT_DRONE_HEARTBEAT)
    | (1 << BIT_PLC_HEARTBEAT)
    | (1 << BIT_READY)
    | (1 << BIT_R_AXIS_ZERO)
    | (1 << 11)
)
DEFAULT_READY_BOOLSTATE2 = 1 << BIT_ALLOW_TAKEOFF

REJECT_COMMAND_CONFLICT = 1
REJECT_PLC_TIMEOUT = 2
REJECT_DRONE_TIMEOUT = 3
REJECT_MULTIPLE_TAKEOFF = 4

_REJECT_REASON_LABELS = {
    0: "无",
    REJECT_COMMAND_CONFLICT: "普通命令与起降命令冲突",
    REJECT_PLC_TIMEOUT: "PLC/机巢状态不允许执行",
    REJECT_DRONE_TIMEOUT: "飞机/起降状态不允许执行",
    REJECT_MULTIPLE_TAKEOFF: "未知或重复起降命令",
}
_GATE_STATE_LABELS = {
    0: "未知",
    1: "舱门打开",
    2: "舱门关闭",
    3: "舱门开启中",
    4: "舱门关闭中",
    5: "舱门暂停中",
    6: "舱门故障",
}
_CLAMP_STATE_LABELS = {
    0: "未知",
    1: "夹紧释放",
    2: "夹紧",
    3: "夹紧异常",
}
_LIGHT_STATE_LABELS = {
    0: "未知",
    1: "打开",
    2: "关闭",
}
_BOOLSTATE1_BITS = {
    BIT_NEST_HEARTBEAT: "机巢心跳正常",
    BIT_DRONE_HEARTBEAT: "飞机上电/心跳正常",
    BIT_PLC_HEARTBEAT: "PLC心跳正常",
    BIT_ESTOP: "急停",
    BIT_CHARGING: "充电中",
    BIT_READY: "系统就绪",
    BIT_BUSY: "系统忙",
    BIT_FAULT: "故障",
    BIT_R_AXIS_ZERO: "R轴归零",
    11: "大灯关闭",
}
_BOOLSTATE2_BITS = {
    BIT_TAKEOFF_IN_PROGRESS: "起飞中",
    BIT_LANDING_IN_PROGRESS: "降落中",
    BIT_TAKEOFF_ABORT_PROGRESS: "起飞终止中",
    BIT_LANDING_ABORT_PROGRESS: "降落终止中",
    BIT_ALLOW_TAKEOFF: "允许起飞",
}


class NestSimulator:
    def __init__(
        self,
        host='0.0.0.0',
        port=14550,
        system_id=101,
        component_id=1,
        command_log='logs/nest_simulator/command_trace.log',
        nest_status_log='logs/nest_simulator/nest_status.log',
        drone_host=None,
        drone_port=None,
        drone_status_log='logs/nest_simulator/drone_status.log',
        fault_state_file='/tmp/edge_gateway_nest_faults.json',
        fault_log='logs/nest_simulator/fault_injection.log',
        mission_file='/home/fenghuolun/zwz/HD.kml',
        mission_upload_timeout=30.0,
    ):
        self.host = host
        self.port = port
        self.system_id = system_id
        self.component_id = component_id
        self.command_log = Path(command_log) if command_log else None
        self.nest_status_log = Path(nest_status_log) if nest_status_log else None
        self.drone_status_log = Path(drone_status_log) if drone_status_log else None
        self.fault_state_file = Path(fault_state_file) if fault_state_file else None
        self.fault_log = Path(fault_log) if fault_log else None
        self._fault_file_signature = None
        self._fault_overrides = {}
        self.mission_file = mission_file
        self.mission_upload_timeout = float(mission_upload_timeout)
        self.sock = None
        self.drone_sock = None
        self.drone_bind_addr = (drone_host, int(drone_port)) if drone_host and drone_port else None
        self.drone_peer_addr = None
        self.client_addr = None
        self.running = False
        self.mav = None
        self.gateway_standard_mav = None
        self.drone_mav = None
        self.drone_last_seen_at = 0.0
        self.drone_system_id = None
        self.drone_component_id = None
        self.drone_mav_type = None
        self.drone_armed = False
        self.drone_relative_alt_m = 0.0
        self.drone_previous_alt_m = 0.0
        self.drone_flight_mode = "UNKNOWN"
        self.drone_current_waypoint = -1
        self.drone_battery_remaining = None
        self.drone_connected = False
        self.last_drone_status_print_at = 0.0
        self.last_nest_status_trace_at = 0.0
        self.pending_mission_items = []
        self.mission_upload_event = threading.Event()
        self.mission_upload_active = False
        self.mission_upload_lock = threading.Lock()

        # 模拟环境数据（单位与 drone_nest.xml 一致）
        self.env_data = {
            'ac_temp': 250,
            'nest_temperature': 260,
            'env_temperature': 240,
            'wind_direction': 18000,
            'wind_speed': 30,
            'atmospheric_pressure': 1013,
            'rainfall': 0,
            'charge_volt': 2600,
            'charge_current': 0,
            'error_code': 0,
            # 默认以健康待命状态模拟可起飞机巢。故障/急停/忙碌分支
            # 仍可由命令和协议专项明确注入，不能让正常 E2E 因全零
            # 状态位被误判为机巢离线。
            'boolstate1': DEFAULT_HEALTHY_BOOLSTATE1,
            'boolstate2': DEFAULT_READY_BOOLSTATE2,
            'clamp_state': 2,
            'gate_state': 2,
            'landing_light_state': 2
        }

    @staticmethod
    def _raw_mavlink_msg_id(data):
        if not data:
            return None
        magic = data[0]
        if magic == 0xFE and len(data) >= 6:
            return data[5]
        if magic == 0xFD and len(data) >= 10:
            return data[7] | (data[8] << 8) | (data[9] << 16)
        return None

    @staticmethod
    def _raw_standard_msg_type(msg_id):
        return {
            11: "SET_MODE",
            76: "COMMAND_LONG",
        }.get(msg_id, f"STANDARD_{msg_id}")

    @staticmethod
    def _bit(mask, bit):
        return bool(mask & (1 << bit))

    @staticmethod
    def _order_response_raw(reason_code, command_code, device_code=0x11):
        if not reason_code:
            return 0
        return (
            ((device_code & 0xFF) << 24)
            | ((command_code & 0xFF) << 16)
            | ((reason_code & 0xFF) << 8)
            | 0x01
        )

    def _evaluate_command_response(self, ordinary_order, takeoff_land_order):
        """Return v1.3-like execution replies for current simulated state.

        The real controller owns the final policy.  The simulator keeps a small
        deterministic subset so protocol tests can cover command conflicts and
        safety rejections before hardware is available.
        """
        ordinary_reason = 0
        takeoff_reason = 0
        env_data = self._effective_env_data()
        boolstate1 = env_data.get("boolstate1", 0)
        boolstate2 = env_data.get("boolstate2", 0)

        if ordinary_order and takeoff_land_order:
            ordinary_reason = REJECT_COMMAND_CONFLICT
            takeoff_reason = REJECT_COMMAND_CONFLICT

        estop = self._bit(boolstate1, BIT_ESTOP)
        fault = self._bit(boolstate1, BIT_FAULT) or bool(env_data.get("error_code", 0))
        busy = self._bit(boolstate1, BIT_BUSY)
        ready = self._bit(boolstate1, BIT_READY)
        allow_takeoff = self._bit(boolstate2, BIT_ALLOW_TAKEOFF)
        takeoff_busy = self._bit(boolstate2, BIT_TAKEOFF_IN_PROGRESS)
        landing_busy = self._bit(boolstate2, BIT_LANDING_IN_PROGRESS)

        if ordinary_order and not ordinary_reason:
            if estop and ordinary_order not in (0x0A02, 0x0A05):
                ordinary_reason = REJECT_PLC_TIMEOUT
            elif fault and ordinary_order not in (0x0A01, 0x0A05):
                ordinary_reason = REJECT_PLC_TIMEOUT
            elif busy and ordinary_order not in (0x1103, 0x1403, 0x0A01):
                ordinary_reason = REJECT_PLC_TIMEOUT

        if takeoff_land_order and not takeoff_reason:
            if takeoff_land_order not in _TAKEOFF_LAND_COMMAND_LABELS:
                takeoff_reason = REJECT_MULTIPLE_TAKEOFF
            elif estop or fault:
                takeoff_reason = REJECT_PLC_TIMEOUT
            elif takeoff_land_order == 0x01 and (busy or takeoff_busy or landing_busy or not ready or not allow_takeoff):
                takeoff_reason = REJECT_DRONE_TIMEOUT

        ordinary_response = self._order_response_raw(ordinary_reason, ordinary_order & 0xFF)
        takeoff_response = self._order_response_raw(takeoff_reason, takeoff_land_order & 0xFF)
        return ordinary_response, takeoff_response

    @staticmethod
    def crc16(data, extra=0):
        crc = 0xFFFF
        for b in data:
            b = b if isinstance(b, int) else b
            tmp = b ^ (crc & 0xFF)
            tmp = (tmp ^ (tmp << 4)) & 0xFF
            crc = (crc >> 8) ^ (tmp << 8) ^ (tmp << 3) ^ (tmp >> 4)
        if extra is not None:
            tmp = extra ^ (crc & 0xFF)
            tmp = (tmp ^ (tmp << 4)) & 0xFF
            crc = (crc >> 8) ^ (tmp << 8) ^ (tmp << 3) ^ (tmp >> 4)
        return crc & 0xFFFF

    @staticmethod
    def _color(text, color):
        return f"{_COLORS[color]}{text}{_RESET}"

    @staticmethod
    def _ordinary_command_label(ordinary_order):
        if not ordinary_order:
            return "none"
        return _ORDINARY_COMMAND_LABELS.get(ordinary_order, f"UNKNOWN 0x{ordinary_order:04X}")

    @staticmethod
    def _takeoff_land_command_label(takeoff_land_order):
        if not takeoff_land_order:
            return "none"
        return _TAKEOFF_LAND_COMMAND_LABELS.get(takeoff_land_order, f"UNKNOWN 0x{takeoff_land_order:02X}")

    def _command_label(self, ordinary_order, takeoff_land_order):
        if takeoff_land_order:
            return self._takeoff_land_command_label(takeoff_land_order)
        return self._ordinary_command_label(ordinary_order)

    @staticmethod
    def _reply_status(ordinary_response, takeoff_land_response):
        return "成功" if ordinary_response == 0 and takeoff_land_response == 0 else "失败/拒绝"

    @staticmethod
    def _active_bits(mask, labels):
        active = [label for bit, label in labels.items() if mask & (1 << bit)]
        return "、".join(active) if active else "无"

    @staticmethod
    def _decode_order_response(raw, response_type):
        if raw == 0:
            return {
                "raw": "0x00000000",
                "status": "成功",
                "reason": "无",
                "command_code": "0x00",
                "device_code": "0x00",
            }

        reply_status = raw & 0xFF
        reason_code = (raw >> 8) & 0xFF
        command_code = (raw >> 16) & 0xFF
        device_code = (raw >> 24) & 0xFF
        if response_type == "ordinary":
            command_label = _ORDINARY_COMMAND_LABELS.get(command_code, f"UNKNOWN 0x{command_code:02X}")
        else:
            command_label = _TAKEOFF_LAND_COMMAND_LABELS.get(command_code, f"UNKNOWN 0x{command_code:02X}")
        return {
            "raw": f"0x{raw:08X}",
            "status": "拒绝" if reply_status else "成功",
            "reason": _REJECT_REASON_LABELS.get(reason_code, f"未知原因 {reason_code}"),
            "reason_code": reason_code,
            "command_code": f"0x{command_code:02X}",
            "command_label": command_label,
            "device_code": f"0x{device_code:02X}",
        }

    def _state_lines(self, env_data=None):
        env_data = env_data or self.env_data
        boolstate1 = env_data.get("boolstate1", 0)
        boolstate2 = env_data.get("boolstate2", 0)
        gate_state = env_data.get("gate_state", 0)
        clamp_state = env_data.get("clamp_state", 0)
        light_state = env_data.get("landing_light_state", 0)
        return [
            (
                "机构",
                "舱门={gate}({gate_raw})，夹紧={clamp}({clamp_raw})，大灯={light}({light_raw})".format(
                    gate=_GATE_STATE_LABELS.get(gate_state, f"未知 {gate_state}"),
                    gate_raw=gate_state,
                    clamp=_CLAMP_STATE_LABELS.get(clamp_state, f"未知 {clamp_state}"),
                    clamp_raw=clamp_state,
                    light=_LIGHT_STATE_LABELS.get(light_state, f"未知 {light_state}"),
                    light_raw=light_state,
                ),
            ),
            (
                "电源",
                "充电电压={volt:.1f}V，充电电流={current:.1f}A".format(
                    volt=env_data.get("charge_volt", 0) / 100,
                    current=env_data.get("charge_current", 0) / 100,
                ),
            ),
            (
                "环境",
                "舱内={nest:.1f}C，外部={env:.1f}C，空调={ac:.1f}C，风向={wind_dir:.1f}deg，风速={wind_speed:.1f}m/s，雨量={rain}".format(
                    nest=env_data.get("nest_temperature", 0) / 10,
                    env=env_data.get("env_temperature", 0) / 10,
                    ac=env_data.get("ac_temp", 0) / 10,
                    wind_dir=env_data.get("wind_direction", 0) / 100,
                    wind_speed=env_data.get("wind_speed", 0) / 10,
                    rain=env_data.get("rainfall", 0) / 10,
                ),
            ),
            (
                "状态位1",
                f"0x{boolstate1:04X} [{self._active_bits(boolstate1, _BOOLSTATE1_BITS)}]",
            ),
            (
                "状态位2",
                f"0x{boolstate2:04X} [{self._active_bits(boolstate2, _BOOLSTATE2_BITS)}]",
            ),
            (
                "故障",
                f"error_code={env_data.get('error_code', 0)}，气压={env_data.get('atmospheric_pressure', 0) / 10:.1f}kPa",
            ),
        ]

    def _trace_fault(self, event, **fields):
        if not self.fault_log:
            return
        try:
            self.fault_log.parent.mkdir(parents=True, exist_ok=True)
            record = {
                "timestamp": datetime.now().isoformat(timespec="milliseconds"),
                "event": event,
                **fields,
            }
            with self.fault_log.open("a", encoding="utf-8") as file:
                file.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
        except Exception:
            pass

    @staticmethod
    def _set_mask_bit(mask, bit, enabled):
        if enabled:
            return mask | (1 << bit)
        return mask & ~(1 << bit)

    def _load_fault_overrides(self):
        """读取运行时故障覆盖；文件缺失或 active=false 即恢复模拟器原状态。"""
        if not self.fault_state_file:
            return {}
        try:
            signature = (
                self.fault_state_file.stat().st_mtime_ns,
                self.fault_state_file.stat().st_size,
            )
        except FileNotFoundError:
            signature = None
        except OSError as exc:
            self._trace_fault("fault_control_read_failed", error=str(exc))
            return self._fault_overrides

        if signature == self._fault_file_signature:
            return self._fault_overrides

        self._fault_file_signature = signature
        if signature is None:
            if self._fault_overrides:
                self._trace_fault("fault_injection_cleared", reason="control_file_removed")
            self._fault_overrides = {}
            return self._fault_overrides

        try:
            document = json.loads(self.fault_state_file.read_text(encoding="utf-8"))
            if not isinstance(document, dict):
                raise ValueError("fault control must be a JSON object")
            active = document.get("active", True)
            raw = document.get("faults", document)
            if not active:
                raw = {}
            if not isinstance(raw, dict):
                raise ValueError("faults must be a JSON object")
            allowed = {
                "nest_heartbeat", "plc_heartbeat", "drone_heartbeat",
                "estop_state", "charging_state", "system_busy", "fault_state",
                "takeoff_allowed", "gate_state", "clamp_state",
                "charge_volt", "charge_current", "error_code",
            }
            overrides = {key: raw[key] for key in allowed if key in raw}
            self._fault_overrides = overrides
            self._trace_fault(
                "fault_injection_applied" if overrides else "fault_injection_cleared",
                scenario=document.get("scenario", ""),
                overrides=overrides,
            )
        except Exception as exc:
            self._trace_fault("fault_control_invalid", error=str(exc))
        return self._fault_overrides

    def _effective_env_data(self):
        data = dict(self.env_data)
        overrides = self._load_fault_overrides()
        boolstate1_bits = {
            "nest_heartbeat": BIT_NEST_HEARTBEAT,
            "drone_heartbeat": BIT_DRONE_HEARTBEAT,
            "plc_heartbeat": BIT_PLC_HEARTBEAT,
            "estop_state": BIT_ESTOP,
            "charging_state": BIT_CHARGING,
            "system_busy": BIT_BUSY,
            "fault_state": BIT_FAULT,
        }
        for name, bit in boolstate1_bits.items():
            if name in overrides:
                data["boolstate1"] = self._set_mask_bit(
                    data.get("boolstate1", 0), bit, bool(overrides[name])
                )
        if "takeoff_allowed" in overrides:
            data["boolstate2"] = self._set_mask_bit(
                data.get("boolstate2", 0), BIT_ALLOW_TAKEOFF, bool(overrides["takeoff_allowed"])
            )
        ranges = {
            "gate_state": (0, 255),
            "clamp_state": (0, 255),
            "charge_volt": (0, 65535),
            "charge_current": (0, 65535),
            "error_code": (0, 65535),
        }
        for name, (minimum, maximum) in ranges.items():
            if name in overrides:
                try:
                    data[name] = min(maximum, max(minimum, int(overrides[name])))
                except (TypeError, ValueError):
                    pass
        return data

    @staticmethod
    def _print_block(title, rows):
        print(f"\n{title}")
        for key, value in rows:
            print(f"  {key}: {value}")

    def _trace_nest_status(self, env_data):
        if not self.nest_status_log:
            return
        now = time.time()
        if now - self.last_nest_status_trace_at < 10:
            return
        self.last_nest_status_trace_at = now
        try:
            self.nest_status_log.parent.mkdir(parents=True, exist_ok=True)
            timestamp = datetime.now().isoformat(timespec="milliseconds")
            rows = " | ".join(f"{key}={value}" for key, value in self._state_lines(env_data))
            with self.nest_status_log.open("a", encoding="utf-8") as file:
                file.write(f"{timestamp} nest_status {rows}\n")
        except Exception:
            pass

    def _trace_command(self, event, **fields):
        if not self.command_log:
            return
        try:
            self.command_log.parent.mkdir(parents=True, exist_ok=True)
            timestamp = datetime.now().isoformat(timespec="milliseconds")
            command = fields.get("command", "未识别命令")
            if event == "simulator_command_received":
                line = (
                    f"{timestamp} {self._color('[机巢模拟器]', 'nest')} "
                    f"收到命令：{command}，"
                    f"ordinary={fields.get('ordinary', '-')}，"
                    f"takeoff={fields.get('takeoff_land', '-')}，"
                    f"来源={fields.get('src', '-')}"
                )
            elif event == "simulator_order_response_sent":
                ordinary_response = fields.get("ordinary_response", "0x00000000")
                takeoff_land_response = fields.get("takeoff_land_response", "0x00000000")
                status = self._reply_status(
                    int(str(ordinary_response), 16),
                    int(str(takeoff_land_response), 16),
                )
                color = "ok" if status == "成功" else "warn"
                line = (
                    f"{timestamp} {self._color('[机巢模拟器]', color)} "
                    f"发送回复：{command}，结果={status}，"
                    f"ordinary_response={ordinary_response}，"
                    f"takeoff_response={takeoff_land_response}，"
                    f"目标={fields.get('target', '-')}"
                )
            else:
                line = f"{timestamp} {self._color('[机巢模拟器]', 'nest')} {event}：{fields}"
            with self.command_log.open("a", encoding="utf-8") as file:
                file.write(line + "\n")
        except Exception:
            pass

    def _drone_status_snapshot(self):
        now = time.time()
        connected = bool(self.drone_last_seen_at and now - self.drone_last_seen_at <= 5)
        age = now - self.drone_last_seen_at if self.drone_last_seen_at else None
        return {
            "connected": connected,
            "peer": self.drone_peer_addr,
            "system": self.drone_system_id,
            "component": self.drone_component_id,
            "mode": self.drone_flight_mode,
            "armed": self.drone_armed,
            "relative_alt_m": self.drone_relative_alt_m,
            "current_waypoint": self.drone_current_waypoint,
            "battery_remaining": self.drone_battery_remaining,
            "last_seen_age_s": age,
        }

    def _format_drone_status(self):
        status = self._drone_status_snapshot()
        connected = "已连接" if status["connected"] else "未连接"
        age = "-" if status["last_seen_age_s"] is None else f"{status['last_seen_age_s']:.1f}s"
        battery = "-" if status["battery_remaining"] is None else f"{status['battery_remaining']}%"
        waypoint = "-" if status["current_waypoint"] < 0 else status["current_waypoint"]
        return (
            f"SITL={connected} peer={status['peer'] or '-'} "
            f"sys/comp={status['system'] or '-'}/{status['component'] or '-'} "
            f"mode={status['mode']} armed={int(status['armed'])} alt={status['relative_alt_m']:.1f}m "
            f"wp={waypoint} battery={battery} last_seen={age}"
        )

    def _trace_drone_status(self, event="status"):
        if not self.drone_status_log:
            return
        try:
            self.drone_status_log.parent.mkdir(parents=True, exist_ok=True)
            timestamp = datetime.now().isoformat(timespec="milliseconds")
            with self.drone_status_log.open("a", encoding="utf-8") as file:
                file.write(f"{timestamp} {event} {self._format_drone_status()}\n")
        except Exception:
            pass

    def _trace_drone_event(self, event, **fields):
        if not self.drone_status_log:
            return
        try:
            self.drone_status_log.parent.mkdir(parents=True, exist_ok=True)
            timestamp = datetime.now().isoformat(timespec="milliseconds")
            details = " ".join(f"{key}={value}" for key, value in fields.items())
            with self.drone_status_log.open("a", encoding="utf-8") as file:
                file.write(f"{timestamp} {event} {details} {self._format_drone_status()}\n")
        except Exception:
            pass

    def _print_drone_status_summary(self, force=False):
        now = time.time()
        if not force and now - self.last_drone_status_print_at < 10:
            return
        self.last_drone_status_print_at = now
        print(f"[SITL] {self._format_drone_status()}")

    def _send_raw(self, data):
        if self.client_addr:
            try:
                self.sock.sendto(data, self.client_addr)
            except Exception as e:
                print(f"发送失败: {e}")

    def _send_to_drone(self, data):
        if not self.drone_peer_addr or not self.drone_sock:
            self._trace_drone_event(
                "send_to_drone_skipped",
                reason="no_peer_or_socket",
                bytes=len(data) if data else 0,
            )
            return
        try:
            self.drone_sock.sendto(data, self.drone_peer_addr)
        except Exception as e:
            print(f"发送到飞机失败: {e}")
            self._trace_drone_event("send_to_drone_failed", error=e)

    def _send_drone_message(self, msg):
        if not self.drone_mav:
            self._trace_drone_event("send_drone_message_skipped", reason="drone_mav_not_ready")
            return False
        data = msg.pack(self.drone_mav)
        self._send_to_drone(data)
        return bool(self.drone_peer_addr and self.drone_sock)

    def _target_system_component(self):
        return self.drone_system_id or 1, self.drone_component_id or 1

    @staticmethod
    def _coord_to_mission_int(value):
        try:
            number = float(value)
        except (TypeError, ValueError):
            return 0
        if abs(number) > 180:
            return int(round(number))
        return int(round(number * 1e7))

    @staticmethod
    def _mavlink_message(message_cls, *args):
        try:
            return message_cls(*args)
        except TypeError:
            # pymavlink 2.4.x dialects differ on whether MAVLink2 extension
            # fields such as mission_type are present in constructors.
            return message_cls(*args[:-1])

    @staticmethod
    def _message_int_attr(msg, name, default=-1):
        value = getattr(msg, name, default)
        if value is None:
            return default
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    def _load_mission_items(self):
        summary, waypoints = parse_wayline_file({"path": self.mission_file})
        if not waypoints:
            warnings = "; ".join(summary.get("warnings", [])) or "no waypoints parsed"
            raise ValueError(f"航线文件无法解析: {summary.get('source') or self.mission_file}: {warnings}")

        items = []
        for index, waypoint in enumerate(waypoints):
            command = int(waypoint.get("command", 16) or 16)
            frame = int(waypoint.get("frame", 3) or 3)
            items.append({
                "seq": index,
                "frame": frame,
                "command": command,
                "current": 1 if index == 0 else 0,
                "autocontinue": int(waypoint.get("autocontinue", 1) or 1),
                "param1": float(waypoint.get("param1", 0.0) or 0.0),
                "param2": float(waypoint.get("param2", 0.0) or 0.0),
                "param3": float(waypoint.get("param3", 0.0) or 0.0),
                "param4": float(waypoint.get("param4", 0.0) or 0.0),
                "x": self._coord_to_mission_int(waypoint.get("latitude", 0.0)),
                "y": self._coord_to_mission_int(waypoint.get("longitude", 0.0)),
                "z": float(waypoint.get("altitude", 50.0) or 50.0),
            })

        takeoff_commands = {
            ardupilotmega.MAV_CMD_NAV_TAKEOFF,
            ardupilotmega.MAV_CMD_NAV_VTOL_TAKEOFF,
        }
        if items and items[0]["command"] not in takeoff_commands:
            first = dict(items[0])
            takeoff_alt = max(10.0, float(first.get("z") or 50.0))
            takeoff = dict(first)
            takeoff.update({
                "seq": 0,
                "command": ardupilotmega.MAV_CMD_NAV_TAKEOFF,
                "current": 1,
                "param1": 0.0,
                "param2": 0.0,
                "param3": 0.0,
                "param4": 0.0,
                "z": takeoff_alt,
            })
            items.insert(0, takeoff)
            for seq, item in enumerate(items):
                item["seq"] = seq
                item["current"] = 1 if seq == 0 else 0

        print(f"[SITL] 已加载航线: {summary.get('source')}，航点={len(items)}")
        self._trace_drone_event(
            "mission_file_loaded",
            source=summary.get("source"),
            format=summary.get("format"),
            waypoints=len(items),
        )
        return items

    def _send_mission_count(self):
        target_system, target_component = self._target_system_component()
        mission_type = getattr(ardupilotmega, "MAV_MISSION_TYPE_MISSION", 0)
        msg = self._mavlink_message(
            ardupilotmega.MAVLink_mission_count_message,
            target_system,
            target_component,
            len(self.pending_mission_items),
            mission_type,
        )
        sent = self._send_drone_message(msg)
        self._trace_drone_event(
            "mission_count_sent",
            target=f"{target_system}/{target_component}",
            count=len(self.pending_mission_items),
            sent=int(sent),
        )
        return sent

    def _send_mission_item(self, seq):
        if seq < 0 or seq >= len(self.pending_mission_items):
            print(f"[SITL] 忽略越界航点请求: seq={seq}")
            return False
        target_system, target_component = self._target_system_component()
        mission_type = getattr(ardupilotmega, "MAV_MISSION_TYPE_MISSION", 0)
        item = self.pending_mission_items[seq]
        msg = self._mavlink_message(
            ardupilotmega.MAVLink_mission_item_int_message,
            target_system,
            target_component,
            int(item["seq"]),
            int(item["frame"]),
            int(item["command"]),
            int(item["current"]),
            int(item["autocontinue"]),
            float(item["param1"]),
            float(item["param2"]),
            float(item["param3"]),
            float(item["param4"]),
            int(item["x"]),
            int(item["y"]),
            float(item["z"]),
            mission_type,
        )
        print(f"[SITL] 下发航点 {seq + 1}/{len(self.pending_mission_items)} command={item['command']}")
        sent = self._send_drone_message(msg)
        self._trace_drone_event(
            "mission_item_sent",
            seq=seq,
            total=len(self.pending_mission_items),
            command=item["command"],
            sent=int(sent),
        )
        return sent

    def _send_command_long(self, command, *params):
        target_system, target_component = self._target_system_component()
        values = list(params[:7]) + [0.0] * (7 - len(params))
        msg = ardupilotmega.MAVLink_command_long_message(
            target_system,
            target_component,
            command,
            0,
            *values,
        )
        return self._send_drone_message(msg)

    def _send_set_mode_auto(self):
        target_system, _target_component = self._target_system_component()
        base_mode = ardupilotmega.MAV_MODE_FLAG_CUSTOM_MODE_ENABLED
        profile = flight_mode_profile(self.drone_mav_type)
        if profile == "unknown":
            profile = "copter"
        custom_mode = mode_name_to_custom_mode("AUTO", profile)
        if custom_mode is None:
            custom_mode = 3
        msg = ardupilotmega.MAVLink_set_mode_message(target_system, base_mode, custom_mode)
        sent = self._send_drone_message(msg)
        self._trace_drone_event(
            "auto_mode_request_sent",
            sent=int(sent),
            mav_type=self.drone_mav_type,
            profile=profile,
            custom_mode=custom_mode,
        )
        return sent

    def _start_sitl_mission_async(self):
        self._trace_drone_event("mission_start_requested", mission_file=self.mission_file)
        if not self.drone_sock or not self.drone_peer_addr:
            print("[SITL] 未连接，无法由机巢模拟器下发航线任务")
            self._trace_drone_event("mission_start_rejected", reason="sitl_not_connected")
            return
        with self.mission_upload_lock:
            if self.mission_upload_active:
                print("[SITL] 航线任务正在下发，忽略重复触发")
                self._trace_drone_event("mission_start_rejected", reason="upload_already_active")
                return
            self.mission_upload_active = True
            self.mission_upload_event.clear()
        try:
            self.pending_mission_items = self._load_mission_items()
            print(f"[SITL] 开始由机巢模拟器上传航线: {len(self.pending_mission_items)} 个航点")
            if not self._send_mission_count():
                self._trace_drone_event("mission_upload_failed", reason="mission_count_send_failed")
                return
            if not self.mission_upload_event.wait(timeout=self.mission_upload_timeout):
                print(f"[SITL] 航线上传超时: {self.mission_upload_timeout:.1f}s")
                self._trace_drone_event("mission_upload_timeout", timeout=self.mission_upload_timeout)
                return
            print("[SITL] 航线上传完成，发送解锁和 AUTO")
            arm_sent = self._send_command_long(ardupilotmega.MAV_CMD_COMPONENT_ARM_DISARM, 1, 0, 0, 0, 0, 0, 0)
            self._trace_drone_event("arm_command_sent", sent=int(arm_sent))
            time.sleep(1.0)
            auto_sent = self._send_set_mode_auto()
            self._trace_drone_event("auto_mode_sent", sent=int(auto_sent))
        except Exception as e:
            print(f"[SITL] 机巢模拟器下发航线失败: {e}")
            self._trace_drone_event("mission_upload_exception", error=e)
        finally:
            with self.mission_upload_lock:
                self.mission_upload_active = False

    def _start_sitl_mission(self):
        threading.Thread(target=self._start_sitl_mission_async, daemon=True).start()

    # 手动构造标准心跳 (无需 dialect)
    def _send_heartbeat(self):
        payload = struct.pack('<BBBBBB', 1, 3, 0, 0, 0, 3)  # type=1 GCS, autopilot=3 ArduPilot
        msg_id = 0
        crc_extra = 50
        crc = self.crc16(payload, extra=crc_extra)
        frame = struct.pack('<BBBBBB', 0xFE, len(payload), 0, self.system_id, self.component_id, msg_id) \
                + payload + struct.pack('<H', crc)
        self._send_raw(frame)

    def _send_drone_heartbeat(self):
        payload = struct.pack('<BBBBBB', 1, 3, 0, 0, 0, 3)  # type=1 GCS, autopilot=3 ArduPilot
        msg_id = 0
        crc_extra = 50
        crc = self.crc16(payload, extra=crc_extra)
        frame = struct.pack('<BBBBBB', 0xFE, len(payload), 0, self.system_id, self.component_id, msg_id) \
                + payload + struct.pack('<H', crc)
        self._send_to_drone(frame)

    def _send_drone_status_text(self):
        if not self.drone_peer_addr or not self.drone_mav:
            return
        env_data = self._effective_env_data()
        text = (
            f"NEST hb={int(bool(env_data['boolstate1'] & 1))} "
            f"takeoff={int(bool(env_data['boolstate2'] & 1))} "
            f"gate={env_data['gate_state']}"
        )
        try:
            msg = ardupilotmega.MAVLink_statustext_message(6, text.encode("utf-8")[:50])
            self._send_to_drone(msg.pack(self.drone_mav))
        except Exception as e:
            print(f"发送机巢状态到飞机失败: {e}")

    # 使用自定义 dialect 发送 DRONE_NEST_RESPONSE
    def _send_nest_response(self):
        env_data = self._effective_env_data()
        msg = drone_nest.MAVLink_drone_nest_response_message(
            ac_temp=env_data['ac_temp'],
            nest_temperature=env_data['nest_temperature'],
            env_temperature=env_data['env_temperature'],
            wind_direction=env_data['wind_direction'],
            wind_speed=env_data['wind_speed'],
            atmospheric_pressure=env_data['atmospheric_pressure'],
            rainfall=env_data['rainfall'],
            charge_volt=env_data['charge_volt'],
            charge_current=env_data['charge_current'],
            error_code=env_data['error_code'],
            boolstate1=env_data['boolstate1'],
            boolstate2=env_data['boolstate2'],
            clamp_state=env_data['clamp_state'],
            gate_state=env_data['gate_state'],
            landing_light_state=env_data['landing_light_state']
        )
        # 关键：直接调用 self.mav 的 pack 方法，不要用 self.mav.mav
        self._send_raw(msg.pack(self.mav))
        self._trace_nest_status(env_data)

    def _send_order_response(
        self,
        ordinary_response=0,
        takeoff_land_response=0,
        command="-",
        triggered_by_command=False,
    ):
        msg = drone_nest.MAVLink_drone_nest_order_response_message(
            ordinary_order_response=ordinary_response,
            takeoff_land_order_response=takeoff_land_response
        )
        self._send_raw(msg.pack(self.mav))
        ordinary_detail = self._decode_order_response(ordinary_response, "ordinary")
        takeoff_detail = self._decode_order_response(takeoff_land_response, "takeoff_land")
        self._print_block("已发送 DRONE_NEST_ORDER_RESPONSE 指令回复", [
            ("命令", command),
            (
                "普通指令回复",
                "{raw}，状态={status}，原因={reason}，命令码={command_code}，设备={device_code}".format(
                    **ordinary_detail
                ),
            ),
            (
                "起降指令回复",
                "{raw}，状态={status}，原因={reason}，命令码={command_code}，设备={device_code}".format(
                    **takeoff_detail
                ),
            ),
        ])
        if triggered_by_command or ordinary_response or takeoff_land_response:
            self._trace_command(
                "simulator_order_response_sent",
                command=command,
                target=str(self.client_addr),
                ordinary_response=f"0x{ordinary_response:08X}",
                takeoff_land_response=f"0x{takeoff_land_response:08X}",
            )

    def _update_state_from_command(self, ordinary_order, takeoff_land_order):
        if ordinary_order == 0x1101:
            self.env_data['gate_state'] = 1
            print("模拟器：舱门打开")
        elif ordinary_order == 0x1102:
            self.env_data['gate_state'] = 2
            print("模拟器：舱门关闭")
        elif ordinary_order == 0x1402:
            self.env_data['clamp_state'] = 1
            print("模拟器：夹紧释放")
        elif ordinary_order == 0x1401:
            self.env_data['clamp_state'] = 2
            print("模拟器：夹紧")
        elif ordinary_order == 0x0F01:
            self.env_data['charge_current'] = 500
            self.env_data['boolstate1'] |= (1 << 4)
            print("模拟器：开始充电")
        elif ordinary_order == 0x0F02:
            self.env_data['charge_current'] = 0
            self.env_data['boolstate1'] &= ~(1 << 4)
            print("模拟器：停止充电")
        elif ordinary_order == 0x0A06:
            self.env_data['landing_light_state'] = 1
            self.env_data['boolstate1'] &= ~(1 << 11)
            print("模拟器：大灯开")
        elif ordinary_order == 0x0A07:
            self.env_data['landing_light_state'] = 2
            self.env_data['boolstate1'] |= (1 << 11)
            print("模拟器：大灯关")
        elif ordinary_order == 0x0B01:
            self.env_data['boolstate1'] |= (1 << 1)
            print("模拟器：飞机上电")
        elif ordinary_order == 0x0B02:
            self.env_data['boolstate1'] &= ~(1 << 1)
            print("模拟器：飞机下电")
        elif ordinary_order:
            print(f"模拟器：未识别普通命令 0x{ordinary_order:04X}")

        if takeoff_land_order == 0x01:
            self.env_data['boolstate2'] |= (1 << 0)
            print("模拟器：起飞流程开始")
        elif takeoff_land_order == 0x02:
            self.env_data['boolstate2'] &= ~(1 << 0)
            self.env_data['boolstate2'] &= ~(1 << BIT_TAKEOFF_ABORT_PROGRESS)
            print("模拟器：起飞流程终止")
        elif takeoff_land_order == 0x04:
            self.env_data['boolstate2'] &= ~(1 << 1)
            self.env_data['boolstate2'] &= ~(1 << BIT_LANDING_ABORT_PROGRESS)
            print("模拟器：降落流程终止")
        elif takeoff_land_order:
            print(f"模拟器：未识别起降命令 0x{takeoff_land_order:02X}")

    def _handle_message(self, msg):
        msg_type = msg.get_type()
        if msg_type.startswith('UNKNOWN_'):
            if getattr(msg, 'id', None) == 0:
                self._send_heartbeat()
            return

        if msg_type != 'HEARTBEAT':
            print(f"收到消息: {msg_type}")
        if msg_type == 'HEARTBEAT':
            self._send_heartbeat()
        elif msg_type == 'DRONE_NEST_COMMAND':
            ordinary = getattr(msg, 'ordinary_order', 0)
            takeoff_land = getattr(msg, 'takeoff_land_order', 0)
            has_command = bool(ordinary or takeoff_land)
            command = self._command_label(ordinary, takeoff_land)
            self._print_block("收到 DRONE_NEST_COMMAND 机巢命令", [
                ("来源", f"system={msg.get_srcSystem()} component={msg.get_srcComponent()}"),
                ("普通指令", f"0x{ordinary:04X} {self._ordinary_command_label(ordinary)}"),
                ("起降指令", f"0x{takeoff_land:02X} {self._takeoff_land_command_label(takeoff_land)}"),
                ("解析动作", command if has_command else "无动作/空命令"),
            ])
            ordinary_response, takeoff_response = self._evaluate_command_response(ordinary, takeoff_land)
            if ordinary_response == 0 and takeoff_response == 0:
                self._update_state_from_command(ordinary, takeoff_land)
                if takeoff_land == 0x01 and self.drone_sock:
                    self._trace_drone_event("takeoff_start_triggers_mission_upload")
                    self._start_sitl_mission()
            else:
                ordinary_detail = self._decode_order_response(ordinary_response, "ordinary")
                takeoff_detail = self._decode_order_response(takeoff_response, "takeoff_land")
                self._print_block("模拟器拒绝命令", [
                    ("普通指令", f"{ordinary_detail['raw']}，原因={ordinary_detail['reason']}"),
                    ("起降指令", f"{takeoff_detail['raw']}，原因={takeoff_detail['reason']}"),
                ])
            if has_command:
                self._trace_command(
                    "simulator_command_received",
                    command=command,
                    src=f"{msg.get_srcSystem()}/{msg.get_srcComponent()}",
                    ordinary=f"0x{ordinary:04X}",
                    takeoff_land=f"0x{takeoff_land:02X}",
                )
            self._send_order_response(
                ordinary_response,
                takeoff_response,
                command=command,
                triggered_by_command=has_command,
            )

    def _set_boolstate1_bit(self, bit, enabled):
        if enabled:
            self.env_data['boolstate1'] |= (1 << bit)
        else:
            self.env_data['boolstate1'] &= ~(1 << bit)

    def _set_boolstate2_bit(self, bit, enabled):
        if enabled:
            self.env_data['boolstate2'] |= (1 << bit)
        else:
            self.env_data['boolstate2'] &= ~(1 << bit)

    def _sync_flight_process_bits_from_drone(self):
        # 机巢流程状态应跟随飞机状态，而不是长期保留终止命令的瞬时标志。
        airborne = self.drone_armed and self.drone_relative_alt_m > 1.0
        landing_modes = {"LAND", "QLAND", "RTL", "QRTL", "LOITER_ALT_QLAND"}
        descending = self.drone_relative_alt_m < self.drone_previous_alt_m - 0.2
        landing = airborne and (
            self.drone_flight_mode in landing_modes
            or (descending and self.drone_relative_alt_m < 10.0)
        )
        takeoff = airborne and not landing

        self._set_boolstate2_bit(BIT_TAKEOFF_IN_PROGRESS, takeoff)
        self._set_boolstate2_bit(BIT_LANDING_IN_PROGRESS, landing)
        self._set_boolstate2_bit(BIT_TAKEOFF_ABORT_PROGRESS, False)
        self._set_boolstate2_bit(BIT_LANDING_ABORT_PROGRESS, False)

        if airborne:
            self._set_boolstate2_bit(BIT_ALLOW_TAKEOFF, False)
            self.env_data['charge_current'] = 0
            self._set_boolstate1_bit(BIT_CHARGING, False)
        elif self.drone_armed:
            self._set_boolstate2_bit(BIT_ALLOW_TAKEOFF, True)

    def _handle_drone_message(self, msg):
        msg_type = msg.get_type()
        was_connected = self.drone_connected
        self.drone_last_seen_at = time.time()
        self.drone_system_id = msg.get_srcSystem()
        self.drone_component_id = msg.get_srcComponent()
        self.drone_connected = True
        self._set_boolstate1_bit(BIT_DRONE_HEARTBEAT, True)

        if msg_type == 'HEARTBEAT':
            base_mode = int(getattr(msg, 'base_mode', 0) or 0)
            self.drone_mav_type = self._message_int_attr(msg, 'type', None)
            self.drone_armed = bool(base_mode & ardupilotmega.MAV_MODE_FLAG_SAFETY_ARMED)
            self.drone_flight_mode = parse_flight_mode(
                self._message_int_attr(msg, 'custom_mode', 0),
                mav_type=self.drone_mav_type,
            )
            self._set_boolstate1_bit(BIT_READY, True)
        elif msg_type == 'GLOBAL_POSITION_INT':
            self.drone_previous_alt_m = self.drone_relative_alt_m
            self.drone_relative_alt_m = float(getattr(msg, 'relative_alt', 0) or 0) / 1000.0
        elif msg_type == 'MISSION_CURRENT':
            self.drone_current_waypoint = self._message_int_attr(msg, 'seq', -1)
        elif msg_type == 'SYS_STATUS':
            remaining = self._message_int_attr(msg, 'battery_remaining', -1)
            if remaining >= 0:
                self.env_data['batteryRemaining'] = remaining
                self.drone_battery_remaining = remaining
        elif msg_type in ('MISSION_REQUEST_INT', 'MISSION_REQUEST'):
            seq = self._message_int_attr(msg, 'seq', -1)
            self._trace_drone_event("mission_request_received", msg_type=msg_type, seq=seq)
            if self.mission_upload_active:
                self._send_mission_item(seq)
        elif msg_type == 'MISSION_ACK':
            ack_type = self._message_int_attr(msg, 'type', -1)
            self._trace_drone_event("mission_ack_received", ack_type=ack_type)
            if self.mission_upload_active:
                if ack_type == 0:
                    self.mission_upload_event.set()
                    print("[SITL] 收到 MISSION_ACK: ACCEPTED")
                else:
                    print(f"[SITL] 收到 MISSION_ACK: type={ack_type}")

        self._sync_flight_process_bits_from_drone()

        if not was_connected:
            self._trace_drone_status("connected")
            self._print_drone_status_summary(force=True)

    def _drone_receive_loop(self):
        if not self.drone_sock or not self.drone_mav:
            return
        print("机巢模拟器等待 SITL MAVLink 消息...")
        while self.running:
            try:
                data, addr = self.drone_sock.recvfrom(4096)
                if not data:
                    continue
                if self.drone_peer_addr != addr:
                    self.drone_peer_addr = addr
                    print(f"已注册 SITL 地址: {addr}")
                if self.client_addr:
                    self._send_raw(data)
                for byte in data:
                    msg = self.drone_mav.parse_char(bytes([byte]))
                    if msg:
                        self._handle_drone_message(msg)
            except socket.timeout:
                if self.drone_last_seen_at and time.time() - self.drone_last_seen_at > 5:
                    if self.drone_connected:
                        self.drone_connected = False
                        self._trace_drone_status("disconnected")
                        self._print_drone_status_summary(force=True)
                    self._set_boolstate1_bit(BIT_DRONE_HEARTBEAT, False)
                continue
            except Exception as e:
                print(f"SITL 接收错误: {e}")
                time.sleep(0.1)

    def _status_loop(self):
        """每2秒主动发送环境状态"""
        while self.running:
            if self.client_addr:
                self._send_nest_response()
            if self.drone_bind_addr:
                self._send_drone_heartbeat()
                self._send_drone_status_text()
                self._trace_drone_status()
                self._print_drone_status_summary()
            time.sleep(2)

    def _receive_loop(self):
        print("模拟器等待网关机巢 MAVLink 消息...")
        while self.running:
            try:
                data, addr = self.sock.recvfrom(4096)
                if not data:
                    continue
                should_forward_to_drone = False
                raw_msg_id = self._raw_mavlink_msg_id(data)
                if raw_msg_id in {11, 76}:
                    should_forward_to_drone = True
                    if self.client_addr != addr:
                        self.client_addr = addr
                        print(f"已注册网关地址: {addr}")
                        self._send_heartbeat()
                    msg_type = self._raw_standard_msg_type(raw_msg_id)
                    self._trace_command(
                        "gateway_raw_standard_seen",
                        msg_type=msg_type,
                        msg_id=raw_msg_id,
                        addr=addr,
                        bytes=len(data),
                        raw=data[:16].hex(),
                    )
                for byte in data:
                    chunk = bytes([byte])
                    try:
                        msg = self.mav.parse_char(chunk)
                        if msg:
                            # ArduPlane 启动命令会按联调要求把一份 SITL 数据镜像到
                            # 14551。该端口的业务客户端只能是网关（component=190）
                            # 或明确的机巢命令，防止高频 SITL 心跳抢占 client_addr，
                            # 导致 12004/12005 被错误回发给 MAVProxy。
                            if not self._is_gateway_link_message(msg):
                                continue
                            if self.client_addr != addr:
                                self.client_addr = addr
                                print(f"已注册网关地址: {addr}")
                                self._send_heartbeat()   # 立即回复心跳
                            self._handle_message(msg)
                    except Exception as e:
                        error_str = str(e).lower()
                        if 'invalid' not in error_str and 'prefix' not in error_str:
                            print(f"自定义 MAVLink 解析错误: {e}")
                    try:
                        standard_msg = self.gateway_standard_mav.parse_char(chunk) if self.gateway_standard_mav else None
                        if standard_msg:
                            if self._is_gateway_standard_message(standard_msg, addr):
                                if self.client_addr != addr:
                                    self.client_addr = addr
                                    print(f"已注册网关地址: {addr}")
                                    self._send_heartbeat()
                                should_forward_to_drone = True
                                self._trace_command(
                                    "gateway_standard_forward_matched",
                                    msg_type=standard_msg.get_type(),
                                    msg_id=standard_msg.get_msgId() if hasattr(standard_msg, "get_msgId") else "-",
                                    src=f"{standard_msg.get_srcSystem()}/{standard_msg.get_srcComponent()}",
                                    addr=addr,
                                )
                            elif standard_msg.get_type() != "BAD_DATA":
                                self._trace_command(
                                    "gateway_standard_ignored",
                                    msg_type=standard_msg.get_type(),
                                    msg_id=standard_msg.get_msgId() if hasattr(standard_msg, "get_msgId") else "-",
                                    src=f"{standard_msg.get_srcSystem()}/{standard_msg.get_srcComponent()}",
                                    addr=addr,
                                    client_addr=self.client_addr,
                                )
                            if self.client_addr is None and self._is_gateway_link_message(standard_msg):
                                self.client_addr = addr
                                print(f"已注册网关地址: {addr}")
                                self._send_heartbeat()
                    except Exception as e:
                        error_str = str(e).lower()
                        if 'invalid' not in error_str and 'prefix' not in error_str:
                            print(f"标准 MAVLink 解析错误: {e}")
                if should_forward_to_drone:
                    self._send_to_drone(data)
                    self._trace_drone_event(
                        "gateway_standard_forwarded",
                        bytes=len(data),
                        target=self.drone_peer_addr or "-",
                    )
            except socket.timeout:
                continue
            except Exception as e:
                print(f"接收错误: {e}")
                time.sleep(0.1)

    @staticmethod
    def _is_gateway_link_message(msg):
        """区分 14551 上的网关自定义链路与 SITL 标准消息镜像。"""
        return (
            msg.get_srcComponent() == 190
            or msg.get_type() == 'DRONE_NEST_COMMAND'
        )

    def _is_gateway_standard_message(self, msg, addr=None):
        """网关到飞机的标准 MAVLink 也必须经由机巢转发给 SITL。"""
        msg_type = msg.get_type()
        msg_id = msg.get_msgId() if hasattr(msg, "get_msgId") else None
        if msg_type in {
            'DRONE_NEST_COMMAND',
            'DRONE_NEST_RESPONSE',
            'DRONE_NEST_ORDER_RESPONSE',
        }:
            return False
        if msg_type == 'BAD_DATA' or str(msg_type).startswith('UNKNOWN_'):
            return False
        if isinstance(msg_id, int) and msg_id < 0:
            return False
        return msg.get_srcComponent() == 190 or (self.client_addr is not None and addr == self.client_addr)

    def start(self):
        print(f"启动 MAVLink 机巢模拟器 (UDP)，监听 {self.host}:{self.port}")
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind((self.host, self.port))
        self.sock.settimeout(0.5)
        if self.drone_bind_addr:
            self.drone_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self.drone_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.drone_sock.bind(self.drone_bind_addr)
            self.drone_sock.settimeout(0.5)
            print(f"已启用机巢模拟器 SITL 监听: {self.drone_bind_addr[0]}:{self.drone_bind_addr[1]}")
            if self.drone_status_log:
                print(f"飞机状态日志: {self.drone_status_log}")
        self.running = True

        # 创建自定义 MAVLink 实例（用于打包）
        class DummyFile:
            def write(self, data):
                pass
        self.mav = drone_nest.MAVLink(DummyFile(), srcSystem=self.system_id, srcComponent=self.component_id)
        self.gateway_standard_mav = ardupilotmega.MAVLink(
            DummyFile(),
            srcSystem=self.system_id,
            srcComponent=self.component_id,
        )
        if self.drone_sock:
            self.drone_mav = ardupilotmega.MAVLink(
                DummyFile(),
                srcSystem=self.system_id,
                srcComponent=self.component_id,
            )
            threading.Thread(target=self._drone_receive_loop, daemon=True).start()

        # 启动状态报告线程
        threading.Thread(target=self._status_loop, daemon=True).start()
        # 主接收循环
        self._receive_loop()

    def stop(self):
        self.running = False
        if self.sock:
            self.sock.close()
        if self.drone_sock:
            self.drone_sock.close()
        print("模拟器已停止")


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="MAVLink drone nest simulator")
    parser.add_argument("--host", default="0.0.0.0", help="UDP bind host")
    parser.add_argument("--port", type=int, default=14550, help="UDP bind port")
    parser.add_argument("--drone-host", default=None, help="SITL MAVLink UDP 本地监听 host；不填则不监听飞机")
    parser.add_argument("--drone-port", type=int, default=None, help="SITL MAVLink UDP 本地监听 port；不填则不监听飞机")
    parser.add_argument(
        "--command-log",
        default="logs/nest_simulator/command_trace.log",
        help="只记录非心跳命令的机巢模拟器日志文件；设为空字符串可关闭",
    )
    parser.add_argument(
        "--nest-status-log",
        default="logs/nest_simulator/nest_status.log",
        help="机巢状态快照日志文件；10秒一条，设为空字符串可关闭",
    )
    parser.add_argument(
        "--drone-status-log",
        default="logs/nest_simulator/drone_status.log",
        help="SITL 飞机连接和状态日志文件；设为空字符串可关闭",
    )
    parser.add_argument(
        "--fault-state-file",
        default="/tmp/edge_gateway_nest_faults.json",
        help="运行时故障注入 JSON 控制文件；删除文件或 active=false 可恢复",
    )
    parser.add_argument(
        "--fault-log",
        default="logs/nest_simulator/fault_injection.log",
        help="故障注入轨迹 JSONL 文件；设为空字符串可关闭",
    )
    parser.add_argument(
        "--mission-file",
        default="/home/fenghuolun/zwz/HD.kml",
        help="机巢模拟器下发给 SITL 的真实航线文件，支持 KML/KMZ/WPML/waypoints",
    )
    parser.add_argument(
        "--mission-upload-timeout",
        type=float,
        default=30.0,
        help="等待 SITL 完成航线上传的超时时间(秒)",
    )
    args = parser.parse_args()

    sim = NestSimulator(
        host=args.host,
        port=args.port,
        command_log=args.command_log,
        nest_status_log=args.nest_status_log,
        drone_host=args.drone_host,
        drone_port=args.drone_port,
        drone_status_log=args.drone_status_log,
        fault_state_file=args.fault_state_file,
        fault_log=args.fault_log,
        mission_file=args.mission_file,
        mission_upload_timeout=args.mission_upload_timeout,
    )
    try:
        sim.start()
    except KeyboardInterrupt:
        print("\n用户中断")
        sim.stop()
