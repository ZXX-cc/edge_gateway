"""
测试管理模块 - 集成DeviceManager物模型验证（Config修复版）
支持：
1. 模拟数据模式（无硬件）
2. 真实MAVLink硬件连接模式
3. 遥测/机巢数据自动字段映射同步到DJI物模型
4. 物模型状态变更实时打印验证
5. 全量物模型状态查询功能
"""
import time
import threading
import argparse
from typing import Dict, Any
import sys
import os

# ✅ 自动添加项目根目录到Python路径
current_file = os.path.abspath(__file__)
project_root = os.path.dirname(os.path.dirname(os.path.dirname(current_file)))
if project_root not in sys.path:
    sys.path.insert(0, project_root)
    print(f"✅ 已添加项目根目录到Python路径: {project_root}")

# ✅ 先定义MockConfig并替换真实Config（解决Config没有set方法的问题）
class MockConfig:
    """临时MockConfig，仅用于测试，不修改真实配置文件"""
    _config_data = {
        'gateway': {
            'sn': 'TEST_GW_SN_001'
        },
        'device': {
            'firmware_version': '1.0.0-test',
            'limits': {'return_home_power': 30},
            'activation_time': 1710000000,
            'job_number': 0,
            'acc_time': 0,
            'user_experience': 0
        }
    }

    @staticmethod
    def get(key, default=None):
        keys = key.split('.')
        value = MockConfig._config_data
        for k in keys:
            if isinstance(value, dict) and k in value:
                value = value[k]
            else:
                return default
        return value

    @staticmethod
    def set(key, value):
        keys = key.split('.')
        current = MockConfig._config_data
        for k in keys[:-1]:
            if k not in current:
                current[k] = {}
            current = current[k]
        current[keys[-1]] = value

    @staticmethod
    def save():
        print("[模拟Config] 配置已保存（测试模式，不写入真实文件）")

    @staticmethod
    def get_gateway_sn():
        return MockConfig.get('gateway_sn', 'UNKNOWN')

# ✅ 关键：在导入DeviceManager之前，替换真实Config模块
sys.modules['src.utils.config'] = type('ConfigModule', (), {'Config': MockConfig})

# 现在再导入所有依赖
from src.modules.telemetry_module import TelemetryModule
from src.modules.drone_nest_module import DroneNestModule
from src.core.message_dispatcher import MessageDispatcher, MAVLinkMessage
from src.device.manager import DeviceManager
from pymavlink import mavutil

# ✅ 完善模拟连接类：完全兼容真实MAVLink连接
class MockMAVLinkConnection:
    class MockMAV:
        def drone_nest_command_send(self, ordinary_order, takeoff_land_order):
            print(f"[模拟MAV] 发送机巢指令: ordinary=0x{ordinary_order:04X}, takeoff_land=0x{takeoff_land_order:02X}")

    def __init__(self):
        self.mav = self.MockMAV()
        self.master = self
        self.target_system = 101
        self.target_component = 1
        self.use_custom_dialect = False
        self.port = "simulated_port"
        self.baud = 115200

    def close(self):
        print("[模拟MAV] 连接已关闭")

    def recv_match(self, blocking=True, timeout=1.0):
        time.sleep(timeout)
        return None

class TestManager:
    def __init__(self, mode: str = "simulated", connection_str: str = None):
        self.mode = mode
        self.connection_str = connection_str
        self.running = False
        self.mav_connection = None
        self.dispatcher = None
        self.telemetry_module = None
        self.drone_nest_module = None
        self.device_manager = None
        self.test_thread = None
        self.print_full_state_interval = 10  # 每10秒打印一次全量物模型状态
        self.last_full_state_print = 0

        # 1. 初始化连接
        self._init_connection()
        # 2. 初始化消息分发器
        self.dispatcher = MessageDispatcher(connection=self.mav_connection)
        # 3. 初始化业务模块
        self._init_modules()
        # 4. 初始化DeviceManager并建立数据同步链路（含字段映射）
        self._init_device_manager()

    def _init_connection(self):
        if self.mode == "real" and self.connection_str:
            self._init_real_mavlink_connection()
        else:
            self.mav_connection = MockMAVLinkConnection()
            print("✅ 已创建模拟MAVLink连接")

    def _init_real_mavlink_connection(self):
        try:
            if "," in self.connection_str:
                port, baud = self.connection_str.split(",")
                self.mav_connection = mavutil.mavlink_connection(port, baud=int(baud))
            else:
                self.mav_connection = mavutil.mavlink_connection(self.connection_str)

            print(f"🔌 正在连接MAVLink设备: {self.connection_str}...")
            self.mav_connection.wait_heartbeat()
            print(f"✅ MAVLink连接成功 | 系统ID: {self.mav_connection.target_system}, 组件ID: {self.mav_connection.target_component}")

            read_thread = threading.Thread(target=self._read_mavlink_messages, daemon=True)
            read_thread.start()
        except Exception as e:
            print(f"❌ MAVLink连接失败: {e}")
            raise

    def _read_mavlink_messages(self):
        while self.running:
            try:
                msg = self.mav_connection.recv_match(blocking=True, timeout=1.0)
                if not msg:
                    continue

                mav_msg = MAVLinkMessage(
                    msg_type=msg.get_type(),
                    msg=msg,
                    system_id=self.mav_connection.target_system,
                    component_id=self.mav_connection.target_component
                )

                self.telemetry_module.on_message(mav_msg)
                self.drone_nest_module.on_message(mav_msg)
            except Exception as e:
                if self.running:
                    print(f"⚠️ MAVLink消息读取错误: {e}")
                break

    def _init_modules(self):
        # 1. 初始化遥测模块
        self.telemetry_module = TelemetryModule(mode=self.mode)
        self.telemetry_module.initialize()
        self.telemetry_module.add_callback(self._on_telemetry_data)

        # 2. 初始化机巢模块
        self.drone_nest_module = DroneNestModule(target_system=101, target_component=1)
        self.drone_nest_module.initialize()
        self.drone_nest_module.add_environment_callback(self._on_nest_env_data)
        self.drone_nest_module.add_status_callback(self._on_nest_status_data)
        self.drone_nest_module.dispatcher = self.dispatcher

        print(f"✅ 业务模块初始化完成 | 测试模式: {self.mode}")

    # ✅ 遥测数据字段映射（转换为DeviceManager期望的格式）
    def _map_telemetry_to_device_manager(self, telemetry_data: Dict[str, Any]) -> Dict[str, Any]:
        """将TelemetryModule的字段映射为DeviceManager.update_from_telemetry期望的字段"""
        return {
            'latitude': telemetry_data['latitude'],
            'longitude': telemetry_data['longitude'],
            'height': telemetry_data['altitudeRelative'],  # 遥测的相对高度 → 物模型的height
            'altitude': telemetry_data['altitude'],        # 海拔高度
            'battery': {
                'capacity_percent': telemetry_data['batteryRemaining']
            },
            'airspeed': telemetry_data['airspeed'],
            'groundspeed': telemetry_data['groundSpeed'],
            'roll': telemetry_data['roll'],
            'pitch': telemetry_data['pitch'],
            'yaw': telemetry_data['yaw'],
            'climb_rate': telemetry_data['climbRate'],
            'wind_speed': telemetry_data['windSpeed']
        }

    # ✅ 机巢数据字段映射（转换为DeviceManager期望的格式）
    def _map_nest_to_device_manager(self, nest_data: Dict[str, Any]) -> Dict[str, Any]:
        """将DroneNestModule的字段映射为DeviceManager.update_from_nest期望的字段"""
        return {
            'data_valid': nest_data['data_valid'],
            'temperature': nest_data['nest_temperature'],  # 机巢温度 → 物模型的temperature
            'humidity': nest_data.get('humidity', 60.0),   # 湿度（模拟数据补充默认值）
            'wind_speed': nest_data['wind_speed'],
            'wind_direction': nest_data['wind_direction'],
            'rainfall': nest_data['rainfall'],
            'atmospheric_pressure': nest_data['atmospheric_pressure'],
            'charging_state': 1 if nest_data['charging_state'] else 0,  # bool → int
            'cover_state': nest_data['gate_state'],        # 机巢的gate_state → 物模型的cover_state
            'clamp_state': nest_data['clamp_state'],
            'landing_light_state': nest_data['landing_light_state'],
            'estop_state': 1 if nest_data['estop_state'] else 0,
            'takeoff_allowed': 1 if nest_data['takeoff_allowed'] else 0
        }

    # ✅ 初始化DeviceManager并建立带字段映射的同步链路
    def _init_device_manager(self):
        print("\n" + "="*50)
        print("🔗 初始化DeviceManager物模型并建立数据同步")
        print("="*50)

        # 1. 初始化DeviceManager（自动从MockConfig读取网关SN和固件版本）
        self.device_manager = DeviceManager()

        # 2. 注入依赖
        self.device_manager.set_nest_module(self.drone_nest_module)
        self.device_manager.set_telemetry_processor(self.telemetry_module)
        print("✅ 已注入机巢和遥测模块到DeviceManager")

        # 3. 建立带字段映射的数据同步链路
        def telemetry_sync(data):
            mapped_data = self._map_telemetry_to_device_manager(data)
            self.device_manager.update_from_telemetry(mapped_data)

        def nest_sync(data):
            mapped_data = self._map_nest_to_device_manager(data)
            self.device_manager.update_from_nest(mapped_data)

        self.telemetry_module.add_callback(telemetry_sync)
        self.drone_nest_module.add_environment_callback(nest_sync)
        print("✅ 已建立带字段映射的数据自动同步链路")

        # 4. 注册物模型状态变更回调
        self.device_manager.set_callbacks(
            event_cb=self._on_device_event,
            state_cb=self._on_device_state_changed
        )
        print("✅ 已注册物模型状态变更回调")

        # 5. 设备上线
        self.device_manager.device_online()
        print(f"✅ 设备上线成功 | 网关SN: {self.device_manager.gateway_sn}")
        print(f"✅ 固件版本: {self.device_manager.get_property('firmware_version')}")
        print("="*50 + "\n")

    # ✅ 物模型事件上报回调
    def _on_device_event(self, event: Dict[str, Any]):
        print("\n" + "🔥"*30)
        print(f"📤 物模型事件上报: {event['method']}")
        print(f"📤 事件数据: {event['data']}")
        print("🔥"*30 + "\n")

    # ✅ 物模型状态变更回调
    def _on_device_state_changed(self, state: Dict[str, Any]):
        print("\n" + "🔄"*30)
        print("📤 物模型状态同步成功！变更字段：")
        for key, value in state['property'].items():
            print(f"   {key:30} = {value}")
        print("🔄"*30 + "\n")

    # ✅ 打印全量物模型状态
    def _print_full_device_state(self):
        """每10秒打印一次全量物模型状态，方便调试"""
        current_time = time.time()
        if current_time - self.last_full_state_print >= self.print_full_state_interval:
            print("\n" + "📊"*30)
            print("📊 全量物模型状态快照")
            print("📊"*30)
            # 核心属性打印
            core_props = [
                ("网关SN", self.device_manager.gateway_sn),
                ("固件版本", self.device_manager.get_property('firmware_version')),
                ("经纬度", f"{self.device_manager.get_property('latitude'):.6f}, {self.device_manager.get_property('longitude'):.6f}"),
                ("飞行高度", f"{self.device_manager.get_property('height'):.1f}m"),
                ("剩余电量", f"{self.device_manager.get_property('electricity')}%"),
                ("机巢温度", f"{self.device_manager.get_property('temperature'):.1f}°C"),
                ("舱门状态", "打开" if self.device_manager.get_property('cover_state') == 1 else "关闭"),
                ("充电状态", "充电中" if self.device_manager.get_property('charging_state') == 1 else "空闲"),
                ("允许起飞", "是" if self.device_manager.get_property('takeoff_allowed') == 1 else "否")
            ]
            for label, value in core_props:
                print(f"{label:20}: {value}")
            print("📊"*30 + "\n")
            self.last_full_state_print = current_time

    def _on_telemetry_data(self, data: Dict[str, Any]):
        # 只打印关键遥测数据，避免刷屏
        print(f"\n📡 遥测更新 | 经纬度: {data['latitude']:.6f}, {data['longitude']:.6f} | 高度: {data['altitudeRelative']:.1f}m")
        # 定期打印全量物模型状态
        self._print_full_device_state()

    def _on_nest_env_data(self, data: Dict[str, Any]):
        print(f"\n🏠 机巢更新 | 温度: {data['nest_temperature']:.1f}°C | 舱门: {self.drone_nest_module._decode_gate_state(data['gate_state'])} | 充电: {'是' if data['charging_state'] else '否'}")

    def _on_nest_status_data(self, data: Dict[str, Any]):
        pass

    def test_nest_commands(self):
        print("\n" + "🔴 开始测试机巢指令发送")
        test_commands = [
            ("手动开门", self.drone_nest_module.manual_open_door),
            ("等待2秒", lambda: time.sleep(2)),
            ("手动关门", self.drone_nest_module.manual_close_door),
            ("等待2秒", lambda: time.sleep(2)),
            ("启动充电", self.drone_nest_module.start_charging),
            ("等待2秒", lambda: time.sleep(2)),
            ("停止充电", self.drone_nest_module.stop_charging),
        ]

        for cmd_name, cmd_func in test_commands:
            print(f"\n📤 执行指令: {cmd_name}")
            try:
                cmd_func()
            except Exception as e:
                print(f"❌ 指令执行失败: {e}")

    def run_idle_tasks(self):
        while self.running:
            self.telemetry_module.idle_task(self.dispatcher)
            self.drone_nest_module.idle_task(self.dispatcher)
            time.sleep(0.1)

    def start(self):
        self.running = True
        self.test_thread = threading.Thread(target=self.run_idle_tasks, daemon=True)
        self.test_thread.start()
        print("\n🚀 测试管理器已启动（按Ctrl+C停止）")

        threading.Timer(3, self.test_nest_commands).start()

        try:
            while self.running:
                time.sleep(1)
        except KeyboardInterrupt:
            print("\n🛑 接收到停止信号，正在关闭...")
            self.stop()

    def stop(self):
        self.running = False
        # 停止DeviceManager
        if self.device_manager:
            self.device_manager.device_offline()
            self.device_manager.stop()
            print("✅ DeviceManager已停止，配置已保存")
        # 清理其他模块
        self.telemetry_module.cleanup()
        self.drone_nest_module.cleanup()
        if self.mav_connection:
            self.mav_connection.close()
        if self.test_thread:
            self.test_thread.join(timeout=2)
        print("✅ 测试管理器已停止")

def main():
    parser = argparse.ArgumentParser(description="机巢+遥测+DeviceManager集成测试工具")
    parser.add_argument(
        "--mode",
        choices=["simulated", "real"],
        default="simulated",
        help="测试模式：simulated（模拟数据）/ real（真实硬件）"
    )
    parser.add_argument(
        "--connection",
        type=str,
        help="真实模式下的MAVLink连接串，如：/dev/ttyUSB0,57600 或 udp:127.0.0.1:14550"
    )
    args = parser.parse_args()

    if args.mode == "real" and not args.connection:
        print("❌ 真实模式必须指定--connection参数")
        parser.print_help()
        sys.exit(1)

    test_manager = TestManager(mode=args.mode, connection_str=args.connection)
    test_manager.start()

if __name__ == "__main__":
    main()
