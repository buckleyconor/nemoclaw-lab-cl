"""Gateway API router.

Endpoints:
  GET  /api/pack                     active pack labels + theme
  GET  /api/assets                   fleet health grid
  GET  /api/notifications            notification inbox
  POST /api/notifications/{id}/read  mark notification read
  GET  /api/faults                   list all fault events
  GET  /api/faults/{id}              fault detail
  POST /api/faults                   agent: create fault event [server→server]
  PATCH /api/faults/{id}/status      agent: update fault status [server→server]
  POST /api/faults/{id}/decision     human: approve / deny → mints token
  GET  /api/faults/{id}/token        agent: retrieve approval token [server→server]
  GET  /api/activity                 agent activity feed
  POST /api/agent/activity           agent: post activity step [server→server]
  GET  /api/events                   SSE multiplex (asset/notification/activity/fault)
"""

from __future__ import annotations

import asyncio
import json
from typing import AsyncGenerator

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from libs.common.models import ApprovalDecision, AssetState, FaultEventStatus
from services.gateway.store import AssetRecord

router = APIRouter()


def _store(request: Request):
    return request.app.state.store


def _orchestrator_client(request: Request) -> httpx.AsyncClient:
    return request.app.state.orchestrator_client


def _mcp_tools_client(request: Request) -> httpx.AsyncClient:
    return request.app.state.mcp_tools_client


# ── Pack ─────────────────────────────────────────────────────────────────────


@router.get("/api/pack")
async def get_pack(request: Request) -> dict:
    """Return active pack labels and theme for the dashboard."""
    loaded = request.app.state.loaded_pack
    pack = loaded.pack
    return {
        "id": pack.id,
        "name": pack.name,
        "asset_noun": {"singular": pack.asset_noun.singular, "plural": pack.asset_noun.plural},
        "fleet_label": pack.fleet_label,
        "theme": pack.theme,
    }


# ── Assets ───────────────────────────────────────────────────────────────────


@router.get("/api/assets")
async def list_assets(request: Request) -> dict:
    """Fleet health grid — all assets with their current state."""
    store = _store(request)
    assets = await store.list_assets()
    return {
        "assets": [
            {
                "id": a.id,
                "type": a.type,
                "state": a.state.value,
                "active_fault_event_id": a.active_fault_event_id,
            }
            for a in assets
        ]
    }


# ── Notifications ─────────────────────────────────────────────────────────────


@router.get("/api/notifications")
async def list_notifications(request: Request) -> dict:
    store = _store(request)
    notifs = sorted(await store.list_notifications(), key=lambda n: n.ts, reverse=True)
    return {
        "notifications": [n.model_dump(mode="json") for n in notifs],
        "unread_count": await store.get_unread_count(),
    }


@router.post("/api/notifications/{notification_id}/read")
async def mark_notification_read(notification_id: str, request: Request) -> dict:
    store = _store(request)
    if not await store.mark_read(notification_id):
        raise HTTPException(status_code=404, detail="notification_not_found")
    return {"status": "read"}


# ── Fault events ──────────────────────────────────────────────────────────────


class CreateFaultRequest(BaseModel):
    scenario_id: str
    asset_id: str
    kb_article_id: str | None = None
    log_extract: str | None = None


class UpdateStatusRequest(BaseModel):
    status: FaultEventStatus


class DecisionRequest(BaseModel):
    decision: ApprovalDecision


@router.get("/api/faults")
async def list_faults(request: Request) -> dict:
    store = _store(request)
    evts = sorted(await store.list_faults(), key=lambda e: e.detected_at, reverse=True)
    return {"faults": [e.model_dump(mode="json") for e in evts]}


@router.get("/api/faults/{fault_id}")
async def get_fault(fault_id: str, request: Request) -> dict:
    store = _store(request)
    evt = await store.get_fault(fault_id)
    if evt is None:
        raise HTTPException(status_code=404, detail="fault_not_found")
    return evt.model_dump(mode="json")


@router.post("/api/faults", status_code=201)
async def create_fault(body: CreateFaultRequest, request: Request) -> dict:
    """Agent: register a detected fault event. Triggers an SSE notification."""
    store = _store(request)
    evt = await store.create_fault_event(
        scenario_id=body.scenario_id,
        asset_id=body.asset_id,
        kb_article_id=body.kb_article_id,
        log_extract=body.log_extract,
    )

    # Update asset state to faulted
    if await store.has_asset(body.asset_id):
        asset = await store.get_asset(body.asset_id)
        await store.set_asset(AssetRecord(
            id=asset.id,
            type=asset.type,
            state=AssetState.faulted,
            active_fault_event_id=evt.id,
        ))

    # Inbox notification
    notif = await store.create_notification(
        fault_event_id=evt.id,
        title=f"Fault detected on {body.asset_id}",
        body=body.log_extract or f"Scenario {body.scenario_id} triggered.",
    )

    # SSE broadcast
    await store.sse.publish("fault", evt.model_dump(mode="json"))
    await store.sse.publish("notification", notif.model_dump(mode="json"))
    await store.sse.publish("asset", {
        "id": body.asset_id,
        "state": "faulted",
        "active_fault_event_id": evt.id,
    })

    return {"id": evt.id, **evt.model_dump(mode="json")}


@router.patch("/api/faults/{fault_id}/status")
async def update_fault_status(fault_id: str, body: UpdateStatusRequest, request: Request) -> dict:
    """Agent: update fault lifecycle status."""
    store = _store(request)
    updated = await store.update_fault_status(fault_id, body.status)
    if updated is None:
        raise HTTPException(status_code=404, detail="fault_not_found")
    await store.sse.publish("fault", updated.model_dump(mode="json"))
    return updated.model_dump(mode="json")


@router.post("/api/faults/{fault_id}/decision")
async def post_decision(fault_id: str, body: DecisionRequest, request: Request) -> dict:
    """Human: approve or deny remediation.

    On approval:
      1. Registers fault event + allowed steps in MCP Tools
      2. Mints a single-use approval token in MCP Tools
      3. Stores token against fault_event_id for agent retrieval
      4. Pushes SSE event
    """
    store = _store(request)
    evt = await store.get_fault(fault_id)
    if evt is None:
        raise HTTPException(status_code=404, detail="fault_not_found")

    if body.decision == ApprovalDecision.denied:
        updated = await store.update_fault_status(fault_id, FaultEventStatus.denied)
        await store.sse.publish("fault", updated.model_dump(mode="json"))
        await store.sse.publish("decision", {
            "fault_event_id": fault_id,
            "decision": "denied",
        })
        return {"decision": "denied", "fault_event_id": fault_id}

    # ── Approved path ────────────────────────────────────────────────────────

    # Fetch allowed step IDs from Orchestrator
    orch_client = _orchestrator_client(request)
    try:
        scenario_r = await orch_client.get(f"/api/assets/{evt.asset_id}/scenario")
        scenario_r.raise_for_status()
        scenario_data = scenario_r.json()
        allowed_step_ids = [s["id"] for s in scenario_data.get("remediation_steps", [])]
    except httpx.HTTPError:
        # Fall back to empty allowlist — remediation will fail step validation
        allowed_step_ids = []

    # Register fault event + allowed steps in MCP Tools
    mcp_client = _mcp_tools_client(request)
    try:
        await mcp_client.post(
            "/internal/fault-events",
            json={
                "fault_event_id": fault_id,
                "asset_id": evt.asset_id,
                "scenario_id": evt.scenario_id,
                "allowed_step_ids": allowed_step_ids,
            },
        )
        token_r = await mcp_client.post(
            "/internal/tokens",
            json={
                "fault_event_id": fault_id,
                "decision": "approved",
                "decided_by": "human",
            },
        )
        token_r.raise_for_status()
        token_data = token_r.json()
        token_str = token_data["token"]
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"mcp_tools_error: {exc}") from exc

    # Store token for agent retrieval
    await store.set_pending_token(fault_id, token_str)

    # Update status
    updated = await store.update_fault_status(fault_id, FaultEventStatus.awaiting_approval)

    await store.sse.publish("fault", updated.model_dump(mode="json"))
    await store.sse.publish("decision", {
        "fault_event_id": fault_id,
        "decision": "approved",
    })

    return {
        "decision": "approved",
        "fault_event_id": fault_id,
        "token_available": True,
    }


@router.get("/api/faults/{fault_id}/token")
async def get_token(fault_id: str, request: Request) -> dict:
    """Agent: retrieve the approval token for a fault event [server-to-server].

    The token is NEVER placed in the LLM's text context — the agent retrieves it
    programmatically over this trusted path right before calling remediation.execute.
    """
    store = _store(request)
    if not await store.has_fault(fault_id):
        raise HTTPException(status_code=404, detail="fault_not_found")
    token = await store.get_pending_token(fault_id)
    if token is None:
        raise HTTPException(status_code=404, detail="token_not_available")
    return {"token": token, "fault_event_id": fault_id}


# ── Activity feed ─────────────────────────────────────────────────────────────


class ActivityRequest(BaseModel):
    fault_event_id: str
    step: str
    message: str


@router.get("/api/activity")
async def list_activity(request: Request) -> dict:
    store = _store(request)
    evts = sorted(await store.list_activity_events(), key=lambda e: e.ts)
    return {"activity": [e.model_dump(mode="json") for e in evts]}


@router.post("/api/agent/activity", status_code=201)
async def post_activity(body: ActivityRequest, request: Request) -> dict:
    """Agent: log an activity step to the real-time feed."""
    store = _store(request)
    evt = await store.create_activity(
        fault_event_id=body.fault_event_id,
        step=body.step,
        message=body.message,
    )
    await store.sse.publish("activity", evt.model_dump(mode="json"))
    return evt.model_dump(mode="json")


# ── SSE ──────────────────────────────────────────────────────────────────────


@router.get("/api/events")
async def events_stream(request: Request) -> StreamingResponse:
    """Server-Sent Events multiplex.

    Event types: ``fault`` | ``notification`` | ``activity`` | ``asset`` | ``decision``

    Clients receive a JSON object per line: ``data: {"type": "...", "data": {...}}``
    Keepalive comment ``: keepalive`` sent every 25 s when idle.
    """
    store = _store(request)
    queue = store.sse.subscribe()

    async def generate() -> AsyncGenerator[str, None]:
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    payload = await asyncio.wait_for(queue.get(), timeout=25.0)
                    yield f"data: {payload}\n\n"
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
        finally:
            store.sse.unsubscribe(queue)

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
