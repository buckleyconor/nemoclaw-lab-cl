"""MCP Tools service entry point.

Exposes four MCP tools over Streamable HTTP:
  monitor.list_events / monitor.get_asset / monitor.list_assets
  logs.get_bundle
  kb.search
  remediation.execute (token-gated, HITL)

Also exposes two FastAPI endpoints:
  GET  /healthz                           — liveness probe
  POST /internal/fault-events             — register a fault event (called by Gateway)
  POST /internal/tokens                   — mint an approval token (called by Gateway)
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncGenerator

from fastapi import FastAPI
from pydantic import BaseModel

from libs.common.models import ApprovalDecision
from libs.common.pack_loader import load_pack
from services.mcp_tools.adapters.redfish import RedfishAdapter
from services.mcp_tools.fault_registry import FaultEventRegistry
from services.mcp_tools.kb_index import KBIndex
from services.mcp_tools.server import _state, mcp
from services.mcp_tools.token_store import ApprovalTokenStore


def _resolve_pack_dir(pack_dir: Path | None) -> Path:
    if pack_dir is not None:
        return pack_dir.resolve()
    pack_id = os.environ.get("PACK_ID", "datacenter-xe9680")
    root = Path(__file__).parent.parent.parent
    return (root / "packs" / pack_id).resolve()


# ── Request bodies for internal endpoints ─────────────────────────────────────


class RegisterFaultEventRequest(BaseModel):
    fault_event_id: str
    asset_id: str
    scenario_id: str
    allowed_step_ids: list[str]


class MintTokenRequest(BaseModel):
    fault_event_id: str
    decision: ApprovalDecision
    decided_by: str = "human"


# ──────────────────────────────────────────────────────────────────────────────


def create_app(
    pack_dir: Path | None = None,
    simulator_url: str | None = None,
    orchestrator_url: str | None = None,
    token_store: ApprovalTokenStore | None = None,
    fault_registry: FaultEventRegistry | None = None,
) -> FastAPI:
    """Create the MCP Tools FastAPI application.

    Args:
        pack_dir:        Pack directory.  Defaults to ``packs/$PACK_ID``.
        simulator_url:   Simulator base URL.  Defaults to ``$SIMULATOR_URL``.
        orchestrator_url: Orchestrator base URL.  Defaults to ``$ORCHESTRATOR_URL``.
        token_store:     Inject a custom token store (tests).
        fault_registry:  Inject a custom fault registry (tests).
    """
    resolved_dir = _resolve_pack_dir(pack_dir)
    sim_url = simulator_url or os.environ.get("SIMULATOR_URL", "http://localhost:8003")
    orch_url = orchestrator_url or os.environ.get("ORCHESTRATOR_URL", "http://localhost:8002")

    # Create token_store and fault_registry at factory time so internal endpoints
    # can capture them in closures — this avoids depending on app.state which
    # is only populated after the lifespan starts (not when using ASGITransport).
    ts: ApprovalTokenStore = token_store if token_store is not None else ApprovalTokenStore()
    fr: FaultEventRegistry = fault_registry if fault_registry is not None else FaultEventRegistry()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
        loaded = load_pack(resolved_dir)

        # Populate the module-level state dict used by the MCP tool handlers.
        _state.update(
            adapter=RedfishAdapter(sim_url),
            orchestrator_url=orch_url,
            simulator_url=sim_url,
            kb_index=KBIndex(loaded),
            token_store=ts,
            fault_registry=fr,
            loaded_pack=loaded,
        )

        app.state.loaded_pack = loaded

        yield

        # Clean up module-level state when app shuts down (important for tests
        # that create multiple app instances in the same process).
        _state.clear()

    app = FastAPI(
        title="NemoClaw MCP Tools",
        version="0.1.0",
        lifespan=lifespan,
    )

    # Mount MCP Streamable HTTP transport at /mcp
    app.mount("/mcp", mcp.streamable_http_app())

    @app.get("/healthz", tags=["health"])
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/internal/fault-events", tags=["internal"])
    async def register_fault_event(req: RegisterFaultEventRequest) -> dict:
        """Register a fault event so remediation.execute can validate step ids."""
        fr.register(
            fault_event_id=req.fault_event_id,
            asset_id=req.asset_id,
            scenario_id=req.scenario_id,
            allowed_step_ids=req.allowed_step_ids,
        )
        return {"status": "registered", "fault_event_id": req.fault_event_id}

    @app.post("/internal/tokens", tags=["internal"])
    async def mint_token(req: MintTokenRequest) -> dict:
        """Mint a single-use approval token (called by Gateway in M5)."""
        token = ts.mint(
            fault_event_id=req.fault_event_id,
            decision=req.decision,
            decided_by=req.decided_by,
        )
        return {
            "token": token.token,
            "fault_event_id": token.fault_event_id,
            "decision": token.decision,
            "consumed": token.consumed,
        }

    return app


# Module-level app for uvicorn.
app = create_app()
