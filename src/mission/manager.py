"""
飞行任务管理模块
处理航线任务、指令飞行、任务状态监控
"""
import json
import math
import os
import shutil
import time
import threading
import asyncio
from functools import wraps
from typing import Any, List, Dict, Optional, Callable, Tuple
from uuid import UUID
from pymavlink import mavutil
from src.utils.logger import get_logger
from src.utils.config import Config
from src.utils.message import MessageBuilder
from src.utils.command_trace import trace_command
from src.utils.dji_error_codes import (
    DEVICE_COMMAND_TIMEOUT,
    DEVICE_STATE_UNSUPPORTED,
    DOCK_BUSY,
    INVALID_COMMAND_FORMAT,
    WAYLINE_EXECUTION_FAILED,
    WAYLINE_PAUSE_STATE_INVALID,
    WAYLINE_RESUME_STATE_INVALID,
)
from src.mavlink.flight_modes import mode_to_dji_code, normalize_flight_mode_request
from src.cloud_api.dock3_wayline import (
    STATUS_CANCELED,
    STATUS_FAILED,
    STATUS_IN_PROGRESS,
    STATUS_OK,
    STATUS_PAUSED,
    STATUS_REJECTED,
    STATUS_SENT,
    normalize_prepare_payload,
    normalize_in_flight_wayline_payload,
    normalize_return_payload,
    normalize_undo_payload,
    output_data as wayline_output_data,
    parse_wayline_file,
    progress_percent,
    validate_flight_id_payload,
)
from src.database.connection import get_async_session_local, init_database_async
from src.database.mission_db import MissionDB
from src.database.waypoint_db import WaypointDB
from src.database.schemas import MissionCreate, WaypointCreate

logger = get_logger("mission")


def serialize_flighttask(handler):
    """Keep one Dock3 flighttask request from replacing another task mid-flight."""
    @wraps(handler)
    def wrapped(self, *args, **kwargs):
        with self._flighttask_lock:
            return handler(self, *args, **kwargs)
    return wrapped


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
        # MQTT requests can arrive from the test tool and local demo platform
        # at nearly the same time.  A task state transition must be atomic.
        self._flighttask_lock = threading.RLock()
        # The monitor updates waypoint progress on its own thread.  Keep a
        # pause transition and one waypoint update mutually exclusive so a
        # final waypoint cannot overwrite (or be overwritten by) PAUSED.
        self._mission_state_lock = threading.RLock()
        self._current_wayline: List[dict] = []
        self._current_waypoint_idx = 0
        self._breakpoint_waypoint_idx = 0  # 断点航点索引（用于断点续飞）

        # DJI标准航线任务属性 (flighttask_prepare 下发)
        self._task_type = 0  # 0=立即 1=定时 2=条件
        self._execute_time = 0
        self._task_file = {}  # {url, fingerprint}
        self._task_file_summary = {}
        self._wayline_delegated_to_nest = False
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
        self._mission_monitor_lock = threading.Lock()
        self._mission_monitor_thread: Optional[threading.Thread] = None
        self._monitor_running = False
        self._mission_monitor_stop_event = threading.Event()
        self._mission_monitor_generation = 0
        self._battery_safety_action: Optional[str] = None
        self._executed_waypoint_actions = set()

        # 数据库相关
        self._main_loop: Optional[asyncio.AbstractEventLoop] = None
        self._db_initialized = False

        # 关联模块
        self._nest_module = None
        self._gimbal_control = None
        self._camera_controller = None
        self._telemetry_module = None
        self._telemetry_reporter = None
        self._last_takeoff_flow_steps: List[dict] = []
        self._last_completion_flow_steps: List[dict] = []
        self._last_task_failure: Dict[str, Any] = {}

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
                        if mission.status in ('running', 'paused'):
                            waypoints = await WaypointDB.get_waypoints_by_mission(session, UUID(mission.id))
                            restored_wayline = []
                            for wp in waypoints:
                                restored_wayline.append({
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

                            if not self._install_recovered_mission_context(
                                mission.name,
                                restored_wayline,
                            ):
                                return

                            saved_breakpoint = self._load_breakpoint_file()
                            breakpoint_task_id = saved_breakpoint.get("task_id") if saved_breakpoint else None
                            if breakpoint_task_id and breakpoint_task_id != mission.name:
                                logger.warning(
                                    "Ignore breakpoint for different mission: breakpoint=%s mission=%s",
                                    breakpoint_task_id,
                                    mission.name,
                                )
                                self._breakpoint_waypoint_idx = 0
                                self._break_point = {}
                            self._current_waypoint_idx = self._breakpoint_waypoint_idx

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
                                                'percent': self._current_progress_percent()
                                            },
                                            'ext': {
                                                'flight_id': mission.name,
                                                'current_waypoint_index': self._current_waypoint_idx,
                                                'wayline_mission_state': 7,
                                                'media_count': 0,
                                                'track_id': '',
                                                'wayline_id': 0,
                                                'break_point': self._break_point or {}
                                            }
                                        }
                                    },
                                    self.gateway_sn
                                )
                                self._event_callback(event)
        except Exception as e:
            logger.error(f"Recover pending missions failed: {e}")

    def _install_recovered_mission_context(self, task_id: str, wayline: List[dict]) -> bool:
        """Install persisted state only when MQTT has not started a live task."""
        with self._flighttask_lock:
            if self._current_task_id is not None:
                logger.info(
                    "Skip persisted mission recovery: active task already exists: %s",
                    self._current_task_id,
                )
                return False
            self._current_task_id = task_id
            self._status = self.MISSION_PAUSED
            self._wayline_mission_state = 7
            self._current_wayline = list(wayline)
            return True

    def recover_missions(self):
        """启动时恢复任务"""
        threading.Thread(
            target=self._run_db_coroutine,
            args=(self._recover_pending_missions(),),
            daemon=True
        ).start()

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
        return self._status in (
            self.MISSION_PREPARING,
            self.MISSION_RUNNING,
            self.MISSION_PAUSED,
        )

    def _current_progress_percent(self) -> int:
        if self._status == self.MISSION_COMPLETED:
            return 100
        return progress_percent(self._current_waypoint_idx, len(self._current_wayline))

    def _is_valid_current_waypoint_index(self, waypoint_index: Any) -> bool:
        """Return whether a MAVLink MISSION_CURRENT index belongs to this task."""
        if not self._current_wayline:
            return False
        try:
            waypoint_index = int(waypoint_index)
        except (TypeError, ValueError):
            return False
        return 0 <= waypoint_index < len(self._current_wayline)

    def _sync_current_waypoint_from_manager(self) -> None:
        """Best-effort sync before persisting a breakpoint."""
        if not self._waypoint_manager:
            return
        waypoint_index = None
        try:
            if hasattr(self._waypoint_manager, "get_current_waypoint_snapshot"):
                waypoint_index, _revision = self._waypoint_manager.get_current_waypoint_snapshot()
            else:
                waypoint_index = getattr(self._waypoint_manager, "current_waypoint", None)
        except Exception as exc:
            logger.warning("Sync current waypoint from manager failed: %s", exc)
            return
        if not self._is_valid_current_waypoint_index(waypoint_index):
            return
        waypoint_index = int(waypoint_index)
        if waypoint_index > self._current_waypoint_idx:
            logger.info(
                "Sync breakpoint waypoint from WaypointManager: %s -> %s",
                self._current_waypoint_idx,
                waypoint_index,
            )
            self._current_waypoint_idx = waypoint_index

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

    def _reject_flighttask_request(self, msg: dict, requested_flight_id: str,
                                   result: int, reason: str) -> dict:
        """Reply without exposing the active task state as another task's data."""
        active_flight_id = self._current_task_id or ''
        logger.warning(
            "Reject flighttask request: requested=%s active=%s status=%s reason=%s",
            requested_flight_id,
            active_flight_id,
            self._get_task_status_name(),
            reason,
        )
        return MessageBuilder.build_reply(
            msg,
            result=result,
            data=self._wayline_reply_data(
                STATUS_REJECTED,
                current_step=self._current_step,
                flight_id=active_flight_id or requested_flight_id,
                extra={
                    'requested_flight_id': requested_flight_id,
                    'active_flight_id': active_flight_id,
                    'task_status': self._get_task_status_name(),
                },
            ),
            msg_str=reason,
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
                               percent: int = None, flight_id: str = None,
                               extra: dict = None):
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
                    extra=extra,
                )
            },
            self.gateway_sn
        )
        self._event_callback(event)

    def _record_takeoff_flow_step(self, steps: list, name: str, status: str = STATUS_OK,
                                  result: int = 0, detail: dict = None):
        step = {
            'name': name,
            'step': name,
            'status': status,
            'result': result,
            'timestamp': int(time.time() * 1000),
        }
        if detail:
            step.update(detail)
        steps.append(step)
        if self._event_callback:
            self._event_callback(MessageBuilder.build_event(
                'one_key_takeoff_progress',
                {
                    'result': result,
                    'output': {
                        'status': status,
                        'step': name,
                        'steps': list(steps),
                        'flight_id': self._current_task_id or '',
                    }
                },
                self.gateway_sn,
            ))

    def _send_takeoff_flow_nest_command(self, command_name: str) -> dict:
        command_map = {
            'charge_close': 'CHARGE_STOP',
            'cover_open': 'DOOR_OPEN',
            'putter_open': 'CLAMP_RELEASE',
            'putter_close': 'CLAMP_TIGHTEN',
            'cover_close': 'DOOR_CLOSE',
            'charge_open': 'CHARGE_START',
        }
        gcs_name = command_map.get(command_name)
        if not self._nest_module:
            return {
                'success': False,
                'simulated': False,
                'message': 'nest_module_not_available',
                'command': command_name,
            }
        try:
            from src.generated_dialect.gcs_order_protocol import GCSCommand
            command = getattr(GCSCommand, gcs_name)
            if not hasattr(self._nest_module, 'send_gcs_command'):
                return {
                    'success': False,
                    'simulated': False,
                    'message': 'nest_module_missing_send_gcs_command',
                    'command': command_name,
                }
            sent = self._nest_module.send_gcs_command(command)
            if sent is False:
                return {
                    'success': False,
                    'simulated': False,
                    'message': 'send_gcs_command_failed',
                    'command': command_name,
                    'gcs_command': gcs_name,
                }
            return {
                'success': True,
                'simulated': False,
                'message': 'sent',
                'command': command_name,
                'gcs_command': gcs_name,
            }
        except Exception as exc:
            logger.warning("Takeoff flow nest command failed: %s -> %s", command_name, exc)
            return {
                'success': False,
                'simulated': False,
                'message': str(exc),
                'command': command_name,
                'gcs_command': gcs_name,
            }

    def _flow_retry_settings(self) -> Tuple[int, float]:
        """读取真实控制链路重试参数；缺省值偏保守，避免把偶发 ACK 超时当成最终失败。"""
        attempts = Config.get('mission.completion.retry_count', Config.get('mission.retry_count', 3))
        interval = Config.get('mission.completion.retry_interval_seconds', Config.get('mission.retry_interval_seconds', 2.0))
        try:
            attempts = max(1, int(attempts))
        except (TypeError, ValueError):
            attempts = 3
        return attempts, max(0.0, self._as_float(interval, 2.0))

    @staticmethod
    def _classify_task_failure(reason: Any, stage: str = "", result: dict = None) -> dict:
        """Classify task execution failures for retry and platform visibility."""
        message = str(reason or "")
        result = result or {}
        lowered = message.lower()
        retryable_markers = (
            "timeout", "timed out", "超时", "ack", "未收到", "no response",
            "not responding", "disconnected", "disconnect", "connection",
            "network", "mqtt", "temporarily unavailable", "暂时不可用",
            "链路", "连接", "断开", "通信", "握手失败", "upload to flight controller failed",
            "航线上传到飞控失败",
        )
        non_retryable_markers = (
            "reject", "rejected", "denied", "refused", "unsupported",
            "not supported", "invalid", "forbidden", "prearm", "pre-arm",
            "armability", "hardware", "fault", "estop", "emergency",
            "battery", "geofence", "fence", "no mission", "not available",
            "自检未通过", "拒绝", "不支持", "参数", "非法", "硬件", "故障",
            "急停", "电量不足", "地理围栏", "飞控预解锁", "不可用",
        )
        adapter_unavailable = message in {
            'nest_module_not_available',
            'nest_module_missing_send_gcs_command',
            'flight_control_not_available',
            'WaypointManager not available',
            'FlightControl not available',
            'FlightControl safe arm gate unavailable',
        } or message.startswith('flight_control_missing_')
        if adapter_unavailable:
            category = "adapter_unavailable"
            retryable = False
        elif any(marker in lowered for marker in retryable_markers):
            category = "network_or_timeout"
            retryable = True
        elif any(marker in lowered for marker in non_retryable_markers):
            category = "hardware_or_rejected"
            retryable = False
        else:
            category = "unknown_retryable"
            retryable = True
        return {
            "stage": stage,
            "reason": message or "unknown task failure",
            "category": category,
            "retryable": retryable,
            "raw_result": result,
        }

    def _remember_task_failure(self, stage: str, reason: Any, result: dict = None,
                               attempt: int = None, max_attempts: int = None) -> dict:
        failure = self._classify_task_failure(reason, stage=stage, result=result)
        if attempt is not None:
            failure["attempt"] = attempt
        if max_attempts is not None:
            failure["max_attempts"] = max_attempts
            failure["retry_exhausted"] = bool(failure["retryable"] and attempt == max_attempts)
        self._last_task_failure = failure
        return failure

    def _clear_task_failure(self):
        self._last_task_failure = {}

    def _run_takeoff_flow_nest_step(self, steps: list, command_name: str) -> bool:
        attempts, interval = self._flow_retry_settings()
        for attempt in range(1, attempts + 1):
            result = self._send_takeoff_flow_nest_command(command_name)
            success = bool(result.get('success'))
            detail = {
                **result,
                'attempt': attempt,
                'max_attempts': attempts,
            }
            failure = None if success else self._remember_task_failure(
                command_name,
                result.get('message') or f'{command_name} failed',
                result,
                attempt,
                attempts,
            )
            if failure:
                detail.update({
                    'failure_category': failure['category'],
                    'retryable': failure['retryable'],
                    'retry_exhausted': failure.get('retry_exhausted', False),
                })
            self._record_takeoff_flow_step(
                steps,
                command_name,
                status=STATUS_OK if success else STATUS_FAILED,
                result=0 if success else -1,
                detail=detail,
            )
            if success:
                self._clear_task_failure()
                return True
            if failure and not failure.get('retryable'):
                return False
            if attempt < attempts and interval > 0:
                time.sleep(interval)
        return False

    def _run_takeoff_flow_flight_step(self, steps: list, name: str, action: str, *args) -> bool:
        if not self._flight_control:
            self._record_takeoff_flow_step(
                steps,
                name,
                status=STATUS_FAILED,
                result=-1,
                detail={'success': False, 'simulated': False, 'message': 'flight_control_not_available'},
            )
            return False
        handler = getattr(self._flight_control, action, None)
        if not handler:
            self._record_takeoff_flow_step(
                steps,
                name,
                status=STATUS_FAILED,
                result=-1,
                detail={'success': False, 'message': f'flight_control_missing_{action}'},
            )
            return False
        attempts, interval = self._flow_retry_settings()
        for attempt in range(1, attempts + 1):
            try:
                result = handler(*args)
            except Exception as exc:
                result = {'success': False, 'message': str(exc)}
            success = bool(result.get('success', result is True)) if isinstance(result, dict) else bool(result)
            detail = result if isinstance(result, dict) else {'success': success}
            failure = None if success else self._remember_task_failure(
                name,
                detail.get('message') or f'{name} failed',
                detail,
                attempt,
                attempts,
            )
            self._record_takeoff_flow_step(
                steps,
                name,
                status=STATUS_OK if success else STATUS_FAILED,
                result=0 if success else -1,
                detail={
                    **detail,
                    'attempt': attempt,
                    'max_attempts': attempts,
                    'simulated': False,
                    **({
                        'failure_category': failure['category'],
                        'retryable': failure['retryable'],
                        'retry_exhausted': failure.get('retry_exhausted', False),
                    } if failure else {}),
                },
            )
            if success:
                self._clear_task_failure()
                return True
            if failure and not failure.get('retryable'):
                return False
            if attempt < attempts and interval > 0:
                time.sleep(interval)
        return False

    def _request_takeoff_from_nest_for_flow(self, steps: list, flow_name: str) -> bool:
        attempts, interval = self._flow_retry_settings()
        if self._nest_module and hasattr(self._nest_module, 'request_takeoff'):
            last_failure = None
            for attempt in range(1, attempts + 1):
                try:
                    self._nest_module.request_takeoff()
                    self._record_takeoff_flow_step(
                        steps,
                        'nest_takeoff_request',
                        detail={
                            'success': True,
                            'simulated': False,
                            'flow': flow_name,
                            'attempt': attempt,
                            'max_attempts': attempts,
                        },
                    )
                    self._clear_task_failure()
                    return True
                except Exception as exc:
                    result = {'success': False, 'message': str(exc), 'flow': flow_name}
                    last_failure = self._remember_task_failure(
                        'nest_takeoff_request',
                        result['message'],
                        result,
                        attempt,
                        attempts,
                    )
                    self._record_takeoff_flow_step(
                        steps,
                        'nest_takeoff_request',
                        status=STATUS_FAILED,
                        result=-1,
                        detail={
                            **result,
                            'simulated': False,
                            'attempt': attempt,
                            'max_attempts': attempts,
                            'failure_category': last_failure['category'],
                            'retryable': last_failure['retryable'],
                            'retry_exhausted': last_failure.get('retry_exhausted', False),
                        },
                    )
                    if not last_failure.get('retryable'):
                        return False
                    if attempt < attempts and interval > 0:
                        time.sleep(interval)
            return False

        failure = self._remember_task_failure(
            'nest_takeoff_request',
            'nest_module_not_available',
            {'success': False, 'message': 'nest_module_not_available', 'flow': flow_name},
            1,
            attempts,
        )
        self._record_takeoff_flow_step(
            steps,
            'nest_takeoff_request',
            status=STATUS_FAILED,
            result=-1,
            detail={
                'success': False,
                'simulated': False,
                'message': 'nest_module_not_available',
                'flow': flow_name,
                'attempt': 1,
                'max_attempts': attempts,
                'failure_category': failure['category'],
                'retryable': failure['retryable'],
            },
        )
        return False

    def _run_real_wayline_takeoff_flow(self, msg: dict, flight_id: str) -> dict:
        """
        Execute a prepared wayline through the real dock + flight-controller flow.

        The ordering mirrors docs/业务流程.md: preflight self-check, dock prepare,
        dock takeoff permission, aircraft takeoff, then AUTO wayline execution.
        """
        data = msg.get('data', {}) if isinstance(msg.get('data', {}), dict) else {}
        takeoff_height = self._as_float(data.get('takeoff_height', data.get('height', 30)), 30.0)
        resume_from_breakpoint = bool(self._break_point or self._breakpoint_waypoint_idx > 0)
        breakpoint_index = max(0, int(self._breakpoint_waypoint_idx or 0))
        steps: List[dict] = []
        self._last_takeoff_flow_steps = steps
        self._record_takeoff_flow_step(
            steps,
            'flighttask_execute_precheck',
            detail={
                'flight_id': flight_id,
                'waypoint_count': len(self._current_wayline),
                'simulate_mission': False,
                'resume_from_breakpoint': resume_from_breakpoint,
                'breakpoint_index': breakpoint_index if resume_from_breakpoint else None,
            },
        )

        self_check_ok, self_check_reasons, self_check_details = self._run_prepare_self_check(False)
        if not self_check_ok:
            self._record_takeoff_flow_step(
                steps,
                'self_check',
                status=STATUS_FAILED,
                result=-1,
                detail={'success': False, 'errors': self_check_reasons, 'details': self_check_details},
            )
            return self._fail_real_wayline_execute(
                msg,
                flight_id,
                'self_check',
                '; '.join(self_check_reasons) or 'self check failed',
            )
        self._record_takeoff_flow_step(
            steps,
            'self_check',
            detail={'success': True, 'details': self_check_details},
        )

        if resume_from_breakpoint:
            if not self._current_wayline:
                return self._fail_real_wayline_execute(
                    msg,
                    flight_id,
                    'breakpoint_wayline',
                    'Breakpoint resume requires a restored wayline',
                )
            if breakpoint_index >= len(self._current_wayline):
                return self._fail_real_wayline_execute(
                    msg,
                    flight_id,
                    'breakpoint_index',
                    f'Breakpoint index out of range: {breakpoint_index}/{len(self._current_wayline)}',
                )
            upload_ok, upload_reason = self._upload_wayline_to_flight_controller(self._current_wayline)
            self._record_takeoff_flow_step(
                steps,
                'breakpoint_wayline_upload',
                status=STATUS_OK if upload_ok else STATUS_FAILED,
                result=0 if upload_ok else -1,
                detail={
                    'success': upload_ok,
                    'flight_id': flight_id,
                    'waypoint_count': len(self._current_wayline),
                    'breakpoint_index': breakpoint_index,
                    'message': upload_reason,
                },
            )
            if not upload_ok:
                return self._fail_real_wayline_execute(
                    msg,
                    flight_id,
                    'breakpoint_wayline_upload',
                    upload_reason or 'Waypoint upload to flight controller failed',
                )

        for command_name in ('charge_close', 'cover_open', 'putter_open'):
            if not self._run_takeoff_flow_nest_step(steps, command_name):
                return self._fail_real_wayline_execute(
                    msg,
                    flight_id,
                    command_name,
                    f'{command_name} failed',
                )

        if not self._request_takeoff_from_nest_for_flow(steps, 'flighttask_execute'):
            return self._fail_real_wayline_execute(
                msg,
                flight_id,
                'nest_takeoff_request',
                'Nest takeoff request failed',
            )

        if not self._run_takeoff_flow_flight_step(steps, 'flight_takeoff', 'takeoff', takeoff_height):
            return self._fail_real_wayline_execute(
                msg,
                flight_id,
                'flight_takeoff',
                'flight_takeoff failed',
            )

        # The monitor must be alive before AUTO starts consuming the uploaded
        # mission, otherwise waypoint progress and completion recovery can be missed.
        if self.start_mission_monitor() is False:
            return self._fail_real_wayline_execute(
                msg,
                flight_id,
                'mission_monitor',
                'Mission monitor unavailable',
            )

        if resume_from_breakpoint:
            if not self._waypoint_manager:
                return self._fail_real_wayline_execute(
                    msg,
                    flight_id,
                    'breakpoint_seek',
                    'WaypointManager not available for breakpoint resume',
                )
            seek_ok = self._waypoint_manager.set_current_waypoint(breakpoint_index)
            self._record_takeoff_flow_step(
                steps,
                'breakpoint_seek',
                status=STATUS_OK if seek_ok else STATUS_FAILED,
                result=0 if seek_ok else -1,
                detail={
                    'success': seek_ok,
                    'flight_id': flight_id,
                    'breakpoint_index': breakpoint_index,
                },
            )
            if not seek_ok:
                return self._fail_real_wayline_execute(
                    msg,
                    flight_id,
                    'breakpoint_seek',
                    'Set current waypoint for breakpoint resume failed',
                )
            self._current_waypoint_idx = breakpoint_index

        if not self._run_takeoff_flow_flight_step(steps, 'flighttask_execute', 'set_mode', 'AUTO'):
            return self._fail_real_wayline_execute(
                msg,
                flight_id,
                'auto_mode',
                'flighttask_execute failed',
            )

        self._status = self.MISSION_RUNNING
        self._wayline_mission_state = 6  # 航线执行
        self._current_step = 23  # 航线执行中
        self._emit_wayline_progress(
            STATUS_IN_PROGRESS,
            current_step=23,
            percent=self._current_progress_percent(),
            flight_id=flight_id,
            extra={'takeoff_flow_steps': list(steps)},
        )
        return MessageBuilder.build_reply(
            msg,
            result=0,
            data=self._wayline_reply_data(
                STATUS_IN_PROGRESS,
                current_step=23,
                flight_id=flight_id,
                extra={'takeoff_flow_steps': list(steps)},
            ),
        )

    def _bool_config(self, key: str, default: bool = True) -> bool:
        value = Config.get(key, default)
        if isinstance(value, str):
            return value.strip().lower() not in ('0', 'false', 'no', 'off')
        return bool(value)

    def _run_mission_completion_flow(self, flight_id: str, source: str = "mission_completed") -> bool:
        """
        航线末航点完成后的真实闭环：返航 -> 降落 -> 夹紧 -> 关盖 -> 充电。
        这里不生成模拟完成；任一真实 adapter 缺失或控制失败都会让任务进入 failed。
        """
        steps: List[dict] = []
        self._last_completion_flow_steps = steps
        self._record_takeoff_flow_step(
            steps,
            'mission_completed_detected',
            detail={'flight_id': flight_id, 'source': source, 'simulated': False},
        )

        completion_enabled = self._bool_config('mission.completion.enabled', True)
        if not completion_enabled:
            self._record_takeoff_flow_step(
                steps,
                'completion_flow_skipped',
                detail={'flight_id': flight_id, 'reason': 'mission.completion.enabled=false'},
            )
            return True

        if self._bool_config('mission.completion.auto_return_home', True):
            if not self._run_takeoff_flow_flight_step(steps, 'return_home', 'one_click_return'):
                self._fail_mission_completion_flow(flight_id, steps, 'return_home failed')
                return False

        if self._bool_config('mission.completion.auto_land', True):
            if not self._run_takeoff_flow_flight_step(steps, 'landing', 'land'):
                self._fail_mission_completion_flow(flight_id, steps, 'landing failed')
                return False

        if self._bool_config('mission.completion.nest_recovery', True):
            for command_name in ('putter_close', 'cover_close', 'charge_open'):
                if not self._run_takeoff_flow_nest_step(steps, command_name):
                    self._fail_mission_completion_flow(flight_id, steps, f'{command_name} failed')
                    return False

        self._record_takeoff_flow_step(
            steps,
            'completion_flow_completed',
            detail={'flight_id': flight_id, 'simulated': False},
        )
        self._last_completion_flow_steps = list(steps)
        return True

    def _fail_mission_completion_flow(self, flight_id: str, steps: list, reason: str):
        self._status = self.MISSION_FAILED
        self._wayline_mission_state = 7  # 航线执行失败
        self._current_step = 33  # 通知任务结果
        self._last_completion_flow_steps = list(steps)
        failure = self._last_task_failure or self._remember_task_failure("completion_flow", reason)
        self._break_point = {
            **(self._break_point or {}),
            'index': self._current_waypoint_idx,
            'total_waypoints': len(self._current_wayline),
            'break_reason': 2,
            'reason': reason,
            'failure': {
                key: value for key, value in failure.items()
                if key != "raw_result"
            },
        }
        logger.warning("Mission completion flow failed: flight_id=%s, reason=%s", flight_id, reason)
        self._emit_wayline_progress(
            STATUS_FAILED,
            current_step=self._current_step,
            percent=100,
            flight_id=flight_id,
            extra={
                'completion_steps': list(steps),
                'reason': reason,
                'failure': {
                    key: value for key, value in failure.items()
                    if key != "raw_result"
                },
            },
        )
        if flight_id:
            threading.Thread(
                target=self._run_db_coroutine,
                args=(self._update_mission_status(flight_id, 'failed'),),
                daemon=True,
            ).start()

    @staticmethod
    def _as_float(value: Any, default: float = 0.0) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    def _get_nest_snapshot(self) -> Dict[str, Any]:
        if not self._nest_module:
            return {}
        try:
            if hasattr(self._nest_module, "get_environment_data"):
                return self._nest_module.get_environment_data() or {}
            return getattr(self._nest_module, "environment_data", {}) or {}
        except Exception as exc:
            logger.warning("Get nest snapshot failed: %s", exc)
            return {}

    def _get_telemetry_snapshot(self) -> Dict[str, Any]:
        if not self._telemetry_module:
            return {}
        try:
            if hasattr(self._telemetry_module, "get_telemetry_data"):
                return self._telemetry_module.get_telemetry_data() or {}
            if hasattr(self._telemetry_module, "get_current_data"):
                return self._telemetry_module.get_current_data() or {}
            if hasattr(self._telemetry_module, "get_last_telemetry"):
                return self._telemetry_module.get_last_telemetry() or {}
        except Exception as exc:
            logger.warning("Get telemetry snapshot failed: %s", exc)
        return {}

    def _call_nest_action(self, action_name: str, context: str = "") -> bool:
        """Best-effort nest command bridge used by mission state transitions."""
        if not self._nest_module:
            return False
        action = getattr(self._nest_module, action_name, None)
        if not callable(action):
            logger.info(
                "Nest action skipped: action=%s, context=%s, reason=not_supported",
                action_name,
                context,
            )
            return False
        try:
            result = action()
            if result is False:
                logger.warning(
                    "Nest action reported failure: action=%s, context=%s",
                    action_name,
                    context,
                )
                return False
            logger.info("Nest action sent: action=%s, context=%s", action_name, context)
            return True
        except Exception as exc:
            logger.warning(
                "Nest action failed: action=%s, context=%s, error=%s",
                action_name,
                context,
                exc,
            )
            return False

    @staticmethod
    def _delegated_wayline_summary(file_info: Dict[str, Any]) -> Dict[str, Any]:
        source = ""
        if isinstance(file_info, dict):
            source = str(file_info.get("url") or file_info.get("local_path") or file_info.get("path") or "")
        return {
            "source": source,
            "format": "",
            "available": bool(source),
            "parsed": False,
            "delegated_to": "nest",
            "waypoint_count": 0,
            "files": [],
            "warnings": [],
        }

    @staticmethod
    def _should_delegate_wayline_to_nest() -> bool:
        """Use the legacy nest-exec path only when the gateway has no FC link."""
        return (
            not bool(Config.get('mavlink.enabled', True))
            and bool(Config.get('nest_mavlink.enabled', False))
        )

    def _upload_wayline_to_flight_controller(self, waypoints: List[dict]) -> Tuple[bool, str]:
        if not self._waypoint_manager:
            return False, "WaypointManager not available"
        if not waypoints:
            return False, "No waypoints parsed from wayline file"

        self._waypoint_manager.clear_waypoints()
        for wp in waypoints:
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

        attempts, interval = self._flow_retry_settings()
        last_reason = "Waypoint upload to flight controller failed"
        for attempt in range(1, attempts + 1):
            try:
                uploaded = bool(self._waypoint_manager.upload_mission(timeout=15.0))
            except Exception as exc:
                uploaded = False
                last_reason = str(exc) or last_reason
            if uploaded:
                self._clear_task_failure()
                return True, ""

            failure = self._remember_task_failure(
                "wayline_upload",
                last_reason,
                {"success": False, "message": last_reason},
                attempt,
                attempts,
            )
            logger.warning(
                "Waypoint upload attempt failed: attempt=%s/%s category=%s retryable=%s reason=%s",
                attempt,
                attempts,
                failure["category"],
                failure["retryable"],
                last_reason,
            )
            if not failure.get("retryable"):
                break
            if attempt < attempts and interval > 0:
                time.sleep(interval)
        return False, last_reason

    def _available_storage_mb(self) -> Optional[int]:
        for path in (
            Config.get('media.storage_path', ''),
            Config.get('data.path', ''),
            os.getcwd(),
        ):
            if not path:
                continue
            try:
                usage = shutil.disk_usage(os.path.abspath(os.path.expanduser(path)))
                return usage.free // (1024 * 1024)
            except (FileNotFoundError, PermissionError, OSError):
                continue
        return None

    def _breakpoint_file_path(self) -> str:
        return str(Config.get('mission.breakpoint_path', './data/mission_breakpoint.json'))

    def _record_breakpoint(self, reason: str, break_reason: int = 0):
        """记录当前航点断点，确保返航或重启后可以从断点继续执行。"""
        self._sync_current_waypoint_from_manager()
        self._breakpoint_waypoint_idx = self._current_waypoint_idx
        self._break_point = {
            **(self._break_point or {}),
            'index': self._breakpoint_waypoint_idx,
            'total_waypoints': len(self._current_wayline),
            'progress': (
                self._breakpoint_waypoint_idx / len(self._current_wayline)
                if self._current_wayline else 0
            ),
            'break_reason': break_reason,
            'reason': reason,
        }
        self._save_breakpoint_file(reason)

    def _save_breakpoint_file(self, reason: str = ""):
        if not self._current_task_id:
            return
        data = {
            "task_id": self._current_task_id,
            "last_waypoint_idx": self._breakpoint_waypoint_idx,
            "total_waypoints": len(self._current_wayline),
            "break_point": self._break_point or {},
            "reason": reason,
            "updated_at": int(time.time() * 1000),
        }
        path = self._breakpoint_file_path()
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as fp:
                json.dump(data, fp, ensure_ascii=False, indent=2)
            logger.info("Breakpoint saved: %s", data)
        except Exception as exc:
            logger.warning("Save breakpoint failed: %s", exc)

    def _load_breakpoint_file(self) -> Optional[Dict[str, Any]]:
        path = self._breakpoint_file_path()
        try:
            if not os.path.exists(path):
                return None
            with open(path, "r", encoding="utf-8") as fp:
                data = json.load(fp)
            if not isinstance(data, dict):
                return None
            idx = int(data.get("last_waypoint_idx", 0))
            self._breakpoint_waypoint_idx = max(0, idx)
            self._break_point = data.get("break_point") if isinstance(data.get("break_point"), dict) else {}
            if data.get("task_id") and not self._current_task_id:
                self._current_task_id = str(data.get("task_id"))
            logger.info("Breakpoint loaded: %s", data)
            return data
        except Exception as exc:
            logger.warning("Load breakpoint failed: %s", exc)
            return None

    def _clear_breakpoint_file(self):
        path = self._breakpoint_file_path()
        try:
            if os.path.exists(path):
                os.remove(path)
                logger.info("Breakpoint cleared: %s", path)
        except Exception as exc:
            logger.warning("Clear breakpoint failed: %s", exc)

    def _run_prepare_self_check(self, simulate_enabled: bool) -> Tuple[bool, List[str], Dict[str, Any]]:
        """Run the P0 preflight gates used before reporting flighttask_ready."""
        reasons: List[str] = []
        details: Dict[str, Any] = {}

        nest = self._get_nest_snapshot()
        telemetry = self._get_telemetry_snapshot()
        if nest:
            details['nest'] = {
                key: nest.get(key) for key in (
                    'nest_heartbeat', 'plc_heartbeat', 'drone_heartbeat',
                    'estop_state', 'fault_state', 'system_busy',
                    'emergency_landing', 'takeoff_allowed', 'gate_state',
                    'wind_speed', 'rainfall', 'error_code',
                ) if key in nest
            }
            last_update = self._as_float(nest.get('last_update'), 0.0)
            data_timeout = self._as_float(Config.get('nest.data_timeout', 10), 10.0)
            if last_update and time.time() - last_update > data_timeout:
                reasons.append("机巢状态数据超时")

            if nest.get('nest_heartbeat') is False:
                reasons.append("机巢心跳异常")
            if nest.get('plc_heartbeat') is False:
                reasons.append("PLC心跳异常")
            if nest.get('estop_state'):
                reasons.append("机巢急停已触发")
            if nest.get('fault_state') or self._as_float(nest.get('error_code'), 0.0) > 0:
                reasons.append(f"机巢故障状态: error_code={nest.get('error_code', 0)}")
            if nest.get('system_busy'):
                reasons.append("机巢系统忙")
            if nest.get('emergency_landing'):
                reasons.append("机巢处于迫降流程")
            if nest.get('gate_state') == 6:
                reasons.append("舱盖状态异常")

            max_wind = self._as_float(Config.get('mission.precheck.max_wind_speed', 10.0), 10.0)
            wind_speed = self._as_float(nest.get('wind_speed'), 0.0)
            rainfall = self._as_float(nest.get('rainfall'), 0.0)
            if wind_speed > max_wind:
                reasons.append(f"风速过大: {wind_speed:.1f}m/s > {max_wind:.1f}m/s")
            if rainfall > 0:
                reasons.append(f"降雨量不适合飞行: {rainfall:g}")

        if telemetry:
            details['telemetry'] = {
                key: telemetry.get(key) for key in (
                    'connection_status', 'batteryRemaining', 'satelliteNumber',
                    'gps_signal_level', 'gps_fixed', 'armed', 'flight_mode',
                ) if key in telemetry
            }
            connection_status = telemetry.get('connection_status')
            if connection_status and connection_status != 'connected' and not simulate_enabled:
                reasons.append(f"飞控遥测未连接: {connection_status}")

            required_battery = self._ready_conditions.get('battery_capacity')
            if required_battery is not None:
                battery = self._as_float(telemetry.get('batteryRemaining'), -1.0)
                if battery >= 0 and battery < float(required_battery):
                    reasons.append(f"电量不足: {battery:.0f}% < {required_battery}%")

        storage_required = self._executable_conditions.get('storage_capacity')
        if storage_required:
            available_mb = self._available_storage_mb()
            details['available_storage_mb'] = available_mb
            if available_mb is not None and available_mb < int(storage_required):
                reasons.append(f"剩余存储不足: {available_mb}MB < {storage_required}MB")

        if self._task_file_summary.get('warnings'):
            details['wayline_warnings'] = self._task_file_summary.get('warnings', [])

        return not reasons, reasons, details

    def _fail_prepare_self_check(self, msg: dict, flight_id: str, reasons: List[str],
                                 details: Dict[str, Any]) -> dict:
        reason_text = "; ".join(reasons) or "飞前自检失败"
        self._status = self.MISSION_FAILED
        self._wayline_mission_state = 1
        self._current_step = 5
        self._break_point = {
            **(self._break_point or {}),
            'break_reason': 2,
            'reason': reason_text,
            'details': details,
        }
        logger.warning("Flighttask prepare self-check failed: %s", reason_text)
        self.report_setup_exception(flight_id, flight_type=1, timeout_time=6)
        self._emit_wayline_progress(STATUS_FAILED, current_step=5, percent=0, flight_id=flight_id)
        return MessageBuilder.build_reply(
            msg,
            result=WAYLINE_EXECUTION_FAILED,
            data=self._wayline_reply_data(
                STATUS_FAILED,
                current_step=5,
                percent=0,
                flight_id=flight_id,
                extra={'errors': reasons, 'self_check': details},
            ),
            msg_str=reason_text,
        )

    def _handle_battery_safety(self) -> bool:
        """Return True when the active mission monitor should stop."""
        if self._status != self.MISSION_RUNNING:
            return False

        telemetry = self._get_telemetry_snapshot()
        if not telemetry:
            return False

        # ``batteryRemaining=0`` is the historical default before a MAVLink
        # battery frame arrives.  It must not be treated as a real 0% battery
        # in SITL or during connection warm-up.  A received SYS_STATUS or
        # BATTERY_STATUS frame marks this field as available; an explicit true
        # keeps compatibility with external telemetry adapters.
        if telemetry.get('battery_available') is not True:
            return False

        battery = self._as_float(telemetry.get('batteryRemaining'), -1.0)
        if battery < 0:
            return False

        rtl_threshold = self._as_float(
            Config.get('device.limits.return_home_power', Config.get('mission.safety.return_home_power', 30)),
            30.0,
        )
        land_threshold = self._as_float(
            Config.get('mission.safety.critical_battery_power', Config.get('device.limits.critical_battery_power', 15)),
            15.0,
        )

        if battery <= land_threshold and self._battery_safety_action != 'land':
            reason = f"严重低电量自动降落: {battery:.0f}% <= {land_threshold:.0f}%"
            logger.warning(reason)
            failure = self._remember_task_failure("battery_safety", reason)
            self._battery_safety_action = 'land'
            self._status = self.MISSION_FAILED
            self._wayline_mission_state = 7
            self._current_step = 28
            self._break_point = {
                **(self._break_point or {}),
                'index': self._current_waypoint_idx,
                'break_reason': 773,
                'reason': reason,
                'failure': {
                    key: value for key, value in failure.items()
                    if key != "raw_result"
                },
            }
            self._breakpoint_waypoint_idx = self._current_waypoint_idx
            self._save_breakpoint_file(reason)
            if self._flight_control:
                result = self._flight_control.land()
                if not result.get("success"):
                    logger.warning("Critical battery land failed: %s", result.get("message"))
            self._report_mission_progress(status='failed')
            return True

        if battery <= rtl_threshold and self._battery_safety_action is None:
            reason = f"低电量自动返航: {battery:.0f}% <= {rtl_threshold:.0f}%"
            logger.warning(reason)
            self._battery_safety_action = 'rtl'
            self._status = self.MISSION_PAUSED
            self._wayline_mission_state = 7
            self._current_step = 24
            self._break_point = {
                **(self._break_point or {}),
                'index': self._current_waypoint_idx,
                'break_reason': 773,
                'reason': reason,
            }
            self._breakpoint_waypoint_idx = self._current_waypoint_idx
            self._save_breakpoint_file(reason)
            if self._flight_control:
                result = self._flight_control.one_click_return()
                if not result.get("success"):
                    logger.warning("Low battery return failed: %s", result.get("message"))
            self._report_mission_progress(status='paused')
            self.report_return_home_info(
                planned_path_points=[],
                last_point_type=0,
                flight_id=self._current_task_id,
            )
            return True

        return False

    def _waypoint_at(self, index: int) -> Optional[dict]:
        if 0 <= index < len(self._current_wayline):
            return self._current_wayline[index]
        return None

    def _send_change_speed(self, speed: float) -> bool:
        if not self._flight_control or not hasattr(self._flight_control, "_send_command_long"):
            return False
        command = getattr(mavutil.mavlink, "MAV_CMD_DO_CHANGE_SPEED", 178)
        return bool(self._flight_control._send_command_long(
            command,
            param1=0,
            param2=speed,
            param3=-1,
            param4=0,
        ))

    def _execute_waypoint_actions(self, waypoint_index: int):
        if waypoint_index in self._executed_waypoint_actions:
            return
        waypoint = self._waypoint_at(waypoint_index)
        if not waypoint:
            return

        self._executed_waypoint_actions.add(waypoint_index)
        actions = list(waypoint.get('actions') or [])
        if waypoint.get('photo') and not any(str(action.get('action_type')).lower() == 'photo' for action in actions):
            actions.append({'action_type': 'photo'})
        if waypoint.get('hover_time') and not any(str(action.get('action_type')).lower() == 'hover' for action in actions):
            actions.append({'action_type': 'hover', 'param': waypoint.get('hover_time')})

        for action in actions:
            action_type = str(action.get('action_type', action.get('func', ''))).lower()
            try:
                if action_type in {'photo', 'takephoto', 'take_photo', '1'}:
                    if self._camera_controller and hasattr(self._camera_controller, 'handle_camera_photo_take'):
                        self._camera_controller.handle_camera_photo_take({'data': {'camera_index': 0}})
                        logger.info("Waypoint %s photo action executed", waypoint_index)
                    elif self._gimbal_control and hasattr(self._gimbal_control, 'take_photo'):
                        self._gimbal_control.take_photo()
                        logger.info("Waypoint %s gimbal photo action executed", waypoint_index)
                    else:
                        logger.warning("Waypoint %s photo action skipped: no camera controller", waypoint_index)
                elif action_type in {'gimbal', 'gimbalrotate', 'gimbal_rotate'}:
                    pitch = self._as_float(action.get('pitch'), self._as_float(action.get('param'), 0.0))
                    yaw = self._as_float(action.get('yaw'), 0.0)
                    if self._gimbal_control and hasattr(self._gimbal_control, 'turn_to'):
                        self._gimbal_control.turn_to(yaw, pitch)
                        logger.info("Waypoint %s gimbal action executed: yaw=%s pitch=%s", waypoint_index, yaw, pitch)
                    else:
                        logger.warning("Waypoint %s gimbal action skipped: no gimbal controller", waypoint_index)
                elif action_type in {'speed', 'changespeed', 'change_speed'}:
                    speed = self._as_float(action.get('param'), 0.0)
                    if speed > 0 and self._send_change_speed(speed):
                        logger.info("Waypoint %s speed action executed: %.1fm/s", waypoint_index, speed)
                    else:
                        logger.warning("Waypoint %s speed action skipped/failed: %s", waypoint_index, speed)
                elif action_type in {'hover', 'wait', 'delay'}:
                    hover_time = self._as_float(action.get('param'), 0.0)
                    logger.info("Waypoint %s hover action observed: %.1fs", waypoint_index, hover_time)
                else:
                    logger.info("Waypoint %s unsupported action observed: %s", waypoint_index, action)
            except Exception as exc:
                logger.warning("Waypoint %s action failed: %s", waypoint_index, exc)

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
        self._battery_safety_action = None
        self._executed_waypoint_actions.clear()

        # 启动航点监控线程
        if self.start_mission_monitor() is False:
            self._status = self.MISSION_FAILED
            self._wayline_mission_state = 1
            return MessageBuilder.build_reply(
                msg,
                result=DEVICE_STATE_UNSUPPORTED,
                msg_str="Mission monitor unavailable",
            )

        # 更新数据库状态
        threading.Thread(
            target=self._run_db_coroutine,
            args=(self._update_mission_status(self._current_task_id, 'running'),),
            daemon=True
        ).start()

        # 机巢联动：如果有机巢模块，请求起飞
        self._call_nest_action("request_takeoff", "execute_task")

        # 通过飞行控制模块启动航线任务
        if not self._flight_control:
            self._status = self.MISSION_FAILED
            self._wayline_mission_state = 1
            return MessageBuilder.build_reply(
                msg,
                result=WAYLINE_EXECUTION_FAILED,
                msg_str="FlightControl not available",
            )
        result = self._flight_control.set_mode("AUTO")
        if not result.get("success"):
            self._status = self.MISSION_FAILED
            self._wayline_mission_state = 1
            reason = result.get("message") or "AUTO mode switch failed"
            logger.warning(f"启动航线任务失败: {reason}")
            return MessageBuilder.build_reply(
                msg,
                result=WAYLINE_EXECUTION_FAILED,
                msg_str=reason,
            )

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
        self._call_nest_action("abort_takeoff", "cancel_task")
        self._call_nest_action("abort_landing", "cancel_task")

        # 发送返航指令
        if self._flight_control:
            result = self._flight_control.one_click_return()
            if not result.get("success"):
                logger.warning(f"返航指令失败: {result.get('message')}")

        return MessageBuilder.build_reply(msg, result=0)

    @serialize_flighttask
    def handle_pause_wayline(self, msg: dict) -> dict:
        """
        处理任务暂停 (pause_wayline)

        Request: { task_id }
        Response: { result }
        """
        # Hold this lock across the verified flight-mode transition.  A
        # MISSION_CURRENT already being processed finishes first; subsequent
        # updates wait and then observe PAUSED, preventing completion/pause
        # state races and waypoint actions after the hold is confirmed.
        with self._mission_state_lock:
            if self._status != self.MISSION_RUNNING:
                return MessageBuilder.build_reply(
                    msg,
                    result=WAYLINE_PAUSE_STATE_INVALID,
                    data=self._wayline_reply_data(STATUS_REJECTED, current_step=self._current_step),
                    msg_str="Mission not running"
                )

            if not self._flight_control:
                reason = "FlightControl not available"
                logger.warning(f"暂停航线失败: {reason}")
                return MessageBuilder.build_reply(
                    msg,
                    result=WAYLINE_EXECUTION_FAILED,
                    data=self._wayline_reply_data(STATUS_REJECTED, current_step=self._current_step),
                    msg_str=reason,
                )

            if hasattr(self._flight_control, "pause_mission"):
                result = self._flight_control.pause_mission()
            else:
                result = self._flight_control.set_mode("LOITER")
            if not result.get("success"):
                reason = result.get("message") or "pause mission failed"
                logger.warning(f"暂停航线失败: {reason}")
                error_code = (
                    DEVICE_COMMAND_TIMEOUT
                    if "超时" in reason or "timeout" in reason.lower()
                    else WAYLINE_EXECUTION_FAILED
                )
                return MessageBuilder.build_reply(
                    msg,
                    result=error_code,
                    data=self._wayline_reply_data(STATUS_REJECTED, current_step=self._current_step),
                    msg_str=reason,
                )

            logger.info(
                "Dock3 pause control confirmed: strategy=%s, target_mode=%s, native=%s",
                result.get("pause_strategy", "legacy"),
                result.get("target_mode"),
                result.get("native_pause"),
            )

            self._status = self.MISSION_PAUSED
            self._wayline_mission_state = 7  # 航线中断

        self.stop_mission_monitor()
        logger.info("Mission paused, wayline_state=7(航线中断)")

        # 记录断点
        self._record_breakpoint("用户暂停航线")

        # 更新数据库状态
        if self._current_task_id:
            threading.Thread(
                target=self._run_db_coroutine,
                args=(self._update_mission_status(self._current_task_id, 'paused'),),
                daemon=True
            ).start()

        self._emit_wayline_progress(STATUS_PAUSED, current_step=23, percent=self._current_progress_percent())
        return MessageBuilder.build_reply(
            msg,
            result=0,
            data=self._wayline_reply_data(STATUS_PAUSED, current_step=23)
        )

    @serialize_flighttask
    def handle_resume_wayline(self, msg: dict) -> dict:
        """
        处理任务恢复 (resume_wayline) - 断点续飞

        Request: { task_id }
        Response: { result }
        """
        if self._status != self.MISSION_PAUSED:
            return MessageBuilder.build_reply(
                msg,
                result=WAYLINE_RESUME_STATE_INVALID,
                data=self._wayline_reply_data(STATUS_REJECTED, current_step=self._current_step),
                msg_str="Mission not paused"
            )

        saved_breakpoint = self._load_breakpoint_file()
        if saved_breakpoint and saved_breakpoint.get("task_id"):
            logger.info(
                "Resume using persisted breakpoint: task=%s waypoint=%s",
                saved_breakpoint.get("task_id"),
                self._breakpoint_waypoint_idx,
            )

        if not self._flight_control:
            reason = "FlightControl not available"
            logger.warning(f"恢复航线失败: {reason}")
            return MessageBuilder.build_reply(
                msg,
                result=WAYLINE_EXECUTION_FAILED,
                data=self._wayline_reply_data(STATUS_REJECTED, current_step=self._current_step),
                msg_str=reason,
            )

        # 真实飞控任务必须先预留监控线程，再向飞控发送断点/AUTO 指令。监控线程
        # 在任务仍为 PAUSED 时只等待，不消费航点；这样启动失败不会留下无监控
        # 的 AUTO 飞行。
        if self.start_mission_monitor() is False:
            return MessageBuilder.build_reply(
                msg,
                result=WAYLINE_EXECUTION_FAILED,
                data=self._wayline_reply_data(STATUS_REJECTED, current_step=23),
                msg_str="Mission monitor unavailable",
            )

        # 断点续飞：使用 WaypointManager 设置当前航点
        if self._waypoint_manager and self._breakpoint_waypoint_idx > 0:
            logger.info(f"Breakpoint resume: set current waypoint to {self._breakpoint_waypoint_idx}")
            ok = self._waypoint_manager.set_current_waypoint(self._breakpoint_waypoint_idx)
            if not ok:
                logger.warning("Breakpoint set_current_waypoint failed, continuing with existing mission")

        if hasattr(self._flight_control, "resume_mission"):
            result = self._flight_control.resume_mission()
        else:
            result = self._flight_control.set_mode("AUTO")
        if not result.get("success"):
            self.stop_mission_monitor()
            reason = result.get("message") or "resume mission failed"
            logger.warning(f"恢复航线失败: {reason}")
            error_code = (
                DEVICE_COMMAND_TIMEOUT
                if "超时" in reason or "timeout" in reason.lower()
                else WAYLINE_EXECUTION_FAILED
            )
            return MessageBuilder.build_reply(
                msg,
                result=error_code,
                data=self._wayline_reply_data(STATUS_REJECTED, current_step=self._current_step),
                msg_str=reason,
            )

        logger.info(
            "Dock3 resume control confirmed: strategy=%s, target_mode=%s, native=%s",
            result.get("resume_strategy", "legacy"),
            result.get("target_mode", "AUTO"),
            result.get("native_resume"),
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

        self._wayline_mission_state = 6  # 航线执行

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

    def handle_one_key_takeoff_flow(self, msg: dict) -> dict:
        """
        一键启航闭环编排。

        真实闭环顺序：
        起飞前检查 -> 断充 -> 开盖 -> 释放夹紧 -> 机巢起飞允许/起飞指令
        -> 飞控起飞 -> 航线执行 -> 返航 -> 降落 -> 夹紧 -> 关盖 -> 充电。

        data:
        - flight_id: 可选，默认使用当前 flighttask_prepare 的 flight_id
        - takeoff_height: 可选，默认 30m
        - auto_complete: 可选；默认 false，必须依赖真实飞控/机巢 adapter
        """
        data = msg.get('data', {}) if isinstance(msg.get('data', {}), dict) else {}
        flight_id = data.get('flight_id') or self._current_task_id or f"takeoff-flow-{int(time.time() * 1000)}"
        takeoff_height = self._as_float(data.get('takeoff_height', data.get('height', 30)), 30.0)
        if isinstance(data.get('simulate_mission'), dict) and data['simulate_mission'].get('is_enable') == 1:
            return MessageBuilder.build_reply(
                msg,
                result=INVALID_COMMAND_FORMAT,
                data={'output': {'status': STATUS_REJECTED, 'steps': [], 'flight_id': flight_id}},
                msg_str='simulate_mission is disabled; real flight execution is required',
            )
        self._simulate_mission = {}
        auto_complete_raw = data.get('auto_complete', False)
        if isinstance(auto_complete_raw, str):
            auto_complete = auto_complete_raw.strip().lower() not in ('0', 'false', 'no', 'off')
        else:
            auto_complete = bool(auto_complete_raw)

        self._current_task_id = flight_id
        steps: list = []
        self._last_takeoff_flow_steps = steps
        self._record_takeoff_flow_step(steps, 'precheck', detail={
            'flight_id': flight_id,
            'waypoint_count': len(self._current_wayline),
            'simulate_mission': False,
        })

        for command_name in ('charge_close', 'cover_open', 'putter_open'):
            if not self._run_takeoff_flow_nest_step(steps, command_name):
                return MessageBuilder.build_reply(
                    msg,
                    result=-1,
                    data={'output': {'status': STATUS_FAILED, 'steps': steps, 'flight_id': flight_id}},
                    msg_str=f'{command_name} failed',
                )

        if self._nest_module and hasattr(self._nest_module, 'request_takeoff'):
            try:
                self._nest_module.request_takeoff()
                self._record_takeoff_flow_step(steps, 'nest_takeoff_request', detail={'success': True, 'simulated': False})
            except Exception as exc:
                self._record_takeoff_flow_step(
                    steps,
                    'nest_takeoff_request',
                    status=STATUS_FAILED,
                    result=-1,
                    detail={'success': False, 'message': str(exc)},
                )
                return MessageBuilder.build_reply(
                    msg,
                    result=-1,
                    data={'output': {'status': STATUS_FAILED, 'steps': steps, 'flight_id': flight_id}},
                    msg_str='nest_takeoff_request failed',
                )
        else:
            self._record_takeoff_flow_step(
                steps,
                'nest_takeoff_request',
                status=STATUS_FAILED,
                result=-1,
                detail={'success': False, 'simulated': False, 'message': 'nest_module_not_available'},
            )
            return MessageBuilder.build_reply(
                msg,
                result=WAYLINE_EXECUTION_FAILED,
                data={'output': {'status': STATUS_FAILED, 'steps': steps, 'flight_id': flight_id}},
                msg_str='nest_takeoff_request failed',
            )

        if not self._run_takeoff_flow_flight_step(steps, 'flight_takeoff', 'takeoff', takeoff_height):
            return MessageBuilder.build_reply(
                msg,
                result=-1,
                data={'output': {'status': STATUS_FAILED, 'steps': steps, 'flight_id': flight_id}},
                msg_str='flight_takeoff failed',
            )

        real_wayline_activation = bool(self._current_wayline)
        if real_wayline_activation:
            if self.start_mission_monitor() is False:
                self._record_takeoff_flow_step(
                    steps,
                    'flighttask_execute',
                    status=STATUS_FAILED,
                    result=-1,
                    detail={'success': False, 'message': 'Mission monitor unavailable', 'real_sitl': True},
                )
                return MessageBuilder.build_reply(
                    msg,
                    result=WAYLINE_EXECUTION_FAILED,
                    data={'output': {'status': STATUS_FAILED, 'steps': steps, 'flight_id': flight_id}},
                    msg_str='Mission monitor unavailable',
                )

            if not self._run_takeoff_flow_flight_step(steps, 'flighttask_execute', 'set_mode', 'AUTO'):
                self.stop_mission_monitor()
                return MessageBuilder.build_reply(
                    msg,
                    result=WAYLINE_EXECUTION_FAILED,
                    data={'output': {'status': STATUS_FAILED, 'steps': steps, 'flight_id': flight_id}},
                    msg_str='flighttask_execute failed',
                )

        self._status = self.MISSION_RUNNING
        self._wayline_mission_state = 6
        self._current_step = 23
        self._battery_safety_action = None
        self._executed_waypoint_actions.clear()
        if not real_wayline_activation:
            self._record_takeoff_flow_step(steps, 'flighttask_execute', status=STATUS_IN_PROGRESS, detail={
                'flight_id': flight_id,
                'waypoint_count': len(self._current_wayline),
            })
        self._emit_wayline_progress(STATUS_IN_PROGRESS, current_step=23, percent=self._current_progress_percent(), flight_id=flight_id)

        if not auto_complete:
            self._last_takeoff_flow_steps = list(steps)
            return MessageBuilder.build_reply(
                msg,
                result=0,
                data={'output': {'status': STATUS_IN_PROGRESS, 'steps': steps, 'flight_id': flight_id}},
            )

        self._status = self.MISSION_COMPLETED
        self._wayline_mission_state = 9
        self._current_step = 24
        self._emit_wayline_progress(STATUS_OK, current_step=24, percent=100, flight_id=flight_id)

        if not self._run_takeoff_flow_flight_step(steps, 'return_home', 'one_click_return'):
            return MessageBuilder.build_reply(
                msg,
                result=-1,
                data={'output': {'status': STATUS_FAILED, 'steps': steps, 'flight_id': flight_id}},
                msg_str='return_home failed',
            )
        if not self._run_takeoff_flow_flight_step(steps, 'landing', 'land'):
            return MessageBuilder.build_reply(
                msg,
                result=-1,
                data={'output': {'status': STATUS_FAILED, 'steps': steps, 'flight_id': flight_id}},
                msg_str='landing failed',
            )

        for command_name in ('putter_close', 'cover_close', 'charge_open'):
            if not self._run_takeoff_flow_nest_step(steps, command_name):
                return MessageBuilder.build_reply(
                    msg,
                    result=-1,
                    data={'output': {'status': STATUS_FAILED, 'steps': steps, 'flight_id': flight_id}},
                    msg_str=f'{command_name} failed',
                )

        self._current_step = 33
        self._last_takeoff_flow_steps = list(steps)
        self._record_takeoff_flow_step(steps, 'completed', detail={'flight_id': flight_id})
        return MessageBuilder.build_reply(
            msg,
            result=0,
            data={'output': {'status': STATUS_OK, 'steps': steps, 'flight_id': flight_id}},
        )

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
        - { "mode": "QLAN", "arm": true }  # SITL/调试扩展：按 QSTABILIZE 解锁
        """
        data = msg.get('data', {})
        mode_payload = dict(data) if isinstance(data, dict) else {}
        for key in ("flight_mode", "mode_name", "mode"):
            raw_mode = mode_payload.get(key)
            if isinstance(raw_mode, str) and raw_mode.strip().upper() == "QLAN":
                mode_payload[key] = "QSTABILIZE"

        ok, normalized, reason = normalize_flight_mode_request(mode_payload)
        if not ok or normalized is None:
            logger.warning(f"Invalid set_flight_mode payload: {reason}, data={data}")
            return MessageBuilder.build_reply(msg, result=INVALID_COMMAND_FORMAT, msg_str=reason)

        requested_mode = normalized.mode_name
        mode_name = requested_mode
        should_arm = bool(mode_payload.get("arm") or mode_payload.get("unlock") or mode_payload.get("armed"))
        if should_arm and mode_name in {"QLAND", "LAND"}:
            available_modes = (
                self._flight_control.get_available_modes()
                if self._flight_control and hasattr(self._flight_control, "get_available_modes")
                else []
            )
            for candidate in ("QSTABILIZE", "QHOVER", "QLOITER", "GUIDED", "MANUAL"):
                if not available_modes or candidate in available_modes:
                    mode_name = candidate
                    break
            logger.warning("set_flight_mode arm requested non-armable mode %s, using %s", requested_mode, mode_name)

        logger.info(
            "Set flight mode: %s (mode_code=%s, source=%s)",
            mode_name,
            mode_to_dji_code(mode_name),
            normalized.source_key,
        )

        # 通过MAVLink设置模式
        if not self._flight_control:
            reason = "FlightControl not available"
            logger.warning(reason)
            return MessageBuilder.build_reply(msg, result=DEVICE_STATE_UNSUPPORTED, msg_str=reason)

        mode_timeout = self._as_float(mode_payload.get("timeout", 10.0), 10.0)
        result = self._flight_control.set_mode(mode_name, timeout=mode_timeout)
        if not result.get("success"):
            reason = result.get("message") or "set flight mode failed"
            logger.warning(f"设置飞行模式失败: {reason}")
            error_code = DEVICE_COMMAND_TIMEOUT if "超时" in reason or "timeout" in reason.lower() else DEVICE_STATE_UNSUPPORTED
            return MessageBuilder.build_reply(
                msg,
                result=error_code,
                data={
                    "mode": mode_name,
                    "mode_code": mode_to_dji_code(mode_name),
                    "requested_mode": requested_mode,
                    "target_mode_id": result.get("mode_id"),
                    "target_mode_name": result.get("mode_name"),
                    "mode_profile": result.get("mode_profile"),
                    "current_mode_id": result.get("current_mode_id"),
                    "current_mode_name": result.get("current_mode_name"),
                    "elapsed_time": result.get("elapsed_time"),
                },
                msg_str=reason,
            )

        armed = self._flight_control.is_armed_status() if hasattr(self._flight_control, "is_armed_status") else None
        if should_arm:
            arm_timeout = self._as_float(mode_payload.get("timeout", 10.0), 10.0)
            readiness_timeout = self._as_float(
                mode_payload.get(
                    "prearm_timeout",
                    Config.get('mission.precheck.armable_timeout', 0.0),
                ),
                0.0,
            )
            arm_and_wait = getattr(self._flight_control, "arm_and_wait", None)
            if callable(arm_and_wait):
                arm_result = arm_and_wait(
                    timeout=arm_timeout,
                    readiness_timeout=readiness_timeout,
                )
            else:
                arm_result = {
                    "success": False,
                    "message": "FlightControl safe arm gate unavailable",
                }
            if not arm_result.get("success"):
                reason = arm_result.get("message") or "arm failed"
                logger.warning(f"飞控解锁失败: {reason}")
                error_code = (
                    DEVICE_COMMAND_TIMEOUT
                    if "超时" in reason or "timeout" in reason.lower()
                    else DEVICE_STATE_UNSUPPORTED
                )
                return MessageBuilder.build_reply(msg, result=error_code, msg_str=reason)
            armed = self._flight_control.is_armed_status() if hasattr(self._flight_control, "is_armed_status") else True

        return MessageBuilder.build_reply(
            msg,
            result=0,
            data={
                "mode": mode_name,
                "mode_code": mode_to_dji_code(mode_name),
                "requested_mode": requested_mode,
                "armed": armed,
            },
        )

    def _select_pre_mission_arm_mode(self) -> str:
        available_modes = (
            self._flight_control.get_available_modes()
            if self._flight_control and hasattr(self._flight_control, "get_available_modes")
            else []
        )
        for candidate in ("QSTABILIZE", "QHOVER", "QLOITER", "GUIDED", "MANUAL"):
            if not available_modes or candidate in available_modes:
                return candidate
        return "QSTABILIZE"

    def _ensure_armed_for_real_wayline(self) -> tuple[bool, str]:
        if not self._flight_control:
            return False, "FlightControl not available"

        arm_and_wait = getattr(self._flight_control, "arm_and_wait", None)
        if not callable(arm_and_wait):
            return False, "FlightControl safe arm gate unavailable"

        readiness_timeout = self._as_float(
            Config.get('mission.precheck.armable_timeout', 0.0),
            0.0,
        )
        is_armed = (
            self._flight_control.is_armed_status()
            if hasattr(self._flight_control, "is_armed_status")
            else None
        )
        if is_armed is True:
            arm_result = arm_and_wait(
                timeout=10.0,
                readiness_timeout=readiness_timeout,
            )
            return (
                bool(arm_result.get("success")),
                "" if arm_result.get("success") else arm_result.get("message") or "arm readiness failed",
            )

        arm_mode = self._select_pre_mission_arm_mode()
        logger.info("Real flighttask execute pre-arm: switch to %s before AUTO", arm_mode)
        mode_result = self._flight_control.set_mode(arm_mode)
        if not mode_result.get("success"):
            return False, mode_result.get("message") or f"set pre-arm mode {arm_mode} failed"

        arm_result = arm_and_wait(
            timeout=10.0,
            readiness_timeout=readiness_timeout,
        )
        if not arm_result.get("success"):
            return False, arm_result.get("message") or "arm failed"

        return True, ""

    def _real_wayline_flight_diagnostics(self, stage: str, reason: str) -> dict:
        """Build a concise, safe diagnostic payload for real-wayline failures."""
        diagnostics = {
            "stage": stage,
            "reason": reason,
        }
        if not self._flight_control:
            return diagnostics

        status_getter = getattr(self._flight_control, "get_status", None)
        if callable(status_getter):
            try:
                status = status_getter()
            except Exception as exc:
                diagnostics["status_error"] = str(exc)
            else:
                diagnostics.update({
                    "armed": status.get("armed"),
                    "current_mode": status.get("current_mode_name"),
                    "current_mode_id": status.get("current_mode_id"),
                    "last_command_ack": status.get("last_command_ack"),
                })
        return diagnostics

    def _fail_real_wayline_execute(self, msg: dict, flight_id: str, stage: str, reason: str) -> dict:
        """Fail real-wayline execution without leaving a monitor or nest action running."""
        self.stop_mission_monitor()
        self._status = self.MISSION_FAILED
        self._wayline_mission_state = 1
        self._current_step = 23
        diagnostics = self._real_wayline_flight_diagnostics(stage, reason)
        failure = self._last_task_failure or self._remember_task_failure(stage, reason)
        failure_payload = {
            key: value for key, value in failure.items()
            if key != "raw_result"
        }
        self._break_point = {
            **(self._break_point or {}),
            'index': self._current_waypoint_idx,
            'total_waypoints': len(self._current_wayline),
            'break_reason': 2,
            'reason': reason,
            'failure': failure_payload,
        }
        self._emit_wayline_progress(
            STATUS_FAILED,
            current_step=23,
            percent=self._current_progress_percent(),
            flight_id=flight_id,
            extra={"errors": [reason], "failure": failure_payload, "flight_control": diagnostics},
        )
        if flight_id:
            threading.Thread(
                target=self._run_db_coroutine,
                args=(self._update_mission_status(flight_id, 'failed'),),
                daemon=True,
            ).start()
        return MessageBuilder.build_reply(
            msg,
            result=WAYLINE_EXECUTION_FAILED,
            data=self._wayline_reply_data(
                STATUS_FAILED,
                current_step=23,
                percent=self._current_progress_percent(),
                flight_id=flight_id,
                extra={"errors": [reason], "failure": failure_payload, "flight_control": diagnostics},
            ),
            msg_str=reason,
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
        if self._status in (self.MISSION_RUNNING, self.MISSION_PAUSED):
            self.stop_mission_monitor()
            self._status = self.MISSION_PAUSED
            self._record_breakpoint("一键返航前保存断点", break_reason=1283)

            if self._current_task_id:
                threading.Thread(
                    target=self._run_db_coroutine,
                    args=(self._update_mission_status(self._current_task_id, 'paused'),),
                    daemon=True
                ).start()

        self._wayline_mission_state = 7  # 航线中断，返航后允许断点恢复
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

    def start_mission_monitor(self) -> bool:
        """确保航点监控运行；旧监控正在退出时短暂等待并接管。"""
        waypoint_manager = self._waypoint_manager
        if not waypoint_manager:
            logger.error("Mission monitor requires WaypointManager; monitor not started")
            return False

        deadline = time.monotonic() + 2.0
        while True:
            with self._mission_monitor_lock:
                existing_thread = self._mission_monitor_thread
                existing_stop_event = self._mission_monitor_stop_event
                if existing_thread and existing_thread.is_alive():
                    if not existing_stop_event.is_set():
                        return True
                    stopping_thread = existing_thread
                else:
                    if hasattr(waypoint_manager, 'get_current_waypoint_snapshot'):
                        current, revision = waypoint_manager.get_current_waypoint_snapshot()
                    else:
                        current = getattr(waypoint_manager, 'current_waypoint', None)
                        revision = None

                    self._mission_monitor_generation += 1
                    generation = self._mission_monitor_generation
                    stop_event = threading.Event()
                    monitor_thread = threading.Thread(
                        target=self._mission_monitor_loop,
                        args=(waypoint_manager, revision, current, stop_event, generation),
                        daemon=True,
                    )
                    self._mission_monitor_stop_event = stop_event
                    self._mission_monitor_thread = monitor_thread
                    self._monitor_running = True
                    monitor_thread.start()
                    break

            if stopping_thread is threading.current_thread():
                logger.error("Mission monitor cannot restart itself while stopping")
                return False

            remaining = deadline - time.monotonic()
            if remaining <= 0:
                logger.error("Mission monitor restart timed out waiting for previous owner")
                return False
            stopping_thread.join(timeout=remaining)

        logger.info("Mission monitor started")
        return True

    def stop_mission_monitor(self) -> bool:
        """停止当前代航点监控；线程未退出时保留所有权，禁止重复启动。"""
        with self._mission_monitor_lock:
            monitor_thread = self._mission_monitor_thread
            stop_event = self._mission_monitor_stop_event
            stop_event.set()
            self._monitor_running = False

        if monitor_thread and monitor_thread is not threading.current_thread():
            monitor_thread.join(timeout=2.0)

        stopped = monitor_thread is None or not monitor_thread.is_alive()
        with self._mission_monitor_lock:
            if self._mission_monitor_thread is monitor_thread and stopped:
                self._mission_monitor_thread = None

        if not stopped:
            logger.warning("Mission monitor stop timed out; restart remains blocked")
        logger.info("Mission monitor stopped")
        return stopped

    def _process_mission_current(
        self,
        waypoint_index: Any,
        stop_event: Optional[threading.Event] = None,
    ) -> bool:
        """处理已分发的航点状态；任务完成时返回 True。"""
        with self._mission_state_lock:
            return self._process_mission_current_locked(waypoint_index, stop_event)

    def _process_mission_current_locked(
        self,
        waypoint_index: Any,
        stop_event: Optional[threading.Event] = None,
    ) -> bool:
        """Process one waypoint while holding ``_mission_state_lock``."""
        if stop_event and stop_event.is_set():
            return False
        if self._status != self.MISSION_RUNNING:
            return False

        try:
            new_idx = int(waypoint_index)
        except (TypeError, ValueError):
            logger.warning("Ignore invalid MISSION_CURRENT index: %r", waypoint_index)
            return False

        if not self._is_valid_current_waypoint_index(new_idx):
            logger.warning(
                "Ignore out-of-range MISSION_CURRENT: seq=%s, waypoints=%s, task=%s",
                new_idx,
                len(self._current_wayline),
                self._current_task_id,
            )
            return False

        self._execute_waypoint_actions(new_idx)
        if stop_event and stop_event.is_set():
            return False
        if new_idx == self._current_waypoint_idx:
            return False

        self._current_waypoint_idx = new_idx
        logger.info(f"Waypoint changed: {new_idx}/{len(self._current_wayline)}")

        # 上报进度
        self._report_mission_progress()

        # 记录断点（用于断点续飞）
        self._breakpoint_waypoint_idx = new_idx
        self._break_point = {
            **(self._break_point or {}),
            'index': new_idx,
            'total_waypoints': len(self._current_wayline),
            'progress': (
                new_idx / len(self._current_wayline)
                if self._current_wayline else 0
            ),
        }
        self._save_breakpoint_file("航点进度更新")

        # 检测任务完成
        if self._status == self.MISSION_RUNNING and new_idx >= len(self._current_wayline) - 1:
            flight_id = self._current_task_id
            self._current_step = 24  # 进入返航/降落阶段
            self._emit_wayline_progress(
                STATUS_IN_PROGRESS,
                current_step=self._current_step,
                percent=100,
                flight_id=flight_id,
                extra={'completion_phase': 'return_land_recovery'},
            )
            if not self._run_mission_completion_flow(flight_id, "mission_completed"):
                return True

            self._status = self.MISSION_COMPLETED
            self._wayline_mission_state = 9  # 航线停止
            self._current_step = 33  # 通知任务结果
            self._clear_task_failure()
            self._report_mission_progress(status='completed')
            self._emit_wayline_progress(
                STATUS_OK,
                current_step=self._current_step,
                percent=100,
                flight_id=flight_id,
                extra={'completion_steps': list(self._last_completion_flow_steps)},
            )
            self._clear_breakpoint_file()

            # 更新数据库状态
            if flight_id:
                threading.Thread(
                    target=self._run_db_coroutine,
                    args=(self._update_mission_status(flight_id, 'completed'),),
                    daemon=True
                ).start()

            logger.info("Mission completed with return/landing recovery, wayline_state=9(航线停止)")
            return True

        return False

    def _mission_monitor_loop(
        self,
        waypoint_manager,
        revision: Optional[int],
        last_waypoint: Optional[int],
        stop_event: threading.Event,
        generation: int,
    ):
        """等待 Dispatcher 已分发的 MISSION_CURRENT 并上报进度。"""
        supports_wait = (
            revision is not None
            and hasattr(waypoint_manager, 'wait_for_current_waypoint')
        )

        try:
            while not stop_event.is_set():
                try:
                    # start_mission_monitor 会在真实飞控动作前预留线程。任务尚未
                    # 进入 RUNNING 时不要消费 MISSION_CURRENT，避免预启动阶段
                    # 执行航点动作；状态切换后再从保存的 revision 顺序处理。
                    if self._status != self.MISSION_RUNNING:
                        if stop_event.wait(0.1):
                            break
                        continue

                    with self._mission_state_lock:
                        battery_safety_triggered = self._handle_battery_safety()
                    if battery_safety_triggered:
                        stop_event.set()
                        break

                    if supports_wait:
                        waypoint_index, revision = waypoint_manager.wait_for_current_waypoint(
                            revision,
                            timeout=0.5,
                        )
                    else:
                        # 兼容只暴露 current_waypoint 的轻量测试/适配器；生产路径
                        # 使用上面的 Condition 接口，不进行忙轮询。
                        if stop_event.wait(0.2):
                            break
                        current_waypoint = getattr(waypoint_manager, 'current_waypoint', None)
                        if current_waypoint == last_waypoint:
                            continue
                        last_waypoint = current_waypoint
                        waypoint_index = current_waypoint

                    if stop_event.is_set():
                        break

                    if waypoint_index is not None:
                        if self._process_mission_current(waypoint_index, stop_event):
                            stop_event.set()
                            break
                except Exception as e:
                    logger.error(f"Mission monitor error: {e}")
                    if stop_event.wait(1.0):
                        break
        finally:
            current_thread = threading.current_thread()
            with self._mission_monitor_lock:
                if (
                    self._mission_monitor_thread is current_thread
                    and self._mission_monitor_generation == generation
                ):
                    self._mission_monitor_thread = None
                    self._monitor_running = False

    def _report_mission_progress(self, status: str = 'executing'):
        """上报航线任务进度 (flighttask_progress)"""
        total = len(self._current_wayline)
        current = self._current_waypoint_idx
        percent = 100 if status == 'completed' else progress_percent(current, total)

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
        if status == 'failed' and self._last_task_failure:
            ext['failure'] = {
                key: value for key, value in self._last_task_failure.items()
                if key != "raw_result"
            }

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

    def complete_simulated_mission(self) -> bool:
        """Simulation completion is disabled; missions must complete from real progress."""
        logger.warning("Simulated mission completion rejected: simulation is disabled")
        return False

    # ---- 大疆标准FlightTask接口 ----

    @serialize_flighttask
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
        if isinstance(simulate_mission, dict) and simulate_mission.get('is_enable') == 1:
            reason = "simulate_mission is disabled; real flight execution is required"
            logger.warning("Flighttask prepare rejected: %s", reason)
            return MessageBuilder.build_reply(
                msg,
                result=INVALID_COMMAND_FORMAT,
                msg_str=reason,
            )

        logger.info(f"Flighttask prepare: flight_id={flight_id}, type={task_type}, "
                   f"execute_time={execute_time}")
        self._clear_task_failure()

        # A second producer (for example the local HD demo) must not replace
        # the currently flying task's file, waypoints or task ID.
        if self._status in (self.MISSION_PREPARING, self.MISSION_RUNNING, self.MISSION_PAUSED):
            if self._status == self.MISSION_PAUSED and self._current_task_id == flight_id:
                saved_breakpoint = self._load_breakpoint_file()
                saved_task_id = saved_breakpoint.get("task_id") if saved_breakpoint else None
                if saved_task_id and saved_task_id != flight_id:
                    return self._reject_flighttask_request(
                        msg,
                        flight_id,
                        DEVICE_STATE_UNSUPPORTED,
                        f"断点任务与准备任务不匹配: breakpoint={saved_task_id}, requested={flight_id}",
                    )
                has_breakpoint = bool(
                    saved_breakpoint or self._break_point or self._breakpoint_waypoint_idx > 0
                )
                if has_breakpoint:
                    current_fp = self._task_file.get("fingerprint") if isinstance(self._task_file, dict) else ""
                    incoming_fp = file_info.get("fingerprint") if isinstance(file_info, dict) else ""
                    if current_fp and incoming_fp and current_fp != incoming_fp:
                        return self._reject_flighttask_request(
                            msg,
                            flight_id,
                            DOCK_BUSY,
                            "同一断点任务不允许更换航线文件",
                        )

                    self._task_type = task_type
                    self._execute_time = execute_time
                    if file_info and not self._task_file:
                        self._task_file = file_info
                    self._ready_conditions = ready_conditions
                    self._executable_conditions = executable_conditions
                    self._rth_altitude = rth_altitude
                    self._out_of_control_action = out_of_control_action
                    self._exit_wayline_when_rc_lost = exit_wayline_when_rc_lost
                    logger.info(
                        "Flighttask prepare accepted as breakpoint relaunch: flight_id=%s waypoint=%s",
                        flight_id,
                        self._breakpoint_waypoint_idx,
                    )
                    self._emit_wayline_progress(
                        STATUS_SENT,
                        current_step=5,
                        percent=self._current_progress_percent(),
                        flight_id=flight_id,
                        extra={"resume_from_breakpoint": True},
                    )
                    self.report_flighttask_ready([flight_id])
                    return MessageBuilder.build_reply(
                        msg,
                        result=0,
                        data=self._wayline_reply_data(
                            STATUS_SENT,
                            current_step=5,
                            percent=self._current_progress_percent(),
                            flight_id=flight_id,
                            extra={"resume_from_breakpoint": True},
                        ),
                    )
            return self._reject_flighttask_request(
                msg,
                flight_id,
                DOCK_BUSY,
                "当前航线任务正在执行，不能覆盖航线文件",
            )

        # 保存任务属性
        self._current_task_id = flight_id
        self._task_type = task_type
        self._execute_time = execute_time
        self._task_file = file_info
        self._current_waypoint_idx = 0
        self._breakpoint_waypoint_idx = 0
        self._ready_conditions = ready_conditions
        self._executable_conditions = executable_conditions
        self._break_point = break_point
        self._rth_altitude = rth_altitude
        self._out_of_control_action = out_of_control_action
        self._exit_wayline_when_rc_lost = exit_wayline_when_rc_lost
        self._simulate_mission = {}

        self._status = self.MISSION_PREPARING
        self._wayline_mission_state = 2  # 航线准备状态
        self._current_step = 5  # 航线执行进入准备状态

        self._wayline_delegated_to_nest = self._should_delegate_wayline_to_nest()
        if self._wayline_delegated_to_nest:
            self._task_file_summary = self._delegated_wayline_summary(file_info)
            self._current_wayline = []
            logger.info(
                "Wayline parsing/upload delegated to nest: flight_id=%s, source=%s",
                flight_id,
                self._task_file_summary.get('source'),
            )
            trace_command(
                "wayline_delegated_to_nest",
                label="航线委托机巢",
                method="flighttask_prepare",
                flight_id=flight_id,
                source=self._task_file_summary.get('source'),
                reason="main_mavlink_disabled",
            )
        else:
            try:
                summary, parsed_waypoints = parse_wayline_file(file_info)
            except Exception as exc:
                summary, parsed_waypoints = {}, []
                logger.warning("Wayline parse failed: flight_id=%s error=%s", flight_id, exc)
            self._task_file_summary = summary or {}
            self._current_wayline = list(parsed_waypoints or [])
            if not self._current_wayline:
                self._status = self.MISSION_FAILED
                self._wayline_mission_state = 1
                reason = "; ".join(self._task_file_summary.get("warnings", [])) or "No waypoints parsed from wayline file"
                return self._fail_prepare_self_check(
                    msg,
                    flight_id,
                    ["wayline_parse"],
                    {"wayline_parse": reason},
                )
            upload_ok, upload_reason = self._upload_wayline_to_flight_controller(self._current_wayline)
            if not upload_ok:
                self._status = self.MISSION_FAILED
                self._wayline_mission_state = 1
                return self._fail_prepare_self_check(
                    msg,
                    flight_id,
                    ["wayline_upload"],
                    {"wayline_upload": upload_reason},
                )
            logger.info(
                "Wayline parsed and uploaded to flight controller: flight_id=%s waypoints=%s",
                flight_id,
                len(self._current_wayline),
            )
            trace_command(
                "gateway_wayline_uploaded",
                label="网关上传航线到飞控",
                method="flighttask_prepare",
                flight_id=flight_id,
                waypoint_count=len(self._current_wayline),
                source=self._task_file_summary.get('source'),
            )

        # 断点续飞设置
        if break_point:
            self._breakpoint_waypoint_idx = break_point.get('index', 0)
            logger.info(f"Breakpoint set: index={break_point.get('index')}, "
                       f"state={break_point.get('state')}, progress={break_point.get('progress')}")

        # 返航高度设置（如飞控支持）
        if rth_altitude and self._flight_control:
            logger.info(f"RTH altitude configured: {rth_altitude}m")

        self_check_ok, self_check_reasons, self_check_details = self._run_prepare_self_check(False)
        if not self_check_ok:
            return self._fail_prepare_self_check(
                msg,
                flight_id,
                self_check_reasons,
                self_check_details,
            )

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

    @serialize_flighttask
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
        self._clear_task_failure()
        if self._current_task_id != flight_id:
            active = self._current_task_id or ''
            result = DOCK_BUSY if self._status in (
                self.MISSION_PREPARING,
                self.MISSION_RUNNING,
                self.MISSION_PAUSED,
            ) else DEVICE_STATE_UNSUPPORTED
            return self._reject_flighttask_request(
                msg,
                flight_id,
                result,
                f"flight_id 与已准备任务不匹配: requested={flight_id}, active={active or 'none'}",
            )

        relaunch_from_breakpoint = False
        if self._status == self.MISSION_PAUSED:
            saved_breakpoint = self._load_breakpoint_file()
            saved_task_id = saved_breakpoint.get("task_id") if saved_breakpoint else None
            if saved_task_id and saved_task_id != flight_id:
                return self._reject_flighttask_request(
                    msg,
                    flight_id,
                    DEVICE_STATE_UNSUPPORTED,
                    f"断点任务与执行任务不匹配: breakpoint={saved_task_id}, requested={flight_id}",
                )
            relaunch_from_breakpoint = bool(
                saved_breakpoint or self._break_point or self._breakpoint_waypoint_idx > 0
            )
            if not relaunch_from_breakpoint:
                return self._reject_flighttask_request(
                    msg,
                    flight_id,
                    WAYLINE_RESUME_STATE_INVALID,
                    "当前任务已暂停，但没有可用于下次执行的断点",
                )
            logger.info(
                "Flighttask execute relaunches paused task from breakpoint: flight_id=%s waypoint=%s",
                flight_id,
                self._breakpoint_waypoint_idx,
            )
        elif self._status != self.MISSION_IDLE:
            result = DOCK_BUSY if self._status in (
                self.MISSION_PREPARING,
                self.MISSION_RUNNING,
                self.MISSION_PAUSED,
            ) else DEVICE_STATE_UNSUPPORTED
            return self._reject_flighttask_request(
                msg,
                flight_id,
                result,
                f"当前任务状态不允许执行: {self._get_task_status_name()}",
            )

        self._current_task_id = flight_id
        self._battery_safety_action = None
        self._executed_waypoint_actions.clear()

        if self._wayline_delegated_to_nest:
            if not self._nest_module:
                return self._fail_real_wayline_execute(
                    msg,
                    flight_id,
                    "nest",
                    "Nest module not available",
                )

            self._status = self.MISSION_RUNNING
            self._wayline_mission_state = 6  # 航线执行
            self._current_step = 23  # 航线执行中
            trace_command(
                "nest_wayline_execute_requested",
                label="请求机巢执行航线",
                method="flighttask_execute",
                flight_id=flight_id,
                resume_from_breakpoint=relaunch_from_breakpoint,
            )
            if not self._call_nest_action("request_takeoff", "flighttask_execute"):
                return self._fail_real_wayline_execute(
                    msg,
                    flight_id,
                    "nest_takeoff",
                    "Nest takeoff request failed",
                )
            self._emit_wayline_progress(
                STATUS_IN_PROGRESS,
                current_step=23,
                percent=self._current_progress_percent(),
                flight_id=flight_id,
                extra={"delegated_to": "nest"},
            )
            return MessageBuilder.build_reply(
                msg,
                result=0,
                data=self._wayline_reply_data(
                    STATUS_IN_PROGRESS,
                    current_step=23,
                    flight_id=flight_id,
                    extra={"delegated_to": "nest"},
                ),
            )

        if not self._flight_control:
            return self._fail_real_wayline_execute(
                msg,
                flight_id,
                "flight_control",
                "FlightControl not available",
            )
        return self._run_real_wayline_takeoff_flow(msg, flight_id)

    @serialize_flighttask
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

        # ``current`` 是本地运维/测试别名，避免操作者在网关重启恢复任务后
        # 必须先从日志中手工抄录 flight_id。云端按真实 flight_id 取消的标准
        # 用法保持不变；未知 ID 不能再伪装成成功取消。
        current_aliases = {"current", "current_task", "active"}
        request_current = any(item.strip().lower() in current_aliases for item in flight_ids)
        canceled_task_id = self._current_task_id
        if not canceled_task_id or (
            canceled_task_id not in flight_ids and not request_current
        ):
            return self._reject_flighttask_request(
                msg,
                flight_ids[0],
                DEVICE_STATE_UNSUPPORTED,
                "请求取消的 flight_id 与当前任务不匹配",
            )

        self._status = self.MISSION_CANCELED
        self._wayline_mission_state = 9  # 航线停止
        self._current_step = 28
        self.stop_mission_monitor()
        self._clear_breakpoint_file()
        logger.info("Current task canceled: %s, wayline_state=9(航线停止)", canceled_task_id)
        self._call_nest_action("abort_takeoff", "flighttask_undo")
        self._call_nest_action("abort_landing", "flighttask_undo")

        # 取消状态必须进入持久层，否则网关重启会把仍标记为 running/paused 的
        # 旧任务恢复回来，再次阻塞新的 flighttask_prepare。
        threading.Thread(
            target=self._run_db_coroutine,
            args=(self._update_mission_status(canceled_task_id, 'canceled'),),
            daemon=True,
        ).start()

        # 真实飞控任务取消后安全返航。
        if self._flight_control:
            result = self._flight_control.one_click_return()
            if not result.get("success"):
                logger.warning(f"返航指令失败: {result.get('message')}")

        percent = self._current_progress_percent()
        self._emit_wayline_progress(STATUS_CANCELED, current_step=28, percent=percent)
        reply_data = self._wayline_reply_data(
            STATUS_CANCELED,
            current_step=28,
            percent=percent,
            flight_id=canceled_task_id,
            extra={
                'canceled_flight_id': canceled_task_id,
                'requested_flight_ids': flight_ids,
            },
        )

        # Reply/event 已带完整的取消任务上下文；随后清空内存状态，允许新的
        # prepare 立即接管，并避免遗留 task_id 被误当作当前任务。
        self._current_task_id = None
        self._current_wayline = []
        self._current_waypoint_idx = 0
        self._breakpoint_waypoint_idx = 0
        self._task_file = {}
        self._task_file_summary = {}
        self._ready_conditions = {}
        self._executable_conditions = {}
        self._break_point = {}
        self._simulate_mission = {}
        self._battery_safety_action = None
        self._executed_waypoint_actions.clear()
        self._clear_task_failure()

        return MessageBuilder.build_reply(
            msg,
            result=0,
            data=reply_data,
        )

    def handle_flighttask_stop(self, msg: dict) -> dict:
        """
        任务终止 (flighttask_stop)

        官方字段: { flight_id, reason }。reason 当前仅作为终止原因透传记录，
        真正终止动作复用当前航线取消链路，避免无真实飞控动作时伪造成功。
        """
        data = msg.get('data', {})
        ok, flight_id, errors = validate_flight_id_payload(data, 'flighttask_stop')
        if not ok:
            error_msg = "; ".join(errors)
            logger.warning(f"Flighttask stop validation failed: {error_msg}")
            return MessageBuilder.build_reply(msg, result=INVALID_COMMAND_FORMAT, msg_str=error_msg)
        reason = data.get('reason', 0) if isinstance(data, dict) else 0
        if not isinstance(reason, int) or isinstance(reason, bool) or reason not in (0, 1):
            return MessageBuilder.build_reply(
                msg,
                result=INVALID_COMMAND_FORMAT,
                msg_str="reason must be int enum 0/1",
            )
        stop_msg = dict(msg)
        stop_msg['data'] = {'flight_ids': [flight_id]}
        reply = self.handle_flighttask_undo(stop_msg)
        if isinstance(reply, dict):
            reply['method'] = msg.get('method', 'flighttask_stop')
            reply.setdefault('data', {})['reason'] = reason
        return reply

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
        if self._status in (self.MISSION_RUNNING, self.MISSION_PAUSED):
            self.stop_mission_monitor()
            self._status = self.MISSION_PAUSED
            self._record_breakpoint("航线返航前保存断点", break_reason=1283)

            if self._current_task_id:
                threading.Thread(
                    target=self._run_db_coroutine,
                    args=(self._update_mission_status(self._current_task_id, 'paused'),),
                    daemon=True
                ).start()

        self._wayline_mission_state = 7  # 航线中断，返航后允许断点恢复
        self._current_step = 24  # 进入返航的检查准备工作
        if not self._flight_control:
            reason = "FlightControl not available"
            logger.warning(f"Flighttask return failed: {reason}")
            return MessageBuilder.build_reply(
                msg,
                result=WAYLINE_EXECUTION_FAILED,
                data=self._wayline_reply_data(STATUS_REJECTED, current_step=self._current_step, flight_id=task_id),
                msg_str=reason,
            )
        if return_type == 0:
            result = self._flight_control.one_click_return()
        else:
            result = self._flight_control.land()
        if not result.get("success"):
            reason = result.get("message") or "return control failed"
            logger.warning(f"Flighttask return failed: {reason}")
            return MessageBuilder.build_reply(
                msg,
                result=WAYLINE_EXECUTION_FAILED,
                data=self._wayline_reply_data(STATUS_REJECTED, current_step=self._current_step, flight_id=task_id),
                msg_str=reason,
            )

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
        if not self._flight_control:
            reason = "FlightControl not available"
            logger.warning(f"一键返航失败: {reason}")
            return MessageBuilder.build_reply(msg, result=WAYLINE_EXECUTION_FAILED, msg_str=reason)
        result = self._flight_control.one_click_return()
        if not result.get("success"):
            reason = result.get("message") or "return home failed"
            logger.warning(f"一键返航失败: {reason}")
            return MessageBuilder.build_reply(msg, result=WAYLINE_EXECUTION_FAILED, msg_str=reason)

        self._emit_wayline_progress(STATUS_IN_PROGRESS, current_step=24, percent=self._current_progress_percent())
        return MessageBuilder.build_reply(
            msg,
            result=0,
            data=self._wayline_reply_data(STATUS_IN_PROGRESS, current_step=24)
        )

    def handle_return_specific_home(self, msg: dict) -> dict:
        """
        指定 home 点返航 (return_specific_home)

        单机场真实链路只支持返回本网关机场。蛙跳/多机场 home 点需要额外的
        多机场拓扑和返航规划 adapter，未接入时必须拒绝而不是假成功。
        """
        data = msg.get('data', {}) if isinstance(msg.get('data', {}), dict) else {}
        home_dock_sn = str(data.get('home_dock_sn', '') or '')
        if not home_dock_sn:
            return MessageBuilder.build_reply(
                msg,
                result=INVALID_COMMAND_FORMAT,
                msg_str="home_dock_sn must be non-empty string",
            )
        if home_dock_sn != self.gateway_sn:
            return MessageBuilder.build_reply(
                msg,
                result=DEVICE_STATE_UNSUPPORTED,
                data=self._wayline_reply_data(
                    STATUS_REJECTED,
                    current_step=self._current_step,
                    extra={'home_dock_sn': home_dock_sn},
                ),
                msg_str="return_specific_home requires multi-dock home adapter",
            )
        return self.handle_return_home(msg)

    @serialize_flighttask
    def handle_flighttask_progress(self, msg: dict) -> dict:
        """
        飞行任务进度查询 (flighttask_progress)

        Request: { flighttask_id: str }
        Response: { status, progress, current_waypoint, total_waypoints }
        """
        data = msg.get('data', {})
        task_id = data.get('flight_id', data.get('flighttask_id', self._current_task_id or ''))
        logger.info(f"Flighttask progress query: {task_id}")

        if task_id and task_id != self._current_task_id:
            return self._reject_flighttask_request(
                msg,
                task_id,
                DEVICE_STATE_UNSUPPORTED,
                "flight_id 与当前任务不匹配，拒绝返回其他任务的进度",
            )

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

    def handle_flighttask_progress_get(self, msg: dict) -> dict:
        """
        机场任务状态获取 (flighttask_progress_get)

        官方 requests_reply 输出: { flight_id, progress: {current_step, percent}, status }。
        本地作为 services 兼容入口时返回同一结构。
        """
        data = msg.get('data', {}) if isinstance(msg.get('data', {}), dict) else {}
        target_sn = str(data.get('sn', '') or self.gateway_sn)
        if target_sn and target_sn != self.gateway_sn:
            return MessageBuilder.build_reply(
                msg,
                result=DEVICE_STATE_UNSUPPORTED,
                data={'output': {
                    'flight_id': '',
                    'progress': {'current_step': self._current_step, 'percent': 0},
                    'status': STATUS_REJECTED,
                }},
                msg_str="flighttask_progress_get for remote dock is not available",
            )

        return MessageBuilder.build_reply(msg, result=0, data={'output': {
            'flight_id': self._current_task_id or '',
            'progress': {
                'current_step': self._current_step,
                'percent': self._current_progress_percent(),
            },
            'status': self._wayline_status_name(),
        }})

    def handle_flighttask_pause(self, msg: dict) -> dict:
        """
        航线暂停 (flighttask_pause)

        Direction: down (services)
        Request: {} / null
        Response: { result }
        """
        logger.info("Flighttask pause")
        reply = self.handle_pause_wayline(msg)
        try:
            if reply.get("data", {}).get("result") == WAYLINE_PAUSE_STATE_INVALID:
                reply["data"]["result"] = DEVICE_STATE_UNSUPPORTED
        except AttributeError:
            pass
        return reply

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

        if not self._flight_control:
            reason = "FlightControl not available"
            logger.warning(f"取消返航失败: {reason}")
            return MessageBuilder.build_reply(msg, result=WAYLINE_EXECUTION_FAILED, msg_str=reason)
        result = self._flight_control.set_mode("LOITER")
        if not result.get("success"):
            reason = result.get("message") or "LOITER mode switch failed"
            logger.warning(f"取消返航失败: {reason}")
            return MessageBuilder.build_reply(msg, result=WAYLINE_EXECUTION_FAILED, msg_str=reason)
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

    def _reject_in_flight_wayline(self, msg: dict, payload: dict) -> dict:
        return MessageBuilder.build_reply(
            msg,
            result=DEVICE_STATE_UNSUPPORTED,
            data={
                'output': {'status': STATUS_REJECTED},
                'in_flight_wayline_id': payload.get('in_flight_wayline_id', ''),
            },
            msg_str="in-flight wayline adapter is not connected",
        )

    def handle_in_flight_wayline_deliver(self, msg: dict) -> dict:
        """空中下发航线。当前未接真实空中航线 adapter，校验字段后明确拒绝。"""
        ok, payload, errors = normalize_in_flight_wayline_payload(
            msg.get('data', {}),
            'in_flight_wayline_deliver',
        )
        if not ok:
            return MessageBuilder.build_reply(
                msg,
                result=INVALID_COMMAND_FORMAT,
                msg_str="; ".join(errors),
            )
        return self._reject_in_flight_wayline(msg, payload)

    def handle_in_flight_wayline_stop(self, msg: dict) -> dict:
        """暂停空中航线。"""
        ok, payload, errors = normalize_in_flight_wayline_payload(
            msg.get('data', {}),
            'in_flight_wayline_stop',
        )
        if not ok:
            return MessageBuilder.build_reply(msg, result=INVALID_COMMAND_FORMAT, msg_str="; ".join(errors))
        return self._reject_in_flight_wayline(msg, payload)

    def handle_in_flight_wayline_recover(self, msg: dict) -> dict:
        """恢复空中航线。"""
        ok, payload, errors = normalize_in_flight_wayline_payload(
            msg.get('data', {}),
            'in_flight_wayline_recover',
        )
        if not ok:
            return MessageBuilder.build_reply(msg, result=INVALID_COMMAND_FORMAT, msg_str="; ".join(errors))
        return self._reject_in_flight_wayline(msg, payload)

    def handle_in_flight_wayline_cancel(self, msg: dict) -> dict:
        """取消空中航线。"""
        ok, payload, errors = normalize_in_flight_wayline_payload(
            msg.get('data', {}),
            'in_flight_wayline_cancel',
        )
        if not ok:
            return MessageBuilder.build_reply(msg, result=INVALID_COMMAND_FORMAT, msg_str="; ".join(errors))
        return self._reject_in_flight_wayline(msg, payload)

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
