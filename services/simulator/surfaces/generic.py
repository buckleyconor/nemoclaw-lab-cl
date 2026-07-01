"""Generic REST surface — used by non-Redfish packs (laptop-fleet, etc.).

GenericAdapter in mcp_tools expects these three endpoints:
  GET /assets                  → [{id, type, state}]
  GET /assets/{asset_id}       → {id, type, state}
  GET /events[?asset_id=X]     → [{asset_id, severity, message, message_id, ts}]
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request

from services.simulator.engine import SimulatorEngine

router = APIRouter(tags=["generic"])


def _engine(request: Request) -> SimulatorEngine:
    return request.app.state.engine  # type: ignore[no-any-return]


@router.get("/assets")
async def list_assets(request: Request) -> list[dict]:
    engine = _engine(request)
    return [
        {"id": aid, "type": engine.asset_type(aid), "state": state}
        for aid, state in engine.all_states().items()
    ]


@router.get("/assets/{asset_id}")
async def get_asset(asset_id: str, request: Request) -> dict:
    engine = _engine(request)
    states = engine.all_states()
    if asset_id not in states:
        raise HTTPException(status_code=404, detail=f"Asset not found: {asset_id!r}")
    return {"id": asset_id, "type": engine.asset_type(asset_id), "state": states[asset_id]}


@router.get("/events")
async def list_events(
    request: Request,
    asset_id: str = Query(default=""),
) -> list[dict]:
    engine = _engine(request)
    target_ids = [asset_id] if asset_id else engine.asset_ids
    events: list[dict] = []
    for aid in target_ids:
        fault = engine.get_fault(aid)
        if fault is None:
            continue
        message = fault.log_entries[0][1] if fault.log_entries else f"Fault on {aid}"
        events.append({
            "asset_id": aid,
            "severity": fault.event_severity,
            "message": message,
            "message_id": fault.event_message_id,
            "ts": fault.injected_at.isoformat(),
        })
    return events
