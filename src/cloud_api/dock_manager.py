"""
DJI Dock 机场管理模块

维护机巢状态缓存和 HMS 告警桥接。

项目历史中 dock_status_query / dock_control 等聚合 services 入口已从
MQTT 路由移除或未注册同名 handler。dock_control 已确认废弃，不接入；
云端对接以 Dock3 properties、CMD、HMS 等具体链路为准。
"""
import time
from typing import Dict, Any, Optional, Callable
from src.utils.logger import get_logger
from src.utils.config import Config
from src.utils.message import MessageBuilder

logger = get_logger("dock")
alarm_logger = get_logger("alarm")


class DockManager:
    """
    DJI Dock 机场状态缓存

    职责：
    1. 缓存机巢状态，供内部状态机和 Dock3 properties 映射使用
    2. 管理本地 HMS 告警列表
    3. 将本地告警桥接为 Dock3 HMS 事件
    """

    def __init__(self):
        self.gateway_sn = Config.get_gateway_sn()

        # 机场状态
        self._dock_status = {
            'cover_state': 0,        # 0:关闭 1:打开 2:半开 3:异常
            'putter_state': 0,       # 0:收回 1:推出 2:异常
            'charging_state': 0,     # 0:未充电 1:充电中 2:充满
            'drone_in_dock': 1,      # 0:舱外 1:舱内
            'environment_temperature': 25.0,
            'nest_temperature': 25.0,
            'wind_speed': 0.0,
            'rainfall': 0,           # 0:无 1:小雨 2:中雨 3:大雨
            'humidity': 50,
            'air_conditioner_mode': 0,
            'supplement_light_state': 0,
            'alarm_state': 0,
            'rtk_state': 0,          # 0:未使用 1:固定解 2:浮点解
            'simulation_mode': False,
        }

        # 告警列表
        self._alarms = []

        # 回调
        self._event_callback: Optional[Callable] = None
        self._state_callback: Optional[Callable] = None

    def set_callbacks(self, event_cb: Callable, state_cb: Callable):
        """设置事件和状态回调"""
        self._event_callback = event_cb
        self._state_callback = state_cb

    # ---- 状态更新接口（供内部模块调用） ----

    def update_dock_status(self, updates: Dict[str, Any]):
        """更新机场状态"""
        changed = {}
        for key, value in updates.items():
            if key in self._dock_status and self._dock_status[key] != value:
                self._dock_status[key] = value
                changed[key] = value

        # 正式 Dock3 state 由 DeviceManager 按 properties.pushMode=1 发布，
        # 禁止输出内部 dock 包装。
        if changed:
            logger.debug("Dock status cache updated: %s", changed)

    def add_alarm(self, alarm: Dict[str, Any]):
        """添加告警"""
        alarm['id'] = alarm.get('id', str(int(time.time() * 1000)))
        alarm['create_time'] = alarm.get('create_time', int(time.time() * 1000))
        self._alarms.append(alarm)
        alarm_logger.warning("Dock alarm added: %s", alarm)

        if self._event_callback:
            event = MessageBuilder.build_event(
                'hms_push',
                {'alarm': alarm},
                self.gateway_sn
            )
            self._event_callback(event)

    def clear_alarms(self, alarm_ids: list = None):
        """清除告警"""
        alarm_logger.info("Dock alarms clear: alarm_ids=%s", alarm_ids or "all")
        if alarm_ids:
            self._alarms = [a for a in self._alarms if a.get('id') not in alarm_ids]
        else:
            self._alarms = []
