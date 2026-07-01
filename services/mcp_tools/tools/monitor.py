"""monitor MCP tool — list events / inspect assets via the MonitoringAdapter."""

from __future__ import annotations

from dataclasses import asdict

from services.mcp_tools.adapters.base import MonitoringAdapter


async def monitor_list_events(
    adapter: MonitoringAdapter,
    asset_id: str | None = None,
) -> list[dict]:
    """Return monitoring events from the active pack's assets.

    Args:
        adapter:  Initialised MonitoringAdapter for the current pack.
        asset_id: Optional filter; returns events for that asset only.
    """
    events = await adapter.list_events(asset_id=asset_id)
    return [asdict(e) for e in events]


async def monitor_get_asset(
    adapter: MonitoringAdapter,
    asset_id: str,
) -> dict:
    """Return health state for a single asset."""
    asset = await adapter.get_asset(asset_id)
    return asdict(asset)


async def monitor_list_assets(
    adapter: MonitoringAdapter,
) -> list[dict]:
    """Return health state for all assets in the pack."""
    assets = await adapter.list_assets()
    return [asdict(a) for a in assets]
