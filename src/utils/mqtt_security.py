"""Local MQTT security validation helpers."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict

from src.utils.config import Config


@dataclass(frozen=True)
class MqttSecurityResult:
    accepted: bool
    reason: str = ""
    message: str = ""


class MqttSecurityValidator:
    """Validate MQTT auth switches without blocking test-mode startup."""

    def validate_downstream(self, topic: str, payload: Dict[str, Any]) -> MqttSecurityResult:
        mqtt_security = Config.get("security.mqtt", {}) or {}
        if not bool(mqtt_security.get("downstream_auth_enabled")):
            return MqttSecurityResult(True)
        return self._reject_missing_platform_signature_rule()

    def validate_reply(self, topic: str, payload: Dict[str, Any]) -> MqttSecurityResult:
        mqtt_security = Config.get("security.mqtt", {}) or {}
        if not bool(mqtt_security.get("reply_auth_enabled")):
            return MqttSecurityResult(True)
        return self._reject_missing_platform_signature_rule()

    @staticmethod
    def _reject_missing_platform_signature_rule() -> MqttSecurityResult:
        return MqttSecurityResult(
            False,
            reason="signature_rule_not_configured",
            message="MQTT auth is enabled, but platform signature verification is not configured",
        )
