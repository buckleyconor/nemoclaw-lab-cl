"""U-05 — ApprovalTokenStore: minting, retrieval, consumption."""

from __future__ import annotations

import pytest

from libs.common.models import ApprovalDecision
from services.mcp_tools.token_store import ApprovalTokenStore


@pytest.fixture()
def store() -> ApprovalTokenStore:
    return ApprovalTokenStore()


# ─────────────────────────────────────────────────────────────────────────────
# U-05: Token minting
# ─────────────────────────────────────────────────────────────────────────────


def test_u05_mint_returns_token_with_correct_fields(store: ApprovalTokenStore) -> None:
    token = store.mint("evt-001", ApprovalDecision.approved, decided_by="alice")
    assert token.fault_event_id == "evt-001"
    assert token.decision == ApprovalDecision.approved
    assert token.decided_by == "alice"
    assert token.consumed is False
    assert len(token.token) > 20  # CSPRNG — at least 20 chars


def test_u05_mint_decision_string_coercion(store: ApprovalTokenStore) -> None:
    token = store.mint("evt-002", "approved")
    assert token.decision == ApprovalDecision.approved


def test_u05_mint_generates_unique_tokens(store: ApprovalTokenStore) -> None:
    t1 = store.mint("evt-001", ApprovalDecision.approved)
    t2 = store.mint("evt-001", ApprovalDecision.approved)
    assert t1.token != t2.token


def test_u05_mint_denied_token(store: ApprovalTokenStore) -> None:
    token = store.mint("evt-003", ApprovalDecision.denied)
    assert token.decision == ApprovalDecision.denied


# ─────────────────────────────────────────────────────────────────────────────
# Retrieval
# ─────────────────────────────────────────────────────────────────────────────


def test_get_returns_minted_token(store: ApprovalTokenStore) -> None:
    token = store.mint("evt-001", ApprovalDecision.approved)
    retrieved = store.get(token.token)
    assert retrieved is not None
    assert retrieved.token == token.token


def test_get_unknown_returns_none(store: ApprovalTokenStore) -> None:
    assert store.get("not-a-real-token") is None


# ─────────────────────────────────────────────────────────────────────────────
# Consumption (single-use invariant)
# ─────────────────────────────────────────────────────────────────────────────


def test_consume_marks_token_consumed(store: ApprovalTokenStore) -> None:
    token = store.mint("evt-001", ApprovalDecision.approved)
    store.consume(token.token)
    retrieved = store.get(token.token)
    assert retrieved is not None
    assert retrieved.consumed is True


def test_consume_noop_for_unknown_token(store: ApprovalTokenStore) -> None:
    store.consume("phantom-token")  # must not raise


def test_original_token_unchanged_after_consume(store: ApprovalTokenStore) -> None:
    """consume() must not mutate the original ApprovalToken (immutability via model_copy)."""
    token = store.mint("evt-001", ApprovalDecision.approved)
    original_consumed = token.consumed
    store.consume(token.token)
    assert original_consumed is False  # original object unchanged


# ─────────────────────────────────────────────────────────────────────────────
# Store helper
# ─────────────────────────────────────────────────────────────────────────────


def test_store_accepts_pre_built_token(store: ApprovalTokenStore) -> None:
    from libs.common.models import ApprovalToken

    tok = ApprovalToken(
        token="test-token-12345",
        fault_event_id="evt-pre",
        decision=ApprovalDecision.approved,
        decided_by="gateway",
    )
    store.store(tok)
    assert store.get("test-token-12345") is not None


def test_len_reflects_stored_count(store: ApprovalTokenStore) -> None:
    assert len(store) == 0
    store.mint("evt-1", ApprovalDecision.approved)
    store.mint("evt-2", ApprovalDecision.denied)
    assert len(store) == 2
    store.clear()
    assert len(store) == 0
