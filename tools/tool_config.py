#!/usr/bin/env python3
"""Shared configuration helpers for local MQTT validation tools."""

from pathlib import Path
from typing import Any, Dict

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = "config/gateway.yaml"
DEFAULT_MQTT_HOST = "127.0.0.1"
DEFAULT_MQTT_PORT = 1883
DEFAULT_GATEWAY_SN = "EDGE-GW-001"


def add_config_argument(parser) -> None:
    parser.add_argument(
        "--config",
        default=DEFAULT_CONFIG,
        help="Gateway YAML config used for default MQTT host/port and gateway SN.",
    )


def _resolve_config_path(config_path: str) -> Path:
    path = Path(config_path)
    if path.is_absolute():
        return path
    if path.exists():
        return path
    return PROJECT_ROOT / path


def load_config(config_path: str = DEFAULT_CONFIG) -> Dict[str, Any]:
    path = _resolve_config_path(config_path)
    try:
        import yaml
    except Exception:
        return {}

    try:
        return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}


def _nested(data: Dict[str, Any], *keys: str, default=None):
    value = data
    for key in keys:
        if not isinstance(value, dict) or key not in value:
            return default
        value = value[key]
    return default if value is None else value


def resolve_mqtt_settings(args):
    data = load_config(getattr(args, "config", DEFAULT_CONFIG))
    mqtt_config = _nested(data, "nanomq", default={}) or {}
    gateway_config = _nested(data, "gateway", default={}) or {}

    host = getattr(args, "host", None) or mqtt_config.get("host") or DEFAULT_MQTT_HOST
    raw_port = getattr(args, "port", None) or mqtt_config.get("port") or DEFAULT_MQTT_PORT
    sn = getattr(args, "sn", None) or gateway_config.get("sn") or DEFAULT_GATEWAY_SN

    try:
        port = int(raw_port)
    except (TypeError, ValueError):
        port = DEFAULT_MQTT_PORT

    return host, port, str(sn)
