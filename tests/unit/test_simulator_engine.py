"""Unit tests for SimulatorEngine — state machine, inject, clear."""

from __future__ import annotations

from pathlib import Path

import pytest

from libs.common.pack_loader import load_pack
from services.simulator.engine import InjectedFault, SimulatorEngine

PACK_DIR = Path(__file__).parent.parent.parent / "packs" / "datacenter-xe9680"


@pytest.fixture(scope="module")
def engine() -> SimulatorEngine:
    loaded = load_pack(PACK_DIR)
    return SimulatorEngine(loaded)


@pytest.fixture(autouse=True)
def clear_all_faults(engine: SimulatorEngine):
    """Ensure each test starts with a clean (all-healthy) slate."""
    for asset_id in engine.asset_ids:
        engine.clear(asset_id)
    yield
    for asset_id in engine.asset_ids:
        engine.clear(asset_id)


# ------------------------------------------------------------------
# Initial state
# ------------------------------------------------------------------

def test_all_assets_start_healthy(engine: SimulatorEngine) -> None:
    for state in engine.all_states().values():
        assert state == "healthy"


def test_asset_ids_match_pack(engine: SimulatorEngine) -> None:
    loaded = load_pack(PACK_DIR)
    assert set(engine.asset_ids) == {a.id for a in loaded.pack.assets}
    assert {"gpu-server-01", "gpu-server-02"} <= set(engine.asset_ids)


def test_asset_type_is_server(engine: SimulatorEngine) -> None:
    assert engine.asset_type("gpu-server-01") == "server"
    assert engine.asset_type("gpu-server-02") == "server"


# ------------------------------------------------------------------
# Inject
# ------------------------------------------------------------------

def test_inject_transitions_asset_to_faulted(engine: SimulatorEngine) -> None:
    engine.inject("gpu-server-02", "scn-gpu-xid-79")
    assert engine.is_faulted("gpu-server-02")
    assert engine.all_states()["gpu-server-02"] == "faulted"


def test_inject_returns_fault_with_correct_data(engine: SimulatorEngine) -> None:
    fault: InjectedFault = engine.inject("gpu-server-02", "scn-gpu-xid-79")
    assert fault.asset_id == "gpu-server-02"
    assert fault.scenario_id == "scn-gpu-xid-79"
    assert fault.fault_type == "gpu_xid"
    assert fault.event_severity == "Critical"
    assert fault.event_message_id == "OEM.1.0.GPUFault"


def test_inject_populates_log_entries(engine: SimulatorEngine) -> None:
    fault = engine.inject("gpu-server-02", "scn-gpu-xid-79")
    assert len(fault.log_entries) >= 1
    messages = [msg for _, msg in fault.log_entries]
    assert any("Xid 79" in m for m in messages)


def test_inject_does_not_affect_other_assets(engine: SimulatorEngine) -> None:
    engine.inject("gpu-server-02", "scn-gpu-xid-79")
    assert not engine.is_faulted("gpu-server-01")
    assert engine.all_states()["gpu-server-01"] == "healthy"


def test_inject_unknown_asset_raises(engine: SimulatorEngine) -> None:
    with pytest.raises(KeyError, match="no-such-server"):
        engine.inject("no-such-server", "scn-gpu-xid-79")


def test_inject_unknown_scenario_raises(engine: SimulatorEngine) -> None:
    with pytest.raises(KeyError, match="scn-no-such"):
        engine.inject("gpu-server-01", "scn-no-such")


def test_inject_overrides_existing_fault(engine: SimulatorEngine) -> None:
    engine.inject("gpu-server-01", "scn-gpu-xid-79")
    engine.inject("gpu-server-01", "scn-ecc-uncorrectable")
    fault = engine.get_fault("gpu-server-01")
    assert fault is not None
    assert fault.scenario_id == "scn-ecc-uncorrectable"


# ------------------------------------------------------------------
# Clear
# ------------------------------------------------------------------

def test_clear_returns_asset_to_healthy(engine: SimulatorEngine) -> None:
    engine.inject("gpu-server-02", "scn-gpu-xid-79")
    engine.clear("gpu-server-02")
    assert not engine.is_faulted("gpu-server-02")
    assert engine.get_fault("gpu-server-02") is None
    assert engine.all_states()["gpu-server-02"] == "healthy"


def test_clear_unknown_asset_raises(engine: SimulatorEngine) -> None:
    with pytest.raises(KeyError):
        engine.clear("no-such-server")


def test_clear_already_healthy_is_idempotent(engine: SimulatorEngine) -> None:
    engine.clear("gpu-server-01")  # already healthy — should not raise
    assert not engine.is_faulted("gpu-server-01")


# ------------------------------------------------------------------
# Scenario variety
# ------------------------------------------------------------------

@pytest.mark.parametrize("scenario_id,asset_id", [
    ("scn-gpu-xid-79", "gpu-server-02"),
    ("scn-ecc-uncorrectable", "gpu-server-01"),
    ("scn-psu-loss", "gpu-server-01"),
    ("scn-nvlink-down", "gpu-server-02"),
    ("scn-thermal-throttle", "gpu-server-01"),
])
def test_all_scenarios_inject_correctly(
    engine: SimulatorEngine, scenario_id: str, asset_id: str
) -> None:
    fault = engine.inject(asset_id, scenario_id)
    assert fault.scenario_id == scenario_id
    assert engine.is_faulted(asset_id)
