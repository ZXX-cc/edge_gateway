"""
DJI Dock 机场管理模块

处理机场特有的状态查询和控制接口，包括：
- 机场状态查询（舱盖、充电、天气、推杆等）
- 机场控制（一键开关盖、充电控制等）
- 机场告警查询
- 机场任务调度状态

参考：DJI Cloud API Dock Feature Set
"""
import time
from typing import Dict, Any, Optional, Callable
from src.utils.logger import get_logger
from src.utils.config import Config
from src.utils.message import MessageBuilder

logger = get_logger("dock")


class DockManager:
    """
    DJI Dock 机场管理器

    职责：
    1. 机场状态聚合与查询
    2. 机场控制指令处理
    3. 机场告警管理
    4. 机场任务调度
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

        # 无人机状态
        self._drone_status = {
            'online': False,
            'armed': False,
            'flight_mode': 'UNKNOWN',
            'battery_percent': 100,
            'longitude': 0.0,
            'latitude': 0.0,
            'altitude': 0.0,
            'height': 0.0,
            'home_longitude': 0.0,
            'home_latitude': 0.0,
            'speed': 0.0,
            'vertical_speed': 0.0,
            'heading': 0.0,
            'satellite_number': 0,
            'gps_signal_level': 0,
            'rc_signal_quality': 100,
            'transmission_signal_quality': 100,
        }

        # 告警列表
        self._alarms = []

        # 机巢模块引用(由外部注入)
        self._nest_module = None

        # 回调
        self._event_callback: Optional[Callable] = None
        self._state_callback: Optional[Callable] = None

    def set_callbacks(self, event_cb: Callable, state_cb: Callable):
        """设置事件和状态回调"""
        self._event_callback = event_cb
        self._state_callback = state_cb

    def set_nest_module(self, nest_module):
        """注入机巢通信模块"""
        self._nest_module = nest_module

    # ---- 状态更新接口（供内部模块调用） ----

    def update_dock_status(self, updates: Dict[str, Any]):
        """更新机场状态"""
        changed = {}
        for key, value in updates.items():
            if key in self._dock_status and self._dock_status[key] != value:
                self._dock_status[key] = value
                changed[key] = value

        if changed and self._state_callback:
            self._state_callback({'dock': changed})

    def update_drone_status(self, updates: Dict[str, Any]):
        """更新无人机状态"""
        changed = {}
        for key, value in updates.items():
            if key in self._drone_status and self._drone_status[key] != value:
                self._drone_status[key] = value
                changed[key] = value

        if changed and self._state_callback:
            self._state_callback({'drone': changed})

    def add_alarm(self, alarm: Dict[str, Any]):
        """添加告警"""
        alarm['id'] = alarm.get('id', str(int(time.time() * 1000)))
        alarm['create_time'] = alarm.get('create_time', int(time.time() * 1000))
        self._alarms.append(alarm)

        if self._event_callback:
            event = MessageBuilder.build_event(
                'hms_push',
                {'alarm': alarm},
                self.gateway_sn
            )
            self._event_callback(event)

    def clear_alarms(self, alarm_ids: list = None):
        """清除告警"""
        if alarm_ids:
            self._alarms = [a for a in self._alarms if a.get('id') not in alarm_ids]
        else:
            self._alarms = []

    # ---- API接口（大疆Cloud API标准） ----

    def handle_dock_status_query(self, msg: dict) -> dict:
        """
        机场状态查询 (dock_status_query)

        Request: { }
        Response: {
            dock_status: { cover_state, putter_state, charging_state, ... },
            drone_status: { online, battery_percent, flight_mode, ... }
        }
        """
        logger.info("Dock status query")
        return MessageBuilder.build_reply(msg, result=0, data={
            'dock_status': self._dock_status.copy(),
            'drone_status': self._drone_status.copy(),
            'timestamp': int(time.time() * 1000)
        })

    def handle_dock_control(self, msg: dict) -> dict:
        """
        机场控制 (dock_control)

        Request: {
            action: 'cover_open'|'cover_close'|'putter_push'|'putter_pull'|
                    'charge_start'|'charge_stop'|'light_on'|'light_off'
        }
        Response: { result }
        """
        data = msg.get('data', {})
        action = data.get('action', '')
        logger.info(f"Dock control: {action}")

        # 执行机场物理控制
        action_map = {
            'cover_open': {'cover_state': 1},
            'cover_close': {'cover_state': 0},
            'putter_push': {'putter_state': 1},
            'putter_pull': {'putter_state': 0},
            'charge_start': {'charging_state': 1},
            'charge_stop': {'charging_state': 0},
            'light_on': {'supplement_light_state': 1},
            'light_off': {'supplement_light_state': 0},
        }

        if action in action_map:
            self.update_dock_status(action_map[action])

        # 通过机巢模块发送实际命令
        if self._nest_module:
            try:
                if action == 'cover_open':
                    self._nest_module.manual_open_door()
                elif action == 'cover_close':
                    self._nest_module.manual_close_door()
                elif action == 'charge_start':
                    self._nest_module.start_charge()
                elif action == 'charge_stop':
                    self._nest_module.stop_charge()
                elif action == 'putter_push':
                    # 推杆推出近似为夹紧释放 (CLAMP_RELEASE)
                    from generated_dialect.gcs_order_protocol import GCSCommand
                    self._nest_module.send_gcs_command(GCSCommand.CLAMP_RELEASE)
                elif action == 'putter_pull':
                    # 推杆收回近似为夹紧 (CLAMP_TIGHTEN)
                    from generated_dialect.gcs_order_protocol import GCSCommand
                    self._nest_module.send_gcs_command(GCSCommand.CLAMP_TIGHTEN)
            except Exception as e:
                logger.error(f"Dock control failed: {e}")

        return MessageBuilder.build_reply(msg, result=0, data={'action': action})

    def handle_dock_alarm_query(self, msg: dict) -> dict:
        """
        机场告警查询 (dock_alarm_query)

        Request: { page, page_size }
        Response: { total, alarms: [...] }
        """
        data = msg.get('data', {})
        page = data.get('page', 1)
        page_size = data.get('page_size', 20)

        total = len(self._alarms)
        start = (page - 1) * page_size
        end = start + page_size
        alarms = self._alarms[start:end]

        logger.info(f"Dock alarm query: total={total}, page={page}")
        return MessageBuilder.build_reply(msg, result=0, data={
            'total': total,
            'page': page,
            'page_size': page_size,
            'alarms': alarms
        })

    def handle_dock_task_schedule_query(self, msg: dict) -> dict:
        """
        机场任务调度查询 (dock_task_schedule_query)

        Request: { }
        Response: {
            current_task_id,
            task_status,
            next_task_id,
            schedule_enabled
        }
        """
        logger.info("Dock task schedule query")
        return MessageBuilder.build_reply(msg, result=0, data={
            'current_task_id': '',
            'task_status': 'idle',
            'next_task_id': '',
            'schedule_enabled': True
        })

    def handle_dock_task_schedule_set(self, msg: dict) -> dict:
        """
        机场任务调度设置 (dock_task_schedule_set)

        Request: {
            schedule_enabled: bool,
            tasks: [{task_id, execute_time, priority}, ...]
        }
        Response: { result }
        """
        data = msg.get('data', {})
        schedule_enabled = data.get('schedule_enabled', True)
        tasks = data.get('tasks', [])

        logger.info(f"Dock task schedule set: enabled={schedule_enabled}, tasks={len(tasks)}")
        # TODO: 设置任务调度队列

        return MessageBuilder.build_reply(msg, result=0, data={
            'schedule_enabled': schedule_enabled,
            'task_count': len(tasks)
        })

    def handle_dock_emergency_stop(self, msg: dict) -> dict:
        """
        机场急停 (dock_emergency_stop)

        Request: { }
        Response: { result }
        """
        logger.info("Dock emergency stop!")

        # 执行机场急停逻辑
        if self._nest_module:
            try:
                from generated_dialect.gcs_order_protocol import GCSCommand
                self._nest_module.send_gcs_command(GCSCommand.ESTOP_ACTIVATE)
            except Exception as e:
                logger.error(f"Emergency stop failed: {e}")

        return MessageBuilder.build_reply(msg, result=0)

    def handle_dock_reset(self, msg: dict) -> dict:
        """
        机场复位 (dock_reset)

        Request: { reset_type: 0=全部复位 1=舱盖 2=推杆 3=充电 }
        Response: { result }
        """
        data = msg.get('data', {})
        reset_type = data.get('reset_type', 0)
        logger.info(f"Dock reset: type={reset_type}")

        # 执行复位操作
        if self._nest_module:
            try:
                from generated_dialect.gcs_order_protocol import GCSCommand
                self._nest_module.send_gcs_command(GCSCommand.FAULT_RECOVERY)
            except Exception as e:
                logger.error(f"Dock reset failed: {e}")

        return MessageBuilder.build_reply(msg, result=0)

    def handle_dock_weather_query(self, msg: dict) -> dict:
        """
        机场气象查询 (dock_weather_query)

        Request: { }
        Response: {
            temperature, humidity, wind_speed, wind_direction,
            rainfall, atmospheric_pressure, weather_suitable
        }
        """
        logger.info("Dock weather query")
        return MessageBuilder.build_reply(msg, result=0, data={
            'temperature': self._dock_status.get('environment_temperature', 25.0),
            'humidity': self._dock_status.get('humidity', 50),
            'wind_speed': self._dock_status.get('wind_speed', 0.0),
            'wind_direction': 0.0,
            'rainfall': self._dock_status.get('rainfall', 0),
            'atmospheric_pressure': self._dock_status.get('atmospheric_pressure', 101.3),
            'weather_suitable': self._is_weather_suitable()
        })

    def _is_weather_suitable(self) -> bool:
        """判断天气是否适合飞行"""
        wind = self._dock_status.get('wind_speed', 0)
        rain = self._dock_status.get('rainfall', 0)
        return wind <= 10.0 and rain == 0
