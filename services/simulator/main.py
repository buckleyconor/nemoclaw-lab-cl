"""Simulator service entry point.

Loads the active pack at startup and wires the engine to the surface routers.
Use ``create_app(pack_dir=...)`` in tests to inject a specific pack directory.
"""

from __future__ import annotations

import os
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI

from libs.common.pack_loader import LoadedPack, load_pack
from services.simulator.control import router as control_router
from services.simulator.engine import SimulatorEngine
from services.simulator.surfaces.generic import router as generic_router
from services.simulator.surfaces.redfish import router as redfish_router


def _resolve_pack_dir(pack_dir: Path | None) -> Path:
    if pack_dir is not None:
        return pack_dir.resolve()
    pack_id = os.environ.get("PACK_ID", "datacenter-xe9680")
    root = Path(__file__).parent.parent.parent
    return (root / "packs" / pack_id).resolve()


def create_app(pack_dir: Path | None = None) -> FastAPI:
    """Create and configure the simulator FastAPI application.

    Args:
        pack_dir: Path to the pack directory. Defaults to ``packs/$PACK_ID``
                  (env var, default ``datacenter-xe9680``).
    """
    resolved_dir = _resolve_pack_dir(pack_dir)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
        loaded: LoadedPack = load_pack(resolved_dir)
        app.state.engine = SimulatorEngine(loaded)
        app.state.loaded_pack = loaded
        yield

    app = FastAPI(
        title="NemoClaw Simulator",
        version="0.1.0",
        description="Asset state engine + Redfish surface for the NemoClaw demo lab.",
        lifespan=lifespan,
    )
    app.include_router(redfish_router)
    app.include_router(generic_router)
    app.include_router(control_router)

    @app.get("/healthz", tags=["health"])
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    return app


# Module-level app for uvicorn: ``uvicorn services.simulator.main:app``
app = create_app()
