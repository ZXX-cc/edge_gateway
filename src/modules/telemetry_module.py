"""
遥测数据模块 - 实时采集和广播飞行器遥测数据
"""
import math
import time
from typing import Optional, Dict, Any
from src.modules.base_module import BaseModule
from src.core.message_dispatcher import MAVLinkMessage, MessageDispatcher

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

        # ✅ 遥测数据状态（增加经纬度字段）
        self.telemetry_data = {
            "latitude": 0.0,           # ✅ 新增：纬度
            "longitude": 0.0,          # ✅ 新增：经度
            "airspeed": 0.0,
            "roll": 0.0,
            "pitch": 0.0,
            "yaw": 0.0,
            "groundSpeed": 0.0,
            "altitude": 0.0,
            "altitudeRelative": 0.0,      # ✅ 新增：相对起飞点高度
            "altitudeAGL": 0.0,           # ✅ 新增：对地高度（AGL）
            "terrainAltitude": 0.0,       # ✅ 新增：地形海拔高度
            "climbRate": 0.0,
            "windSpeed": 0.0,
            # ⭐ 新增：电压数据
            "voltage": 0.0,  # 电池电压（V）
            "current": 0.0,  # 电流（A）
            "batteryRemaining": 0,  # 剩余电量（%）
            "connection_status": "disconnected",
            "data_mode": mode,
            "timestamp": 0.0
        }

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
        msg_type = message.msg_type
        is_updated = False

        # 解析姿态数据
        if msg_type == "ATTITUDE":
            self.telemetry_data["roll"] = math.degrees(msg.roll)
            self.telemetry_data["pitch"] = math.degrees(msg.pitch)
            self.telemetry_data["yaw"] = math.degrees(msg.yaw) % 360  # 转换为0-360度
            is_updated = True

        # 解析飞行数据
        elif msg_type == "VFR_HUD":
            self.telemetry_data["airspeed"] = msg.airspeed
            self.telemetry_data["groundSpeed"] = msg.groundspeed
            self.telemetry_data["climbRate"] = msg.climb
            is_updated = True

        # 解析风速数据
        elif msg_type == "WIND":
            self.telemetry_data["windSpeed"] = msg.speed
            is_updated = True

        # ✅ 解析全球位置数据（增强版）
        elif msg_type == "GLOBAL_POSITION_INT":
            # 经纬度（单位：度 * 1e7）
            if hasattr(msg, 'lat'):
                self.telemetry_data["latitude"] = msg.lat / 1e7
                is_updated = True

            if hasattr(msg, 'lon'):
                self.telemetry_data["longitude"] = msg.lon / 1e7
                is_updated = True

            # ✅ 海拔高度（单位：毫米）
            if hasattr(msg, 'alt'):
                self.telemetry_data["altitude"] = msg.alt / 1000.0
                # print(f'altitude = {self.telemetry_data["altitude"]}')
                is_updated = True

            # ✅ 相对起飞点高度（单位：毫米）
            if hasattr(msg, 'relative_alt'):
                self.telemetry_data["altitudeRelative"] = msg.relative_alt / 1000.0
                # print(f'altitudeRelative = {self.telemetry_data["altitudeRelative"]}')
                is_updated = True

        # ✅ 新增：解析地形报告数据
        elif msg_type == "TERRAIN_REPORT":
            # 地形海拔高度（单位：米）
            if hasattr(msg, 'terrain_height'):
                self.telemetry_data["terrainAltitude"] = msg.terrain_height
                # print(f'terrainAltitude = {self.telemetry_data["terrainAltitude"]}')
                is_updated = True

            # 对地高度（单位：米）
            if hasattr(msg, 'current_height'):
                self.telemetry_data["altitudeAGL"] = msg.current_height
                # print(f'altitudeAGL = {self.telemetry_data["altitudeAGL"]}')
                is_updated = True

        # ⭐ 新增：解析电池状态数据
        elif msg_type == "SYS_STATUS":
            # 电池电压（单位：毫伏 -> 伏特）
            if hasattr(msg, 'voltage_battery') and msg.voltage_battery != 65535:
                self.telemetry_data["voltage"] = round(msg.voltage_battery / 1000.0, 2)
                is_updated = True

            # 电流（单位：厘安培 -> 安培，-1表示不支持）
            if hasattr(msg, 'current_battery') and msg.current_battery != -1:
                self.telemetry_data["current"] = round(msg.current_battery / 100.0, 2)
                is_updated = True

            # 剩余电量（单位：%，-1表示不支持）
            if hasattr(msg, 'battery_remaining') and msg.battery_remaining != -1:
                self.telemetry_data["batteryRemaining"] = msg.battery_remaining
                is_updated = True

            # # ✅ 调试日志
            # if is_updated:
            #     print(f"[SYS_STATUS] 电压: {self.telemetry_data['voltage']:.2f}V, "
            #           f"电流: {self.telemetry_data['current']:.2f}A, "
            #           f"电量: {self.telemetry_data['batteryRemaining']}%")

        # ⭐ 备选：解析更详细的电池信息（BATTERY_STATUS）
        elif msg_type == "BATTERY_STATUS":
            # ✅ 修复：正确计算总电压（所有有效电芯电压之和）
            if hasattr(msg, 'voltages') and msg.voltages:
                # 过滤无效电压（65535 表示未连接的电芯）
                valid_voltages = [v for v in msg.voltages if v != 65535 and v > 0]

                if valid_voltages:
                    # 总电压 = 所有有效电芯电压之和（单位：mV -> V）
                    total_voltage = sum(valid_voltages) / 1000.0
                    self.telemetry_data["voltage"] = round(total_voltage, 2)
                    is_updated = True

                    # ✅ 调试日志（可选，验证数据）
                    # print(f"[BATTERY_STATUS] 电芯电压: {valid_voltages} mV, 总电压: {total_voltage:.2f}V")

            # 电流（单位：厘安培 -> 安培）
            if hasattr(msg, 'current_battery') and msg.current_battery != -1:
                self.telemetry_data["current"] = round(msg.current_battery / 100.0, 2)
                is_updated = True

            # 剩余电量（%）
            if hasattr(msg, 'battery_remaining') and msg.battery_remaining != -1:
                self.telemetry_data["batteryRemaining"] = msg.battery_remaining
                is_updated = True

            # # ✅ 综合调试日志
            # if is_updated:
            #     print(f"[BATTERY] 电压: {self.telemetry_data['voltage']:.2f}V, "
            #           f"电流: {self.telemetry_data['current']:.2f}A, "
            #           f"电量: {self.telemetry_data['batteryRemaining']}%")

        # ✅ 如果没有地形数据，用海拔高度和地形高度计算对地高度
        if is_updated and self.telemetry_data["altitudeAGL"] == 0.0:
            altitude = self.telemetry_data["altitude"]
            terrain = self.telemetry_data["terrainAltitude"]
            if altitude > 0 and terrain > 0:
                self.telemetry_data["altitudeAGL"] = altitude - terrain

        if is_updated:
            self.telemetry_data["connection_status"] = "connected"
            self.telemetry_data["data_mode"] = "real"
            self.telemetry_data["timestamp"] = time.time()

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

    def get_last_telemetry(self) -> Dict[str, Any]:
        """
        获取最后一次遥测数据（兼容旧接口）

        Returns:
            遥测数据字典
        """
        return self.get_telemetry_data()
