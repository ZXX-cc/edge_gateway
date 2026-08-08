"""真实链路运行前检查。

生产模式根据 mqtt.mode 选择 nano/cloud/bridge 三种 MQTT 连接架构。
这里主要防止测试身份或伪造拓扑凭据被当成真实设备数据上报。
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
    mqtt_root = config.get("mqtt") or {}
    mqtt_cfg = config.get("nanomq") or {}
    cloud_cfg = config.get("cloud") or {}
    security = config.get("security") or {}
    mqtt_security = security.get("mqtt") or {}

    gateway_sn = _text(gateway.get("sn"))
    if not gateway_sn:
        errors.append("gateway.sn is required in production mode")
    elif _looks_like_test_identity(gateway_sn):
        errors.append(f"gateway.sn looks like a test identity: {gateway_sn}")

    if not _text(topology.get("device_secret")):
        errors.append("device.topology.device_secret is required in production mode")
    if not _text(topology.get("nonce")):
        errors.append("device.topology.nonce is required in production mode")

    mqtt_mode = _text(mqtt_root.get("mode") or mqtt_cfg.get("mode") or "nano").lower()
    if mqtt_mode not in {"nano", "cloud", "bridge"}:
        errors.append(f"mqtt.mode must be one of nano/cloud/bridge in production mode: {mqtt_mode}")
    if mqtt_mode == "bridge":
        local_cfg = ((mqtt_root.get("bridge") or {}).get("local") or {})
        remote_cfg = ((mqtt_root.get("bridge") or {}).get("remote") or {})
        if not _text(local_cfg.get("host") or mqtt_cfg.get("host")):
            errors.append("mqtt.bridge.local.host is required in production bridge mode")
        if not _text(remote_cfg.get("host") or cloud_cfg.get("broker_host")):
            errors.append("mqtt.bridge.remote.host is required in production bridge mode")
    elif mqtt_mode == "cloud":
        direct_cloud = mqtt_root.get("cloud") or {}
        if not _text(direct_cloud.get("host") or cloud_cfg.get("broker_host")):
            errors.append("mqtt.cloud.host / cloud.broker_host is required in production cloud mode")
    elif not _text((mqtt_root.get("nano") or {}).get("host") or mqtt_cfg.get("host")):
        errors.append("mqtt.nano.host / nanomq.host is required in production nano mode")

    if not bool(mqtt_security.get("downstream_auth_enabled")):
        errors.append("security.mqtt.downstream_auth_enabled must be true in production mode")
    sign_secret = _text(mqtt_security.get("sign_secret"))
    if len(sign_secret) < 16:
        errors.append("security.mqtt.sign_secret must be at least 16 characters in production mode")

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
