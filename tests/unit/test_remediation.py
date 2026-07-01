"""SEC-01..05 — remediation.execute security invariants."""

from __future__ import annotations

import pytest
import pytest_asyncio

from libs.common.models import ApprovalDecision
from services.mcp_tools.fault_registry import FaultEventRegistry
from services.mcp_tools.token_store import ApprovalTokenStore
from services.mcp_tools.tools.remediation import RemediationError, remediation_execute

# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────

_FAULT_ID = "evt-xid79-001"
_ASSET_ID = "gpu-server-02"
_ALLOWED_STEPS = ["drain_node", "gpu_reset", "verify_health"]


@pytest.fixture()
def store() -> ApprovalTokenStore:
    return ApprovalTokenStore()


@pytest.fixture()
def registry() -> FaultEventRegistry:
    fr = FaultEventRegistry()
    fr.register(
        fault_event_id=_FAULT_ID,
        asset_id=_ASSET_ID,
        scenario_id="scn-gpu-xid-79",
        allowed_step_ids=_ALLOWED_STEPS,
    )
    return fr


_cleared: list[str] = []


async def _fake_clear(asset_id: str) -> None:
    _cleared.append(asset_id)


@pytest.fixture(autouse=True)
def reset_cleared() -> None:
    _cleared.clear()
    yield
    _cleared.clear()


# ─────────────────────────────────────────────────────────────────────────────
# SEC-01: No token provided → not_approved
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_sec01_no_token_raises_not_approved(
    store: ApprovalTokenStore,
    registry: FaultEventRegistry,
) -> None:
    with pytest.raises(RemediationError) as exc_info:
        await remediation_execute(
            fault_event_id=_FAULT_ID,
            approval_token=None,
            step_ids=_ALLOWED_STEPS,
            token_store=store,
            fault_registry=registry,
            clear_fn=_fake_clear,
        )
    assert exc_info.value.error == "not_approved"


@pytest.mark.asyncio
async def test_sec01_empty_string_token_raises_not_approved(
    store: ApprovalTokenStore,
    registry: FaultEventRegistry,
) -> None:
    with pytest.raises(RemediationError) as exc_info:
        await remediation_execute(
            fault_event_id=_FAULT_ID,
            approval_token="",
            step_ids=_ALLOWED_STEPS,
            token_store=store,
            fault_registry=registry,
            clear_fn=_fake_clear,
        )
    assert exc_info.value.error == "not_approved"


# ─────────────────────────────────────────────────────────────────────────────
# SEC-02: Forged / non-existent token → token_invalid
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_sec02_forged_token_raises_token_invalid(
    store: ApprovalTokenStore,
    registry: FaultEventRegistry,
) -> None:
    with pytest.raises(RemediationError) as exc_info:
        await remediation_execute(
            fault_event_id=_FAULT_ID,
            approval_token="aaaaaaaaaaaaaaaa-fake-token-bbbbbbbbbbb",
            step_ids=_ALLOWED_STEPS,
            token_store=store,
            fault_registry=registry,
            clear_fn=_fake_clear,
        )
    assert exc_info.value.error == "token_invalid"


# ─────────────────────────────────────────────────────────────────────────────
# SEC-03: Consumed token → token_consumed
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_sec03_consumed_token_raises_token_consumed(
    store: ApprovalTokenStore,
    registry: FaultEventRegistry,
) -> None:
    token = store.mint(_FAULT_ID, ApprovalDecision.approved)
    store.consume(token.token)  # pre-consume

    with pytest.raises(RemediationError) as exc_info:
        await remediation_execute(
            fault_event_id=_FAULT_ID,
            approval_token=token.token,
            step_ids=_ALLOWED_STEPS,
            token_store=store,
            fault_registry=registry,
            clear_fn=_fake_clear,
        )
    assert exc_info.value.error == "token_consumed"


@pytest.mark.asyncio
async def test_sec03_second_execution_with_same_token_raises(
    store: ApprovalTokenStore,
    registry: FaultEventRegistry,
) -> None:
    """Token is consumed on first successful execute; second call must fail."""
    token = store.mint(_FAULT_ID, ApprovalDecision.approved)

    # First call — should succeed
    result = await remediation_execute(
        fault_event_id=_FAULT_ID,
        approval_token=token.token,
        step_ids=_ALLOWED_STEPS,
        token_store=store,
        fault_registry=registry,
        clear_fn=_fake_clear,
    )
    assert result["status"] == "resolved"

    # Second call — token is now consumed
    with pytest.raises(RemediationError) as exc_info:
        await remediation_execute(
            fault_event_id=_FAULT_ID,
            approval_token=token.token,
            step_ids=_ALLOWED_STEPS,
            token_store=store,
            fault_registry=registry,
            clear_fn=_fake_clear,
        )
    assert exc_info.value.error == "token_consumed"


# ─────────────────────────────────────────────────────────────────────────────
# SEC-04: Token bound to a different fault event → token_invalid
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_sec04_wrong_event_token_raises_token_invalid(
    store: ApprovalTokenStore,
    registry: FaultEventRegistry,
) -> None:
    # Mint a token for a DIFFERENT fault event
    other_token = store.mint("evt-other-fault-999", ApprovalDecision.approved)

    with pytest.raises(RemediationError) as exc_info:
        await remediation_execute(
            fault_event_id=_FAULT_ID,  # wrong event for this token
            approval_token=other_token.token,
            step_ids=_ALLOWED_STEPS,
            token_store=store,
            fault_registry=registry,
            clear_fn=_fake_clear,
        )
    assert exc_info.value.error == "token_invalid"


# ─────────────────────────────────────────────────────────────────────────────
# SEC-05: Step outside allowlist → step_not_allowed
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_sec05_unlisted_step_raises_step_not_allowed(
    store: ApprovalTokenStore,
    registry: FaultEventRegistry,
) -> None:
    token = store.mint(_FAULT_ID, ApprovalDecision.approved)

    with pytest.raises(RemediationError) as exc_info:
        await remediation_execute(
            fault_event_id=_FAULT_ID,
            approval_token=token.token,
            step_ids=["drain_node", "delete_all_data"],  # second step not allowed
            token_store=store,
            fault_registry=registry,
            clear_fn=_fake_clear,
        )
    err = exc_info.value
    assert err.error == "step_not_allowed"
    assert "delete_all_data" in err.extra.get("invalid_steps", [])


@pytest.mark.asyncio
async def test_sec05_all_invalid_steps_reported(
    store: ApprovalTokenStore,
    registry: FaultEventRegistry,
) -> None:
    token = store.mint(_FAULT_ID, ApprovalDecision.approved)

    with pytest.raises(RemediationError) as exc_info:
        await remediation_execute(
            fault_event_id=_FAULT_ID,
            approval_token=token.token,
            step_ids=["hack_firmware", "exfiltrate_data"],
            token_store=store,
            fault_registry=registry,
            clear_fn=_fake_clear,
        )
    err = exc_info.value
    invalid = err.extra.get("invalid_steps", [])
    assert "hack_firmware" in invalid
    assert "exfiltrate_data" in invalid


# ─────────────────────────────────────────────────────────────────────────────
# Happy path
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_successful_execute_returns_resolved(
    store: ApprovalTokenStore,
    registry: FaultEventRegistry,
) -> None:
    token = store.mint(_FAULT_ID, ApprovalDecision.approved)

    result = await remediation_execute(
        fault_event_id=_FAULT_ID,
        approval_token=token.token,
        step_ids=["drain_node", "gpu_reset"],
        token_store=store,
        fault_registry=registry,
        clear_fn=_fake_clear,
    )

    assert result["status"] == "resolved"
    assert result["fault_event_id"] == _FAULT_ID
    assert result["asset_state"] == "healthy"
    executed_ids = [e["step_id"] for e in result["executed"]]
    assert executed_ids == ["drain_node", "gpu_reset"]


@pytest.mark.asyncio
async def test_successful_execute_calls_clear_fn(
    store: ApprovalTokenStore,
    registry: FaultEventRegistry,
) -> None:
    token = store.mint(_FAULT_ID, ApprovalDecision.approved)
    await remediation_execute(
        fault_event_id=_FAULT_ID,
        approval_token=token.token,
        step_ids=_ALLOWED_STEPS,
        token_store=store,
        fault_registry=registry,
        clear_fn=_fake_clear,
    )
    assert _ASSET_ID in _cleared


@pytest.mark.asyncio
async def test_successful_execute_consumes_token(
    store: ApprovalTokenStore,
    registry: FaultEventRegistry,
) -> None:
    token = store.mint(_FAULT_ID, ApprovalDecision.approved)
    await remediation_execute(
        fault_event_id=_FAULT_ID,
        approval_token=token.token,
        step_ids=_ALLOWED_STEPS,
        token_store=store,
        fault_registry=registry,
        clear_fn=_fake_clear,
    )
    retrieved = store.get(token.token)
    assert retrieved is not None
    assert retrieved.consumed is True


# ─────────────────────────────────────────────────────────────────────────────
# Denied token (not_approved path)
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_denied_token_raises_not_approved(
    store: ApprovalTokenStore,
    registry: FaultEventRegistry,
) -> None:
    token = store.mint(_FAULT_ID, ApprovalDecision.denied)

    with pytest.raises(RemediationError) as exc_info:
        await remediation_execute(
            fault_event_id=_FAULT_ID,
            approval_token=token.token,
            step_ids=_ALLOWED_STEPS,
            token_store=store,
            fault_registry=registry,
            clear_fn=_fake_clear,
        )
    assert exc_info.value.error == "not_approved"
