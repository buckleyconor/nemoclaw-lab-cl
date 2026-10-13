"""The dashboard must open healthy — no leftover fault cards, empty activity feed.

Reported after a long debug session: opening the UI showed a fault from earlier
testing and the activity feed was full of it. In-memory state already starts
empty, but nothing *guaranteed* it, and Redis-backed state deliberately outlives
the process — so a rebooted lab would come up showing the previous run.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from services.gateway import main as main_mod
from services.gateway.main import _reset_state_on_start
from services.gateway.store import GatewayStore

PACK_DIR = Path(__file__).parent.parent.parent / "packs" / "datacenter-xe9680"


@pytest.mark.parametrize(
    ("redis_url", "flag", "expected"),
    [
        (None, None, True),  # in-memory lab default: opens clean
        ("", None, True),  # empty string is "not configured", not "Redis"
        ("redis://redis:6379/0", None, False),  # shared state: don't wipe others
        (None, "0", False),  # explicit override, both directions
        ("redis://redis:6379/0", "1", True),
        (None, "yes", True),  # unparseable flag -> the safe default
    ],
)
def test_reset_state_on_start_decision(
    redis_url: str | None, flag: str | None, expected: bool
) -> None:
    assert _reset_state_on_start(redis_url, flag) is expected


class _TrackingStore(GatewayStore):
    """Records whether startup reset the store.

    Accepts and ignores constructor args so the same fake can stand in for both
    GatewayStore() and RedisGatewayStore(redis_url, prefix=...).
    """

    reset_calls = 0

    def __init__(self, *args, **kwargs) -> None:
        super().__init__()

    async def reset(self) -> None:
        _TrackingStore.reset_calls += 1
        await super().reset()


def _boot(monkeypatch, *, redis_url: str | None = None, reset_flag: str | None = None):
    _TrackingStore.reset_calls = 0
    monkeypatch.delenv("REDIS_URL", raising=False)
    monkeypatch.delenv("RESET_STATE_ON_START", raising=False)
    if redis_url is not None:
        monkeypatch.setenv("REDIS_URL", redis_url)
    if reset_flag is not None:
        monkeypatch.setenv("RESET_STATE_ON_START", reset_flag)

    # create_app imports RedisGatewayStore inside the lifespan, so patching the
    # attribute is what the lifespan actually resolves at call time.
    import services.gateway.redis_store as redis_store

    monkeypatch.setattr(redis_store, "RedisGatewayStore", _TrackingStore)
    monkeypatch.setattr(main_mod, "GatewayStore", _TrackingStore)

    app = main_mod.create_app(pack_dir=PACK_DIR)
    with TestClient(app):  # entering runs the lifespan (startup + shutdown)
        pass
    return _TrackingStore.reset_calls


def test_in_memory_startup_resets_state(monkeypatch) -> None:
    assert _boot(monkeypatch) == 1


def test_redis_startup_does_not_wipe_shared_state(monkeypatch) -> None:
    assert _boot(monkeypatch, redis_url="redis://redis:6379/0") == 0


def test_redis_startup_resets_when_forced(monkeypatch) -> None:
    assert _boot(monkeypatch, redis_url="redis://redis:6379/0", reset_flag="1") == 1


def test_in_memory_startup_reset_can_be_disabled(monkeypatch) -> None:
    assert _boot(monkeypatch, reset_flag="0") == 0


async def test_reset_leaves_assets_healthy_and_feeds_empty() -> None:
    """What "opens healthy" has to mean for the things the operator can see."""
    from libs.common.models import AssetState
    from services.gateway.store import AssetRecord

    store = GatewayStore()
    await store.set_asset(AssetRecord(id="gpu-server-01", type="server", state=AssetState.faulted))
    await store.create_fault_event(scenario_id="scn-nvlink-down", asset_id="gpu-server-01")
    await store.create_activity(
        fault_event_id="f1", step="detect", message="leftover from the last run"
    )

    await store.reset()

    assert [a.state for a in await store.list_assets()] == [AssetState.healthy]
    assert await store.list_faults() == []
    assert await store.list_activity_events() == []
    assert await store.get_unread_count() == 0


def _boot_prefilled(monkeypatch, reset_flag: str | None) -> GatewayStore:
    """Boot the app with a store the lifespan seeding deliberately dirties."""
    from libs.common.models import AssetState
    from services.gateway.store import AssetRecord

    captured: list[GatewayStore] = []

    class PreFilled(GatewayStore):
        def __init__(self) -> None:
            super().__init__()
            captured.append(self)

        async def set_asset(self, asset: AssetRecord) -> None:
            await super().set_asset(asset)
            # Dirty it exactly where the lifespan seeds it, so only a startup
            # reset can bring it back — this is the state a Redis-backed store
            # would legitimately still hold after a reboot.
            await super().set_asset(
                AssetRecord(id=asset.id, type=asset.type, state=AssetState.faulted)
            )
            await self.create_fault_event(scenario_id="scn-nvlink-down", asset_id=asset.id)
            await self.create_activity(
                fault_event_id="f1", step="detect", message="stale fault from last run"
            )

    monkeypatch.delenv("REDIS_URL", raising=False)
    monkeypatch.delenv("RESET_STATE_ON_START", raising=False)
    if reset_flag is not None:
        monkeypatch.setenv("RESET_STATE_ON_START", reset_flag)
    monkeypatch.setattr(main_mod, "GatewayStore", PreFilled)

    app = main_mod.create_app(pack_dir=PACK_DIR)
    with TestClient(app):
        pass
    return captured[0]


async def test_startup_leaves_the_dashboard_clean(monkeypatch) -> None:
    store = _boot_prefilled(monkeypatch, reset_flag=None)
    assets = await store.list_assets()
    assert assets, "pack should have seeded assets"
    assert [a.state.value for a in assets] == ["healthy"] * len(assets)
    assert await store.list_faults() == []
    assert await store.list_activity_events() == []


async def test_startup_reset_off_leaves_the_stale_state(monkeypatch) -> None:
    """Negative control — proves the test above isn't passing vacuously."""
    store = _boot_prefilled(monkeypatch, reset_flag="0")
    assert await store.list_faults() != []
    assert await store.list_activity_events() != []
    assert any(a.state.value != "healthy" for a in await store.list_assets())
