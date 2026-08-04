#!/usr/bin/env python3
"""Shared configuration helpers for local MQTT validation tools."""

from pathlib import Path
from typing import Any, Dict
from urllib.parse import urlparse

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = "config/gateway.yaml"
DEFAULT_MQTT_HOST = "39.185.72.155"
DEFAULT_MQTT_PORT = 11883
DEFAULT_GATEWAY_SN = "EDGEGW001"


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


def _normalize_broker_address(host, port):
    """Return the plain host/port pair expected by paho-mqtt."""
    host = str(host or "").strip()
    raw_port = port or DEFAULT_MQTT_PORT

    if "://" not in host:
        try:
            return host, int(raw_port)
        except (TypeError, ValueError):
            return host, DEFAULT_MQTT_PORT

    parsed = urlparse(host)
    if parsed.scheme not in ("tcp", "mqtt"):
        raise ValueError(f"unsupported MQTT broker scheme: {parsed.scheme}")
    if not parsed.hostname:
        raise ValueError(f"invalid MQTT broker host: {host}")

    try:
        parsed_port = int(parsed.port or raw_port)
    except (TypeError, ValueError):
        parsed_port = DEFAULT_MQTT_PORT
    return parsed.hostname, parsed_port


def resolve_mqtt_settings(args):
    data = load_config(getattr(args, "config", DEFAULT_CONFIG))
    mqtt_config = _nested(data, "nanomq", default={}) or {}
    cloud_config = _nested(data, "cloud", default={}) or {}
    gateway_config = _nested(data, "gateway", default={}) or {}

    host = (
        getattr(args, "host", None)
        or mqtt_config.get("host")
        or cloud_config.get("broker_host")
        or DEFAULT_MQTT_HOST
    )
    raw_port = (
        getattr(args, "port", None)
        or mqtt_config.get("port")
        or cloud_config.get("broker_port")
        or DEFAULT_MQTT_PORT
    )
    sn = getattr(args, "sn", None) or gateway_config.get("sn") or DEFAULT_GATEWAY_SN

    host, port = _normalize_broker_address(host, raw_port)

    return host, port, str(sn)


def resolve_mqtt_credentials(args):
    data = load_config(getattr(args, "config", DEFAULT_CONFIG))
    mqtt_config = _nested(data, "nanomq", default={}) or {}
    cloud_config = _nested(data, "cloud", default={}) or {}

    username = (
        getattr(args, "username", None)
        or mqtt_config.get("username")
        or cloud_config.get("username")
        or ""
    )
    password = (
        getattr(args, "password", None)
        or mqtt_config.get("password")
        or cloud_config.get("password")
        or ""
    )
    return str(username), str(password)
