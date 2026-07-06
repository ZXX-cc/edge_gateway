"""
模拟机巢全流程测试脚本
测试覆盖：连接建立、状态接收、单指令执行、组合业务流程、断开重连
前置条件：先启动 test6.py 中的 MockDroneNest 服务
"""
import sys
import os
import time
import socket
import threading
from typing import Dict, List, Optional

# ========== 路径修正：自动定位项目根目录（src 所在目录） ==========
current_script_path = os.path.abspath(__file__)
tests_dir = os.path.dirname(current_script_path)
project_root = os.path.dirname(tests_dir)
if project_root not in sys.path:
    sys.path.insert(0, project_root)
# ==================================================================

from src.generated_dialect import drone_nest
from src.generated_dialect.gcs_order_protocol import GCSCommand, GCSOrderCodec


class NestTestClient:
    def __init__(self, host="127.0.0.1", port=5760):
        self.host = host
        self.port = port
        self.client_sock: Optional[socket.socket] = None
        self.mav: Optional[drone_nest.MAVLink] = None
        self.running = False

        self.latest_nest_status: Dict = {}
        self.status_lock = threading.Lock()

        self.codec = GCSOrderCodec()

        self.test_results = {
            "passed": 0,
            "failed": 0,
            "skipped": 0
        }

    def connect(self, timeout=10) -> bool:
        print("\n" + "="*60)
        print("🔌 测试步骤1：连接模拟机巢服务端")
        print(f"目标地址：{self.host}:{self.port}")
        print(f"超时时间：{timeout}秒")

        start_time = time.time()
        while time.time() - start_time < timeout:
            try:
                self.client_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                self.client_sock.settimeout(5)
                self.client_sock.connect((self.host, self.port))
                print("✅ 成功建立TCP连接")

                file_obj = self.client_sock.makefile('rwb', buffering=0)
                self.mav = drone_nest.MAVLink(file_obj, srcSystem=1, srcComponent=1)
                self.running = True

                recv_thread = threading.Thread(target=self._recv_status_loop, daemon=True)
                recv_thread.start()

                wait_time = 0
                while wait_time < 3 and not self.latest_nest_status:
                    time.sleep(0.5)
                    wait_time += 0.5

                if self.latest_nest_status:
                    print("✅ 成功接收模拟机巢首次状态推送")
                    self._print_status("初始状态")
                    self.test_results["passed"] += 1
                    return True
                else:
                    print("❌ 连接成功但未收到状态推送")
                    self.client_sock.close()
                    self.client_sock = None
                    self.mav = None
                    self.running = False
                    self.test_results["failed"] += 1
                    return False

            except socket.timeout:
                print("⏳ 连接超时，重试中...")
                time.sleep(1)
            except Exception as e:
                print(f"❌ 连接失败：{e}")
                time.sleep(1)

        print("❌ 连接超时，模拟机巢服务可能未启动？")
        self.test_results["failed"] += 1
        return False

    def _recv_status_loop(self):
        print("📡 启动状态接收线程，持续监听机巢状态...")
        while self.running and self.client_sock:
            try:
                data = self.client_sock.recv(4096)
                if not data:
                    print("⚠️  机巢连接断开，停止状态接收")
                    self.running = False
                    break

                for byte in data:
                    msg = self.mav.parse_char(bytes([byte]))
                    if msg and msg.get_type() == "DRONE_NEST_RESPONSE":
                        with self.status_lock:
                            self.latest_nest_status = {
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
                                "estop_state": bool(msg.boolstate1 & (1 << 3)),
                                "charging_state": bool(msg.boolstate1 & (1 << 4)),
                                "plc_ready": bool(msg.boolstate1 & (1 << 6)),
                                "takeoff_in_progress": bool(msg.boolstate2 & (1 << 0)),
                                "landing_in_progress": bool(msg.boolstate2 & (1 << 1)),
                                "allow_takeoff": bool(msg.boolstate2 & (1 << 5))
                            }
            except Exception as e:
                print(f"❌ 状态接收异常：{e}")
                self.running = False
                break

    def _print_status(self, label: str = "当前状态"):
        with self.status_lock:
            if not self.latest_nest_status:
                print(f"⚠️  {label}：暂无状态数据")
                return

            status = self.latest_nest_status
            print(f"\n📊 {label}：")
            print(f"  基础环境：机巢温度={status['nest_temperature']}℃ | 环境温度={status['env_temperature']}℃")
            print(f"  气象数据：风速={status['wind_speed']}m/s | 风向={status['wind_direction']}° | 气压={status['atmospheric_pressure']}kPa")
            print(f"  充电状态：电压={status['charge_volt']}V | 电流={status['charge_current']}A | 充电中={status['charging_state']}")
            print(f"  硬件状态：舱门={self._get_gate_text(status['gate_state'])} | 夹紧={self._get_clamp_text(status['clamp_state'])}")
            print(f"  作业状态：急停={status['estop_state']} | 起飞中={status['takeoff_in_progress']} | 降落中={status['landing_in_progress']}")
            print(f"  系统状态：PLC就绪={status['plc_ready']} | 允许起飞={status['allow_takeoff']} | 错误码={hex(status['error_code'])}")

    @staticmethod
    def _get_gate_text(gate_state: int) -> str:
        state_map = {
            1: "打开",
            2: "关闭",
            3: "开启中",
            4: "关闭中"
        }
        return state_map.get(gate_state, f"未知({gate_state})")

    @staticmethod
    def _get_clamp_text(clamp_state: int) -> str:
        state_map = {
            1: "释放",
            2: "夹紧",
            3: "释放中",
            4: "夹紧中"
        }
        return state_map.get(clamp_state, f"未知({clamp_state})")

    def send_nest_command(self, commands: List[GCSCommand]) -> bool:
        if not self.mav or not self.running:
            print("❌ 未建立有效连接，无法发送指令")
            return False

        # ✅ 修复：对命令列表解包，匹配 encode_gcs_command 的可变参数设计
        gcs_order = self.codec.encode_gcs_command(*commands)
        ordinary_order = (gcs_order >> 8) & 0xFFFF
        takeoff_land_order = gcs_order & 0xFF

        try:
            self.mav.drone_nest_command_send(
                ordinary_order=ordinary_order,
                takeoff_land_order=takeoff_land_order
            )
            cmd_names = [cmd.name for cmd in commands]
            print(f"\n📤 发送指令：{cmd_names} | gcs_order=0x{gcs_order:08X}")
            return True
        except Exception as e:
            print(f"❌ 指令发送失败：{e}")
            return False

    def wait_for_state(self, check_func, timeout=10, interval=0.5) -> bool:
        start_time = time.time()
        while time.time() - start_time < timeout:
            with self.status_lock:
                if check_func(self.latest_nest_status):
                    return True
            time.sleep(interval)
        return False

    def test_single_commands(self):
        print("\n" + "="*60)
        print("🧪 测试步骤2：单指令功能验证")

        print("\n--- 子测试2.1：舱门打开 ---")
        self.send_nest_command([GCSCommand.DOOR_OPEN])
        if self.wait_for_state(lambda s: s.get("gate_state") == 1, timeout=5):
            print("✅ 舱门打开成功")
            self.test_results["passed"] += 1
        else:
            print("❌ 舱门打开超时/失败")
            self.test_results["failed"] += 1
        self._print_status("舱门打开后状态")

        print("\n--- 子测试2.2：舱门关闭 ---")
        self.send_nest_command([GCSCommand.DOOR_CLOSE])
        if self.wait_for_state(lambda s: s.get("gate_state") == 2, timeout=5):
            print("✅ 舱门关闭成功")
            self.test_results["passed"] += 1
        else:
            print("❌ 舱门关闭超时/失败")
            self.test_results["failed"] += 1
        self._print_status("舱门关闭后状态")

        print("\n--- 子测试2.3：夹紧释放 ---")
        self.send_nest_command([GCSCommand.CLAMP_RELEASE])
        if self.wait_for_state(lambda s: s.get("clamp_state") == 1, timeout=4):
            print("✅ 夹紧释放成功")
            self.test_results["passed"] += 1
        else:
            print("❌ 夹紧释放超时/失败")
            self.test_results["failed"] += 1
        self._print_status("夹紧释放后状态")

        print("\n--- 子测试2.4：夹紧收紧 ---")
        self.send_nest_command([GCSCommand.CLAMP_TIGHTEN])
        if self.wait_for_state(lambda s: s.get("clamp_state") == 2, timeout=4):
            print("✅ 夹紧收紧成功")
            self.test_results["passed"] += 1
        else:
            print("❌ 夹紧收紧超时/失败")
            self.test_results["failed"] += 1
        self._print_status("夹紧收紧后状态")

        print("\n--- 子测试2.5：充电启动 ---")
        self.send_nest_command([GCSCommand.CHARGE_START])
        if self.wait_for_state(lambda s: s.get("charging_state") is True, timeout=3):
            print("✅ 充电启动成功")
            self.test_results["passed"] += 1
        else:
            print("❌ 充电启动超时/失败")
            self.test_results["failed"] += 1
        self._print_status("充电启动后状态")

        print("\n--- 子测试2.6：充电停止 ---")
        self.send_nest_command([GCSCommand.CHARGE_STOP])
        if self.wait_for_state(lambda s: s.get("charging_state") is False, timeout=3):
            print("✅ 充电停止成功")
            self.test_results["passed"] += 1
        else:
            print("❌ 充电停止超时/失败")
            self.test_results["failed"] += 1
        self._print_status("充电停止后状态")

        print("\n--- 子测试2.7：急停触发 ---")
        self.send_nest_command([GCSCommand.ESTOP_ACTIVATE])
        if self.wait_for_state(lambda s: s.get("estop_state") is True and s.get("error_code") == 0x0001, timeout=3):
            print("✅ 急停触发成功")
            self.test_results["passed"] += 1
        else:
            print("❌ 急停触发超时/失败")
            self.test_results["failed"] += 1
        self._print_status("急停触发后状态")

    def test_combined_workflow(self):
        print("\n" + "="*60)
        print("🔄 测试步骤3：组合业务流程验证（真实作业场景）")

        print("\n--- 子测试3.0：重置急停状态 ---")
        print("提示：请在 test6.py 运行窗口中，手动将急停状态复原")
        print("      修改 self.estop_state = False, self.error_code = 0")
        input("修改完成后按回车继续...")

        if self.wait_for_state(lambda s: s.get("estop_state") is False and s.get("error_code") == 0, timeout=10):
            print("✅ 急停状态已重置")
        else:
            print("❌ 急停状态重置失败，跳过组合流程测试")
            self.test_results["skipped"] += 1
            return

        print("\n--- 子测试3.1：打开舱门 ---")
        self.send_nest_command([GCSCommand.DOOR_OPEN])
        if self.wait_for_state(lambda s: s.get("gate_state") == 1, timeout=5):
            print("✅ 舱门打开成功")
        else:
            print("❌ 舱门打开失败，终止组合流程")
            self.test_results["failed"] += 1
            return

        print("\n--- 子测试3.2：释放夹紧 ---")
        self.send_nest_command([GCSCommand.CLAMP_RELEASE])
        if self.wait_for_state(lambda s: s.get("clamp_state") == 1, timeout=4):
            print("✅ 夹紧释放成功")
        else:
            print("❌ 夹紧释放失败，终止组合流程")
            self.test_results["failed"] += 1
            return

        print("\n--- 子测试3.3：停止充电 ---")
        self.send_nest_command([GCSCommand.CHARGE_STOP])
        if self.wait_for_state(lambda s: s.get("charging_state") is False, timeout=3):
            print("✅ 充电停止成功")
        else:
            print("❌ 充电停止失败，终止组合流程")
            self.test_results["failed"] += 1
            return

        print("\n--- 子测试3.4：模拟起飞中 ---")
        print("请修改 test6.py 中 self.takeoff_in_progress = True")
        input("修改完成后按回车继续...")
        if self.wait_for_state(lambda s: s.get("takeoff_in_progress") is True, timeout=10):
            print("✅ 起飞中状态已同步")
        else:
            print("❌ 起飞中状态同步失败，终止组合流程")
            self.test_results["failed"] += 1
            return

        print("\n--- 子测试3.5：模拟起飞完成 ---")
        print("请修改 test6.py 中 self.takeoff_in_progress = False")
        input("修改完成后按回车继续...")
        if self.wait_for_state(lambda s: s.get("takeoff_in_progress") is False, timeout=10):
            print("✅ 起飞完成状态已同步")
        else:
            print("❌ 起飞完成状态同步失败，终止组合流程")
            self.test_results["failed"] += 1
            return

        print("\n--- 子测试3.6：模拟降落中 ---")
        print("请修改 test6.py 中 self.landing_in_progress = True")
        input("修改完成后按回车继续...")
        if self.wait_for_state(lambda s: s.get("landing_in_progress") is True, timeout=10):
            print("✅ 降落中状态已同步")
        else:
            print("❌ 降落中状态同步失败，终止组合流程")
            self.test_results["failed"] += 1
            return

        print("\n--- 子测试3.7：收紧夹紧 ---")
        self.send_nest_command([GCSCommand.CLAMP_TIGHTEN])
        if self.wait_for_state(lambda s: s.get("clamp_state") == 2, timeout=4):
            print("✅ 夹紧收紧成功")
        else:
            print("❌ 夹紧收紧失败，终止组合流程")
            self.test_results["failed"] += 1
            return

        print("\n--- 子测试3.8：关闭舱门 ---")
        self.send_nest_command([GCSCommand.DOOR_CLOSE])
        if self.wait_for_state(lambda s: s.get("gate_state") == 2, timeout=5):
            print("✅ 舱门关闭成功")
        else:
            print("❌ 舱门关闭失败，终止组合流程")
            self.test_results["failed"] += 1
            return

        print("\n--- 子测试3.9：启动充电 ---")
        self.send_nest_command([GCSCommand.CHARGE_START])
        if self.wait_for_state(lambda s: s.get("charging_state") is True, timeout=3):
            print("✅ 充电启动成功")
        else:
            print("❌ 充电启动失败，终止组合流程")
            self.test_results["failed"] += 1
            return

        print("\n🎉 组合业务流程测试全部完成！")
        self._print_status("组合流程最终状态")
        self.test_results["passed"] += 1

    def test_reconnect(self):
        print("\n" + "="*60)
        print("🔌 测试步骤4：断开重连验证")

        print("\n--- 子测试4.1：主动断开连接 ---")
        if self.client_sock:
            self.client_sock.close()
            self.running = False
            self.latest_nest_status = {}
            print("✅ 已主动断开连接")
            time.sleep(2)

        print("\n--- 子测试4.2：重新连接模拟机巢 ---")
        if self.connect(timeout=10):
            print("✅ 重连成功，状态推送恢复正常")
            self.test_results["passed"] += 1
        else:
            print("❌ 重连失败")
            self.test_results["failed"] += 1

    def run_all_tests(self):
        print("🚀 模拟机巢全流程测试开始")
        print(f"测试时间：{time.strftime('%Y-%m-%d %H:%M:%S')}")

        if not self.connect():
            print("❌ 连接失败，无法执行后续测试")
            return

        self.test_single_commands()
        self.test_combined_workflow()
        self.test_reconnect()

        print("\n" + "="*60)
        print("📋 测试结果总结")
        print(f"✅ 通过用例：{self.test_results['passed']}")
        print(f"❌ 失败用例：{self.test_results['failed']}")
        print(f"⏭️  跳过用例：{self.test_results['skipped']}")

        total = self.test_results['passed'] + self.test_results['failed'] + self.test_results['skipped']
        pass_rate = (self.test_results['passed'] / total * 100) if total > 0 else 0
        print(f"📈 通过率：{pass_rate:.1f}%")

        if self.test_results['failed'] == 0:
            print("🎉 所有测试用例通过！")
        else:
            print("❌ 存在失败用例，请检查模拟机巢或测试步骤！")

    def close(self):
        self.running = False
        if self.client_sock:
            self.client_sock.close()
        print("\n🔚 测试客户端已关闭")


if __name__ == "__main__":
    test_client = NestTestClient(host="127.0.0.1", port=5760)
    try:
        test_client.run_all_tests()
    except KeyboardInterrupt:
        print("\n⚠️  用户中断测试")
    finally:
        test_client.close()
