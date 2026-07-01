"""U-03, U-04 — signature snap-to-known."""

from __future__ import annotations

from pathlib import Path

import pytest

from agent.snap import snap_to_known
from libs.common.pack_loader import load_pack

PACK_DIR = Path(__file__).parent.parent.parent / "packs" / "datacenter-xe9680"


@pytest.fixture(scope="module")
def signature_index() -> dict[str, str]:
    return load_pack(PACK_DIR).signature_index


# ─────────────────────────────────────────────────────────────────────────────
# U-03: LLM output contains a known signature — snap returns canonical form
# ─────────────────────────────────────────────────────────────────────────────


def test_u03_exact_signature_in_output(signature_index) -> None:
    """U-03: LLM returns exact canonical string → same string returned."""
    result = snap_to_known("Xid 79", signature_index)
    assert result == "Xid 79"


def test_u03_signature_embedded_in_sentence(signature_index) -> None:
    """U-03: LLM output 'looks like Xid 79 error' → 'Xid 79'."""
    result = snap_to_known("The error looks like an Xid 79 GPU bus fault", signature_index)
    assert result == "Xid 79"


def test_u03_case_insensitive_match(signature_index) -> None:
    """U-03: Match is case-insensitive."""
    result = snap_to_known("detected XID 79 condition on GPU0", signature_index)
    assert result == "Xid 79"


def test_u03_ecc_signature(signature_index) -> None:
    """U-03: ECC signature in LLM output."""
    result = snap_to_known("ECC uncorrectable error on HBM bank 3", signature_index)
    assert result == "ECC uncorrectable error"


def test_u03_psu_signature(signature_index) -> None:
    """U-03: PSU signature in LLM output."""
    result = snap_to_known("Identified PSU 2 input lost event", signature_index)
    assert result == "PSU 2 input lost"


def test_u03_thermal_signature(signature_index) -> None:
    result = snap_to_known("GPU is in thermal throttle state", signature_index)
    assert result == "thermal throttle"


def test_u03_nvlink_signature(signature_index) -> None:
    result = snap_to_known("NVLink fabric failure detected on switch 0", signature_index)
    assert result == "NVLink fabric failure"


def test_u03_returns_string_not_kb_id(signature_index) -> None:
    """snap_to_known returns the signature, not the KB article id."""
    result = snap_to_known("Xid 79 error", signature_index)
    assert result is not None
    assert "KB" not in result


# ─────────────────────────────────────────────────────────────────────────────
# U-04: LLM output has NO known signature — returns None
# ─────────────────────────────────────────────────────────────────────────────


def test_u04_no_match_returns_none(signature_index) -> None:
    """U-04: LLM returns something unrecognised — snap returns None."""
    result = snap_to_known("something completely unrelated to any known fault", signature_index)
    assert result is None


def test_u04_empty_output_returns_none(signature_index) -> None:
    result = snap_to_known("", signature_index)
    assert result is None


def test_u04_empty_index_always_returns_none() -> None:
    result = snap_to_known("Xid 79", {})
    assert result is None
