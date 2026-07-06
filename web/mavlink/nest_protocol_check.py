#!/usr/bin/env python3
"""机巢 MAVLink 常用命令语义批量检查。"""
from __future__ import annotations

import argparse
import socket
import time

from protocol import (
    BIT_CALIBRATE,
    BIT_CHARGING,
    BIT_ESTOP,
    BIT_INIT,
    BIT_LANDING_ABORT_PROGRESS,
    BIT_TAKEOFF_ABORT_PROGRESS,
    BIT_TAKEOFF_IN_PROGRESS,
    NEST_COMMANDS,
    drone_nest,
)


CHECKS = [
    ("open-door", lambda msg: msg.gate_state == 1, "舱门=打开"),
    ("close-door", lambda msg: msg.gate_state == 2, "舱门=关闭"),
    ("door-stop", lambda msg: msg.gate_state == 5, "舱门=暂停"),
    ("charge-start", lambda msg: bool(msg.boolstate1 & (1 << BIT_CHARGING)), "充电中=True"),
    ("charge-stop", lambda msg: not bool(msg.boolstate1 & (1 << BIT_CHARGING)), "充电中=False"),
    ("clamp-release", lambda msg: msg.clamp_state == 1, "夹紧=释放"),
    ("clamp-tighten", lambda msg: msg.clamp_state == 2, "夹紧=收紧"),
    ("clamp-stop", lambda msg: msg.clamp_state == 5, "夹紧=暂停"),
    ("estop-on", lambda msg: bool(msg.boolstate1 & (1 << BIT_ESTOP)) and msg.error_code == 1, "急停=True"),
    ("estop-off", lambda msg: not bool(msg.boolstate1 & (1 << BIT_ESTOP)) and msg.error_code == 0, "急停=False"),
    ("calibrate-r", lambda msg: bool(msg.boolstate1 & (1 << BIT_CALIBRATE)), "校准=True"),
    ("initialize", lambda msg: bool(msg.boolstate1 & (1 << BIT_INIT)), "初始化=True"),
    ("takeoff-start", lambda msg: bool(msg.boolstate2 & (1 << 0)), "起飞流程中=True"),
    (
        "takeoff-abort",
        lambda msg: (
            not bool(msg.boolstate2 & (1 << BIT_TAKEOFF_IN_PROGRESS))
            and bool(msg.boolstate2 & (1 << BIT_TAKEOFF_ABORT_PROGRESS))
        ),
        "起飞流程中=False, 起飞终止中=True",
    ),
    ("landing-abort", lambda msg: bool(msg.boolstate2 & (1 << BIT_LANDING_ABORT_PROGRESS)), "降落终止中=True"),
]


class UdpMavlinkWriter:
    def __init__(self, sock, target):
        self.sock = sock
        self.target = target

    def write(self, data):
        self.sock.sendto(data, self.target)


def wait_for_response(sock, mav, predicate, timeout=3.0):
    deadline = time.time() + timeout
    saw_order_response = False
    latest_status = None

    while time.time() < deadline:
        sock.settimeout(min(0.2, max(0.01, deadline - time.time())))
        try:
            data, _ = sock.recvfrom(4096)
        except socket.timeout:
            continue

        for byte in data:
            msg = mav.parse_char(bytes([byte]))
            if not msg:
                continue
            msg_type = msg.get_type()
            if msg_type == "DRONE_NEST_ORDER_RESPONSE":
                saw_order_response = True
            elif msg_type == "DRONE_NEST_RESPONSE":
                latest_status = msg
                if saw_order_response and predicate(msg):
                    return True, latest_status

    return False, latest_status


def main():
    parser = argparse.ArgumentParser(description="批量检查机巢 MAVLink 命令和状态映射")
    parser.add_argument("--host", default="127.0.0.1", help="机巢模拟器地址")
    parser.add_argument("--port", type=int, default=14550, help="机巢模拟器端口")
    args = parser.parse_args()

    target = (args.host, args.port)
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("127.0.0.1", 0))
    mav = drone_nest.MAVLink(UdpMavlinkWriter(sock, target), srcSystem=1, srcComponent=190)

    failed = []
    for command, predicate, expected in CHECKS:
        ordinary_order, takeoff_land_order = NEST_COMMANDS[command]
        print(f"检查 {command}: 期望 {expected}")
        mav.drone_nest_command_send(ordinary_order=ordinary_order, takeoff_land_order=takeoff_land_order)
        ok, status = wait_for_response(sock, mav, predicate)
        if ok:
            print("  通过")
        else:
            failed.append(command)
            if status:
                print(
                    "  失败: "
                    f"舱门={status.gate_state}, 夹紧={status.clamp_state}, "
                    f"充电中={bool(status.boolstate1 & (1 << BIT_CHARGING))}, "
                    f"急停={bool(status.boolstate1 & (1 << BIT_ESTOP))}, "
                    f"起飞流程中={bool(status.boolstate2 & (1 << 0))}"
                )
            else:
                print("  失败: 未收到 DRONE_NEST_RESPONSE")
        time.sleep(0.2)

    if failed:
        print(f"检查失败: {', '.join(failed)}")
        return 1

    print("全部通过：机巢命令含义与状态回传一致")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
