"""Event reducer facade for the gateway global state machine."""
from __future__ import annotations

import time
from typing import Any, Dict

from src.state_machine.global_state import GatewayStateProvider, GatewayStateSnapshot


class GatewayEventReducer:
    """Keeps event overrides and delegates live snapshot reads to the provider."""

    def __init__(self, provider: GatewayStateProvider | None = None, **sources: Any):
        self.provider = provider or GatewayStateProvider(**sources)
        self._events: Dict[str, Dict[str, Any]] = {}

    def set_sources(self, **sources: Any) -> None:
        self.provider.set_sources(**sources)

    def reduce_event(self, event_type: str, **fields: Any) -> None:
        self._events[event_type] = {
            "updated_at": time.time(),
            **fields,
        }

    def snapshot(self) -> GatewayStateSnapshot:
        snapshot = self.provider.snapshot()
        if not self._events:
            return snapshot
        details = dict(snapshot.details)
        details["events"] = dict(self._events)
        return GatewayStateSnapshot(
            gateway_phase=snapshot.gateway_phase,
            cloud=snapshot.cloud,
            device=snapshot.device,
            dock=snapshot.dock,
            aircraft=snapshot.aircraft,
            mission=snapshot.mission,
            drc=snapshot.drc,
            ota=snapshot.ota,
            safety=snapshot.safety,
            generated_at=snapshot.generated_at,
            details=details,
        )
