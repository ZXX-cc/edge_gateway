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
import queue
import threading
from typing import Dict, Callable, Optional
from src.utils.logger import get_logger
from src.utils.config import Config
from src.mavlink.flight_modes import mode_to_dji_code
from src.cloud_api.dock3_properties import get_property_spec
from web.mavlink.telemetry.mapping import build_osd_data as build_dock3_drone_osd

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
        self.configure_osd_rate(Config.get('mavlink.telemetry_rate.osd', 0.5))

        # 回调
        self._osd_callback: Optional[Callable] = None
        self._state_callback: Optional[Callable] = None
        self._event_callback: Optional[Callable] = None

        # 定时推送线程
        self._osd_thread: Optional[threading.Thread] = None
        self._osd_worker_thread: Optional[threading.Thread] = None
        self._osd_queue: queue.Queue = queue.Queue(maxsize=1)
        self._running = False
        self._stop_event = threading.Event()

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
        self._stop_event.clear()
        self._drain_osd_queue()
        self._osd_worker_thread = threading.Thread(target=self._osd_worker, daemon=True)
        self._osd_worker_thread.start()
        self._osd_thread = threading.Thread(target=self._osd_loop, daemon=True)
        self._osd_thread.start()
        logger.info("Telemetry OSD loop started: interval=%ss", self._osd_interval)

    def stop(self):
        """停止OSD定时推送线程"""
        self._running = False
        self._stop_event.set()
        self._enqueue_osd_item(None)
        if self._osd_thread:
            if threading.current_thread() is not self._osd_thread:
                self._osd_thread.join(timeout=2.0)
            self._osd_thread = None
        if self._osd_worker_thread:
            if threading.current_thread() is not self._osd_worker_thread:
                self._osd_worker_thread.join(timeout=2.0)
            self._osd_worker_thread = None
        logger.info("Telemetry OSD loop stopped")

    def _osd_loop(self):
        """OSD定时生成循环；耗时上报由后台 worker 处理。"""
        next_deadline = time.monotonic()
        while self._running:
            try:
                osd_data = self.build_osd_data()
                if osd_data:
                    osd_data['timestamp'] = int(time.time() * 1000)
                    self._enqueue_osd_item(osd_data)
            except Exception as e:
                logger.error(f"OSD build error: {e}")
            interval = max(float(self._osd_interval or 2.0), 0.1)
            next_deadline += interval
            delay = next_deadline - time.monotonic()
            if delay < 0:
                skipped = int(abs(delay) // interval) + 1
                next_deadline += skipped * interval
                delay = max(0.0, next_deadline - time.monotonic())
            self._stop_event.wait(delay)

    def _osd_worker(self):
        """消费定时生成的 OSD 快照并执行上报回调。"""
        while self._running or not self._osd_queue.empty():
            try:
                osd_data = self._osd_queue.get(timeout=0.5)
            except queue.Empty:
                continue
            try:
                if osd_data is None:
                    return
                if self._osd_callback:
                    self._osd_callback(osd_data)
            except Exception as e:
                logger.error(f"OSD push error: {e}")
            finally:
                self._osd_queue.task_done()

    def _enqueue_osd_item(self, osd_data: Optional[dict]) -> None:
        """保留最新 OSD 快照；worker 慢时丢弃旧帧，避免补发过期遥测。"""
        try:
            self._osd_queue.put_nowait(osd_data)
            return
        except queue.Full:
            pass

        try:
            self._osd_queue.get_nowait()
            self._osd_queue.task_done()
        except queue.Empty:
            pass
        try:
            self._osd_queue.put_nowait(osd_data)
        except queue.Full:
            logger.warning("Drop OSD snapshot because worker queue is full")

    def _drain_osd_queue(self) -> None:
        while True:
            try:
                self._osd_queue.get_nowait()
                self._osd_queue.task_done()
            except queue.Empty:
                return

    def configure_osd_rate(self, rate_hz) -> None:
        """按配置设置 OSD 上报频率；客户要求 2 秒一次时配置 0.5Hz。"""
        try:
            rate = float(rate_hz)
        except (TypeError, ValueError):
            rate = 0.5
        if rate <= 0:
            self._osd_interval = 2.0
            return
        self._osd_interval = max(1.0 / rate, 0.1)

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

        # 仅维护变更缓存。飞控模式、电量、定位等是 Dock3 定频 OSD 字段，
        # 不再把内部字段名直接发布到 gateway state。
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
            elif last != current:
                self._last_state[category] = current
        else:
            if last != current:
                self._last_state[category] = current

    # ---- 数据转换 ----

    def build_osd_data(self) -> dict:
        """通过唯一映射入口构建 Dock3 aircraft OSD。"""
        return build_dock3_drone_osd(self._telemetry_data)

    @staticmethod
    def _mode_to_code(mode: str) -> int:
        """将飞行模式字符串转换为DJI mode_code"""
        return mode_to_dji_code(mode)

    def build_state_data(self, changed_fields: dict) -> dict:
        """构建符合 Dock3 state topic 的属性变更数据。"""
        properties = {}
        for key, value in changed_fields.items():
            spec = get_property_spec(key)
            if spec and spec.push_mode == 1:
                properties[key] = value
        return {"property": properties}

    def get_current_data(self) -> Dict[str, any]:
        """获取当前遥测数据"""
        return self._telemetry_data.copy()
