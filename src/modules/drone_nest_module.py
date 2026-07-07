"""
机巢遥测模块 - 处理机巢环境监测和无人机-机巢状态数据
modules/drone_nest_module.py
"""
import time
from typing import Optional, Dict, Any, List, Callable
from src.modules.base_module import BaseModule
from src.core.message_dispatcher import MAVLinkMessage, MessageDispatcher
from pymavlink import mavutil
import sys
import os

from src.utils.command_trace import trace_command

# 导入自定义 dialect（包含你的自定义消息）
from src.generated_dialect.drone_nest import *
from src.generated_dialect.gcs_order_protocol import (
    GCSOrderCodec,
    GCSCommand,
    DoorState,
    GCSOrderDecoded,
    REJECT_REASONS
)
from web.mavlink.nest.property_mapping import build_dock3_properties_from_nest


class DroneNestModule(BaseModule):
    """
    机巢遥测模块

    功能：
    - 实时接收机巢环境监测数据（温度、风速、气压、降雨等）
    - 接收无人机与机巢的状态数据
    - 支持数据回调推送
    - ⭐ 支持一键启航协议（转发机巢消息到FlightControl）
    - ⭐ 支持 gcs_order 位域协议解析
    - 提供数据查询接口
    """

    def __init__(self, target_system: int = 101, target_component: int = 1):
        """初始化机巢遥测模块"""
        super().__init__("DroneNestModule")
        self.target_system = target_system
        self.target_component = target_component
        self.dispatcher: Optional[MessageDispatcher] = None

        # 机巢环境数据
        self.environment_data = {
            "nest_temperature": 0.0,      # 机巢内部温度 (°C)
            "wind_direction": 0.0,        # 风向 (0-360°)
            "wind_speed": 0.0,            # 风速 (m/s)
            "atmospheric_pressure": 0.0,  # 大气压力 (kPa)
            "rainfall": 0.0,              # 降雨量 (mm/h)
            "ac_temp": 0,                 # 空调温度
            "gcs_order": 0,               # 地面站指令（原始值）
            "gcs_order_decoded": None,    # ⭐ 解码后的 gcs_order（GCSOrderDecoded对象）
            "cloud_properties": {},       # MAVLink 状态转换后的上云 API 属性字段
            "last_update": 0.0,           # 最后更新时间
            "data_valid": False           # 数据有效性
        }

        # 无人机-机巢状态数据
        self.uav_nest_status = {
            "rel_alt": 0.0,              # 相对地面高度 (m)
            "status": 0,                 # 状态标志
            "last_update": 0.0,          # 最后更新时间
            "data_valid": False          # 数据有效性
        }

        # 数据更新回调列表
        self.env_callbacks: List[Callable] = []
        self.status_callbacks: List[Callable] = []

        # 数据统计
        self.env_message_count = 0
        self.status_message_count = 0

        # 数据超时设置（秒）
        self.data_timeout = 5.0
        self.status_poll_interval = 2.0
        self._last_status_poll_time = 0.0

        # ⭐ 飞行控制模块引用（用于一键启航协议）
        self.flight_control = None

        # ⭐ gcs_order 编解码器
        self.gcs_codec = GCSOrderCodec()

        # ⭐ 上一次的 gcs_order（用于检测变化）
        self._last_gcs_order = 0
        # ⭐ 新增：记录上一次的状态（用于复位检测）
        self.last_door_state = None
        self.last_charging = None
        self.last_homing = None
        self.last_allow_takeoff = None
        self.last_estop = None

        # 操作事件回调
        self._operation_callback: Optional[Callable] = None

        # 最近一次机巢命令执行回复，已转换为上层 API 可直接使用的结构
        self.last_order_response: Dict[str, Any] = {}
        self.last_cloud_order_response: Dict[str, Any] = {}

    # ========== 模块关联 ==========

    def set_flight_control(self, flight_control):
        """
        注入飞行控制模块（用于一键启航协议）

        Args:
            flight_control: FlightControl模块实例
        """
        self.flight_control = flight_control
        self.log("✅ 已关联飞行控制模块")

    def set_operation_callback(self, callback: Callable):
        """
        设置操作事件回调

        Args:
            callback: 回调函数，接收操作事件字典
        """
        self._operation_callback = callback
        self.log("✅ 已设置操作事件回调")

    def _report_operation_event(self, event_type: str, command: str, data: dict = None):
        """
        上报操作事件

        Args:
            event_type: 事件类型 (started/completed/failed/progress)
            command: 命令名称
            data: 事件数据
        """
        event = {
            'event_type': event_type,
            'command': command,
            'timestamp': time.time(),
        }
        if data:
            event.update(data)

        if self._operation_callback:
            try:
                self._operation_callback(event)
            except Exception as e:
                self.log(f"操作事件回调执行错误: {e}", "ERROR")

    # ========== 消息处理 ==========

    def on_message(self, message: MAVLinkMessage):
        """处理机巢消息"""
        msg = message.msg
        msg_type = message.msg_type
        try:
            # print(f"📡 收到消息: {msg}")

            if msg_type == "DRONE_NEST_RESPONSE":
                # ✅ 路由到专用处理函数（统一逻辑，不要在这里内联）
                self._handle_nest_response(msg)

            elif msg_type == "DRONE_NEST_ORDER_RESPONSE":
                # ✅ 路由到专用处理函数
                self._handle_order_response(msg)

            else:
                self.log(f"⚠️ 未处理的消息类型: {msg_type}", "WARNING")

        except AttributeError as e:
            print(f"[DroneNestModule][ERROR] 消息字段错误: {e}")
            print(f"[DroneNestModule][DEBUG] 消息类型: {type(msg)}")
            print(f"[DroneNestModule][DEBUG] 可用字段: {[attr for attr in dir(msg) if not attr.startswith('_')]}")
        except Exception as e:
            print(f"[DroneNestModule][ERROR] 处理消息失败: {e}")
            import traceback
            traceback.print_exc()

    def _decode_clamp_state(self, state: int) -> str:
        """解码夹紧状态"""
        states = {
            0x0000: "默认",
            0x0001: "放开",
            0x0002: "夹紧",
            0x0003: "放开打开中",
            0x0004: "夹紧关闭中",
            0x0005: "暂停中",
            0x0006: "故障中"
        }
        return states.get(state, f"未知({state})")

    def _decode_gate_state(self, state: int) -> str:
        """解码门状态"""
        states = {
            0x0000: "默认",
            0x0001: "打开",
            0x0002: "关闭",
            0x0003: "开启中",
            0x0004: "关闭中",
            0x0005: "暂停中",
            0x0006: "故障中"
        }
        return states.get(state, f"未知({state})")

    def _decode_light_state(self, state: int) -> str:
        """解码灯状态"""
        states = {
            0x00: "默认",
            0x01: "打开",
            0x02: "关闭",
            0x06: "故障中"
        }
        return states.get(state, f"未知({state})")

    def _decode_gcs_order(self, gcs_order: int):
        """解码 gcs_order 字段"""
        decoded = self.gcs_codec.decode_nest_status(gcs_order)

        # ⭐ 返回包含 raw_value 的对象（用于 WebSocket 推送）
        class GCSOrderResult:
            def __init__(self, decoded_obj, raw):
                self.raw_value = raw
                self.commands = decoded_obj.commands if hasattr(decoded_obj, 'commands') else []
                self.door_state = decoded_obj.door_state
                self.homing = decoded_obj.homing
                self.charging = decoded_obj.charging
                self.estop = decoded_obj.estop
                self.plc_ready = decoded_obj.plc_ready
                self.allow_takeoff = decoded_obj.allow_takeoff
                self.takeoff_in_progress = decoded_obj.takeoff_in_progress
                self.landing_in_progress = decoded_obj.landing_in_progress

        return GCSOrderResult(decoded, gcs_order)

    @staticmethod
    def _map_rainfall(raw_rainfall: float) -> int:
        """机巢降雨量转换为上云属性降雨等级。"""
        if raw_rainfall <= 0:
            return 0
        if raw_rainfall < 2.5:
            return 1
        if raw_rainfall < 8:
            return 2
        return 3

    @staticmethod
    def _map_gate_state(gate_state: int) -> int:
        """gate_state -> cover_state。"""
        if gate_state == 1:
            return 1
        if gate_state in (0, 2):
            return 0
        if gate_state in (3, 4, 5):
            return 2
        return 3

    @staticmethod
    def _map_clamp_state(clamp_state: int) -> int:
        """clamp_state -> putter_state。"""
        if clamp_state == 1:
            return 1
        if clamp_state in (0, 2):
            return 0
        if clamp_state in (3, 4, 5):
            return 2
        return 3

    def _build_cloud_properties(self, nest_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        将 DRONE_NEST_RESPONSE 解析结果转换为上云 API 属性字段。

        这里只负责 MAVLink 层字段构造，不发布 MQTT。
        """
        return build_dock3_properties_from_nest(nest_data)

    @staticmethod
    def _decode_ordinary_order_response(raw_value: int) -> Dict[str, Any]:
        """解析 ordinary_order_response 位域。"""
        return {
            "raw": raw_value,
            "reply_status": raw_value & 0xFF,
            "reject_reason_code": (raw_value >> 8) & 0xFF,
            "command_code": (raw_value >> 16) & 0xFF,
            "device_code": (raw_value >> 24) & 0xFF,
        }

    @staticmethod
    def _decode_takeoff_land_order_response(raw_value: int) -> Dict[str, Any]:
        """解析 takeoff_land_order_response 位域。"""
        return {
            "raw": raw_value,
            "reply_status": raw_value & 0xFF,
            "reject_reason_code": (raw_value >> 8) & 0xFF,
            "reserved": (raw_value >> 16) & 0xFF,
            "command_code": (raw_value >> 24) & 0xFF,
        }

    def _build_cloud_order_response(self, order_result: Dict[str, Any]) -> Dict[str, Any]:
        """
        将 DRONE_NEST_ORDER_RESPONSE 解析结果转换为上层 API 可用结构。

        result=0 表示机巢未拒绝；非 0 表示存在拒绝原因。
        """
        rejections = order_result.get("rejections") or []
        first_rejection = rejections[0] if rejections else {}
        return {
            "result": 0 if not rejections else int(first_rejection.get("reason_code") or -1),
            "status": "success" if not rejections else "rejected",
            "ordinary_order_response": order_result.get("ordinary_response"),
            "takeoff_land_order_response": order_result.get("takeoff_land_response"),
            "rejections": rejections,
            "timestamp": int(order_result.get("timestamp", time.time()) * 1000),
        }

    def _send_reset_command(self):
        """
        发送复位指令
        复位 = ordinary_order=0, takeoff_land_order=0
        """
        try:
            if not self.dispatcher or not self.dispatcher.connection:
                self.log("⚠️ 无法发送复位指令：机巢连接未就绪", "WARNING")
                return

            connection = self.dispatcher.connection

            mav_obj = (
                connection.mav if hasattr(connection, 'mav')
                else connection.master.mav if hasattr(connection, 'master')
                else None
            )

            if not mav_obj:
                self.log("⚠️ 无法找到 MAV 对象", "ERROR")
                return

            # ✅ 使用真实存在的消息：DRONE_NEST_COMMAND
            if hasattr(mav_obj, 'drone_nest_command_send'):
                mav_obj.drone_nest_command_send(
                    ordinary_order=0,  # 复位 = 全0
                    takeoff_land_order=0
                )
                self.log("✅ 已发送复位指令 (DRONE_NEST_COMMAND, all zeros)")
            else:
                # 手动打包
                reset_msg = drone_nest.MAVLink_drone_nest_command_message(
                    ordinary_order=0,
                    takeoff_land_order=0
                )
                msgbuf = reset_msg.pack(mav_obj)

                if hasattr(connection, 'master'):
                    connection.master.write(msgbuf)
                elif hasattr(connection, 'write'):
                    connection.write(msgbuf)

                self.log("✅ 已发送复位指令 (手动打包)")

        except Exception as e:
            self.log(f"发送复位指令失败: {e}", "ERROR")
            import traceback
            traceback.print_exc()

    def _handle_nest_response(self, msg):
        """
        处理机巢状态反馈消息（DRONE_NEST_RESPONSE）

        根据二代机巢协议解析状态信息
        """
        try:
            # self.log(f"🔍 收到DRONE_NEST_RESPONSE消息，开始解析环境数据", "INFO")
            # 解析环境数据（单位转换）
            self.environment_data.update({
                "ac_temp": msg.ac_temp / 10.0,  # 空调温度 (°C)
                "nest_temperature": msg.nest_temperature / 10.0,  # 机巢温度 (°C) - 弃用
                "env_temperature": msg.env_temperature / 10.0,  # 环境温度 (°C)
                "wind_direction": msg.wind_direction / 100.0,  # 风向 (°)
                "wind_speed": msg.wind_speed / 10.0,  # 风速 (m/s)
                "atmospheric_pressure": msg.atmospheric_pressure / 10.0,  # 气压 (kPa)
                "rainfall": msg.rainfall / 10.0,  # 降雨量 (mm/m²)
                "charge_volt": msg.charge_volt / 100.0,  # 充电电压 (V)
                "charge_current": msg.charge_current / 100.0,  # 充电电流 (A)
                "error_code": msg.error_code,  # 故障码
                "last_update": time.time(),
                "data_valid": True
            })

            # 解析状态位
            boolstate1 = msg.boolstate1
            boolstate2 = msg.boolstate2

            # boolstate1: 心跳状态
            nest_heartbeat = bool(boolstate1 & (1 << 0))
            drone_heartbeat = bool(boolstate1 & (1 << 1))
            plc_heartbeat = bool(boolstate1 & (1 << 2))
            estop_state = bool(boolstate1 & (1 << 3))
            charging_state = bool(boolstate1 & (1 << 4))
            calibrate_state = bool(boolstate1 & (1 << 5))
            ready_state = bool(boolstate1 & (1 << 6))
            init_state = bool(boolstate1 & (1 << 7))
            system_busy = bool(boolstate1 & (1 << 8))
            fault_state = bool(boolstate1 & (1 << 9))
            r_axis_zero = bool(boolstate1 & (1 << 10))
            headlight_off = bool(boolstate1 & (1 << 11))

            # boolstate2: 流程状态
            takeoff_in_progress = bool(boolstate2 & (1 << 0))
            landing_in_progress = bool(boolstate2 & (1 << 1))
            takeoff_abort_progress = bool(boolstate2 & (1 << 2))
            landing_abort_progress = bool(boolstate2 & (1 << 3))
            emergency_landing = bool(boolstate2 & (1 << 4))
            takeoff_allowed = bool(boolstate2 & (1 << 5))

            # 更新状态数据
            self.environment_data.update({
                "nest_heartbeat": nest_heartbeat,
                "drone_heartbeat": drone_heartbeat,
                "plc_heartbeat": plc_heartbeat,
                "estop_state": estop_state,
                "charging_state": charging_state,
                "calibrate_state": calibrate_state,
                "ready_state": ready_state,
                "init_state": init_state,
                "system_busy": system_busy,
                "fault_state": fault_state,
                "r_axis_zero": r_axis_zero,
                "headlight_off": headlight_off,
                "takeoff_in_progress": takeoff_in_progress,
                "landing_in_progress": landing_in_progress,
                "takeoff_abort_progress": takeoff_abort_progress,
                "landing_abort_progress": landing_abort_progress,
                "emergency_landing": emergency_landing,
                "takeoff_allowed": takeoff_allowed,
                "clamp_state": msg.clamp_state,
                "gate_state": msg.gate_state,
                "landing_light_state": msg.landing_light_state
            })

            self.environment_data["cloud_properties"] = self._build_cloud_properties(self.environment_data)

            # 构造 gcs_order（兼容现有API）
            # 从解析出的状态位构造 gcs_order 值
            try:
                # 映射舱门状态到 DoorState 枚举
                door_state = DoorState.CLOSED  # 默认关闭
                if msg.gate_state == 1:
                    door_state = DoorState.OPEN
                elif msg.gate_state == 0:
                    door_state = DoorState.CLOSED
                
                # 使用 GCSOrderCodec 编码状态
                gcs_order_raw = self.gcs_codec.encode_nest_status(
                    door_state=door_state,
                    homing=True,  # 假设已归中
                    charging=charging_state,
                    estop=estop_state,
                    plc_ready=ready_state,
                    allow_takeoff=takeoff_allowed,
                    takeoff_in_progress=takeoff_in_progress,
                    landing_in_progress=landing_in_progress
                )
                
                # 解码 gcs_order
                decoded_order = self.gcs_codec.decode_nest_status(gcs_order_raw)
                
                # 更新 gcs_order 相关字段
                self.environment_data.update({
                    "gcs_order": gcs_order_raw,
                    "gcs_order_decoded": decoded_order
                })
                
            except Exception as codec_error:
                self.log(f"gcs_order 编解码失败: {codec_error}", "WARNING")
                self.environment_data.update({
                    "gcs_order": 0,
                    "gcs_order_decoded": None
                })

            # 通知回调
            self._notify_env_callbacks()
            # 在 _handle_nest_response 末尾更新 uav_nest_status
            self.uav_nest_status.update({
                "nest_heartbeat": nest_heartbeat,
                "drone_heartbeat": drone_heartbeat,
                "plc_heartbeat": plc_heartbeat,
                "system_busy": system_busy,
                "fault_state": fault_state,
                "r_axis_zero": r_axis_zero,
                "headlight_off": headlight_off,
                "clamp_state": msg.clamp_state,
                "gate_state": msg.gate_state,
                "landing_light_state": msg.landing_light_state,
                "error_code": msg.error_code,
                "last_update": time.time(),
                "data_valid": True
            })

            # 同时通知状态回调
            self.status_message_count += 1
            self._notify_status_callbacks()
            # self.log(f"📡 收到机巢状态: 心跳={nest_heartbeat}, 允许起飞={takeoff_allowed}")

        except Exception as e:
            self.log(f"处理机巢状态反馈时出错: {e}", "ERROR")

    def _handle_order_response(self, msg):
        """
        处理指令执行确认消息（DRONE_NEST_ORDER_RESPONSE）
        增强版：统一解析状态、拒绝原因，并准备前端展示数据
        """
        try:
            result = {
                "timestamp": time.time(),
                "ordinary_response": None,
                "takeoff_land_response": None,
                "rejections": []  # 前端统一展示的拒绝列表
            }

            # ==================== 解析普通命令回复 (字节10-13) ====================
            ordinary_info = self._decode_ordinary_order_response(msg.ordinary_order_response)
            ordinary_info.update({
                "type": "ordinary",
                "reject_reason": None,
            })

            if ordinary_info["reject_reason_code"] != 0:
                reason_text = REJECT_REASONS.get(11, {}).get(
                    ordinary_info["reject_reason_code"],
                    f"未知拒绝原因({ordinary_info['reject_reason_code']})"
                )
                ordinary_info["reject_reason"] = reason_text
                result["rejections"].append({
                    "source": "机巢主控",
                    "reason_code": ordinary_info["reject_reason_code"],
                    "reason": reason_text,
                    "command_type": "ordinary",
                    "command_code": ordinary_info["command_code"]
                })

            result["ordinary_response"] = ordinary_info

            # ==================== 解析起飞/降落命令回复 (字节14-17) ====================
            takeoff_info = self._decode_takeoff_land_order_response(msg.takeoff_land_order_response)
            takeoff_info.update({
                "type": "takeoff_land",
                "reject_reason": None,
            })

            if takeoff_info["reject_reason_code"] != 0:
                reason_text = REJECT_REASONS.get(15, {}).get(
                    takeoff_info["reject_reason_code"],
                    f"未知拒绝原因({takeoff_info['reject_reason_code']})"
                )
                takeoff_info["reject_reason"] = reason_text
                result["rejections"].append({
                    "source": "机巢主控",
                    "reason_code": takeoff_info["reject_reason_code"],
                    "reason": reason_text,
                    "command_type": "takeoff_land",
                    "command_code": takeoff_info["command_code"]
                })

            result["takeoff_land_response"] = takeoff_info

            is_empty_poll_response = (
                ordinary_info["raw"] == 0
                and takeoff_info["raw"] == 0
                and not result["rejections"]
            )
            if is_empty_poll_response:
                self.last_order_response = result
                self.last_cloud_order_response = self._build_cloud_order_response(result)
                return

            trace_command(
                "nest_order_response_received",
                ordinary_raw=ordinary_info.get("raw"),
                ordinary_command_code=ordinary_info.get("command_code"),
                ordinary_reply_status=ordinary_info.get("reply_status"),
                ordinary_reject_reason_code=ordinary_info.get("reject_reason_code"),
                takeoff_raw=takeoff_info.get("raw"),
                takeoff_command_code=takeoff_info.get("command_code"),
                takeoff_reply_status=takeoff_info.get("reply_status"),
                takeoff_reject_reason_code=takeoff_info.get("reject_reason_code"),
                rejections=result["rejections"],
            )
            self.log(f"检测到常规回复: {result['ordinary_response']}", "INFO")
            self.log(f"检测到起飞回复: {result['takeoff_land_response']}", "INFO")

            # ==================== 多拒绝优先级处理 ====================
            if len(result["rejections"]) > 1:
                # 按优先级排序：机巢主控 > PLC > 无人机（当前仅支持机巢主控，可后续扩展）
                priority_map = {"机巢主控": 0, "PLC": 1, "无人机": 2}
                result["rejections"].sort(key=lambda x: priority_map.get(x["source"], 99))

            # ==================== 记录日志 + 存储到模块（供API读取） ====================
            self.last_order_response = result  # ⭐ 新增成员变量，供API查询
            self.last_cloud_order_response = self._build_cloud_order_response(result)

            # 上报命令执行结果事件
            if result["rejections"]:
                self.log(f"⚠️ 检测到拒绝: {result['rejections']}", "WARNING")
                self._report_operation_event('failed', 'gcs_command_response',
                                             self.last_cloud_order_response)
            else:
                self.log("✅ 命令执行成功（无拒绝）", "INFO")
                self._report_operation_event('completed', 'gcs_command_response',
                                             self.last_cloud_order_response)

        except Exception as e:
            self.log(f"处理指令回复时出错: {e}", "ERROR")
            self._report_operation_event('failed', 'gcs_command_response',
                                         {'error': str(e)})
    # ========== 一键启航协议支持 ==========

    def send_gcs_command(self, *commands: GCSCommand):
        """向机巢发送地面站命令"""
        if not self.dispatcher or not self.dispatcher.connection:
            self.log("⚠️ 机巢连接未就绪，无法发送命令", "WARNING")
            self._report_operation_event('failed', 'gcs_command',
                                         {'commands': [cmd.name for cmd in commands],
                                          'error': 'connection not ready'})
            return

        try:
            gcs_order = self.gcs_codec.encode_gcs_command(*commands)

            # ✅ 拆分成 DRONE_NEST_COMMAND 的两个字段
            ordinary_order = int((gcs_order >> 8) & 0xFFFF)  # uint16_t
            takeoff_land_order = int(gcs_order & 0xFF)  # uint8_t

            trace_command(
                "nest_command_encoded",
                commands=[cmd.name for cmd in commands],
                ordinary_order=f"0x{ordinary_order:04X}",
                takeoff_land_order=f"0x{takeoff_land_order:02X}",
            )
            self.send_gcs_order_response(ordinary_order, takeoff_land_order)

            self.log(
                f"📤 已发送命令: {[cmd.name for cmd in commands]}, "
                f"ordinary=0x{ordinary_order:04X}, "
                f"takeoff_land=0x{takeoff_land_order:02X}"
            )

            # 上报命令发送事件
            self._report_operation_event('started', 'gcs_command',
                                         {'commands': [cmd.name for cmd in commands],
                                          'ordinary_order': ordinary_order,
                                          'takeoff_land_order': takeoff_land_order})

        except Exception as e:
            self.log(f"发送命令失败: {e}", "ERROR")
            self._report_operation_event('failed', 'gcs_command',
                                         {'commands': [cmd.name for cmd in commands],
                                          'error': str(e)})
            import traceback
            traceback.print_exc()

    def send_gcs_order_response(
        self,
        ordinary_order: int,
        takeoff_land_order: int,
        verbose: bool = True,
    ):
        """
        向机巢发送指令

        Args:
            ordinary_order: 普通指令值 (uint16_t)
            takeoff_land_order: 起降指令值 (uint8_t)
        """
        if not self.dispatcher or not self.dispatcher.connection:
            self.log("⚠️ 机巢连接未就绪，无法发送响应", "WARNING")
            return

        try:
            connection = self.dispatcher.connection

            mav_obj = (
                connection.mav if hasattr(connection, 'mav')
                else connection.master.mav if hasattr(connection, 'master')
                else None
            )

            if not mav_obj:
                self.log("⚠️ 无法找到 MAV 对象", "ERROR")
                return

            # ✅ 使用真实存在的消息：DRONE_NEST_COMMAND
            if hasattr(mav_obj, 'drone_nest_command_send'):
                mav_obj.drone_nest_command_send(
                    ordinary_order=int(ordinary_order),
                    takeoff_land_order=int(takeoff_land_order)
                )
            else:
                cmd_msg = drone_nest.MAVLink_drone_nest_command_message(
                    ordinary_order=int(ordinary_order),
                    takeoff_land_order=int(takeoff_land_order)
                )
                msgbuf = cmd_msg.pack(mav_obj)
                if hasattr(connection, 'master'):
                    connection.master.write(msgbuf)
                elif hasattr(connection, 'write'):
                    connection.write(msgbuf)

            if verbose:
                trace_command(
                    "nest_command_mavlink_sent",
                    ordinary_order=f"0x{int(ordinary_order):04X}",
                    takeoff_land_order=f"0x{int(takeoff_land_order):02X}",
                )
                self.log(
                    f"📤 已发送指令: ordinary=0x{ordinary_order:04X}, "
                    f"takeoff_land=0x{takeoff_land_order:02X}"
                )

        except Exception as e:
            self.log(f"发送指令失败: {e}", "ERROR")
            import traceback
            traceback.print_exc()

    def poll_nest_status(self):
        """定期发送空机巢命令，用于 UDP 握手和状态查询。"""
        self.send_gcs_order_response(0, 0, verbose=False)
    # ⭐ 便捷命令方法
    # ⭐ 便捷命令方法
    def request_takeoff(self):
        """请求起飞"""
        self.send_gcs_command(GCSCommand.TAKEOFF_START)

    def abort_takeoff(self):
        """终止起飞"""
        self.send_gcs_command(GCSCommand.TAKEOFF_ABORT)

    def abort_landing(self):
        """终止降落"""
        self.send_gcs_command(GCSCommand.LANDING_ABORT)

    def manual_open_door(self):
        """手动开门"""
        self.send_gcs_command(GCSCommand.DOOR_OPEN)

    def manual_close_door(self):
        """手动关门"""
        self.send_gcs_command(GCSCommand.DOOR_CLOSE)

    def stop_door(self):
        """门停止"""
        self.send_gcs_command(GCSCommand.DOOR_STOP)

    def start_charging(self):
        """启动充电"""
        self.send_gcs_command(GCSCommand.CHARGE_START)

    def stop_charging(self):
        """停止充电"""
        self.send_gcs_command(GCSCommand.CHARGE_STOP)

    def clamp_tighten(self):
        """夹紧"""
        self.send_gcs_command(GCSCommand.CLAMP_TIGHTEN)

    def clamp_release(self):
        """夹紧释放"""
        self.send_gcs_command(GCSCommand.CLAMP_RELEASE)

    def clamp_stop(self):
        """夹紧停止"""
        self.send_gcs_command(GCSCommand.CLAMP_STOP)

    def emergency_stop(self):
        """急停"""
        self.send_gcs_command(GCSCommand.ESTOP_ACTIVATE)

    def deactivate_estop(self):
        """解除急停"""
        self.send_gcs_command(GCSCommand.ESTOP_DEACTIVATE)

    def initialize_device(self):
        """设备初始化"""
        self.send_gcs_command(GCSCommand.INITIALIZE)

    def fault_recovery(self):
        """故障恢复"""
        self.send_gcs_command(GCSCommand.FAULT_RECOVERY)

    def light_on(self):
        """大灯开"""
        self.send_gcs_command(GCSCommand.LIGHT_ON)

    def light_off(self):
        """大灯关"""
        self.send_gcs_command(GCSCommand.LIGHT_OFF)

    def drone_power_on(self):
        """飞机上电"""
        self.send_gcs_command(GCSCommand.DRONE_POWER_ON)

    def drone_power_off(self):
        """飞机下电"""
        self.send_gcs_command(GCSCommand.DRONE_POWER_OFF)

    # ========== 回调管理 ==========

    def add_environment_callback(self, callback: Callable[[Dict[str, Any]], None]):
        """添加环境数据更新回调"""
        if callback not in self.env_callbacks:
            self.env_callbacks.append(callback)
            self.log(f"添加环境数据回调: {callback.__name__ if hasattr(callback, '__name__') else 'anonymous'}")

    def remove_environment_callback(self, callback: Callable):
        """移除环境数据回调"""
        if callback in self.env_callbacks:
            self.env_callbacks.remove(callback)
            self.log("移除环境数据回调")

    def add_status_callback(self, callback: Callable[[Dict[str, Any]], None]):
        """添加状态数据更新回调"""
        if callback not in self.status_callbacks:
            self.status_callbacks.append(callback)
            self.log(f"添加状态数据回调: {callback.__name__ if hasattr(callback, '__name__') else 'anonymous'}")

    def remove_status_callback(self, callback: Callable):
        """移除状态数据回调"""
        if callback in self.status_callbacks:
            self.status_callbacks.remove(callback)
            self.log("移除状态数据回调")

    def _notify_env_callbacks(self):
        """通知所有环境数据回调函数"""
        for callback in self.env_callbacks:
            try:
                callback(self.environment_data.copy())
            except Exception as e:
                self.log(f"环境数据回调执行错误: {e}", "ERROR")

    def _notify_status_callbacks(self):
        """通知所有状态数据回调函数"""
        for callback in self.status_callbacks:
            try:
                callback(self.uav_nest_status.copy())
            except Exception as e:
                self.log(f"状态数据回调执行错误: {e}", "ERROR")

    # ========== 数据查询接口 ==========

    def get_environment_data(self) -> Dict[str, Any]:
        """获取机巢环境数据（包含解码后的 gcs_order）"""
        return self.environment_data.copy()

    def get_cloud_properties(self) -> Dict[str, Any]:
        """获取最近一次 DRONE_NEST_RESPONSE 转换出的上云属性字段。"""
        return dict(self.environment_data.get("cloud_properties") or {})

    def get_cloud_order_response(self) -> Dict[str, Any]:
        """获取最近一次 DRONE_NEST_ORDER_RESPONSE 转换出的执行结果字段。"""
        return dict(self.last_cloud_order_response or {})

    def get_gcs_order_decoded(self) -> Optional[GCSOrderDecoded]:
        """⭐ 获取解码后的 gcs_order"""
        return self.environment_data.get("gcs_order_decoded")

    def get_uav_nest_status(self) -> Dict[str, Any]:
        """获取无人机-机巢状态"""
        return self.uav_nest_status.copy()

    def get_wind_info(self) -> Dict[str, float]:
        """获取风力信息"""
        return {
            "direction": self.environment_data["wind_direction"],
            "speed": self.environment_data["wind_speed"],
            "valid": self.environment_data["data_valid"]
        }

    def get_weather_summary(self) -> Dict[str, Any]:
        """获取天气概况"""
        return {
            "temperature": self.environment_data["nest_temperature"],
            "pressure": self.environment_data["atmospheric_pressure"],
            "rainfall": self.environment_data["rainfall"],
            "wind_speed": self.environment_data["wind_speed"],
            "wind_direction": self.environment_data["wind_direction"],
            "valid": self.environment_data["data_valid"],
            "last_update": self.environment_data["last_update"]
        }

    def is_weather_suitable_for_flight(self,
                                       max_wind_speed: float = 10.0,
                                       max_rainfall: float = 1.0) -> Dict[str, Any]:
        """判断天气是否适合飞行"""
        if not self.environment_data["data_valid"]:
            return {
                "suitable": False,
                "reason": "数据无效",
                "wind_ok": False,
                "rain_ok": False
            }

        wind_speed = self.environment_data["wind_speed"]
        rainfall = self.environment_data["rainfall"]

        wind_ok = wind_speed <= max_wind_speed
        rain_ok = rainfall <= max_rainfall
        suitable = wind_ok and rain_ok

        reason = []
        if not wind_ok:
            reason.append(f"风速过大({wind_speed:.1f} m/s)")
        if not rain_ok:
            reason.append(f"降雨量过大({rainfall:.1f} mm/h)")

        return {
            "suitable": suitable,
            "reason": ", ".join(reason) if reason else "天气适宜",
            "wind_ok": wind_ok,
            "rain_ok": rain_ok,
            "wind_speed": wind_speed,
            "rainfall": rainfall
        }

    def decode_status_flags(self, status: Optional[int] = None) -> Dict[str, bool]:
        """解码状态标志位"""
        if status is None:
            status = self.uav_nest_status["status"]

        return {
            "door_open": bool(status & 0x0001),
            "charging": bool(status & 0x0002),
            "uav_present": bool(status & 0x0004),
            "emergency": bool(status & 0x0008),
            "maintenance": bool(status & 0x0010),
            "ready": bool(status & 0x0020),
        }

    def get_statistics(self) -> Dict[str, Any]:
        """获取模块统计信息"""
        return {
            "env_message_count": self.env_message_count,
            "status_message_count": self.status_message_count,
            "env_data_valid": self.environment_data["data_valid"],
            "status_data_valid": self.uav_nest_status["data_valid"],
            "env_callbacks": len(self.env_callbacks),
            "status_callbacks": len(self.status_callbacks),
            "flight_control_linked": self.flight_control is not None
        }

    # ========== 空闲任务 ==========

    def idle_task(self, dispatcher: MessageDispatcher):
        """空闲任务：定期检查数据超时状态"""
        current_time = time.time()

        if current_time - self._last_status_poll_time >= self.status_poll_interval:
            self._last_status_poll_time = current_time
            self.poll_nest_status()

        if self.environment_data["data_valid"]:
            if current_time - self.environment_data["last_update"] > self.data_timeout:
                self.environment_data["data_valid"] = False
                self.log("机巢环境数据超时", "WARNING")

        if self.uav_nest_status["data_valid"]:
            if current_time - self.uav_nest_status["last_update"] > self.data_timeout:
                self.uav_nest_status["data_valid"] = False
                self.log("无人机-机巢状态数据超时", "WARNING")

    # ========== 生命周期方法 ==========

    def initialize(self):
        """模块初始化"""
        self.log("初始化机巢遥测模块")

    def cleanup(self):
        """模块清理"""
        self.env_callbacks.clear()
        self.status_callbacks.clear()
        self.log("清理机巢遥测模块")
