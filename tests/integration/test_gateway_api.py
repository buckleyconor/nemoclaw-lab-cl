"""Integration tests for Gateway API — I-04, I-05 and related flows.

The test stack wires three services together using ASGI transports:
  - Orchestrator (with FakeSimulatorClient)
  - MCP Tools (with real token store + fault registry)
  - Gateway (with injected httpx clients pointing at the above)

This lets tests verify the full human approval flow without any network calls.
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from httpx import ASGITransport

from services.gateway.main import create_app as create_gateway
from services.mcp_tools.fault_registry import FaultEventRegistry
from services.mcp_tools.main import create_app as create_mcp_tools
from services.mcp_tools.token_store import ApprovalTokenStore
from services.mcp_tools.tools.remediation import RemediationError, remediation_execute
from services.orchestrator.main import create_app as create_orchestrator
from services.orchestrator.simulator_client import FakeSimulatorClient

PACK_DIR = Path(__file__).parent.parent.parent / "packs" / "datacenter-xe9680"


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures — shared in-process services
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def fake_sim() -> FakeSimulatorClient:
    return FakeSimulatorClient()


@pytest.fixture(scope="module")
def orchestrator_app(fake_sim: FakeSimulatorClient):
    return create_orchestrator(pack_dir=PACK_DIR, sim_client=fake_sim)


@pytest.fixture(scope="module")
def token_store() -> ApprovalTokenStore:
    return ApprovalTokenStore()


@pytest.fixture(scope="module")
def fault_registry() -> FaultEventRegistry:
    return FaultEventRegistry()


@pytest.fixture(scope="module")
def mcp_tools_app(token_store, fault_registry):
    return create_mcp_tools(
        pack_dir=PACK_DIR,
        token_store=token_store,
        fault_registry=fault_registry,
    )


@pytest.fixture(scope="module")
def gateway_client(
    orchestrator_app, mcp_tools_app, token_store, fault_registry, fake_sim
) -> TestClient:
    # Wire Gateway's HTTP clients to the in-process ASGI apps.
    orch_transport = ASGITransport(app=orchestrator_app)
    mcp_transport = ASGITransport(app=mcp_tools_app)

    # Start the Orchestrator to inject a scenario before Gateway starts
    with TestClient(orchestrator_app) as orch_tc:
        orch_tc.post("/api/run/scn-gpu-xid-79")

    orch_client = httpx.AsyncClient(transport=orch_transport, base_url="http://orchestrator")
    mcp_client = httpx.AsyncClient(transport=mcp_transport, base_url="http://mcp-tools")

    # In-process remediation.execute for the Gateway's post-approval executor
    # (ADR-011) — same business logic the MCP wire reaches in production.
    async def execute_fn(fault_event_id: str, approval_token: str, step_ids: list[str]) -> dict:
        async def _clear(asset_id: str) -> None:
            await fake_sim.clear(asset_id)

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

    gateway_app = create_gateway(
        pack_dir=PACK_DIR,
        orchestrator_client=orch_client,
        mcp_tools_client=mcp_client,
        remediation_execute_fn=execute_fn,
    )
    with TestClient(gateway_app) as c:
        yield c


def _wait_for_status(gateway_client: TestClient, fault_id: str, status: str) -> dict:
    """Poll until the Gateway's background executor lands the fault on
    ``status`` (narration delays are zeroed in tests/conftest.py)."""
    import time

    fault: dict = {}
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        fault = gateway_client.get(f"/api/faults/{fault_id}").json()
        if fault.get("status") == status:
            return fault
        time.sleep(0.02)
    raise AssertionError(f"fault never reached {status}: last={fault.get('status')}")


@pytest.fixture(autouse=True)
def reset_state(gateway_client: TestClient, token_store, fault_registry, fake_sim) -> None:
    token_store.clear()
    fault_registry.clear()
    fake_sim.reset_history()
    yield


# ─────────────────────────────────────────────────────────────────────────────
# Health
# ─────────────────────────────────────────────────────────────────────────────


def test_gateway_healthz(gateway_client: TestClient) -> None:
    r = gateway_client.get("/healthz")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


# ─────────────────────────────────────────────────────────────────────────────
# Pack labels
# ─────────────────────────────────────────────────────────────────────────────


def test_get_pack_returns_labels(gateway_client: TestClient) -> None:
    r = gateway_client.get("/api/pack")
    assert r.status_code == 200
    data = r.json()
    assert data["id"] == "datacenter-xe9680"
    assert data["fleet_label"] == "Cluster Health"
    assert data["asset_noun"]["singular"] == "server"


# ─────────────────────────────────────────────────────────────────────────────
# Assets
# ─────────────────────────────────────────────────────────────────────────────


def test_get_assets_returns_fleet_grid(gateway_client: TestClient) -> None:
    r = gateway_client.get("/api/assets")
    assert r.status_code == 200
    data = r.json()
    asset_ids = {a["id"] for a in data["assets"]}
    assert "gpu-server-01" in asset_ids
    assert "gpu-server-02" in asset_ids


def test_assets_start_healthy(gateway_client: TestClient) -> None:
    r = gateway_client.get("/api/assets")
    for asset in r.json()["assets"]:
        assert asset["state"] == "healthy"


# ─────────────────────────────────────────────────────────────────────────────
# Fault events
# ─────────────────────────────────────────────────────────────────────────────


def test_create_fault_returns_id(gateway_client: TestClient) -> None:
    r = gateway_client.post(
        "/api/faults",
        json={
            "scenario_id": "scn-gpu-xid-79",
            "asset_id": "gpu-server-02",
            "log_extract": "GPU0: Xid 79: GPU has fallen off the bus",
        },
    )
    assert r.status_code == 201
    data = r.json()
    assert "id" in data
    assert data["scenario_id"] == "scn-gpu-xid-79"
    assert data["asset_id"] == "gpu-server-02"
    assert data["status"] == "detected"


def test_create_fault_updates_asset_state(gateway_client: TestClient) -> None:
    r = gateway_client.post(
        "/api/faults",
        json={"scenario_id": "scn-gpu-xid-79", "asset_id": "gpu-server-02"},
    )
    fault_id = r.json()["id"]
    assets_r = gateway_client.get("/api/assets")
    server_02 = next(a for a in assets_r.json()["assets"] if a["id"] == "gpu-server-02")
    assert server_02["state"] == "faulted"
    assert server_02["active_fault_event_id"] == fault_id


def test_create_fault_creates_notification(gateway_client: TestClient) -> None:
    gateway_client.post(
        "/api/faults",
        json={"scenario_id": "scn-gpu-xid-79", "asset_id": "gpu-server-02"},
    )
    n_r = gateway_client.get("/api/notifications")
    notifs = n_r.json()["notifications"]
    assert len(notifs) > 0
    assert n_r.json()["unread_count"] > 0


def test_get_fault_by_id(gateway_client: TestClient) -> None:
    create_r = gateway_client.post(
        "/api/faults",
        json={"scenario_id": "scn-gpu-xid-79", "asset_id": "gpu-server-02"},
    )
    fault_id = create_r.json()["id"]
    r = gateway_client.get(f"/api/faults/{fault_id}")
    assert r.status_code == 200
    assert r.json()["id"] == fault_id


def test_get_fault_404_for_unknown(gateway_client: TestClient) -> None:
    r = gateway_client.get("/api/faults/does-not-exist")
    assert r.status_code == 404


def test_update_fault_status(gateway_client: TestClient) -> None:
    create_r = gateway_client.post(
        "/api/faults",
        json={"scenario_id": "scn-gpu-xid-79", "asset_id": "gpu-server-02"},
    )
    fault_id = create_r.json()["id"]
    patch_r = gateway_client.patch(
        f"/api/faults/{fault_id}/status",
        json={"status": "diagnosing"},
    )
    assert patch_r.status_code == 200
    assert patch_r.json()["status"] == "diagnosing"


# ─────────────────────────────────────────────────────────────────────────────
# I-05: Deny flow
# ─────────────────────────────────────────────────────────────────────────────


def test_i05_deny_sets_status_denied(gateway_client: TestClient) -> None:
    """I-05: Human denies → status=denied, no token minted."""
    create_r = gateway_client.post(
        "/api/faults",
        json={"scenario_id": "scn-gpu-xid-79", "asset_id": "gpu-server-02"},
    )
    fault_id = create_r.json()["id"]

    deny_r = gateway_client.post(
        f"/api/faults/{fault_id}/decision",
        json={"decision": "denied"},
    )
    assert deny_r.status_code == 200
    assert deny_r.json()["decision"] == "denied"

    # Status should be denied
    fault_r = gateway_client.get(f"/api/faults/{fault_id}")
    assert fault_r.json()["status"] == "denied"


def test_i05_deny_no_token_available(gateway_client: TestClient) -> None:
    """I-05: After deny, token endpoint returns 404."""
    create_r = gateway_client.post(
        "/api/faults",
        json={"scenario_id": "scn-gpu-xid-79", "asset_id": "gpu-server-02"},
    )
    fault_id = create_r.json()["id"]
    gateway_client.post(f"/api/faults/{fault_id}/decision", json={"decision": "denied"})

    token_r = gateway_client.get(f"/api/faults/{fault_id}/token")
    assert token_r.status_code == 404


# ─────────────────────────────────────────────────────────────────────────────
# I-04: Approve → remediate full path
# ─────────────────────────────────────────────────────────────────────────────


def test_i04_approve_starts_execution_and_keeps_token_auditable(
    gateway_client: TestClient,
) -> None:
    """I-04: Human approves → the Gateway starts execution immediately
    (ADR-011) and the token stays retrievable over the trusted path."""
    create_r = gateway_client.post(
        "/api/faults",
        json={"scenario_id": "scn-gpu-xid-79", "asset_id": "gpu-server-02"},
    )
    fault_id = create_r.json()["id"]

    approve_r = gateway_client.post(
        f"/api/faults/{fault_id}/decision",
        json={"decision": "approved"},
    )
    assert approve_r.status_code == 200
    assert approve_r.json()["decision"] == "approved"
    assert approve_r.json()["execution"] == "started"

    token_r = gateway_client.get(f"/api/faults/{fault_id}/token")
    assert token_r.status_code == 200
    token = token_r.json()["token"]
    assert len(token) > 20
    _wait_for_status(gateway_client, fault_id, "resolved")


def test_i04_full_approve_remediate_flow(
    gateway_client: TestClient,
    fake_sim: FakeSimulatorClient,
) -> None:
    """I-04: Full flow: create fault → approve → Gateway executes
    remediation.execute server-side → fault resolved, asset cleared."""
    create_r = gateway_client.post(
        "/api/faults",
        json={
            "scenario_id": "scn-gpu-xid-79",
            "asset_id": "gpu-server-02",
            "log_extract": "GPU0: Xid 79: GPU has fallen off the bus",
        },
    )
    fault_id = create_r.json()["id"]

    gateway_client.post(f"/api/faults/{fault_id}/decision", json={"decision": "approved"})

    fault = _wait_for_status(gateway_client, fault_id, "resolved")
    assert fault["status"] == "resolved"
    assert "gpu-server-02" in fake_sim.cleared

    # The executor's narration reached the operator feed
    activity = gateway_client.get("/api/activity").json()["activity"]
    steps = {a["step"] for a in activity if a["fault_event_id"] == fault_id}
    assert "remediate" in steps and "resolved" in steps


@pytest.mark.asyncio
async def test_i04_token_single_use_after_remediation(
    gateway_client: TestClient,
    token_store: ApprovalTokenStore,
    fault_registry: FaultEventRegistry,
) -> None:
    """I-04 (SEC-03): the Gateway's execution consumes the token; any later
    replay with the same token fails."""
    create_r = gateway_client.post(
        "/api/faults",
        json={"scenario_id": "scn-gpu-xid-79", "asset_id": "gpu-server-02"},
    )
    fault_id = create_r.json()["id"]
    gateway_client.post(f"/api/faults/{fault_id}/decision", json={"decision": "approved"})
    token_str = gateway_client.get(f"/api/faults/{fault_id}/token").json()["token"]

    _wait_for_status(gateway_client, fault_id, "resolved")

    async def _noop_clear(asset_id: str) -> None:
        pass

    # Replay after the Gateway already executed — token consumed
    with pytest.raises(RemediationError) as exc_info:
        await remediation_execute(
            fault_event_id=fault_id,
            approval_token=token_str,
            step_ids=["drain_node", "gpu_reset", "verify_health"],
            token_store=token_store,
            fault_registry=fault_registry,
            clear_fn=_noop_clear,
        )
    assert exc_info.value.error == "token_consumed"


# ─────────────────────────────────────────────────────────────────────────────
# Notifications
# ─────────────────────────────────────────────────────────────────────────────


def test_mark_notification_read(gateway_client: TestClient) -> None:
    gateway_client.post(
        "/api/faults",
        json={"scenario_id": "scn-gpu-xid-79", "asset_id": "gpu-server-02"},
    )
    notifs = gateway_client.get("/api/notifications").json()["notifications"]
    notif_id = notifs[0]["id"]
    r = gateway_client.post(f"/api/notifications/{notif_id}/read")
    assert r.status_code == 200
    updated = gateway_client.get("/api/notifications").json()["notifications"]
    found = next(n for n in updated if n["id"] == notif_id)
    assert found["read"] is True


# ─────────────────────────────────────────────────────────────────────────────
# Activity feed
# ─────────────────────────────────────────────────────────────────────────────


def test_post_activity_appears_in_feed(gateway_client: TestClient) -> None:
    create_r = gateway_client.post(
        "/api/faults",
        json={"scenario_id": "scn-gpu-xid-79", "asset_id": "gpu-server-02"},
    )
    fault_id = create_r.json()["id"]

    gateway_client.post(
        "/api/agent/activity",
        json={
            "fault_event_id": fault_id,
            "step": "detect",
            "message": "Fault detected on gpu-server-02",
        },
    )
    gateway_client.post(
        "/api/agent/activity",
        json={"fault_event_id": fault_id, "step": "diagnose", "message": "Gathering logs"},
    )

    activity_r = gateway_client.get("/api/activity")
    assert activity_r.status_code == 200
    steps = [e["step"] for e in activity_r.json()["activity"]]
    assert "detect" in steps
    assert "diagnose" in steps


# ─────────────────────────────────────────────────────────────────────────────
# SC2 / SC3: Security constraints through Gateway HTTP path
# ─────────────────────────────────────────────────────────────────────────────


def test_sc3_token_endpoint_requires_prior_approval(gateway_client: TestClient) -> None:
    """SC3: Without a prior approval decision, no token exists."""
    create_r = gateway_client.post(
        "/api/faults",
        json={"scenario_id": "scn-gpu-xid-79", "asset_id": "gpu-server-02"},
    )
    fault_id = create_r.json()["id"]
    # No decision posted — token must not be available
    token_r = gateway_client.get(f"/api/faults/{fault_id}/token")
    assert token_r.status_code == 404


def test_list_faults_returns_all(gateway_client: TestClient) -> None:
    gateway_client.post(
        "/api/faults",
        json={"scenario_id": "scn-gpu-xid-79", "asset_id": "gpu-server-02"},
    )
    gateway_client.post(
        "/api/faults",
        json={"scenario_id": "scn-ecc-uncorrectable", "asset_id": "gpu-server-01"},
    )
    r = gateway_client.get("/api/faults")
    assert r.status_code == 200
    assert len(r.json()["faults"]) >= 2
