"""Payload-to-gateway link topology.

The physical payload links are independent from the flight-controller MAVLink
connection.  This module keeps that boundary explicit and exposes a stable
snapshot for diagnostics and Cloud API status queries.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, Iterable, List, Mapping, Optional

from src.payload.dock3_payload import with_dock3_payload_index


REQUIRED_CHANNELS = {
    "gimbal": {
        "control": {"direction": "bidirectional"},
        "video": {"direction": "payload_to_gateway", "timing": "realtime"},
    },
    "mapping_camera": {
        "video": {"direction": "payload_to_gateway", "timing": "realtime"},
        "post_landing_raw_data": {
            "direction": "payload_to_gateway",
            "timing": "post_landing",
        },
    },
    "lidar": {
        "post_landing_raw_data": {
            "direction": "payload_to_gateway",
            "timing": "post_landing",
        },
    },
    "spectral_camera": {
        "video": {"direction": "payload_to_gateway", "timing": "realtime"},
        "post_landing_raw_data": {
            "direction": "payload_to_gateway",
            "timing": "post_landing",
        },
    },
    "speaker": {
        "control_audio": {
            "direction": "gateway_to_payload",
            "timing": "on_demand",
        },
    },
}


class PayloadTopology:
    """Read-only view of the configured direct payload links."""

    def __init__(self, devices: Optional[Mapping[str, Mapping[str, Any]]] = None):
        self._devices: Dict[str, Dict[str, Any]] = deepcopy(dict(devices or {}))

    def get_device(self, device_id: str) -> Dict[str, Any]:
        return deepcopy(self._devices.get(device_id, {}))

    def iter_devices(self) -> Iterable[tuple[str, Dict[str, Any]]]:
        for device_id, device in self._devices.items():
            yield device_id, deepcopy(device)

    def validate(self) -> List[str]:
        """Return topology errors without attempting any hardware IO."""
        errors: List[str] = []
        for device_id, required_channels in REQUIRED_CHANNELS.items():
            device = self._devices.get(device_id)
            if not isinstance(device, dict):
                errors.append(f"payload.devices.{device_id} is required")
                continue
            if device.get("direct_to_gateway") is not True:
                errors.append(f"payload.devices.{device_id}.direct_to_gateway must be true")
            channels = device.get("channels")
            if not isinstance(channels, dict):
                errors.append(f"payload.devices.{device_id}.channels must be an object")
                continue
            missing = sorted(set(required_channels) - set(channels))
            if missing:
                errors.append(
                    f"payload.devices.{device_id}.channels missing: {', '.join(missing)}"
                )
            for channel_id, expected_values in required_channels.items():
                channel = channels.get(channel_id)
                if not isinstance(channel, dict):
                    continue
                for key, expected in expected_values.items():
                    if channel.get(key) != expected:
                        errors.append(
                            f"payload.devices.{device_id}.channels.{channel_id}.{key} "
                            f"must be {expected}"
                        )
        return errors

    def snapshot(self, connection_states: Optional[Mapping[str, str]] = None) -> List[Dict[str, Any]]:
        """Build a serializable status snapshot without claiming IO is connected."""
        states = dict(connection_states or {})
        payloads: List[Dict[str, Any]] = []
        for device_id, device in self._devices.items():
            enabled = bool(device.get("enabled", True))
            state = states.get(device_id)
            state_payload = state if isinstance(state, dict) else {}
            connection_state = (
                state_payload.get("connection_state")
                if state_payload
                else state
            )
            if device_id == "gimbal" and state_payload:
                state_payload = with_dock3_payload_index(state_payload, device)
            payloads.append({
                "id": device_id,
                "name": device.get("name", device_id),
                "enabled": enabled,
                "direct_to_gateway": bool(device.get("direct_to_gateway", False)),
                "connection_state": connection_state or (
                    "configured" if enabled else "disabled"
                ),
                "channels": deepcopy(device.get("channels", {})),
                **deepcopy({
                    key: value
                    for key, value in state_payload.items()
                    if key not in {"id", "name", "enabled", "direct_to_gateway", "channels"}
                }),
            })
        return payloads
