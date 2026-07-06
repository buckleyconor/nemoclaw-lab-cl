"""Redis-backed gateway store for multi-pod / 30-user demo deployments.

Implements the same async interface as GatewayStore using redis.asyncio.
All domain objects are stored as JSON in Redis hashes/lists.

Key layout (prefix = REDIS_KEY_PREFIX env var, default "nemoclaw"):
  {p}:faults    HASH  {fault_id: json}
  {p}:assets    HASH  {asset_id: json}
  {p}:tokens    HASH  {fault_id: token_str}
  {p}:notifs    HASH  {notif_id: json}
  {p}:activity  LIST  [json ...]  (append-only; sorted in memory on read)
"""

from __future__ import annotations

import asyncio
import contextlib
import json

import redis.asyncio as aioredis

from libs.common.models import (
    ActivityEvent,
    AssetState,
    FaultEvent,
    FaultEventStatus,
    Notification,
)
from services.gateway.store import AssetRecord, SSEBroker


class RedisSSEBroker(SSEBroker):
    """SSE broker that fans out across gateway replicas via Redis pub/sub.

    ``publish()`` writes to a Redis channel instead of the local queues; each
    process runs a listener task that receives every channel message
    (including its own) and fans it out to the local subscriber queues. A
    browser holding an SSE connection to one replica therefore sees events
    triggered by requests that landed on any other replica.
    """

    def __init__(self, r: aioredis.Redis, channel: str) -> None:
        super().__init__()
        self._r = r
        self._channel = channel
        self._listener: asyncio.Task[None] | None = None
        self._ready = asyncio.Event()

    def subscribe(self) -> asyncio.Queue[str]:
        if self._listener is None or self._listener.done():
            self._ready.clear()
            self._listener = asyncio.get_running_loop().create_task(self._listen())
        return super().subscribe()

    async def wait_ready(self) -> None:
        """Block until the listener holds the channel subscription."""
        await self._ready.wait()

    async def publish(self, event_type: str, data: dict) -> None:
        await self._r.publish(self._channel, json.dumps({"type": event_type, "data": data}))

    async def _listen(self) -> None:
        pubsub = self._r.pubsub()
        await pubsub.subscribe(self._channel)
        self._ready.set()
        try:
            async for message in pubsub.listen():
                if message.get("type") == "message":
                    self._fanout(message["data"])
        finally:
            await pubsub.aclose()

    async def aclose(self) -> None:
        if self._listener is not None:
            self._listener.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._listener
            self._listener = None


class RedisGatewayStore:
    """Redis-backed gateway state. Drop-in async replacement for GatewayStore."""

    def __init__(
        self,
        redis_url: str,
        prefix: str = "nemoclaw",
        *,
        _redis: aioredis.Redis | None = None,
    ) -> None:
        self._r: aioredis.Redis = _redis or aioredis.from_url(redis_url, decode_responses=True)
        self._p = prefix
        self.sse = RedisSSEBroker(self._r, channel=f"{prefix}:events")

    async def aclose(self) -> None:
        await self.sse.aclose()
        await self._r.aclose()

    def _k(self, name: str) -> str:
        return f"{self._p}:{name}"

    # ── FaultEvent ────────────────────────────────────────────────────────────

    async def create_fault_event(self, **kwargs) -> FaultEvent:
        evt = FaultEvent(**kwargs)
        await self._r.hset(self._k("faults"), evt.id, evt.model_dump_json())
        return evt

    async def update_fault_status(
        self,
        fault_event_id: str,
        status: FaultEventStatus,
        **extra,
    ) -> FaultEvent | None:
        raw = await self._r.hget(self._k("faults"), fault_event_id)
        if raw is None:
            return None
        evt = FaultEvent.model_validate_json(raw)
        updated = evt.model_copy(update={"status": status, **extra})
        await self._r.hset(self._k("faults"), fault_event_id, updated.model_dump_json())
        return updated

    async def update_fault_fields(
        self,
        fault_event_id: str,
        **fields,
    ) -> FaultEvent | None:
        """Partial update of diagnosis fields (signature, analysis, KB match, …)."""
        raw = await self._r.hget(self._k("faults"), fault_event_id)
        if raw is None:
            return None
        evt = FaultEvent.model_validate_json(raw)
        updated = evt.model_copy(update=fields)
        await self._r.hset(self._k("faults"), fault_event_id, updated.model_dump_json())
        return updated

    async def list_faults(self) -> list[FaultEvent]:
        raw = await self._r.hgetall(self._k("faults"))
        return [FaultEvent.model_validate_json(v) for v in raw.values()]

    async def get_fault(self, fault_id: str) -> FaultEvent | None:
        raw = await self._r.hget(self._k("faults"), fault_id)
        return FaultEvent.model_validate_json(raw) if raw else None

    async def has_fault(self, fault_id: str) -> bool:
        return bool(await self._r.hexists(self._k("faults"), fault_id))

    # ── Asset ─────────────────────────────────────────────────────────────────

    async def list_assets(self) -> list[AssetRecord]:
        raw = await self._r.hgetall(self._k("assets"))
        return [self._deserialize_asset(v) for v in raw.values()]

    async def get_asset(self, asset_id: str) -> AssetRecord | None:
        raw = await self._r.hget(self._k("assets"), asset_id)
        return self._deserialize_asset(raw) if raw else None

    async def has_asset(self, asset_id: str) -> bool:
        return bool(await self._r.hexists(self._k("assets"), asset_id))

    async def set_asset(self, asset: AssetRecord) -> None:
        payload = json.dumps({
            "id": asset.id,
            "type": asset.type,
            "state": asset.state.value,
            "active_fault_event_id": asset.active_fault_event_id,
        })
        await self._r.hset(self._k("assets"), asset.id, payload)

    def _deserialize_asset(self, raw: str) -> AssetRecord:
        data = json.loads(raw)
        return AssetRecord(
            id=data["id"],
            type=data["type"],
            state=AssetState(data["state"]),
            active_fault_event_id=data.get("active_fault_event_id"),
        )

    # ── Notification ──────────────────────────────────────────────────────────

    async def create_notification(self, **kwargs) -> Notification:
        notif = Notification(**kwargs)
        await self._r.hset(self._k("notifs"), notif.id, notif.model_dump_json())
        return notif

    async def mark_read(self, notification_id: str) -> bool:
        raw = await self._r.hget(self._k("notifs"), notification_id)
        if raw is None:
            return False
        notif = Notification.model_validate_json(raw)
        updated = notif.model_copy(update={"read": True})
        await self._r.hset(self._k("notifs"), notification_id, updated.model_dump_json())
        return True

    async def list_notifications(self) -> list[Notification]:
        raw = await self._r.hgetall(self._k("notifs"))
        return [Notification.model_validate_json(v) for v in raw.values()]

    async def get_unread_count(self) -> int:
        notifs = await self.list_notifications()
        return sum(1 for n in notifs if not n.read)

    # ── Activity ──────────────────────────────────────────────────────────────

    async def create_activity(self, **kwargs) -> ActivityEvent:
        evt = ActivityEvent(**kwargs)
        await self._r.rpush(self._k("activity"), evt.model_dump_json())
        return evt

    async def list_activity_events(self) -> list[ActivityEvent]:
        raw = await self._r.lrange(self._k("activity"), 0, -1)
        return [ActivityEvent.model_validate_json(v) for v in raw]

    # ── Pending tokens ────────────────────────────────────────────────────────

    async def set_pending_token(self, fault_id: str, token: str) -> None:
        await self._r.hset(self._k("tokens"), fault_id, token)

    async def get_pending_token(self, fault_id: str) -> str | None:
        return await self._r.hget(self._k("tokens"), fault_id)
