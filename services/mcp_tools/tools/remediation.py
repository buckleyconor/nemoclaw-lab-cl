"""remediation MCP tool — HITL-gated fault remediation.

Security invariants (SEC-01..05):
  SEC-01  no token provided              → not_approved
  SEC-02  token string not in store      → token_invalid
  SEC-03  token already consumed         → token_consumed
  SEC-04  token bound to a different     → token_invalid (binding mismatch)
          fault_event_id
  SEC-05  step_id outside allowlist      → step_not_allowed

Only after all checks pass does the tool consume the token and execute steps.
"""

from __future__ import annotations

from typing import Protocol

from services.mcp_tools.fault_registry import FaultEventRegistry
from services.mcp_tools.token_store import ApprovalTokenStore
from libs.common.models import ApprovalDecision


class _ClearFn(Protocol):
    async def __call__(self, asset_id: str) -> None: ...


class RemediationError(Exception):
    """Raised when a security or validation check fails."""

    def __init__(self, error: str, **extra: object) -> None:
        super().__init__(error)
        self.error = error
        self.extra = extra

    def to_dict(self) -> dict:
        return {"status": "error", "error": self.error, **self.extra}


async def remediation_execute(
    fault_event_id: str,
    approval_token: str | None,
    step_ids: list[str],
    token_store: ApprovalTokenStore,
    fault_registry: FaultEventRegistry,
    clear_fn: _ClearFn,
) -> dict:
    """Execute approved remediation steps for a fault event.

    Args:
        fault_event_id:  FaultEvent id the agent is remediating.
        approval_token:  Token string minted by the Gateway (from human click).
        step_ids:        Ordered list of step ids to execute (must be a subset
                         of the scenario's remediation_steps).
        token_store:     ApprovalTokenStore shared with the Gateway.
        fault_registry:  FaultEventRegistry mapping event → allowed steps.
        clear_fn:        Coroutine that calls ``POST /control/clear`` on the
                         Simulator for the given asset_id.

    Returns:
        dict with keys: status ("resolved"), executed (list), asset_state.

    Raises:
        RemediationError: On any security check failure.
    """
    # ── SEC-01: no token provided ──────────────────────────────────────────
    if not approval_token:
        raise RemediationError("not_approved")

    # ── SEC-02: token not in store ─────────────────────────────────────────
    token = token_store.get(approval_token)
    if token is None:
        raise RemediationError("token_invalid")

    # ── SEC-04: binding mismatch — token is for a different fault event ────
    if token.fault_event_id != fault_event_id:
        raise RemediationError("token_invalid")

    # ── decision check ─────────────────────────────────────────────────────
    if token.decision != ApprovalDecision.approved:
        raise RemediationError("not_approved")

    # ── SEC-03: token already consumed ────────────────────────────────────
    if token.consumed:
        raise RemediationError("token_consumed")

    # ── fault event must be registered ────────────────────────────────────
    record = fault_registry.get(fault_event_id)
    if record is None:
        raise RemediationError("not_approved")

    # ── SEC-05: step outside allowlist ────────────────────────────────────
    invalid = [s for s in step_ids if s not in record.allowed_step_ids]
    if invalid:
        raise RemediationError("step_not_allowed", invalid_steps=invalid)

    # ── Consume token (single-use) ─────────────────────────────────────────
    token_store.consume(approval_token)

    # ── Execute: narrate steps, then clear fault ───────────────────────────
    executed = [{"step_id": sid, "status": "executed"} for sid in step_ids]
    await clear_fn(record.asset_id)

    return {
        "status": "resolved",
        "fault_event_id": fault_event_id,
        "executed": executed,
        "asset_state": "healthy",
    }
