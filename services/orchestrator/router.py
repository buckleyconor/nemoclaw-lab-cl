"""Orchestrator API routes.

POST /api/run               Select next scenario (rotating), inject into Simulator.
POST /api/run/{scenario_id} Presenter override: force a specific scenario.
POST /api/reset             Clear all active faults; return to idle.
GET  /api/current           Active scenario per asset (null if idle).
GET  /api/scenarios         List all scenarios in the active pack.
GET  /api/assets/{id}/logs     Log bundle text for the active scenario on this asset.
GET  /api/assets/{id}/scenario Full scenario data for the active scenario on this asset.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from libs.common.models import RemediationStep
from libs.common.pack_loader import LoadedPack
from services.orchestrator.rotation import RotationPolicy
from services.orchestrator.simulator_client import SimulatorClient

router = APIRouter(prefix="/api", tags=["orchestrator"])


# ─────────────────────────────────────────────────────────────────────────────
# Response schemas
# ─────────────────────────────────────────────────────────────────────────────

class RunResponse(BaseModel):
    scenario_id: str
    target_asset: str
    fault_type: str
    error_signatures: list[str]
    kb_article_ref: str
    remediation_steps: list[RemediationStep]
    log_extract: str | None


class ScenarioSummary(BaseModel):
    id: str
    fault_type: str
    target_asset: str
    error_signatures: list[str]
    status: str  # "active" | "scaffold"


class ScenariosResponse(BaseModel):
    scenarios: list[ScenarioSummary]
    last_used: str | None


class CurrentResponse(BaseModel):
    # asset_id → RunResponse | None
    active: dict[str, RunResponse | None]


class LogsResponse(BaseModel):
    asset_id: str
    scenario_id: str
    log_text: str


# ─────────────────────────────────────────────────────────────────────────────
# Dependency helpers
# ─────────────────────────────────────────────────────────────────────────────

def _rotation(request: Request) -> RotationPolicy:
    return request.app.state.rotation  # type: ignore[no-any-return]


def _sim(request: Request) -> SimulatorClient:
    return request.app.state.sim_client  # type: ignore[no-any-return]


def _pack(request: Request) -> LoadedPack:
    return request.app.state.loaded_pack  # type: ignore[no-any-return]


def _active(request: Request) -> dict[str, str | None]:
    """Mutable dict {asset_id: scenario_id | None} on app state."""
    return request.app.state.active_scenarios  # type: ignore[no-any-return]


# ─────────────────────────────────────────────────────────────────────────────
# Internal helpers
# ─────────────────────────────────────────────────────────────────────────────

def _run_response(scenario_id: str, pack: LoadedPack) -> RunResponse:
    scenario = pack.scenarios_by_id[scenario_id]
    return RunResponse(
        scenario_id=scenario.id,
        target_asset=scenario.target_asset,
        fault_type=scenario.fault_type,
        error_signatures=scenario.error_signatures,
        kb_article_ref=scenario.kb_article_ref,
        remediation_steps=scenario.remediation_steps,
        log_extract=scenario.log_extract,
    )


async def _inject(
    scenario_id: str,
    pack: LoadedPack,
    sim: SimulatorClient,
    active: dict[str, str | None],
) -> RunResponse:
    """Clear any existing active faults then inject *scenario_id*."""
    # Clear currently active faults across all assets before injecting a new one.
    for asset_id, current_id in list(active.items()):
        if current_id is not None:
            await sim.clear(asset_id)
            active[asset_id] = None

    scenario = pack.scenarios_by_id[scenario_id]
    await sim.inject(scenario.target_asset, scenario_id)
    active[scenario.target_asset] = scenario_id
    return _run_response(scenario_id, pack)


# ─────────────────────────────────────────────────────────────────────────────
# Routes
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/run", response_model=RunResponse)
async def run_next(request: Request) -> RunResponse:
    """Select the next scenario in rotation and inject it into the Simulator."""
    rotation = _rotation(request)
    pack = _pack(request)
    sim = _sim(request)
    active = _active(request)

    scenario_id = rotation.next()
    return await _inject(scenario_id, pack, sim, active)


@router.post("/run/{scenario_id}", response_model=RunResponse)
async def run_specific(scenario_id: str, request: Request) -> RunResponse:
    """Presenter override: force a specific scenario regardless of rotation order."""
    rotation = _rotation(request)
    pack = _pack(request)
    sim = _sim(request)
    active = _active(request)

    try:
        rotation.force(scenario_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    return await _inject(scenario_id, pack, sim, active)


@router.post("/reset")
async def reset(request: Request) -> dict[str, str]:
    """Clear all active faults and return all assets to idle."""
    sim = _sim(request)
    active = _active(request)
    rotation = _rotation(request)

    for asset_id, current_id in list(active.items()):
        if current_id is not None:
            await sim.clear(asset_id)
            active[asset_id] = None

    rotation.reset()
    return {"status": "idle"}


@router.get("/current", response_model=CurrentResponse)
async def get_current(request: Request) -> CurrentResponse:
    """Return the active scenario for each asset (None if idle)."""
    pack = _pack(request)
    active = _active(request)

    result: dict[str, RunResponse | None] = {}
    for asset_id in active:
        sid = active[asset_id]
        result[asset_id] = _run_response(sid, pack) if sid else None

    return CurrentResponse(active=result)


@router.get("/scenarios", response_model=ScenariosResponse)
async def list_scenarios(request: Request) -> ScenariosResponse:
    """List all scenarios in the active pack (for the presenter UI)."""
    pack = _pack(request)
    rotation = _rotation(request)
    return ScenariosResponse(
        scenarios=[
            ScenarioSummary(
                id=s.id,
                fault_type=s.fault_type,
                target_asset=s.target_asset,
                error_signatures=s.error_signatures,
                status=s.status,
            )
            for s in pack.scenarios
        ],
        last_used=rotation.last_used,
    )


@router.get("/assets/{asset_id}/logs", response_model=LogsResponse)
async def get_logs(asset_id: str, request: Request) -> LogsResponse:
    """Return the log bundle text for the active scenario on *asset_id*.

    Called by the ``logs`` MCP tool (M4) when the agent calls ``logs.get_bundle``.
    """
    pack = _pack(request)
    active = _active(request)

    scenario_id = active.get(asset_id)
    if scenario_id is None:
        raise HTTPException(
            status_code=404,
            detail=f"No active scenario for asset {asset_id!r}",
        )

    scenario = pack.scenarios_by_id[scenario_id]
    log_text = pack.log_bundles.get(scenario.log_bundle_ref, "")
    return LogsResponse(asset_id=asset_id, scenario_id=scenario_id, log_text=log_text)


@router.get("/assets/{asset_id}/scenario", response_model=RunResponse)
async def get_asset_scenario(asset_id: str, request: Request) -> RunResponse:
    """Return full scenario data for the active scenario on *asset_id*.

    Used by the ``remediation`` MCP tool to validate allowed step IDs.
    """
    pack = _pack(request)
    active = _active(request)

    scenario_id = active.get(asset_id)
    if scenario_id is None:
        raise HTTPException(
            status_code=404,
            detail=f"No active scenario for asset {asset_id!r}",
        )
    return _run_response(scenario_id, pack)
