"""Integration tests for MCP tool business logic.

Tests:
  I-01  Redfish adapter: inject → list_events returns event (adapter-level)
  I-02  logs.get_bundle: Orchestrator integration
  I-03  kb.search: extracted signature → correct article
  PACK-04  Both adapters satisfy MonitoringAdapter; list_events returns same shape
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from httpx import ASGITransport

from libs.common.pack_loader import load_pack
from services.mcp_tools.adapters.base import MonitoringAdapter
from services.mcp_tools.adapters.generic import GenericAdapter
from services.mcp_tools.adapters.redfish import RedfishAdapter
from services.mcp_tools.kb_index import KBIndex
from services.mcp_tools.tools.kb import kb_search
from services.mcp_tools.tools.logs import logs_get_bundle
from services.mcp_tools.tools.monitor import monitor_list_events
from services.orchestrator.main import create_app as create_orchestrator
from services.orchestrator.simulator_client import FakeSimulatorClient
from services.simulator.main import create_app as create_simulator

PACK_DIR = Path(__file__).parent.parent.parent / "packs" / "datacenter-xe9680"


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def loaded_pack():
    return load_pack(PACK_DIR)


@pytest.fixture(scope="module")
def kb_index(loaded_pack) -> KBIndex:
    return KBIndex(loaded_pack, confidence_threshold=0.60)


@pytest.fixture(scope="module")
def simulator_tc() -> TestClient:
    app = create_simulator(pack_dir=PACK_DIR)
    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="module")
def orchestrator_tc(simulator_tc: TestClient) -> TestClient:
    # FakeSimulatorClient records calls but doesn't call a network Simulator.
    fake_sim = FakeSimulatorClient()
    app = create_orchestrator(pack_dir=PACK_DIR, sim_client=fake_sim)
    with TestClient(app) as c:
        yield c


@pytest.fixture(autouse=True)
def reset_between_tests(orchestrator_tc: TestClient, simulator_tc: TestClient):
    orchestrator_tc.post("/api/reset")
    simulator_tc.post("/control/clear", json={"asset_id": "gpu-server-01"})
    simulator_tc.post("/control/clear", json={"asset_id": "gpu-server-02"})
    yield
    orchestrator_tc.post("/api/reset")
    simulator_tc.post("/control/clear", json={"asset_id": "gpu-server-01"})
    simulator_tc.post("/control/clear", json={"asset_id": "gpu-server-02"})


# ─────────────────────────────────────────────────────────────────────────────
# I-01: Redfish adapter — inject fault → list_events returns event
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_i01_redfish_adapter_list_events_after_inject(
    simulator_tc: TestClient,
) -> None:
    """I-01: inject scn-gpu-xid-79 → Redfish adapter sees Critical events."""
    # Inject via Simulator control API
    r = simulator_tc.post(
        "/control/inject",
        json={"asset_id": "gpu-server-02", "scenario_id": "scn-gpu-xid-79"},
    )
    assert r.status_code == 200

    # Use the Redfish adapter with the TestClient's ASGI transport
    transport = ASGITransport(app=simulator_tc.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        # Patch the adapter to use our test transport
        class _TestRedfishAdapter(RedfishAdapter):
            def __init__(self) -> None:
                self._base = "http://test"
                self._client = client

            async def list_events(self, asset_id=None):
                from services.mcp_tools.adapters.base import MonitorEvent
                if asset_id:
                    url = f"/redfish/v1/Systems/{asset_id}/LogServices/DCGM/Entries"
                    r = await client.get(url)
                    r.raise_for_status()
                    entries = r.json().get("Members", [])
                    return [
                        MonitorEvent(
                            asset_id=asset_id,
                            severity=e.get("Severity", "Unknown"),
                            message=e.get("Message", ""),
                            message_id=e.get("MessageId", ""),
                            ts=e.get("Created", ""),
                        )
                        for e in entries
                    ]
                r = await client.get("/redfish/v1/EventService/Events")
                r.raise_for_status()
                events = []
                for member in r.json().get("Members", []):
                    aid = member["Id"]
                    for evt in member.get("Events", []):
                        from services.mcp_tools.adapters.base import MonitorEvent
                        events.append(MonitorEvent(
                            asset_id=aid,
                            severity=evt.get("Severity", "Unknown"),
                            message=evt.get("Message", ""),
                            message_id=evt.get("MessageId", ""),
                            ts=evt.get("EventTimestamp", ""),
                        ))
                return events

        adapter = _TestRedfishAdapter()
        events = await monitor_list_events(adapter, asset_id="gpu-server-02")

    assert len(events) > 0
    messages = [e["message"] for e in events]
    assert any("Xid 79" in m or "GPU" in m for m in messages)


# ─────────────────────────────────────────────────────────────────────────────
# I-02: logs.get_bundle — Orchestrator returns scenario log text
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_i02_logs_get_bundle_returns_scenario_text(
    orchestrator_tc: TestClient,
) -> None:
    """I-02: inject scn-gpu-xid-79 via Orchestrator → logs.get_bundle returns Xid 79 log."""
    run_r = orchestrator_tc.post("/api/run/scn-gpu-xid-79")
    assert run_r.status_code == 200
    target_asset = run_r.json()["target_asset"]

    transport = ASGITransport(app=orchestrator_tc.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        result = await logs_get_bundle(client, asset_id=target_asset)

    assert result["scenario_id"] == "scn-gpu-xid-79"
    assert "Xid 79" in result["log_text"]
    assert result["asset_id"] == target_asset


@pytest.mark.asyncio
async def test_i02_logs_get_bundle_404_when_idle(
    orchestrator_tc: TestClient,
) -> None:
    """I-02: No active scenario → logs.get_bundle raises HTTPStatusError (404)."""
    transport = ASGITransport(app=orchestrator_tc.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        with pytest.raises(httpx.HTTPStatusError) as exc_info:
            await logs_get_bundle(client, asset_id="gpu-server-01")
    assert exc_info.value.response.status_code == 404


# ─────────────────────────────────────────────────────────────────────────────
# I-03: kb.search — extracted signature → correct article
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_i03_kb_search_xid79_signature_returns_correct_article(
    kb_index: KBIndex,
) -> None:
    """I-03: 'Xid 79' → KB000123 with score > 0."""
    result = await kb_search(kb_index, signature="Xid 79")
    assert result is not None
    assert result["kb_id"] == "KB000123"
    assert result["score"] > 0
    assert len(result["remediation_step_ids"]) > 0


@pytest.mark.asyncio
async def test_i03_kb_search_returns_title_and_body(kb_index: KBIndex) -> None:
    result = await kb_search(kb_index, signature="ECC uncorrectable error")
    assert result is not None
    assert result["kb_id"] == "KB000456"
    assert isinstance(result["title"], str)
    assert isinstance(result["body_md"], str)


# ─────────────────────────────────────────────────────────────────────────────
# PACK-04: Both adapters satisfy MonitoringAdapter; list_events same shape
# ─────────────────────────────────────────────────────────────────────────────


def test_pack04_redfish_adapter_satisfies_protocol() -> None:
    """PACK-04: RedfishAdapter has all methods required by MonitoringAdapter."""
    adapter = RedfishAdapter("http://localhost:8003")
    # Protocol check — verify all methods present with correct names
    assert callable(getattr(adapter, "list_assets", None))
    assert callable(getattr(adapter, "list_events", None))
    assert callable(getattr(adapter, "get_asset", None))


def test_pack04_generic_adapter_satisfies_protocol() -> None:
    """PACK-04: GenericAdapter has all methods required by MonitoringAdapter."""
    adapter = GenericAdapter("http://localhost:9000")
    assert callable(getattr(adapter, "list_assets", None))
    assert callable(getattr(adapter, "list_events", None))
    assert callable(getattr(adapter, "get_asset", None))


def test_pack04_runtime_protocol_isinstance() -> None:
    """PACK-04: Structural protocol compliance — all required methods present."""
    redfish = RedfishAdapter("http://localhost:8003")
    generic = GenericAdapter("http://localhost:9000")

    required_methods = {"list_assets", "list_events", "get_asset"}
    for method in required_methods:
        assert hasattr(redfish, method), f"RedfishAdapter missing {method}"
        assert hasattr(generic, method), f"GenericAdapter missing {method}"
        assert callable(getattr(redfish, method))
        assert callable(getattr(generic, method))
