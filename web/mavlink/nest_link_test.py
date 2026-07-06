#!/usr/bin/env python3
"""网关侧机巢 MAVLink 最小联通测试。"""
from __future__ import annotations

import argparse
import socket
import time

from protocol import (
    BIT_ALLOW_TAKEOFF,
    BIT_CALIBRATE,
    BIT_CHARGING,
    BIT_DRONE_HEARTBEAT,
    BIT_EMERGENCY_LANDING,
    BIT_ESTOP,
    BIT_INIT,
    BIT_LANDING_ABORT_PROGRESS,
    BIT_LANDING_IN_PROGRESS,
    BIT_NEST_HEARTBEAT,
    BIT_PLC_HEARTBEAT,
    BIT_PLC_READY,
    BIT_TAKEOFF_ABORT_PROGRESS,
    BIT_TAKEOFF_IN_PROGRESS,
    NEST_COMMANDS,
    drone_nest,
)


class UdpMavlinkWriter:
    def __init__(self, sock, target):
        self.sock = sock
        self.target = target

    def write(self, data):
        self.sock.sendto(data, self.target)


def recv_messages(sock, mav, timeout):
    deadline = time.time() + timeout
    messages = []
    while time.time() < deadline:
        remaining = max(0.01, deadline - time.time())
        sock.settimeout(min(0.2, remaining))
        try:
            data, _ = sock.recvfrom(4096)
        except socket.timeout:
            continue

        for byte in data:
            msg = mav.parse_char(bytes([byte]))
            if not msg:
                continue
            messages.append(msg)
            msg_type = msg.get_type()
            if msg_type == "DRONE_NEST_RESPONSE":
                print(
                    "收到 DRONE_NEST_RESPONSE: "
                    f"空调={msg.ac_temp / 10.0:.1f}℃, "
                    f"机巢温度={msg.nest_temperature / 10.0:.1f}℃, "
                    f"环境温度={msg.env_temperature / 10.0:.1f}℃, "
                    f"风向={msg.wind_direction / 100.0:.2f}°, "
                    f"风速={msg.wind_speed / 10.0:.1f}m/s, "
                    f"气压={msg.atmospheric_pressure / 10.0:.1f}kPa, "
                    f"降雨={msg.rainfall / 10.0:.1f}mm/m², "
                    f"充电电压={msg.charge_volt / 100.0:.2f}V, "
                    f"充电电流={msg.charge_current / 100.0:.2f}A, "
                    f"错误码=0x{msg.error_code:04X}, "
                    f"舱门={msg.gate_state}, 夹紧={msg.clamp_state}, "
                    f"着陆灯={msg.landing_light_state}"
                )
                print(
                    "  boolstate1: "
                    f"机巢心跳={bool(msg.boolstate1 & (1 << BIT_NEST_HEARTBEAT))}, "
                    f"无人机心跳={bool(msg.boolstate1 & (1 << BIT_DRONE_HEARTBEAT))}, "
                    f"PLC心跳={bool(msg.boolstate1 & (1 << BIT_PLC_HEARTBEAT))}, "
                    f"急停={bool(msg.boolstate1 & (1 << BIT_ESTOP))}, "
                    f"充电中={bool(msg.boolstate1 & (1 << BIT_CHARGING))}, "
                    f"校准={bool(msg.boolstate1 & (1 << BIT_CALIBRATE))}, "
                    f"PLC就绪={bool(msg.boolstate1 & (1 << BIT_PLC_READY))}, "
                    f"初始化={bool(msg.boolstate1 & (1 << BIT_INIT))}"
                )
                print(
                    "  boolstate2: "
                    f"起飞中={bool(msg.boolstate2 & (1 << BIT_TAKEOFF_IN_PROGRESS))}, "
                    f"降落中={bool(msg.boolstate2 & (1 << BIT_LANDING_IN_PROGRESS))}, "
                    f"起飞终止中={bool(msg.boolstate2 & (1 << BIT_TAKEOFF_ABORT_PROGRESS))}, "
                    f"降落终止中={bool(msg.boolstate2 & (1 << BIT_LANDING_ABORT_PROGRESS))}, "
                    f"紧急降落={bool(msg.boolstate2 & (1 << BIT_EMERGENCY_LANDING))}, "
                    f"允许起飞={bool(msg.boolstate2 & (1 << BIT_ALLOW_TAKEOFF))}"
                )
            elif msg_type == "DRONE_NEST_ORDER_RESPONSE":
                ordinary_reply = msg.ordinary_order_response & 0xFF
                ordinary_reason = (msg.ordinary_order_response >> 8) & 0xFF
                ordinary_command = (msg.ordinary_order_response >> 16) & 0xFF
                ordinary_device = (msg.ordinary_order_response >> 24) & 0xFF
                takeoff_reply = msg.takeoff_land_order_response & 0xFF
                takeoff_reason = (msg.takeoff_land_order_response >> 8) & 0xFF
                takeoff_command = (msg.takeoff_land_order_response >> 24) & 0xFF
                print(
                    "收到 DRONE_NEST_ORDER_RESPONSE: "
                    f"普通命令回复=0x{msg.ordinary_order_response:08X}, "
                    f"起降命令回复=0x{msg.takeoff_land_order_response:08X}"
                )
                print(
                    "  普通命令位域: "
                    f"reply_status={ordinary_reply}, "
                    f"reject_reason_code={ordinary_reason}, "
                    f"command_code={ordinary_command}, "
                    f"device_code={ordinary_device}"
                )
                print(
                    "  起降命令位域: "
                    f"reply_status={takeoff_reply}, "
                    f"reject_reason_code={takeoff_reason}, "
                    f"command_code={takeoff_command}"
                )
            else:
                print(f"收到 {msg_type}")
    return messages


def main():
    parser = argparse.ArgumentParser(description="测试机巢 MAVLink 模拟器收发链路")
    parser.add_argument("command", nargs="?", choices=sorted(NEST_COMMANDS), default="open-door")
    parser.add_argument("--host", default="127.0.0.1", help="机巢模拟器地址")
    parser.add_argument("--port", type=int, default=14550, help="机巢模拟器端口")
    parser.add_argument("--listen-seconds", type=float, default=5.0, help="等待回包秒数")
    args = parser.parse_args()

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("127.0.0.1", 0))

    mav = drone_nest.MAVLink(UdpMavlinkWriter(sock, (args.host, args.port)), srcSystem=1, srcComponent=190)
    ordinary_order, takeoff_land_order = NEST_COMMANDS[args.command]
    print(
        f"发送 DRONE_NEST_COMMAND 到 {args.host}:{args.port}: "
        f"ordinary=0x{ordinary_order:04X}, takeoff_land=0x{takeoff_land_order:02X}"
    )
    mav.drone_nest_command_send(ordinary_order=ordinary_order, takeoff_land_order=takeoff_land_order)

    messages = recv_messages(sock, mav, args.listen_seconds)
    types = {msg.get_type() for msg in messages}
    missing = {"DRONE_NEST_ORDER_RESPONSE", "DRONE_NEST_RESPONSE"} - types
    if missing:
        print(f"测试未完全通过，缺少消息: {', '.join(sorted(missing))}")
        return 1

    print("测试通过：12003 -> 12005 -> 12004 链路已打通")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
