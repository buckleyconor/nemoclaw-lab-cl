"""Unit tests for the 4 scaffold Domain Packs.

Verifies that each scaffold pack:
  - Loads without error (schema + integrity checks pass)
  - Has at least one scenario with status="scaffold"
  - Has valid KB articles and log bundles (even if content is placeholder)
  - Uses the generic monitoring adapter
"""

from __future__ import annotations

from pathlib import Path

import pytest

from libs.common.models import MonitoringAdapterType
from libs.common.pack_loader import PackLoadError, load_pack

PACKS_DIR = Path(__file__).parent.parent.parent / "packs"

SCAFFOLD_PACKS = [
    "network-fabric",
    "edge-inference",
    "storage-nvme",
    "hpc-cluster",
]


@pytest.mark.parametrize("pack_id", SCAFFOLD_PACKS)
def test_scaffold_pack_loads(pack_id: str) -> None:
    """All scaffold packs must load without PackLoadError."""
    pack_dir = PACKS_DIR / pack_id
    try:
        loaded = load_pack(pack_dir)
    except PackLoadError as exc:
        pytest.fail(f"Pack {pack_id!r} failed to load: {exc}")
    assert loaded.pack.id == pack_id


@pytest.mark.parametrize("pack_id", SCAFFOLD_PACKS)
def test_scaffold_pack_has_scaffold_scenarios(pack_id: str) -> None:
    """Every scaffold pack must have at least one scenario with status='scaffold'."""
    loaded = load_pack(PACKS_DIR / pack_id)
    scaffold_scenarios = [s for s in loaded.scenarios if s.status == "scaffold"]
    assert scaffold_scenarios, (
        f"Pack {pack_id!r} has no scaffold scenarios — "
        "mark scenarios with 'status: scaffold'"
    )


@pytest.mark.parametrize("pack_id", SCAFFOLD_PACKS)
def test_scaffold_pack_no_active_scenarios(pack_id: str) -> None:
    """Scaffold packs must not accidentally have active scenarios."""
    loaded = load_pack(PACKS_DIR / pack_id)
    active = [s for s in loaded.scenarios if s.status == "active"]
    assert not active, (
        f"Pack {pack_id!r} has active scenarios {[s.id for s in active]} — "
        "scaffold packs should only have scaffold scenarios"
    )


@pytest.mark.parametrize("pack_id", SCAFFOLD_PACKS)
def test_scaffold_pack_uses_generic_adapter(pack_id: str) -> None:
    """Scaffold packs all use the generic monitoring adapter."""
    loaded = load_pack(PACKS_DIR / pack_id)
    assert loaded.pack.monitoring_adapter == MonitoringAdapterType.generic, (
        f"Pack {pack_id!r} declares adapter {loaded.pack.monitoring_adapter!r}, "
        "expected generic"
    )


@pytest.mark.parametrize("pack_id", SCAFFOLD_PACKS)
def test_scaffold_pack_kb_articles_present(pack_id: str) -> None:
    loaded = load_pack(PACKS_DIR / pack_id)
    assert loaded.kb_articles, f"Pack {pack_id!r} has no KB articles"
    for kb_id, article in loaded.kb_articles.items():
        assert article.body_md.strip(), f"KB article {kb_id} in {pack_id!r} is empty"


@pytest.mark.parametrize("pack_id", SCAFFOLD_PACKS)
def test_scaffold_pack_log_bundles_present(pack_id: str) -> None:
    loaded = load_pack(PACKS_DIR / pack_id)
    assert loaded.log_bundles, f"Pack {pack_id!r} has no log bundles"
    for ref, text in loaded.log_bundles.items():
        assert text.strip(), f"Log bundle {ref!r} in {pack_id!r} is empty"


@pytest.mark.parametrize("pack_id", SCAFFOLD_PACKS)
def test_scaffold_pack_signature_index_populated(pack_id: str) -> None:
    """signature_index must include all error signatures (scaffold scenarios included)."""
    loaded = load_pack(PACKS_DIR / pack_id)
    for scenario in loaded.scenarios:
        for sig in scenario.error_signatures:
            assert sig in loaded.signature_index, (
                f"Pack {pack_id!r}: signature {sig!r} from {scenario.id} "
                "missing from signature_index"
            )


@pytest.mark.parametrize("pack_id", SCAFFOLD_PACKS)
def test_scaffold_pack_simulator_profile_consistent(pack_id: str) -> None:
    """Simulator profile assets must match pack.yaml assets."""
    loaded = load_pack(PACKS_DIR / pack_id)
    pack_ids = {a.id for a in loaded.pack.assets}
    profile_ids = {a.id for a in loaded.simulator_profile.assets}
    assert pack_ids == profile_ids, (
        f"Pack {pack_id!r}: pack asset IDs {sorted(pack_ids)} "
        f"!= simulator-profile asset IDs {sorted(profile_ids)}"
    )
