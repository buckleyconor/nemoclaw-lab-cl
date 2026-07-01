"""PACK-01, U-07 — Pack Loader validation and signature index."""

from pathlib import Path

import pytest
import yaml

from libs.common.pack_loader import LoadedPack, PackLoadError, load_pack


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

FLAGSHIP_PACK_DIR = Path(__file__).parent.parent.parent / "packs" / "datacenter-xe9680"


def _write_pack(tmp_path: Path, pack_yaml: dict, include_extras: bool = True) -> Path:
    """Write a minimal valid pack to tmp_path for isolation testing."""
    (tmp_path / "pack.yaml").write_text(yaml.dump(pack_yaml))
    if include_extras:
        # simulator profile
        (tmp_path / "simulator-profile.yaml").write_text(
            yaml.dump({
                "pack_id": pack_yaml.get("id", "test-pack"),
                "assets": [{"id": "asset-01", "type": "server"}],
            })
        )
        # scenario dir + one valid scenario
        scn_dir = tmp_path / "scenarios"
        scn_dir.mkdir()
        (scn_dir / "scn-test.yaml").write_text(yaml.dump({
            "id": "scn-test",
            "pack_id": pack_yaml.get("id", "test-pack"),
            "target_asset": "asset-01",
            "fault_type": "test_fault",
            "emit": {
                "event": {"severity": "Critical", "message_id": "TEST.1.0.Fault"},
                "log_entries": [{"severity": "Critical", "message": "Test fault message"}],
            },
            "log_bundle_ref": "bundles/scn-test.log",
            "error_signatures": ["test fault"],
            "kb_article_ref": "kb/KB-TEST.md",
            "remediation_steps": [{"id": "fix_it", "label": "Fix it"}],
        }))
        # kb dir + article
        kb_dir = tmp_path / "kb"
        kb_dir.mkdir()
        (kb_dir / "KB-TEST.md").write_text("# Test KB Article\n\nPlaceholder body.")
        # bundles dir + bundle
        bundles_dir = tmp_path / "bundles"
        bundles_dir.mkdir()
        (bundles_dir / "scn-test.log").write_text("Test log line 1\nTest log line 2\n")
    return tmp_path


# ---------------------------------------------------------------------------
# PACK-01 — malformed pack.yaml is rejected at load with a clear error
# ---------------------------------------------------------------------------

def test_pack01_missing_monitoring_adapter(tmp_path: Path) -> None:
    """PACK-01: pack.yaml missing monitoring_adapter must raise PackLoadError."""
    pack_yaml = {
        "id": "test-pack",
        "name": "Test Pack",
        "asset_noun": {"singular": "device", "plural": "devices"},
        "fleet_label": "Fleet Health",
        # monitoring_adapter intentionally omitted
        "assets": [{"id": "asset-01"}],
    }
    _write_pack(tmp_path, pack_yaml)
    with pytest.raises(PackLoadError, match="pack.yaml schema error"):
        load_pack(tmp_path)


def test_pack01_missing_assets_raises(tmp_path: Path) -> None:
    """PACK-01: pack.yaml with empty assets list must raise PackLoadError."""
    pack_yaml = {
        "id": "test-pack",
        "name": "Test Pack",
        "asset_noun": {"singular": "device", "plural": "devices"},
        "fleet_label": "Fleet Health",
        "monitoring_adapter": "generic",
        "assets": [],  # min_length=1 violated
    }
    _write_pack(tmp_path, pack_yaml)
    with pytest.raises(PackLoadError, match="pack.yaml schema error"):
        load_pack(tmp_path)


def test_pack01_nonexistent_directory() -> None:
    """PACK-01: loading a missing directory raises PackLoadError."""
    with pytest.raises(PackLoadError, match="not found"):
        load_pack(Path("/nonexistent/pack/dir"))


def test_pack01_target_asset_not_in_assets(tmp_path: Path) -> None:
    """PACK-01: scenario target_asset must exist in pack.assets."""
    pack_yaml = {
        "id": "test-pack",
        "name": "Test Pack",
        "asset_noun": {"singular": "device", "plural": "devices"},
        "fleet_label": "Fleet Health",
        "monitoring_adapter": "generic",
        "assets": [{"id": "asset-01"}],
    }
    _write_pack(tmp_path, pack_yaml)
    # overwrite the scenario with a bad target_asset
    scn = {
        "id": "scn-bad",
        "pack_id": "test-pack",
        "target_asset": "asset-DOES-NOT-EXIST",
        "fault_type": "test",
        "emit": {"event": {"severity": "Critical", "message_id": "X"}, "log_entries": []},
        "log_bundle_ref": "bundles/scn-test.log",
        "error_signatures": ["sig"],
        "kb_article_ref": "kb/KB-TEST.md",
        "remediation_steps": [{"id": "fix_it", "label": "Fix"}],
    }
    (tmp_path / "scenarios" / "scn-test.yaml").write_text(yaml.dump(scn))
    with pytest.raises(PackLoadError, match="target_asset"):
        load_pack(tmp_path)


def test_pack01_missing_kb_article_file(tmp_path: Path) -> None:
    """PACK-01: scenario kb_article_ref must reference an existing file."""
    pack_yaml = {
        "id": "test-pack",
        "name": "Test",
        "asset_noun": {"singular": "device", "plural": "devices"},
        "fleet_label": "Fleet",
        "monitoring_adapter": "generic",
        "assets": [{"id": "asset-01"}],
    }
    _write_pack(tmp_path, pack_yaml)
    scn = yaml.safe_load((tmp_path / "scenarios" / "scn-test.yaml").read_text())
    scn["kb_article_ref"] = "kb/MISSING.md"
    (tmp_path / "scenarios" / "scn-test.yaml").write_text(yaml.dump(scn))
    with pytest.raises(PackLoadError, match="kb_article_ref"):
        load_pack(tmp_path)


# ---------------------------------------------------------------------------
# PACK-01 — happy path: minimal valid pack loads correctly
# ---------------------------------------------------------------------------

def test_valid_pack_loads(tmp_path: Path) -> None:
    """A well-formed pack loads without error."""
    pack_yaml = {
        "id": "test-pack",
        "name": "Test Pack",
        "asset_noun": {"singular": "device", "plural": "devices"},
        "fleet_label": "Fleet Health",
        "monitoring_adapter": "generic",
        "assets": [{"id": "asset-01"}],
    }
    loaded = load_pack(_write_pack(tmp_path, pack_yaml))
    assert isinstance(loaded, LoadedPack)
    assert loaded.pack.id == "test-pack"
    assert len(loaded.scenarios) == 1
    assert len(loaded.kb_articles) == 1
    assert "KB-TEST" in loaded.kb_articles


# ---------------------------------------------------------------------------
# U-07 — signature index built from flagship pack; "Xid 79" → KB000123
# ---------------------------------------------------------------------------

@pytest.mark.skipif(
    not FLAGSHIP_PACK_DIR.is_dir(),
    reason="Flagship pack directory not found",
)
def test_u07_signature_index_xid79() -> None:
    """U-07: loading the flagship pack builds a signature index with 'Xid 79' → KB000123."""
    loaded = load_pack(FLAGSHIP_PACK_DIR)
    assert "Xid 79" in loaded.signature_index, (
        f"'Xid 79' not found in signature_index. Keys: {list(loaded.signature_index)}"
    )
    assert loaded.signature_index["Xid 79"] == "KB000123"


@pytest.mark.skipif(
    not FLAGSHIP_PACK_DIR.is_dir(),
    reason="Flagship pack directory not found",
)
def test_u07_kb000123_article_present() -> None:
    """U-07: KB000123 is loaded and has a meaningful title."""
    loaded = load_pack(FLAGSHIP_PACK_DIR)
    assert "KB000123" in loaded.kb_articles
    article = loaded.kb_articles["KB000123"]
    assert "Xid 79" in article.title
    assert len(article.body_md) > 100


@pytest.mark.skipif(
    not FLAGSHIP_PACK_DIR.is_dir(),
    reason="Flagship pack directory not found",
)
def test_u07_log_extract_backfilled() -> None:
    """U-07: scenario for scn-gpu-xid-79 has a non-None log_extract after pack load."""
    loaded = load_pack(FLAGSHIP_PACK_DIR)
    xid_scenario = next(s for s in loaded.scenarios if s.id == "scn-gpu-xid-79")
    assert xid_scenario.log_extract is not None
    assert len(xid_scenario.log_extract) > 0


@pytest.mark.skipif(
    not FLAGSHIP_PACK_DIR.is_dir(),
    reason="Flagship pack directory not found",
)
def test_flagship_pack_loads_all_scenarios() -> None:
    """Flagship pack loads all 5 scenarios without error."""
    loaded = load_pack(FLAGSHIP_PACK_DIR)
    scenario_ids = {s.id for s in loaded.scenarios}
    expected = {
        "scn-gpu-xid-79",
        "scn-ecc-uncorrectable",
        "scn-psu-loss",
        "scn-nvlink-down",
        "scn-thermal-throttle",
    }
    assert expected == scenario_ids


@pytest.mark.skipif(
    not FLAGSHIP_PACK_DIR.is_dir(),
    reason="Flagship pack directory not found",
)
def test_flagship_pack_signature_index_complete() -> None:
    """All 5 scenarios contribute signatures to the index."""
    loaded = load_pack(FLAGSHIP_PACK_DIR)
    assert "Xid 79" in loaded.signature_index
    assert "ECC uncorrectable error" in loaded.signature_index
    assert "PSU 2 input lost" in loaded.signature_index
    assert "NVLink fabric failure" in loaded.signature_index
    assert "thermal throttle" in loaded.signature_index
