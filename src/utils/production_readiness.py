"""Runtime checks for real-chain operation.

Production mode still uses the configured EMQX/MQTT broker. These checks only
prevent test identities or fabricated topology credentials from being reported
as real device data.
"""
from __future__ import annotations

from typing import Any, Dict, List


TEST_SN_MARKERS = (
    "EDGEGW",
    "DRONESIM",
    "DRONE-SIM",
    "SIM",
    "TEST",
    "MOCK",
)


def is_production_mode(config: Dict[str, Any]) -> bool:
    return str((config.get("runtime") or {}).get("mode", "test")).lower() == "production"


def _text(value: Any) -> str:
    return str(value or "").strip()


def _looks_like_test_identity(value: Any) -> bool:
    text = _text(value).upper()
    return bool(text) and any(marker in text for marker in TEST_SN_MARKERS)


def validate_production_config(config: Dict[str, Any]) -> List[str]:
    if not is_production_mode(config):
        return []

    errors: List[str] = []
    gateway = config.get("gateway") or {}
    device = config.get("device") or {}
    topology = device.get("topology") or {}
    mqtt_cfg = config.get("nanomq") or {}

    gateway_sn = _text(gateway.get("sn"))
    if not gateway_sn:
        errors.append("gateway.sn is required in production mode")
    elif _looks_like_test_identity(gateway_sn):
        errors.append(f"gateway.sn looks like a test identity: {gateway_sn}")

    if not _text(topology.get("device_secret")):
        errors.append("device.topology.device_secret is required in production mode")
    if not _text(topology.get("nonce")):
        errors.append("device.topology.nonce is required in production mode")

    if not _text(mqtt_cfg.get("host")):
        errors.append("nanomq.host / EMQX host is required in production mode")

    for index, sub_device in enumerate(device.get("sub_devices") or []):
        sn = _text(sub_device.get("sn"))
        prefix = f"device.sub_devices[{index}]"
        if not sn:
            errors.append(f"{prefix}.sn is required in production mode")
        elif _looks_like_test_identity(sn):
            errors.append(f"{prefix}.sn looks like a test identity: {sn}")
        if not _text(sub_device.get("device_secret")):
            errors.append(f"{prefix}.device_secret is required in production mode")
        if not _text(sub_device.get("nonce")):
            errors.append(f"{prefix}.nonce is required in production mode")

    return errors
