"""Helpers for applying command-line runtime metadata to configuration."""
from __future__ import annotations

import json


def build_runtime_overrides(args) -> dict:
    overrides = {}

    if getattr(args, "runtime_mode", None):
        overrides.setdefault("runtime", {})["mode"] = args.runtime_mode
    if getattr(args, "gateway_sn", None):
        overrides.setdefault("gateway", {})["sn"] = args.gateway_sn
    if getattr(args, "gateway_device_secret", None):
        overrides.setdefault("device", {}).setdefault("topology", {})["device_secret"] = args.gateway_device_secret
    if getattr(args, "gateway_nonce", None):
        overrides.setdefault("device", {}).setdefault("topology", {})["nonce"] = args.gateway_nonce

    sub_devices = []
    for raw in getattr(args, "sub_device_json", []) or []:
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise SystemExit(f"Invalid --sub-device-json: {exc}") from exc
        if not isinstance(data, dict):
            raise SystemExit("--sub-device-json must be a JSON object")
        sub_devices.append(data)
    if sub_devices:
        overrides.setdefault("device", {})["sub_devices"] = sub_devices

    return overrides
