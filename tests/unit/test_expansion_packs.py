"""Unit tests for the 4 expansion Domain Packs.

Covers oil-rigs, healthcare-devices, finance-atm-fleet, telco-edge-5g-masts:
  - Pack loads cleanly with 3 active scenarios each
  - Generic monitoring adapter declared
  - KB articles and log bundles present, non-placeholder
  - Log bundles contain their scenario's error signatures verbatim
  - Signature index maps every signature to a valid, loaded KB article
  - Remediation step IDs declared in scenarios match the linked KB article
  - Simulator profile asset IDs match pack.yaml asset IDs
"""

from __future__ import annotations

from pathlib import Path

import pytest

from libs.common.models import MonitoringAdapterType
from libs.common.pack_loader import LoadedPack, PackLoadError, load_pack

PACKS_DIR = Path(__file__).parent.parent.parent / "packs"

EXPANSION_PACKS = [
    "oil-rigs",
    "healthcare-devices",
    "finance-atm-fleet",
    "telco-edge-5g-masts",
]

EXPECTED_SCENARIOS = {
    "oil-rigs": {
        "scn-mud-pump-washout",
        "scn-top-drive-vfd-overtemp",
        "scn-bop-accumulator-low",
    },
    "healthcare-devices": {
        "scn-infusion-pump-battery",
        "scn-monitor-telemetry-loss",
        "scn-mri-coldhead-failure",
    },
    "finance-atm-fleet": {
        "scn-dispenser-jam",
        "scn-card-reader-fault",
        "scn-atm-comms-loss",
    },
    "telco-edge-5g-masts": {
        "scn-gnss-sync-loss",
        "scn-radio-overtemp",
        "scn-fronthaul-link-fail",
    },
}

EXPECTED_KB_IDS = {
    "oil-rigs": {"KB600001", "KB600002", "KB600003"},
    "healthcare-devices": {"KB700001", "KB700002", "KB700003"},
    "finance-atm-fleet": {"KB800001", "KB800002", "KB800003"},
    "telco-edge-5g-masts": {"KB900001", "KB900002", "KB900003"},
}


@pytest.fixture(params=EXPANSION_PACKS)
def pack_id(request: pytest.FixtureRequest) -> str:
    return request.param


@pytest.fixture
def loaded(pack_id: str) -> LoadedPack:
    return load_pack(PACKS_DIR / pack_id)


def test_pack_loads(pack_id: str) -> None:
    try:
        loaded_pack = load_pack(PACKS_DIR / pack_id)
    except PackLoadError as exc:
        pytest.fail(f"Pack {pack_id!r} failed to load: {exc}")
    assert loaded_pack.pack.id == pack_id


def test_pack_uses_generic_adapter(loaded: LoadedPack) -> None:
    assert loaded.pack.monitoring_adapter == MonitoringAdapterType.generic


def test_pack_three_active_scenarios(loaded: LoadedPack, pack_id: str) -> None:
    assert {s.id for s in loaded.scenarios} == EXPECTED_SCENARIOS[pack_id]
    for s in loaded.scenarios:
        assert s.status == "active", f"{s.id} should be active, not scaffold"


def test_pack_kb_articles_present(loaded: LoadedPack, pack_id: str) -> None:
    assert set(loaded.kb_articles.keys()) == EXPECTED_KB_IDS[pack_id]


def test_pack_kb_articles_not_placeholder(loaded: LoadedPack) -> None:
    for kb_id, article in loaded.kb_articles.items():
        assert article.body_md.strip(), f"KB article {kb_id} is empty"
        assert len(article.body_md) > 500, (
            f"KB article {kb_id} body too short ({len(article.body_md)} chars) "
            "— looks like a placeholder"
        )
        assert "PLACEHOLDER" not in article.body_md, (
            f"KB article {kb_id} still has PLACEHOLDER text"
        )


def test_pack_kb_articles_have_remediation_steps(loaded: LoadedPack) -> None:
    for kb_id, article in loaded.kb_articles.items():
        assert article.remediation_step_ids, f"KB article {kb_id} has no remediation_step_ids"


def test_pack_log_bundles_present(loaded: LoadedPack) -> None:
    assert len(loaded.log_bundles) == 3


def test_pack_log_bundles_contain_signatures(loaded: LoadedPack) -> None:
    for scenario in loaded.scenarios:
        bundle_text = loaded.log_bundles.get(scenario.log_bundle_ref, "")
        assert bundle_text, f"Empty log bundle for {scenario.id}"
        found = any(sig.lower() in bundle_text.lower() for sig in scenario.error_signatures)
        assert found, (
            f"Scenario {scenario.id}: none of {scenario.error_signatures!r} found in log bundle"
        )


def test_pack_signature_index_populated(loaded: LoadedPack) -> None:
    for scenario in loaded.scenarios:
        for sig in scenario.error_signatures:
            assert sig in loaded.signature_index, (
                f"Signature {sig!r} from {scenario.id} not in signature_index"
            )


def test_pack_signature_index_maps_to_valid_kb_id(loaded: LoadedPack) -> None:
    for sig, kb_id in loaded.signature_index.items():
        assert kb_id in loaded.kb_articles, (
            f"signature_index[{sig!r}] = {kb_id!r} — not a loaded KB article"
        )


def test_pack_all_scenarios_have_remediation_steps(loaded: LoadedPack) -> None:
    for scn in loaded.scenarios:
        assert scn.remediation_steps, f"{scn.id} has no remediation steps"
        for step in scn.remediation_steps:
            assert step.id and step.label


def test_pack_remediation_steps_match_kb_article(loaded: LoadedPack) -> None:
    """Step IDs declared in the scenario appear in the linked KB article."""
    for scn in loaded.scenarios:
        kb_id = Path(scn.kb_article_ref).stem
        article = loaded.kb_articles[kb_id]
        for step in scn.remediation_steps:
            assert step.id in article.remediation_step_ids, (
                f"{scn.id}: step {step.id!r} not in KB article {kb_id} remediation_step_ids"
            )


def test_pack_simulator_profile_matches_assets(loaded: LoadedPack) -> None:
    profile_asset_ids = {a.id for a in loaded.simulator_profile.assets}
    pack_asset_ids = {a.id for a in loaded.pack.assets}
    assert profile_asset_ids == pack_asset_ids


def test_pack_scenarios_have_impact_block(loaded: LoadedPack) -> None:
    for scn in loaded.scenarios:
        assert scn.impact is not None, f"{scn.id} has no impact block"
