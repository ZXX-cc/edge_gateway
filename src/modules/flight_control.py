"""
飞行控制模块 - 参考ModeControl设计（核心功能）
modules/flight_control.py
"""
from typing import Optional, Dict
from pymavlink import mavutil
from pymavlink.mavutil import mavlink
import math
import time
from enum import Enum
from threading import Condition, Event, Lock, Thread
from src.modules.base_module import BaseModule
from src.core.message_dispatcher import MAVLinkMessage, MessageDispatcher
from src.mavlink.flight_modes import (
    flight_mode_profile,
    mode_map_for_profile,
    parse_flight_mode,
)

# ① 枚举新增 ENDED
class AutoTakeoffState(Enum):
    """一键启航状态机"""
    IDLE = 0
    WAITING_NEST_READY = 1
    EXECUTING = 3
    WAITING_MANUAL_CONFIRM = 4
    COMPLETED = 5
    FAILED = 6
    ENDED = 7



class FlightControl(BaseModule):
    """
    飞行控制模块（参考ModeControl设计）

    核心功能：
    - 一键启航（切换AUTO模式）
    - 一键返航（切换RTL模式）
    - 解锁/上锁
    - 模式切换（支持ArduPlane所有模式）
    """

    def __init__(self, target_system: int = 1, target_component: int = 1):
        super().__init__("FlightControl")

        self.target_system = target_system
        self.target_component = target_component
        self.dispatcher: Optional[MessageDispatcher] = None

        # ✅ 当前状态（明确初始化为None）
        self.current_mode = None
        self.current_mode_name = None
        self.is_armed = False  # ⭐ 默认为False
        self.last_command_ack = None
        self._command_acks = {}
        self._command_ack_condition = Condition()
        # PAUSE 和 CONTINUE 共用 MAV_CMD_DO_PAUSE_CONTINUE(193)，ACK 无法区分
        # param1。必须把发送和最终 ACK 等待作为一个事务串行化，避免并发调用
        # 互相消费同一条 ACK。
        self._pause_continue_lock = Lock()
        self._arm_lock = Lock()
        self.mode_profile = "plane"

        # 飞控解锁门禁。ArduPilot 在 EKF/AHRS 尚未就绪时会把
        # MAV_SYS_STATUS_PREARM_CHECK 的 health 位保持为 0；只有该结构化
        # 状态和心跳都新鲜时才允许发送 ARM，避免启动阶段盲目解锁。
        self._armability_condition = Condition()
        self._last_heartbeat_monotonic = 0.0
        self._vehicle_system_status = None
        self._prearm_check_present = False
        self._prearm_check_enabled = False
        self._prearm_check_healthy = False
        self._prearm_status_monotonic = 0.0
        self._last_arm_status_text = None
        self._last_arm_status_text_time = 0.0

        # 任务信息
        self.mission_count = 0
        self.current_waypoint = 0

        self._set_mode_profile("plane")

        # ⭐ 机巢通信
        self.auto_takeoff_state = AutoTakeoffState.IDLE
        self.auto_takeoff_event = Event()
        self.auto_takeoff_error = None
        self.nest_module = None

        # ✅ 记录任务起止时间
        self.auto_takeoff_started_at = None
        self.auto_takeoff_completed_at = None
        self.auto_takeoff_ended_at = None

        # ✅ 模式防抖
        self._last_raw_mode = None
        self._stable_mode_count = 0
        self._STABLE_MODE_THRESHOLD = 3

        self.auto_arm_mode = None
        # ✅ 解锁状态防抖
        self._last_raw_armed = None
        self._stable_armed_count = 0
        self._STABLE_ARMED_THRESHOLD = 3

        # ✅ 新增：连接监控
        self._last_heartbeat_time = 0
        self._connection_monitor_thread = None
        self._connection_monitor_running = False
        self._heartbeat_timeout = 10.0  # 10秒无心跳视为断开

        # ✅ 起飞模式：True=自动，False=手动确认
        self.auto_arm_mode = True

        # ✅ 手动确认事件
        self.manual_confirm_event = Event()
        self.manual_confirm_result = None  # True=允许, False=拒绝

    def _set_mode_profile(self, profile: str):
        if profile not in {"copter", "plane"}:
            return
        self.mode_profile = profile
        mode_by_id = mode_map_for_profile(profile)
        self.mode_id_to_name = dict(mode_by_id)
        self.mode_mapping = {name: mode_id for mode_id, name in mode_by_id.items()}

    def set_nest_module(self, nest_module):
        """注入机巢模块（用于发送响应消息）"""
        self.nest_module = nest_module
        self.log("✅ 已关联机巢模块")

    def on_start(self):
        super().on_start()
        self.log("飞行控制模块已启动（ArduPlane完整模式支持）")

        # ✅ 启动连接监控线程
        self._start_connection_monitor()
    def on_stop(self):
        """模块停止时清理资源"""
        self._stop_connection_monitor()
        super().on_stop()

    def on_message(self, message: MAVLinkMessage):
        msg_type = message.msg_type
        msg = message.msg

        if (
            getattr(message, 'sysid', self.target_system) != self.target_system
            or getattr(message, 'compid', self.target_component) != self.target_component
        ):
            return

        if msg_type == 'HEARTBEAT':
            self._handle_heartbeat(msg)
        elif msg_type == 'COMMAND_ACK':
            self._handle_command_ack(msg)
        elif msg_type == 'SYS_STATUS':
            self._handle_sys_status(msg)
        elif msg_type == 'STATUSTEXT':
            self._handle_statustext(msg)
        elif msg_type == 'MISSION_CURRENT':
            self.current_waypoint = msg.seq
        elif msg_type == 'MISSION_COUNT':
            self.mission_count = msg.count
            self.log(f"航线总数: {self.mission_count}")

    def idle_task(self, dispatcher: MessageDispatcher):
        if self.dispatcher is None:
            self.dispatcher = dispatcher

    def _save_takeoff_location(self):
        # 从遥测模块获取当前位置
        if self.dispatcher:
            for module in self.dispatcher.get_modules():
                if hasattr(module, 'latitude') and hasattr(module, 'longitude'):
                    takeoff_data = {
                        "lng": module.longitude,
                        "lat": module.latitude,
                        "timestamp": time.time()
                    }
                    # 写入数据库或文件
                    self.log(f"保存起飞点: {takeoff_data}")
                    return takeoff_data

        self.log("无法获取起飞点信息", "WARNING")
        return None

    def handle_nest_environment_from_callback(self, env_data: dict):
        """
        ⭐ 关键方法：由 DroneNestModule 调用此方法传递机巢环境数据

        新的二代机巢协议：
        - 检查 takeoff_allowed 状态
        - 当机巢允许起飞且飞机已解锁时进入EXECUTING状态
        """
        # 检查是否在等待机巢准备状态
        if self.auto_takeoff_state == AutoTakeoffState.WAITING_NEST_READY:
            takeoff_allowed = env_data.get("takeoff_allowed", False)

            if takeoff_allowed:
                self.log("🚀 检测到机巢允许起飞信号")

                # 检查飞机是否已解锁
                if self.is_armed:
                    # 根据auto_arm_mode决定下一步
                    if self.auto_arm_mode:
                        # 自动解锁模式：直接进入执行状态
                        self.auto_takeoff_state = AutoTakeoffState.EXECUTING
                        self.log("✅ 飞机已解锁，自动模式直接进入EXECUTING状态")
                        self._execute_auto_takeoff()
                    else:
                        # 手动确认模式：等待前端确认
                        self.auto_takeoff_state = AutoTakeoffState.WAITING_MANUAL_CONFIRM
                        self.log("✅ 飞机已解锁，手动确认模式等待前端确认")
                        self._execute_auto_takeoff()
                        # 不在这里调用_execute_auto_takeoff，等待confirm_manual_takeoff()调用
                else:
                    self.log("⏳ 等待飞机解锁...")
                    # 保持在WAITING_NEST_READY状态，等待解锁后进入EXECUTING

    def _execute_auto_takeoff(self):
        """执行实际的起飞流程"""
        try:
            if self.mission_count == 0:
                raise Exception("航线为空,无法启航")

            # 判断起飞模式
            if self.auto_arm_mode:
                self.log("🔓 自动解锁模式,解锁飞机...")
                self._perform_arm_and_takeoff()
            else:
                self.log("👤 手动确认模式,等待前端确认起飞...")
                self.auto_takeoff_state = AutoTakeoffState.WAITING_MANUAL_CONFIRM
                self.manual_confirm_event.clear()

                if not self.manual_confirm_event.wait(timeout=1000.0):
                    raise Exception("等待前端确认超时(1000秒)")

                if self.manual_confirm_result is False:
                    raise Exception("用户拒绝起飞")

                self.log("✅ 前端已确认,开始解锁...")
                self.auto_takeoff_state = AutoTakeoffState.EXECUTING
                self._perform_arm_and_takeoff()

            # 完成
            self.auto_takeoff_state = AutoTakeoffState.COMPLETED
            self.auto_takeoff_completed_at = time.time()
            self.auto_takeoff_event.set()
            self.log("✅ 一键启航完成!")

        except Exception as e:
            self.log(f"❌ 一键启航失败: {e}", "ERROR")
            self.auto_takeoff_state = AutoTakeoffState.FAILED
            self.auto_takeoff_error = str(e)
            self.auto_takeoff_event.set()

    def _perform_arm_and_takeoff(self):
        """执行解锁和起飞动作"""
        arm_result = self.arm_and_wait(timeout=10.0, readiness_timeout=30.0)
        if not arm_result["success"]:
            raise Exception(f"解锁失败: {arm_result['message']}")

        self.log("✅ 飞机已解锁，开始执行起飞到航线流程")
        takeoff_result = self.one_click_takeoff_auto()
        if not takeoff_result.get("success"):
            raise Exception(f"起飞流程失败: {takeoff_result.get('message')}")

    def confirm_manual_takeoff(self, allow: bool):
        """
        ✅ 手动确认起飞

        Args:
            allow: True=允许起飞, False=拒绝起飞

        Returns:
            dict: 确认结果
        """
        if self.auto_takeoff_state != AutoTakeoffState.WAITING_MANUAL_CONFIRM:
            return {
                "success": False,
                "message": f"当前状态不是等待手动确认 (当前: {self.auto_takeoff_state.name})"
            }

        self.manual_confirm_result = allow
        self.manual_confirm_event.set()  # ✅ 唤醒阻塞的 _execute_auto_takeoff

        if allow:
            self.log("✅ 前端确认允许起飞")
            return {
                "success": True,
                "message": "已确认起飞，飞机将开始解锁"
            }
        else:
            self.log("❌ 前端拒绝起飞，任务取消")
            self.auto_takeoff_state = AutoTakeoffState.FAILED
            self.auto_takeoff_error = "用户拒绝起飞"
            self.auto_takeoff_event.set()
            return {
                "success": True,
                "message": "已拒绝起飞，任务已取消"
            }

    def start_auto_takeoff_flow(self, auto_arm: bool = False):
        """
        ⭐ API调用入口：启动一键启航流程

        Args:
            auto_arm: 是否自动解锁模式
                - False: 原流程（等待机巢信号 -> 确定航线 -> 切换AUTO）
                - True: 新流程（等待机巢信号 -> 确定航线 -> 切换AUTO -> 等待 -> 解锁）

        Returns:
            dict: 启动结果
        """
        # ✅ 修复：允许从 ENDED 状态重新启动任务
        if self.auto_takeoff_state not in [AutoTakeoffState.IDLE,
                                           AutoTakeoffState.COMPLETED,
                                           AutoTakeoffState.FAILED,
                                           AutoTakeoffState.ENDED]:  # ⭐ 添加 ENDED
            return {
                "success": False,
                "message": f"一键启航正在进行中 (状态: {self.auto_takeoff_state.name})"
            }

        # 检查航线
        if self.mission_count == 0:
            return {
                "success": False,
                "message": "航线为空，请先上传航线"
            }

        # 重置状态
        self.auto_takeoff_error = None
        self.auto_takeoff_event.clear()
        self.auto_arm_mode = auto_arm
        self.auto_takeoff_started_at = time.time()
        self.auto_takeoff_completed_at = None
        self.auto_takeoff_ended_at = None  # ⭐ 重置 ended_at

        self.log("🔄 切换到AUTO模式...")
        mode_result = self.set_mode("AUTO")
        if not mode_result["success"]:
            raise Exception(f"AUTO模式切换失败: {mode_result['message']}")

        if self.nest_module:
            self.nest_module.request_takeoff()

        # 启动等待机巢信号
        self.auto_takeoff_state = AutoTakeoffState.WAITING_NEST_READY
        self.log("🎯 一键启航流程已启动，等待机巢信号...")

        return {
            "success": True,
            "message": f"一键启航流程已启动，等待机巢发送准备信号 (gcs_order=3)，模式: {'自动解锁' if auto_arm else '默认'}",
            "state": self.auto_takeoff_state.name,
            "auto_arm": auto_arm
        }

    def wait_for_auto_takeoff_complete(self, timeout: float = 60.0):
        """
        ⭐ 阻塞等待一键启航完成（用于同步API）

        Args:
            timeout: 超时时间（秒）

        Returns:
            dict: 执行结果
        """
        if not self.auto_takeoff_event.wait(timeout):
            self.auto_takeoff_state = AutoTakeoffState.FAILED
            self.auto_takeoff_error = "等待机巢响应超时"
            return {
                "success": False,
                "message": "等待机巢响应超时",
                "state": self.auto_takeoff_state.name
            }

        # 检查结果
        if self.auto_takeoff_state in (AutoTakeoffState.COMPLETED, AutoTakeoffState.ENDED):  # ✅
            return {
                "success": True,
                "message": "一键启航完成" if self.auto_takeoff_state == AutoTakeoffState.COMPLETED else "一键启航已结束",
                "state": self.auto_takeoff_state.name
            }
        else:
            return {
                "success": False,
                "message": self.auto_takeoff_error or "一键启航失败",
                "state": self.auto_takeoff_state.name
            }

    def get_auto_takeoff_status(self):
        """获取当前一键启航状态"""
        return {
            "state": self.auto_takeoff_state.name,
            "error": self.auto_takeoff_error,
            "mission_count": self.mission_count,
            # ✅ 附带任务时间信息（秒级 Unix 时间戳，方便前端做耗时/倒计时）
            "started_at": self.auto_takeoff_started_at,
            "completed_at": self.auto_takeoff_completed_at,
            "ended_at": self.auto_takeoff_ended_at,
        }

    def is_armed_status(self) -> bool:
        """✅ 统一接口：获取解锁状态"""
        return self.is_armed

    def _handle_command_ack(self, msg):
        """处理命令确认"""
        ack = {
            'command': msg.command,
            'result': msg.result,
            'result_text': self._get_ack_result_text(msg.result),
            'timestamp': time.time()
        }
        with self._command_ack_condition:
            self.last_command_ack = ack
            self._command_acks[msg.command] = ack
            self._command_ack_condition.notify_all()
        self.log(f"命令确认: {msg.command} -> {ack['result_text']}")

    def _handle_sys_status(self, msg):
        """缓存 ArduPilot 结构化预解锁检查状态。"""
        prearm_mask = getattr(mavlink, 'MAV_SYS_STATUS_PREARM_CHECK', 1 << 28)
        present = bool(int(getattr(msg, 'onboard_control_sensors_present', 0) or 0) & prearm_mask)
        enabled = bool(int(getattr(msg, 'onboard_control_sensors_enabled', 0) or 0) & prearm_mask)
        healthy = bool(int(getattr(msg, 'onboard_control_sensors_health', 0) or 0) & prearm_mask)
        with self._armability_condition:
            self._prearm_check_present = present
            self._prearm_check_enabled = enabled
            self._prearm_check_healthy = healthy
            self._prearm_status_monotonic = time.monotonic()
            if present and enabled and healthy:
                self._last_arm_status_text = None
                self._last_arm_status_text_time = 0.0
            self._armability_condition.notify_all()

    def _handle_statustext(self, msg):
        """保留最近一次 Arm/PreArm 诊断，供 ACK 失败时返回真实原因。"""
        raw_text = getattr(msg, 'text', '')
        if isinstance(raw_text, bytes):
            text = raw_text.decode('utf-8', errors='replace')
        else:
            text = str(raw_text)
        text = text.rstrip('\x00').strip()
        if not text.startswith(('Arm:', 'PreArm:')):
            return
        with self._armability_condition:
            self._last_arm_status_text = text
            self._last_arm_status_text_time = time.time()
            self._armability_condition.notify_all()

    def _get_ack_result_text(self, result: int) -> str:
        ack_results = {0: "接受", 1: "临时拒绝", 2: "拒绝", 3: "不支持", 4: "失败", 5: "执行中", 6: "已取消"}
        return ack_results.get(result, f"未知({result})")

    # ========== MAVLink通信 ==========

    def _send_message(self, msg) -> bool:
        if self.dispatcher and self.dispatcher.connection:
            self.dispatcher.connection.send_message(msg)
            return True
        self.log("未连接到MAVLink设备", "ERROR")
        return False

    def _send_command_long(self, command: int, param1=0, param2=0, param3=0,
                          param4=0, param5=0, param6=0, param7=0) -> bool:
        msg = mavutil.mavlink.MAVLink_command_long_message(
            self.target_system, self.target_component, command, 0,
            param1, param2, param3, param4, param5, param6, param7
        )
        return self._send_message(msg)

    def _wait_for_command_ack(
        self,
        command: int,
        since: float,
        timeout: float = 2.0,
        strictly_after: bool = False,
    ) -> Optional[Dict]:
        def has_new_ack() -> bool:
            if command not in self._command_acks:
                return False
            timestamp = self._command_acks[command].get('timestamp', 0)
            return timestamp > since if strictly_after else timestamp >= since

        with self._command_ack_condition:
            updated = self._command_ack_condition.wait_for(
                has_new_ack,
                timeout=max(0.0, timeout),
            )
            if not updated:
                return None
            return dict(self._command_acks[command])

    def _send_pause_continue(self, pause: bool) -> Dict:
        with self._pause_continue_lock:
            return self._send_pause_continue_locked(pause)

    def _send_pause_continue_locked(self, pause: bool) -> Dict:
        command = mavutil.mavlink.MAV_CMD_DO_PAUSE_CONTINUE
        action = "暂停" if pause else "继续"
        result = {"success": False, "message": "", "action": action}
        since = time.time()
        if not self._send_command_long(command, param1=0 if pause else 1):
            result["message"] = f"航线{action}命令发送失败"
            self.log(result["message"], "ERROR")
            return result

        ack = self._wait_for_command_ack(command, since)
        if not ack:
            result["message"] = f"航线{action}命令未收到 ACK"
            self.log(result["message"], "WARNING")
            return result

        # MAV_RESULT_IN_PROGRESS 不是最终成功。短时继续等待同一命令更新的
        # 最终 ACK，避免把仍在执行（甚至随后失败）的命令提前上报为成功。
        if ack.get("result") == 5:
            final_ack_deadline = time.monotonic() + 1.0
            last_ack_timestamp = ack.get("timestamp", since)
            while ack.get("result") == 5:
                remaining = final_ack_deadline - time.monotonic()
                if remaining <= 0:
                    break
                next_ack = self._wait_for_command_ack(
                    command,
                    last_ack_timestamp,
                    timeout=remaining,
                    strictly_after=True,
                )
                if not next_ack:
                    break
                next_timestamp = next_ack.get("timestamp", 0)
                if next_timestamp <= last_ack_timestamp:
                    break
                ack = next_ack
                last_ack_timestamp = next_timestamp

        if ack.get("result") == 5:
            result["ack_result"] = 5
            result["message"] = f"航线{action}命令仍在执行，未收到最终 ACK"
            self.log(result["message"], "WARNING")
            return result

        if ack.get("result") != 0:
            result["ack_result"] = ack.get("result")
            result["message"] = f"航线{action}被飞控拒绝: {ack.get('result_text')}"
            self.log(result["message"], "ERROR")
            return result

        result["success"] = True
        result["ack_result"] = 0
        result["message"] = f"航线{action}命令已接受"
        return result

    def _pause_with_hold_modes(
        self,
        candidates: list[str],
        fallback_reason: str,
        mode_timeout: float = 8.0,
    ) -> Dict:
        """按优先级切入可验证的 hold 模式，提供 Dock3 暂停语义。"""
        attempts = []
        for mode in candidates:
            if mode not in self.mode_mapping:
                continue

            self.log(f"航线暂停采用 hold 模式，尝试切换 {mode}")
            mode_result = self.set_mode(mode, timeout=mode_timeout)
            attempts.append({
                "mode": mode,
                "timeout": mode_timeout,
                "success": bool(mode_result.get("success")),
                "message": mode_result.get("message", ""),
            })
            if mode_result.get("success"):
                result = dict(mode_result)
                result.update({
                    "action": "暂停",
                    "pause_strategy": "hold_mode",
                    "target_mode": mode,
                    "native_pause": False,
                    "fallback_reason": fallback_reason,
                    # 保留旧字段，兼容已有调用方和日志解析。
                    "fallback_from": fallback_reason,
                    "hold_attempts": attempts,
                })
                return result

        attempted_modes = ", ".join(item["mode"] for item in attempts) or "none"
        failure_details = "; ".join(
            f"{item['mode']}: {item['message'] or 'failed'}" for item in attempts
        )
        message = f"航线暂停 hold 模式均失败（尝试: {attempted_modes}）"
        if failure_details:
            message = f"{message}: {failure_details}"
        self.log(message, "ERROR")
        return {
            "success": False,
            "message": message,
            "action": "暂停",
            "pause_strategy": "hold_mode",
            "target_mode": None,
            "native_pause": False,
            "fallback_reason": fallback_reason,
            "fallback_from": fallback_reason,
            "hold_attempts": attempts,
        }

    def pause_mission(self) -> Dict:
        if self.mode_profile == "plane":
            # 当前 ArduPlane/QuadPlane 对 MAV_CMD_DO_PAUSE_CONTINUE 会明确返回
            # UNSUPPORTED。真实 SITL 中 QLOITER 可能无法保高，暂停航线时
            # 改用固定翼 LOITER 作为可由 HEARTBEAT 确认的 hold 模式。
            return self._pause_with_hold_modes(
                ["LOITER"],
                "plane profile 使用受控 hold 模式，不发送原生暂停命令",
            )

        native_result = self._send_pause_continue(pause=True)
        if native_result.get("success"):
            result = dict(native_result)
            result.update({
                "pause_strategy": "native_command",
                "target_mode": None,
                "native_pause": True,
                "fallback_reason": "",
                "fallback_from": "",
            })
            return result

        return self._pause_with_hold_modes(
            ["LOITER", "BRAKE", "POSHOLD"],
            native_result.get("message") or "原生暂停命令失败",
        )

    def resume_mission(self) -> Dict:
        if self.mode_profile == "plane":
            self.log("航线恢复采用 AUTO 模式，不发送原生继续命令")
            mode_result = self.set_mode("AUTO")
            result = dict(mode_result)
            result.update({
                "action": "继续",
                "resume_strategy": "auto_mode",
                "target_mode": "AUTO",
                "native_resume": False,
                "fallback_reason": "plane profile 使用 AUTO 模式恢复，不发送原生继续命令",
                "fallback_from": "plane profile 使用 AUTO 模式恢复，不发送原生继续命令",
            })
            return result

        native_result = self._send_pause_continue(pause=False)
        if native_result.get("success"):
            result = dict(native_result)
            result.update({
                "resume_strategy": "native_command",
                "target_mode": None,
                "native_resume": True,
                "fallback_reason": "",
                "fallback_from": "",
            })
            return result

        self.log("航线继续命令未确认，尝试切换 AUTO 恢复")
        mode_result = self.set_mode("AUTO")
        result = dict(mode_result)
        fallback_reason = native_result.get("message") or "原生继续命令失败"
        result.update({
            "action": "继续",
            "resume_strategy": "auto_mode",
            "target_mode": "AUTO",
            "native_resume": False,
            "fallback_reason": fallback_reason,
            "fallback_from": fallback_reason,
        })
        return result

    # ========== 模式管理（参考ModeControl） ==========

    def set_mode(self, mode: str, timeout: float = 10.0) -> Dict:
        """
        设置飞行模式（支持ArduPlane所有模式）

        Args:
            mode: 模式名称（如 "AUTO", "RTL", "FBWA"）或模式ID（如 "10", "11"）
            timeout: 等待 HEARTBEAT 确认模式切换的最长秒数

        Returns:
            dict: 执行结果
        """
        result = {"success": False, "message": "", "mode": mode}

        try:
            mode_timeout = float(timeout)
        except (TypeError, ValueError):
            mode_timeout = 0.0
        if not math.isfinite(mode_timeout) or mode_timeout <= 0:
            result["message"] = f"无效模式切换超时: {timeout}"
            self.log(result["message"], "ERROR")
            return result

        mode_text = str(mode).strip()

        # 如果是数字，直接作为当前 profile 的 MAVLink custom_mode
        if mode_text.isdigit():
            modenum = int(mode_text)
            mode_name = self.mode_id_to_name.get(modenum, f"MODE_{modenum}")
        else:
            # 转换为大写并查找
            mode_upper = mode_text.upper()
            if mode_upper not in self.mode_mapping:
                result["message"] = f"未知模式: {mode}。可用模式: {', '.join(self.get_available_modes())}"
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
        mode_result = self._wait_for_mode(modenum, timeout=mode_timeout)
        if not mode_result["success"]:
            result["message"] = mode_result["message"]
            return result

        result["success"] = True
        result["message"] = f"成功切换到 {mode_name} 模式"
        result["mode_id"] = modenum
        result["mode_name"] = mode_name
        self.log(result["message"])

        return result

    def _wait_for_mode(self, target_mode: int, timeout: float = 10.0) -> Dict:
        """等待模式切换完成"""
        start_time = time.time()
        last_mode = self.current_mode

        self.log(f"开始等待模式切换: 当前={self.current_mode_name}({self.current_mode}), 目标={self.mode_id_to_name.get(target_mode, target_mode)}({target_mode})")

        while time.time() - start_time < timeout:
            if self.current_mode == target_mode:
                elapsed = round(time.time() - start_time, 2)
                self.log(f"✅ 模式切换成功: {self.mode_id_to_name.get(last_mode, last_mode)} → {self.current_mode_name} (耗时 {elapsed}s)")
                return {
                    "success": True,
                    "message": f"模式切换成功（耗时 {elapsed} 秒）",
                    "elapsed_time": elapsed
                }

            if self.current_mode != last_mode:
                self.log(f"模式变化: {self.mode_id_to_name.get(last_mode, last_mode)} → {self.current_mode_name}")
                last_mode = self.current_mode

            time.sleep(0.1)

        elapsed = round(timeout, 2)
        self.log(f"❌ 模式切换超时: 当前={self.current_mode_name}, 目标={self.mode_id_to_name.get(target_mode)}")
        return {
            "success": False,
            "message": f"模式切换超时，当前模式: {self.current_mode_name}",
            "elapsed_time": elapsed
        }

    def get_available_modes(self) -> list:
        """获取所有可用模式（按分类排序）"""
        if self.mode_profile == "copter":
            return list(self.mode_mapping.keys())

        basic_modes = ["MANUAL", "CIRCLE", "STABILIZE", "TRAINING", "ACRO",
                      "FBWA", "FBWB", "CRUISE", "AUTOTUNE"]
        auto_modes = ["AUTO", "RTL", "LOITER", "TAKEOFF", "AVOID_ADSB", "GUIDED"]
        quadplane_modes = ["QSTABILIZE", "QHOVER", "QLOITER", "QLAND", "QRTL",
                          "QAUTOTUNE", "QACRO"]
        special_modes = ["THERMAL", "LOITER_ALT_QLAND"]

        return basic_modes + auto_modes + quadplane_modes + special_modes

    def _first_available_mode(self, candidates: list[str]) -> Optional[str]:
        """Return the first mode supported by the active vehicle profile."""
        for mode in candidates:
            if mode in self.mode_mapping:
                return mode
        return None

    def _takeoff_mode_candidates(self) -> list[str]:
        """Return safe mode priority for NAV_TAKEOFF on the active profile."""
        # User-issued MAV_CMD_NAV_TAKEOFF is accepted by the SITL/ArduPilot
        # stacks we validate against only from GUIDED.  TAKEOFF remains a
        # fallback for profiles that expose it but do not support GUIDED.
        return ["GUIDED", "TAKEOFF"]

    def _ensure_armed_for_takeoff(self) -> Dict:
        """Ensure the vehicle is armed before sending NAV_TAKEOFF."""
        if self.is_armed_status():
            return {"success": True, "message": "飞机已解锁", "already_armed": True}

        arm_and_wait = getattr(self, "arm_and_wait", None)
        if not callable(arm_and_wait):
            return {"success": False, "message": "FlightControl safe arm gate unavailable"}
        return arm_and_wait(timeout=10.0, readiness_timeout=30.0)

    def get_current_mode(self) -> Dict:
        """✅ 获取当前模式（统一接口）"""
        return {
            "mode_id": self.current_mode,
            "mode_name": self.current_mode_name or "UNKNOWN"
        }

    # ========== 航线管理 ==========

    def _request_mission_count(self):
        """请求航线总数"""
        msg = mavutil.mavlink.MAVLink_mission_request_list_message(
            self.target_system, self.target_component, mavlink.MAV_MISSION_TYPE_MISSION
        )
        self._send_message(msg)

    # ========== 🚀 一键启航/返航 ==========

    def one_click_takeoff_auto(self) -> Dict:
        """
        一键启航（先TAKEOFF再AUTO）

        执行步骤：
        1. 检查航线
        2. 获取第一个航点高度
        3. 切换到TAKEOFF模式
        4. 起飞到第一个航点高度
        5. 切换到AUTO模式执行航线任务
        """
        self.log(f"🚀 执行一键启航 (TAKEOFF→AUTO, sysid={self.target_system})")

        result = {"success": False, "steps": [], "message": "", "target_system": self.target_system}

        # 步骤1: 检查航线
        self.log("步骤1: 检查航线...")
        self._request_mission_count()

        wait_start = time.time()
        while time.time() - wait_start < 1.0:
            if self.mission_count > 0:
                break
            time.sleep(0.1)

        if self.mission_count == 0:
            result["message"] = "航线为空，无法启航。请先上传航线。"
            result["steps"].append({"step": "check_mission", "status": "failed", "reason": "no_mission"})
            self.log(result["message"], "ERROR")
            return result

        self.log(f"检测到 {self.mission_count} 个航点")
        result["steps"].append({"step": "check_mission", "status": "completed", "mission_count": self.mission_count})

        # 步骤2: 获取第一个航点高度（从航线获取）
        self.log("步骤2: 获取起飞高度...")
        takeoff_altitude = self._get_first_waypoint_altitude()

        result["steps"].append({
            "step": "get_takeoff_altitude",
            "status": "completed",
            "altitude": takeoff_altitude
        })

        # 步骤3: 切换到起飞可用模式。当前真实 SITL 验收中，用户
        # NAV_TAKEOFF 必须在 GUIDED 下执行，TAKEOFF 只作为兜底模式。
        takeoff_mode = self._first_available_mode(self._takeoff_mode_candidates())
        if not takeoff_mode:
            result["message"] = f"当前机型不支持起飞模式，profile={self.mode_profile}"
            result["steps"].append({"step": "set_takeoff_mode", "status": "failed", "reason": "unsupported_mode"})
            self.log(result["message"], "ERROR")
            return result

        self.log(f"步骤3: 切换到{takeoff_mode}模式...")
        mode_result = self.set_mode(takeoff_mode)

        if not mode_result["success"]:
            result["message"] = f"切换{takeoff_mode}模式失败: {mode_result['message']}"
            result["steps"].append({"step": "set_takeoff_mode", "status": "failed"})
            self.log(result["message"], "ERROR")
            return result

        result["steps"].append({
            "step": "set_takeoff_mode",
            "status": "completed",
            "mode": takeoff_mode,
            "elapsed_time": mode_result.get("elapsed_time", 0)
        })

        self.log("步骤4: 起飞前确认解锁...")
        arm_result = self._ensure_armed_for_takeoff()
        if not arm_result.get("success"):
            result["message"] = f"起飞前解锁失败: {arm_result.get('message')}"
            result["steps"].append({
                "step": "arm_before_takeoff",
                "status": "failed",
                "reason": arm_result.get("message", "arm failed"),
            })
            self.log(result["message"], "ERROR")
            return result
        result["steps"].append({
            "step": "arm_before_takeoff",
            "status": "completed",
            "already_armed": bool(arm_result.get("already_armed")),
        })

        # 步骤4: 发送起飞命令
        self.log(f"步骤5: 起飞到 {takeoff_altitude}m...")
        if not self._send_command_long(
                mavlink.MAV_CMD_NAV_TAKEOFF,
                param7=takeoff_altitude
        ):
            result["message"] = "起飞命令发送失败"
            result["steps"].append({"step": "takeoff", "status": "failed"})
            self.log(result["message"], "ERROR")
            return result

        time.sleep(1.0)  # 等待起飞命令处理

        result["steps"].append({
            "step": "takeoff",
            "status": "completed",
            "altitude": takeoff_altitude
        })

        # 步骤5: 等待一段时间让飞机开始起飞，然后切换到AUTO模式
        self.log("步骤5: 等待起飞稳定后切换到AUTO模式...")
        time.sleep(3.0)  # 等待3秒让飞机开始起飞

        mode_result = self.set_mode("AUTO")

        if not mode_result["success"]:
            result["message"] = f"切换AUTO模式失败: {mode_result['message']}"
            result["steps"].append({"step": "set_auto_mode", "status": "failed"})
            self.log(result["message"], "ERROR")
            return result

        result["steps"].append({
            "step": "set_auto_mode",
            "status": "completed",
            "elapsed_time": mode_result.get("elapsed_time", 0)
        })

        # 成功
        result["success"] = True
        result["message"] = f"一键启航成功！飞机已起飞到{takeoff_altitude}m，将自动执行{self.mission_count}个航点任务"
        self.log(result["message"])

        return result

    def _get_first_waypoint_altitude(self) -> float:
        """
        从航线获取第一个航点的高度

        Returns:
            float: 第一个航点高度（米），如果无法获取则返回默认值50米
        """
        # 如果有航点管理器，尝试从中获取
        if self.dispatcher:
            for module in self.dispatcher.get_modules():
                if hasattr(module, 'waypoints') and len(module.waypoints) > 0:
                    # 获取第一个航点
                    first_waypoint = module.waypoints[0]

                    # 检查是否是TAKEOFF类型（命令ID 22）或普通航点（命令ID 16）
                    if isinstance(first_waypoint, dict):
                        altitude = first_waypoint.get('z', 50.0)
                        self.log(f"从航线获取起飞高度: {altitude}m")
                        return altitude
                    elif hasattr(first_waypoint, 'z'):
                        altitude = getattr(first_waypoint, 'z', 50.0)
                        self.log(f"从航线获取起飞高度: {altitude}m")
                        return altitude

        # 默认返回50米
        self.log("无法从航线获取起飞高度，使用默认值: 50.0m", "WARNING")
        return 50.0

    def one_click_return(self) -> Dict:
        """
        一键返航（智能选择RTL或QRTL）

        逻辑参考Mission Planner：
        - 固定翼模式（RTL/AUTO/FBWA/CRUISE/GUIDED等） → 切换到RTL
        - 垂直起降模式（QRTL/QSTABILIZE/QHOVER/QLOITER等） → 切换到QRTL
        """
        self.log(f"🏠 执行一键返航 (sysid={self.target_system}, 当前模式={self.current_mode_name})")

        result = {
            "success": False,
            "steps": [],
            "message": "",
            "target_system": self.target_system,
            "original_mode": self.current_mode_name
        }

        # 判断当前模式类型，智能选择返航模式
        current_mode_upper = self.current_mode_name.upper() if self.current_mode_name else ""

        # 定义QuadPlane模式（垂直起降）
        quadplane_modes = ["QRTL", "QSTABILIZE", "QHOVER", "QLOITER", "QLAND", "QACRO", "QAUTOTUNE"]

        # 定义固定翼模式
        fixed_wing_modes = ["RTL", "AUTO", "FBWA", "FBWB", "CRUISE", "GUIDED",
                            "LOITER", "CIRCLE", "MANUAL", "STABILIZE", "TRAINING", "ACRO"]

        # 根据当前模式选择返航模式
        if current_mode_upper in quadplane_modes:
            target_mode = "QRTL"
            mode_type = "QuadPlane"
            self.log(f"检测到垂直起降模式 ({current_mode_upper})，切换到QRTL")
        elif current_mode_upper in fixed_wing_modes:
            target_mode = "RTL"
            mode_type = "固定翼"
            self.log(f"检测到固定翼模式 ({current_mode_upper})，切换到RTL")
        else:
            # 未知模式，默认使用RTL
            target_mode = "RTL"
            mode_type = "未知"
            self.log(f"未识别模式 ({current_mode_upper})，默认切换到RTL", "WARNING")

        result["target_mode"] = target_mode
        result["mode_type"] = mode_type

        # 执行模式切换
        self.log(f"切换到{target_mode}模式...")
        mode_result = self.set_mode(target_mode)

        if not mode_result["success"]:
            result["message"] = f"切换{target_mode}模式失败: {mode_result['message']}"
            result["steps"].append({
                "step": f"set_{target_mode.lower()}_mode",
                "status": "failed",
                "reason": mode_result['message']
            })
            self.log(result["message"], "ERROR")
            return result

        result["steps"].append({
            "step": f"set_{target_mode.lower()}_mode",
            "status": "completed",
            "elapsed_time": mode_result.get("elapsed_time", 0)
        })

        result["success"] = True
        result["message"] = f"一键返航成功！已从{current_mode_upper}切换到{target_mode}模式，飞机将返回起飞点"
        self.log(result["message"])

        return result

    # ========== 解锁/上锁 ==========

    def _armability_snapshot(self, max_status_age: float) -> Dict:
        """返回当前预解锁门禁状态；调用方须持有 _armability_condition。"""
        now = time.monotonic()
        heartbeat_age = (
            now - self._last_heartbeat_monotonic
            if self._last_heartbeat_monotonic > 0 else None
        )
        prearm_age = (
            now - self._prearm_status_monotonic
            if self._prearm_status_monotonic > 0 else None
        )
        flight_ready_states = {
            getattr(mavlink, 'MAV_STATE_STANDBY', 3),
            getattr(mavlink, 'MAV_STATE_ACTIVE', 4),
        }

        reason = ""
        if heartbeat_age is None:
            ready = False
            reason = "飞控心跳尚未就绪"
        elif heartbeat_age > max_status_age:
            ready = False
            reason = f"飞控心跳状态已过期 ({heartbeat_age:.1f}s)"
        elif self._vehicle_system_status is None:
            ready = False
            reason = "飞控系统状态尚未就绪"
        elif self._vehicle_system_status not in flight_ready_states:
            ready = False
            reason = f"飞控仍在启动、校准或异常状态 (system_status={self._vehicle_system_status})"
        elif prearm_age is None:
            ready = False
            reason = "飞控预解锁状态尚未就绪"
        elif prearm_age > max_status_age:
            ready = False
            reason = f"飞控预解锁状态已过期 ({prearm_age:.1f}s)"
        elif not self._prearm_check_present or not self._prearm_check_enabled:
            ready = False
            reason = "飞控未启用 MAVLink 预解锁检查状态"
        elif not self._prearm_check_healthy:
            ready = False
            reason = "飞控预解锁检查尚未通过"
            if self._last_arm_status_text:
                reason = f"{reason}: {self._last_arm_status_text}"
        else:
            ready = True

        return {
            'success': ready,
            'message': "飞控预解锁检查已就绪" if ready else reason,
            'heartbeat_age': heartbeat_age,
            'prearm_status_age': prearm_age,
            'system_status': self._vehicle_system_status,
            'prearm_present': self._prearm_check_present,
            'prearm_enabled': self._prearm_check_enabled,
            'prearm_healthy': self._prearm_check_healthy,
        }

    def wait_until_armable(self, timeout: float = 30.0,
                           max_status_age: float = 3.0) -> Dict:
        """等待新鲜 HEARTBEAT 与 SYS_STATUS 共同确认飞控允许解锁。"""
        try:
            timeout = float(timeout)
            max_status_age = float(max_status_age)
        except (TypeError, ValueError):
            return {'success': False, 'message': '预解锁等待参数无效'}
        if not math.isfinite(timeout) or timeout < 0 or timeout > 60:
            return {'success': False, 'message': '预解锁等待时间必须在 0 到 60 秒之间'}
        if not math.isfinite(max_status_age) or not 0.1 <= max_status_age <= 10:
            return {'success': False, 'message': '飞控状态新鲜度必须在 0.1 到 10 秒之间'}
        deadline = time.monotonic() + timeout

        with self._armability_condition:
            while True:
                snapshot = self._armability_snapshot(max_status_age)
                if snapshot['success']:
                    return snapshot

                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    snapshot['message'] = f"等待飞控预解锁就绪超时: {snapshot['message']}"
                    return snapshot
                self._armability_condition.wait(timeout=min(0.25, remaining))

    def _recent_arm_failure_text(self, since: float, timeout: float = 0.25) -> Optional[str]:
        with self._armability_condition:
            self._armability_condition.wait_for(
                lambda: self._last_arm_status_text_time >= since,
                timeout=max(0.0, timeout),
            )
            if self._last_arm_status_text_time >= since:
                return self._last_arm_status_text
            return None

    def arm_and_wait(self, timeout: float = 10.0,
                     readiness_timeout: float = 30.0) -> Dict:
        """安全解锁：先通过预解锁门禁，再按 ARM ACK 与心跳确认结果。"""
        try:
            timeout = float(timeout)
            readiness_timeout = float(readiness_timeout)
        except (TypeError, ValueError):
            return {'success': False, 'stage': 'validation', 'message': '解锁等待参数无效'}
        if not math.isfinite(timeout) or not 1.0 <= timeout <= 30.0:
            return {
                'success': False,
                'stage': 'validation',
                'message': '解锁确认时间必须在 1 到 30 秒之间',
            }
        if (
            not math.isfinite(readiness_timeout)
            or not 0.0 <= readiness_timeout <= 60.0
        ):
            return {
                'success': False,
                'stage': 'validation',
                'message': '预解锁等待时间必须在 0 到 60 秒之间',
            }
        with self._arm_lock:
            return self._arm_and_wait_locked(timeout, readiness_timeout)

    def _arm_and_wait_locked(self, timeout: float,
                             readiness_timeout: float) -> Dict:
        readiness = self.wait_until_armable(timeout=readiness_timeout)
        if not readiness.get('success'):
            return {**readiness, 'stage': 'prearm'}

        if self.is_armed:
            return {
                **readiness,
                'success': True,
                'message': '飞机已解锁且飞控状态新鲜',
                'already_armed': True,
            }

        deadline = time.monotonic() + timeout
        command = mavlink.MAV_CMD_COMPONENT_ARM_DISARM
        since = time.time()
        if not self.arm():
            return {
                'success': False,
                'stage': 'send',
                'message': '解锁命令发送失败',
            }

        ack_timeout = min(2.0, max(0.0, deadline - time.monotonic()))
        ack = self._wait_for_command_ack(command, since, timeout=ack_timeout)
        if ack and ack.get('result') not in (0, 5):
            detail = self._recent_arm_failure_text(since)
            reason = f"解锁被飞控拒绝: {ack.get('result_text', ack.get('result'))}"
            if detail:
                reason = f"{reason} ({detail})"
            return {
                'success': False,
                'stage': 'command_ack',
                'message': reason,
                'ack': ack,
            }

        remaining = max(0.0, deadline - time.monotonic())
        armed_result = self._wait_for_armed(target_state=True, timeout=remaining)
        if armed_result.get('success'):
            return {
                **armed_result,
                'stage': 'armed',
                'ack': ack,
            }

        if ack is None:
            message = f"解锁命令未收到 ACK，且{armed_result.get('message', '未检测到解锁状态')}"
        else:
            message = f"解锁命令已接受，但{armed_result.get('message', '未检测到解锁状态')}"
        return {
            **armed_result,
            'success': False,
            'stage': 'armed',
            'message': message,
            'ack': ack,
        }

    def arm(self) -> bool:
        """解锁飞机"""
        self.log(f"发送解锁命令 (sysid={self.target_system})")
        return self._send_command_long(mavlink.MAV_CMD_COMPONENT_ARM_DISARM, param1=1)

    def disarm(self, force: bool = False) -> bool:
        """上锁飞机"""
        self.log(f"发送上锁命令 (sysid={self.target_system})")
        return self._send_command_long(
            mavlink.MAV_CMD_COMPONENT_ARM_DISARM,
            param1=0,
            param2=21196 if force else 0
        )

    # ========== 🛫 起飞和降落控制 ==========

    def takeoff(self, altitude: float = 50.0) -> Dict:
        """
        起飞到指定高度

        执行步骤：
        1. 切换到TAKEOFF模式
        2. 发送起飞命令

        Args:
            altitude: 起飞高度（米，相对于起飞点，范围5-500米）

        Returns:
            dict: 执行结果
        """
        self.log(f"🛫 执行起飞 (sysid={self.target_system}, alt={altitude}m)")

        result = {
            "success": False,
            "steps": [],
            "message": "",
            "target_system": self.target_system,
            "altitude": altitude
        }

        # 步骤1: 切换到起飞可用模式
        takeoff_mode = self._first_available_mode(self._takeoff_mode_candidates())
        if not takeoff_mode:
            result["message"] = f"当前机型不支持起飞模式，profile={self.mode_profile}"
            result["steps"].append({"step": "set_takeoff_mode", "status": "failed", "reason": "unsupported_mode"})
            self.log(result["message"], "ERROR")
            return result

        self.log(f"步骤1: 切换到{takeoff_mode}模式...")
        mode_result = self.set_mode(takeoff_mode)

        if not mode_result["success"]:
            result["message"] = f"切换{takeoff_mode}模式失败: {mode_result['message']}"
            result["steps"].append({"step": "set_takeoff_mode", "status": "failed"})
            self.log(result["message"], "ERROR")
            return result

        result["steps"].append({
            "step": "set_takeoff_mode",
            "status": "completed",
            "mode": takeoff_mode,
            "elapsed_time": mode_result.get("elapsed_time", 0)
        })

        self.log("步骤2: 起飞前确认解锁...")
        arm_result = self._ensure_armed_for_takeoff()
        if not arm_result.get("success"):
            result["message"] = f"起飞前解锁失败: {arm_result.get('message')}"
            result["steps"].append({
                "step": "arm_before_takeoff",
                "status": "failed",
                "reason": arm_result.get("message", "arm failed"),
            })
            self.log(result["message"], "ERROR")
            return result
        result["steps"].append({
            "step": "arm_before_takeoff",
            "status": "completed",
            "already_armed": bool(arm_result.get("already_armed")),
        })

        # 步骤2: 发送起飞命令
        self.log(f"步骤3: 发送起飞命令（高度 {altitude}m）...")
        if not self._send_command_long(
                mavlink.MAV_CMD_NAV_TAKEOFF,
                param7=altitude
        ):
            result["message"] = "起飞命令发送失败"
            result["steps"].append({"step": "send_takeoff_command", "status": "failed"})
            self.log(result["message"], "ERROR")
            return result

        time.sleep(0.5)

        result["steps"].append({
            "step": "send_takeoff_command",
            "status": "completed",
            "altitude": altitude
        })

        result["success"] = True
        result["message"] = f"起飞成功！已切换到{takeoff_mode}模式，目标高度: {altitude}m"
        self.log(result["message"])

        return result

    def land(self) -> Dict:
        """
        降落到当前位置

        Returns:
            dict: 执行结果
        """
        self.log(f"🛬 执行降落 (sysid={self.target_system})")

        result = {"success": False, "message": "", "target_system": self.target_system}

        # ArduCopter 使用 LAND；QuadPlane/Plane 有 QLAND 时优先使用 QLAND。
        landing_mode = self._first_available_mode(["QLAND", "LAND"])
        if not landing_mode:
            result["message"] = f"当前机型不支持降落模式，profile={self.mode_profile}"
            self.log(result["message"], "ERROR")
            return result

        mode_result = self.set_mode(landing_mode)

        if not mode_result["success"]:
            result["message"] = mode_result["message"]
            self.log(result["message"], "ERROR")
            return result

        result["success"] = True
        result["mode"] = landing_mode
        result["message"] = f"降落命令已发送，飞机将在当前位置降落（{landing_mode}）"
        self.log(result["message"])

        return result

    def _wait_for_armed(self, target_state: bool, timeout: float = 10.0) -> Dict:
        """
        等待解锁/上锁状态

        Args:
            target_state: 目标状态（True=解锁, False=上锁）
            timeout: 超时时间（秒）

        Returns:
            dict: 等待结果
        """
        state_text = "解锁" if target_state else "上锁"
        start_time = time.time()

        self.log(f"开始等待{state_text}...")

        while time.time() - start_time < timeout:
            if self.is_armed == target_state:
                elapsed = round(time.time() - start_time, 2)
                self.log(f"✅ {state_text}成功 (耗时 {elapsed}s)")
                return {
                    "success": True,
                    "message": f"{state_text}成功（耗时 {elapsed} 秒）",
                    "elapsed_time": elapsed
                }
            time.sleep(0.1)

        elapsed = round(timeout, 2)
        self.log(f"❌ {state_text}超时")
        return {
            "success": False,
            "message": f"{state_text}超时（{timeout}秒内未完成）",
            "elapsed_time": elapsed
        }

    # ========== 状态查询 ==========

    def get_status(self) -> Dict:
        """获取飞行控制状态"""
        return {
            "armed": self.is_armed,
            "current_mode_id": self.current_mode,
            "current_mode_name": self.current_mode_name,
            "mission_count": self.mission_count,
            "current_waypoint": self.current_waypoint,
            "available_modes": self.get_available_modes(),
            "last_command_ack": self.last_command_ack,
            "module_enabled": self.enabled,
            "target_system": self.target_system,
            "target_component": self.target_component,
            # ✅ 让 /api/flight/status 也能看到任务状态
            "auto_takeoff_state": self.auto_takeoff_state.name,
            "auto_takeoff_started_at": self.auto_takeoff_started_at,
            "auto_takeoff_completed_at": self.auto_takeoff_completed_at,
            "auto_takeoff_ended_at": self.auto_takeoff_ended_at,
        }

    def _start_connection_monitor(self):
        """启动连接监控线程"""
        if self._connection_monitor_thread is None or not self._connection_monitor_thread.is_alive():
            self._connection_monitor_running = True
            self._connection_monitor_thread = Thread(
                target=self._connection_monitor_loop,
                daemon=True
            )
            self._connection_monitor_thread.start()
            self.log("✅ 连接监控已启动")

    def _stop_connection_monitor(self):
        """停止连接监控线程"""
        self._connection_monitor_running = False
        if self._connection_monitor_thread:
            self._connection_monitor_thread.join(timeout=2.0)
            self.log("🛑 连接监控已停止")

    def _connection_monitor_loop(self):
        """连接监控循环"""
        while self._connection_monitor_running:
            try:
                # 检查心跳超时
                if self._last_heartbeat_time > 0:
                    elapsed = time.time() - self._last_heartbeat_time

                    if elapsed > self._heartbeat_timeout:
                        self.log(f"⚠️ 连接超时 ({elapsed:.1f}秒未收到心跳)", "WARNING")
                        self._handle_connection_lost()
                        break  # 退出监控循环

                time.sleep(2.0)  # 每2秒检查一次

            except Exception as e:
                self.log(f"❌ 连接监控异常: {e}", "ERROR")

    def _handle_connection_lost(self):
        """处理连接丢失"""
        self.log("🔌 检测到连接断开，正在恢复任务状态...", "WARNING")

        # 所有飞行/预解锁缓存都属于上一条连接，断链后不得继续用于安全判断。
        with self._armability_condition:
            self._last_heartbeat_monotonic = 0.0
            self._vehicle_system_status = None
            self._prearm_check_present = False
            self._prearm_check_enabled = False
            self._prearm_check_healthy = False
            self._prearm_status_monotonic = 0.0
            self.is_armed = False
            self.current_mode = None
            self.current_mode_name = None
            self._last_raw_mode = None
            self._stable_mode_count = 0
            self._last_raw_armed = None
            self._stable_armed_count = 0
            self._armability_condition.notify_all()

        try:
            current_state = self.auto_takeoff_state

            self.log(f"📊 连接断开前状态: {current_state.name}")

            # 只有在执行中的任务才需要恢复
            if current_state in [AutoTakeoffState.EXECUTING,
                                 AutoTakeoffState.WAITING_NEST_READY,
                                 AutoTakeoffState.WAITING_MANUAL_CONFIRM]:
                # 重置状态为 IDLE
                self.auto_takeoff_state = AutoTakeoffState.IDLE
                self.auto_takeoff_error = "连接断开，任务已取消"
                self.auto_takeoff_ended_at = time.time()
                self.auto_takeoff_event.set()

                self.log("✅ 任务状态已恢复到 IDLE")

        except Exception as e:
            self.log(f"❌ 处理连接丢失失败: {e}", "ERROR")

    def _handle_heartbeat(self, msg):
        """
        ✅ 处理心跳 - 更新最后心跳时间
        """
        # 更新心跳时间
        now_wall = time.time()
        now_monotonic = time.monotonic()
        self._last_heartbeat_time = now_wall
        system_status = getattr(msg, 'system_status', None)
        flight_ready_states = {
            getattr(mavlink, 'MAV_STATE_STANDBY', 3),
            getattr(mavlink, 'MAV_STATE_ACTIVE', 4),
        }
        reset_states = {
            getattr(mavlink, 'MAV_STATE_UNINIT', 0),
            getattr(mavlink, 'MAV_STATE_BOOT', 1),
            getattr(mavlink, 'MAV_STATE_CALIBRATING', 2),
            getattr(mavlink, 'MAV_STATE_POWEROFF', 7),
        }
        with self._armability_condition:
            self._last_heartbeat_monotonic = now_monotonic
            self._vehicle_system_status = system_status
            if system_status not in flight_ready_states:
                self._prearm_check_present = False
                self._prearm_check_enabled = False
                self._prearm_check_healthy = False
                self._prearm_status_monotonic = 0.0
            if system_status in reset_states:
                self.is_armed = False
                self.current_mode = None
                self.current_mode_name = None
                self._last_raw_mode = None
                self._stable_mode_count = 0
                self._last_raw_armed = None
                self._stable_armed_count = 0
            self._armability_condition.notify_all()

        # ... 保持原有心跳处理逻辑 ...

        raw_mode = msg.custom_mode
        mav_type = getattr(msg, "type", None)
        profile = flight_mode_profile(mav_type)
        if profile != "unknown" and profile != self.mode_profile:
            self._set_mode_profile(profile)
        raw_armed = bool(msg.base_mode & mavlink.MAV_MODE_FLAG_SAFETY_ARMED)

        # ========== 模式防抖 ==========
        if raw_mode == self._last_raw_mode:
            self._stable_mode_count += 1
        else:
            self._stable_mode_count = 0
            self._last_raw_mode = raw_mode

        if self._stable_mode_count >= self._STABLE_MODE_THRESHOLD:
            if raw_mode != self.current_mode:
                old_mode_name = self.current_mode_name or "NONE"
                self.current_mode = raw_mode
                self.current_mode_name = parse_flight_mode(
                    self.current_mode,
                    mav_type=mav_type,
                    profile=self.mode_profile,
                )

                if old_mode_name != "NONE":
                    self.log(f"📡 模式切换: {old_mode_name} → {self.current_mode_name}", "INFO")
                else:
                    self.log(f"📡 初始模式: {self.current_mode_name}", "INFO")

                self._stable_mode_count = 0

        # ========== 解锁状态防抖 ==========-
        if raw_armed == self._last_raw_armed:
            self._stable_armed_count += 1
        else:
            self._stable_armed_count = 0
            self._last_raw_armed = raw_armed

        if self._stable_armed_count >= self._STABLE_ARMED_THRESHOLD:
            if raw_armed != self.is_armed:
                old_armed = self.is_armed
                self.is_armed = raw_armed

                if old_armed is not None:
                    status = "✅ 已解锁" if self.is_armed else "🔒 已上锁"
                    self.log(f"🔄 解锁状态变更: {status}", "INFO")
                else:
                    status = "✅ 已解锁" if self.is_armed else "🔒 已上锁"
                    self.log(f"📡 初始解锁状态: {status}", "INFO")

                self._stable_armed_count = 0

            # ✅ 当已完成(COMPLETED)且检测到"已上锁"→ 标记任务 ENDED
            if (self.auto_takeoff_state == AutoTakeoffState.COMPLETED) and (self.is_armed is False):
                self.auto_takeoff_state = AutoTakeoffState.ENDED
                self.auto_takeoff_ended_at = time.time()
                self.log("🏁 一键启航任务已结束（检测到上锁），状态: ENDED")
