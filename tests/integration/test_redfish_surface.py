"""Integration tests for the Redfish surface — I-01 and supporting cases.

These tests run against a real (in-process) FastAPI app with the flagship
pack loaded. No network calls; the TestClient drives everything in-process.

I-01: Orchestrator injects fault into sim → redfish surface returns the event;
      asset health transitions from OK → Critical.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from services.simulator.main import create_app

PACK_DIR = Path(__file__).parent.parent.parent / "packs" / "datacenter-xe9680"


@pytest.fixture(scope="module")
def client() -> TestClient:
    app = create_app(pack_dir=PACK_DIR)
    with TestClient(app) as c:
        yield c


@pytest.fixture(autouse=True)
def reset_assets(client: TestClient):
    """Clear all faults between tests."""
    for asset_id in ("gpu-server-01", "gpu-server-02"):
        client.post("/control/clear", json={"asset_id": asset_id})
    yield
    for asset_id in ("gpu-server-01", "gpu-server-02"):
        client.post("/control/clear", json={"asset_id": asset_id})


# ------------------------------------------------------------------
# Healthz
# ------------------------------------------------------------------


def test_healthz(client: TestClient) -> None:
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


# ------------------------------------------------------------------
# Redfish service root
# ------------------------------------------------------------------


def test_service_root(client: TestClient) -> None:
    r = client.get("/redfish/v1/")
    assert r.status_code == 200
    data = r.json()
    assert "Systems" in data
    assert "EventService" in data


# ------------------------------------------------------------------
# Systems  (healthy baseline)
# ------------------------------------------------------------------


def test_systems_collection_lists_both_assets(client: TestClient) -> None:
    r = client.get("/redfish/v1/Systems")
    assert r.status_code == 200
    data = r.json()
    ids = [m["@odata.id"] for m in data["Members"]]
    assert "/redfish/v1/Systems/gpu-server-01" in ids
    assert "/redfish/v1/Systems/gpu-server-02" in ids


def test_system_health_ok_when_healthy(client: TestClient) -> None:
    r = client.get("/redfish/v1/Systems/gpu-server-02")
    assert r.status_code == 200
    assert r.json()["Status"]["Health"] == "OK"


def test_system_404_for_unknown_asset(client: TestClient) -> None:
    r = client.get("/redfish/v1/Systems/no-such-server")
    assert r.status_code == 404


# ------------------------------------------------------------------
# I-01: inject fault → Redfish surfaces the event
# ------------------------------------------------------------------


def test_i01_inject_transitions_system_health_to_critical(client: TestClient) -> None:
    """I-01: After fault injection, System health = Critical."""
    r = client.post(
        "/control/inject",
        json={
            "asset_id": "gpu-server-02",
            "scenario_id": "scn-gpu-xid-79",
        },
    )
    assert r.status_code == 200

    r = client.get("/redfish/v1/Systems/gpu-server-02")
    assert r.json()["Status"]["Health"] == "Critical"


def test_i01_log_entries_contain_fault_message(client: TestClient) -> None:
    """I-01: DCGM log entries contain the fault message after injection."""
    client.post(
        "/control/inject",
        json={
            "asset_id": "gpu-server-02",
            "scenario_id": "scn-gpu-xid-79",
        },
    )

    r = client.get("/redfish/v1/Systems/gpu-server-02/LogServices/DCGM/Entries")
    assert r.status_code == 200
    members = r.json()["Members"]
    assert len(members) > 0
    messages = [m["Message"] for m in members]
    assert any("Xid 79" in msg for msg in messages)


def test_i01_log_entries_have_correct_severity(client: TestClient) -> None:
    """I-01: DCGM log entries carry Critical severity."""
    client.post(
        "/control/inject",
        json={
            "asset_id": "gpu-server-02",
            "scenario_id": "scn-gpu-xid-79",
        },
    )
    r = client.get("/redfish/v1/Systems/gpu-server-02/LogServices/DCGM/Entries")
    members = r.json()["Members"]
    assert members[0]["Severity"] == "Critical"
    assert members[0]["MessageId"] == "OEM.1.0.GPUFault"


def test_i01_event_service_shows_active_fault(client: TestClient) -> None:
    """I-01: EventService/Events surfaces the active fault after injection."""
    client.post(
        "/control/inject",
        json={
            "asset_id": "gpu-server-02",
            "scenario_id": "scn-gpu-xid-79",
        },
    )
    r = client.get("/redfish/v1/EventService/Events")
    assert r.status_code == 200
    members = r.json()["Members"]
    asset_ids = [m["Id"] for m in members]
    assert "gpu-server-02" in asset_ids


def test_clear_returns_system_to_healthy(client: TestClient) -> None:
    """After clear, System health returns to OK and log entries are empty."""
    client.post(
        "/control/inject",
        json={
            "asset_id": "gpu-server-02",
            "scenario_id": "scn-gpu-xid-79",
        },
    )
    client.post("/control/clear", json={"asset_id": "gpu-server-02"})

    r = client.get("/redfish/v1/Systems/gpu-server-02")
    assert r.json()["Status"]["Health"] == "OK"

    r = client.get("/redfish/v1/Systems/gpu-server-02/LogServices/DCGM/Entries")
    assert r.json()["Members@odata.count"] == 0


def test_fault_only_affects_injected_asset(client: TestClient) -> None:
    """Injecting a fault on gpu-server-02 does not affect gpu-server-01."""
    client.post(
        "/control/inject",
        json={
            "asset_id": "gpu-server-02",
            "scenario_id": "scn-gpu-xid-79",
        },
    )
    r = client.get("/redfish/v1/Systems/gpu-server-01")
    assert r.json()["Status"]["Health"] == "OK"


def test_log_entries_empty_when_healthy(client: TestClient) -> None:
    """DCGM log entries are empty when the asset is healthy."""
    r = client.get("/redfish/v1/Systems/gpu-server-01/LogServices/DCGM/Entries")
    assert r.json()["Members@odata.count"] == 0


def test_events_empty_when_all_healthy(client: TestClient) -> None:
    """EventService/Events is empty when no faults are active."""
    r = client.get("/redfish/v1/EventService/Events")
    assert r.json()["Members@odata.count"] == 0


# ------------------------------------------------------------------
# PSU fault — Chassis/Power surface
# ------------------------------------------------------------------


def test_chassis_power_shows_psu_failure(client: TestClient) -> None:
    """PSU 2 shows as Absent/Critical after psu_loss fault injection."""
    client.post(
        "/control/inject",
        json={
            "asset_id": "gpu-server-01",
            "scenario_id": "scn-psu-loss",
        },
    )
    r = client.get("/redfish/v1/Chassis/gpu-server-01/Power")
    assert r.status_code == 200
    psus = r.json()["PowerSupplies"]
    assert psus[1]["Name"] == "PSU 2"
    assert psus[1]["Status"]["Health"] == "Critical"
    # Other PSUs should remain healthy
    assert psus[0]["Status"]["Health"] == "OK"
    assert psus[2]["Status"]["Health"] == "OK"


def test_chassis_power_all_healthy_baseline(client: TestClient) -> None:
    """All 4 PSUs show OK when no fault is active."""
    r = client.get("/redfish/v1/Chassis/gpu-server-01/Power")
    psus = r.json()["PowerSupplies"]
    assert all(p["Status"]["Health"] == "OK" for p in psus)


# ------------------------------------------------------------------
# Control API
# ------------------------------------------------------------------


def test_control_state_all_healthy(client: TestClient) -> None:
    r = client.get("/control/state")
    assert r.status_code == 200
    assert all(v == "healthy" for v in r.json()["states"].values())


def test_control_inject_unknown_asset_returns_404(client: TestClient) -> None:
    r = client.post(
        "/control/inject",
        json={
            "asset_id": "no-such-server",
            "scenario_id": "scn-gpu-xid-79",
        },
    )
    assert r.status_code == 404


def test_control_inject_unknown_scenario_returns_404(client: TestClient) -> None:
    r = client.post(
        "/control/inject",
        json={
            "asset_id": "gpu-server-01",
            "scenario_id": "scn-no-such",
        },
    )
    assert r.status_code == 404
