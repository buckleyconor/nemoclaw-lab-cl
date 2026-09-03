"""Gateway API router.

Endpoints:
  GET  /api/pack                     active pack labels + theme
  GET  /api/lab-health               host-side dependency health (UI chip)
  GET  /api/agent/status             agent configured/monitoring state (UI)
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
from collections.abc import AsyncGenerator

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from libs.common.models import (
    ApprovalDecision,
    AssetState,
    FaultEventStatus,
    ScenarioImpact,
)
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
        "sentinel_name": pack.sentinel_name,
        "asset_image_url": pack.asset_image_url,
        "asset_spec_label": pack.asset_spec_label,
        "fleet_layout": pack.fleet_layout,
        # {asset_id: display_name} — only assets with an explicit display_name
        # are included; the UI falls back to the raw id for the rest.
        "asset_display_names": {
            a.id: a.display_name for a in pack.assets if a.display_name
        },
        # {asset_id: image_url} — only assets with a per-asset override are
        # included; the UI falls back to pack.asset_image_url for the rest.
        "asset_image_urls": {
            a.id: a.image_url for a in pack.assets if a.image_url
        },
    }


# ── Lab health ─────────────────────────────────────────────────────────────────

# The container can't run the host's `make doctor` (no docker/nginx visibility),
# but it can probe every host dependency the demo rides on: compose services
# (same network), the inference proxy + wake hook + terminal daemon
# (host.docker.internal). Before this endpoint, a dead agent LLM route or a
# dead wake forward presented in the UI as a plain "agent idle" — indistinguish
# from "no faults right now" (the 2026-08-28 incident). A red chip here means
# the lab is broken, not the scenario.

_LAB_HEALTH_TIMEOUT = 4.0


@router.get("/api/lab-health")
async def lab_health() -> dict:
    """Probe lab dependencies from inside the container.

    `skip` (feature not configured) is not a failure; `healthy` is true when
    nothing is `fail`. See the section comment for the failure mode this
    surfaces.
    """
    import os
    from datetime import datetime, timezone

    checks: list[dict] = []

    def add(cid: str, name: str, status: str, detail: str) -> None:
        checks.append({"id": cid, "name": name, "status": status, "detail": detail})

    orch_url = os.environ.get("ORCHESTRATOR_URL", "http://orchestrator:8002")
    mcp_url = os.environ.get("MCP_TOOLS_URL", "http://mcp-tools:8004")
    sim_url = os.environ.get("SIMULATOR_URL", "http://simulator:8003")
    hook_url = os.environ.get("OPENCLAW_HOOK_URL", "")
    llm_key = os.environ.get("LLM_API_KEY", "")
    llm_port = os.environ.get("LLM_PROXY_PORT", "18100")
    llm_direct = os.environ.get("LLM_DIRECT", "") == "1"
    term_ws = os.environ.get("TERMINAL_WS_URL", "")
    term_enabled = os.environ.get("TERMINAL_ENABLED", "")

    async with httpx.AsyncClient(timeout=_LAB_HEALTH_TIMEOUT) as client:
        for cid, name, base in (
            ("orchestrator", "Orchestrator", orch_url),
            ("simulator", "Simulator", sim_url),
            ("mcp-tools", "MCP tools", mcp_url),
        ):
            try:
                r = await client.get(f"{base.rstrip('/')}/healthz")
                add(cid, name, "ok" if r.status_code == 200 else "fail",
                    f"{base}/healthz -> {r.status_code}")
            except httpx.HTTPError as e:
                add(cid, name, "fail", f"{base}/healthz unreachable ({type(e).__name__})")

        # Inference proxy — the agent's LLM route (ADR-014). When this dies
        # the agent's cron wakes burn on 503s and the UI shows "idle".
        #
        # LLM_DIRECT=1 opts out of the proxy entirely (the sandbox was
        # onboarded against a public endpoint), so nothing listens on that
        # port — probing it would paint the chip red on a correctly
        # configured host. The endpoint itself is not reachable from this
        # container by design; `make doctor` covers it host-side.
        probe = f"http://host.docker.internal:{llm_port}/v1/models"
        headers = {"Authorization": f"Bearer {llm_key}"} if llm_key else None
        if llm_direct:
            add("inference-proxy", "Agent LLM route (inference proxy)", "skip",
                "LLM_DIRECT=1 — sandbox talks to the endpoint directly; "
                "check it with `make doctor` on the host")
        else:
            try:
                r = await client.get(probe, headers=headers)
                if llm_key:
                    add("inference-proxy", "Agent LLM route (inference proxy)",
                        "ok" if r.status_code == 200 else "fail", f"{probe} -> {r.status_code}")
                else:
                    add("inference-proxy", "Agent LLM route (inference proxy)",
                        "skip",
                        f"{probe} -> {r.status_code} — LLM_API_KEY unset in the gateway; run `make doctor` on the host")
            except httpx.HTTPError as e:
                add("inference-proxy", "Agent LLM route (inference proxy)", "fail",
                    f"{probe} unreachable ({type(e).__name__}) — `sudo systemctl restart nginx` / `make doctor`")

        # Wake hook — a bad token must 401: forward alive AND auth wired.
        if hook_url:
            try:
                r = await client.post(f"{hook_url.rstrip('/')}/hooks/wake",
                                      headers={"Authorization": "Bearer doctor-probe"})
                add("wake-hook", "Agent wake hook", "ok" if r.status_code == 401 else "fail",
                    f"POST /hooks/wake -> {r.status_code} (want 401)")
            except httpx.HTTPError as e:
                add("wake-hook", "Agent wake hook", "fail",
                    f"{hook_url}/hooks/wake unreachable ({type(e).__name__}) — `nemoclaw <sandbox> recover` + `make hook-relay`")
        else:
            add("wake-hook", "Agent wake hook", "skip", "OPENCLAW_HOOK_URL unset — webhook wake-up off (cron-only)")

        # Terminal daemon — host process (ADR-012).
        if term_ws and term_enabled != "0":
            base_http = term_ws.replace("wss://", "https://").replace("ws://", "http://").rsplit("/", 1)[0]
            try:
                r = await client.get(f"{base_http}/healthz")
                add("terminal", "Terminal daemon", "ok" if r.status_code == 200 else "fail",
                    f"{base_http}/healthz -> {r.status_code}")
            except httpx.HTTPError as e:
                add("terminal", "Terminal daemon", "fail",
                    f"{base_http}/healthz unreachable ({type(e).__name__}) — `make terminal` / `make doctor-fix`")
        else:
            add("terminal", "Terminal daemon", "skip", "terminal feature off")

    return {
        "healthy": not any(c["status"] == "fail" for c in checks),
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "checks": checks,
    }


# ── Agent status ─────────────────────────────────────────────────────────────

# "Is the agent configured (and therefore monitoring)" from the Gateway's
# vantage point. Onboarding (deploy/scripts/onboard-openclaw.sh) is one
# set -e script and the webhook (step 5) is configured LAST — after the
# Agent runtime (step 2), Soul/AGENTS/skills seeding (step 4), tool
# lockdown and the safety-net cron. So a wake hook that answers 401 on a
# bad token proves the whole onboarding completed, Agent + Soul + Skills
# included. Before that (fresh VM, aborted onboarding) or when the
# relay/forward is dead, the probe is refused — and the dashboard must not
# show a green "scanning fleet" ticker claiming monitoring is happening.
# (The 2026-08-28 incident was exactly this class: a dead agent route
# presenting as a quiet, healthy-looking fleet.)


@router.get("/api/agent/status")
async def agent_status(request: Request) -> dict:
    """Agent runtime + configuration state (see the section comment).

    ``configured`` is true only when the wake hook answers 401 on a bad
    token (hooks enabled + token wired = onboarding complete). The agent
    posts activity only while handling faults, so a stale
    ``last_activity_ts`` is normal while the fleet is quiet — it is a
    liveness display, not the configured signal.
    """
    import os

    hook_url = os.environ.get("OPENCLAW_HOOK_URL", "")
    configured = False
    detail = "OPENCLAW_HOOK_URL unset — onboarding incomplete (make bootstrap)"
    if hook_url:
        try:
            async with httpx.AsyncClient(timeout=_LAB_HEALTH_TIMEOUT) as client:
                r = await client.post(
                    f"{hook_url.rstrip('/')}/hooks/wake",
                    headers={"Authorization": "Bearer doctor-probe"},
                )
            if r.status_code == 401:
                configured = True
                detail = "wake hook auth alive (401 on bad token)"
            else:
                detail = f"POST /hooks/wake -> {r.status_code} (want 401) — hooks not wired"
        except httpx.HTTPError as e:
            detail = f"wake hook unreachable ({type(e).__name__}) — no agent, or relay/forward dead"

    evts = await _store(request).list_activity_events()
    last = max(e.ts for e in evts).isoformat() if evts else None

    return {"configured": configured, "detail": detail, "last_activity_ts": last}


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
    remediation_step_labels: list[str] = Field(default_factory=list)


class UpdateStatusRequest(BaseModel):
    status: FaultEventStatus


class UpdateDiagnosisRequest(BaseModel):
    """Agent: diagnosis fields discovered during the investigation."""

    error_signature: str | None = None
    analysis: str | None = None
    kb_article_id: str | None = None
    kb_title: str | None = None
    kb_score: float | None = None


class DecisionRequest(BaseModel):
    decision: ApprovalDecision


def _impact_for_scenario(request: Request, scenario_id: str, asset_id: str,
                         step_count: int) -> ScenarioImpact:
    """Pack-provided impact assessment, or a synthesised generic fallback."""
    loaded = request.app.state.loaded_pack
    scenario = loaded.scenarios_by_id.get(scenario_id)
    if scenario is not None and scenario.impact is not None:
        return scenario.impact
    return ScenarioImpact(
        summary=f"Execute {step_count} approved remediation steps on {asset_id}.",
        workload_impact=f"Workloads on {asset_id} may be interrupted while remediation runs.",
        service_risk="Single-asset action — no cluster-wide changes are made.",
        estimated_duration="~5–10 minutes",
    )


_MAX_STEP_DETAIL_CHARS = 160


def _kb_step_detail(body_md: str, step_id: str) -> str | None:
    """First prose sentence of the KB article's `### Step N — … (`step_id`)` section.

    The pack KB articles tag each resolution-step heading with its step id in
    backticks; the line(s) immediately after the heading explain what the step
    does before the first code block. Returns None when the article doesn't
    follow that structure — callers fall back to the scenario's plain label.
    """
    lines = body_md.splitlines()
    heading_idx = next(
        (
            i
            for i, ln in enumerate(lines)
            if ln.lstrip().startswith("###") and f"(`{step_id}`)" in ln
        ),
        None,
    )
    if heading_idx is None:
        return None
    for ln in lines[heading_idx + 1:]:
        s = ln.strip()
        if not s:
            continue
        if s.startswith(("#", "```", "|", ">")):
            break  # next section / code / table before any prose — give up
        # Accept a plain paragraph or the first list item.
        s = s.lstrip("-*0123456789. ").strip()
        if not s:
            continue
        sentence = s.split(". ")[0].rstrip(".:").strip()
        # Drop a dangling connective when the sentence led into a code block
        # ("Confirm with the owner, then:" → "Confirm with the owner").
        if sentence.endswith(" then"):
            sentence = sentence[: -len(" then")].rstrip(",;: ")
        if not sentence:
            break
        if len(sentence) > _MAX_STEP_DETAIL_CHARS:
            sentence = sentence[: _MAX_STEP_DETAIL_CHARS - 1].rstrip(",;: ") + "…"
        return sentence
    return None


def _step_labels_for_scenario(request: Request, scenario_id: str) -> list[str]:
    """Proposed-remediation labels for the Operator Dashboard.

    Base labels come from the scenario; each is enriched with the opening
    sentence of the matching resolution step in the scenario's own KB article
    ("<label> — <what the step actually does>"), so the dashboard's proposed
    steps read like the KB runbook rather than terse step names.
    """
    loaded = request.app.state.loaded_pack
    scenario = loaded.scenarios_by_id.get(scenario_id)
    if scenario is None:
        return []
    labels = [s.label for s in scenario.remediation_steps]
    kb_id = scenario.kb_article_ref.rsplit("/", 1)[-1].removesuffix(".md")
    article = loaded.kb_articles.get(kb_id)
    if article is None:
        return labels
    out: list[str] = []
    for step in scenario.remediation_steps:
        detail = _kb_step_detail(article.body_md, step.id)
        if detail is None:
            out.append(step.label)
        else:
            period = "" if detail.endswith("…") else "."
            out.append(f"{step.label} — {detail}{period}")
    return out


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
    """Agent: register a detected fault event. Triggers an SSE notification.

    When the caller omits remediation_step_labels (the OpenClaw plugin has no
    pack access), they default to the scenario's step labels from the
    Gateway's own loaded pack.
    """
    store = _store(request)
    step_labels = body.remediation_step_labels
    if not step_labels:
        step_labels = _step_labels_for_scenario(request, body.scenario_id)
    evt = await store.create_fault_event(
        scenario_id=body.scenario_id,
        asset_id=body.asset_id,
        kb_article_id=body.kb_article_id,
        log_extract=body.log_extract,
        remediation_step_labels=step_labels,
        impact=_impact_for_scenario(
            request, body.scenario_id, body.asset_id, len(step_labels)
        ),
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

    # When a fault resolves, return the asset to healthy and clear its fault link.
    if body.status == FaultEventStatus.resolved and updated.asset_id:
        if await store.has_asset(updated.asset_id):
            asset = await store.get_asset(updated.asset_id)
            await store.set_asset(AssetRecord(
                id=asset.id,
                type=asset.type,
                state=AssetState.healthy,
                active_fault_event_id=None,
            ))
            await store.sse.publish("asset", {
                "id": updated.asset_id,
                "state": "healthy",
                "active_fault_event_id": None,
            })

    await store.sse.publish("fault", updated.model_dump(mode="json"))
    return updated.model_dump(mode="json")


@router.patch("/api/faults/{fault_id}/diagnosis")
async def update_fault_diagnosis(
    fault_id: str, body: UpdateDiagnosisRequest, request: Request
) -> dict:
    """Agent: record diagnosis fields as the investigation progresses [server→server].

    Partial update — only fields present in the body are written. The Operator
    Dashboard renders these directly (signature, KB match, agent assessment).
    """
    store = _store(request)
    fields = body.model_dump(exclude_none=True)
    updated = await store.update_fault_fields(fault_id, **fields)
    if updated is None:
        raise HTTPException(status_code=404, detail="fault_not_found")
    await store.sse.publish("fault", updated.model_dump(mode="json"))
    return updated.model_dump(mode="json")


@router.post("/api/faults/{fault_id}/decision")
async def post_decision(fault_id: str, body: DecisionRequest, request: Request) -> dict:
    """Human: approve or deny remediation.

    On approval (ADR-011 — the Gateway is the execution trigger):
      1. Registers fault event + allowed steps in MCP Tools
      2. Mints a single-use approval token in MCP Tools
      3. Pushes SSE decision event
      4. Starts remediation execution immediately (background task) — no
         polling, no LLM; the token goes straight into the trusted
         server-to-server remediation.execute call
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
        step_labels = {
            s["id"]: s.get("label", s["id"])
            for s in scenario_data.get("remediation_steps", [])
        }
    except httpx.HTTPError:
        # Fall back to empty allowlist — remediation will fail step validation
        allowed_step_ids = []
        step_labels = {}

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

    # Keep the token retrievable over the trusted path for audit/API compat.
    await store.set_pending_token(fault_id, token_str)

    await store.sse.publish("decision", {
        "fault_event_id": fault_id,
        "decision": "approved",
    })

    # Execute immediately (ADR-011): background task so this POST returns at
    # once; the executor narrates progress and resolves the fault over SSE.
    request.app.state.remediation_executor.start(
        store,
        fault_id=fault_id,
        asset_id=evt.asset_id,
        scenario_id=evt.scenario_id,
        step_ids=allowed_step_ids,
        step_labels=step_labels,
        token=token_str,
    )

    return {
        "decision": "approved",
        "fault_event_id": fault_id,
        "execution": "started",
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
                except TimeoutError:
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


# ── Presenter controls (proxy to orchestrator) ────────────────────────────────


async def _fire_agent_webhook(scenario_id: str, asset_id: str) -> None:
    """Wake the OpenClaw agent the instant a scenario is injected (ADR-011).

    Event-driven replacement for the retired agent's fixed-interval poll; a
    cron job on the agent side remains as the safety net. Best-effort: an
    unreachable agent must never fail scenario injection. No-op unless
    OPENCLAW_HOOK_URL and OPENCLAW_HOOK_TOKEN are configured.
    """
    import os

    hook_url = os.environ.get("OPENCLAW_HOOK_URL")
    hook_token = os.environ.get("OPENCLAW_HOOK_TOKEN")
    if not hook_url or not hook_token:
        return
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            await client.post(
                f"{hook_url.rstrip('/')}/hooks/wake",
                headers={"Authorization": f"Bearer {hook_token}"},
                json={
                    "text": (
                        f"fault-event: scenario {scenario_id} injected on {asset_id} — "
                        "run the Infrastructure Fault Response program now"
                    ),
                    "mode": "now",
                },
            )
    except httpx.HTTPError:
        pass


@router.get("/api/presenter/scenarios")
async def presenter_scenarios(request: Request):
    """List the active pack's scenarios — backs the lab guide's injection
    picker, so a presenter can demo a specific fault instead of rotating."""
    orch = _orchestrator_client(request)
    resp = await orch.get("/api/scenarios")
    resp.raise_for_status()
    return resp.json()


@router.post("/api/presenter/inject")
async def presenter_inject(request: Request, scenario: str | None = None):
    """Inject a fault scenario. Proxies to the orchestrator /api/run endpoint.

    Optional ``?scenario=<id>`` selects a specific scenario; omit to rotate.
    """
    orch = _orchestrator_client(request)
    if scenario:
        resp = await orch.post(f"/api/run/{scenario}")
    else:
        resp = await orch.post("/api/run")
    resp.raise_for_status()
    data = resp.json()
    await _fire_agent_webhook(data.get("scenario_id", ""), data.get("target_asset", ""))
    return data


@router.post("/api/presenter/reset")
async def presenter_reset(request: Request):
    """Reset all state: orchestrator rotation, gateway store, mcp-tools token/registry."""
    store = _store(request)
    orch = _orchestrator_client(request)
    mcp = _mcp_tools_client(request)

    # Clear orchestrator rotation
    orch_resp = await orch.post("/api/reset")
    orch_resp.raise_for_status()

    # Clear mcp-tools tokens + fault registry
    try:
        await mcp.post("/internal/reset")
    except httpx.HTTPError:
        pass

    # Clear gateway in-memory state + broadcast reset SSE
    await store.reset()

    # SSE: push fresh healthy asset state for all assets
    for asset in await store.list_assets():
        await store.sse.publish("asset", {
            "id": asset.id,
            "state": asset.state.value,
            "active_fault_event_id": None,
        })

    return {"status": "idle"}
