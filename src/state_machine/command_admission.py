"""Command admission rules for platform-to-gateway commands."""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Iterable, Optional

from src.state_machine.global_state import GatewayStateSnapshot


GLOBAL_ADMISSION_REJECTED = 325100
GLOBAL_STATE_CONFLICT = 325101
GLOBAL_INVALID_TRANSITION = 325102
GLOBAL_SAFETY_BLOCKED = 325103


@dataclass(frozen=True)
class CommandAdmissionDecision:
    accepted: bool
    reason: str = ""
    message: str = ""
    result: int = 0
    required_state: list[str] = field(default_factory=list)
    snapshot: Optional[GatewayStateSnapshot] = None

    def build_reply(self, request: dict, gateway_sn: str) -> dict:
        current_state = self.snapshot.to_dict(compact=True) if self.snapshot else {}
        return {
            "bid": request.get("bid") or str(uuid.uuid4()),
            "data": {
                "output": {
                    "status": "rejected",
                    "reason": self.reason,
                    "message": self.message,
                    "required_state": list(self.required_state),
                    "current_state": current_state,
                },
                "result": self.result or GLOBAL_ADMISSION_REJECTED,
            },
            "gateway": request.get("gateway", gateway_sn),
            "tid": request.get("tid", ""),
            "timestamp": int(time.time() * 1000),
            "method": request.get("method", ""),
            "msg": self.message,
        }


class CommandAdmissionEngine:
    """Central command admission engine.

    The engine only decides whether a command can enter the existing business
    handler.  It does not execute MAVLink, dock, DRC, media, or OTA actions.
    """

    QUERY_METHODS = frozenset({
        "property_get",
        "gateway_state_query",
        "hms_get",
        "dock_status_query",
        "dock_alarm_query",
        "dock_weather_query",
        "device_log_list_get",
        "device_capability_query",
        "workspace_bind_status",
        "workspace_get",
        "airport_bind_status",
        "config_get",
        "wayline_task_query",
        "wayline_mission_query",
        "flighttask_progress",
        "flighttask_progress_get",
        "flighttask_resource_get",
        "job_query",
        "file_list_get",
        "storage_config_get",
        "live_capability_query",
        "payload_status_query",
        "payload_capability_query",
        "gimbal_status_query",
        "gimbal_capability_query",
        "camera_capability_query",
        "parachute_status_query",
        "radar_status_query",
        "searchlight_status_query",
        "speaker_status_query",
    })

    BINDING_METHODS = frozenset({
        "device_binding",
        "device_unbinding",
        "update_topo",
        "airport_bind_status",
        "workspace_bind_status",
        "workspace_get",
    })

    SAFETY_METHODS = frozenset({
        "emergency_stop",
        "drone_emergency_stop",
        "dock_emergency_stop",
        "dock_reset",
        "hms_clear",
        "flighttask_undo",
        "flighttask_stop",
        "flighttask_pause",
        "cancel_task",
        "wayline_task_cancel",
        "in_flight_wayline_cancel",
        "in_flight_wayline_stop",
        "drc_mode_exit",
        "fault_recover",
        "dock_control",
    })

    SAFETY_RECOVERY_METHODS = frozenset({
        "emergency_stop",
        "drone_emergency_stop",
        "dock_emergency_stop",
        "dock_reset",
        "hms_clear",
        "flighttask_undo",
        "flighttask_stop",
        "cancel_task",
        "wayline_task_cancel",
        "in_flight_wayline_cancel",
        "in_flight_wayline_stop",
        "drc_mode_exit",
        "fault_recover",
    })

    MISSION_START_METHODS = frozenset({
        "flighttask_prepare",
        "flighttask_execute",
        "flighttask_create",
        "wayline_task_create",
        "wayline_task_execute",
        "wayline_mission_upload",
        "load_wayline",
        "upload_wayline",
        "execute_task",
        "take_off",
        "one_key_takeoff_flow",
        "return_specific_home",
        "in_flight_wayline_deliver",
        "in_flight_wayline_recover",
    })
    MISSION_PREPARE_METHODS = frozenset({
        "flighttask_prepare",
        "flighttask_create",
        "wayline_task_create",
        "wayline_mission_upload",
        "load_wayline",
        "upload_wayline",
        "in_flight_wayline_deliver",
        "in_flight_wayline_recover",
    })

    MISSION_CONTROL_METHODS = frozenset({
        "flighttask_pause",
        "flighttask_resume",
        "resume_wayline_task",
        "flighttask_undo",
        "flighttask_stop",
        "flighttask_return",
        "return_home",
        "go_home",
        "one_key_return_home",
        "return_home_cancel",
        "cancel_return_home",
        "pause_wayline",
        "resume_wayline",
        "cancel_task",
        "wayline_task_pause",
        "wayline_task_resume",
        "wayline_task_cancel",
        "flight_setup_abort",
    })

    DRC_ENTER_METHODS = frozenset({"drc_mode_enter"})
    DRC_EXIT_METHODS = frozenset({"drc_mode_exit"})
    DRC_HEARTBEAT_METHODS = frozenset({"heart_beat", "drc_heartbeat"})
    DRC_POINT_FLIGHT_METHODS = frozenset({
        "start_takeoff_to_point",
        "takeoff_to_point",
        "flyto",
        "fly_to_point",
        "fly_to_point_update",
        "fly_to_point_stop",
    })
    DRC_CONTROL_METHODS = frozenset({
        "drc_speed_push",
        "drc_heading_push",
        "drc_altitude_push",
        "drc_flyto_point_push",
        "stick_control",
        "drone_control",
        "payload_control",
        "flight_authority_grab",
        "drc_gimbal_speed_push",
        "camera_screen_drag",
        "camera_aim",
        "camera_screen_split",
        "poi_mode_enter",
        "poi_mode_exit",
        "poi_circle_speed_set",
    }) | DRC_POINT_FLIGHT_METHODS

    OTA_METHODS = frozenset({
        "ota_create",
        "firmware_upgrade",
        "config_update",
        "config_rollback",
        "device_reboot",
        "format_sd_card",
        "device_format",
        "device_reset",
    })
    OTA_CANCEL_METHODS = frozenset({"ota_cancel", "firmware_upgrade_cancel"})

    DOCK_CONTROL_METHODS = frozenset({
        "cover_open",
        "cover_close",
        "cover_force_close",
        "charge_open",
        "charge_close",
        "putter_open",
        "putter_close",
        "dock_control",
        "drone_open",
        "drone_close",
        "supplement_light_open",
        "supplement_light_close",
        "alarm_state_switch",
        "battery_store_mode_switch",
        "battery_maintenance_switch",
        "air_conditioner_mode_switch",
    })

    OTA_BUSY_STATES = frozenset({"checking", "downloading", "verifying", "upgrading"})
    MISSION_BUSY_STATES = frozenset({"preparing", "running", "paused"})

    def __init__(self, reducer):
        self.reducer = reducer

    def check(self, method: str, request: Optional[dict] = None, source_topic: str = "") -> CommandAdmissionDecision:
        request = request or {}
        snapshot = self.reducer.snapshot()
        method = str(method or request.get("method") or "")

        if method in self.QUERY_METHODS:
            return self._accept(snapshot)

        if snapshot.safety == "emergency_stop" or snapshot.dock == "emergency_stop":
            if self._is_safety_recovery_action(method, request):
                return self._accept(snapshot)
            return self._reject(
                snapshot,
                "emergency_stop_active",
                "急停状态下仅允许查询、告警清除、故障恢复、任务取消或退出 DRC",
                ["safety.normal", "dock.not_emergency_stop"],
                GLOBAL_SAFETY_BLOCKED,
            )

        if snapshot.safety in {"blocking_hms_alarm", "low_battery"}:
            if (
                method in self.MISSION_START_METHODS
                or method in self.DRC_ENTER_METHODS
                or method in self.DRC_POINT_FLIGHT_METHODS
            ):
                return self._reject(
                    snapshot,
                    "blocking_hms_alarm",
                    "存在安全阻断告警，当前命令不允许执行",
                    ["safety.normal"],
                    GLOBAL_SAFETY_BLOCKED,
                )

        if snapshot.ota in self.OTA_BUSY_STATES and method not in self.OTA_CANCEL_METHODS:
            if (
                method in self.MISSION_START_METHODS
                or method in self.DRC_ENTER_METHODS
                or method in self.DRC_POINT_FLIGHT_METHODS
                or method in self.DOCK_CONTROL_METHODS
            ):
                return self._reject(
                    snapshot,
                    "ota_in_progress",
                    "OTA/配置升级进行中，禁止执行飞行、DRC 或机巢冲突命令",
                    ["ota.idle"],
                    GLOBAL_STATE_CONFLICT,
                )

        if snapshot.device == "unbound" and method not in self.BINDING_METHODS and method != "property_set":
            return self._reject(
                snapshot,
                "device_unbound",
                "设备未绑定，当前命令不能进入业务执行",
                ["device.bound", "device.online"],
                GLOBAL_ADMISSION_REJECTED,
            )

        if method in self.OTA_METHODS:
            if snapshot.mission in {"preparing", "running", "paused"}:
                return self._reject(snapshot, "mission_busy", "航线任务占用中，不能执行 OTA/配置类命令", ["mission.idle"], GLOBAL_STATE_CONFLICT)
            if snapshot.drc == "active":
                return self._reject(snapshot, "drc_active", "DRC 控制权占用中，不能执行 OTA/配置类命令", ["drc.idle"], GLOBAL_STATE_CONFLICT)

        if method in self.MISSION_START_METHODS:
            return self._check_mission_start(method, request, snapshot)

        if method in self.MISSION_CONTROL_METHODS:
            return self._check_mission_control(method, snapshot)

        if method in self.DRC_ENTER_METHODS:
            return self._check_drc_enter(snapshot)

        if method in self.DRC_POINT_FLIGHT_METHODS:
            if snapshot.drc == "active":
                return self._accept(snapshot)
            if method == "fly_to_point_stop":
                return self._reject(snapshot, "invalid_state_transition", "DRC 未激活，不能停止 DRC 指点飞行", ["drc.active"], GLOBAL_INVALID_TRANSITION)
            if snapshot.aircraft == "offline":
                return self._reject(snapshot, "aircraft_offline", "飞控未在线，不能执行 DRC 指点飞行", ["aircraft.online"], GLOBAL_ADMISSION_REJECTED)
            return self._accept(snapshot)

        if method in self.DRC_CONTROL_METHODS or method in self.DRC_HEARTBEAT_METHODS:
            if snapshot.drc != "active":
                return self._reject(snapshot, "invalid_state_transition", "DRC 未激活，不能执行 DRC 控制命令", ["drc.active"], GLOBAL_INVALID_TRANSITION)

        if method in self.DOCK_CONTROL_METHODS:
            return self._check_dock_control(method, request, snapshot)

        return self._accept(snapshot)

    @staticmethod
    def _request_flight_id(request: dict) -> str:
        data = request.get("data") if isinstance(request, dict) else {}
        if not isinstance(data, dict):
            return ""
        value = data.get("flight_id", data.get("flighttask_id", data.get("task_id", "")))
        return value if isinstance(value, str) else ""

    def _check_mission_start(self, method: str, request: dict, snapshot: GatewayStateSnapshot) -> CommandAdmissionDecision:
        if snapshot.drc == "active":
            return self._reject(snapshot, "drc_active", "DRC 控制权占用中，不能开始新的航线/飞行任务", ["drc.idle"], GLOBAL_STATE_CONFLICT)
        if snapshot.mission in self.MISSION_BUSY_STATES:
            mission_details = snapshot.details.get("mission", {}) if isinstance(snapshot.details, dict) else {}
            active_task_id = mission_details.get("task_id") if isinstance(mission_details, dict) else ""
            requested_flight_id = self._request_flight_id(request)
            if (
                snapshot.mission == "paused"
                and method in {"flighttask_prepare", "flighttask_execute"}
                and requested_flight_id
                and active_task_id
                and requested_flight_id == active_task_id
            ):
                return self._accept(snapshot)
            data = request.get("data") if isinstance(request, dict) else {}
            break_point = data.get("break_point") if isinstance(data, dict) else None
            if (
                snapshot.mission == "paused"
                and method == "flighttask_prepare"
                and requested_flight_id
                and active_task_id
                and requested_flight_id != active_task_id
                and isinstance(break_point, dict)
                and break_point
            ):
                return self._accept(snapshot)
            return self._reject(snapshot, "mission_busy", "当前航线任务正在执行或暂停，不能覆盖新的航线任务", ["mission.idle"], GLOBAL_STATE_CONFLICT)
        if snapshot.ota in self.OTA_BUSY_STATES:
            return self._reject(snapshot, "ota_in_progress", "OTA/配置升级进行中，不能开始航线/飞行任务", ["ota.idle"], GLOBAL_STATE_CONFLICT)
        if snapshot.aircraft == "offline":
            return self._reject(snapshot, "aircraft_offline", "飞控未在线，不能开始航线/飞行任务", ["aircraft.online"], GLOBAL_ADMISSION_REJECTED)
        allowed_dock_states = {"ready", "unknown"}
        if method in self.MISSION_PREPARE_METHODS:
            # ``online`` means the dock status frame and PLC heartbeat are
            # available but the transient takeoff_allowed bit is not asserted.
            # Preparation/upload is still useful in this state; the mission
            # module's self-check reports more precise failures before execute.
            allowed_dock_states.add("online")
        if snapshot.dock not in allowed_dock_states:
            reason = "dock_busy" if snapshot.dock == "busy" else "dock_not_ready"
            return self._reject(
                snapshot,
                reason,
                "机巢未处于可执行飞行任务状态",
                ["dock.ready"],
                GLOBAL_ADMISSION_REJECTED,
            )
        return self._accept(snapshot)

    def _check_mission_control(self, method: str, snapshot: GatewayStateSnapshot) -> CommandAdmissionDecision:
        if method in {"flighttask_resume", "resume_wayline", "resume_wayline_task", "wayline_task_resume"} and snapshot.mission != "paused":
            return self._reject(snapshot, "invalid_state_transition", "当前任务未暂停，不能执行恢复命令", ["mission.paused"], GLOBAL_INVALID_TRANSITION)
        if method in {"flighttask_pause", "pause_wayline", "wayline_task_pause"} and snapshot.mission != "running":
            return self._reject(snapshot, "invalid_state_transition", "当前任务未运行，不能执行暂停命令", ["mission.running"], GLOBAL_INVALID_TRANSITION)
        return self._accept(snapshot)

    def _check_drc_enter(self, snapshot: GatewayStateSnapshot) -> CommandAdmissionDecision:
        if snapshot.drc == "active":
            return self._reject(snapshot, "drc_active", "DRC 已激活，不能重复进入", ["drc.idle"], GLOBAL_STATE_CONFLICT)
        if snapshot.mission == "running":
            return self._reject(snapshot, "mission_busy", "航线任务运行中，不能进入 DRC", ["mission.idle", "mission.paused"], GLOBAL_STATE_CONFLICT)
        if snapshot.ota in self.OTA_BUSY_STATES:
            return self._reject(snapshot, "ota_in_progress", "OTA/配置升级进行中，不能进入 DRC", ["ota.idle"], GLOBAL_STATE_CONFLICT)
        if snapshot.aircraft == "offline":
            return self._reject(snapshot, "aircraft_offline", "飞控未在线，不能进入 DRC", ["aircraft.online"], GLOBAL_ADMISSION_REJECTED)
        return self._accept(snapshot)

    def _check_dock_control(self, method: str, request: dict, snapshot: GatewayStateSnapshot) -> CommandAdmissionDecision:
        if snapshot.mission == "running":
            return self._reject(snapshot, "mission_busy", "航线任务运行中，不能执行冲突的机巢控制", ["mission.idle", "mission.paused"], GLOBAL_STATE_CONFLICT)
        if snapshot.dock == "busy":
            return self._reject(snapshot, "dock_busy", "机巢正在执行动作，不能执行冲突命令", ["dock.ready", "dock.online"], GLOBAL_STATE_CONFLICT)
        if snapshot.dock == "fault" and not self._is_safety_recovery_action(method, request):
            return self._reject(snapshot, "dock_not_ready", "机巢故障未恢复，不能执行普通机巢控制", ["dock.ready"], GLOBAL_SAFETY_BLOCKED)
        return self._accept(snapshot)

    def _is_safety_recovery_action(self, method: str, request: dict) -> bool:
        if method != "dock_control":
            return method in self.SAFETY_RECOVERY_METHODS
        data = request.get("data", {}) if isinstance(request, dict) else {}
        action = str(data.get("action", "")).lower()
        return action in {"fault_recovery", "reset", "estop_off", "emergency_stop_off"}

    @staticmethod
    def _accept(snapshot: GatewayStateSnapshot) -> CommandAdmissionDecision:
        return CommandAdmissionDecision(accepted=True, snapshot=snapshot)

    @staticmethod
    def _reject(
        snapshot: GatewayStateSnapshot,
        reason: str,
        message: str,
        required_state: Iterable[str],
        result: int,
    ) -> CommandAdmissionDecision:
        return CommandAdmissionDecision(
            accepted=False,
            reason=reason,
            message=message,
            result=result,
            required_state=list(required_state),
            snapshot=snapshot,
        )
