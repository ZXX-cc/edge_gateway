"""
DJI Dock 3 remote debugging / command helpers.

Dock3 cmd methods use ``thing/product/{sn}/services`` and reply with
``data.result`` plus ``data.output.status``.  This module keeps the command
status, enum validation, and progress-event shape out of DeviceManager.
"""
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Tuple


TASK_STATUS_CANCELED = "canceled"
TASK_STATUS_FAILED = "failed"
TASK_STATUS_IN_PROGRESS = "in_progress"
TASK_STATUS_OK = "ok"
TASK_STATUS_PAUSED = "paused"
TASK_STATUS_REJECTED = "rejected"
TASK_STATUS_SENT = "sent"
TASK_STATUS_TIMEOUT = "timeout"

TASK_STATUSES = {
    TASK_STATUS_CANCELED,
    TASK_STATUS_FAILED,
    TASK_STATUS_IN_PROGRESS,
    TASK_STATUS_OK,
    TASK_STATUS_PAUSED,
    TASK_STATUS_REJECTED,
    TASK_STATUS_SENT,
    TASK_STATUS_TIMEOUT,
}


@dataclass(frozen=True)
class CommandActionSpec:
    method: str
    field: str
    allowed_values: Iterable[int]
    label: str
    aliases: Tuple[str, ...] = ()

    @property
    def allowed_set(self) -> set:
        return set(self.allowed_values)

    @property
    def accepted_fields(self) -> Tuple[str, ...]:
        return (self.field, *self.aliases)


ACTION_SPECS: Dict[str, CommandActionSpec] = {
    "battery_maintenance_switch": CommandActionSpec(
        method="battery_maintenance_switch",
        field="action",
        allowed_values=(0, 1),
        label="电池保养开关",
    ),
    "air_conditioner_mode_switch": CommandActionSpec(
        method="air_conditioner_mode_switch",
        field="action",
        allowed_values=(0, 1, 2, 3),
        label="空调工作模式",
    ),
    "alarm_state_switch": CommandActionSpec(
        method="alarm_state_switch",
        field="action",
        allowed_values=(0, 1),
        label="声光报警开关",
    ),
    "battery_store_mode_switch": CommandActionSpec(
        method="battery_store_mode_switch",
        field="action",
        allowed_values=(1, 2),
        label="电池运行模式",
    ),
    "sdr_workmode_switch": CommandActionSpec(
        method="sdr_workmode_switch",
        field="link_workmode",
        aliases=("mode", "action"),
        allowed_values=(0, 1),
        label="图传链路工作模式",
    ),
}


PROGRESS_STEP_KEYS: Dict[str, str] = {
    "cover_open": "open_cover",
    "cover_close": "close_cover",
    "cover_force_close": "force_close_cover",
    "charge_open": "start_charge",
    "charge_close": "stop_charge",
    "putter_open": "free_putter",
    "putter_close": "close_putter",
    "drone_open": "turn_on_drone",
    "drone_close": "turn_off_drone",
    "drone_format": "format_drone",
    "device_format": "format_device",
    "device_reboot": "device_reboot",
    "device_reset": "device_reset",
    "format_sd_card": "format_device",
}


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def validate_action_payload(method: str, data: Any) -> Tuple[bool, Optional[int], str, List[str]]:
    """
    Validate official Dock3 enum command payloads.

    Returns ``(ok, action_value, used_field, errors)``.  ``used_field`` is the
    official field unless a legacy alias was accepted for compatibility.
    """
    spec = ACTION_SPECS.get(method)
    if spec is None:
        return True, None, "", []

    if not isinstance(data, dict):
        return False, None, spec.field, [f"{method} data must be an object"]

    used_field = ""
    value = None
    for field in spec.accepted_fields:
        if field in data:
            used_field = field
            value = data.get(field)
            break

    if not used_field:
        return False, None, spec.field, [
            f"{method} missing required field: {spec.field}"
        ]

    if not _is_int(value):
        return False, None, used_field, [
            f"{method}.{used_field} must be int enum"
        ]

    if method == "battery_store_mode_switch" and used_field == "action" and value == 0:
        # Some Dock3 link-test scripts use action=0 to mean "disable storage
        # mode / normal battery mode". The Dock3 property enum remains 1/2, so
        # normalize the command value to 1 while accepting the compatibility
        # payload instead of reporting an invalid command.
        value = 1

    if value not in spec.allowed_set:
        allowed = ",".join(str(item) for item in sorted(spec.allowed_set))
        return False, value, used_field, [
            f"{method}.{used_field} out of range, allowed: {allowed}"
        ]

    return True, value, used_field, []


def command_output(
    status: str = TASK_STATUS_OK,
    percent: Optional[int] = None,
    step_key: Optional[str] = None,
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Build Dock3 ``data`` body fragment containing ``output.status``."""
    output: Dict[str, Any] = {
        "status": status if status in TASK_STATUSES else TASK_STATUS_FAILED,
    }
    if percent is not None or step_key is not None:
        progress: Dict[str, Any] = {}
        if percent is not None:
            progress["percent"] = max(0, min(100, int(percent)))
        if step_key:
            progress["step_key"] = step_key
        output["progress"] = progress

    data = {"output": output}
    if extra:
        data.update(extra)
    return data


def progress_event_data(
    method: str,
    status: str = TASK_STATUS_OK,
    percent: int = 100,
    step_key: Optional[str] = None,
    result: int = 0,
) -> Dict[str, Any]:
    """Build Dock3 task progress event data."""
    data = command_output(
        status=status,
        percent=percent,
        step_key=step_key or PROGRESS_STEP_KEYS.get(method, method),
    )
    data["result"] = result
    return data
