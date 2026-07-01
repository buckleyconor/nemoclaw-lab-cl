"""MonitoringAdapter protocol — the interface all adapters must satisfy.

The agent calls ``monitor.list_events()`` and is identical across all
packs. Adapters translate between pack-specific surfaces (Redfish for
datacenter, generic REST for laptops/rigs) and this common shape.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass
class MonitorAsset:
    id: str
    type: str
    state: str  # "healthy" | "faulted"


@dataclass
class MonitorEvent:
    asset_id: str
    severity: str
    message: str
    message_id: str
    ts: str


class MonitoringAdapter(Protocol):
    """Vertical-blind monitoring interface. Implemented by redfish.py and generic.py."""

    async def list_assets(self) -> list[MonitorAsset]: ...

    async def list_events(self, asset_id: str | None = None) -> list[MonitorEvent]: ...

    async def get_asset(self, asset_id: str) -> MonitorAsset: ...
