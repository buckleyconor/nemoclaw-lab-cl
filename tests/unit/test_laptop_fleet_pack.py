"""Unit tests for the laptop-fleet Domain Pack.

Covers:
  PACK-05  laptop-fleet pack loads with all 3 active scenarios.
  PACK-06  All scenarios reference valid KB articles and log bundles.
  PACK-07  Signature index maps all error signatures to KB article IDs.
  PACK-08  Generic monitoring adapter is declared (not Redfish).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from libs.common.models import MonitoringAdapterType
from libs.common.pack_loader import LoadedPack, load_pack

PACK_DIR = Path(__file__).parent.parent.parent / "packs" / "laptop-fleet"

EXPECTED_SCENARIOS = {
    "scn-driver-tdr",
    "scn-gpu-oom",
    "scn-nvgpu-ecc-error",
}

EXPECTED_KB_IDS = {"KB100001", "KB100002", "KB100003"}

EXPECTED_ASSETS = {"laptop-01", "laptop-02", "laptop-03", "laptop-04"}


@pytest.fixture(scope="module")
def loaded() -> LoadedPack:
    return load_pack(PACK_DIR)


# ─────────────────────────────────────────────────────────────────────────────
# PACK-05: pack loads cleanly
# ─────────────────────────────────────────────────────────────────────────────

def test_pack05_pack_id(loaded: LoadedPack) -> None:
    assert loaded.pack.id == "laptop-fleet"


def test_pack05_monitoring_adapter_is_generic(loaded: LoadedPack) -> None:
    """PACK-08: laptop-fleet uses GenericAdapter, not Redfish."""
    assert loaded.pack.monitoring_adapter == MonitoringAdapterType.generic


def test_pack05_asset_noun(loaded: LoadedPack) -> None:
    assert loaded.pack.asset_noun.singular == "laptop"
    assert loaded.pack.asset_noun.plural == "laptops"


def test_pack05_assets(loaded: LoadedPack) -> None:
    assert {a.id for a in loaded.pack.assets} == EXPECTED_ASSETS


def test_pack05_fleet_layout_is_list(loaded: LoadedPack) -> None:
    """4 laptops is dense enough that the compact list layout beats image tiles."""
    assert loaded.pack.fleet_layout == "list"


def test_pack05_three_active_scenarios(loaded: LoadedPack) -> None:
    """PACK-05: All 3 scenarios are present and active."""
    assert {s.id for s in loaded.scenarios} == EXPECTED_SCENARIOS
    for s in loaded.scenarios:
        assert s.status == "active", f"{s.id} should be active"


def test_pack05_simulator_profile_matches_assets(loaded: LoadedPack) -> None:
    profile_asset_ids = {a.id for a in loaded.simulator_profile.assets}
    pack_asset_ids = {a.id for a in loaded.pack.assets}
    assert profile_asset_ids == pack_asset_ids


# ─────────────────────────────────────────────────────────────────────────────
# PACK-06: KB articles and log bundles
# ─────────────────────────────────────────────────────────────────────────────

def test_pack06_kb_articles_present(loaded: LoadedPack) -> None:
    """PACK-06: All 3 KB articles loaded."""
    assert set(loaded.kb_articles.keys()) == EXPECTED_KB_IDS


def test_pack06_kb_articles_have_body(loaded: LoadedPack) -> None:
    for kb_id, article in loaded.kb_articles.items():
        assert article.body_md.strip(), f"KB article {kb_id} has empty body"
        assert len(article.body_md) > 50, f"KB article {kb_id} body too short (placeholder?)"


def test_pack06_kb_articles_have_remediation_steps(loaded: LoadedPack) -> None:
    for kb_id, article in loaded.kb_articles.items():
        assert article.remediation_step_ids, f"KB article {kb_id} has no remediation_step_ids"


def test_pack06_log_bundles_loaded(loaded: LoadedPack) -> None:
    """PACK-06: All 3 log bundles loaded."""
    assert len(loaded.log_bundles) == 3


def test_pack06_log_bundles_contain_signatures(loaded: LoadedPack) -> None:
    """PACK-06: Each log bundle contains at least one error signature from its scenario."""
    for scenario in loaded.scenarios:
        bundle_text = loaded.log_bundles.get(scenario.log_bundle_ref, "")
        assert bundle_text, f"Empty log bundle for {scenario.id}"
        found = any(sig.lower() in bundle_text.lower() for sig in scenario.error_signatures)
        assert found, (
            f"Scenario {scenario.id}: none of {scenario.error_signatures!r} "
            f"found in log bundle"
        )


# ─────────────────────────────────────────────────────────────────────────────
# PACK-07: signature index
# ─────────────────────────────────────────────────────────────────────────────

def test_pack07_signature_index_populated(loaded: LoadedPack) -> None:
    """PACK-07: All error_signatures from all scenarios appear in the index."""
    for scenario in loaded.scenarios:
        for sig in scenario.error_signatures:
            assert sig in loaded.signature_index, (
                f"Signature {sig!r} from {scenario.id} not in signature_index"
            )


def test_pack07_signature_index_maps_to_valid_kb_id(loaded: LoadedPack) -> None:
    for sig, kb_id in loaded.signature_index.items():
        assert kb_id in loaded.kb_articles, (
            f"signature_index[{sig!r}] = {kb_id!r} — not a loaded KB article"
        )


# ─────────────────────────────────────────────────────────────────────────────
# Scenario-specific checks
# ─────────────────────────────────────────────────────────────────────────────

def test_scn_driver_tdr_targets_laptop02(loaded: LoadedPack) -> None:
    scn = loaded.scenarios_by_id["scn-driver-tdr"]
    assert scn.target_asset == "laptop-02"
    assert "TDR" in scn.error_signatures


def test_scn_gpu_oom_targets_laptop01(loaded: LoadedPack) -> None:
    scn = loaded.scenarios_by_id["scn-gpu-oom"]
    assert scn.target_asset == "laptop-01"
    assert "CUDA out of memory" in scn.error_signatures


def test_scn_nvgpu_ecc_error_targets_laptop03(loaded: LoadedPack) -> None:
    scn = loaded.scenarios_by_id["scn-nvgpu-ecc-error"]
    assert scn.target_asset == "laptop-03"
    assert "ECC error" in scn.error_signatures


def test_all_scenarios_have_remediation_steps(loaded: LoadedPack) -> None:
    for scn in loaded.scenarios:
        assert scn.remediation_steps, f"{scn.id} has no remediation steps"
        for step in scn.remediation_steps:
            assert step.id and step.label


def test_remediation_steps_match_kb_article(loaded: LoadedPack) -> None:
    """Step IDs declared in the scenario appear in the linked KB article."""
    for scn in loaded.scenarios:
        kb_id = Path(scn.kb_article_ref).stem
        article = loaded.kb_articles[kb_id]
        for step in scn.remediation_steps:
            assert step.id in article.remediation_step_ids, (
                f"{scn.id}: step {step.id!r} not in KB article {kb_id} remediation_step_ids"
            )
