"""
飞行任务管理模块
处理航线任务、指令飞行、任务状态监控
"""
import math
import time
import threading
from typing import List, Dict, Optional, Callable
from pymavlink import mavutil
from src.utils.logger import get_logger
from src.utils.config import Config
from src.utils.message import MessageBuilder

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

    def __init__(self):
        self.gateway_sn = Config.get_gateway_sn()

        # 当前任务状态
        self._status = self.MISSION_IDLE
        self._current_task_id: Optional[str] = None
        self._current_wayline: List[dict] = []
        self._current_waypoint_idx = 0
        self._breakpoint_waypoint_idx = 0  # 断点航点索引（用于断点续飞）

        # MAVLink模块引用(由外部注入)
        self._flight_control = None
        self._mode_control = None
        self._mavlink_conn = None  # MAVLinkConnection 引用

        # 回调
        self._event_callback: Optional[Callable] = None

        # 航点监控线程
        self._mission_monitor_thread: Optional[threading.Thread] = None
        self._monitor_running = False

    def set_flight_control(self, flight_control):
        """注入飞行控制模块"""
        self._flight_control = flight_control

    def set_mode_control(self, mode_control):
        """注入模式控制模块"""
        self._mode_control = mode_control

    def set_mavlink_connection(self, mavlink_conn):
        """注入MAVLink连接"""
        self._mavlink_conn = mavlink_conn

    def set_event_callback(self, callback: Callable):
        """设置事件上报回调"""
        self._event_callback = callback

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

        # 上报任务准备就绪
        if self._event_callback:
            event = MessageBuilder.build_event(
                'task_ready_notify',
                {
                    'task_id': data.get('task_id', ''),
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

        logger.info(f"Task started: {self._current_task_id}")

        # 通过飞行控制模块启动航线任务
        if self._flight_control:
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
        self.stop_mission_monitor()
        logger.info(f"Task canceled: {self._current_task_id}")

        # 发送返航指令
        if self._flight_control:
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
            return MessageBuilder.build_reply(msg, result=-1, msg_str="Mission not running")

        self._status = self.MISSION_PAUSED
        self.stop_mission_monitor()
        logger.info("Mission paused")

        # 记录断点
        self._breakpoint_waypoint_idx = self._current_waypoint_idx

        # 发送悬停指令
        if self._flight_control:
            result = self._flight_control.set_mode("LOITER")
            if not result.get("success"):
                logger.warning(f"暂停航线失败: {result.get('message')}")

        return MessageBuilder.build_reply(msg, result=0)

    def handle_resume_wayline(self, msg: dict) -> dict:
        """
        处理任务恢复 (resume_wayline) - 断点续飞

        Request: { task_id }
        Response: { result }
        """
        if self._status != self.MISSION_PAUSED:
            return MessageBuilder.build_reply(msg, result=-1, msg_str="Mission not paused")

        self._status = self.MISSION_RUNNING
        logger.info(f"Mission resumed from breakpoint: waypoint {self._breakpoint_waypoint_idx}")

        # 断点续飞：从断点航点重新上传并执行
        if self._current_wayline and self._breakpoint_waypoint_idx > 0:
            logger.info(f"Breakpoint resume: re-uploading from waypoint {self._breakpoint_waypoint_idx}")
            upload_ok = self.upload_waypoints_to_fc(self._current_wayline, self._breakpoint_waypoint_idx)
            if not upload_ok:
                logger.warning("Breakpoint re-upload failed, continuing with existing mission")

        # 启动监控并切换到AUTO模式
        self.start_mission_monitor()
        if self._flight_control:
            result = self._flight_control.set_mode("AUTO")
            if not result.get("success"):
                logger.warning(f"恢复航线失败: {result.get('message')}")

        return MessageBuilder.build_reply(msg, result=0)

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

        Request: { mode: 0=手动 1=姿态 2=GPS 3=自动 4=返航 5=降落 }
        """
        data = msg.get('data', {})
        mode = data.get('mode', 2)

        mode_map = {
            0: 'STABILIZE', 1: 'ACRO', 2: 'ALT_HOLD', 3: 'AUTO',
            4: 'GUIDED', 5: 'LOITER', 6: 'RTL', 9: 'LAND'
        }
        mode_name = mode_map.get(mode, 'UNKNOWN')
        logger.info(f"Set flight mode: {mode_name} ({mode})")

        # 通过MAVLink设置模式
        if self._flight_control:
            result = self._flight_control.set_mode(mode_name)
            if not result.get("success"):
                logger.warning(f"设置飞行模式失败: {result.get('message')}")

        return MessageBuilder.build_reply(msg, result=0)

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
        """
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

    def report_device_exit_homing(self, action: int, reason: int):
        """
        上报设备返航退出状态 (device_exit_homing_notify)

        Args:
            action: 0=退出返航退出状态, 1=进入返航退出状态
            reason: 退出返航原因枚举
        """
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
            logger.info(f"Exit homing reported: action={action}, reason={reason}")

    def report_flighttask_progress(self, flight_id: str, status: str,
                                   current_step: int, percent: int,
                                   ext: dict = None):
        """
        上报航线任务进度 (flighttask_progress)

        文档格式：
        { result: 0, output: { status, progress: {current_step, percent}, ext: {...} } }
        """
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

    def handle_return_home(self, msg: dict) -> dict:
        """返航（另一个接口名）"""
        return self.handle_go_home(msg)

    def handle_go_to_altitude(self, msg: dict) -> dict:
        """上升至指定高度"""
        data = msg.get('data', {})
        altitude = data.get('altitude', 50)
        logger.info(f"Go to altitude: {altitude}m")
        return MessageBuilder.build_reply(msg, result=0)

    # ---- 航点上传到飞控 ----

    def upload_waypoints_to_fc(self, waypoints: List[dict], start_from: int = 0) -> bool:
        """
        上传航点到飞控 (MAVLink Mission Protocol)

        Args:
            waypoints: 航点列表
            start_from: 从第几个航点开始上传（断点续飞用）

        Returns:
            bool: 是否上传成功
        """
        master = self._get_mavlink_master()
        if not master:
            logger.warning("MAVLink not connected, cannot upload waypoints")
            return False

        try:
            # 1. 清除现有任务
            master.mav.mission_clear_all_send(
                master.target_system,
                master.target_component,
                mavutil.mavlink.MAV_MISSION_TYPE_MISSION
            )
            time.sleep(0.5)

            # 2. 发送任务数量
            upload_list = waypoints[start_from:]
            count = len(upload_list)
            if count == 0:
                logger.warning("No waypoints to upload")
                return False

            logger.info(f"Uploading {count} waypoints to FC (start_from={start_from})")

            master.mav.mission_count_send(
                master.target_system,
                master.target_component,
                count,
                mavutil.mavlink.MAV_MISSION_TYPE_MISSION
            )

            # 3. 等待飞控请求并逐个发送航点
            uploaded = 0
            timeout = time.time() + 30.0
            while uploaded < count and time.time() < timeout:
                msg = master.recv_match(
                    type=['MISSION_REQUEST', 'MISSION_ACK', 'MISSION_REQUEST_INT'],
                    blocking=True,
                    timeout=5.0
                )
                if not msg:
                    logger.warning("Waypoint upload timeout waiting for request")
                    break

                if msg.get_type() == 'MISSION_ACK':
                    if msg.type == mavutil.mavlink.MAV_MISSION_ACCEPTED:
                        logger.info(f"Waypoint upload accepted: {uploaded}/{count}")
                        return True
                    else:
                        logger.error(f"Waypoint upload rejected: {msg.type}")
                        return False

                # 飞控请求第 seq 个航点
                seq = msg.seq
                if seq >= count:
                    continue

                wp = upload_list[seq]
                master.mav.mission_item_int_send(
                    master.target_system,
                    master.target_component,
                    seq,
                    mavutil.mavlink.MAV_FRAME_GLOBAL_RELATIVE_ALT,
                    mavutil.mavlink.MAV_CMD_NAV_WAYPOINT,
                    0, 1,  # current, autocontinue
                    0, 0, 0, 0,  # param1-4
                    int(wp['latitude'] * 1e7),
                    int(wp['longitude'] * 1e7),
                    wp['altitude'],
                    mavutil.mavlink.MAV_MISSION_TYPE_MISSION
                )
                uploaded += 1
                logger.debug(f"Uploaded waypoint {seq}: lat={wp['latitude']}, lon={wp['longitude']}, alt={wp['altitude']}")

            # 4. 等待最终ACK
            ack = master.recv_match(type='MISSION_ACK', blocking=True, timeout=5.0)
            if ack and ack.type == mavutil.mavlink.MAV_MISSION_ACCEPTED:
                logger.info(f"Waypoint upload complete: {uploaded}/{count}")
                return True
            else:
                logger.warning("Waypoint upload final ACK missing or rejected")
                return False

        except Exception as e:
            logger.error(f"Waypoint upload failed: {e}")
            return False

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
                                self._report_mission_progress(status='completed')
                                self.stop_mission_monitor()
                                logger.info("Mission completed!")
                else:
                    time.sleep(1.0)
            except Exception as e:
                logger.error(f"Mission monitor error: {e}")
                time.sleep(1.0)

    def _report_mission_progress(self, status: str = 'executing'):
        """上报航线任务进度"""
        total = len(self._current_wayline)
        current = self._current_waypoint_idx
        percent = int(current / total * 100) if total > 0 else 0

        if self._event_callback:
            event = MessageBuilder.build_event(
                'flighttask_progress',
                {
                    'result': 0,
                    'output': {
                        'ext': {
                            'current_waypoint_index': current,
                            'wayline_mission_state': 6 if status == 'executing' else 5,
                            'media_count': 0,
                            'track_id': '',
                            'flight_id': self._current_task_id or '',
                            'wayline_id': 0,
                        },
                        'progress': {
                            'current_step': 23,
                            'percent': percent
                        },
                        'status': status
                    }
                },
                self.gateway_sn
            )
            self._event_callback(event)

    # ---- 大疆标准FlightTask接口 ----

    def handle_flighttask_prepare(self, msg: dict) -> dict:
        """
        飞行任务准备 (flighttask_prepare)

        Request: {
            flighttask_id: str,
            wayline_type: 0=普通航线 1=全景 2=扫描,
            wayline_info: { waypoints: [...] }
        }
        Response: { result }
        Events: flighttask_prepare_progress
        """
        data = msg.get('data', {})
        task_id = data.get('flighttask_id', '')
        wayline_info = data.get('wayline_info', {})
        logger.info(f"Flighttask prepare: {task_id}")

        self._status = self.MISSION_PREPARING
        self._current_task_id = task_id

        # 1. 加载航线
        if wayline_info.get('waypoints'):
            parsed = []
            for i, wp in enumerate(wayline_info['waypoints']):
                p = self._parse_waypoint(wp, i)
                if p:
                    parsed.append(p)
            self._current_wayline = parsed
            logger.info(f"Parsed {len(parsed)} waypoints for task {task_id}")

        # 2. 上传航点到飞控
        upload_ok = False
        if self._current_wayline:
            upload_ok = self.upload_waypoints_to_fc(self._current_wayline)

        self._status = self.MISSION_IDLE if upload_ok else self.MISSION_FAILED

        # 上报准备进度
        if self._event_callback:
            event = MessageBuilder.build_event(
                'flighttask_prepare_progress',
                {
                    'flighttask_id': task_id,
                    'status': 'ready' if upload_ok else 'failed',
                    'progress': 100 if upload_ok else 0
                },
                self.gateway_sn
            )
            self._event_callback(event)

        return MessageBuilder.build_reply(msg, result=0 if upload_ok else -1, data={
            'flighttask_id': task_id,
            'status': 'ready' if upload_ok else 'failed'
        })

    def handle_flighttask_execute(self, msg: dict) -> dict:
        """
        飞行任务执行 (flighttask_execute)

        Request: { flighttask_id: str }
        Response: { result }
        Events: flighttask_execute_progress
        """
        data = msg.get('data', {})
        task_id = data.get('flighttask_id', '')
        logger.info(f"Flighttask execute: {task_id}")

        self._current_task_id = task_id
        self._status = self.MISSION_RUNNING

        # 启动航点监控线程
        self.start_mission_monitor()

        # 切换到AUTO模式执行航线
        if self._flight_control:
            result = self._flight_control.set_mode("AUTO")
            if not result.get("success"):
                logger.warning(f"启动航线任务失败: {result.get('message')}")
                self._status = self.MISSION_FAILED
                return MessageBuilder.build_reply(msg, result=-1, msg_str=result.get('message'))

        return MessageBuilder.build_reply(msg, result=0, data={
            'flighttask_id': task_id,
            'status': 'executing'
        })

    def handle_flighttask_undo(self, msg: dict) -> dict:
        """
        飞行任务撤销 (flighttask_undo)

        Request: { flighttask_id: str }
        Response: { result }
        """
        data = msg.get('data', {})
        task_id = data.get('flighttask_id', '')
        logger.info(f"Flighttask undo: {task_id}")

        self._status = self.MISSION_CANCELED

        # TODO: 撤销任务，返航或降落

        return MessageBuilder.build_reply(msg, result=0, data={
            'flighttask_id': task_id,
            'status': 'undone'
        })

    def handle_flighttask_return(self, msg: dict) -> dict:
        """
        飞行任务返航 (flighttask_return)

        Request: { flighttask_id: str, return_type: 0=返航 1=降落 }
        Response: { result }
        """
        data = msg.get('data', {})
        task_id = data.get('flighttask_id', '')
        return_type = data.get('return_type', 0)
        logger.info(f"Flighttask return: {task_id}, type={return_type}")

        # TODO: 执行返航或降落
        if self._flight_control:
            if return_type == 0:
                result = self._flight_control.one_click_return()
            else:
                result = self._flight_control.land()
            if not result.get("success"):
                logger.warning(f"Flighttask return failed: {result.get('message')}")

        return MessageBuilder.build_reply(msg, result=0, data={
            'flighttask_id': task_id,
            'return_type': return_type
        })

    def handle_flighttask_progress(self, msg: dict) -> dict:
        """
        飞行任务进度查询 (flighttask_progress)

        Request: { flighttask_id: str }
        Response: { status, progress, current_waypoint, total_waypoints }
        """
        data = msg.get('data', {})
        task_id = data.get('flighttask_id', '')
        logger.info(f"Flighttask progress query: {task_id}")

        total = len(self._current_wayline)
        progress = int(self._current_waypoint_idx / total * 100) if total > 0 else 0

        return MessageBuilder.build_reply(msg, result=0, data={
            'flighttask_id': task_id,
            'status': self._status,
            'progress': progress,
            'current_waypoint': self._current_waypoint_idx,
            'total_waypoints': total,
            'task_status': self._get_task_status_name()
        })

    def handle_flighttask_pause(self, msg: dict) -> dict:
        """飞行任务暂停（标准名）"""
        return self.handle_pause_wayline(msg)

    def handle_flighttask_resume(self, msg: dict) -> dict:
        """飞行任务恢复（标准名）"""
        return self.handle_resume_wayline(msg)

    def handle_flighttask_ready(self, msg: dict) -> dict:
        """
        飞行任务准备就绪查询/通知 (flighttask_ready)

        Direction: down/up
        Request: { flight_ids: [...] }
        Response: { result }
        """
        data = msg.get('data', {})
        flight_ids = data.get('flight_ids', [])
        logger.info(f"Flighttask ready: {flight_ids}")

        # TODO: 实现具体逻辑

        return MessageBuilder.build_reply(msg, result=0, data={
            'flight_ids': flight_ids
        })

    def handle_return_home_info(self, msg: dict) -> dict:
        """
        返航信息 (return_home_info)

        Direction: down/up
        Request: { planned_path_points, last_point_type, flight_id }
        Response: { result }
        """
        data = msg.get('data', {})
        flight_id = data.get('flight_id', '')
        planned_path_points = data.get('planned_path_points', [])
        last_point_type = data.get('last_point_type', 0)
        logger.info(f"Return home info: flight_id={flight_id}, points={len(planned_path_points)}, last_point_type={last_point_type}")

        # TODO: 实现具体逻辑

        return MessageBuilder.build_reply(msg, result=0, data={
            'flight_id': flight_id
        })

    def handle_flight_setup_abort(self, msg: dict) -> dict:
        """
        飞行准备中止 (flight_setup_abort)

        Direction: down
        仅支持航线任务和指令飞行任务准备阶段调用。

        Request: null
        Response: { result }
        """
        logger.info("Flight setup abort")

        # TODO: 实现具体逻辑

        return MessageBuilder.build_reply(msg, result=0)

    def handle_flighttask_create(self, msg: dict) -> dict:
        """
        飞行任务创建 (flighttask_create) - 已废弃

        Direction: down
        注意: 功能等同于 wayline_task_create

        Request: { flight_id, type, file: { url, sign } }
        Response: { result }
        """
        data = msg.get('data', {})
        flight_id = data.get('flight_id', '')
        file_info = data.get('file', {})
        logger.info(f"Flighttask create (deprecated): {flight_id}, type={data.get('type', '')}")

        # TODO: 实现具体逻辑

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

        Direction: down
        注意: 功能等同于 cancel_return_home

        Request: null
        Response: { result }
        """
        logger.info("Return home cancel")
        return self.handle_cancel_return_home(msg)

    def handle_flighttask_resource_get(self, msg: dict) -> dict:
        """
        获取飞行任务资源 (flighttask_resource_get)

        Direction: down
        Request: { flight_id }
        Response: { result, output: { file: { url, fingerprint } } }
        """
        data = msg.get('data', {})
        flight_id = data.get('flight_id', '')
        logger.info(f"Flighttask resource get: {flight_id}")

        # TODO: 实现具体逻辑

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

