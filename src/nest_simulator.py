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
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
GENERATED_DIALECT_DIR = PROJECT_ROOT / 'src' / 'generated_dialect'
sys.path.insert(0, str(GENERATED_DIALECT_DIR))
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
    3: "舱门异常",
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
    1: "飞机上电",
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
    ):
        self.host = host
        self.port = port
        self.system_id = system_id
        self.component_id = component_id
        self.command_log = Path(command_log) if command_log else None
        self.sock = None
        self.client_addr = None
        self.running = False
        self.mav = None

        # 模拟环境数据（单位与 drone_nest.xml 一致）
        self.env_data = {
            'ac_temp': 250,
            'nest_temperature': 260,
            'env_temperature': 240,
            'wind_direction': 1800,
            'wind_speed': 30,
            'atmospheric_pressure': 1013,
            'rainfall': 0,
            'charge_volt': 260,
            'charge_current': 0,
            'error_code': 0,
            'boolstate1': 0,
            'boolstate2': 0,
            'clamp_state': 2,
            'gate_state': 2,
            'landing_light_state': 2
        }

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
        boolstate1 = self.env_data.get("boolstate1", 0)
        boolstate2 = self.env_data.get("boolstate2", 0)

        if ordinary_order and takeoff_land_order:
            ordinary_reason = REJECT_COMMAND_CONFLICT
            takeoff_reason = REJECT_COMMAND_CONFLICT

        estop = self._bit(boolstate1, BIT_ESTOP)
        fault = self._bit(boolstate1, BIT_FAULT) or bool(self.env_data.get("error_code", 0))
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
            elif takeoff_land_order == 0x04 and not landing_busy:
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

    def _state_lines(self):
        boolstate1 = self.env_data.get("boolstate1", 0)
        boolstate2 = self.env_data.get("boolstate2", 0)
        gate_state = self.env_data.get("gate_state", 0)
        clamp_state = self.env_data.get("clamp_state", 0)
        light_state = self.env_data.get("landing_light_state", 0)
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
                    volt=self.env_data.get("charge_volt", 0) / 10,
                    current=self.env_data.get("charge_current", 0) / 10,
                ),
            ),
            (
                "环境",
                "舱内={nest:.1f}C，外部={env:.1f}C，空调={ac:.1f}C，风向={wind_dir:.1f}deg，风速={wind_speed:.1f}m/s，雨量={rain}".format(
                    nest=self.env_data.get("nest_temperature", 0) / 10,
                    env=self.env_data.get("env_temperature", 0) / 10,
                    ac=self.env_data.get("ac_temp", 0) / 10,
                    wind_dir=self.env_data.get("wind_direction", 0) / 10,
                    wind_speed=self.env_data.get("wind_speed", 0) / 10,
                    rain=self.env_data.get("rainfall", 0),
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
                f"error_code={self.env_data.get('error_code', 0)}，气压={self.env_data.get('atmospheric_pressure', 0)}hPa",
            ),
        ]

    @staticmethod
    def _print_block(title, rows):
        print(f"\n{title}")
        for key, value in rows:
            print(f"  {key}: {value}")

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

    def _send_raw(self, data):
        if self.client_addr:
            try:
                self.sock.sendto(data, self.client_addr)
            except Exception as e:
                print(f"发送失败: {e}")

    # 手动构造标准心跳 (无需 dialect)
    def _send_heartbeat(self):
        payload = struct.pack('<BBBBBB', 1, 3, 0, 0, 0, 3)  # type=1 GCS, autopilot=3 ArduPilot
        msg_id = 0
        crc_extra = 50
        crc = self.crc16(payload, extra=crc_extra)
        frame = struct.pack('<BBBBBB', 0xFE, len(payload), 0, self.system_id, self.component_id, msg_id) \
                + payload + struct.pack('<H', crc)
        self._send_raw(frame)
        print("已发送 HEARTBEAT")

    # 使用自定义 dialect 发送 DRONE_NEST_RESPONSE
    def _send_nest_response(self):
        msg = drone_nest.MAVLink_drone_nest_response_message(
            ac_temp=self.env_data['ac_temp'],
            nest_temperature=self.env_data['nest_temperature'],
            env_temperature=self.env_data['env_temperature'],
            wind_direction=self.env_data['wind_direction'],
            wind_speed=self.env_data['wind_speed'],
            atmospheric_pressure=self.env_data['atmospheric_pressure'],
            rainfall=self.env_data['rainfall'],
            charge_volt=self.env_data['charge_volt'],
            charge_current=self.env_data['charge_current'],
            error_code=self.env_data['error_code'],
            boolstate1=self.env_data['boolstate1'],
            boolstate2=self.env_data['boolstate2'],
            clamp_state=self.env_data['clamp_state'],
            gate_state=self.env_data['gate_state'],
            landing_light_state=self.env_data['landing_light_state']
        )
        # 关键：直接调用 self.mav 的 pack 方法，不要用 self.mav.mav
        self._send_raw(msg.pack(self.mav))
        self._print_block("已发送 DRONE_NEST_RESPONSE 状态上报", self._state_lines())

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
            self.env_data['charge_current'] = 50
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
            print("模拟器：起飞流程终止")
        elif takeoff_land_order == 0x04:
            self.env_data['boolstate2'] &= ~(1 << 1)
            self.env_data['boolstate2'] |= (1 << 3)
            print("模拟器：降落流程终止")
        elif takeoff_land_order:
            print(f"模拟器：未识别起降命令 0x{takeoff_land_order:02X}")

    def _handle_message(self, msg):
        msg_type = msg.get_type()
        if msg_type.startswith('UNKNOWN_'):
            if getattr(msg, 'id', None) == 0:
                self._send_heartbeat()
            return

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

    def _status_loop(self):
        """每2秒主动发送环境状态"""
        while self.running:
            if self.client_addr:
                self._send_nest_response()
            time.sleep(2)

    def _receive_loop(self):
        print("模拟器等待 MAVLink 消息...")
        while self.running:
            try:
                data, addr = self.sock.recvfrom(4096)
                if not data:
                    continue
                if self.client_addr != addr:
                    self.client_addr = addr
                    print(f"已注册客户端地址: {addr}")
                    self._send_heartbeat()   # 立即回复心跳
                for byte in data:
                    msg = self.mav.parse_char(bytes([byte]))
                    if msg:
                        self._handle_message(msg)
            except socket.timeout:
                continue
            except Exception as e:
                print(f"接收错误: {e}")
                time.sleep(0.1)

    def start(self):
        print(f"启动 MAVLink 机巢模拟器 (UDP)，监听 {self.host}:{self.port}")
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind((self.host, self.port))
        self.sock.settimeout(0.5)
        self.running = True

        # 创建自定义 MAVLink 实例（用于打包）
        class DummyFile:
            def write(self, data):
                pass
        self.mav = drone_nest.MAVLink(DummyFile(), srcSystem=self.system_id, srcComponent=self.component_id)

        # 启动状态报告线程
        threading.Thread(target=self._status_loop, daemon=True).start()
        # 主接收循环
        self._receive_loop()

    def stop(self):
        self.running = False
        if self.sock:
            self.sock.close()
        print("模拟器已停止")


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="MAVLink drone nest simulator")
    parser.add_argument("--host", default="0.0.0.0", help="UDP bind host")
    parser.add_argument("--port", type=int, default=14550, help="UDP bind port")
    parser.add_argument(
        "--command-log",
        default="logs/nest_simulator/command_trace.log",
        help="只记录非心跳命令的机巢模拟器日志文件；设为空字符串可关闭",
    )
    args = parser.parse_args()

    sim = NestSimulator(host=args.host, port=args.port, command_log=args.command_log)
    try:
        sim.start()
    except KeyboardInterrupt:
        print("\n用户中断")
        sim.stop()
