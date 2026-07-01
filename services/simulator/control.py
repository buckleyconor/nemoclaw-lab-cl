"""Simulator control API — inject/clear faults and query state.

These endpoints are simulator-internal; they are NOT Redfish and NOT
exposed to the agent. The Scenario Orchestrator (M3) calls them to
set up scenarios; the test suite uses them directly.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from services.simulator.engine import InjectedFault, SimulatorEngine

router = APIRouter(prefix="/control", tags=["control"])


def _engine(request: Request) -> SimulatorEngine:
    return request.app.state.engine  # type: ignore[no-any-return]


# ------------------------------------------------------------------
# Request / response schemas
# ------------------------------------------------------------------

class InjectRequest(BaseModel):
    asset_id: str
    scenario_id: str


class InjectResponse(BaseModel):
    asset_id: str
    scenario_id: str
    fault_type: str
    injected_at: str


class ClearRequest(BaseModel):
    asset_id: str


class StateResponse(BaseModel):
    states: dict[str, str]  # asset_id → "healthy" | "faulted"


# ------------------------------------------------------------------
# Endpoints
# ------------------------------------------------------------------

@router.post("/inject", response_model=InjectResponse)
async def inject_fault(
    body: InjectRequest,
    engine: SimulatorEngine = Depends(_engine),
) -> InjectResponse:
    """Inject a fault onto an asset using scenario emit data."""
    try:
        fault: InjectedFault = engine.inject(body.asset_id, body.scenario_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return InjectResponse(
        asset_id=fault.asset_id,
        scenario_id=fault.scenario_id,
        fault_type=fault.fault_type,
        injected_at=fault.injected_at.isoformat(),
    )


@router.post("/clear")
async def clear_fault(
    body: ClearRequest,
    engine: SimulatorEngine = Depends(_engine),
) -> dict[str, str]:
    """Return an asset to healthy state."""
    try:
        engine.clear(body.asset_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"asset_id": body.asset_id, "state": "healthy"}


@router.get("/state", response_model=StateResponse)
async def get_state(engine: SimulatorEngine = Depends(_engine)) -> StateResponse:
    """Return healthy/faulted state for all assets."""
    return StateResponse(states=engine.all_states())
