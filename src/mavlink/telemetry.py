"""
遥测数据处理模块
将TelemetryModule采集的数据转换为OSD/State格式，是遥测上报的核心处理层

架构定位：
- 上游：TelemetryModule（数据采集）
- 下游：TelemetryReporter（云端上报）、Database（本地持久化）

三类数据输出：
1. OSD：定频上报（0.5Hz），包含位置、姿态、电量等实时数据
2. State：触发式上报，仅在状态变更时上报
3. Event：事件上报，用于异常、告警等
"""
import time
import math
import threading
from typing import Dict, Callable, Optional
from src.utils.logger import get_logger
from src.utils.config import Config
from src.mavlink.flight_modes import mode_to_dji_code

logger = get_logger("telemetry")


class TelemetryProcessor:
    """
    遥测数据处理器

    职责:
    1. 接收TelemetryModule的原始数据
    2. 转换为OSD格式(定频推送, 0.5Hz)
    3. 检测状态变更并触发state推送
    4. 数据平滑和滤波
    5. 持久化遥测数据到数据库
    """

    def __init__(self):
        self.gateway_sn = Config.get_gateway_sn()

        # 最新遥测数据缓存（来自TelemetryModule）
        self._telemetry_data: Dict[str, any] = {}

        # 状态缓存(用于变更检测)
        self._last_state: Dict[str, any] = {}
        self._last_osd_time = 0
        self._osd_interval = 2.0

        # 回调
        self._osd_callback: Optional[Callable] = None
        self._state_callback: Optional[Callable] = None
        self._event_callback: Optional[Callable] = None

        # 定时推送线程
        self._osd_thread: Optional[threading.Thread] = None
        self._running = False

        # 飞行时间累计
        self._flight_start_time = None
        self._total_flight_time = 0

    def set_callbacks(self, osd_cb: Callable = None, state_cb: Callable = None, event_cb: Callable = None):
        """设置数据推送回调"""
        self._osd_callback = osd_cb
        self._state_callback = state_cb
        self._event_callback = event_cb

    def start(self):
        """启动OSD定时推送线程"""
        if self._running:
            return
        self._running = True
        self._osd_thread = threading.Thread(target=self._osd_loop, daemon=True)
        self._osd_thread.start()
        logger.info("Telemetry OSD loop started (0.5Hz)")

    def stop(self):
        """停止OSD定时推送线程"""
        self._running = False
        if self._osd_thread:
            self._osd_thread.join(timeout=2.0)
            self._osd_thread = None
        logger.info("Telemetry OSD loop stopped")

    def _osd_loop(self):
        """OSD定时推送循环"""
        while self._running:
            try:
                osd_data = self.build_osd_data()
                if self._osd_callback and osd_data:
                    osd_data['timestamp'] = int(time.time() * 1000)
                    self._osd_callback(osd_data)
            except Exception as e:
                logger.error(f"OSD push error: {e}")
            time.sleep(self._osd_interval)

    # ---- 数据输入接口（来自TelemetryModule） ----

    def on_telemetry_update(self, telemetry_data: Dict[str, any]):
        """
        接收TelemetryModule的数据更新

        Args:
            telemetry_data: 遥测数据字典
        """
        self._telemetry_data = telemetry_data

        # 更新飞行时间
        self._update_flight_time()

        # 检测状态变更
        self._check_all_state_changes()

    def _update_flight_time(self):
        """更新飞行时间"""
        connection_status = self._telemetry_data.get('connection_status', 'disconnected')
        armed = self._telemetry_data.get('armed', False)

        if connection_status == 'connected' and armed and self._flight_start_time is None:
            self._flight_start_time = time.time()
        elif (connection_status != 'connected' or not armed) and self._flight_start_time is not None:
            self._total_flight_time += int(time.time() - self._flight_start_time)
            self._flight_start_time = None

        if self._flight_start_time is not None:
            current_flight_time = self._total_flight_time + int(time.time() - self._flight_start_time)
            self._telemetry_data['flight_time'] = current_flight_time
        else:
            self._telemetry_data['flight_time'] = self._total_flight_time

    def _check_all_state_changes(self):
        """检测所有状态变更"""
        # 飞行模式变更
        flight_mode = self._telemetry_data.get('flight_mode', 'UNKNOWN')
        self._check_state_change('flight_mode', flight_mode)

        # 上锁状态变更
        armed = self._telemetry_data.get('armed', False)
        self._check_state_change('armed', armed)

        # 连接状态变更
        connection_status = self._telemetry_data.get('connection_status', 'disconnected')
        self._check_state_change('connection_status', connection_status)

        # 电池状态变更（电量变化超过5%才上报）
        battery = self._telemetry_data.get('batteryRemaining', 0)
        self._check_state_change('battery', battery, threshold=5)

        # GPS状态变更
        gps_fixed = self._telemetry_data.get('gps_fixed', 0)
        self._check_state_change('gps_fixed', gps_fixed)

    def _check_state_change(self, category: str, current: any, threshold: int = None):
        """
        检测状态变更

        Args:
            category: 状态类别
            current: 当前值
            threshold: 阈值（用于数值类型的状态变更检测）
        """
        last = self._last_state.get(category)

        if threshold is not None:
            if isinstance(last, (int, float)) and isinstance(current, (int, float)):
                if abs(current - last) >= threshold:
                    self._last_state[category] = current
                    if self._state_callback:
                        self._state_callback(self.build_state_data({category: current}))
            elif last != current:
                self._last_state[category] = current
                if self._state_callback:
                    self._state_callback(self.build_state_data({category: current}))
        else:
            if last != current:
                self._last_state[category] = current
                if self._state_callback:
                    self._state_callback(self.build_state_data({category: current}))

    # ---- 数据转换 ----

    def build_osd_data(self) -> dict:
        """构建OSD定频数据（对齐DJI Cloud API规范）"""
        osd = {}
        data = self._telemetry_data

        # 位置
        osd['latitude'] = data.get('latitude', 0.0)
        osd['longitude'] = data.get('longitude', 0.0)
        osd['height'] = data.get('altitudeRelative', 0.0)
        osd['altitude'] = data.get('altitude', 0.0)

        # 速度
        ground_speed = data.get('groundSpeed', 0.0)
        climb_rate = data.get('climbRate', 0.0)
        osd['horizontal_speed'] = ground_speed
        osd['vertical_speed'] = climb_rate
        osd['course'] = data.get('course', 0.0)

        # 姿态
        osd['attitude_pitch'] = data.get('pitch', 0.0)
        osd['attitude_roll'] = data.get('roll', 0.0)
        osd['attitude_head'] = data.get('yaw', 0.0)

        # 电量
        voltage = data.get('voltage', 0.0)
        current = data.get('current', 0.0)
        battery_remaining = data.get('batteryRemaining', 0)

        osd['battery'] = {
            'capacity_percent': battery_remaining,
            'voltage': int(voltage * 1000),
        }
        if current > 0:
            osd['battery']['current'] = int(current * 1000)

        # GPS/定位状态
        osd['position_state'] = {
            'gps_number': data.get('satelliteNumber', 0),
            'is_fixed': data.get('gps_fixed', 0),
            'quality': data.get('gps_signal_level', 0),
            'rtk_number': data.get('rtkNumber', 0),
            'rtk_flag': data.get('rtk_flag', 0),
        }

        # 风速
        osd['wind_speed'] = data.get('windSpeed', 0.0)
        osd['wind_direction'] = data.get('windDirection', 0.0)

        # 负载/安全感知
        osd['gimbal_pitch'] = data.get('gimbal_pitch', 0.0)
        osd['gimbal_roll'] = data.get('gimbal_roll', 0.0)
        osd['gimbal_yaw'] = data.get('gimbal_yaw', 0.0)
        osd['obstacle_state'] = data.get('obstacle_state', {})

        # 飞行模式和状态
        flight_mode = data.get('flight_mode', 'UNKNOWN')
        osd['mode_code'] = mode_to_dji_code(flight_mode)
        osd['gear'] = 1 if data.get('armed', False) else 0

        # 飞行时间
        osd['flight_time'] = data.get('flight_time', 0)

        return osd

    @staticmethod
    def _mode_to_code(mode: str) -> int:
        """将飞行模式字符串转换为DJI mode_code"""
        return mode_to_dji_code(mode)

    def build_state_data(self, changed_fields: dict) -> dict:
        """构建符合 Dock3 state topic 的属性变更数据。"""
        # ``TelemetryReporter.report_state`` 会补齐 tid/bid/gateway/timestamp
        # 外层信封；此处只提供 Dock3 规定的 data.property 内容。
        return {"property": dict(changed_fields)}

    def get_current_data(self) -> Dict[str, any]:
        """获取当前遥测数据"""
        return self._telemetry_data.copy()
