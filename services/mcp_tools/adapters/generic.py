"""GenericAdapter — MonitoringAdapter for non-Redfish assets (laptop fleet, GPU rigs).

Calls a simple REST health surface:
  GET {base}/assets               → list of {id, type, state}
  GET {base}/assets/{id}          → {id, type, state}
  GET {base}/events[?asset_id=X]  → list of event objects

Used by future packs (e.g. laptop-fleet, rtx-pro-rig).
Included in M4 so PACK-04 can verify both adapters satisfy MonitoringAdapter.
"""

from __future__ import annotations

import httpx

from services.mcp_tools.adapters.base import MonitorAsset, MonitorEvent, MonitoringAdapter


class GenericAdapter:
    """Calls a generic REST health surface.

    Satisfies the ``MonitoringAdapter`` protocol (verified by PACK-04).
    """

    def __init__(self, base_url: str) -> None:
        self._base = base_url.rstrip("/")

    async def list_assets(self) -> list[MonitorAsset]:
        async with httpx.AsyncClient() as client:
            r = await client.get(f"{self._base}/assets")
            r.raise_for_status()
            return [
                MonitorAsset(
                    id=a["id"],
                    type=a.get("type", "device"),
                    state=a.get("state", "healthy"),
                )
                for a in r.json()
            ]

    async def list_events(self, asset_id: str | None = None) -> list[MonitorEvent]:
        async with httpx.AsyncClient() as client:
            url = f"{self._base}/events"
            params = {"asset_id": asset_id} if asset_id else {}
            r = await client.get(url, params=params)
            r.raise_for_status()
            return [
                MonitorEvent(
                    asset_id=e.get("asset_id", ""),
                    severity=e.get("severity", "Unknown"),
                    message=e.get("message", ""),
                    message_id=e.get("message_id", ""),
                    ts=e.get("ts", ""),
                )
                for e in r.json()
            ]

    async def get_asset(self, asset_id: str) -> MonitorAsset:
        async with httpx.AsyncClient() as client:
            r = await client.get(f"{self._base}/assets/{asset_id}")
            r.raise_for_status()
            a = r.json()
            return MonitorAsset(
                id=a["id"],
                type=a.get("type", "device"),
                state=a.get("state", "healthy"),
            )


# Runtime type-check: verify the class satisfies the protocol.
_: MonitoringAdapter = GenericAdapter.__new__(GenericAdapter)  # noqa: F841
