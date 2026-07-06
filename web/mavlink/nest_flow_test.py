#!/usr/bin/env python3
"""流程式机巢 MAVLink 联调脚本，风格参考 tests/test3.py。"""
from __future__ import annotations

import argparse
import socket
import threading
import time
from typing import Optional

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


class NestFlowTestClient:
    def __init__(self, host="127.0.0.1", port=14550):
        self.host = host
        self.port = port
        self.sock: Optional[socket.socket] = None
        self.mav = None
        self.running = False
        self.status_lock = threading.Lock()
        self.latest_status = {}
        self.results = {"passed": 0, "failed": 0, "skipped": 0}

    def connect(self):
        print("\n" + "=" * 60)
        print("测试步骤1：连接机巢模拟器")
        print(f"目标地址：{self.host}:{self.port}")

        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.settimeout(0.2)
        self.mav = drone_nest.MAVLink(UdpMavlinkWriter(self.sock, (self.host, self.port)), srcSystem=1, srcComponent=190)
        self.running = True

        threading.Thread(target=self._recv_loop, daemon=True).start()

        self.send_command("open-door", quiet=True)
        if self.wait_for_state(lambda s: bool(s), timeout=5):
            print("通过：已收到首次机巢状态")
            self._print_status("初始状态")
            self.results["passed"] += 1
            return True

        print("失败：未收到机巢状态，请确认已启动 web/mavlink/nest_simulator.py")
        self.results["failed"] += 1
        return False

    def _recv_loop(self):
        print("启动状态接收线程，持续监听 DRONE_NEST_RESPONSE...")
        while self.running and self.sock:
            try:
                data, _ = self.sock.recvfrom(4096)
            except socket.timeout:
                continue
            except Exception as exc:
                if self.running:
                    print(f"状态接收异常：{exc}")
                break

            for byte in data:
                msg = self.mav.parse_char(bytes([byte]))
                if not msg:
                    continue
                msg_type = msg.get_type()
                if msg_type == "DRONE_NEST_RESPONSE":
                    with self.status_lock:
                        self.latest_status = {
                            "ac_temp": msg.ac_temp / 10.0,
                            "nest_temperature": msg.nest_temperature / 10.0,
                            "env_temperature": msg.env_temperature / 10.0,
                            "wind_direction": msg.wind_direction / 100.0,
                            "wind_speed": msg.wind_speed / 10.0,
                            "atmospheric_pressure": msg.atmospheric_pressure / 10.0,
                            "rainfall": msg.rainfall / 10.0,
                            "charge_volt": msg.charge_volt / 100.0,
                            "charge_current": msg.charge_current / 100.0,
                            "error_code": msg.error_code,
                            "boolstate1": msg.boolstate1,
                            "boolstate2": msg.boolstate2,
                            "clamp_state": msg.clamp_state,
                            "gate_state": msg.gate_state,
                            "landing_light_state": msg.landing_light_state,
                            "nest_heartbeat": bool(msg.boolstate1 & (1 << BIT_NEST_HEARTBEAT)),
                            "drone_heartbeat": bool(msg.boolstate1 & (1 << BIT_DRONE_HEARTBEAT)),
                            "plc_heartbeat": bool(msg.boolstate1 & (1 << BIT_PLC_HEARTBEAT)),
                            "estop_state": bool(msg.boolstate1 & (1 << BIT_ESTOP)),
                            "charging_state": bool(msg.boolstate1 & (1 << BIT_CHARGING)),
                            "calibrate_state": bool(msg.boolstate1 & (1 << BIT_CALIBRATE)),
                            "plc_ready": bool(msg.boolstate1 & (1 << BIT_PLC_READY)),
                            "init_state": bool(msg.boolstate1 & (1 << BIT_INIT)),
                            "takeoff_in_progress": bool(msg.boolstate2 & (1 << BIT_TAKEOFF_IN_PROGRESS)),
                            "landing_in_progress": bool(msg.boolstate2 & (1 << BIT_LANDING_IN_PROGRESS)),
                            "takeoff_abort_progress": bool(msg.boolstate2 & (1 << BIT_TAKEOFF_ABORT_PROGRESS)),
                            "landing_abort_progress": bool(msg.boolstate2 & (1 << BIT_LANDING_ABORT_PROGRESS)),
                            "emergency_landing": bool(msg.boolstate2 & (1 << BIT_EMERGENCY_LANDING)),
                            "allow_takeoff": bool(msg.boolstate2 & (1 << BIT_ALLOW_TAKEOFF)),
                        }
                elif msg_type == "DRONE_NEST_ORDER_RESPONSE":
                    print(
                        "收到命令回复："
                        f"ordinary=0x{msg.ordinary_order_response:08X}, "
                        f"takeoff_land=0x{msg.takeoff_land_order_response:08X}"
                    )

    def send_command(self, command, quiet=False):
        ordinary_order, takeoff_land_order = NEST_COMMANDS[command]
        self.mav.drone_nest_command_send(ordinary_order=ordinary_order, takeoff_land_order=takeoff_land_order)
        if not quiet:
            print(f"发送指令：{command} | ordinary=0x{ordinary_order:04X}, takeoff_land=0x{takeoff_land_order:02X}")

    def wait_for_state(self, check_func, timeout=5, interval=0.2):
        start = time.time()
        while time.time() - start < timeout:
            with self.status_lock:
                snapshot = dict(self.latest_status)
            if check_func(snapshot):
                return True
            time.sleep(interval)
        return False

    def _print_status(self, label):
        with self.status_lock:
            status = dict(self.latest_status)
        if not status:
            print(f"{label}：暂无状态")
            return

        print(f"\n{label}：")
        print(f"  环境：机巢温度={status['nest_temperature']}℃ | 环境温度={status['env_temperature']}℃")
        print(f"  气象：风速={status['wind_speed']}m/s | 风向={status['wind_direction']}° | 气压={status['atmospheric_pressure']}kPa")
        print(f"  充电：电压={status['charge_volt']}V | 电流={status['charge_current']}A | 充电中={status['charging_state']}")
        print(
            f"  机构：舱门={self._gate_text(status['gate_state'])} | "
            f"夹紧={self._clamp_text(status['clamp_state'])} | "
            f"着陆灯={self._light_text(status['landing_light_state'])}"
        )
        print(
            f"  心跳：机巢={status['nest_heartbeat']} | "
            f"无人机={status['drone_heartbeat']} | PLC={status['plc_heartbeat']}"
        )
        print(
            f"  流程：急停={status['estop_state']} | 校准={status['calibrate_state']} | 初始化={status['init_state']} | "
            f"起飞中={status['takeoff_in_progress']} | 降落中={status['landing_in_progress']} | "
            f"起飞终止中={status['takeoff_abort_progress']} | 降落终止中={status['landing_abort_progress']} | "
            f"紧急降落={status['emergency_landing']}"
        )
        print(
            f"  系统：PLC就绪={status['plc_ready']} | 允许起飞={status['allow_takeoff']} | "
            f"状态位1={hex(status['boolstate1'])} | 错误码={hex(status['error_code'])}"
        )

    @staticmethod
    def _gate_text(value):
        return {1: "打开", 2: "关闭", 3: "开启中", 4: "关闭中", 5: "暂停", 6: "故障"}.get(value, f"未知({value})")

    @staticmethod
    def _clamp_text(value):
        return {1: "释放", 2: "夹紧", 3: "释放中", 4: "夹紧中", 5: "暂停", 6: "故障"}.get(value, f"未知({value})")

    @staticmethod
    def _light_text(value):
        return {0: "默认/关闭", 1: "打开", 2: "关闭", 6: "故障"}.get(value, f"未知({value})")

    def _case(self, title, command, predicate, timeout=5):
        print(f"\n--- {title} ---")
        self.send_command(command)
        if self.wait_for_state(predicate, timeout=timeout):
            print(f"通过：{title}")
            self.results["passed"] += 1
        else:
            print(f"失败：{title} 超时/状态不匹配")
            self.results["failed"] += 1
        self._print_status(f"{title}后状态")

    def test_single_commands(self):
        print("\n" + "=" * 60)
        print("测试步骤2：单指令功能验证")

        self._case("舱门打开", "open-door", lambda s: s.get("gate_state") == 1)
        self._case("舱门关闭", "close-door", lambda s: s.get("gate_state") == 2)
        self._case("舱门停止", "door-stop", lambda s: s.get("gate_state") == 5)
        self._case("夹紧释放", "clamp-release", lambda s: s.get("clamp_state") == 1)
        self._case("夹紧收紧", "clamp-tighten", lambda s: s.get("clamp_state") == 2)
        self._case("夹紧停止", "clamp-stop", lambda s: s.get("clamp_state") == 5)
        self._case("充电启动", "charge-start", lambda s: s.get("charging_state") is True)
        self._case("充电停止", "charge-stop", lambda s: s.get("charging_state") is False)
        self._case("急停触发", "estop-on", lambda s: s.get("estop_state") is True and s.get("error_code") == 1)
        self._case("急停解除", "estop-off", lambda s: s.get("estop_state") is False and s.get("error_code") == 0)
        self._case("R轴校准", "calibrate-r", lambda s: s.get("calibrate_state") is True)
        self._case("设备初始化", "initialize", lambda s: s.get("init_state") is True and s.get("plc_ready") is True)

    def test_workflow(self):
        print("\n" + "=" * 60)
        print("测试步骤3：组合业务流程验证")

        steps = [
            ("停止充电", "charge-stop", lambda s: s.get("charging_state") is False),
            ("打开舱门", "open-door", lambda s: s.get("gate_state") == 1),
            ("释放夹紧", "clamp-release", lambda s: s.get("clamp_state") == 1),
            ("请求起飞流程", "takeoff-start", lambda s: s.get("takeoff_in_progress") is True),
            ("终止起飞流程", "takeoff-abort", lambda s: s.get("takeoff_in_progress") is False and s.get("takeoff_abort_progress") is True),
            ("终止降落流程", "landing-abort", lambda s: s.get("landing_abort_progress") is True),
            ("夹紧收紧", "clamp-tighten", lambda s: s.get("clamp_state") == 2),
            ("关闭舱门", "close-door", lambda s: s.get("gate_state") == 2),
            ("启动充电", "charge-start", lambda s: s.get("charging_state") is True),
        ]

        for title, command, predicate in steps:
            self._case(title, command, predicate)
            if self.results["failed"] > 0:
                print("组合流程已有失败项，继续执行后续步骤以保留完整日志")

    def run(self):
        print("模拟机巢流程式联调开始")
        print(f"测试时间：{time.strftime('%Y-%m-%d %H:%M:%S')}")

        if not self.connect():
            return

        self.test_single_commands()
        self.test_workflow()
        self.summary()

    def summary(self):
        print("\n" + "=" * 60)
        print("测试结果总结")
        print(f"通过用例：{self.results['passed']}")
        print(f"失败用例：{self.results['failed']}")
        print(f"跳过用例：{self.results['skipped']}")
        total = sum(self.results.values())
        pass_rate = self.results["passed"] / total * 100 if total else 0
        print(f"通过率：{pass_rate:.1f}%")
        if self.results["failed"] == 0:
            print("所有测试用例通过")
        else:
            print("存在失败用例，请检查机巢模拟器状态和日志")

    def close(self):
        self.running = False
        if self.sock:
            self.sock.close()
        print("\n测试客户端已关闭")


def main():
    parser = argparse.ArgumentParser(description="流程式机巢 MAVLink 联调测试")
    parser.add_argument("--host", default="127.0.0.1", help="机巢模拟器地址")
    parser.add_argument("--port", type=int, default=14550, help="机巢模拟器端口")
    args = parser.parse_args()

    client = NestFlowTestClient(host=args.host, port=args.port)
    try:
        client.run()
    except KeyboardInterrupt:
        print("\n用户中断测试")
    finally:
        client.close()


if __name__ == "__main__":
    main()
