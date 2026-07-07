"""
飞行任务管理模块
处理航线任务、指令飞行、任务状态监控
"""
import math
import time
import threading
import asyncio
from typing import List, Dict, Optional, Callable
from uuid import UUID
from pymavlink import mavutil
from src.utils.logger import get_logger
from src.utils.config import Config
from src.utils.message import MessageBuilder
from src.utils.dji_error_codes import DEVICE_COMMAND_TIMEOUT, DEVICE_STATE_UNSUPPORTED, INVALID_COMMAND_FORMAT
from src.mavlink.flight_modes import normalize_flight_mode_request
from src.cloud_api.dock3_wayline import (
    STATUS_CANCELED,
    STATUS_FAILED,
    STATUS_IN_PROGRESS,
    STATUS_OK,
    STATUS_PAUSED,
    STATUS_REJECTED,
    STATUS_SENT,
    normalize_prepare_payload,
    parse_wayline_file,
    normalize_return_payload,
    normalize_undo_payload,
    output_data as wayline_output_data,
    progress_percent,
    validate_flight_id_payload,
)
from src.database.connection import get_async_session_local, init_database_async
from src.database.mission_db import MissionDB
from src.database.waypoint_db import WaypointDB
from src.database.schemas import MissionCreate, WaypointCreate

logger = get_logger("mission")


class MissionManager:
    """
    飞行任务管理器

    职责:
    1. 航线任务管理(上传、执行、暂停、恢复、取消)
    2. 指令飞行DRC(flyto、takeoff、rtl等)
    3. 任务状态监控和进度上报
    4. 飞行模式设置

    API方法:
    - 接收: load_wayline, execute_task, cancel_task, pause_wayline, resume_wayline
    - 接收: flyto, take_off, one_key_return_home, set_flight_mode
    - 上报: task_ready_notify, flight_task_progress_notify, flight_setup_exception_notify
    """

    # 任务状态
    MISSION_IDLE = 0
    MISSION_PREPARING = 1
    MISSION_RUNNING = 2
    MISSION_PAUSED = 3
    MISSION_COMPLETED = 4
    MISSION_FAILED = 5
    MISSION_CANCELED = 6

    # DJI Dock3 wayline_mission_state 枚举映射
    # 参考: https://developer.dji.com/doc/cloud-api-tutorial/cn/api-reference/dock-to-cloud/mqtt/dock/dock3/wayline.html
    WAYLINE_MISSION_STATE_MAP = {
        0: '断连',
        1: '不支持该航点',
        2: '航线准备状态,可上传文件,可执行已有文件',
        3: '航线文件上传中',
        4: '触发开始命令，飞行器触发读航线等逻辑，还未开始任务，处于准备状态',
        5: '进入航线,到第一个航点',
        6: '航线执行',
        7: '航线中断，触发条件：1.用户主动暂停 2.飞控异常',
        8: '航线恢复',
        9: '航线停止',
    }

    # DJI break_reason 枚举映射
    BREAK_REASON_MAP = {
        0: '无异常',
        1: 'Mission ID 不存在，该航线任务未执行',
        2: '不常见错误，建议联系技术支持',
        4: '请求开始/恢复航线任务时，航线文件加载出错，请重新尝试上传文件开始或联系技术支持',
        5: '请求查询断点信息时，查询断点文件失败。请求恢复航线任务时，解析断点类型失败',
        6: '请求开始/结束航线任务时，cmd参数有误，协议请求的指令有误。请求恢复航线任务时，解析断点类型失败',
        7: '请求开始/恢复航线任务时，解析 wpmz 文件超时，请重试',
        257: '航线已经开始，不能再次开始',
        258: '此状态下无法中断航线，只允许在航线执行状态时暂停航线',
        259: '航线未开始，不能结束航线',
        261: '飞行任务冲突，无法获取飞行器控制权，不允许在降落和返航中开始航线',
        262: '该状态下无法恢复航线，只允许在航线暂停状态时恢复航线',
        513: '飞行器超过限高高度',
        514: '飞行器超过限远距离',
        515: '航线穿过限飞区，机场无法执行飞行任务',
        516: '飞行器触发限低',
        517: '飞行器触发避障',
        518: 'RTK 信号差',
        519: '接近禁飞区边界',
        521: '超过机场限飞区限高',
        522: '航线请求起飞失败',
        523: '起飞任务执行失败',
        524: '请求航线任务失败',
        526: '请求航线 RTK 收敛任务失败',
        527: '航线 RTK 收敛任务运行失败',
        528: '接近用户自定义飞行区边界',
        769: 'GPS 信号弱',
        770: '遥控器档位不在 N 档，无法开始任务',
        771: '返航点未刷新',
        772: '当前电量过低无法开始任务',
        773: '低电量返航导致航线中断',
        775: '遥控器与飞行器失联',
        778: '飞行器在地面起桨，不允许开始航线',
        779: '实时仿地过程中，视觉状态异常（如过亮，过暗，两侧亮度不一致）',
        780: '实时仿地用户设置的仿地高度不合法（大于 200 m 或者小于 30 m）',
        781: '实时仿地过程中全局地图计算出错',
        784: '大风返航导致航线中断',
        1281: '用户退出',
        1282: '用户中断',
        1283: '用户触发返航',
        1539: '开始信息（航点 index 或者 progress）错误',
        1540: '使用不支持的坐标系',
        1541: '使用不支持的高度模式',
        1542: '使用不支持的过渡航线模式',
        1543: '使用不支持的 yaw 模式',
        1544: '使用不支持的 yaw 方向调转模式',
        1545: '使用不支持的航点类型',
        1546: '首尾点不能使用协调转弯类型',
        1547: '航线全局速度超过合理范围',
        1548: '航点数量异常',
        1549: '经纬度数据异常',
        1550: '转弯截距异常',
        1551: '航段最大速度超过合理范围',
        1552: '航段目标速度超过合理范围',
        1553: '航点 yaw 角度超过合理范围',
        1555: '断点续飞的 mission_id 输入错误',
        1556: '断点续飞的 progress 信息输入错误',
        1557: '断点续飞的任务状态异常',
        1558: '断点续飞的航点 index 信息输入错误',
        1559: '断点续飞的经纬度信息输入错误',
        1560: '断点续飞的航点 yaw 输入错误',
        1561: '断点续飞的标志位设置错误',
        1563: '航线生成失败',
        1564: '航线运行失败',
        1565: '航线避障紧急刹停',
        1588: '无法识别的动作类型',
        1595: '同一动作组内动作 ID 不能重复',
        1598: '动作 ID 值不能为 65535',
        1602: '动作组数量超过合理范围',
        1603: '动作组生效范围错误',
        1606: '断点续飞中动作 index 超过合理范围',
        1608: '断点信息中触发器运行结果异常',
        1609: '断点续飞中动作组 ID 信息不能重复',
        1610: '断点续飞中动作组位置不能重复',
        1611: '断点续飞中动作组位置超过合理范围',
        1612: '续飞中动作 ID 不在断点信息中',
        1613: '续飞中不能修改动作状态为中断',
        1614: '断点信息错误导致续飞失败',
        1634: '无法识别的动作类型',
        1649: '无法识别的触发器类型',
        65534: '未知错误',
        65535: '未知错误',
    }

    # DJI current_step 枚举映射
    CURRENT_STEP_MAP = {
        0: '初始状态',
        1: '启动前检查，飞行器是否在执行航线中',
        2: '启动前检查，机场是否退出工作模式',
        3: '启动前检查，航线执行中',
        4: '启动前检查，返航中',
        5: '航线执行进入准备状态，开始等待任务下发',
        6: '机场进入工作状态',
        7: '进入开机检查准备工作和开盖准备工作',
        8: '等待飞行系统准备就绪，推送连接建立',
        9: '等待 RTK 源监听有值上报',
        10: '检查 RTK 源是否是机场源，如果不是要重新设置',
        11: '等待飞行控制权通知',
        12: '机场无控制权，抢夺飞行器控制权',
        13: '自定义飞行区一致性检查',
        14: '离线地图一致性检查',
        15: '获取最新 KMZ URL',
        16: '下载 KMZ',
        17: 'KMZ 上传中',
        18: '染色配置',
        19: '飞行器起飞参数设置，备降点设置，起飞高度设置，染色设置',
        20: '飞行器 flyto 起飞参数设置',
        21: 'Home 点设置',
        22: '触发执行航线',
        23: '航线执行中',
        24: '进入返航的检查准备工作',
        25: '飞行器降落机场',
        26: '降落以后的关盖',
        27: '机场退出工作模式',
        28: '机场异常恢复',
        29: '机场上传飞行系统日志',
        30: '相机录像状态检查',
        31: '获取媒体文件数量',
        32: '机场起飞开盖的异常恢复',
        33: '通知任务结果',
        34: '任务执行完成，通过配置文件配置，是否进行主动 log 拉取',
        35: '日志列表拉取 - 飞行器列表',
        36: '日志列表拉取 - 拉取机场列表',
        37: '日志列表拉取 - 上传日志列表结果',
        38: '日志拉取-拉取飞行器日志',
        39: '日志拉取-拉取机场日志',
        40: '日志拉取-压缩飞行器日志',
        41: '日志拉取-压缩机场日志',
        42: '日志拉取-上传飞行器日志',
        43: '日志拉取-上传机场日志',
        44: '日志拉取-通知结果',
        45: '自定义飞行区文件更新准备中',
        46: '自定义飞行区更新中',
        47: '离线地图更新准备中',
        48: '离线地图更新中',
        65533: '结束后等待服务回包',
        65534: '无具体状态',
        65535: 'UNKNOWN',
    }

    def __init__(self):
        self.gateway_sn = Config.get_gateway_sn()

        # 当前任务状态
        self._status = self.MISSION_IDLE
        self._current_task_id: Optional[str] = None
        self._current_wayline: List[dict] = []
        self._current_waypoint_idx = 0
        self._breakpoint_waypoint_idx = 0  # 断点航点索引（用于断点续飞）

        # DJI标准航线任务属性 (flighttask_prepare 下发)
        self._task_type = 0  # 0=立即 1=定时 2=条件
        self._execute_time = 0
        self._task_file = {}  # {url, fingerprint}
        self._task_file_summary = {}
        self._ready_conditions = {}
        self._executable_conditions = {}
        self._break_point = {}
        self._rth_altitude = 100
        self._out_of_control_action = 0
        self._exit_wayline_when_rc_lost = 0
        self._simulate_mission = {}

        # DJI航线任务状态 (wayline_mission_state)
        self._wayline_mission_state = 2  # 默认: 2=航线准备状态
        self._current_step = 0  # 默认: 0=初始状态

        # MAVLink模块引用(由外部注入)
        self._flight_control = None
        self._mode_control = None
        self._mavlink_conn = None  # MAVLinkConnection 引用
        self._waypoint_manager = None  # WaypointManager 引用

        # 回调
        self._event_callback: Optional[Callable] = None

        # 航点监控线程
        self._mission_monitor_thread: Optional[threading.Thread] = None
        self._monitor_running = False

        # 数据库相关
        self._main_loop: Optional[asyncio.AbstractEventLoop] = None
        self._db_initialized = False

        # 关联模块
        self._nest_module = None
        self._gimbal_control = None
        self._camera_controller = None
        self._telemetry_module = None
        self._telemetry_reporter = None

    def set_flight_control(self, flight_control):
        """注入飞行控制模块"""
        self._flight_control = flight_control

    def set_mode_control(self, mode_control):
        """注入模式控制模块"""
        self._mode_control = mode_control

    def set_mavlink_connection(self, mavlink_conn):
        """注入MAVLink连接"""
        self._mavlink_conn = mavlink_conn

    def set_waypoint_manager(self, waypoint_manager):
        """注入航点管理模块"""
        self._waypoint_manager = waypoint_manager

    def set_event_callback(self, callback: Callable):
        """设置事件上报回调"""
        self._event_callback = callback

    def set_nest_module(self, nest_module):
        """注入机巢模块"""
        self._nest_module = nest_module

    def set_gimbal_control(self, gimbal_control):
        """注入云台控制模块"""
        self._gimbal_control = gimbal_control

    def set_camera_controller(self, camera_controller):
        """注入相机控制模块"""
        self._camera_controller = camera_controller

    def set_telemetry_module(self, telemetry_module):
        """注入遥测模块"""
        self._telemetry_module = telemetry_module

    def set_telemetry_reporter(self, telemetry_reporter):
        """注入遥测上报模块"""
        self._telemetry_reporter = telemetry_reporter

    def set_event_loop(self, loop):
        """注入异步事件循环引用"""
        self._main_loop = loop
        logger.info("MissionManager event loop set")

    def _ensure_event_loop(self):
        """确保事件循环已设置"""
        if self._main_loop is None:
            try:
                self._main_loop = asyncio.get_running_loop()
                logger.info("MissionManager captured main event loop")
            except RuntimeError:
                logger.warning("No running event loop detected")

    async def _init_database(self):
        """初始化数据库"""
        if self._db_initialized:
            return
        await init_database_async()
        self._db_initialized = True
        logger.info("MissionManager database initialized")

    async def _save_mission_to_database(self, task_id: str, wayline: List[dict]):
        """保存任务和航点到数据库"""
        try:
            await self._init_database()
            AsyncSessionLocal = get_async_session_local()

            async with AsyncSessionLocal() as session:
                mission_create = MissionCreate(
                    name=task_id,
                    device_id=self.gateway_sn,
                    status="uploaded",
                    execution_conditions={}
                )
                mission = await MissionDB.create_mission(session, mission_create)

                if wayline:
                    waypoints_create = []
                    for wp in wayline:
                        waypoint_create = WaypointCreate(
                            mission_id=mission.id,
                            sequence=wp.get('seq', 0),
                            command=wp.get('command', 16),
                            frame=wp.get('frame', 3),
                            latitude=wp.get('latitude', 0.0),
                            longitude=wp.get('longitude', 0.0),
                            altitude=wp.get('altitude', 50.0),
                            param1=wp.get('param1', 0.0),
                            param2=wp.get('param2', 0.0),
                            param3=wp.get('param3', 0.0),
                            param4=wp.get('param4', 0.0),
                            autocontinue=wp.get('autocontinue', 1),
                            action_type=wp.get('action_type', 'waypoint'),
                            flight_speed=wp.get('speed'),
                            photo_interval=None
                        )
                        waypoints_create.append(waypoint_create)

                    await WaypointDB.batch_create_waypoints_for_mission(
                        session, mission.id, waypoints_create
                    )

                await session.commit()
                logger.info(f"Mission saved to database: {task_id}")
                return str(mission.id)
        except Exception as e:
            logger.error(f"Save mission to database failed: {e}")
            return None

    async def _update_mission_status(self, task_id: str, status: str):
        """更新任务状态到数据库"""
        try:
            await self._init_database()
            AsyncSessionLocal = get_async_session_local()

            async with AsyncSessionLocal() as session:
                mission = await MissionDB.get_mission_by_external_id(session, task_id)
                if mission:
                    await MissionDB.update_mission_status(session, UUID(mission.id), status)
                    await session.commit()
                    logger.info(f"Mission status updated: {task_id} -> {status}")
        except Exception as e:
            logger.error(f"Update mission status failed: {e}")

    def _run_db_coroutine(self, coro):
        """在主事件循环中运行数据库协程（非阻塞版）"""
        self._ensure_event_loop()
        if self._main_loop:
            try:
                asyncio.run_coroutine_threadsafe(coro, self._main_loop)
            except Exception as e:
                logger.error(f"Failed to schedule DB coroutine: {e}")
                if hasattr(coro, "close"):
                    coro.close()
        else:
            logger.warning("Cannot schedule DB coroutine: no event loop available")
            if hasattr(coro, "close"):
                coro.close()

    async def _recover_pending_missions(self):
        """恢复上次中断的任务"""
        try:
            await self._init_database()
            AsyncSessionLocal = get_async_session_local()

            async with AsyncSessionLocal() as session:
                pending_missions = await MissionDB.get_pending_missions(session)
                if pending_missions:
                    logger.info(f"Found {len(pending_missions)} pending missions for recovery")
                    for mission in pending_missions:
                        logger.info(f"Recovering mission: {mission.name}, status: {mission.status}")
                        if mission.status == 'running':
                            self._current_task_id = mission.name
                            self._status = self.MISSION_PAUSED
                            self._wayline_mission_state = 7

                            waypoints = await WaypointDB.get_waypoints_by_mission(session, UUID(mission.id))
                            self._current_wayline = []
                            for wp in waypoints:
                                self._current_wayline.append({
                                    'seq': wp.sequence,
                                    'latitude': wp.latitude,
                                    'longitude': wp.longitude,
                                    'altitude': wp.altitude,
                                    'command': wp.command,
                                    'frame': wp.frame,
                                    'param1': wp.param1,
                                    'param2': wp.param2,
                                    'param3': wp.param3,
                                    'param4': wp.param4,
                                    'autocontinue': wp.autocontinue,
                                    'action_type': wp.action_type,
                                    'speed': wp.flight_speed,
                                })
                            self._current_waypoint_idx = 0
                            self._breakpoint_waypoint_idx = 0

                            logger.info(f"Mission {mission.name} recovered with {len(self._current_wayline)} waypoints")

                            if self._event_callback:
                                event = MessageBuilder.build_event(
                                    'flighttask_progress',
                                    {
                                        'result': 0,
                                        'output': {
                                            'status': 'paused',
                                            'progress': {
                                                'current_step': 33,
                                                'percent': 0
                                            },
                                            'ext': {
                                                'flight_id': mission.name,
                                                'current_waypoint_index': 0,
                                                'wayline_mission_state': 7,
                                                'media_count': 0,
                                                'track_id': '',
                                                'wayline_id': 0,
                                                'break_point': {}
                                            }
                                        }
                                    },
                                    self.gateway_sn
                                )
                                self._event_callback(event)
        except Exception as e:
            logger.error(f"Recover pending missions failed: {e}")

    def recover_missions(self):
        """启动时恢复任务"""
        threading.Thread(
            target=self._run_db_coroutine,
            args=(self._recover_pending_missions(),),
            daemon=True
        ).start()

    def _get_mavlink_master(self):
        """获取MAVLink master对象"""
        if self._mavlink_conn and hasattr(self._mavlink_conn, 'master') and self._mavlink_conn.master:
            return self._mavlink_conn.master
        return None

    def _send_mavlink_message(self, msg) -> bool:
        """发送MAVLink消息"""
        if self._mavlink_conn and hasattr(self._mavlink_conn, 'send_message'):
            self._mavlink_conn.send_message(msg)
            return True
        return False

    # ---- 属性 ----

    @property
    def status(self) -> int:
        return self._status

    @property
    def is_busy(self) -> bool:
        return self._status in (self.MISSION_PREPARING, self.MISSION_RUNNING)

    def _current_progress_percent(self) -> int:
        return progress_percent(self._current_waypoint_idx, len(self._current_wayline))

    def _wayline_ext(self, flight_id: str = None) -> dict:
        ext = {
            'flight_id': flight_id or self._current_task_id or '',
            'current_waypoint_index': self._current_waypoint_idx,
            'wayline_mission_state': self._wayline_mission_state,
            'media_count': 0,
            'track_id': '',
            'wayline_id': 0,
            'waypoint_count': len(self._current_wayline),
        }
        if self._task_file_summary:
            ext['file'] = self._task_file_summary
        if self._break_point:
            ext['break_point'] = self._break_point
        return ext

    def _wayline_reply_data(self, status: str, current_step: int = None,
                            percent: int = None, flight_id: str = None,
                            extra: dict = None) -> dict:
        return wayline_output_data(
            status=status,
            current_step=self._current_step if current_step is None else current_step,
            percent=self._current_progress_percent() if percent is None else percent,
            ext=self._wayline_ext(flight_id),
            extra=extra,
        )

    def _wayline_status_name(self) -> str:
        status_map = {
            self.MISSION_PREPARING: STATUS_SENT,
            self.MISSION_RUNNING: STATUS_IN_PROGRESS,
            self.MISSION_PAUSED: STATUS_PAUSED,
            self.MISSION_COMPLETED: STATUS_OK,
            self.MISSION_FAILED: STATUS_FAILED,
            self.MISSION_CANCELED: STATUS_CANCELED,
        }
        return status_map.get(self._status, STATUS_OK)

    def _emit_wayline_progress(self, status: str, current_step: int = None,
                               percent: int = None, flight_id: str = None):
        if not self._event_callback:
            return
        event = MessageBuilder.build_event(
            'flighttask_progress',
            {
                'result': 0,
                **self._wayline_reply_data(
                    status=status,
                    current_step=current_step,
                    percent=percent,
                    flight_id=flight_id,
                )
            },
            self.gateway_sn
        )
        self._event_callback(event)

    # ---- 参数校验工具 ----

    @staticmethod
    def _check_enum(name: str, value, allowed: set, errors: list):
        """校验枚举值"""
        if value is not None and value not in allowed:
            errors.append(f"{name}={value} 不在允许值 {allowed} 中")

    @staticmethod
    def _check_range(name: str, value, min_val, max_val, errors: list):
        """校验数值范围"""
        if value is not None:
            try:
                v = float(value)
                if min_val is not None and v < min_val:
                    errors.append(f"{name}={value} 小于最小值 {min_val}")
                if max_val is not None and v > max_val:
                    errors.append(f"{name}={value} 大于最大值 {max_val}")
            except (TypeError, ValueError):
                errors.append(f"{name}={value} 不是有效数值")

    @staticmethod
    def _check_length(name: str, value, length: int, errors: list):
        """校验字符串/数字长度"""
        if value is not None:
            s = str(value)
            if len(s) != length:
                errors.append(f"{name} 长度应为 {length}，实际为 {len(s)}")

    @staticmethod
    def _check_type(name: str, value, expected_type, errors: list):
        """校验类型"""
        if value is not None and not isinstance(value, expected_type):
            errors.append(f"{name} 类型错误，应为 {expected_type.__name__}，实际为 {type(value).__name__}")

    # ---- 航线任务API ----

    def handle_load_wayline(self, msg: dict) -> dict:
        """
        处理航线上传/加载 (load_wayline)

        Request: { wayline: { waypoints: [...], global_settings: {...} } }
        Response: { result }
        """
        if self.is_busy:
            return MessageBuilder.build_reply(msg, result=-1, msg_str="Mission in progress")

        data = msg.get('data', {})
        wayline = data.get('wayline', {})
        waypoints = wayline.get('waypoints', [])

        # 解析和验证航线文件
        parsed_waypoints = []
        for i, wp in enumerate(waypoints):
            try:
                parsed_wp = self._parse_waypoint(wp, i)
                if parsed_wp:
                    parsed_waypoints.append(parsed_wp)
            except Exception as e:
                logger.warning(f"Waypoint {i} parse failed: {e}")

        self._current_wayline = parsed_waypoints
        self._current_waypoint_idx = 0
        self._breakpoint_waypoint_idx = 0

        logger.info(f"Wayline loaded: {len(self._current_wayline)} waypoints")

        # 上传航点到飞控
        if self._waypoint_manager and self._current_wayline:
            self._waypoint_manager.clear_waypoints()
            for wp in self._current_wayline:
                wp_dict = {
                    'seq': wp.get('seq', 0),
                    'lat': wp.get('latitude', 0.0),
                    'lon': wp.get('longitude', 0.0),
                    'alt': wp.get('altitude', 50.0),
                    'command': wp.get('command', 16),
                    'frame': wp.get('frame', 3),
                    'param1': wp.get('param1', 0.0),
                    'param2': wp.get('param2', 0.0),
                    'param3': wp.get('param3', 0.0),
                    'param4': wp.get('param4', 0.0),
                    'autocontinue': wp.get('autocontinue', 1),
                }
                self._waypoint_manager.add_waypoint_from_dict(wp_dict)

            upload_ok = self._waypoint_manager.upload_mission(timeout=15.0)
            if upload_ok:
                logger.info(f"Waypoint uploaded to FC: {len(self._current_wayline)} waypoints")
            else:
                logger.warning("Waypoint upload to FC failed")

        # 保存到数据库
        task_id = data.get('task_id', str(int(time.time())))
        threading.Thread(
            target=self._run_db_coroutine,
            args=(self._save_mission_to_database(task_id, self._current_wayline),),
            daemon=True
        ).start()

        # 上报任务准备就绪
        if self._event_callback:
            event = MessageBuilder.build_event(
                'task_ready_notify',
                {
                    'task_id': task_id,
                    'status': 'ready',
                    'waypoint_count': len(self._current_wayline)
                },
                self.gateway_sn
            )
            self._event_callback(event)

        return MessageBuilder.build_reply(msg, result=0)

    def _parse_waypoint(self, wp: dict, index: int) -> Optional[dict]:
        """
        解析单个航点

        支持 DJI Wayline 格式和简化格式:
        - DJI格式: { latitude, longitude, height, speed, action, ... }
        - 简化格式: { lat, lon, alt, ... }
        """
        # 提取经纬度
        lat = wp.get('latitude', wp.get('lat', 0))
        lon = wp.get('longitude', wp.get('lon', 0))
        alt = wp.get('height', wp.get('alt', wp.get('altitude', 50)))
        speed = wp.get('speed', wp.get('waypoint_speed', 5))

        # 航点动作
        actions = []
        wp_actions = wp.get('waypoint_actions', wp.get('actions', []))
        for act in wp_actions:
            actions.append({
                'action_type': act.get('action_type', 0),
                'param': act.get('action_param', act.get('param', 0))
            })

        # 转弯模式
        turn_mode = wp.get('turn_mode', 0)  # 0=协调转弯, 1=点到点

        return {
            'seq': index,
            'latitude': float(lat),
            'longitude': float(lon),
            'altitude': float(alt),
            'speed': float(speed),
            'actions': actions,
            'turn_mode': turn_mode,
            'hover_time': wp.get('hover_time', 0),
            'photo': wp.get('photo', False),
            'record': wp.get('record', False),
        }

    def handle_execute_task(self, msg: dict) -> dict:
        """
        处理任务执行 (execute_task)

        Request: { task_id, flight_type: 1=航线 }
        Response: { result }
        """
        if self._status != self.MISSION_IDLE and self._status != self.MISSION_PAUSED:
            return MessageBuilder.build_reply(msg, result=-1, msg_str="Not ready")

        data = msg.get('data', {})
        self._current_task_id = data.get('task_id', str(int(time.time())))
        self._status = self.MISSION_RUNNING
        self._wayline_mission_state = 6  # 航线执行
        self._current_step = 23  # 航线执行中

        logger.info(f"Task started: {self._current_task_id}, wayline_state=6(航线执行)")

        # 启动航点监控线程
        self.start_mission_monitor()

        # 更新数据库状态
        threading.Thread(
            target=self._run_db_coroutine,
            args=(self._update_mission_status(self._current_task_id, 'running'),),
            daemon=True
        ).start()

        # 机巢联动：如果有机巢模块，请求起飞
        if self._nest_module:
            self._nest_module.request_takeoff()

        # 通过飞行控制模块启动航线任务
        if self._flight_control and self._simulate_mission.get('is_enable') != 1:
            result = self._flight_control.set_mode("AUTO")
            if not result.get("success"):
                logger.warning(f"启动航线任务失败: {result.get('message')}")

        return MessageBuilder.build_reply(msg, result=0)

    def handle_cancel_task(self, msg: dict) -> dict:
        """
        处理任务取消 (cancel_task)

        Request: { task_id }
        Response: { result }
        """
        if self._status not in (self.MISSION_PREPARING, self.MISSION_RUNNING, self.MISSION_PAUSED):
            return MessageBuilder.build_reply(msg, result=-1, msg_str="No active mission")

        self._status = self.MISSION_CANCELED
        self._wayline_mission_state = 9  # 航线停止
        self._current_step = 33  # 通知任务结果
        self.stop_mission_monitor()
        logger.info(f"Task canceled: {self._current_task_id}, wayline_state=9(航线停止)")

        # 更新数据库状态
        if self._current_task_id:
            threading.Thread(
                target=self._run_db_coroutine,
                args=(self._update_mission_status(self._current_task_id, 'canceled'),),
                daemon=True
            ).start()

        # 上报任务取消事件
        if self._event_callback:
            event = MessageBuilder.build_event(
                'flighttask_progress',
                {
                    'result': 0,
                    'output': {
                        'status': 'canceled',
                        'progress': {
                            'current_step': 33,
                            'percent': 0
                        }
                    }
                },
                self.gateway_sn
            )
            self._event_callback(event)

        # 机巢联动：如果有机巢模块，终止起飞或降落
        if self._nest_module:
            self._nest_module.abort_takeoff()
            self._nest_module.abort_landing()

        # 发送返航指令
        if self._flight_control and self._simulate_mission.get('is_enable') != 1:
            result = self._flight_control.one_click_return()
            if not result.get("success"):
                logger.warning(f"返航指令失败: {result.get('message')}")

        return MessageBuilder.build_reply(msg, result=0)

    def handle_pause_wayline(self, msg: dict) -> dict:
        """
        处理任务暂停 (pause_wayline)

        Request: { task_id }
        Response: { result }
        """
        if self._status != self.MISSION_RUNNING:
            return MessageBuilder.build_reply(
                msg,
                result=DEVICE_STATE_UNSUPPORTED,
                data=self._wayline_reply_data(STATUS_REJECTED, current_step=self._current_step),
                msg_str="Mission not running"
            )

        self._status = self.MISSION_PAUSED
        self._wayline_mission_state = 7  # 航线中断
        self.stop_mission_monitor()
        logger.info("Mission paused, wayline_state=7(航线中断)")

        # 记录断点
        self._breakpoint_waypoint_idx = self._current_waypoint_idx

        # 更新数据库状态
        if self._current_task_id:
            threading.Thread(
                target=self._run_db_coroutine,
                args=(self._update_mission_status(self._current_task_id, 'paused'),),
                daemon=True
            ).start()

        # 发送悬停指令
        if self._flight_control and self._simulate_mission.get('is_enable') != 1:
            result = self._flight_control.set_mode("LOITER")
            if not result.get("success"):
                logger.warning(f"暂停航线失败: {result.get('message')}")

        self._emit_wayline_progress(STATUS_PAUSED, current_step=23, percent=self._current_progress_percent())
        return MessageBuilder.build_reply(
            msg,
            result=0,
            data=self._wayline_reply_data(STATUS_PAUSED, current_step=23)
        )

    def handle_resume_wayline(self, msg: dict) -> dict:
        """
        处理任务恢复 (resume_wayline) - 断点续飞

        Request: { task_id }
        Response: { result }
        """
        if self._status != self.MISSION_PAUSED:
            return MessageBuilder.build_reply(
                msg,
                result=DEVICE_STATE_UNSUPPORTED,
                data=self._wayline_reply_data(STATUS_REJECTED, current_step=self._current_step),
                msg_str="Mission not paused"
            )

        self._status = self.MISSION_RUNNING
        self._wayline_mission_state = 8  # 航线恢复
        self._current_step = 22  # 触发执行航线
        logger.info(f"Mission resumed from breakpoint: waypoint {self._breakpoint_waypoint_idx}, wayline_state=8(航线恢复)")

        # 更新数据库状态
        if self._current_task_id:
            threading.Thread(
                target=self._run_db_coroutine,
                args=(self._update_mission_status(self._current_task_id, 'running'),),
                daemon=True
            ).start()

        # 断点续飞：使用 WaypointManager 设置当前航点
        if self._waypoint_manager and self._breakpoint_waypoint_idx > 0:
            logger.info(f"Breakpoint resume: set current waypoint to {self._breakpoint_waypoint_idx}")
            ok = self._waypoint_manager.set_current_waypoint(self._breakpoint_waypoint_idx)
            if not ok:
                logger.warning("Breakpoint set_current_waypoint failed, continuing with existing mission")

        # 启动监控并切换到AUTO模式
        self.start_mission_monitor()
        self._wayline_mission_state = 6  # 航线执行
        if self._flight_control and self._simulate_mission.get('is_enable') != 1:
            result = self._flight_control.set_mode("AUTO")
            if not result.get("success"):
                logger.warning(f"恢复航线失败: {result.get('message')}")

        self._emit_wayline_progress(STATUS_IN_PROGRESS, current_step=23, percent=self._current_progress_percent())
        return MessageBuilder.build_reply(
            msg,
            result=0,
            data=self._wayline_reply_data(STATUS_IN_PROGRESS, current_step=23)
        )

    # ---- 指令飞行DRC API ----

    def handle_flyto(self, msg: dict) -> dict:
        """
        处理flyto指令

        Request: { latitude, longitude, height, speed }
        Response: { result, fly_to_id }
        """
        data = msg.get('data', {})
        lat = data.get('latitude', 0)
        lon = data.get('longitude', 0)
        alt = data.get('height', 50)
        speed = data.get('speed', 5)

        fly_to_id = str(int(time.time() * 1000))
        logger.info(f"Flyto: lat={lat}, lon={lon}, alt={alt}, speed={speed}")

        # 通过MAVLink发送位置目标
        if self._mode_control:
            result = self._mode_control.guided_goto(lat, lon, alt)
            if not result.get("success"):
                logger.warning(f"Flyto指令失败: {result.get('message')}")

        return MessageBuilder.build_reply(
            msg, result=0, data={'fly_to_id': fly_to_id}
        )

    def handle_takeoff(self, msg: dict) -> dict:
        """
        处理一键起飞

        Request: { height }
        Response: { result }
        """
        data = msg.get('data', {})
        height = data.get('height', 30)

        logger.info(f"Takeoff to {height}m")

        # 通过MAVLink执行起飞
        if self._flight_control:
            result = self._flight_control.takeoff(height)
            if not result.get("success"):
                logger.warning(f"起飞指令失败: {result.get('message')}")

        return MessageBuilder.build_reply(msg, result=0)

    def handle_one_key_return_home(self, msg: dict) -> dict:
        """处理一键返航"""
        logger.info("Return to home")

        # 通过MAVLink执行RTL
        if self._flight_control:
            result = self._flight_control.one_click_return()
            if not result.get("success"):
                logger.warning(f"返航指令失败: {result.get('message')}")

        return MessageBuilder.build_reply(msg, result=0)

    def handle_cancel_return_home(self, msg: dict) -> dict:
        """处理取消返航"""
        logger.info("Cancel return home")

        # 切换到悬停模式
        if self._flight_control:
            result = self._flight_control.set_mode("LOITER")
            if not result.get("success"):
                logger.warning(f"取消返航失败: {result.get('message')}")

        return MessageBuilder.build_reply(msg, result=0)

    def handle_set_flight_mode(self, msg: dict) -> dict:
        """
        设置飞行模式

        Request:
        - { "flight_mode": "GUIDED" }
        - { "mode": "GUIDED" }
        - { "mode": 4 }
        - { "mode_code": 4 }
        """
        data = msg.get('data', {})
        ok, normalized, reason = normalize_flight_mode_request(data)
        if not ok or normalized is None:
            logger.warning(f"Invalid set_flight_mode payload: {reason}, data={data}")
            return MessageBuilder.build_reply(msg, result=INVALID_COMMAND_FORMAT, msg_str=reason)

        mode_name = normalized.mode_name
        logger.info(
            "Set flight mode: %s (mode_code=%s, source=%s)",
            mode_name,
            normalized.mode_code,
            normalized.source_key,
        )

        # 通过MAVLink设置模式
        if not self._flight_control:
            reason = "FlightControl not available"
            logger.warning(reason)
            return MessageBuilder.build_reply(msg, result=DEVICE_STATE_UNSUPPORTED, msg_str=reason)

        result = self._flight_control.set_mode(mode_name)
        if not result.get("success"):
            reason = result.get("message") or "set flight mode failed"
            logger.warning(f"设置飞行模式失败: {reason}")
            error_code = DEVICE_COMMAND_TIMEOUT if "超时" in reason or "timeout" in reason.lower() else DEVICE_STATE_UNSUPPORTED
            return MessageBuilder.build_reply(msg, result=error_code, msg_str=reason)

        return MessageBuilder.build_reply(
            msg,
            result=0,
            data={
                "mode": mode_name,
                "mode_code": normalized.mode_code,
            },
        )

    # ---- 进度上报 ----

    def report_progress(self, waypoint_idx: int, total: int, status: str = "executing"):
        """上报航线进度"""
        if self._event_callback:
            progress = {
                'current_waypoint': waypoint_idx,
                'total_waypoints': total,
                'percentage': int(waypoint_idx / total * 100) if total > 0 else 0,
                'status': status
            }
            event = MessageBuilder.build_event(
                'flight_task_progress_notify',
                progress,
                self.gateway_sn
            )
            self._event_callback(event)

    def report_flyto_progress(self, fly_to_id: str, status: str, result: int = 0, **kwargs):
        """上报flyto进度"""
        if self._event_callback:
            data = {
                'fly_to_id': fly_to_id,
                'status': status,  # wayline_progress/wayline_ok/wayline_failed/wayline_cancel
                'result': result,
                **kwargs
            }
            event = MessageBuilder.build_event('fly_to_point_progress', data, self.gateway_sn)
            self._event_callback(event)

    def report_takeoff_progress(self, status: str, **kwargs):
        """上报起飞进度"""
        if self._event_callback:
            data = {'status': status, **kwargs}
            event = MessageBuilder.build_event('take_off_progress', data, self.gateway_sn)
            self._event_callback(event)

    def report_setup_exception(self, flight_id: str, flight_type: int,
                                timeout_time: int = 6, timestamp: float = None):
        """
        上报准备阶段异常 (flight_setup_exception_notify)

        文档格式：
        { sn, flight_id, flight_type, timeout_time, timestamp }

        Constraint:
            flight_type: 1~2
            timeout_time: 2~10, step=2
        """
        # 参数校验
        errors = []
        self._check_enum('flight_type', flight_type, {1, 2}, errors)
        self._check_range('timeout_time', timeout_time, 2, 10, errors)
        if timeout_time is not None and timeout_time % 2 != 0:
            errors.append(f"timeout_time={timeout_time} 应为 2 的倍数")
        if errors:
            logger.warning(f"report_setup_exception validation failed: {'; '.join(errors)}")
            return

        if self._event_callback:
            data = {
                'sn': self.gateway_sn,
                'flight_id': flight_id,
                'flight_type': flight_type,
                'timeout_time': timeout_time,
                'timestamp': timestamp or time.time() * 1000
            }
            event = MessageBuilder.build_event(
                'flight_setup_exception_notify', data, self.gateway_sn
            )
            self._event_callback(event)
            logger.info(f"Setup exception reported: flight={flight_id}, type={flight_type}")

    # DJI设备返航退出状态枚举映射
    _EXIT_HOMING_ACTION_MAP = {
        0: '退出返航退出状态',
        1: '进入返航退出状态',
    }
    _EXIT_HOMING_REASON_MAP = {
        0: '操纵杆油门添加',
        1: '操纵杆间距添加',
        2: '行为树初始化失败',
        3: '被障碍物包围',
        4: '触发限飞限制',
        5: '障碍物距离太近',
        6: '无GPS信号',
        7: 'GPS和VIO位置输出标志为false',
        8: 'GPS和VIO融合位置误差太大',
        9: '短距离回溯',
        10: '近距离触发返航',
    }

    def report_device_exit_homing(self, action: int, reason: int):
        """
        上报设备返航退出状态 (device_exit_homing_notify)

        Direction: up (events)
        Data: {
            sn: str,
            action: enum_int,  # 0=退出返航退出状态, 1=进入返航退出状态
            reason: enum_int    # 见_EXIT_HOMING_REASON_MAP
        }

        Args:
            action: 0=退出返航退出状态, 1=进入返航退出状态
            reason: 退出返航原因枚举值(0-10)
        """
        # 参数有效性校验与映射
        if action not in self._EXIT_HOMING_ACTION_MAP:
            logger.warning(f"Invalid exit homing action: {action}, using raw value")
        if reason not in self._EXIT_HOMING_REASON_MAP:
            logger.warning(f"Invalid exit homing reason: {reason}, using raw value")

        action_desc = self._EXIT_HOMING_ACTION_MAP.get(action, f'未知({action})')
        reason_desc = self._EXIT_HOMING_REASON_MAP.get(reason, f'未知({reason})')

        if self._event_callback:
            data = {
                'sn': self.gateway_sn,
                'action': action,
                'reason': reason
            }
            event = MessageBuilder.build_event(
                'device_exit_homing_notify', data, self.gateway_sn
            )
            self._event_callback(event)
            logger.info(f"Exit homing reported: action={action}({action_desc}), "
                       f"reason={reason}({reason_desc})")

    # DJI status 枚举允许值
    _STATUS_ENUM = {'canceled', 'failed', 'in_progress', 'ok', 'partially_done',
                    'paused', 'rejected', 'sent', 'timeout'}

    def report_flighttask_progress(self, flight_id: str, status: str,
                                   current_step: int, percent: int,
                                   ext: dict = None):
        """
        上报航线任务进度 (flighttask_progress)

        文档格式：
        { result: 0, output: { status, progress: {current_step, percent}, ext: {...} } }

        Constraint:
            status: enum_string {canceled, failed, in_progress, ok, partially_done, paused, rejected, sent, timeout}
            current_step: enum_int {0-48, 65533, 65534, 65535}
            percent: 0~100
        """
        # 参数校验
        errors = []
        if status not in self._STATUS_ENUM:
            errors.append(f"status={status} 不在允许值 {self._STATUS_ENUM} 中")
        valid_steps = set(range(49)) | {65533, 65534, 65535}
        self._check_enum('current_step', current_step, valid_steps, errors)
        self._check_range('percent', percent, 0, 100, errors)
        if errors:
            logger.warning(f"report_flighttask_progress validation failed: {'; '.join(errors)}")
            return

        if self._event_callback:
            output = {
                'status': status,
                'progress': {
                    'current_step': current_step,
                    'percent': percent
                }
            }
            if ext:
                output['ext'] = ext
            event = MessageBuilder.build_event(
                'flighttask_progress',
                {'result': 0, 'output': output},
                self.gateway_sn
            )
            self._event_callback(event)

    def report_flighttask_ready(self, flight_ids: list):
        """
        任务就绪通知 (flighttask_ready)

        Direction: up (events)
        当条件任务的就绪条件满足后，设备主动上报云端。

        Data: { flight_ids: [str] }
        """
        # 参数校验
        errors = []
        if not isinstance(flight_ids, list):
            errors.append("flight_ids 应为数组")
        else:
            for i, fid in enumerate(flight_ids):
                if not isinstance(fid, str):
                    errors.append(f"flight_ids[{i}] 应为字符串")
        if errors:
            logger.warning(f"report_flighttask_ready validation failed: {'; '.join(errors)}")
            return

        if self._event_callback:
            event = MessageBuilder.build_event(
                'flighttask_ready',
                {'flight_ids': flight_ids},
                self.gateway_sn
            )
            self._event_callback(event)
            logger.info(f"Flighttask ready reported: {flight_ids}")

    def report_return_home_info(self, planned_path_points: list,
                                last_point_type: int = 0,
                                flight_id: str = None):
        """
        返航信息 (return_home_info)

        Direction: up (events)
        飞行器实时规划返航路线后，设备主动上报云端。

        Data: {
            planned_path_points: [{latitude, longitude, height}, ...],
            last_point_type: 0=在返航点上空 1=不在返航点上空,
            flight_id: str
        }

        Constraint:
            planned_path_points[].latitude: -90~90
            planned_path_points[].longitude: -180~180
            last_point_type: {0, 1}
        """
        # 参数校验
        errors = []
        self._check_enum('last_point_type', last_point_type, {0, 1}, errors)
        if isinstance(planned_path_points, list):
            for i, pt in enumerate(planned_path_points):
                if not isinstance(pt, dict):
                    errors.append(f"planned_path_points[{i}] 应为对象")
                    continue
                lat = pt.get('latitude')
                lon = pt.get('longitude')
                if lat is not None:
                    self._check_range(f'planned_path_points[{i}].latitude', lat, -90, 90, errors)
                if lon is not None:
                    self._check_range(f'planned_path_points[{i}].longitude', lon, -180, 180, errors)
        else:
            errors.append("planned_path_points 应为数组")
        if errors:
            logger.warning(f"report_return_home_info validation failed: {'; '.join(errors)}")
            return

        if self._event_callback:
            data = {
                'planned_path_points': planned_path_points,
                'last_point_type': last_point_type,
                'flight_id': flight_id or self._current_task_id or ''
            }
            event = MessageBuilder.build_event(
                'return_home_info', data, self.gateway_sn
            )
            self._event_callback(event)
            logger.info(f"Return home info reported: points={len(planned_path_points)}")

    # ---- 航线管理 (大疆标准Wayline接口) ----

    def handle_upload_wayline(self, msg: dict) -> dict:
        """航线上传（大疆标准接口，等同于load_wayline）"""
        return self.handle_load_wayline(msg)

    def handle_wayline_task_create(self, msg: dict) -> dict:
        """航线任务创建"""
        data = msg.get('data', {})
        task_id = data.get('task_id', '')
        logger.info(f"Wayline task create: {task_id}")
        return MessageBuilder.build_reply(msg, result=0, data={'task_id': task_id})

    def handle_wayline_task_execute(self, msg: dict) -> dict:
        """航线任务执行"""
        return self.handle_execute_task(msg)

    def handle_wayline_task_pause(self, msg: dict) -> dict:
        """航线任务暂停"""
        return self.handle_pause_wayline(msg)

    def handle_wayline_task_resume(self, msg: dict) -> dict:
        """航线任务恢复"""
        return self.handle_resume_wayline(msg)

    def handle_wayline_task_cancel(self, msg: dict) -> dict:
        """航线任务取消"""
        return self.handle_cancel_task(msg)

    def handle_wayline_task_query(self, msg: dict) -> dict:
        """航线任务查询"""
        return MessageBuilder.build_reply(msg, result=0, data={
            'status': self._status,
            'task_id': self._current_task_id,
            'waypoint_idx': self._current_waypoint_idx,
            'total_waypoints': len(self._current_wayline)
        })

    def handle_wayline_mission_upload(self, msg: dict) -> dict:
        """航线上传（大疆标准）"""
        return self.handle_load_wayline(msg)

    def handle_wayline_mission_download(self, msg: dict) -> dict:
        """航线下载"""
        logger.info("Wayline mission download")
        return MessageBuilder.build_reply(msg, result=0, data={
            'waypoints': self._current_wayline
        })

    def handle_wayline_mission_delete(self, msg: dict) -> dict:
        """航线删除"""
        logger.info("Wayline mission delete")
        self._current_wayline = []
        return MessageBuilder.build_reply(msg, result=0)

    def handle_wayline_mission_query(self, msg: dict) -> dict:
        """航线查询"""
        logger.info("Wayline mission query")
        return MessageBuilder.build_reply(msg, result=0, data={
            'waypoints': self._current_wayline,
            'count': len(self._current_wayline)
        })

    # ---- 飞行控制扩展 ----

    def handle_go_home(self, msg: dict) -> dict:
        """返航"""
        logger.info("Go home")
        self._wayline_mission_state = 9  # 航线停止
        self._current_step = 24  # 进入返航的检查准备工作
        if self._flight_control:
            result = self._flight_control.one_click_return()
            if not result.get("success"):
                logger.warning(f"返航失败: {result.get('message')}")
        return MessageBuilder.build_reply(msg, result=0)

    def handle_landing(self, msg: dict) -> dict:
        """降落"""
        logger.info("Landing")
        if self._flight_control:
            result = self._flight_control.land()
            if not result.get("success"):
                logger.warning(f"降落失败: {result.get('message')}")
        return MessageBuilder.build_reply(msg, result=0)

    def handle_emergency_stop(self, msg: dict) -> dict:
        """紧急停止"""
        logger.info("Emergency stop!")
        if self._flight_control:
            result = self._flight_control.disarm(force=True)
            if not result.get("success"):
                logger.warning(f"紧急停止失败: {result.get('message')}")
        return MessageBuilder.build_reply(msg, result=0)

    def handle_start_takeoff_to_point(self, msg: dict) -> dict:
        """起飞到指定点"""
        data = msg.get('data', {})
        lat = data.get('latitude', 0)
        lon = data.get('longitude', 0)
        alt = data.get('altitude', 50)
        logger.info(f"Takeoff to point: ({lat}, {lon}, {alt})")
        return MessageBuilder.build_reply(msg, result=0)

    def handle_go_to_altitude(self, msg: dict) -> dict:
        """上升至指定高度"""
        data = msg.get('data', {})
        altitude = data.get('altitude', 50)
        logger.info(f"Go to altitude: {altitude}m")
        return MessageBuilder.build_reply(msg, result=0)

    # ---- 航点上传到飞控 ----

    # ---- 航点监控线程 ----

    def start_mission_monitor(self):
        """启动航点监控线程"""
        if self._monitor_running:
            return
        self._monitor_running = True
        self._mission_monitor_thread = threading.Thread(
            target=self._mission_monitor_loop, daemon=True
        )
        self._mission_monitor_thread.start()
        logger.info("Mission monitor started")

    def stop_mission_monitor(self):
        """停止航点监控线程"""
        self._monitor_running = False
        if self._mission_monitor_thread:
            self._mission_monitor_thread.join(timeout=2.0)
            self._mission_monitor_thread = None
        logger.info("Mission monitor stopped")

    def _mission_monitor_loop(self):
        """航点监控循环：监听 MISSION_CURRENT，上报进度"""
        master = self._get_mavlink_master()
        while self._monitor_running:
            try:
                if master:
                    msg = master.recv_match(type='MISSION_CURRENT', blocking=False, timeout=1.0)
                    if msg:
                        new_idx = msg.seq
                        if new_idx != self._current_waypoint_idx:
                            self._current_waypoint_idx = new_idx
                            logger.info(f"Waypoint changed: {new_idx}/{len(self._current_wayline)}")

                            # 上报进度
                            self._report_mission_progress()

                            # 记录断点（用于断点续飞）
                            self._breakpoint_waypoint_idx = new_idx

                            # 检测任务完成
                            if self._status == self.MISSION_RUNNING and new_idx >= len(self._current_wayline) - 1:
                                self._status = self.MISSION_COMPLETED
                                self._wayline_mission_state = 9  # 航线停止
                                self._current_step = 33  # 通知任务结果
                                self._report_mission_progress(status='completed')

                                # 更新数据库状态
                                if self._current_task_id:
                                    threading.Thread(
                                        target=self._run_db_coroutine,
                                        args=(self._update_mission_status(self._current_task_id, 'completed'),),
                                        daemon=True
                                    ).start()

                                # 机巢联动：如果有机巢模块，请求降落
                                if self._nest_module:
                                    self._nest_module.request_landing()

                                self.stop_mission_monitor()
                                logger.info("Mission completed!, wayline_state=9(航线停止)")
                else:
                    time.sleep(1.0)
            except Exception as e:
                logger.error(f"Mission monitor error: {e}")
                time.sleep(1.0)

    def _report_mission_progress(self, status: str = 'executing'):
        """上报航线任务进度 (flighttask_progress)"""
        total = len(self._current_wayline)
        current = self._current_waypoint_idx
        percent = int(current / total * 100) if total > 0 else 0

        # 状态映射到DJI标准status枚举
        status_map = {
            'executing': 'in_progress',
            'completed': 'ok',
            'failed': 'failed',
            'paused': 'paused',
            'canceled': 'canceled',
        }
        dji_status = status_map.get(status, 'in_progress')

        # 使用精确的 wayline_mission_state (0-9)
        wayline_state = self._wayline_mission_state
        wayline_state_desc = self.WAYLINE_MISSION_STATE_MAP.get(
            wayline_state, f'未知({wayline_state})'
        )

        # 使用动态的 current_step
        current_step = self._current_step
        current_step_desc = self.CURRENT_STEP_MAP.get(
            current_step, f'未知({current_step})'
        )

        # 构建ext字段
        ext = {
            'current_waypoint_index': current,
            'wayline_mission_state': wayline_state,
            'media_count': 0,
            'track_id': '',
            'flight_id': self._current_task_id or '',
            'wayline_id': 0,
        }

        # 断点信息（暂停或失败时上报）
        if status in ('paused', 'failed') and self._break_point:
            bp = self._break_point.copy()
            # break_reason 映射
            break_reason = bp.get('break_reason')
            if break_reason is not None:
                bp['break_reason_desc'] = self.BREAK_REASON_MAP.get(
                    break_reason, f'未知({break_reason})'
                )
            ext['break_point'] = bp

        if self._event_callback:
            event = MessageBuilder.build_event(
                'flighttask_progress',
                {
                    'result': 0,
                    'output': {
                        'ext': ext,
                        'progress': {
                            'current_step': current_step,
                            'percent': percent
                        },
                        'status': dji_status
                    }
                },
                self.gateway_sn
            )
            self._event_callback(event)

    # ---- 大疆标准FlightTask接口 ----

    def handle_flighttask_prepare(self, msg: dict) -> dict:
        """
        飞行任务准备 (flighttask_prepare)

        Direction: down (services)
        Request: {
            flight_id: str,
            execute_time: int,          # 开始执行时间(毫秒时间戳)
            task_type: int,             # 0=立即 1=定时 2=条件
            file: { url, fingerprint }, # 航线文件对象
            ready_conditions: { battery_capacity, begin_time, end_time },
            executable_conditions: { storage_capacity },
            break_point: { index, state, progress, wayline_id },
            rth_altitude: int,
            out_of_control_action: int,      # 0=返航 1=悬停 2=降落
            exit_wayline_when_rc_lost: int,  # 0=继续 1=退出
            simulate_mission: { is_enable, latitude, longitude }
        }
        Response: { result }
        """
        ok, payload, errors = normalize_prepare_payload(msg.get('data', {}))
        if not ok:
            error_msg = "; ".join(errors)
            logger.warning(f"Flighttask prepare validation failed: {error_msg}")
            return MessageBuilder.build_reply(msg, result=INVALID_COMMAND_FORMAT, msg_str=error_msg)

        flight_id = payload['flight_id']
        task_type = payload['task_type']
        execute_time = payload['execute_time']
        file_info = payload['file']
        ready_conditions = payload['ready_conditions']
        executable_conditions = payload['executable_conditions']
        break_point = payload['break_point']
        rth_altitude = payload['rth_altitude']
        out_of_control_action = payload['out_of_control_action']
        exit_wayline_when_rc_lost = payload['exit_wayline_when_rc_lost']
        simulate_mission = payload['simulate_mission']

        logger.info(f"Flighttask prepare: flight_id={flight_id}, type={task_type}, "
                   f"execute_time={execute_time}")

        # 保存任务属性
        self._current_task_id = flight_id
        self._task_type = task_type
        self._execute_time = execute_time
        self._task_file = file_info
        self._task_file_summary, parsed_waypoints = parse_wayline_file(file_info)
        if parsed_waypoints:
            self._current_wayline = parsed_waypoints
            self._current_waypoint_idx = 0
            self._breakpoint_waypoint_idx = 0
            logger.info(
                "Parsed wayline file: flight_id=%s, format=%s, waypoints=%s",
                flight_id,
                self._task_file_summary.get('format'),
                len(parsed_waypoints),
            )
        elif self._task_file_summary.get('warnings'):
            logger.info(
                "Wayline file parse skipped: flight_id=%s, warnings=%s",
                flight_id,
                "; ".join(self._task_file_summary.get('warnings', [])),
            )
        self._ready_conditions = ready_conditions
        self._executable_conditions = executable_conditions
        self._break_point = break_point
        self._rth_altitude = rth_altitude
        self._out_of_control_action = out_of_control_action
        self._exit_wayline_when_rc_lost = exit_wayline_when_rc_lost
        self._simulate_mission = simulate_mission

        self._status = self.MISSION_PREPARING
        self._wayline_mission_state = 2  # 航线准备状态
        self._current_step = 5  # 航线执行进入准备状态

        # ---- 实际航线上传到飞控 ----
        if self._current_wayline and self._waypoint_manager:
            self._waypoint_manager.clear_waypoints()
            for wp in self._current_wayline:
                wp_dict = {
                    'seq': wp.get('seq', 0),
                    'lat': wp.get('latitude', 0.0),
                    'lon': wp.get('longitude', 0.0),
                    'alt': wp.get('altitude', 50.0),
                    'command': wp.get('command', 16),
                    'frame': wp.get('frame', 3),
                    'param1': wp.get('param1', 0.0),
                    'param2': wp.get('param2', 0.0),
                    'param3': wp.get('param3', 0.0),
                    'param4': wp.get('param4', 0.0),
                    'autocontinue': wp.get('autocontinue', 1),
                }
                self._waypoint_manager.add_waypoint_from_dict(wp_dict)

            upload_ok = self._waypoint_manager.upload_mission(timeout=15.0)
            if not upload_ok:
                self._status = self.MISSION_FAILED
                self._wayline_mission_state = 1  # 不支持该航点
                logger.error("Waypoint upload to FC failed")
                return MessageBuilder.build_reply(msg, result=-1, msg_str="航线上传到飞控失败")
            logger.info(f"Waypoint uploaded to FC: {len(self._current_wayline)} waypoints")
        elif self._waypoint_manager and file_info.get('url'):
            logger.info(
                "No local wayline coordinates available for waypoint upload: %s",
                file_info.get('url', '')[:80],
            )

        # 断点续飞设置
        if break_point:
            self._breakpoint_waypoint_idx = break_point.get('index', 0)
            logger.info(f"Breakpoint set: index={break_point.get('index')}, "
                       f"state={break_point.get('state')}, progress={break_point.get('progress')}")

        # 返航高度设置（如飞控支持）
        if rth_altitude and self._flight_control:
            logger.info(f"RTH altitude configured: {rth_altitude}m")

        # 模拟器任务
        if simulate_mission and simulate_mission.get('is_enable') == 1:
            logger.info(f"Simulate mission enabled: lat={simulate_mission.get('latitude')}, "
                       f"lon={simulate_mission.get('longitude')}")

        self._status = self.MISSION_IDLE
        self._emit_wayline_progress(STATUS_SENT, current_step=5, percent=0, flight_id=flight_id)
        self.report_flighttask_ready([flight_id])
        return MessageBuilder.build_reply(
            msg,
            result=0,
            data=self._wayline_reply_data(
                STATUS_SENT,
                current_step=5,
                percent=0,
                flight_id=flight_id,
            )
        )

    def handle_flighttask_execute(self, msg: dict) -> dict:
        """
        飞行任务执行 (flighttask_execute)

        Direction: down (services)
        Request: { flight_id: str }
        Response: { result }
        """
        ok, flight_id, errors = validate_flight_id_payload(msg.get('data', {}), 'flighttask_execute')
        if not ok:
            error_msg = "; ".join(errors)
            logger.warning(f"Flighttask execute validation failed: {error_msg}")
            return MessageBuilder.build_reply(msg, result=INVALID_COMMAND_FORMAT, msg_str=error_msg)

        logger.info(f"Flighttask execute: {flight_id}")
        self._current_task_id = flight_id
        self._status = self.MISSION_RUNNING
        self._wayline_mission_state = 6  # 航线执行
        self._current_step = 23  # 航线执行中

        # 启动航点监控线程
        self.start_mission_monitor()

        # 纯模拟任务只验证 Dock3 协议状态机，不触发真实飞控模式切换。
        if self._flight_control and self._simulate_mission.get('is_enable') != 1:
            result = self._flight_control.set_mode("AUTO")
            if not result.get("success"):
                logger.warning(f"启动航线任务失败: {result.get('message')}")
                self._status = self.MISSION_FAILED
                return MessageBuilder.build_reply(msg, result=-1, msg_str=result.get('message'))
        elif self._simulate_mission.get('is_enable') == 1:
            logger.info("Flighttask execute in simulation mode, skip AUTO mode switch")

        self._emit_wayline_progress(STATUS_IN_PROGRESS, current_step=23, percent=self._current_progress_percent())
        return MessageBuilder.build_reply(
            msg,
            result=0,
            data=self._wayline_reply_data(STATUS_IN_PROGRESS, current_step=23)
        )

    def handle_flighttask_undo(self, msg: dict) -> dict:
        """
        取消任务 (flighttask_undo)

        Direction: down (services)
        Request: { flight_ids: [str] }
        Response: { result }
        """
        ok, flight_ids, errors = normalize_undo_payload(msg.get('data', {}))
        if not ok:
            error_msg = "; ".join(errors)
            logger.warning(f"Flighttask undo validation failed: {error_msg}")
            return MessageBuilder.build_reply(msg, result=INVALID_COMMAND_FORMAT, msg_str=error_msg)

        logger.info(f"Flighttask undo: {flight_ids}")

        # 如果当前任务在取消列表中，则执行取消逻辑
        if self._current_task_id and self._current_task_id in flight_ids:
            self._status = self.MISSION_CANCELED
            self._wayline_mission_state = 9  # 航线停止
            self.stop_mission_monitor()
            logger.info(f"Current task {self._current_task_id} canceled, wayline_state=9(航线停止)")

            # 执行返航
            if self._flight_control and self._simulate_mission.get('is_enable') != 1:
                result = self._flight_control.one_click_return()
                if not result.get("success"):
                    logger.warning(f"返航指令失败: {result.get('message')}")

        self._emit_wayline_progress(STATUS_CANCELED, current_step=28, percent=self._current_progress_percent())
        return MessageBuilder.build_reply(
            msg,
            result=0,
            data=self._wayline_reply_data(STATUS_CANCELED, current_step=28)
        )

    def handle_flighttask_return(self, msg: dict) -> dict:
        """
        飞行任务返航 (flighttask_return)

        Request: { flighttask_id: str, return_type: 0=返航 1=降落 }
        Response: { result }
        """
        ok, task_id, return_type, errors = normalize_return_payload(msg.get('data', {}))
        if not ok:
            error_msg = "; ".join(errors)
            logger.warning(f"Flighttask return validation failed: {error_msg}")
            return MessageBuilder.build_reply(msg, result=INVALID_COMMAND_FORMAT, msg_str=error_msg)

        logger.info(f"Flighttask return: {task_id}, type={return_type}")
        self._wayline_mission_state = 9  # 航线停止
        self._current_step = 24  # 进入返航的检查准备工作
        if self._flight_control and self._simulate_mission.get('is_enable') != 1:
            if return_type == 0:
                result = self._flight_control.one_click_return()
            else:
                result = self._flight_control.land()
            if not result.get("success"):
                logger.warning(f"Flighttask return failed: {result.get('message')}")

        self._emit_wayline_progress(STATUS_IN_PROGRESS, current_step=24, percent=self._current_progress_percent(), flight_id=task_id)
        self.report_return_home_info(
            planned_path_points=[],
            last_point_type=0,
            flight_id=task_id,
        )
        return MessageBuilder.build_reply(
            msg,
            result=0,
            data=self._wayline_reply_data(
                STATUS_IN_PROGRESS,
                current_step=24,
                flight_id=task_id,
                extra={
                    'flight_id': task_id,
                    'return_type': return_type,
                }
            )
        )

    def handle_return_home(self, msg: dict) -> dict:
        """
        一键返航 (return_home)

        Direction: down (services)
        Request: null
        Response: { result, output: { status } }
        """
        logger.info("Return home (one-key)")

        # 参数校验: data 应为 null 或空对象
        data = msg.get('data', {})
        if data and not isinstance(data, dict):
            logger.warning(f"Return home validation failed: data 应为 null 或空对象")
            return MessageBuilder.build_reply(msg, result=INVALID_COMMAND_FORMAT, msg_str="data 应为 null 或空对象")

        self._wayline_mission_state = 9  # 航线停止
        self._current_step = 24  # 进入返航的检查准备工作
        if self._flight_control and self._simulate_mission.get('is_enable') != 1:
            result = self._flight_control.one_click_return()
            if not result.get("success"):
                logger.warning(f"一键返航失败: {result.get('message')}")
                return MessageBuilder.build_reply(msg, result=-1, msg_str=result.get('message'))

        self._emit_wayline_progress(STATUS_IN_PROGRESS, current_step=24, percent=self._current_progress_percent())
        return MessageBuilder.build_reply(
            msg,
            result=0,
            data=self._wayline_reply_data(STATUS_IN_PROGRESS, current_step=24)
        )

    def handle_flighttask_progress(self, msg: dict) -> dict:
        """
        飞行任务进度查询 (flighttask_progress)

        Request: { flighttask_id: str }
        Response: { status, progress, current_waypoint, total_waypoints }
        """
        data = msg.get('data', {})
        task_id = data.get('flight_id', data.get('flighttask_id', self._current_task_id or ''))
        logger.info(f"Flighttask progress query: {task_id}")

        total = len(self._current_wayline)
        progress = self._current_progress_percent()

        return MessageBuilder.build_reply(
            msg,
            result=0,
            data=self._wayline_reply_data(
                self._wayline_status_name(),
                current_step=self._current_step,
                percent=progress,
                flight_id=task_id,
                extra={
                    'flight_id': task_id,
                    'status': self._status,
                    'progress': progress,
                    'current_waypoint': self._current_waypoint_idx,
                    'total_waypoints': total,
                    'file': self._task_file_summary,
                    'task_status': self._get_task_status_name()
                }
            )
        )

    def handle_flighttask_pause(self, msg: dict) -> dict:
        """
        航线暂停 (flighttask_pause)

        Direction: down (services)
        Request: {} / null
        Response: { result }
        """
        logger.info("Flighttask pause")
        return self.handle_pause_wayline(msg)

    def handle_flighttask_resume(self, msg: dict) -> dict:
        """
        航线恢复 (flighttask_resume)

        Direction: down (services)
        注意: 功能等同于 flighttask_recovery / resume_wayline
        """
        logger.info("Flighttask resume")
        return self.handle_resume_wayline(msg)

    def handle_flighttask_ready(self, msg: dict) -> dict:
        """
        任务就绪通知 (flighttask_ready)

        ⚠️ 注意: 此接口 Direction 为 up (events)，由设备主动上报云端。
        此处保留仅为处理可能的 services 调用（如查询），正常情况下不应由云端下发。

        Request: { flight_ids: [...] }
        Response: { result }
        """
        data = msg.get('data', {})
        flight_ids = data.get('flight_ids', [])
        logger.info(f"Flighttask ready (services, unusual): {flight_ids}")
        return MessageBuilder.build_reply(msg, result=0, data={
            'flight_ids': flight_ids
        })

    def handle_return_home_info(self, msg: dict) -> dict:
        """
        返航信息 (return_home_info)

        ⚠️ 注意: 此接口 Direction 为 up (events)，由设备主动上报云端。
        此处保留仅为处理可能的 services 调用，正常情况下不应由云端下发。

        Request: { planned_path_points, last_point_type, flight_id }
        Response: { result }
        """
        data = msg.get('data', {})
        flight_id = data.get('flight_id', '')
        planned_path_points = data.get('planned_path_points', [])
        last_point_type = data.get('last_point_type', 0)
        logger.info(f"Return home info (services, unusual): flight_id={flight_id}")
        return MessageBuilder.build_reply(msg, result=0)

    def handle_flight_setup_abort(self, msg: dict) -> dict:
        """
        取消准备中的任务 (flight_setup_abort)

        Direction: down (services)
        仅支持航线任务和指令飞行任务准备阶段，在Home点设置状态（current_step为21）进行调用。

        Request: null
        Response: { result }
        错误码: 326108=当前状态不支持, 326109=飞行器已起飞不支持取消
        """
        logger.info("Flight setup abort")

        # 参数校验: data 应为 null 或空对象
        data = msg.get('data', {})
        if data and not isinstance(data, dict):
            logger.warning(f"Flight setup abort validation failed: data 应为 null 或空对象")
            return MessageBuilder.build_reply(msg, result=INVALID_COMMAND_FORMAT, msg_str="data 应为 null 或空对象")

        # 状态检查：仅允许在准备阶段取消
        if self._status not in (self.MISSION_PREPARING, self.MISSION_IDLE):
            logger.warning(f"Flight setup abort rejected: current status={self._status}")
            return MessageBuilder.build_reply(
                msg,
                result=326108,
                data=self._wayline_reply_data(STATUS_REJECTED, current_step=self._current_step),
                msg_str="当前状态不支持取消准备"
            )

        self._status = self.MISSION_CANCELED
        self._wayline_mission_state = 9  # 航线停止
        self._current_step = 28  # 机场异常恢复
        self._current_task_id = None
        logger.info("Flight setup aborted, wayline_state=9(航线停止)")
        self._emit_wayline_progress(STATUS_CANCELED, current_step=28, percent=0)
        return MessageBuilder.build_reply(
            msg,
            result=0,
            data=self._wayline_reply_data(STATUS_CANCELED, current_step=28, percent=0)
        )

    def handle_flighttask_create(self, msg: dict) -> dict:
        """
        创建航线任务 (flighttask_create) - 已废弃

        Direction: down (services)
        已被 flighttask_prepare 替代。

        Request: { flight_id, type, file: { url, sign } }
        Response: { result }
        """
        data = msg.get('data', {})
        flight_id = data.get('flight_id', '')
        file_info = data.get('file', {})
        task_type = data.get('type', '')

        # 参数校验
        errors = []
        if not flight_id or not isinstance(flight_id, str):
            errors.append("flight_id 必填且应为字符串")
        if not isinstance(task_type, str):
            errors.append("type 应为字符串")
        if file_info:
            if not isinstance(file_info.get('url'), str):
                errors.append("file.url 应为字符串")
            if not isinstance(file_info.get('sign'), str):
                errors.append("file.sign 应为字符串")
        if errors:
            error_msg = "; ".join(errors)
            logger.warning(f"Flighttask create validation failed: {error_msg}")
            return MessageBuilder.build_reply(msg, result=-1, msg_str=error_msg)

        logger.info(f"Flighttask create (deprecated): {flight_id}, type={task_type}")

        # 保存文件信息兼容处理
        self._current_task_id = flight_id
        self._task_file = {
            'url': file_info.get('url', ''),
            'fingerprint': file_info.get('sign', '')
        }

        return MessageBuilder.build_reply(msg, result=0, data={'flight_id': flight_id})

    def handle_flighttask_recovery(self, msg: dict) -> dict:
        """
        飞行任务恢复 (flighttask_recovery)

        Direction: down
        注意: 功能等同于 flighttask_resume / resume_wayline

        Request: null
        Response: { result }
        """
        logger.info("Flighttask recovery")
        return self.handle_flighttask_resume(msg)

    def handle_return_home_cancel(self, msg: dict) -> dict:
        """
        取消返航 (return_home_cancel)

        Direction: down (services)
        返航后，飞行器会退出航线模式，此时取消返航，飞行器会悬停。

        Request: null
        Response: { result }
        """
        logger.info("Return home cancel")

        # 参数校验: data 应为 null 或空对象
        data = msg.get('data', {})
        if data and not isinstance(data, dict):
            logger.warning(f"Return home cancel validation failed: data 应为 null 或空对象")
            return MessageBuilder.build_reply(msg, result=INVALID_COMMAND_FORMAT, msg_str="data 应为 null 或空对象")

        # 纯模拟任务只验证 Dock3 协议状态机，不触发真实飞控模式切换。
        if self._flight_control and self._simulate_mission.get('is_enable') != 1:
            result = self._flight_control.set_mode("LOITER")
            if not result.get("success"):
                logger.warning(f"取消返航失败: {result.get('message')}")
        elif self._simulate_mission.get('is_enable') == 1:
            logger.info("Return home cancel in simulation mode, skip LOITER mode switch")
        self._emit_wayline_progress(STATUS_PAUSED, current_step=23, percent=self._current_progress_percent())
        return MessageBuilder.build_reply(
            msg,
            result=0,
            data=self._wayline_reply_data(STATUS_PAUSED, current_step=23)
        )

    def handle_flighttask_resource_get(self, msg: dict) -> dict:
        """
        任务资源获取 (flighttask_resource_get)

        ⚠️ 注意: 此接口 Direction 为 up (requests)，应由设备主动请求云端获取资源。
        此处保留仅为兼容可能的 services 调用。

        Request: { flight_id }
        Response: { result, output: { file: { url, fingerprint } } }
        """
        data = msg.get('data', {})
        flight_id = data.get('flight_id', '')

        # 参数校验
        errors = []
        if not flight_id or not isinstance(flight_id, str):
            errors.append("flight_id 必填且应为字符串")
        if errors:
            error_msg = "; ".join(errors)
            logger.warning(f"Flighttask resource get validation failed: {error_msg}")
            return MessageBuilder.build_reply(msg, result=-1, msg_str=error_msg)

        logger.info(f"Flighttask resource get (services, unusual): {flight_id}")

        # 如果本地已有该任务的文件信息，直接返回
        if self._current_task_id == flight_id and self._task_file:
            return MessageBuilder.build_reply(msg, result=0, data={
                'output': {
                    'file': {
                        'url': self._task_file.get('url', ''),
                        'fingerprint': self._task_file.get('fingerprint', ''),
                    },
                    'file_summary': self._task_file_summary,
                }
            })

        return MessageBuilder.build_reply(msg, result=0, data={
            'output': {
                'file': {
                    'url': '',
                    'fingerprint': ''
                }
            }
        })

    def _get_task_status_name(self) -> str:
        """获取任务状态名称"""
        status_map = {
            self.MISSION_IDLE: 'idle',
            self.MISSION_PREPARING: 'preparing',
            self.MISSION_RUNNING: 'running',
            self.MISSION_PAUSED: 'paused',
            self.MISSION_COMPLETED: 'completed',
            self.MISSION_FAILED: 'failed',
            self.MISSION_CANCELED: 'canceled'
        }
        return status_map.get(self._status, 'unknown')

    # ---- 作业管理 ----

    def handle_job_create(self, msg: dict) -> dict:
        """作业创建"""
        data = msg.get('data', {})
        job_id = data.get('job_id', str(int(time.time())))
        logger.info(f"Job create: {job_id}")
        return MessageBuilder.build_reply(msg, result=0, data={'job_id': job_id})

    def handle_job_delete(self, msg: dict) -> dict:
        """作业删除"""
        data = msg.get('data', {})
        job_id = data.get('job_id', '')
        logger.info(f"Job delete: {job_id}")
        return MessageBuilder.build_reply(msg, result=0)

    def handle_job_query(self, msg: dict) -> dict:
        """作业查询"""
        logger.info("Job query")
        return MessageBuilder.build_reply(msg, result=0, data={
            'jobs': [],
            'total': 0
        })

    def handle_fly_to_point(self, msg: dict) -> dict:
        """Fly to point (fly_to_point) - alias of flyto"""
        return self.handle_flyto(msg)
