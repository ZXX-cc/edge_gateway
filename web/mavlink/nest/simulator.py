#!/usr/bin/env python3
"""基于 UDP 的机巢自定义 MAVLink 模拟器。"""
from __future__ import annotations

import argparse
import socket
import struct
import threading
import time
from datetime import datetime
from pathlib import Path

from protocol import (
    BIT_CALIBRATE,
    BIT_CHARGING,
    BIT_DRONE_HEARTBEAT,
    BIT_ESTOP,
    BIT_INIT,
    BIT_HEADLIGHT_OFF,
    BIT_LANDING_ABORT_PROGRESS,
    BIT_LANDING_IN_PROGRESS,
    BIT_NEST_HEARTBEAT,
    BIT_PLC_HEARTBEAT,
    BIT_PLC_READY,
    BIT_R_AXIS_ZERO,
    BIT_SYSTEM_BUSY,
    BIT_TAKEOFF_ABORT_PROGRESS,
    BIT_TAKEOFF_IN_PROGRESS,
    NEST_COMMANDS,
    ORDINARY_COMMAND_LABELS,
    TAKEOFF_LAND_COMMAND_LABELS,
    drone_nest,
)


class NestSimulator:
    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 14550,
        system_id: int = 101,
        component_id: int = 1,
        log_status: bool = False,
        command_log: str = "logs/nest_simulator_command_trace.log",
    ):
        self.host = host
        self.port = port
        self.system_id = system_id
        self.component_id = component_id
        self.log_status = log_status
        self.command_log = Path(command_log) if command_log else None
        self.sock = None
        self.client_addr = None
        self.running = False
        self.mav = None
        self.env_data = {
            "ac_temp": 250,
            "nest_temperature": 260,
            "env_temperature": 240,
            "wind_direction": 1800,
            "wind_speed": 30,
            "atmospheric_pressure": 1013,
            "rainfall": 0,
            "charge_volt": 260,
            "charge_current": 0,
            "error_code": 0,
            "boolstate1": (
                (1 << BIT_NEST_HEARTBEAT)
                | (1 << BIT_DRONE_HEARTBEAT)
                | (1 << BIT_PLC_HEARTBEAT)
                | (1 << BIT_PLC_READY)
                | (1 << BIT_R_AXIS_ZERO)
                | (1 << BIT_HEADLIGHT_OFF)
            ),
            "boolstate2": 0,
            "clamp_state": 2,
            "gate_state": 2,
            "landing_light_state": 2,
        }

    @staticmethod
    def _print_block(title, rows):
        print("")
        print(f"========== {title} ==========")
        for key, value in rows:
            print(f"{key:<18}: {value}")
        print("=" * (22 + len(title)))

    def _trace_command(self, event, **fields):
        if not self.command_log:
            return
        try:
            self.command_log.parent.mkdir(parents=True, exist_ok=True)
            labels = {
                "simulator_command_received": "机巢收到命令",
                "simulator_order_response_sent": "机巢发送回包",
            }
            label = labels.get(event, event)
            preferred_keys = (
                "command",
                "src",
                "target",
                "ordinary",
                "takeoff_land",
                "ordinary_response",
                "takeoff_land_response",
            )
            ordered_keys = [key for key in preferred_keys if key in fields]
            ordered_keys.extend(key for key in fields if key not in ordered_keys)
            detail = " ".join(f"{key}={fields[key]}" for key in ordered_keys)
            with self.command_log.open("a", encoding="utf-8") as file:
                file.write(
                    f"{datetime.now().isoformat(timespec='milliseconds')} "
                    f"[{label}] {detail}\n"
                )
        except Exception:
            pass

    @staticmethod
    def crc16(data, extra=0):
        crc = 0xFFFF
        for byte in data:
            tmp = byte ^ (crc & 0xFF)
            tmp = (tmp ^ (tmp << 4)) & 0xFF
            crc = (crc >> 8) ^ (tmp << 8) ^ (tmp << 3) ^ (tmp >> 4)
        if extra is not None:
            tmp = extra ^ (crc & 0xFF)
            tmp = (tmp ^ (tmp << 4)) & 0xFF
            crc = (crc >> 8) ^ (tmp << 8) ^ (tmp << 3) ^ (tmp >> 4)
        return crc & 0xFFFF

    def _send_raw(self, data):
        if self.client_addr:
            self.sock.sendto(data, self.client_addr)

    def _send_heartbeat(self):
        payload = struct.pack("<BBBBBB", 1, 3, 0, 0, 0, 3)
        crc = self.crc16(payload, extra=50)
        frame = (
            struct.pack("<BBBBBB", 0xFE, len(payload), 0, self.system_id, self.component_id, 0)
            + payload
            + struct.pack("<H", crc)
        )
        self._send_raw(frame)
        if self.log_status:
            self._print_block("发送 HEARTBEAT", [
                ("target", self.client_addr),
                ("system/component", f"{self.system_id}/{self.component_id}"),
            ])

    def _send_nest_response(self, reason="command"):
        msg = drone_nest.MAVLink_drone_nest_response_message(
            ac_temp=self.env_data["ac_temp"],
            nest_temperature=self.env_data["nest_temperature"],
            env_temperature=self.env_data["env_temperature"],
            wind_direction=self.env_data["wind_direction"],
            wind_speed=self.env_data["wind_speed"],
            atmospheric_pressure=self.env_data["atmospheric_pressure"],
            rainfall=self.env_data["rainfall"],
            charge_volt=self.env_data["charge_volt"],
            charge_current=self.env_data["charge_current"],
            error_code=self.env_data["error_code"],
            boolstate1=self.env_data["boolstate1"],
            boolstate2=self.env_data["boolstate2"],
            clamp_state=self.env_data["clamp_state"],
            gate_state=self.env_data["gate_state"],
            landing_light_state=self.env_data["landing_light_state"],
        )
        self._send_raw(msg.pack(self.mav))
        if reason == "command" or self.log_status:
            self._print_block("发送 DRONE_NEST_RESPONSE", [
                ("reason", reason),
                ("target", self.client_addr),
                ("gate_state", self.env_data["gate_state"]),
                ("clamp_state", self.env_data["clamp_state"]),
                ("charge_current", self.env_data["charge_current"]),
                ("boolstate1", f"0x{self.env_data['boolstate1']:04X}"),
                ("boolstate2", f"0x{self.env_data['boolstate2']:04X}"),
                ("error_code", f"0x{self.env_data['error_code']:04X}"),
            ])

    def _send_order_response(
        self,
        ordinary_response=0,
        takeoff_land_response=0,
        triggered_by_command=False,
        command="-",
    ):
        msg = drone_nest.MAVLink_drone_nest_order_response_message(
            ordinary_order_response=ordinary_response,
            takeoff_land_order_response=takeoff_land_response,
        )
        self._send_raw(msg.pack(self.mav))
        if triggered_by_command or self.log_status:
            self._print_block("发送 DRONE_NEST_ORDER_RESPONSE", [
                ("target", self.client_addr),
                ("ordinary_response", f"0x{ordinary_response:08X}"),
                ("takeoff_land_resp", f"0x{takeoff_land_response:08X}"),
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
        if ordinary_order == NEST_COMMANDS["open-door"][0]:
            self.env_data["gate_state"] = 1
        elif ordinary_order == NEST_COMMANDS["close-door"][0]:
            self.env_data["gate_state"] = 2
        elif ordinary_order == NEST_COMMANDS["door-stop"][0]:
            self.env_data["gate_state"] = 5
        elif ordinary_order == NEST_COMMANDS["clamp-release"][0]:
            self.env_data["clamp_state"] = 1
        elif ordinary_order == NEST_COMMANDS["clamp-tighten"][0]:
            self.env_data["clamp_state"] = 2
        elif ordinary_order == NEST_COMMANDS["clamp-stop"][0]:
            self.env_data["clamp_state"] = 5
        elif ordinary_order == NEST_COMMANDS["charge-start"][0]:
            self.env_data["charge_current"] = 50
            self.env_data["boolstate1"] |= 1 << BIT_CHARGING
        elif ordinary_order == NEST_COMMANDS["charge-stop"][0]:
            self.env_data["charge_current"] = 0
            self.env_data["boolstate1"] &= ~(1 << BIT_CHARGING)
        elif ordinary_order == NEST_COMMANDS["estop-on"][0]:
            self.env_data["boolstate1"] |= 1 << BIT_ESTOP
            self.env_data["error_code"] = 0x0001
        elif ordinary_order in (
            NEST_COMMANDS["estop-off"][0],
            NEST_COMMANDS["fault-recovery"][0],
        ):
            self.env_data["boolstate1"] &= ~(1 << BIT_ESTOP)
            self.env_data["error_code"] = 0
        elif ordinary_order == NEST_COMMANDS["initialize"][0]:
            self.env_data["boolstate1"] &= ~(1 << BIT_ESTOP)
            self.env_data["boolstate1"] |= (1 << BIT_INIT) | (1 << BIT_PLC_READY)
            self.env_data["error_code"] = 0
        elif ordinary_order == NEST_COMMANDS["light-on"][0]:
            self.env_data["landing_light_state"] = 1
            self.env_data["boolstate1"] &= ~(1 << BIT_HEADLIGHT_OFF)
        elif ordinary_order == NEST_COMMANDS["light-off"][0]:
            self.env_data["landing_light_state"] = 2
            self.env_data["boolstate1"] |= 1 << BIT_HEADLIGHT_OFF
        elif ordinary_order == NEST_COMMANDS["drone-power-on"][0]:
            self.env_data["boolstate1"] |= 1 << BIT_DRONE_HEARTBEAT
        elif ordinary_order == NEST_COMMANDS["drone-power-off"][0]:
            self.env_data["boolstate1"] &= ~(1 << BIT_DRONE_HEARTBEAT)

        if takeoff_land_order == 0x01:
            self.env_data["boolstate2"] |= 1 << BIT_TAKEOFF_IN_PROGRESS
            self.env_data["boolstate2"] &= ~(1 << BIT_TAKEOFF_ABORT_PROGRESS)
        elif takeoff_land_order == 0x02:
            self.env_data["boolstate2"] &= ~(1 << BIT_TAKEOFF_IN_PROGRESS)
            self.env_data["boolstate2"] |= 1 << BIT_TAKEOFF_ABORT_PROGRESS
        elif takeoff_land_order == NEST_COMMANDS["landing-abort"][1]:
            self.env_data["boolstate2"] &= ~(1 << BIT_LANDING_IN_PROGRESS)
            self.env_data["boolstate2"] |= 1 << BIT_LANDING_ABORT_PROGRESS

    def _ordinary_command_label(self, ordinary_order):
        if not ordinary_order:
            return "none"
        return ORDINARY_COMMAND_LABELS.get(ordinary_order, f"UNKNOWN 0x{ordinary_order:04X}")

    def _takeoff_land_command_label(self, takeoff_land_order):
        if not takeoff_land_order:
            return "none"
        return TAKEOFF_LAND_COMMAND_LABELS.get(takeoff_land_order, f"UNKNOWN 0x{takeoff_land_order:02X}")

    def _handle_message(self, msg):
        msg_type = msg.get_type()
        if msg_type == "HEARTBEAT":
            if self.log_status:
                self._print_block("收到 HEARTBEAT", [
                    ("src", f"{msg.get_srcSystem()}/{msg.get_srcComponent()}"),
                ])
            self._send_heartbeat()
        elif msg_type == "DRONE_NEST_COMMAND":
            ordinary = getattr(msg, "ordinary_order", 0)
            takeoff_land = getattr(msg, "takeoff_land_order", 0)
            has_command = bool(ordinary or takeoff_land)
            self._update_state_from_command(ordinary, takeoff_land)
            command = self._ordinary_command_label(ordinary)
            if takeoff_land:
                command = self._takeoff_land_command_label(takeoff_land)
            if has_command:
                self._trace_command(
                    "simulator_command_received",
                    command=command,
                    src=f"{msg.get_srcSystem()}/{msg.get_srcComponent()}",
                    ordinary=f"0x{ordinary:04X}",
                    takeoff_land=f"0x{takeoff_land:02X}",
                )
                self._print_block("收到 DRONE_NEST_COMMAND", [
                    ("src", f"{msg.get_srcSystem()}/{msg.get_srcComponent()}"),
                    ("ordinary", f"0x{ordinary:04X} {self._ordinary_command_label(ordinary)}"),
                    ("takeoff_land", f"0x{takeoff_land:02X} {self._takeoff_land_command_label(takeoff_land)}"),
                ])
            self._send_order_response(triggered_by_command=has_command, command=command)
            self._send_nest_response(reason="command" if has_command else "poll")

    def _periodic_status_loop(self):
        while self.running:
            time.sleep(2.0)
            if self.client_addr:
                self._send_nest_response(reason="periodic")

    def run(self):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind((self.host, self.port))
        self.sock.settimeout(0.2)
        self.mav = drone_nest.MAVLink(None, srcSystem=self.system_id, srcComponent=self.component_id)
        self.running = True

        status_thread = threading.Thread(target=self._periodic_status_loop, daemon=True)
        status_thread.start()

        print(f"机巢模拟器启动: udp://{self.host}:{self.port}")
        print("等待 HEARTBEAT 或 DRONE_NEST_COMMAND...")
        try:
            while self.running:
                try:
                    data, addr = self.sock.recvfrom(4096)
                except socket.timeout:
                    continue
                self.client_addr = addr
                for byte in data:
                    msg = self.mav.parse_char(bytes([byte]))
                    if msg:
                        self._handle_message(msg)
        finally:
            self.running = False
            self.sock.close()


def main():
    parser = argparse.ArgumentParser(description="机巢自定义 MAVLink 模拟器")
    parser.add_argument("--host", default="127.0.0.1", help="监听地址")
    parser.add_argument("--port", type=int, default=14550, help="监听端口")
    parser.add_argument("--system-id", type=int, default=101, help="模拟器 system id")
    parser.add_argument("--component-id", type=int, default=1, help="模拟器 component id")
    parser.add_argument("--log-status", action="store_true", help="打印周期状态帧")
    parser.add_argument(
        "--command-log",
        default="logs/nest_simulator_command_trace.log",
        help="只记录非心跳命令的 JSONL 日志文件；设为空字符串可关闭",
    )
    args = parser.parse_args()

    simulator = NestSimulator(
        host=args.host,
        port=args.port,
        system_id=args.system_id,
        component_id=args.component_id,
        log_status=args.log_status,
        command_log=args.command_log,
    )
    simulator.run()


if __name__ == "__main__":
    main()
