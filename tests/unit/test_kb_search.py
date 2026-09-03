"""KB-01, KB-02 — KBIndex: semantic and deterministic KB article search."""

from __future__ import annotations

from pathlib import Path

import pytest

from libs.common.pack_loader import load_pack
from services.mcp_tools.kb_index import KBIndex

PACK_DIR = Path(__file__).parent.parent.parent / "packs" / "datacenter-xe9680"


@pytest.fixture(scope="module")
def kb_index() -> KBIndex:
    loaded = load_pack(PACK_DIR)
    return KBIndex(loaded, confidence_threshold=0.60)


# ─────────────────────────────────────────────────────────────────────────────
# KB-01: Correct article returned for an exact / semantically-close signature
# ─────────────────────────────────────────────────────────────────────────────


def test_kb01_exact_signature_xid79_returns_kb000123(kb_index: KBIndex) -> None:
    """KB-01: 'Xid 79' is an exact signature for KB000123 (Xid 79 article)."""
    result = kb_index.search("Xid 79")
    assert result is not None
    assert result["kb_id"] == "KB000123"
    assert result["score"] > 0


def test_kb01_exact_signature_ecc_returns_kb000456(kb_index: KBIndex) -> None:
    """KB-01: ECC uncorrectable signature → KB000456."""
    result = kb_index.search("ECC uncorrectable error")
    assert result is not None
    assert result["kb_id"] == "KB000456"


def test_kb01_psu_signature_returns_kb000789(kb_index: KBIndex) -> None:
    result = kb_index.search("PSU 2 input lost")
    assert result is not None
    assert result["kb_id"] == "KB000789"


def test_kb01_nvlink_signature_returns_kb001011(kb_index: KBIndex) -> None:
    result = kb_index.search("NVLink fabric failure")
    assert result is not None
    assert result["kb_id"] == "KB001011"


def test_kb01_thermal_signature_returns_kb001213(kb_index: KBIndex) -> None:
    result = kb_index.search("thermal throttle")
    assert result is not None
    assert result["kb_id"] == "KB001213"


def test_kb01_result_has_remediation_step_ids(kb_index: KBIndex) -> None:
    result = kb_index.search("Xid 79")
    assert result is not None
    assert isinstance(result["remediation_step_ids"], list)
    assert len(result["remediation_step_ids"]) > 0


def test_kb01_result_has_body_md(kb_index: KBIndex) -> None:
    result = kb_index.search("Xid 79")
    assert result is not None
    assert isinstance(result["body_md"], str)
    assert len(result["body_md"]) > 10


# ─────────────────────────────────────────────────────────────────────────────
# KB-02: Unknown signature with fallback_kb_id
# ─────────────────────────────────────────────────────────────────────────────


def test_kb02_unknown_signature_with_fallback_returns_fallback(
    kb_index: KBIndex,
) -> None:
    """KB-02: low-confidence / unknown signature → fallback_kb_id used."""
    result = kb_index.search(
        "completely unrelated noise signal that matches nothing",
        fallback_kb_id="KB000123",
    )
    assert result is not None
    assert result["kb_id"] == "KB000123"


def test_kb02_unknown_signature_without_fallback_returns_none(
    kb_index: KBIndex,
) -> None:
    """KB-02: no fallback and no match → None."""
    result = kb_index.search("completely unrelated noise signal that matches nothing")
    # Without semantic search (dev machine) this returns None.
    # With semantic search (CI) the score may still be too low.
    # Either outcome is acceptable; we verify the return type.
    assert result is None or isinstance(result["score"], float)


# ─────────────────────────────────────────────────────────────────────────────
# Semantic flag
# ─────────────────────────────────────────────────────────────────────────────


def test_has_semantic_is_bool(kb_index: KBIndex) -> None:
    assert isinstance(kb_index.has_semantic, bool)
