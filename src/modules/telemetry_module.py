"""
遥测数据模块 - 实时采集和广播飞行器遥测数据
"""
import math
import time
from typing import Optional, Dict, Any
from src.modules.base_module import BaseModule
from src.core.message_dispatcher import MAVLinkMessage, MessageDispatcher
from web.mavlink.telemetry.mapping import apply_mavlink_message, new_telemetry_state

class TelemetryModule(BaseModule):
    """
    遥测数据模块

    功能：
    - 实时采集飞行器遥测数据（姿态、速度、高度等）
    - 支持WebSocket实时推送
    - 支持模拟数据生成
    """

    def __init__(self, mode: str = "real"):
        """
        初始化遥测模块

        Args:
            mode: 数据模式 ('real' 或 'simulated')
        """
        super().__init__("TelemetryModule")

        self.mode = mode  # 'real' 或 'simulated'

        self.telemetry_data = new_telemetry_state()
        self.telemetry_data["data_mode"] = mode

        # 模拟数据生成器
        self.sim_time = 0.0
        self.base_altitude = 100.0
        self.base_airspeed = 25.0
        # ✅ 新增：模拟GPS起始位置（北京天安门广场示例）
        self.sim_base_lat = 39.9042
        self.sim_base_lng = 116.4074
        self.sim_gps_offset = 0.0  # GPS漂移累积
        self.sim_terrain_altitude = 50.0  # ✅ 新增：模拟地形高度

        # 数据更新回调（用于WebSocket推送）
        self.data_callbacks = []

        # 更新频率控制
        self.last_update_time = 0.0
        self.update_interval = 0.1  # 100ms更新一次

        # ✅ 新增：推送频率控制（独立于数据更新）
        self.last_push_time = 0.0
        self.push_interval = 0.3  # ⭐ 300ms推送一次（0.3秒）


    def on_message(self, message: MAVLinkMessage):
        """消息回调（推送机制）"""
        if self.mode != "real":
            return

        msg = message.msg
        if not hasattr(msg, "get_type") and not hasattr(msg, "msg_type") and not hasattr(msg, "_msg_type"):
            try:
                setattr(msg, "msg_type", message.msg_type)
            except Exception:
                return

        previous_timestamp = self.telemetry_data.get("timestamp", 0.0)
        apply_mavlink_message(msg, self.telemetry_data)
        is_updated = self.telemetry_data.get("timestamp", 0.0) != previous_timestamp

        if is_updated:
            # 节流控制推送
            current_time = time.time()
            if current_time - self.last_push_time >= self.push_interval:
                self._notify_callbacks()
                self.last_push_time = current_time

    def idle_task(self, dispatcher: MessageDispatcher):
        """
        空闲任务（拉取机制）

        用于生成模拟数据或定期更新
        """
        current_time = time.time()

        # 控制更新频率
        if current_time - self.last_update_time < self.update_interval:
            return

        self.last_update_time = current_time

        if self.mode == "simulated":
            self._generate_simulated_data()
            self._notify_callbacks()

    def _generate_simulated_data(self):
        """生成模拟的飞行数据"""
        import random

        self.sim_time += self.update_interval

        # ✅ 生成模拟GPS轨迹（顺时针圆形飞行）
        circle_radius_deg = 0.001  # 约111米
        angular_speed = 0.1  # 角速度（弧度/秒）

        self.sim_gps_offset += angular_speed * self.update_interval

        # 圆形轨迹计算
        lat_offset = circle_radius_deg * math.cos(self.sim_gps_offset)
        lng_offset = circle_radius_deg * math.sin(self.sim_gps_offset)

        # 生成平滑变化的模拟数据
        self.telemetry_data.update({
            # ✅ 模拟GPS数据
            "latitude": self.sim_base_lat + lat_offset + random.uniform(-0.00001, 0.00001),
            "longitude": self.sim_base_lng + lng_offset + random.uniform(-0.00001, 0.00001),

            # 其他遥测数据
            "airspeed": self.base_airspeed + 5 * math.sin(self.sim_time * 0.3) + random.uniform(-1, 1),
            "roll": 15 * math.sin(self.sim_time * 0.5) + random.uniform(-2, 2),
            "pitch": 8 * math.sin(self.sim_time * 0.4) + random.uniform(-1, 1),
            "yaw": (self.sim_time * 10) % 360 + random.uniform(-3, 3),
            "groundSpeed": self.base_airspeed - 2 + 3 * math.sin(self.sim_time * 0.2) + random.uniform(-0.5, 0.5),
            "altitude": self.base_altitude + 20 * math.sin(self.sim_time * 0.1) + random.uniform(-1, 1),
            "climbRate": 3 * math.cos(self.sim_time * 0.15) + random.uniform(-0.5, 0.5),
            "windSpeed": 8 + 3 * math.sin(self.sim_time * 0.25) + random.uniform(-0.3, 0.3),
            "connection_status": "simulated",
            "data_mode": "simulated",
            "timestamp": time.time()
        })

    def set_mode(self, mode: str):
        """
        切换数据模式

        Args:
            mode: 'real' 或 'simulated'
        """
        if mode not in ["real", "simulated"]:
            self.log(f"无效的模式: {mode}", "WARNING")
            return False

        old_mode = self.mode
        self.mode = mode
        self.telemetry_data["data_mode"] = mode

        if mode == "simulated":
            self.sim_time = 0.0
            self.sim_gps_offset = 0.0  # ✅ 重置GPS偏移
            self.telemetry_data["connection_status"] = "simulated"
            # ✅ 立即生成初始模拟数据
            self._generate_simulated_data()
        else:
            self.telemetry_data["connection_status"] = "disconnected"

        self.log(f"数据模式已切换: {old_mode} -> {mode}")
        return True

    # ... 其余方法保持不变 ...

    def add_callback(self, callback):
        """
        添加数据更新回调

        Args:
            callback: 回调函数,接收 telemetry_data 作为参数
        """
        if callback not in self.data_callbacks:
            self.data_callbacks.append(callback)
            self.log(f"添加数据回调: {callback.__name__ if hasattr(callback, '__name__') else 'anonymous'}")

    def remove_callback(self, callback):
        """移除数据更新回调"""
        if callback in self.data_callbacks:
            self.data_callbacks.remove(callback)
            self.log("移除数据回调")

    def _notify_callbacks(self):
        """通知所有回调函数"""
        for callback in self.data_callbacks:
            try:
                callback(self.telemetry_data.copy())
            except Exception as e:
                self.log(f"回调执行错误: {e}", "ERROR")

    @staticmethod
    def _parse_flight_mode(custom_mode: int) -> str:
        """解析MAVLink飞行模式为字符串（ArduPlane/QuadPlane）"""
        mode_map = {
            0: 'MANUAL', 1: 'CIRCLE', 2: 'STABILIZE', 3: 'TRAINING',
            4: 'ACRO', 5: 'FBWA', 6: 'FBWB', 7: 'CRUISE',
            8: 'AUTOTUNE', 10: 'AUTO', 11: 'RTL', 12: 'LOITER',
            13: 'TAKEOFF', 14: 'AVOID_ADSB', 15: 'GUIDED', 16: 'INITIALISING',
            17: 'QSTABILIZE', 18: 'QHOVER', 19: 'QLOITER', 20: 'QLAND',
            21: 'QRTL', 22: 'QAUTOTUNE', 23: 'QACRO', 24: 'THERMAL',
            25: 'LOITER_ALT_QLAND',
        }
        return mode_map.get(custom_mode, f'UNKNOWN({custom_mode})')

    def get_telemetry_data(self) -> Dict[str, Any]:
        """
        获取当前遥测数据

        Returns:
            遥测数据字典
        """
        return self.telemetry_data.copy()

    def get_connection_status(self) -> Dict[str, Any]:
        """
        获取连接状态

        Returns:
            连接状态信息
        """
        return {
            "connection_status": self.telemetry_data["connection_status"],
            "data_mode": self.mode,
            "timestamp": self.telemetry_data["timestamp"]
        }

    def get_flight_status(self) -> Dict[str, Any]:
        """
        获取飞行状态（DRC模块调用）

        Returns:
            飞行状态字典，包含 is_flying, altitude 等关键字段
        """
        data = self.telemetry_data
        return {
            "is_flying": data.get("altitude", 0) > 1.0 or data.get("groundSpeed", 0) > 0.5,
            "altitude": data.get("altitude", 0),
            "ground_speed": data.get("groundSpeed", 0),
            "battery": data.get("batteryRemaining", 0),
            "connection_status": data.get("connection_status", "disconnected")
        }

    def get_last_telemetry(self) -> Dict[str, Any]:
        """
        获取最后一次遥测数据（兼容旧接口）

        Returns:
            遥测数据字典
        """
        return self.get_telemetry_data()
