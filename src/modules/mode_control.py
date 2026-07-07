"""
模式管理模块 - QuadPlane混合翼专用（固定模式）
modules/mode_control.py
"""
from typing import Optional, Dict
from pymavlink import mavutil
from pymavlink.mavutil import mavlink
import time

from src.modules.base_module import BaseModule
from src.core.message_dispatcher import MAVLinkMessage, MessageDispatcher
from src.mavlink.flight_modes import (
    flight_mode_profile,
    mode_map_for_profile,
    parse_flight_mode,
)


class ModeControl(BaseModule):
    """
    模式管理模块 - QuadPlane混合翼专用

    功能：
    - 模式切换（支持固定翼+四旋翼所有模式）
    - 获取可用模式列表
    - GUIDED模式位置控制
    """

    def __init__(self, target_system: int = 1, target_component: int = 1):
        super().__init__("ModeControl")

        self.target_system = target_system
        self.target_component = target_component
        self.dispatcher: Optional[MessageDispatcher] = None

        # 当前状态
        self.current_mode = None
        self.current_mode_name = None
        self.last_command_ack = None
        self.vehicle_type = "QUADPLANE"
        self.mode_profile = "plane"

        # ✅ 添加防抖/稳定性检测
        self._last_raw_mode = None  # 上一次读取的原始模式
        self._stable_count = 0      # 稳定计数器
        self._STABLE_THRESHOLD = 3  # 需要连续3次相同才认为稳定

        self._set_mode_profile("plane")

    def _set_mode_profile(self, profile: str):
        if profile not in {"copter", "plane"}:
            return
        self.mode_profile = profile
        mode_by_id = mode_map_for_profile(profile)
        self.mode_id_to_name = dict(mode_by_id)
        self.mode_mapping = {name: mode_id for mode_id, name in mode_by_id.items()}
        self.vehicle_type = profile.upper()

    def on_start(self):
        super().on_start()
        self.log("模式管理模块已启动（QuadPlane混合翼）")

    def on_message(self, message: MAVLinkMessage):
        msg_type = message.msg_type
        msg = message.msg

        if msg_type == 'HEARTBEAT':
            self._handle_heartbeat(msg)
        elif msg_type == 'COMMAND_ACK':
            self._handle_command_ack(msg)

    def idle_task(self, dispatcher: MessageDispatcher):
        if self.dispatcher is None:
            self.dispatcher = dispatcher

    def _handle_heartbeat(self, msg):
        """
        ✅ 处理心跳消息 - 带稳定性检测的防抖逻辑

        逻辑：
        1. 读取原始 custom_mode
        2. 如果与上次读取相同，稳定计数器+1
        3. 如果不同，重置计数器
        4. 只有连续3次相同 且 与当前确认模式不同时，才真正更新
        """
        raw_mode = msg.custom_mode
        mav_type = getattr(msg, "type", None)
        profile = flight_mode_profile(mav_type)
        if profile != "unknown" and profile != self.mode_profile:
            self._set_mode_profile(profile)

        # ✅ 稳定性检测
        if raw_mode == self._last_raw_mode:
            self._stable_count += 1
        else:
            # 检测到不同的值，重置计数器
            self._stable_count = 0
            self._last_raw_mode = raw_mode
            return  # 等待稳定

        # ✅ 只有连续检测到相同值达到阈值才处理
        if self._stable_count >= self._STABLE_THRESHOLD:
            # 检查是否真的变化了
            if raw_mode != self.current_mode:
                old_mode_name = self.current_mode_name or "NONE"

                self.current_mode = raw_mode
                self.current_mode_name = parse_flight_mode(
                    self.current_mode,
                    mav_type=mav_type,
                    profile=self.mode_profile,
                )

                # ✅ 只在真正变化时打印（格式更简洁）
                self.log(
                    f"📡 模式切换: {old_mode_name} → {self.current_mode_name}",
                    "INFO"
                )

                # 重置计数器（准备下次检测）
                self._stable_count = 0

    def _handle_command_ack(self, msg):
        """处理命令确认"""
        self.last_command_ack = {
            'command': msg.command,
            'result': msg.result,
            'result_text': self._get_ack_result_text(msg.result),
            'timestamp': time.time()
        }

    def _get_ack_result_text(self, result: int) -> str:
        ack_results = {
            0: "接受",
            1: "临时拒绝",
            2: "拒绝",
            3: "不支持",
            4: "失败",
            5: "执行中",
            6: "已取消"
        }
        return ack_results.get(result, f"未知({result})")

    # ========== MAVLink通信 ==========

    def _send_message(self, msg):
        if self.dispatcher and self.dispatcher.connection:
            self.dispatcher.connection.send_message(msg)
            return True
        else:
            self.log("未连接到MAVLink设备", "ERROR")
            return False

    def _get_mav_sender(self):
        """Return the pymavlink sender object from supported connection shapes."""
        connection = self.dispatcher.connection if self.dispatcher and self.dispatcher.connection else None
        if not connection:
            return None
        if hasattr(connection, "mav"):
            return connection.mav
        master = getattr(connection, "master", None)
        if master and hasattr(master, "mav"):
            return master.mav
        return None

    # ========== 模式管理 ==========

    def set_mode(self, mode: str) -> dict:
        """
        设置飞行模式

        Args:
            mode: 模式名称（如 "QSTABILIZE", "FBWA"）或模式ID

        Returns:
            dict: 执行结果
        """
        result = {
            "success": False,
            "message": "",
            "mode": mode
        }

        mode_text = str(mode).strip()

        # 如果是数字，直接作为当前 profile 的 MAVLink custom_mode
        if mode_text.isdigit():
            modenum = int(mode_text)
            mode_name = self.mode_id_to_name.get(modenum, f"MODE_{modenum}")
        else:
            # 转换为大写并查找
            mode_upper = mode_text.upper()
            if mode_upper not in self.mode_mapping:
                result["message"] = (
                    f"未知模式: {mode}\n"
                    f"可用模式: {', '.join(self.get_available_modes())}"
                )
                self.log(result["message"], "ERROR")
                return result

            modenum = self.mode_mapping[mode_upper]
            mode_name = mode_upper

        # 发送模式切换命令
        self.log(f"切换模式: {mode_name} (ID={modenum})")

        msg = mavutil.mavlink.MAVLink_set_mode_message(
            self.target_system,
            mavutil.mavlink.MAV_MODE_FLAG_CUSTOM_MODE_ENABLED,
            modenum
        )

        if not self._send_message(msg):
            result["message"] = "模式切换命令发送失败"
            self.log(result["message"], "ERROR")
            return result

        # 等待模式切换完成
        mode_result = self._wait_for_mode(modenum, timeout=5.0)
        if not mode_result["success"]:
            result["message"] = mode_result["message"]
            return result

        result["success"] = True
        result["message"] = f"成功切换到 {mode_name} 模式"
        result["mode_id"] = modenum
        result["mode_name"] = mode_name
        self.log(result["message"])

        return result

    def get_available_modes(self) -> list:
        """获取所有可用模式"""
        return sorted(list(self.mode_mapping.keys()))

    def get_current_mode(self) -> dict:
        """✅ 获取当前模式（统一接口）"""
        return {
            "mode_id": self.current_mode,
            "mode_name": self.current_mode_name or "UNKNOWN",
            "vehicle_type": self.vehicle_type
        }

    def _wait_for_mode(self, target_mode: int, timeout: float = 5.0) -> dict:
        """等待模式切换完成"""
        start_time = time.time()

        while time.time() - start_time < timeout:
            if self.current_mode == target_mode:
                elapsed = round(time.time() - start_time, 2)
                return {
                    "success": True,
                    "message": f"模式切换成功（耗时 {elapsed} 秒）",
                    "elapsed_time": elapsed
                }
            time.sleep(0.1)

        elapsed = round(timeout, 2)
        return {
            "success": False,
            "message": f"模式切换超时（当前模式: {self.current_mode_name}）",
            "elapsed_time": elapsed
        }

    # ========== GUIDED模式位置控制 ==========
    # ... (其他方法保持不变) ...

    def guided_goto(self, lat: float, lon: float, alt: float,
                    frame: int = mavutil.mavlink.MAV_FRAME_GLOBAL_RELATIVE_ALT_INT) -> dict:
        """GUIDED模式飞往指定位置"""
        result = {
            "success": False,
            "message": ""
        }

        self.log(f"GUIDED模式飞往: ({lat:.6f}, {lon:.6f}, {alt}m)")

        mav = self._get_mav_sender()
        if not mav or not hasattr(mav, "mission_item_int_send"):
            result["message"] = "MAVLink连接不可用，无法发送GUIDED目标点"
            self.log(result["message"], "ERROR")
            return result

        mav.mission_item_int_send(
            self.target_system,
            self.target_component,
            0,
            frame,
            mavutil.mavlink.MAV_CMD_NAV_WAYPOINT,
            2,
            0,
            0, 0, 0, 0,
            int(lat * 1.0e7),
            int(lon * 1.0e7),
            alt
        )

        result["success"] = True
        result["message"] = f"GUIDED目标已设置: ({lat:.6f}, {lon:.6f}, {alt}m)"
        self.log(result["message"])

        return result

    def guided_forward(self, distance: float) -> dict:
        """GUIDED模式向前飞行指定距离"""
        result = {
            "success": False,
            "message": ""
        }

        self.log(f"GUIDED模式向前飞行: {distance}m")

        ignore_mask = self._build_pt_ignoremask(["X", "YAWRATE"])

        mav = self._get_mav_sender()
        if not mav or not hasattr(mav, "set_position_target_local_ned_send"):
            result["message"] = "MAVLink连接不可用，无法发送GUIDED位置控制"
            self.log(result["message"], "ERROR")
            return result

        mav.set_position_target_local_ned_send(
            0,
            self.target_system,
            self.target_component,
            mavutil.mavlink.MAV_FRAME_BODY_NED,
            ignore_mask,
            float(distance), 0, 0,
            0, 0, 0,
            0, 0, 0,
            0, 0
        )

        result["success"] = True
        result["message"] = f"GUIDED模式向前飞行命令已发送: {distance}m"
        self.log(result["message"])

        return result

    def _build_pt_ignoremask(self, bits: list) -> int:
        """构建位置目标忽略掩码"""
        ignore_bits = {
            "X": 1, "Y": 2, "Z": 4,
            "VX": 8, "VY": 16, "VZ": 32,
            "AX": 64, "AY": 128, "AZ": 256,
            "YAW": 1024, "YAWRATE": 2048,
        }

        value = 0
        for (n, v) in ignore_bits.items():
            if n not in bits:
                value |= v

        for prefix in "", "V", "A":
            for axis in "X", "Y", "Z":
                name = f"{prefix}{axis}"
                if (value & ignore_bits[name]) == 0:
                    for resetaxis in "X", "Y", "Z":
                        resetname = f"{prefix}{resetaxis}"
                        value = value & ~ignore_bits[resetname]
                    break

        return value

    def get_status(self) -> dict:
        """获取模式管理状态"""
        return {
            "vehicle_type": self.vehicle_type,
            "current_mode_id": self.current_mode,
            "current_mode_name": self.current_mode_name,
            "available_modes": self.get_available_modes(),
            "last_command_ack": self.last_command_ack,
            "module_enabled": self.enabled
        }
