"""In-memory gateway state store (development).

All state-access methods are ``async def`` so the router uses ``await store.X()``
everywhere — both GatewayStore and RedisGatewayStore share the same async interface
without any router changes between environments.
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


# ── Per-asset record ──────────────────────────────────────────────────────────

@dataclass
class AssetRecord:
    id: str
    type: str
    state: AssetState = AssetState.healthy
    active_fault_event_id: str | None = None


# ── SSE broker ────────────────────────────────────────────────────────────────

class SSEBroker:
    """Fan-out SSE broker. One asyncio.Queue per subscriber."""

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


# ── In-memory store ───────────────────────────────────────────────────────────

@dataclass
class GatewayStore:
    """In-memory gateway state. All methods async to match RedisGatewayStore."""

    _fault_events: dict[str, FaultEvent] = field(default_factory=dict)
    _notifications: dict[str, Notification] = field(default_factory=dict)
    _activity_events: list[ActivityEvent] = field(default_factory=list)
    _assets: dict[str, AssetRecord] = field(default_factory=dict)
    _pending_tokens: dict[str, str] = field(default_factory=dict)
    sse: SSEBroker = field(default_factory=SSEBroker)

    # ── FaultEvent ────────────────────────────────────────────────────────────

    async def create_fault_event(self, **kwargs) -> FaultEvent:
        evt = FaultEvent(**kwargs)
        self._fault_events[evt.id] = evt
        return evt

    async def update_fault_status(
        self,
        fault_event_id: str,
        status: FaultEventStatus,
        **extra,
    ) -> FaultEvent | None:
        evt = self._fault_events.get(fault_event_id)
        if evt is None:
            return None
        updated = evt.model_copy(update={"status": status, **extra})
        self._fault_events[fault_event_id] = updated
        return updated

    async def list_faults(self) -> list[FaultEvent]:
        return list(self._fault_events.values())

    async def get_fault(self, fault_id: str) -> FaultEvent | None:
        return self._fault_events.get(fault_id)

    async def has_fault(self, fault_id: str) -> bool:
        return fault_id in self._fault_events

    # ── Asset ─────────────────────────────────────────────────────────────────

    async def list_assets(self) -> list[AssetRecord]:
        return list(self._assets.values())

    async def get_asset(self, asset_id: str) -> AssetRecord | None:
        return self._assets.get(asset_id)

    async def has_asset(self, asset_id: str) -> bool:
        return asset_id in self._assets

    async def set_asset(self, asset: AssetRecord) -> None:
        self._assets[asset.id] = asset

    # ── Notification ──────────────────────────────────────────────────────────

    async def create_notification(self, **kwargs) -> Notification:
        notif = Notification(**kwargs)
        self._notifications[notif.id] = notif
        return notif

    async def mark_read(self, notification_id: str) -> bool:
        if notification_id not in self._notifications:
            return False
        n = self._notifications[notification_id]
        self._notifications[notification_id] = n.model_copy(update={"read": True})
        return True

    async def list_notifications(self) -> list[Notification]:
        return list(self._notifications.values())

    async def get_unread_count(self) -> int:
        return sum(1 for n in self._notifications.values() if not n.read)

    # ── Activity ──────────────────────────────────────────────────────────────

    async def create_activity(self, **kwargs) -> ActivityEvent:
        evt = ActivityEvent(**kwargs)
        self._activity_events.append(evt)
        return evt

    async def list_activity_events(self) -> list[ActivityEvent]:
        return list(self._activity_events)

    # ── Pending tokens ────────────────────────────────────────────────────────

    async def set_pending_token(self, fault_id: str, token: str) -> None:
        self._pending_tokens[fault_id] = token

    async def get_pending_token(self, fault_id: str) -> str | None:
        return self._pending_tokens.get(fault_id)
