"""
航点管理模块 - 整合到观察者模式框架（最终修复版）
"""
import time
import threading
import asyncio
from typing import List, Dict, Optional
from uuid import UUID
from pymavlink import mavutil, mavwp
from pymavlink.mavutil import mavlink

from src.modules.base_module import BaseModule
from src.core.message_dispatcher import MAVLinkMessage, MessageDispatcher

# 🔥 修复：移除 AsyncSessionLocal 的直接导入
from src.database.connection import get_async_session_local, init_database_async
from src.database.mission_db import MissionDB
from src.database.waypoint_db import WaypointDB
from src.database.schemas import MissionCreate, WaypointCreate



class WaypointManager(BaseModule):
    """
    航点管理模块（最终修复版）
    """

    def __init__(self, target_system: int = 1, target_component: int = 1):
        super().__init__("WaypointManager")

        self.target_system = target_system
        self.target_component = target_component

        # 航点加载器
        self.wploader = mavwp.MAVWPLoader()

        # 状态管理
        self.mission_count = 0
        self.current_waypoint = -1
        self.home_position = None

        # 同步控制
        self.mission_loading_complete = threading.Event()
        self.mission_upload_complete = threading.Event()
        self.received_waypoints = set()
        self.is_requesting = False

        # 消息分发器引用
        self.dispatcher: Optional[MessageDispatcher] = None

        # 当前任务信息
        self.current_mission_info = None

        # 主事件循环引用
        self.main_loop: Optional[asyncio.AbstractEventLoop] = None
        self._loop_capture_attempted = False

    def _ensure_event_loop(self):
        """
        🔥 延迟捕获事件循环（在第一次使用时）
        """
        if self.main_loop is None and not self._loop_capture_attempted:
            self._loop_capture_attempted = True
            try:
                self.main_loop = asyncio.get_running_loop()
                self.log(f"✅ 已捕获主事件循环: {id(self.main_loop)}")
            except RuntimeError:
                self.log("⚠️ 未检测到运行中的事件循环", "WARNING")

    def set_mission_info(self, mission_name: str, device_id: str,
                        execution_conditions: Optional[Dict] = None):
        """
        设置当前任务信息（上传前调用）
        """
        try:
            # 验证 device_id 格式
            UUID(device_id)

            self.current_mission_info = {
                "name": mission_name,
                "device_id": device_id,
                "execution_conditions": execution_conditions or {}
            }
            self.log(f"📝 设置任务信息: {mission_name}, 设备ID: {device_id}")

            # 🔥 第一次调用时尝试捕获事件循环
            self._ensure_event_loop()

        except ValueError as e:
            self.log(f"❌ 无效的设备ID格式: {device_id}", "ERROR")
            raise ValueError(f"device_id 必须是有效的UUID格式: {e}")

    async def _save_mission_to_database_async(self):
        """
        异步保存任务到数据库（使用批量操作）
        """
        try:
            if not self.current_mission_info:
                self.log("⚠️ 未设置任务信息，跳过数据库保存", "WARNING")
                return None

            waypoints = self.get_waypoints()
            if not waypoints:
                self.log("⚠️ 没有航点数据，跳过数据库保存", "WARNING")
                return None

            self.log(f"💾 准备保存任务到数据库: {self.current_mission_info['name']}")
            self.log(f"   航点数量: {len(waypoints)}")

            # 🔥 延迟初始化数据库
            from database.connection import get_async_session_local, init_database_async

            # 确保数据库已初始化
            await init_database_async()

            # 获取正确的 AsyncSessionLocal
            AsyncSessionLocal = get_async_session_local()

            async with AsyncSessionLocal() as session:
                # 1. 创建任务
                mission_create = MissionCreate(
                    name=self.current_mission_info["name"],
                    device_id=UUID(self.current_mission_info["device_id"]),
                    status="uploaded",
                    execution_conditions=self.current_mission_info.get("execution_conditions")
                )

                mission = await MissionDB.create_mission(session, mission_create)
                self.log(f"✅ 任务已创建，ID: {mission.id}")

                # 2. 批量创建航点
                waypoints_create = []
                for wp in waypoints:
                    waypoint_create = WaypointCreate(
                        mission_id=mission.id,
                        sequence=wp["seq"],
                        command=wp["command"],
                        frame=wp["frame"],
                        latitude=wp["lat"],
                        longitude=wp["lon"],
                        altitude=wp["alt"],
                        param1=wp.get("param1", 0.0),
                        param2=wp.get("param2", 0.0),
                        param3=wp.get("param3", 0.0),
                        param4=wp.get("param4", 0.0),
                        autocontinue=wp.get("autocontinue", 1),
                        action_type=wp.get("action_type", "waypoint"),
                        flight_speed=wp.get("flight_speed"),
                        photo_interval=wp.get("photo_interval")
                    )
                    waypoints_create.append(waypoint_create)

                created_waypoints = await WaypointDB.batch_create_waypoints_for_mission(
                    session, mission.id, waypoints_create
                )

                self.log(f"✅ 已批量创建 {len(created_waypoints)} 个航点")

                # 3. 提交事务
                await session.commit()

                self.log(f"✅ 任务保存成功！任务ID: {mission.id}", "INFO")

                # 清除任务信息
                self.current_mission_info = None

                return str(mission.id)

        except Exception as e:
            self.log(f"❌ 保存任务到数据库失败: {e}", "ERROR")
            import traceback
            traceback.print_exc()
            return None

    def _save_mission_to_database(self):
        """
        同步包装器：使用 run_coroutine_threadsafe 在主事件循环中执行
        """
        try:
            # 🔥 确保事件循环已捕获
            self._ensure_event_loop()

            if not self.main_loop:
                self.log("❌ 主事件循环未设置，无法保存数据库", "ERROR")
                return

            # 在主事件循环中调度协程
            future = asyncio.run_coroutine_threadsafe(
                self._save_mission_to_database_async(),
                self.main_loop
            )

            # 等待完成
            mission_id = future.result(timeout=30)

            if mission_id:
                self.log(f"✅ 数据库保存完成，任务ID: {mission_id}", "INFO")
            else:
                self.log("⚠️ 数据库保存未执行", "WARNING")

        except Exception as e:
            self.log(f"❌ 数据库保存线程异常: {e}", "ERROR")
            import traceback
            traceback.print_exc()

    async def update_mission_in_database_async(self, mission_id: str):
        """更新数据库中的任务航点"""
        try:
            mission_uuid = UUID(mission_id)
            waypoints = self.get_waypoints()

            if not waypoints:
                self.log("⚠️ 没有航点数据，跳过更新", "WARNING")
                return False

            self.log(f"🔄 更新任务航点: {mission_id}")
            self.log(f"   航点数量: {len(waypoints)}")

            AsyncSessionLocal = get_async_session_local()
            async with AsyncSessionLocal() as session:
                waypoints_create = []
                for wp in waypoints:
                    waypoint_create = WaypointCreate(
                        mission_id=mission_uuid,
                        sequence=wp["seq"],
                        command=wp["command"],
                        frame=wp["frame"],
                        latitude=wp["lat"],
                        longitude=wp["lon"],
                        altitude=wp["alt"],
                        param1=wp.get("param1", 0.0),
                        param2=wp.get("param2", 0.0),
                        param3=wp.get("param3", 0.0),
                        param4=wp.get("param4", 0.0),
                        autocontinue=wp.get("autocontinue", 1),
                        action_type=wp.get("action_type", "waypoint"),
                        flight_speed=wp.get("flight_speed"),
                        photo_interval=wp.get("photo_interval")
                    )
                    waypoints_create.append(waypoint_create)

                replaced_waypoints = await WaypointDB.replace_mission_waypoints(
                    session, mission_uuid, waypoints_create
                )

                await session.commit()

                self.log(f"✅ 任务航点已替换，共 {len(replaced_waypoints)} 个航点", "INFO")
                return True

        except Exception as e:
            self.log(f"❌ 更新任务航点失败: {e}", "ERROR")
            import traceback
            traceback.print_exc()
            return False

    async def load_mission_from_database_async(self, mission_id: str) -> bool:
        """从数据库加载任务"""
        try:
            mission_uuid = UUID(mission_id)

            self.log(f"📥 从数据库加载任务: {mission_id}")

            AsyncSessionLocal = get_async_session_local()
            async with AsyncSessionLocal() as session:
                mission = await MissionDB.get_mission(session, mission_uuid)
                if not mission:
                    self.log(f"❌ 任务不存在: {mission_id}", "ERROR")
                    return False

                waypoints = await WaypointDB.get_waypoints_by_mission(session, mission_uuid)
                if not waypoints:
                    self.log(f"⚠️ 任务没有航点: {mission_id}", "WARNING")
                    return False

                self.wploader.clear()

                for wp in waypoints:
                    wp_dict = {
                        "seq": wp.sequence,
                        "frame": wp.frame,
                        "command": wp.command,
                        "lat": wp.latitude,
                        "lon": wp.longitude,
                        "alt": wp.altitude,
                        "param1": wp.param1 or 0.0,
                        "param2": wp.param2 or 0.0,
                        "param3": wp.param3 or 0.0,
                        "param4": wp.param4 or 0.0,
                        "autocontinue": wp.autocontinue,
                        "action_type": wp.action_type,
                        "flight_speed": wp.flight_speed,
                        "photo_interval": wp.photo_interval
                    }
                    self.add_waypoint_from_dict(wp_dict)

                self.log(f"✅ 已加载 {len(waypoints)} 个航点", "INFO")
                self.log(f"   任务名称: {mission.name}")
                self.log(f"   任务状态: {mission.status}")

                return True

        except Exception as e:
            self.log(f"❌ 从数据库加载任务失败: {e}", "ERROR")
            import traceback
            traceback.print_exc()
            return False

    def add_waypoint_from_dict(self, wp_dict: dict):
        """从字典添加航点"""
        wp = mavutil.mavlink.MAVLink_mission_item_int_message(
            self.target_system,
            self.target_component,
            wp_dict["seq"],
            wp_dict["frame"],
            wp_dict["command"],
            0,
            wp_dict.get("autocontinue", 1),
            wp_dict.get("param1", 0.0),
            wp_dict.get("param2", 0.0),
            wp_dict.get("param3", 0.0),
            wp_dict.get("param4", 0.0),
            int(wp_dict["lat"] * 1e7),
            int(wp_dict["lon"] * 1e7),
            wp_dict["alt"]
        )
        self.wploader.add(wp)

    @staticmethod
    def _coord_to_mission_int(value) -> int:
        """Return MAVLink MISSION_ITEM_INT coordinate without double scaling."""
        try:
            number = float(value)
        except (TypeError, ValueError):
            return 0
        if abs(number) > 180:
            return int(round(number))
        return int(round(number * 1e7))

    def clear_mission(self):
        """清空当前任务"""
        self.wploader.clear()
        self.log("已清空当前任务")

    def on_start(self):
        """模块启动"""
        super().on_start()
        self.log("航点管理模块已启动（使用批量数据库操作）")

    def on_message(self, message: MAVLinkMessage):
        """消息回调"""
        msg_type = message.msg_type
        msg = message.msg

        handlers = {
            'MISSION_COUNT': self._handle_mission_count,
            'MISSION_ITEM': self._handle_mission_item,
            'MISSION_ITEM_INT': self._handle_mission_item_int,
            'MISSION_CURRENT': self._handle_mission_current,
            'MISSION_ACK': self._handle_mission_ack,
            'HOME_POSITION': self._handle_home_position,
            'MISSION_REQUEST': self._handle_mission_request,
            'MISSION_REQUEST_INT': self._handle_mission_request_int,
        }

        handler = handlers.get(msg_type)
        if handler:
            try:
                handler(msg)
            except Exception as e:
                self.log(f"处理消息 {msg_type} 时出错: {e}", "ERROR")

    def idle_task(self, dispatcher: MessageDispatcher):
        """空闲任务"""
        if self.dispatcher is None:
            self.dispatcher = dispatcher

    # ========== 消息处理器 ==========

    def _handle_mission_count(self, msg):
        """处理MISSION_COUNT"""
        self.mission_count = msg.count
        self.mission_loading_complete.clear()
        self.received_waypoints.clear()

        self.log(f"收到航点数量: {msg.count}")

        self.wploader.clear()

        if msg.count == 0:
            self.mission_loading_complete.set()
            self.is_requesting = False
            self.log("飞行器没有航点")
            return

        for seq in range(msg.count):
            self._request_waypoint(seq)
            time.sleep(0.05)

    def _handle_mission_item(self, msg):
        """处理MISSION_ITEM"""
        self._process_mission_item(msg)

    def _handle_mission_item_int(self, msg):
        """处理MISSION_ITEM_INT"""
        self._process_mission_item(msg)

    def _process_mission_item(self, msg):
        """处理航点项目"""
        if msg.seq in self.received_waypoints:
            return

        if hasattr(msg, 'x') and isinstance(msg.x, int):
            wp = mavutil.mavlink.MAVLink_mission_item_message(
                msg.target_system, msg.target_component, msg.seq, msg.frame,
                msg.command, msg.current, msg.autocontinue,
                msg.param1, msg.param2, msg.param3, msg.param4,
                msg.x / 1e7, msg.y / 1e7, msg.z
            )
        else:
            wp = msg

        while self.wploader.count() <= msg.seq:
            dummy_wp = mavutil.mavlink.MAVLink_mission_item_message(
                self.target_system, self.target_component,
                self.wploader.count(), 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0
            )
            self.wploader.add(dummy_wp)

        self.wploader.set(wp, msg.seq)
        self.received_waypoints.add(msg.seq)

        self.log(f"收到航点 {msg.seq + 1}/{self.mission_count}")

        if len(self.received_waypoints) >= self.mission_count:
            self.mission_loading_complete.set()
            self.is_requesting = False
            self.log(f"✓ 所有 {self.mission_count} 个航点已接收", "INFO")

    def _handle_mission_current(self, msg):
        """处理MISSION_CURRENT"""
        if msg.seq != self.current_waypoint:
            self.current_waypoint = msg.seq
            self.log(f"当前航点: {msg.seq}")

    def _handle_mission_ack(self, msg):
        """处理MISSION_ACK - 上传成功后自动保存"""
        ack_types = {
            0: "接受",
            1: "错误",
            2: "不支持的坐标系",
            3: "无效序列",
            4: "当前任务项",
            5: "超时",
            6: "取消",
        }
        ack_msg = ack_types.get(msg.type, f"未知({msg.type})")
        self.log(f"任务确认: {ack_msg}")

        if msg.type == 0:
            self.mission_upload_complete.set()
            #
            # if self.current_mission_info:
            #     self.log("💾 启动数据库保存线程...")
            #     threading.Thread(
            #         target=self._save_mission_to_database,
            #         daemon=True,
            #         name="MissionSaver"
            #     ).start()
            # else:
            #     self.log("⚠️ 未设置任务信息，跳过数据库保存", "WARNING")

    def _handle_home_position(self, msg):
        """处理HOME_POSITION"""
        self.home_position = {
            "lat": msg.latitude / 1e7,
            "lon": msg.longitude / 1e7,
            "alt": msg.altitude / 1000.0
        }
        # self.log(f"Home位置: {self.home_position}")

    def _handle_mission_request(self, msg):
        """处理飞行器请求航点"""
        self.log(f"飞行器请求航点 {msg.seq}")
        self._send_waypoint(msg.seq)

    def _handle_mission_request_int(self, msg):
        """处理飞行器请求航点INT"""
        self.log(f"飞行器请求航点INT {msg.seq}")
        self._send_waypoint(msg.seq)

    # ========== 航点操作 ==========

    def add_waypoint(self,
                     lat: float,
                     lon: float,
                     alt: float = 100,
                     command: int = mavlink.MAV_CMD_NAV_WAYPOINT,
                     frame: int = mavlink.MAV_FRAME_GLOBAL_RELATIVE_ALT,
                     param1: float = 0.0,
                     param2: float = 0.0,
                     param3: float = 0.0,
                     param4: float = 0.0,
                     autocontinue: int = 1) -> int:
        """添加航点(支持完整参数)"""

        wp = mavutil.mavlink.MAVLink_mission_item_message(
            self.target_system,
            self.target_component,
            self.wploader.count(),
            frame,
            command,
            0,  # current
            autocontinue,
            param1,
            param2,
            param3,
            param4,
            lat,
            lon,
            alt
        )

        self.wploader.add(wp)
        index = self.wploader.count() - 1
        self.log(f"添加航点 {index}: ({lat:.6f}, {lon:.6f}, {alt}m) cmd={command}")
        return index

    def update_waypoint(self, index: int, lat: float = None,
                        lon: float = None, alt: float = None) -> bool:
        """更新航点"""
        if 0 <= index < self.wploader.count():
            wp = self.wploader.wp(index)
            if wp:
                if lat is not None:
                    wp.x = lat
                if lon is not None:
                    wp.y = lon
                if alt is not None:
                    wp.z = alt
                self.wploader.set(wp, index)
                self.log(f"更新航点 {index}")
                return True
        return False

    def remove_waypoint(self, index: int) -> bool:
        """删除航点"""
        if 0 <= index < self.wploader.count():
            self.wploader.remove(index)
            self.log(f"删除航点 {index}")
            return True
        return False

    def clear_waypoints(self):
        """清除所有航点"""
        count = self.wploader.count()
        self.wploader.clear()
        self.log(f"清除所有航点 (共{count}个)")

    def get_waypoints(self) -> List[Dict]:
        """获取所有航点"""
        waypoints = []

        command_to_action = {
            16: "takeoff",
            17: "roi",
            18: "landing",
            19: "loiter",
            20: "loiter",
            21: "loiter",
            22: "camera",
            203: "camera",
        }

        for i in range(self.wploader.count()):
            wp = self.wploader.wp(i)
            if wp:
                if hasattr(wp, 'x') and isinstance(wp.x, int):
                    lat, lon = wp.x / 1e7, wp.y / 1e7
                else:
                    lat, lon = wp.x, wp.y

                action_type = command_to_action.get(wp.command, "waypoint")

                waypoints.append({
                    "seq": i,
                    "command": wp.command,
                    "frame": wp.frame,
                    "lat": lat,
                    "lon": lon,
                    "alt": wp.z,
                    "param1": float(wp.param1),
                    "param2": float(wp.param2),
                    "param3": float(wp.param3),
                    "param4": float(wp.param4),
                    "autocontinue": int(wp.autocontinue),
                    "action_type": action_type,
                    "flight_speed": None,
                    "photo_interval": None
                })

        return waypoints

    def get_waypoint_count(self) -> int:
        """获取航点数量"""
        return self.wploader.count()

    # ========== MAVLink通信 ==========

    def _send_message(self, msg):
        """发送MAVLink消息"""
        if self.dispatcher and self.dispatcher.connection:
            self.dispatcher.connection.send_message(msg)
            return True
        return False

    def _request_waypoint(self, seq: int):
        """请求指定序号的航点"""
        msg = mavutil.mavlink.MAVLink_mission_request_int_message(
            self.target_system,
            self.target_component,
            seq,
            mavlink.MAV_MISSION_TYPE_MISSION
        )
        self._send_message(msg)

    def _send_waypoint(self, seq: int):
        """发送航点给飞行器"""
        if seq >= self.wploader.count():
            return

        wp = self.wploader.wp(seq)
        if wp:
            msg = mavutil.mavlink.MAVLink_mission_item_int_message(
                self.target_system,
                self.target_component,
                seq,
                wp.frame,
                wp.command,
                wp.current,
                wp.autocontinue,
                wp.param1, wp.param2, wp.param3, wp.param4,
                self._coord_to_mission_int(wp.x),
                self._coord_to_mission_int(wp.y),
                wp.z,
                mavlink.MAV_MISSION_TYPE_MISSION
            )
            self._send_message(msg)

    def request_mission_list(self, timeout: float = 8.0) -> bool:
        """请求任务列表"""
        if self.is_requesting:
            return False

        self.is_requesting = True
        self.mission_loading_complete.clear()

        msg = mavutil.mavlink.MAVLink_mission_request_list_message(
            self.target_system,
            self.target_component,
            mavlink.MAV_MISSION_TYPE_MISSION
        )

        if not self._send_message(msg):
            self.is_requesting = False
            return False

        completed = self.mission_loading_complete.wait(timeout=timeout)
        return completed

    def upload_mission(self, timeout: float = 10.0) -> bool:
        """上传任务到飞行器"""
        if self.wploader.count() == 0:
            return False

        self.mission_upload_complete.clear()

        msg = mavutil.mavlink.MAVLink_mission_count_message(
            self.target_system,
            self.target_component,
            self.wploader.count(),
            mavlink.MAV_MISSION_TYPE_MISSION
        )

        if not self._send_message(msg):
            return False

        self.log(f"开始上传 {self.wploader.count()} 个航点")
        completed = self.mission_upload_complete.wait(timeout=timeout)

        if completed:
            self.log("✓ 任务上传成功", "INFO")

        return completed

    def set_current_waypoint(self, wp_num: int, reset: bool = False) -> bool:
        """设置当前航点"""
        p2 = 1 if reset else 0
        msg = mavutil.mavlink.MAVLink_command_long_message(
            self.target_system,
            self.target_component,
            mavlink.MAV_CMD_DO_SET_MISSION_CURRENT,
            0,
            wp_num, p2, 0, 0, 0, 0, 0
        )

        success = self._send_message(msg)
        if success:
            self.log(f"设置当前航点为 {wp_num}")
        return success

    def request_home_position(self) -> bool:
        """请求Home位置"""
        msg = mavutil.mavlink.MAVLink_command_long_message(
            self.target_system,
            0,
            mavlink.MAV_CMD_GET_HOME_POSITION,
            0, 0, 0, 0, 0, 0, 0, 0
        )

        success = self._send_message(msg)
        if success:
            self.log("请求Home位置")
        return success
