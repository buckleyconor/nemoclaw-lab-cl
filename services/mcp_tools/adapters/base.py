"""MonitoringAdapter protocol — the interface all adapters must satisfy."""

from __future__ import annotations

from typing import Protocol

from libs.common.models import _Placeholder as Asset  # replaced in M1


class Event(_Placeholder):  # noqa: F821 — replaced in M1
    pass


class MonitoringAdapter(Protocol):
    """Vertical-blind monitoring interface. Implemented by redfish.py and generic.py."""

    async def list_assets(self) -> list[Asset]: ...

    async def list_events(self, asset_id: str | None = None) -> list[Event]: ...

    async def get_asset(self, asset_id: str) -> Asset: ...
