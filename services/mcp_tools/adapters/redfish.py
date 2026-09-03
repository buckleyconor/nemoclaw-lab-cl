"""RedfishAdapter — MonitoringAdapter backed by the Simulator's Redfish surface."""

from __future__ import annotations

import httpx

from services.mcp_tools.adapters.base import MonitorAsset, MonitorEvent, MonitoringAdapter


class RedfishAdapter:
    """Calls the Simulator's read-only Redfish surface.

    Satisfies the ``MonitoringAdapter`` protocol (verified by PACK-04).
    """

    def __init__(self, simulator_url: str, *, _transport=None) -> None:
        self._base = simulator_url.rstrip("/")
        self._transport = _transport  # None → real HTTP; ASGI transport → tests

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=self._transport)

    # ──────────────────────────────────────────────────────────────────────────
    # MonitoringAdapter protocol
    # ──────────────────────────────────────────────────────────────────────────

    async def list_assets(self) -> list[MonitorAsset]:
        async with self._client() as client:
            r = await client.get(f"{self._base}/redfish/v1/Systems")
            r.raise_for_status()
            data = r.json()
            assets: list[MonitorAsset] = []
            for member in data.get("Members", []):
                asset_url: str = member["@odata.id"]
                asset_id = asset_url.rstrip("/").split("/")[-1]
                asset_r = await client.get(f"{self._base}{asset_url}")
                asset_r.raise_for_status()
                asset_data = asset_r.json()
                health = asset_data.get("Status", {}).get("Health", "OK")
                assets.append(
                    MonitorAsset(
                        id=asset_id,
                        type=asset_data.get("SystemType", "server"),
                        state="faulted" if health != "OK" else "healthy",
                    )
                )
            return assets

    async def list_events(self, asset_id: str | None = None) -> list[MonitorEvent]:
        async with self._client() as client:
            if asset_id:
                url = f"{self._base}/redfish/v1/Systems/{asset_id}/LogServices/DCGM/Entries"
                r = await client.get(url)
                r.raise_for_status()
                entries = r.json().get("Members", [])
                return [
                    MonitorEvent(
                        asset_id=asset_id,
                        severity=e.get("Severity", "Unknown"),
                        message=e.get("Message", ""),
                        message_id=e.get("MessageId", ""),
                        ts=e.get("Created", ""),
                    )
                    for e in entries
                ]
            # All-assets path via EventService
            r = await client.get(f"{self._base}/redfish/v1/EventService/Events")
            r.raise_for_status()
            events: list[MonitorEvent] = []
            for member in r.json().get("Members", []):
                aid: str = member["Id"]
                for evt in member.get("Events", []):
                    events.append(
                        MonitorEvent(
                            asset_id=aid,
                            severity=evt.get("Severity", "Unknown"),
                            message=evt.get("Message", ""),
                            message_id=evt.get("MessageId", ""),
                            ts=evt.get("EventTimestamp", ""),
                        )
                    )
            return events

    async def get_asset(self, asset_id: str) -> MonitorAsset:
        async with self._client() as client:
            r = await client.get(f"{self._base}/redfish/v1/Systems/{asset_id}")
            r.raise_for_status()
            data = r.json()
            health = data.get("Status", {}).get("Health", "OK")
            return MonitorAsset(
                id=asset_id,
                type=data.get("SystemType", "server"),
                state="faulted" if health != "OK" else "healthy",
            )


# Runtime type-check: verify the class satisfies the protocol.
_: MonitoringAdapter = RedfishAdapter.__new__(RedfishAdapter)  # noqa: F841
