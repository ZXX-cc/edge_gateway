#!/usr/bin/env python3
"""基于 UDP 的机巢自定义 MAVLink 模拟器。"""
from __future__ import annotations

import argparse
import socket
import struct
import threading
import time

from protocol import (
    BIT_CALIBRATE,
    BIT_CHARGING,
    BIT_ESTOP,
    BIT_INIT,
    BIT_LANDING_ABORT_PROGRESS,
    BIT_LANDING_IN_PROGRESS,
    BIT_NEST_HEARTBEAT,
    BIT_DRONE_HEARTBEAT,
    NEST_COMMANDS,
    ORDINARY_COMMAND_LABELS,
    BIT_PLC_HEARTBEAT,
    BIT_PLC_READY,
    BIT_TAKEOFF_ABORT_PROGRESS,
    BIT_TAKEOFF_IN_PROGRESS,
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
    ):
        self.host = host
        self.port = port
        self.system_id = system_id
        self.component_id = component_id
        self.log_status = log_status
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
        if reason != "periodic" or self.log_status:
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

    def _send_order_response(self, ordinary_response=0, takeoff_land_response=0):
        msg = drone_nest.MAVLink_drone_nest_order_response_message(
            ordinary_order_response=ordinary_response,
            takeoff_land_order_response=takeoff_land_response,
        )
        self._send_raw(msg.pack(self.mav))
        self._print_block("发送 DRONE_NEST_ORDER_RESPONSE", [
            ("target", self.client_addr),
            ("ordinary_response", f"0x{ordinary_response:08X}"),
            ("takeoff_land_resp", f"0x{takeoff_land_response:08X}"),
        ])

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
        elif ordinary_order == NEST_COMMANDS["calibrate-r"][0]:
            self.env_data["boolstate1"] |= 1 << BIT_CALIBRATE
        elif ordinary_order == NEST_COMMANDS["initialize"][0]:
            self.env_data["boolstate1"] &= ~(1 << BIT_ESTOP)
            self.env_data["boolstate1"] |= (1 << BIT_INIT) | (1 << BIT_PLC_READY)
            self.env_data["error_code"] = 0

        if takeoff_land_order == 0x01:
            self.env_data["boolstate2"] |= 1 << BIT_TAKEOFF_IN_PROGRESS
            self.env_data["boolstate2"] &= ~(1 << BIT_TAKEOFF_ABORT_PROGRESS)
        elif takeoff_land_order == 0x02:
            self.env_data["boolstate2"] &= ~(1 << BIT_TAKEOFF_IN_PROGRESS)
            self.env_data["boolstate2"] |= 1 << BIT_TAKEOFF_ABORT_PROGRESS
        elif takeoff_land_order == 0x04:
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
            self._print_block("收到 HEARTBEAT", [
                ("src", f"{msg.get_srcSystem()}/{msg.get_srcComponent()}"),
            ])
            self._send_heartbeat()
        elif msg_type == "DRONE_NEST_COMMAND":
            ordinary = getattr(msg, "ordinary_order", 0)
            takeoff_land = getattr(msg, "takeoff_land_order", 0)
            self._update_state_from_command(ordinary, takeoff_land)
            self._print_block("收到 DRONE_NEST_COMMAND", [
                ("src", f"{msg.get_srcSystem()}/{msg.get_srcComponent()}"),
                ("ordinary_order", f"0x{ordinary:04X}"),
                ("ordinary_name", self._ordinary_command_label(ordinary)),
                ("takeoff_land", f"0x{takeoff_land:02X}"),
                ("takeoff_name", self._takeoff_land_command_label(takeoff_land)),
                ("gate_state", self.env_data["gate_state"]),
                ("clamp_state", self.env_data["clamp_state"]),
                ("charge_current", self.env_data["charge_current"]),
                ("boolstate1", f"0x{self.env_data['boolstate1']:04X}"),
                ("boolstate2", f"0x{self.env_data['boolstate2']:04X}"),
                ("error_code", f"0x{self.env_data['error_code']:04X}"),
            ])
            self._send_order_response(0, 0)
            self._send_nest_response(reason="command")
        elif not msg_type.startswith("UNKNOWN_"):
            self._print_block("收到 MAVLink 消息", [
                ("type", msg_type),
                ("src", f"{msg.get_srcSystem()}/{msg.get_srcComponent()}"),
            ])

    def _describe_datagram(self, data, addr):
        if not data:
            return

        magic = data[0]
        if magic == 0xFD and len(data) >= 10:
            msg_id = data[7] | (data[8] << 8) | (data[9] << 16)
            rows = [
                ("from", addr),
                ("length", len(data)),
                ("mavlink", "2"),
                ("msgid", msg_id),
            ]
            if msg_id == drone_nest.MAVLINK_MSG_ID_DRONE_NEST_COMMAND:
                rows.append(("message", "DRONE_NEST_COMMAND"))
                rows.append(("confirm", "command frame arrived"))
                self._print_block("收到 UDP 包", rows)
            return

        if magic == 0xFE and len(data) >= 6:
            msg_id = data[5]
            if msg_id != 0:
                self._print_block("收到 UDP 包", [
                    ("from", addr),
                    ("length", len(data)),
                    ("mavlink", "1"),
                    ("msgid", msg_id),
                ])
            return

        self._print_block("收到非 MAVLink UDP 包", [
            ("from", addr),
            ("length", len(data)),
            ("magic", f"0x{magic:02X}"),
        ])

    def _status_loop(self):
        while self.running:
            if self.client_addr:
                self._send_nest_response(reason="periodic")
            time.sleep(2)

    def _receive_loop(self):
        print("模拟器等待 MAVLink 消息...")
        while self.running:
            try:
                data, addr = self.sock.recvfrom(4096)
                self._describe_datagram(data, addr)
                if self.client_addr != addr:
                    self.client_addr = addr
                    self._print_block("注册客户端地址", [
                        ("client", addr),
                    ])
                    self._send_heartbeat()
                for byte in data:
                    msg = self.mav.parse_char(bytes([byte]))
                    if msg:
                        self._handle_message(msg)
            except socket.timeout:
                continue
            except Exception as exc:
                print(f"接收错误: {exc}")
                time.sleep(0.1)

    def start(self):
        print(f"启动 MAVLink 机巢模拟器 (UDP)，监听 {self.host}:{self.port}")
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind((self.host, self.port))
        self.sock.settimeout(0.5)
        self.running = True

        class DummyFile:
            def write(self, data):
                pass

        self.mav = drone_nest.MAVLink(DummyFile(), srcSystem=self.system_id, srcComponent=self.component_id)
        threading.Thread(target=self._status_loop, daemon=True).start()
        self._receive_loop()

    def stop(self):
        self.running = False
        if self.sock:
            self.sock.close()
        print("模拟器已停止")


def main():
    parser = argparse.ArgumentParser(description="机巢 MAVLink 模拟器")
    parser.add_argument("--host", default="127.0.0.1", help="UDP 监听地址")
    parser.add_argument("--port", type=int, default=14550, help="UDP 监听端口")
    parser.add_argument(
        "--log-status",
        action="store_true",
        help="打印周期性 DRONE_NEST_RESPONSE，上报调试时使用"
    )
    args = parser.parse_args()

    sim = NestSimulator(host=args.host, port=args.port, log_status=args.log_status)
    try:
        sim.start()
    except KeyboardInterrupt:
        print("\n用户中断")
        sim.stop()


if __name__ == "__main__":
    main()
