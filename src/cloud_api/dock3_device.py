"""
DJI Dock 3 device/topology metadata and validation helpers.

The official device management page defines ``update_topo`` as an upstream
status message:

    sys/product/{gateway_sn}/status, method=update_topo

Keep this module focused on the payload contract so DeviceManager, cloud
simulators, and smoke tests share the same schema rules.
"""
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple


GATEWAY_DOMAIN = "3"
GATEWAY_TYPE = 119
GATEWAY_SUB_TYPE = 0
THING_VERSION = "1.1.2"
BX160_MODEL_SUB_TYPES = {
    "01": 0,  # 20x single-light
    "02": 1,  # 20x triple-light
    "03": 2,  # five-lens
    "04": 3,  # multispectral
    "05": 4,  # other
}
GATEWAY_TOPOLOGY_KEYS = {
    (GATEWAY_DOMAIN, GATEWAY_TYPE, GATEWAY_SUB_TYPE),
}
SUB_DEVICE_TOPOLOGY_KEYS = {
    ("0", 60, 0),
    ("0", 91, 0),
}

REQUIRED_TOPO_FIELDS = {
    "domain",
    "type",
    "sub_type",
    "device_secret",
    "nonce",
    "thing_version",
    "sub_devices",
}

REQUIRED_SUB_DEVICE_FIELDS = {
    "sn",
    "domain",
    "type",
    "sub_type",
    "index",
    "device_secret",
    "nonce",
    "thing_version",
}


def _to_text(value: Any, default: str = "") -> str:
    if value is None:
        return default
    return str(value)


def _to_int(value: Any, default: int = 0) -> int:
    if isinstance(value, bool):
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def bx160_model_sub_type(model_code: Any, default: int = GATEWAY_SUB_TYPE) -> int:
    """Map BX160 01-05 model codes to Dock3 update_topo sub_type."""
    if model_code is None:
        return default
    code = str(model_code).strip()
    if code.isdigit():
        code = code.zfill(2)
    return BX160_MODEL_SUB_TYPES.get(code, default)


def build_sub_device_payload(
    sn: str,
    domain: str = "0",
    device_type: int = 0,
    sub_type: int = 0,
    index: str = "A",
    device_secret: str = "",
    nonce: str = "",
    thing_version: str = THING_VERSION,
) -> Dict[str, Any]:
    return {
        "sn": _to_text(sn),
        "domain": _to_text(domain, "0"),
        "type": _to_int(device_type),
        "sub_type": _to_int(sub_type),
        "index": _to_text(index, "A"),
        "device_secret": _to_text(device_secret),
        "nonce": _to_text(nonce),
        "thing_version": _to_text(thing_version, THING_VERSION),
    }


def normalize_sub_device(data: Dict[str, Any]) -> Dict[str, Any]:
    return build_sub_device_payload(
        sn=data.get("sn", ""),
        domain=data.get("domain", "0"),
        device_type=data.get("type", 0),
        sub_type=data.get("sub_type", 0),
        index=data.get("index", "A"),
        device_secret=data.get("device_secret", ""),
        nonce=data.get("nonce", ""),
        thing_version=data.get("thing_version", THING_VERSION),
    )


def build_update_topo_payload(
    domain: str = GATEWAY_DOMAIN,
    device_type: int = GATEWAY_TYPE,
    sub_type: int = GATEWAY_SUB_TYPE,
    device_secret: str = "",
    nonce: str = "",
    thing_version: str = THING_VERSION,
    sub_devices: Iterable[Dict[str, Any]] = (),
) -> Dict[str, Any]:
    return {
        "domain": _to_text(domain, GATEWAY_DOMAIN),
        "type": _to_int(device_type, GATEWAY_TYPE),
        "sub_type": _to_int(sub_type, GATEWAY_SUB_TYPE),
        "device_secret": _to_text(device_secret),
        "nonce": _to_text(nonce),
        "thing_version": _to_text(thing_version, THING_VERSION),
        "sub_devices": [normalize_sub_device(device) for device in sub_devices],
    }


def validate_update_topo_payload(
    data: Any,
    strict: bool = False,
    gateway_topology_keys: Optional[Set[Tuple[str, int, int]]] = None,
    sub_device_topology_keys: Optional[Set[Tuple[str, int, int]]] = None,
) -> Tuple[bool, List[str]]:
    errors: List[str] = []
    if not isinstance(data, dict):
        return False, ["update_topo data must be an object"]

    missing = sorted(REQUIRED_TOPO_FIELDS - set(data.keys()))
    if missing:
        errors.append(f"missing update_topo keys: {', '.join(missing)}")

    if not isinstance(data.get("domain"), str):
        errors.append("domain must be string")
    if isinstance(data.get("type"), bool) or not isinstance(data.get("type"), int):
        errors.append("type must be int")
    if isinstance(data.get("sub_type"), bool) or not isinstance(data.get("sub_type"), int):
        errors.append("sub_type must be int")
    if not isinstance(data.get("device_secret"), str):
        errors.append("device_secret must be string")
    elif strict and not data.get("device_secret"):
        errors.append("device_secret must not be empty")
    if not isinstance(data.get("nonce"), str):
        errors.append("nonce must be string")
    elif strict and not data.get("nonce"):
        errors.append("nonce must not be empty")
    if not isinstance(data.get("thing_version"), str):
        errors.append("thing_version must be string")
    elif strict and data.get("thing_version") != THING_VERSION:
        errors.append(f"thing_version must be {THING_VERSION}")

    if strict and isinstance(data.get("domain"), str) and isinstance(data.get("type"), int) and not isinstance(data.get("type"), bool) and isinstance(data.get("sub_type"), int) and not isinstance(data.get("sub_type"), bool):
        gateway_key = (data.get("domain"), data.get("type"), data.get("sub_type"))
        allowed_gateway_keys = gateway_topology_keys or GATEWAY_TOPOLOGY_KEYS
        if gateway_key not in allowed_gateway_keys:
            errors.append(f"unsupported gateway topology: domain={gateway_key[0]}, type={gateway_key[1]}, sub_type={gateway_key[2]}")

    sub_devices = data.get("sub_devices")
    if not isinstance(sub_devices, list):
        errors.append("sub_devices must be array")
        return False, errors

    for index, device in enumerate(sub_devices):
        if not isinstance(device, dict):
            errors.append(f"sub_devices[{index}] must be object")
            continue

        sub_missing = sorted(REQUIRED_SUB_DEVICE_FIELDS - set(device.keys()))
        if sub_missing:
            errors.append(f"sub_devices[{index}] missing keys: {', '.join(sub_missing)}")
        if not device.get("sn"):
            errors.append(f"sub_devices[{index}].sn must not be empty")
        if not isinstance(device.get("domain"), str):
            errors.append(f"sub_devices[{index}].domain must be string")
        if isinstance(device.get("type"), bool) or not isinstance(device.get("type"), int):
            errors.append(f"sub_devices[{index}].type must be int")
        if isinstance(device.get("sub_type"), bool) or not isinstance(device.get("sub_type"), int):
            errors.append(f"sub_devices[{index}].sub_type must be int")
        if not isinstance(device.get("index"), str):
            errors.append(f"sub_devices[{index}].index must be string")
        if not isinstance(device.get("device_secret"), str):
            errors.append(f"sub_devices[{index}].device_secret must be string")
        elif strict and not device.get("device_secret"):
            errors.append(f"sub_devices[{index}].device_secret must not be empty")
        if not isinstance(device.get("nonce"), str):
            errors.append(f"sub_devices[{index}].nonce must be string")
        elif strict and not device.get("nonce"):
            errors.append(f"sub_devices[{index}].nonce must not be empty")
        if not isinstance(device.get("thing_version"), str):
            errors.append(f"sub_devices[{index}].thing_version must be string")
        elif strict and device.get("thing_version") != THING_VERSION:
            errors.append(f"sub_devices[{index}].thing_version must be {THING_VERSION}")
        if strict and isinstance(device.get("domain"), str) and isinstance(device.get("type"), int) and not isinstance(device.get("type"), bool) and isinstance(device.get("sub_type"), int) and not isinstance(device.get("sub_type"), bool):
            sub_key = (device.get("domain"), device.get("type"), device.get("sub_type"))
            allowed_sub_device_keys = sub_device_topology_keys or SUB_DEVICE_TOPOLOGY_KEYS
            if sub_key not in allowed_sub_device_keys:
                errors.append(
                    f"sub_devices[{index}] unsupported topology: "
                    f"domain={sub_key[0]}, type={sub_key[1]}, sub_type={sub_key[2]}"
                )

    return not errors, errors
