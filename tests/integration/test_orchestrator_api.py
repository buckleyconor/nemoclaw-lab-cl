"""Integration tests for the Scenario Orchestrator API.

Uses FakeSimulatorClient so these tests don't need a running Simulator.
Verifies rotation behaviour, presenter override, log serving, and reset.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from services.orchestrator.main import create_app
from services.orchestrator.simulator_client import FakeSimulatorClient

PACK_DIR = Path(__file__).parent.parent.parent / "packs" / "datacenter-xe9680"

ALL_SCENARIO_IDS = {
    "scn-gpu-xid-79",
    "scn-ecc-uncorrectable",
    "scn-psu-loss",
    "scn-nvlink-down",
    "scn-thermal-throttle",
}


@pytest.fixture(scope="module")
def fake_sim() -> FakeSimulatorClient:
    return FakeSimulatorClient()


@pytest.fixture(scope="module")
def client(fake_sim: FakeSimulatorClient) -> TestClient:
    app = create_app(pack_dir=PACK_DIR, sim_client=fake_sim)
    with TestClient(app) as c:
        yield c


@pytest.fixture(autouse=True)
def reset_between_tests(client: TestClient, fake_sim: FakeSimulatorClient) -> None:
    client.post("/api/reset")
    fake_sim.reset_history()
    yield
    client.post("/api/reset")
    fake_sim.reset_history()


# ─────────────────────────────────────────────────────────────────────────────
# Health
# ─────────────────────────────────────────────────────────────────────────────

def test_healthz(client: TestClient) -> None:
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


# ─────────────────────────────────────────────────────────────────────────────
# Scenarios listing
# ─────────────────────────────────────────────────────────────────────────────

def test_list_scenarios_returns_all_five(client: TestClient) -> None:
    r = client.get("/api/scenarios")
    assert r.status_code == 200
    ids = {s["id"] for s in r.json()["scenarios"]}
    assert ids == ALL_SCENARIO_IDS


def test_list_scenarios_last_used_is_null_initially(client: TestClient) -> None:
    r = client.get("/api/scenarios")
    assert r.json()["last_used"] is None


# ─────────────────────────────────────────────────────────────────────────────
# POST /api/run — rotation
# ─────────────────────────────────────────────────────────────────────────────

def test_run_returns_scenario_details(client: TestClient) -> None:
    r = client.post("/api/run")
    assert r.status_code == 200
    data = r.json()
    assert data["scenario_id"] in ALL_SCENARIO_IDS
    assert "target_asset" in data
    assert "error_signatures" in data
    assert len(data["remediation_steps"]) > 0


def test_run_calls_simulator_inject(client: TestClient, fake_sim: FakeSimulatorClient) -> None:
    r = client.post("/api/run")
    assert r.status_code == 200
    assert len(fake_sim.injected) == 1
    asset_id, scenario_id = fake_sim.injected[0]
    assert scenario_id == r.json()["scenario_id"]
    assert asset_id == r.json()["target_asset"]


def test_two_consecutive_runs_produce_different_scenarios(client: TestClient) -> None:
    """U-06 end-to-end: two POST /api/run calls must yield different scenario ids."""
    r1 = client.post("/api/run")
    r2 = client.post("/api/run")
    assert r1.json()["scenario_id"] != r2.json()["scenario_id"]


def test_run_clears_previous_fault_before_injecting(
    client: TestClient, fake_sim: FakeSimulatorClient
) -> None:
    """Second run clears the first asset's fault before injecting the new one."""
    r1 = client.post("/api/run")
    first_asset = r1.json()["target_asset"]
    fake_sim.reset_history()

    client.post("/api/run")
    # The first asset must have been cleared
    assert first_asset in fake_sim.cleared


def test_run_updates_last_used(client: TestClient) -> None:
    r = client.post("/api/run")
    scenario_id = r.json()["scenario_id"]
    r2 = client.get("/api/scenarios")
    assert r2.json()["last_used"] == scenario_id


# ─────────────────────────────────────────────────────────────────────────────
# POST /api/run/{scenario_id} — presenter override
# ─────────────────────────────────────────────────────────────────────────────

def test_run_specific_injects_correct_scenario(
    client: TestClient, fake_sim: FakeSimulatorClient
) -> None:
    r = client.post("/api/run/scn-psu-loss")
    assert r.status_code == 200
    assert r.json()["scenario_id"] == "scn-psu-loss"
    assert fake_sim.injected[-1][1] == "scn-psu-loss"


def test_run_specific_unknown_scenario_returns_404(client: TestClient) -> None:
    r = client.post("/api/run/scn-does-not-exist")
    assert r.status_code == 404


def test_run_specific_updates_rotation_last_used(client: TestClient) -> None:
    client.post("/api/run/scn-ecc-uncorrectable")
    r = client.get("/api/scenarios")
    assert r.json()["last_used"] == "scn-ecc-uncorrectable"


def test_run_specific_next_auto_avoids_forced(client: TestClient) -> None:
    """After a forced run, the next auto-rotation picks a different scenario."""
    client.post("/api/run/scn-gpu-xid-79")
    r = client.post("/api/run")  # auto-rotate
    assert r.json()["scenario_id"] != "scn-gpu-xid-79"


# ─────────────────────────────────────────────────────────────────────────────
# GET /api/current
# ─────────────────────────────────────────────────────────────────────────────

def test_current_is_null_initially(client: TestClient) -> None:
    r = client.get("/api/current")
    assert r.status_code == 200
    active = r.json()["active"]
    assert all(v is None for v in active.values())


def test_current_reflects_active_scenario(client: TestClient) -> None:
    run_r = client.post("/api/run")
    scenario_id = run_r.json()["scenario_id"]
    target_asset = run_r.json()["target_asset"]

    r = client.get("/api/current")
    active = r.json()["active"]
    assert active[target_asset]["scenario_id"] == scenario_id


# ─────────────────────────────────────────────────────────────────────────────
# POST /api/reset
# ─────────────────────────────────────────────────────────────────────────────

def test_reset_clears_active_scenarios(client: TestClient) -> None:
    client.post("/api/run")
    r = client.post("/api/reset")
    assert r.status_code == 200
    assert r.json()["status"] == "idle"

    r2 = client.get("/api/current")
    assert all(v is None for v in r2.json()["active"].values())


def test_reset_calls_simulator_clear(
    client: TestClient, fake_sim: FakeSimulatorClient
) -> None:
    run_r = client.post("/api/run")
    target_asset = run_r.json()["target_asset"]
    fake_sim.reset_history()

    client.post("/api/reset")
    assert target_asset in fake_sim.cleared


# ─────────────────────────────────────────────────────────────────────────────
# GET /api/assets/{id}/logs
# ─────────────────────────────────────────────────────────────────────────────

def test_get_logs_returns_bundle_for_active_scenario(client: TestClient) -> None:
    run_r = client.post("/api/run/scn-gpu-xid-79")
    target_asset = run_r.json()["target_asset"]

    r = client.get(f"/api/assets/{target_asset}/logs")
    assert r.status_code == 200
    data = r.json()
    assert data["scenario_id"] == "scn-gpu-xid-79"
    assert "Xid 79" in data["log_text"]


def test_get_logs_returns_404_when_idle(client: TestClient) -> None:
    r = client.get("/api/assets/gpu-server-01/logs")
    assert r.status_code == 404


# ─────────────────────────────────────────────────────────────────────────────
# GET /api/assets/{id}/scenario
# ─────────────────────────────────────────────────────────────────────────────

def test_get_asset_scenario_returns_remediation_steps(client: TestClient) -> None:
    run_r = client.post("/api/run/scn-gpu-xid-79")
    target_asset = run_r.json()["target_asset"]

    r = client.get(f"/api/assets/{target_asset}/scenario")
    assert r.status_code == 200
    data = r.json()
    assert data["scenario_id"] == "scn-gpu-xid-79"
    step_ids = [s["id"] for s in data["remediation_steps"]]
    assert "drain_node" in step_ids
    assert "gpu_reset" in step_ids


def test_get_asset_scenario_404_when_idle(client: TestClient) -> None:
    r = client.get("/api/assets/gpu-server-01/scenario")
    assert r.status_code == 404
