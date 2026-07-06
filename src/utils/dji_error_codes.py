"""
DJI Cloud API error code helpers.

The gateway still has legacy internal result codes in many handlers.  Keep
handlers stable for now and normalize those codes at MQTT reply boundaries.
"""
from copy import deepcopy
from typing import Any, Dict, List, Tuple


SUCCESS = 0

# Official DJI Cloud API device-side codes used by the first Dock3 migration
# pass.  Source: docs/71.error-code.md, mirrored from the DJI Cloud API error
# code page.
DEVICE_STATE_UNSUPPORTED = 314000
DOCK_COMMUNICATION_ERROR = 315000
DOCK_SYSTEM_ERROR = 319999
INVALID_COMMAND_FORMAT = 325001
DEVICE_COMMAND_TIMEOUT = 325004
PARAMETER_SET_FAILED = 327000
DOCK_BUSY = 514116


ERROR_DESCRIPTIONS: Dict[int, str] = {
    SUCCESS: "成功",
    DEVICE_STATE_UNSUPPORTED: "设备当前无法支持该操作，建议检查设备当前工作状态",
    DOCK_COMMUNICATION_ERROR: "机场通信异常，请重启机场后重试",
    DOCK_SYSTEM_ERROR: "机场系统运行异常，请重启机场后重试",
    INVALID_COMMAND_FORMAT: "云端下发命令不符合格式要求，设备无法执行",
    DEVICE_COMMAND_TIMEOUT: "设备端命令请求已超时，请重试",
    PARAMETER_SET_FAILED: "参数设置失败，请稍后重试",
    DOCK_BUSY: "无法执行当前操作，机场正在执行其他控制指令，请稍后重试",
}


_LEGACY_MAP_BY_CONTEXT: Dict[str, Dict[int, int]] = {
    "generic": {
        -1: DOCK_SYSTEM_ERROR,
        -2: DOCK_BUSY,
        -3: INVALID_COMMAND_FORMAT,
    },
    "handler_exception": {
        -1: DOCK_SYSTEM_ERROR,
    },
    "unknown_method": {
        -2: DEVICE_STATE_UNSUPPORTED,
    },
    "invalid_request": {
        -1: INVALID_COMMAND_FORMAT,
        -2: INVALID_COMMAND_FORMAT,
        -3: INVALID_COMMAND_FORMAT,
    },
    "property": {
        -1: PARAMETER_SET_FAILED,
        -2: PARAMETER_SET_FAILED,
        -3: INVALID_COMMAND_FORMAT,
    },
    "timeout": {
        -1: DEVICE_COMMAND_TIMEOUT,
    },
}


def describe_error(code: Any, fallback: str = "") -> str:
    """Return the known Chinese description for a DJI error code."""
    try:
        normalized = int(code)
    except (TypeError, ValueError):
        return fallback
    return ERROR_DESCRIPTIONS.get(normalized, fallback)


def normalize_error_code(code: Any, context: str = "generic") -> Any:
    """
    Convert legacy internal negative result codes to DJI Cloud API error codes.

    Non-negative values are kept as-is because many handlers already return
    official DJI codes for specific business cases.
    """
    try:
        numeric = int(code)
    except (TypeError, ValueError):
        return code

    if numeric >= 0:
        return numeric

    context_map = _LEGACY_MAP_BY_CONTEXT.get(context, {})
    return context_map.get(numeric, _LEGACY_MAP_BY_CONTEXT["generic"].get(numeric, DOCK_SYSTEM_ERROR))


def normalize_reply_error_codes(reply: dict, context: str = "generic") -> Tuple[dict, List[dict]]:
    """
    Normalize every negative ``result`` field in a reply payload.

    Returns a copied reply and a conversion list containing path/original/new
    values.  Keeping this copy-based avoids mutating business handler data
    while the MQTT client is still logging and publishing the response.
    """
    normalized = deepcopy(reply)
    conversions: List[dict] = []

    def walk(value: Any, path: str):
        if isinstance(value, dict):
            for key, item in list(value.items()):
                item_path = f"{path}.{key}" if path else str(key)
                if key == "result":
                    new_code = normalize_error_code(item, context=context)
                    if new_code != item:
                        value[key] = new_code
                        conversions.append({
                            "path": item_path,
                            "original_result": item,
                            "dji_result": new_code,
                            "description": describe_error(new_code),
                        })
                else:
                    walk(item, item_path)
        elif isinstance(value, list):
            for index, item in enumerate(value):
                walk(item, f"{path}[{index}]")

    walk(normalized, "")
    return normalized, conversions
