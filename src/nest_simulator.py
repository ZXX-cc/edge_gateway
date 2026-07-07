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
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
GENERATED_DIALECT_DIR = PROJECT_ROOT / 'src' / 'generated_dialect'
sys.path.insert(0, str(GENERATED_DIALECT_DIR))
import drone_nest


class NestSimulator:
    def __init__(self, host='0.0.0.0', port=14550, system_id=101, component_id=1):
        self.host = host
        self.port = port
        self.system_id = system_id
        self.component_id = component_id
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
        print("已发送 DRONE_NEST_RESPONSE")

    def _send_order_response(self, ordinary_response=0, takeoff_land_response=0):
        msg = drone_nest.MAVLink_drone_nest_order_response_message(
            ordinary_order_response=ordinary_response,
            takeoff_land_order_response=takeoff_land_response
        )
        self._send_raw(msg.pack(self.mav))
        print(f"已发送 DRONE_NEST_ORDER_RESPONSE: ordinary=0x{ordinary_response:08X}")

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
        print(f"收到消息: {msg_type}")
        if msg_type == 'HEARTBEAT':
            self._send_heartbeat()
        elif msg_type == 'DRONE_NEST_COMMAND':
            ordinary = getattr(msg, 'ordinary_order', 0)
            takeoff_land = getattr(msg, 'takeoff_land_order', 0)
            print(f"收到机巢命令: ordinary=0x{ordinary:04X}, takeoff_land=0x{takeoff_land:02X}")
            self._update_state_from_command(ordinary, takeoff_land)
            self._send_order_response(0, 0)

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
    args = parser.parse_args()

    sim = NestSimulator(host=args.host, port=args.port)
    try:
        sim.start()
    except KeyboardInterrupt:
        print("\n用户中断")
        sim.stop()
