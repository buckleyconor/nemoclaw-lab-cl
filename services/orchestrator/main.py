"""Scenario Orchestrator service entry point.

Loads the active pack at startup, initialises the rotation policy,
and wires the Simulator client. Use ``create_app(...)`` in tests to
inject a specific pack directory and/or a fake Simulator client.
"""

from __future__ import annotations

import os
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI

from libs.common.pack_loader import load_pack
from services.orchestrator.rotation import RotationPolicy
from services.orchestrator.router import router
from services.orchestrator.simulator_client import (
    HttpSimulatorClient,
    SimulatorClient,
)


def _resolve_pack_dir(pack_dir: Path | None) -> Path:
    if pack_dir is not None:
        return pack_dir.resolve()
    pack_id = os.environ.get("PACK_ID", "datacenter-xe9680")
    root = Path(__file__).parent.parent.parent
    return (root / "packs" / pack_id).resolve()


def create_app(
    pack_dir: Path | None = None,
    sim_client: SimulatorClient | None = None,
) -> FastAPI:
    """Create the Orchestrator FastAPI application.

    Args:
        pack_dir:   Path to the pack directory.  Defaults to ``packs/$PACK_ID``.
        sim_client: Simulator client.  Defaults to ``HttpSimulatorClient``
                    pointed at ``$SIMULATOR_URL``.  Pass a ``FakeSimulatorClient``
                    in tests to avoid network calls.
    """
    resolved_dir = _resolve_pack_dir(pack_dir)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
        loaded = load_pack(resolved_dir)
        app.state.loaded_pack = loaded

        # Build initial active-scenario state: all assets start idle.
        app.state.active_scenarios = {a.id: None for a in loaded.pack.assets}

        # Rotation policy over the loaded scenario ids.
        app.state.rotation = RotationPolicy([s.id for s in loaded.scenarios])

        # Simulator client — injected for tests, read from env otherwise.
        if sim_client is not None:
            app.state.sim_client = sim_client
        else:
            sim_url = os.environ.get("SIMULATOR_URL", "http://localhost:8003")
            app.state.sim_client = HttpSimulatorClient(sim_url)

        yield

    app = FastAPI(
        title="NemoClaw Orchestrator",
        version="0.1.0",
        description="Scenario selection, rotation, and fault injection for the NemoClaw demo lab.",
        lifespan=lifespan,
    )
    app.include_router(router)

    @app.get("/healthz", tags=["health"])
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    return app


# Module-level app for uvicorn: ``uvicorn services.orchestrator.main:app``
app = create_app()
