"""Unit tests for RedisGatewayStore.

Uses fakeredis so no real Redis instance is needed.  Verifies that
RedisGatewayStore implements the same async interface as GatewayStore and
correctly persists/retrieves all domain objects.

Test IDs: REDIS-01 .. REDIS-08
"""

from __future__ import annotations

import pytest
import fakeredis.aioredis as fake_aioredis

from libs.common.models import AssetState, FaultEventStatus
from services.gateway.redis_store import RedisGatewayStore
from services.gateway.store import AssetRecord


@pytest.fixture
async def store() -> RedisGatewayStore:
    fake = fake_aioredis.FakeRedis(decode_responses=True)
    return RedisGatewayStore("redis://unused", prefix="test", _redis=fake)


# ── REDIS-01: FaultEvent CRUD ─────────────────────────────────────────────────

async def test_redis01_create_and_get_fault(store: RedisGatewayStore) -> None:
    evt = await store.create_fault_event(
        scenario_id="scn-gpu-xid-79",
        asset_id="xe9680-01",
        kb_article_id="KB0001",
        log_extract="Xid 79 detected",
    )
    fetched = await store.get_fault(evt.id)
    assert fetched is not None
    assert fetched.id == evt.id
    assert fetched.scenario_id == "scn-gpu-xid-79"
    assert fetched.asset_id == "xe9680-01"


async def test_redis01_list_faults_returns_all(store: RedisGatewayStore) -> None:
    await store.create_fault_event(scenario_id="s1", asset_id="a1")
    await store.create_fault_event(scenario_id="s2", asset_id="a2")
    faults = await store.list_faults()
    assert len(faults) == 2


async def test_redis01_has_fault(store: RedisGatewayStore) -> None:
    evt = await store.create_fault_event(scenario_id="s", asset_id="a")
    assert await store.has_fault(evt.id)
    assert not await store.has_fault("nonexistent")


async def test_redis01_update_fault_status(store: RedisGatewayStore) -> None:
    evt = await store.create_fault_event(scenario_id="s", asset_id="a")
    updated = await store.update_fault_status(evt.id, FaultEventStatus.resolved)
    assert updated is not None
    assert updated.status == FaultEventStatus.resolved
    # Verify persistence
    fetched = await store.get_fault(evt.id)
    assert fetched.status == FaultEventStatus.resolved


async def test_redis01_update_nonexistent_fault_returns_none(store: RedisGatewayStore) -> None:
    result = await store.update_fault_status("ghost", FaultEventStatus.resolved)
    assert result is None


# ── REDIS-02: Asset CRUD ──────────────────────────────────────────────────────

async def test_redis02_set_and_get_asset(store: RedisGatewayStore) -> None:
    asset = AssetRecord(id="xe9680-01", type="gpu-server")
    await store.set_asset(asset)
    fetched = await store.get_asset("xe9680-01")
    assert fetched is not None
    assert fetched.id == "xe9680-01"
    assert fetched.type == "gpu-server"
    assert fetched.state == AssetState.healthy


async def test_redis02_set_asset_with_faulted_state(store: RedisGatewayStore) -> None:
    asset = AssetRecord(
        id="xe9680-02",
        type="gpu-server",
        state=AssetState.faulted,
        active_fault_event_id="fe-123",
    )
    await store.set_asset(asset)
    fetched = await store.get_asset("xe9680-02")
    assert fetched.state == AssetState.faulted
    assert fetched.active_fault_event_id == "fe-123"


async def test_redis02_has_asset(store: RedisGatewayStore) -> None:
    await store.set_asset(AssetRecord(id="xe9680-01", type="gpu-server"))
    assert await store.has_asset("xe9680-01")
    assert not await store.has_asset("missing")


async def test_redis02_list_assets(store: RedisGatewayStore) -> None:
    await store.set_asset(AssetRecord(id="a1", type="t"))
    await store.set_asset(AssetRecord(id="a2", type="t"))
    assets = await store.list_assets()
    assert {a.id for a in assets} == {"a1", "a2"}


# ── REDIS-03: Notification CRUD ───────────────────────────────────────────────

async def test_redis03_create_and_list_notifications(store: RedisGatewayStore) -> None:
    n1 = await store.create_notification(
        fault_event_id="fe-1",
        title="GPU fault on xe9680-01",
        body="Xid 79 detected",
    )
    n2 = await store.create_notification(
        fault_event_id="fe-2",
        title="Driver fault on laptop-02",
        body="TDR detected",
    )
    notifs = await store.list_notifications()
    ids = {n.id for n in notifs}
    assert n1.id in ids
    assert n2.id in ids


async def test_redis03_mark_read(store: RedisGatewayStore) -> None:
    notif = await store.create_notification(
        fault_event_id="fe-1", title="t", body="b"
    )
    assert not notif.read
    result = await store.mark_read(notif.id)
    assert result is True
    updated = await store.list_notifications()
    assert all(n.read for n in updated if n.id == notif.id)


async def test_redis03_mark_read_nonexistent_returns_false(store: RedisGatewayStore) -> None:
    assert not await store.mark_read("ghost-id")


async def test_redis03_unread_count(store: RedisGatewayStore) -> None:
    n1 = await store.create_notification(fault_event_id="fe-1", title="t", body="b")
    await store.create_notification(fault_event_id="fe-2", title="t", body="b")
    assert await store.get_unread_count() == 2
    await store.mark_read(n1.id)
    assert await store.get_unread_count() == 1


# ── REDIS-04: Activity ────────────────────────────────────────────────────────

async def test_redis04_create_and_list_activity(store: RedisGatewayStore) -> None:
    e1 = await store.create_activity(
        fault_event_id="fe-1", step="diagnose", message="Running KB search"
    )
    e2 = await store.create_activity(
        fault_event_id="fe-1", step="remediate", message="Applied patch"
    )
    events = await store.list_activity_events()
    steps = [e.step for e in events]
    assert "diagnose" in steps
    assert "remediate" in steps


# ── REDIS-05: Pending tokens ──────────────────────────────────────────────────

async def test_redis05_set_and_get_pending_token(store: RedisGatewayStore) -> None:
    await store.set_pending_token("fe-1", "tok-abc123")
    token = await store.get_pending_token("fe-1")
    assert token == "tok-abc123"


async def test_redis05_get_missing_token_returns_none(store: RedisGatewayStore) -> None:
    result = await store.get_pending_token("no-such-fault")
    assert result is None


# ── REDIS-06: SSE broker is present ──────────────────────────────────────────

async def test_redis06_sse_broker_exists(store: RedisGatewayStore) -> None:
    assert store.sse is not None
    assert store.sse.subscriber_count == 0


# ── REDIS-07: Key isolation by prefix ────────────────────────────────────────

async def test_redis07_prefix_isolation() -> None:
    """Two stores with different prefixes don't share data."""
    fake = fake_aioredis.FakeRedis(decode_responses=True)
    store_a = RedisGatewayStore("redis://unused", prefix="ns-a", _redis=fake)
    store_b = RedisGatewayStore("redis://unused", prefix="ns-b", _redis=fake)

    await store_a.create_fault_event(scenario_id="scn", asset_id="a1")
    faults_b = await store_b.list_faults()
    assert len(faults_b) == 0


# ── REDIS-08: get_fault returns None for missing ──────────────────────────────

async def test_redis08_get_fault_missing_returns_none(store: RedisGatewayStore) -> None:
    result = await store.get_fault("no-such-id")
    assert result is None


async def test_redis08_get_asset_missing_returns_none(store: RedisGatewayStore) -> None:
    result = await store.get_asset("no-such-asset")
    assert result is None
