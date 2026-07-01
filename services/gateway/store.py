"""In-memory state stores for the Gateway.

Dev implementation — state lives in process memory.  Single-process demo
only; M9 upgrades to Redis-backed shared state for the 30-user prod deployment.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field

from libs.common.models import (
    ActivityEvent,
    AssetState,
    FaultEvent,
    FaultEventStatus,
    Notification,
)


# ── Per-asset record held by Gateway ─────────────────────────────────────────

@dataclass
class AssetRecord:
    id: str
    type: str
    state: AssetState = AssetState.healthy
    active_fault_event_id: str | None = None


# ── SSE broker ───────────────────────────────────────────────────────────────

class SSEBroker:
    """Fan-out SSE broker.  One asyncio.Queue per subscriber."""

    def __init__(self) -> None:
        self._queues: list[asyncio.Queue[str]] = []

    def subscribe(self) -> asyncio.Queue[str]:
        q: asyncio.Queue[str] = asyncio.Queue()
        self._queues.append(q)
        return q

    def unsubscribe(self, q: asyncio.Queue[str]) -> None:
        try:
            self._queues.remove(q)
        except ValueError:
            pass

    async def publish(self, event_type: str, data: dict) -> None:
        payload = json.dumps({"type": event_type, "data": data})
        for q in list(self._queues):
            await q.put(payload)

    @property
    def subscriber_count(self) -> int:
        return len(self._queues)


# ── Main store ────────────────────────────────────────────────────────────────

@dataclass
class GatewayStore:
    """Holds all mutable Gateway runtime state."""

    fault_events: dict[str, FaultEvent] = field(default_factory=dict)
    notifications: list[Notification] = field(default_factory=list)
    activity_events: list[ActivityEvent] = field(default_factory=list)
    assets: dict[str, AssetRecord] = field(default_factory=dict)

    # fault_event_id → approval token string (set after human decision)
    pending_tokens: dict[str, str] = field(default_factory=dict)

    sse: SSEBroker = field(default_factory=SSEBroker)

    # ── FaultEvent helpers ────────────────────────────────────────────────────

    def create_fault_event(self, **kwargs) -> FaultEvent:
        evt = FaultEvent(**kwargs)
        self.fault_events[evt.id] = evt
        return evt

    def update_fault_status(
        self,
        fault_event_id: str,
        status: FaultEventStatus,
        **extra,
    ) -> FaultEvent | None:
        evt = self.fault_events.get(fault_event_id)
        if evt is None:
            return None
        updated = evt.model_copy(update={"status": status, **extra})
        self.fault_events[fault_event_id] = updated
        return updated

    # ── Notification helpers ──────────────────────────────────────────────────

    def create_notification(self, **kwargs) -> Notification:
        notif = Notification(**kwargs)
        self.notifications.append(notif)
        return notif

    def mark_read(self, notification_id: str) -> bool:
        for i, n in enumerate(self.notifications):
            if n.id == notification_id:
                self.notifications[i] = n.model_copy(update={"read": True})
                return True
        return False

    @property
    def unread_count(self) -> int:
        return sum(1 for n in self.notifications if not n.read)

    # ── Activity helpers ──────────────────────────────────────────────────────

    def create_activity(self, **kwargs) -> ActivityEvent:
        evt = ActivityEvent(**kwargs)
        self.activity_events.append(evt)
        return evt
