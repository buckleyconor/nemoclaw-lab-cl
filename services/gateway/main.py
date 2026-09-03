"""Gateway service — HITL approval gate, SSE, notification inbox, activity feed.

HTTP clients for Orchestrator and MCP Tools are injected via ``create_app()`` so tests
can pass ASGI transports instead of real network clients.

Set REDIS_URL to enable Redis-backed shared state (required for multi-pod / 30-user
deployments). When unset, state lives in process memory (single-process dev mode).
"""

from __future__ import annotations

import os
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from libs.common.models import AssetState
from libs.common.pack_loader import load_pack
from services.gateway.executor import (
    RemediationExecuteFn,
    RemediationExecutor,
    make_mcp_remediation_execute,
)
from services.gateway.router import router
from services.gateway.store import AssetRecord, GatewayStore
from services.gateway.terminal import terminal_router


def _resolve_pack_dir(pack_dir: Path | None) -> Path:
    if pack_dir is not None:
        return pack_dir.resolve()
    pack_id = os.environ.get("PACK_ID", "datacenter-xe9680")
    root = Path(__file__).parent.parent.parent
    return (root / "packs" / pack_id).resolve()


def create_app(
    pack_dir: Path | None = None,
    orchestrator_client: httpx.AsyncClient | None = None,
    mcp_tools_client: httpx.AsyncClient | None = None,
    remediation_execute_fn: RemediationExecuteFn | None = None,
) -> FastAPI:
    """Create the Gateway FastAPI application.

    Args:
        pack_dir:             Pack directory.  Defaults to ``packs/$PACK_ID``.
        orchestrator_client:  Injected httpx client for Orchestrator (tests).
        mcp_tools_client:     Injected httpx client for MCP Tools (tests).
        remediation_execute_fn: Injected remediation.execute call (tests).
                              Defaults to a Streamable HTTP MCP call against
                              $MCP_TOOLS_URL (ADR-011).
    """
    resolved_dir = _resolve_pack_dir(pack_dir)
    orch_url = os.environ.get("ORCHESTRATOR_URL", "http://localhost:8002")
    mcp_url = os.environ.get("MCP_TOOLS_URL", "http://localhost:8004")
    redis_url = os.environ.get("REDIS_URL")
    redis_prefix = os.environ.get("REDIS_KEY_PREFIX", "nemoclaw")
    narration_delay_scale = float(os.environ.get("GATEWAY_NARRATION_DELAY_SCALE", "1.0"))

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
        loaded = load_pack(resolved_dir)

        if redis_url:
            from services.gateway.redis_store import RedisGatewayStore

            store = RedisGatewayStore(redis_url, prefix=redis_prefix)
        else:
            store = GatewayStore()

        # Seed asset records from pack (all healthy at startup)
        for pack_asset in loaded.pack.assets:
            await store.set_asset(
                AssetRecord(
                    id=pack_asset.id,
                    type=loaded.pack.asset_noun.singular,
                    state=AssetState.healthy,
                )
            )

        app.state.store = store
        app.state.loaded_pack = loaded

        # HTTP clients — use injected ones in tests, real URLs in production
        orch = orchestrator_client or httpx.AsyncClient(base_url=orch_url)
        mcp = mcp_tools_client or httpx.AsyncClient(base_url=mcp_url)
        app.state.orchestrator_client = orch
        app.state.mcp_tools_client = mcp

        # Post-approval execution trigger (ADR-011) — deterministic harness
        # code; the LLM has no path to it.
        execute_fn = remediation_execute_fn or make_mcp_remediation_execute(mcp_url)
        app.state.remediation_executor = RemediationExecutor(
            execute_fn=execute_fn,
            delay_scale=narration_delay_scale,
        )

        yield

        store_close = getattr(store, "aclose", None)
        if store_close is not None:
            await store_close()
        if orchestrator_client is None:
            await orch.aclose()
        if mcp_tools_client is None:
            await mcp.aclose()

    app = FastAPI(
        title="NemoClaw Gateway",
        version="0.1.0",
        description="HITL approval gate, SSE feed, notification inbox, activity log.",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(router)
    app.include_router(terminal_router)

    @app.get("/healthz", tags=["health"])
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    # Serve lab guide docs at /lab/ (must be mounted before the SPA catch-all).
    docs_dir = Path(__file__).parent.parent.parent / "docs"
    if docs_dir.is_dir():
        app.mount("/lab", StaticFiles(directory=str(docs_dir), html=True), name="lab")

    # Serve the React SPA from the built ui/dist directory in prod.
    # During dev the Vite dev server handles this; the path won't exist yet.
    ui_dist = Path(__file__).parent.parent.parent / "ui" / "dist"
    if ui_dist.is_dir():
        app.mount("/", StaticFiles(directory=str(ui_dist), html=True), name="ui")

    return app


# Module-level app for uvicorn.
app = create_app()
