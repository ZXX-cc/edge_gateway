"""
DJI Dock 机场管理模块

处理机场特有的状态查询和控制接口，包括：
- 机场状态查询（舱盖、充电、天气、推杆等）
- 机场控制（一键开关盖、充电控制等）
- 机场告警查询
- 机场任务调度状态
- 命令执行状态跟踪与事件上报

参考：DJI Cloud API Dock Feature Set
"""
import time
import uuid
import asyncio
from typing import Dict, Any, Optional, Callable
from src.utils.logger import get_logger
from src.utils.config import Config
from src.utils.message import MessageBuilder
from src.database.connection import get_async_session_local
from src.database.operation_db import OperationDB
from src.generated_dialect.gcs_order_protocol import GCSCommand

logger = get_logger("dock")
alarm_logger = get_logger("alarm")


class DockManager:
    """
    DJI Dock 机场管理器

    职责：
    1. 机场状态聚合与查询
    2. 机场控制指令处理
    3. 机场告警管理
    4. 机场任务调度
    5. 命令执行状态跟踪
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

        # 遥测上报器引用
        self._telemetry_reporter = None
        self._telemetry_processor = None

        # 当前执行的操作
        self._current_operations: Dict[str, dict] = {}

        # 回调
        self._event_callback: Optional[Callable] = None
        self._state_callback: Optional[Callable] = None

    def _schedule_async(self, coro):
        """安全调度异步任务"""
        try:
            loop = asyncio.get_running_loop()
            asyncio.run_coroutine_threadsafe(coro, loop)
        except RuntimeError:
            logger.warning("Cannot schedule async task: no running event loop")

    def set_callbacks(self, event_cb: Callable, state_cb: Callable):
        """设置事件和状态回调"""
        self._event_callback = event_cb
        self._state_callback = state_cb

    def set_nest_module(self, nest_module):
        """注入机巢通信模块"""
        self._nest_module = nest_module

    def set_telemetry_reporter(self, reporter):
        """注入遥测上报器"""
        self._telemetry_reporter = reporter

    def set_telemetry_processor(self, telemetry_processor):
        """注入遥测处理器，用于查询时获取实时无人机状态快照"""
        self._telemetry_processor = telemetry_processor

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
            'drone_status': self._build_live_drone_status(),
            'timestamp': int(time.time() * 1000)
        })

    def _build_live_drone_status(self) -> Dict[str, Any]:
        """构建实时无人机状态快照，不触发 state 上报。"""
        status = self._drone_status.copy()
        if not self._telemetry_processor:
            return status

        try:
            telemetry = self._telemetry_processor.get_current_data()
        except Exception as exc:
            logger.warning(f"Failed to read telemetry snapshot for dock status: {exc}")
            return status

        if not telemetry:
            return status

        connection_status = telemetry.get('connection_status', 'disconnected')
        timestamp = telemetry.get('timestamp', 0.0) or 0.0
        status.update({
            'online': connection_status == 'connected' or timestamp > 0,
            'armed': bool(telemetry.get('armed', status.get('armed', False))),
            'flight_mode': telemetry.get('flight_mode', status.get('flight_mode', 'UNKNOWN')),
            'battery_percent': telemetry.get('batteryRemaining', status.get('battery_percent', 100)),
            'longitude': telemetry.get('longitude', status.get('longitude', 0.0)),
            'latitude': telemetry.get('latitude', status.get('latitude', 0.0)),
            'altitude': telemetry.get('altitude', status.get('altitude', 0.0)),
            'height': telemetry.get('altitudeRelative', telemetry.get('altitudeAGL', status.get('height', 0.0))),
            'speed': telemetry.get('groundSpeed', status.get('speed', 0.0)),
            'vertical_speed': telemetry.get('climbRate', status.get('vertical_speed', 0.0)),
            'heading': telemetry.get('yaw', status.get('heading', 0.0)),
            'satellite_number': telemetry.get('satelliteNumber', status.get('satellite_number', 0)),
            'gps_signal_level': 5 if telemetry.get('gps_fixed', 0) else 0,
        })
        return status

    def handle_dock_control(self, msg: dict) -> dict:
        """
        机场控制 (dock_control)

        Request: {
            action: 'cover_open'|'cover_close'|'putter_push'|'putter_pull'|
                    'charge_start'|'charge_stop'|'light_on'|'light_off'
        }
        Response: { result, operation_id }
        """
        data = msg.get('data', {})
        action = data.get('action', '')
        logger.info(f"Dock control: {action}")

        # 开始操作跟踪
        operation_id = self._start_operation('dock_control', {'action': action})

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
                    self._nest_module.start_charging()
                elif action == 'charge_stop':
                    self._nest_module.stop_charging()
                elif action == 'putter_push':
                    # 推杆推出近似为夹紧释放 (CLAMP_RELEASE)
                    self._nest_module.send_gcs_command(GCSCommand.CLAMP_RELEASE)
                elif action == 'putter_pull':
                    # 推杆收回近似为夹紧 (CLAMP_TIGHTEN)
                    self._nest_module.send_gcs_command(GCSCommand.CLAMP_TIGHTEN)

                # 标记操作完成
                self._complete_operation(operation_id, {'action': action, 'success': True})

            except Exception as e:
                logger.error(f"Dock control failed: {e}")
                self._fail_operation(operation_id, str(e))
                return MessageBuilder.build_reply(msg, result=-1,
                                                 data={'action': action, 'operation_id': operation_id})
        else:
            # 标记操作完成（模拟模式）
            self._complete_operation(operation_id, {'action': action, 'success': True})

        return MessageBuilder.build_reply(msg, result=0,
                                         data={'action': action, 'operation_id': operation_id})

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

    # ---- 操作跟踪与事件上报 ----

    def _start_operation(self, command: str, params: dict = None) -> str:
        """
        开始一个新操作

        Args:
            command: 命令名称
            params: 命令参数

        Returns:
            operation_id: 操作ID
        """
        operation_id = str(uuid.uuid4())
        self._current_operations[operation_id] = {
            'command': command,
            'status': 'running',
            'progress': 0,
            'params': params,
            'start_time': int(time.time() * 1000)
        }

        # 异步保存操作记录到数据库
        self._schedule_async(self._save_operation_record(operation_id, command, params))

        # 上报操作开始事件
        self._report_operation_event(operation_id, 'started', command, params)

        return operation_id

    def _update_operation_progress(self, operation_id: str, progress: int):
        """
        更新操作进度

        Args:
            operation_id: 操作ID
            progress: 进度百分比 (0-100)
        """
        if operation_id in self._current_operations:
            self._current_operations[operation_id]['progress'] = progress

            # 上报进度事件
            op = self._current_operations[operation_id]
            self._report_operation_event(operation_id, 'progress', op['command'],
                                         {'progress': progress})

    def _complete_operation(self, operation_id: str, result: dict = None):
        """
        完成操作

        Args:
            operation_id: 操作ID
            result: 操作结果
        """
        if operation_id in self._current_operations:
            op = self._current_operations[operation_id]
            op['status'] = 'completed'
            op['progress'] = 100
            op['result'] = result
            op['end_time'] = int(time.time() * 1000)

            # 异步更新数据库记录
            self._schedule_async(self._complete_operation_record(operation_id, result))

            # 上报操作完成事件
            self._report_operation_event(operation_id, 'completed', op['command'],
                                         {'result': result})

            # 清理已完成的操作（保留最近的）
            if len(self._current_operations) > 10:
                oldest = min(self._current_operations.keys(),
                            key=lambda k: self._current_operations[k]['start_time'])
                del self._current_operations[oldest]

    def _fail_operation(self, operation_id: str, error_message: str):
        """
        操作失败

        Args:
            operation_id: 操作ID
            error_message: 错误信息
        """
        if operation_id in self._current_operations:
            op = self._current_operations[operation_id]
            op['status'] = 'failed'
            op['error_message'] = error_message
            op['end_time'] = int(time.time() * 1000)

            # 异步更新数据库记录
            self._schedule_async(self._fail_operation_record(operation_id, error_message))

            # 上报操作失败事件
            self._report_operation_event(operation_id, 'failed', op['command'],
                                         {'error': error_message})

    def _report_operation_event(self, operation_id: str, event_type: str,
                                command: str, data: dict = None):
        """
        上报操作事件到云端

        Args:
            operation_id: 操作ID
            event_type: 事件类型 (started/progress/completed/failed)
            command: 命令名称
            data: 事件数据
        """
        event_data = {
            'operation_id': operation_id,
            'event_type': event_type,
            'command': command,
            'timestamp': int(time.time() * 1000),
        }
        if data:
            event_data.update(data)

        # 通过TelemetryReporter上报事件
        if self._telemetry_reporter:
            self._telemetry_reporter.report_event('dock_operation', event_data)

        # 同时通过事件回调上报
        if self._event_callback:
            try:
                self._event_callback(event_data)
            except Exception as e:
                logger.error(f"Event callback error: {e}")

    async def _save_operation_record(self, operation_id: str, command: str, params: dict):
        """异步保存操作记录到数据库"""
        try:
            session_factory = get_async_session_local()
            async with session_factory() as session:
                async with session.begin():
                    await OperationDB.create_operation_async(
                        session,
                        self.gateway_sn,
                        command,
                        params,
                        operation_id=operation_id,
                    )
        except Exception as e:
            logger.error(f"Failed to save operation record: {e}")

    async def _complete_operation_record(self, operation_id: str, result: dict):
        """异步完成操作记录"""
        try:
            session_factory = get_async_session_local()
            async with session_factory() as session:
                async with session.begin():
                    await OperationDB.complete_operation_async(session, operation_id, result)
        except Exception as e:
            logger.error(f"Failed to complete operation record: {e}")

    async def _fail_operation_record(self, operation_id: str, error_message: str):
        """异步标记操作失败"""
        try:
            session_factory = get_async_session_local()
            async with session_factory() as session:
                async with session.begin():
                    await OperationDB.fail_operation_async(session, operation_id, error_message)
        except Exception as e:
            logger.error(f"Failed to fail operation record: {e}")
