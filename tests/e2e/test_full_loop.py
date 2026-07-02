"""E-01 — Full agent happy-path loop with stub LLM (ADR-010 tool-calling).

Stack:
  Simulator     — in-process (TestClient)
  Orchestrator  — in-process (TestClient, FakeSimulatorClient)
  Gateway       — in-process (ASGITransport, shared token_store + fault_registry)
  MCP Tools     — DirectAgentTools (bypass MCP protocol; call logic directly)
  LLM           — StubLLMClient (rule-based tool-calling policy, signature "Xid 79")

Verified:
  E-01  Full sequence: detect → diagnose → present → approve → remediate → resolved,
        driven by LLM tool calls (monitor → logs → notify → kb → propose).
  SC1   Agent never auto-remediates; always waits for a human-minted token.
  SC2   After approval the fault is fully resolved.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from httpx import ASGITransport

from agent.llm import StubLLMClient
from agent.loop import LoopResult, run_agent_loop
from agent.tools import DirectAgentTools
from libs.common.pack_loader import load_pack
from services.gateway.main import create_app as create_gateway
from services.mcp_tools.adapters.redfish import RedfishAdapter
from services.mcp_tools.fault_registry import FaultEventRegistry
from services.mcp_tools.kb_index import KBIndex
from services.mcp_tools.main import create_app as create_mcp_tools
from services.mcp_tools.token_store import ApprovalTokenStore
from services.orchestrator.main import create_app as create_orchestrator
from services.orchestrator.simulator_client import FakeSimulatorClient
from services.simulator.main import create_app as create_simulator

PACK_DIR = Path(__file__).parent.parent.parent / "packs" / "datacenter-xe9680"


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
def gateway_tc(orchestrator_tc, token_store, fault_registry) -> TestClient:
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

    gw_app = create_gateway(
        pack_dir=PACK_DIR,
        orchestrator_client=orch_client,
        mcp_tools_client=mcp_client,
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
    # Before each test: reset all mutable state
    orchestrator_tc.post("/api/reset")
    simulator_tc.post("/control/clear", json={})
    token_store.clear()
    fault_registry.clear()
    fake_sim.reset_history()
    yield
    # After: also reset so tests don't bleed state forward
    orchestrator_tc.post("/api/reset")


@pytest.fixture()
def stub_llm() -> StubLLMClient:
    return StubLLMClient(default_response="Xid 79")


# ─────────────────────────────────────────────────────────────────────────────
# Helper — build DirectAgentTools wired to in-process services
# ─────────────────────────────────────────────────────────────────────────────


def _build_tools(
    simulator_tc: TestClient,
    orchestrator_tc: TestClient,
    gateway_tc: TestClient,
    loaded_pack,
    token_store: ApprovalTokenStore,
    fault_registry: FaultEventRegistry,
    fake_sim: FakeSimulatorClient,
) -> DirectAgentTools:
    sim_transport = ASGITransport(app=simulator_tc.app)
    orch_transport = ASGITransport(app=orchestrator_tc.app)
    gw_transport = ASGITransport(app=gateway_tc.app)

    return DirectAgentTools(
        # RedfishAdapter now accepts _transport= to avoid real HTTP in tests
        adapter=RedfishAdapter("http://simulator", _transport=sim_transport),
        orchestrator_client=httpx.AsyncClient(
            transport=orch_transport, base_url="http://orchestrator"
        ),
        kb_index=KBIndex(loaded_pack),
        token_store=token_store,
        fault_registry=fault_registry,
        sim_client=fake_sim,
        # notify.post_activity / remediation.propose land on the Gateway (ADR-010)
        gateway_client=httpx.AsyncClient(
            transport=gw_transport, base_url="http://gateway"
        ),
    )


# ─────────────────────────────────────────────────────────────────────────────
# Tests
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_e01_no_fault_returns_no_fault(
    simulator_tc, orchestrator_tc, gateway_tc, loaded_pack,
    token_store, fault_registry, fake_sim, stub_llm,
) -> None:
    """E-01 base case: no active fault → loop exits immediately."""
    tools = _build_tools(simulator_tc, orchestrator_tc, gateway_tc, loaded_pack, token_store, fault_registry, fake_sim)
    gw_transport = ASGITransport(app=gateway_tc.app)
    async with httpx.AsyncClient(transport=gw_transport, base_url="http://gateway") as gw:
        result = await run_agent_loop(
            tools=tools,
            gateway_client=gw,
            llm_client=stub_llm,
            loaded_pack=loaded_pack,
            poll_interval=0.05,
        )
    assert result.status == "no_fault"


@pytest.mark.asyncio
async def test_e01_full_happy_path_resolves(
    simulator_tc, orchestrator_tc, gateway_tc, loaded_pack,
    token_store, fault_registry, fake_sim, stub_llm,
) -> None:
    """E-01 (SC1, SC2): inject fault → detect → diagnose → present → human approves → resolved."""
    # Inject scenario via Orchestrator (makes log bundle available)
    r = orchestrator_tc.post("/api/run/scn-gpu-xid-79")
    assert r.status_code == 200
    target_asset = r.json()["target_asset"]

    # Inject into simulator's Redfish surface
    simulator_tc.post("/control/inject", json={"asset_id": target_asset, "scenario_id": "scn-gpu-xid-79"})

    tools = _build_tools(simulator_tc, orchestrator_tc, gateway_tc, loaded_pack, token_store, fault_registry, fake_sim)
    gw_transport = ASGITransport(app=gateway_tc.app)
    loop_result: list[LoopResult] = []

    async def run_loop(gw: httpx.AsyncClient) -> None:
        loop_result.append(await run_agent_loop(
            tools=tools,
            gateway_client=gw,
            llm_client=stub_llm,
            loaded_pack=loaded_pack,
            approval_timeout=10.0,
            poll_interval=0.05,
        ))

    async def approve_when_ready(gw: httpx.AsyncClient) -> None:
        for _ in range(100):
            await asyncio.sleep(0.1)
            resp = await gw.get("/api/faults")
            for f in resp.json().get("faults", []):
                if f["status"] == "awaiting_approval":
                    await gw.post(f"/api/faults/{f['id']}/decision", json={"decision": "approved"})
                    return
        raise AssertionError("fault never reached awaiting_approval")

    async with httpx.AsyncClient(transport=gw_transport, base_url="http://gateway") as gw:
        await asyncio.gather(
            asyncio.create_task(run_loop(gw)),
            asyncio.create_task(approve_when_ready(gw)),
        )

    assert len(loop_result) == 1
    result = loop_result[0]
    assert result.status == "resolved", f"got: {result.status}"
    assert result.scenario_id == "scn-gpu-xid-79"

    # Diagnosis fields were persisted for the Operator Dashboard along the way
    fault_r = gateway_tc.get(f"/api/faults/{result.fault_id}")
    fault = fault_r.json()
    assert fault["error_signature"] == "Xid 79"
    assert fault["kb_article_id"] == "KB000123"
    assert fault["kb_title"], "KB title missing from fault detail"
    assert fault["kb_score"] is not None
    assert fault["analysis"], "agent summary missing from fault detail"
    # Impact assessment comes from pack content at fault creation
    assert fault["impact"] is not None
    for key in ("summary", "workload_impact", "service_risk", "estimated_duration"):
        assert fault["impact"][key], f"impact.{key} empty"


@pytest.mark.asyncio
async def test_e01_sc1_timeout_without_approval(
    simulator_tc, orchestrator_tc, gateway_tc, loaded_pack,
    token_store, fault_registry, fake_sim, stub_llm,
) -> None:
    """SC1/SC3: when no approval comes within timeout, loop returns 'timeout'; no remediation runs."""
    r = orchestrator_tc.post("/api/run/scn-gpu-xid-79")
    target_asset = r.json()["target_asset"]
    simulator_tc.post("/control/inject", json={"asset_id": target_asset, "scenario_id": "scn-gpu-xid-79"})

    tools = _build_tools(simulator_tc, orchestrator_tc, gateway_tc, loaded_pack, token_store, fault_registry, fake_sim)
    gw_transport = ASGITransport(app=gateway_tc.app)

    async with httpx.AsyncClient(transport=gw_transport, base_url="http://gateway") as gw:
        result = await run_agent_loop(
            tools=tools,
            gateway_client=gw,
            llm_client=stub_llm,
            loaded_pack=loaded_pack,
            approval_timeout=0.2,  # expires fast in tests
            poll_interval=0.05,
        )

    assert result.status == "timeout"
    # SC1: no remediation — FakeSimulatorClient records nothing cleared
    assert len(fake_sim.cleared) == 0


@pytest.mark.asyncio
async def test_e01_deny_flow(
    simulator_tc, orchestrator_tc, gateway_tc, loaded_pack,
    token_store, fault_registry, fake_sim, stub_llm,
) -> None:
    """E-01: human denies → loop returns 'denied', no remediation executes."""
    r = orchestrator_tc.post("/api/run/scn-gpu-xid-79")
    target_asset = r.json()["target_asset"]
    simulator_tc.post("/control/inject", json={"asset_id": target_asset, "scenario_id": "scn-gpu-xid-79"})

    tools = _build_tools(simulator_tc, orchestrator_tc, gateway_tc, loaded_pack, token_store, fault_registry, fake_sim)
    gw_transport = ASGITransport(app=gateway_tc.app)
    loop_result: list[LoopResult] = []

    async def run_loop(gw: httpx.AsyncClient) -> None:
        loop_result.append(await run_agent_loop(
            tools=tools,
            gateway_client=gw,
            llm_client=stub_llm,
            loaded_pack=loaded_pack,
            approval_timeout=10.0,
            poll_interval=0.05,
        ))

    async def deny_when_ready(gw: httpx.AsyncClient) -> None:
        for _ in range(100):
            await asyncio.sleep(0.1)
            resp = await gw.get("/api/faults")
            for f in resp.json().get("faults", []):
                if f["status"] == "awaiting_approval":
                    await gw.post(f"/api/faults/{f['id']}/decision", json={"decision": "denied"})
                    return
        raise AssertionError("fault never reached awaiting_approval")

    async with httpx.AsyncClient(transport=gw_transport, base_url="http://gateway") as gw:
        await asyncio.gather(
            asyncio.create_task(run_loop(gw)),
            asyncio.create_task(deny_when_ready(gw)),
        )

    assert loop_result[0].status == "denied"
    assert len(fake_sim.cleared) == 0
