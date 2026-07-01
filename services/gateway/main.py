"""Gateway service — HITL approval gate, SSE, notification inbox, activity feed.

HTTP clients for Orchestrator and MCP Tools are injected via ``create_app()`` so tests
can pass ASGI transports instead of real network clients.

Set REDIS_URL to enable Redis-backed shared state (required for multi-pod / 30-user
deployments). When unset, state lives in process memory (single-process dev mode).
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncGenerator

import httpx
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from libs.common.models import AssetState
from libs.common.pack_loader import load_pack
from services.gateway.router import router
from services.gateway.store import AssetRecord, GatewayStore


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
) -> FastAPI:
    """Create the Gateway FastAPI application.

    Args:
        pack_dir:             Pack directory.  Defaults to ``packs/$PACK_ID``.
        orchestrator_client:  Injected httpx client for Orchestrator (tests).
        mcp_tools_client:     Injected httpx client for MCP Tools (tests).
    """
    resolved_dir = _resolve_pack_dir(pack_dir)
    orch_url = os.environ.get("ORCHESTRATOR_URL", "http://localhost:8002")
    mcp_url = os.environ.get("MCP_TOOLS_URL", "http://localhost:8004")
    redis_url = os.environ.get("REDIS_URL")
    redis_prefix = os.environ.get("REDIS_KEY_PREFIX", "nemoclaw")

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
            await store.set_asset(AssetRecord(
                id=pack_asset.id,
                type=loaded.pack.asset_noun.singular,
                state=AssetState.healthy,
            ))

        app.state.store = store
        app.state.loaded_pack = loaded

        # HTTP clients — use injected ones in tests, real URLs in production
        orch = orchestrator_client or httpx.AsyncClient(base_url=orch_url)
        mcp = mcp_tools_client or httpx.AsyncClient(base_url=mcp_url)
        app.state.orchestrator_client = orch
        app.state.mcp_tools_client = mcp

        yield

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

    app.include_router(router)

    @app.get("/healthz", tags=["health"])
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    # Serve the React SPA from the built ui/dist directory in prod.
    # During dev the Vite dev server handles this; the path won't exist yet.
    ui_dist = Path(__file__).parent.parent.parent / "ui" / "dist"
    if ui_dist.is_dir():
        app.mount("/", StaticFiles(directory=str(ui_dist), html=True), name="ui")

    return app


# Module-level app for uvicorn.
app = create_app()
