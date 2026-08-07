"""
模拟机巢硬件服务端
复刻真实机巢MAVLink协议行为，无硬件环境下全流程测试用
"""
import sys
import os
import time
import socket
import threading

# 路径处理：当前文件在根目录，直接加入Python路径
current_file_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, current_file_dir)

from src.generated_dialect import drone_nest
from src.generated_dialect.gcs_order_protocol import GCSCommand, GCSOrderCodec


class MockDroneNest:
    def __init__(self, host="127.0.0.1", port=5760):
        self.host = host
        self.port = port
        self.running = False
        self.client_sock = None
        self.mav = None

        # 机巢状态（和真实硬件字段完全对应）
        self.gate_state = 2          # 舱门：2=关闭
        self.clamp_state = 2         # 夹紧：2=夹紧
        self.charging_state = False  # 充电：未充电
        self.estop_state = False
        self.plc_ready = True
        self.allow_takeoff = True
        self.takeoff_in_progress = False
        self.landing_in_progress = False
        self.error_code = 0

        # 环境模拟数据
        self.nest_temp = 25.0
        self.env_temp = 23.5
        self.wind_speed = 2.1
        self.wind_direction = 180.0
        self.pressure = 101.3
        self.rainfall = 0.0
        self.charge_volt = 24.5
        self.charge_current = 0.0

        self.codec = GCSOrderCodec()

    def start(self):
        """启动服务，支持断开自动重连"""
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind((self.host, self.port))
        server.listen(1)
        print(f"🟢 模拟机巢已启动，监听地址: {self.host}:{self.port}")

        while True:
            print("⏳ 等待网关连接...")
            client_sock, addr = server.accept()
            print(f"✅ 网关已连接: {addr}")
            self.client_sock = client_sock
            self.running = True

            file_obj = client_sock.makefile('rwb', buffering=0)
            self.mav = drone_nest.MAVLink(file_obj, srcSystem=101, srcComponent=1)

            send_thread = threading.Thread(target=self._send_status_loop, daemon=True)
            recv_thread = threading.Thread(target=self._recv_command_loop, daemon=True)
            send_thread.start()
            recv_thread.start()

            print("✅ 模拟机巢运行中，定时推送状态消息")
            print("提示：保持本窗口打开，另开终端运行测试脚本\n")

            send_thread.join()
            recv_thread.join()
            self.client_sock.close()
            self.client_sock = None
            self.mav = None
            print("\n⚠️  网关连接已断开，等待下一次连接\n")

    def _send_status_loop(self):
        """每秒推送1次状态消息（DRONE_NEST_RESPONSE）"""
        while self.running and self.client_sock:
            try:
                boolstate1 = 0
                boolstate1 |= (1 << 0)  # 机巢心跳
                boolstate1 |= (1 << 1)  # 无人机心跳
                boolstate1 |= (1 << 2)  # PLC心跳
                if self.estop_state:
                    boolstate1 |= (1 << 3)
                if self.charging_state:
                    boolstate1 |= (1 << 4)
                if self.plc_ready:
                    boolstate1 |= (1 << 6)

                boolstate2 = 0
                if self.takeoff_in_progress:
                    boolstate2 |= (1 << 0)
                if self.landing_in_progress:
                    boolstate2 |= (1 << 1)
                if self.allow_takeoff:
                    boolstate2 |= (1 << 5)

                self.mav.drone_nest_response_send(
                    ac_temp=int(self.nest_temp * 10),
                    nest_temperature=int(self.nest_temp * 10),
                    env_temperature=int(self.env_temp * 10),
                    wind_direction=int(self.wind_direction * 100),
                    wind_speed=int(self.wind_speed * 10),
                    atmospheric_pressure=int(self.pressure * 10),
                    rainfall=int(self.rainfall * 10),
                    charge_volt=int(self.charge_volt * 100),
                    charge_current=int(self.charge_current * 100),
                    error_code=self.error_code,
                    boolstate1=boolstate1,
                    boolstate2=boolstate2,
                    clamp_state=self.clamp_state,
                    gate_state=self.gate_state,
                    landing_light_state=2,
                    lumen=0,
                    qr_light_current=0
                )
            except Exception:
                self.running = False
                break
            time.sleep(1)

    def _recv_command_loop(self):
        """接收网关发来的指令（带详细调试）"""
        while self.running and self.client_sock:
            try:
                data = self.client_sock.recv(4096)
                if not data:
                    self.running = False
                    break
                # 调试：打印收到的原始数据长度
                print(f"[调试] 收到原始数据，长度: {len(data)} 字节")
                # 逐字节解析MAVLink消息
                for byte in data:
                    msg = self.mav.parse_char(bytes([byte]))
                    if msg:
                        msg_type = msg.get_type()
                        print(f"[调试] 解析出消息: {msg_type}, ID={msg.get_msgId()}")
                        if msg_type == "DRONE_NEST_COMMAND":
                            self._handle_command(msg)
            except Exception as e:
                print(f"接收异常: {e}")
                self.running = False
                break


    def _handle_command(self, msg):
        """处理控制指令，返回响应并模拟动作"""
        ordinary_order = msg.ordinary_order
        takeoff_land_order = msg.takeoff_land_order
        gcs_order = (ordinary_order << 8) | takeoff_land_order

        commands = self.codec.decode_gcs_command(gcs_order)
        cmd_names = [cmd.name for cmd in commands]

        print(f"\n📥 收到网关指令: gcs_order=0x{gcs_order:08X}")
        print(f"   指令内容: {cmd_names}")

        # 先回指令响应
        self._send_order_response(commands)
        # 后台模拟硬件动作
        threading.Thread(target=self._simulate_action, args=(commands,), daemon=True).start()

    def _send_order_response(self, commands):
        """返回指令执行响应（DRONE_NEST_ORDER_RESPONSE）"""
        ordinary_resp = 0
        takeoff_land_resp = 0

        for cmd in commands:
            if cmd in [GCSCommand.DOOR_OPEN, GCSCommand.DOOR_CLOSE, GCSCommand.DOOR_STOP,
                       GCSCommand.CHARGE_START, GCSCommand.CHARGE_STOP,
                       GCSCommand.CLAMP_TIGHTEN, GCSCommand.CLAMP_RELEASE, GCSCommand.CLAMP_STOP]:
                ordinary_resp = 0x00010001  # 普通指令成功
            elif cmd in [GCSCommand.TAKEOFF_START, GCSCommand.TAKEOFF_ABORT,
                         GCSCommand.LANDING_ABORT]:
                takeoff_land_resp = 0x01000001

        self.mav.drone_nest_order_response_send(
            ordinary_order_response=ordinary_resp,
            takeoff_land_order_response=takeoff_land_resp
        )
        print("   ✅ 已返回指令成功响应")

    def _simulate_action(self, commands):
        """模拟硬件动作过程（带中间状态）"""
        for cmd in commands:
            if cmd == GCSCommand.DOOR_OPEN:
                print("   ⚙️ 模拟：舱门开始打开...")
                self.gate_state = 3  # 开启中
                time.sleep(3)
                self.gate_state = 1  # 打开完成
                print("   ✅ 模拟：舱门已完全打开")

            elif cmd == GCSCommand.DOOR_CLOSE:
                print("   ⚙️ 模拟：舱门开始关闭...")
                self.gate_state = 4  # 关闭中
                time.sleep(3)
                self.gate_state = 2  # 关闭完成
                print("   ✅ 模拟：舱门已完全关闭")

            elif cmd == GCSCommand.CHARGE_START:
                print("   ⚙️ 模拟：开始充电...")
                time.sleep(1)
                self.charging_state = True
                self.charge_current = 5.2
                print("   ✅ 模拟：充电已启动")

            elif cmd == GCSCommand.CHARGE_STOP:
                print("   ⚙️ 模拟：停止充电...")
                time.sleep(1)
                self.charging_state = False
                self.charge_current = 0.0
                print("   ✅ 模拟：充电已停止")

            elif cmd == GCSCommand.CLAMP_RELEASE:
                print("   ⚙️ 模拟：推杆推出（释放夹紧）...")
                self.clamp_state = 3
                time.sleep(2)
                self.clamp_state = 1
                print("   ✅ 模拟：推杆已推出")

            elif cmd == GCSCommand.CLAMP_TIGHTEN:
                print("   ⚙️ 模拟：推杆收回（夹紧）...")
                self.clamp_state = 4
                time.sleep(2)
                self.clamp_state = 2
                print("   ✅ 模拟：推杆已夹紧")

            elif cmd == GCSCommand.ESTOP_ACTIVATE:
                print("   🚨 模拟：触发急停！")
                self.estop_state = True
                self.error_code = 0x0001
                print("   ✅ 模拟：急停已生效")


if __name__ == "__main__":
    nest = MockDroneNest()
    nest.start()
