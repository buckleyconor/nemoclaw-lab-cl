"""E-01 — Full fault lifecycle with the Gateway as execution trigger (ADR-011).

Stack:
  Simulator     — in-process (TestClient)
  Orchestrator  — in-process (TestClient, FakeSimulatorClient)
  Gateway       — in-process (ASGITransport, injected remediation_execute_fn)
  MCP Tools     — business-logic functions called directly (no MCP wire)
  LLM           — none. The OpenClaw agent's tool calls are simulated by
                  driving the same Gateway/MCP surfaces the
                  nemoclaw-infra-tools plugin uses; genuine LLM tool-calling
                  coverage lives in the plugin's test suite and the ADR-011
                  validation spike, outside pytest (see ADR-011).

Verified:
  E-01  detect → diagnose → propose → approve → Gateway executes → resolved.
  SC1   Nothing executes without a human decision; the token is minted only
        by POST /decision and consumed server-side.
  SC2   After approval the fault resolves, the asset heals, and the operator
        feed shows the execution narration.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from httpx import ASGITransport

from libs.common.pack_loader import load_pack
from services.gateway.main import create_app as create_gateway
from services.mcp_tools.fault_registry import FaultEventRegistry
from services.mcp_tools.main import create_app as create_mcp_tools
from services.mcp_tools.token_store import ApprovalTokenStore
from services.mcp_tools.tools.remediation import (
    RemediationError,
    remediation_execute,
    remediation_propose,
)
from services.orchestrator.main import create_app as create_orchestrator
from services.orchestrator.simulator_client import FakeSimulatorClient
from services.simulator.main import create_app as create_simulator

PACK_DIR = Path(__file__).parent.parent.parent / "packs" / "datacenter-xe9680"

# Execution narration must not sleep in tests.
os.environ["GATEWAY_NARRATION_DELAY_SCALE"] = "0"


# ─────────────────────────────────────────────────────────────────────────────
# Module-scope fixtures (shared across tests; reset_state clears mutable state)
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def loaded_pack():
    return load_pack(PACK_DIR)


@pytest.fixture(scope="module")
def simulator_tc() -> TestClient:
    with TestClient(create_simulator(pack_dir=PACK_DIR)) as c:
        yield c


@pytest.fixture(scope="module")
def fake_sim() -> FakeSimulatorClient:
    return FakeSimulatorClient()


@pytest.fixture(scope="module")
def orchestrator_tc(fake_sim: FakeSimulatorClient) -> TestClient:
    with TestClient(create_orchestrator(pack_dir=PACK_DIR, sim_client=fake_sim)) as c:
        yield c


@pytest.fixture(scope="module")
def token_store() -> ApprovalTokenStore:
    return ApprovalTokenStore()


@pytest.fixture(scope="module")
def fault_registry() -> FaultEventRegistry:
    return FaultEventRegistry()


@pytest.fixture(scope="module")
def gateway_tc(orchestrator_tc, token_store, fault_registry, fake_sim) -> TestClient:
    orch_transport = ASGITransport(app=orchestrator_tc.app)
    orch_client = httpx.AsyncClient(transport=orch_transport, base_url="http://orchestrator")

    mcp_app = create_mcp_tools(
        pack_dir=PACK_DIR,
        token_store=token_store,
        fault_registry=fault_registry,
    )
    mcp_client = httpx.AsyncClient(
        transport=ASGITransport(app=mcp_app), base_url="http://mcp-tools"
    )

    # The Gateway's post-approval executor, wired to in-process business
    # logic — the same shape make_mcp_remediation_execute produces over the
    # MCP wire in production.
    async def execute_fn(fault_event_id: str, approval_token: str, step_ids: list[str]) -> dict:
        async def _clear(asset_id: str) -> bool:
            await fake_sim.clear(asset_id)
            return True

        try:
            return await remediation_execute(
                fault_event_id=fault_event_id,
                approval_token=approval_token,
                step_ids=step_ids,
                token_store=token_store,
                fault_registry=fault_registry,
                clear_fn=_clear,
            )
        except RemediationError as exc:
            return exc.to_dict()

    gw_app = create_gateway(
        pack_dir=PACK_DIR,
        orchestrator_client=orch_client,
        mcp_tools_client=mcp_client,
        remediation_execute_fn=execute_fn,
    )
    with TestClient(gw_app) as c:
        yield c


@pytest.fixture(autouse=True)
def reset_state(
    orchestrator_tc: TestClient,
    simulator_tc: TestClient,
    token_store: ApprovalTokenStore,
    fault_registry: FaultEventRegistry,
    fake_sim: FakeSimulatorClient,
) -> None:
    orchestrator_tc.post("/api/reset")
    simulator_tc.post("/control/clear", json={})
    token_store.clear()
    fault_registry.clear()
    fake_sim.reset_history()
    yield
    orchestrator_tc.post("/api/reset")


# ─────────────────────────────────────────────────────────────────────────────
# Helpers — drive the same surfaces the OpenClaw plugin drives
# ─────────────────────────────────────────────────────────────────────────────


def _gw(gateway_tc: TestClient) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=ASGITransport(app=gateway_tc.app), base_url="http://gateway"
    )


async def _register_and_propose(gw: httpx.AsyncClient, scenario_id: str, asset_id: str) -> str:
    """The plugin's deterministic side-work: register the fault on first log
    evidence, mark diagnosing, record the diagnosis, then propose (which the
    LLM triggers via the remediation_propose tool)."""
    r = await gw.post("/api/faults", json={
        "scenario_id": scenario_id,
        "asset_id": asset_id,
        "log_extract": "iDRAC: GPU3 has fallen off the bus (Xid 79)",
    })
    assert r.status_code == 201
    fault_id = r.json()["id"]

    await gw.patch(f"/api/faults/{fault_id}/status", json={"status": "diagnosing"})
    await gw.patch(f"/api/faults/{fault_id}/diagnosis", json={
        "error_signature": "Xid 79",
        "kb_article_id": "KB000123",
        "kb_title": "GPU Xid 79: GPU Has Fallen Off the Bus",
        "kb_score": 0.92,
    })

    result = await remediation_propose(
        gw,
        fault_event_id=fault_id,
        step_ids=[],
        summary="GPU3 on the asset has fallen off the bus (Xid 79); a drain, "
                "reset and health-check cycle is the validated KB remediation. "
                "Workloads on the node will be interrupted during the reset.",
    )
    assert result["status"] == "proposal_recorded"
    return fault_id


async def _wait_for_status(
    gw: httpx.AsyncClient, fault_id: str, status: str, timeout: float = 5.0
) -> dict:
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        r = await gw.get(f"/api/faults/{fault_id}")
        fault = r.json()
        if fault.get("status") == status:
            return fault
        await asyncio.sleep(0.02)
    raise AssertionError(f"fault {fault_id} never reached {status}: last={fault.get('status')}")


def _inject(orchestrator_tc: TestClient, simulator_tc: TestClient) -> tuple[str, str]:
    r = orchestrator_tc.post("/api/run/scn-gpu-xid-79")
    assert r.status_code == 200
    target_asset = r.json()["target_asset"]
    simulator_tc.post(
        "/control/inject",
        json={"asset_id": target_asset, "scenario_id": "scn-gpu-xid-79"},
    )
    return "scn-gpu-xid-79", target_asset


# ─────────────────────────────────────────────────────────────────────────────
# Tests
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_e01_full_happy_path_resolves(
    simulator_tc, orchestrator_tc, gateway_tc, fake_sim,
) -> None:
    """E-01 (SC2): inject → register → propose → human approves → Gateway
    executes immediately → resolved, asset healthy, feed narrated."""
    scenario_id, target_asset = _inject(orchestrator_tc, simulator_tc)

    async with _gw(gateway_tc) as gw:
        fault_id = await _register_and_propose(gw, scenario_id, target_asset)
        await _wait_for_status(gw, fault_id, "awaiting_approval")

        r = await gw.post(f"/api/faults/{fault_id}/decision", json={"decision": "approved"})
        assert r.status_code == 200
        assert r.json()["execution"] == "started"

        fault = await _wait_for_status(gw, fault_id, "resolved")

        # Diagnosis fields persisted for the Operator Dashboard
        assert fault["error_signature"] == "Xid 79"
        assert fault["kb_article_id"] == "KB000123"
        assert fault["kb_score"] is not None
        assert fault["analysis"], "agent summary missing from fault detail"
        assert fault["impact"] is not None
        for key in ("summary", "workload_impact", "service_risk", "estimated_duration"):
            assert fault["impact"][key], f"impact.{key} empty"
        # Step labels defaulted from the Gateway's own pack (plugin sends none)
        assert fault["remediation_step_labels"], "step labels not defaulted from pack"

        # Remediation actually ran against the simulator
        assert target_asset in fake_sim.cleared

        # Asset returned to healthy
        assets = (await gw.get("/api/assets")).json()["assets"]
        state = next(a["state"] for a in assets if a["id"] == target_asset)
        assert state == "healthy"

        # Execution narration reached the operator feed
        activity = (await gw.get("/api/activity")).json()["activity"]
        steps = [a["step"] for a in activity if a["fault_event_id"] == fault_id]
        assert "remediate" in steps and "resolved" in steps


@pytest.mark.asyncio
async def test_e01_sc1_no_decision_no_execution(
    simulator_tc, orchestrator_tc, gateway_tc, fake_sim,
) -> None:
    """SC1: without a human decision nothing executes and no token exists."""
    scenario_id, target_asset = _inject(orchestrator_tc, simulator_tc)

    async with _gw(gateway_tc) as gw:
        fault_id = await _register_and_propose(gw, scenario_id, target_asset)
        await _wait_for_status(gw, fault_id, "awaiting_approval")

        token_r = await gw.get(f"/api/faults/{fault_id}/token")
        assert token_r.status_code == 404

        await asyncio.sleep(0.1)  # give any (wrongly) started task time to act
        fault = (await gw.get(f"/api/faults/{fault_id}")).json()
        assert fault["status"] == "awaiting_approval"
        assert len(fake_sim.cleared) == 0


@pytest.mark.asyncio
async def test_e01_deny_flow(
    simulator_tc, orchestrator_tc, gateway_tc, fake_sim,
) -> None:
    """E-01: human denies → fault marked denied, no remediation executes."""
    scenario_id, target_asset = _inject(orchestrator_tc, simulator_tc)

    async with _gw(gateway_tc) as gw:
        fault_id = await _register_and_propose(gw, scenario_id, target_asset)
        await _wait_for_status(gw, fault_id, "awaiting_approval")

        r = await gw.post(f"/api/faults/{fault_id}/decision", json={"decision": "denied"})
        assert r.status_code == 200

        fault = await _wait_for_status(gw, fault_id, "denied")
        assert fault["status"] == "denied"
        await asyncio.sleep(0.1)
        assert len(fake_sim.cleared) == 0


@pytest.mark.asyncio
async def test_e01_approval_token_retrievable_for_audit(
    simulator_tc, orchestrator_tc, gateway_tc,
) -> None:
    """The trusted token endpoint still serves the minted token (audit/API
    compat) even though nothing polls it anymore."""
    scenario_id, target_asset = _inject(orchestrator_tc, simulator_tc)

    async with _gw(gateway_tc) as gw:
        fault_id = await _register_and_propose(gw, scenario_id, target_asset)
        await gw.post(f"/api/faults/{fault_id}/decision", json={"decision": "approved"})
        token_r = await gw.get(f"/api/faults/{fault_id}/token")
        assert token_r.status_code == 200
        assert token_r.json()["token"]
        await _wait_for_status(gw, fault_id, "resolved")
