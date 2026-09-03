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

import httpx

from libs.common.models import ApprovalDecision
from services.mcp_tools.fault_registry import FaultEventRegistry
from services.mcp_tools.token_store import ApprovalTokenStore


class _ClearFn(Protocol):
    async def __call__(self, asset_id: str) -> bool: ...


async def remediation_propose(
    gateway_client: httpx.AsyncClient,
    fault_event_id: str,
    step_ids: list[str],
    summary: str = "",
) -> dict:
    """Record the agent's recommended remediation plan and request approval.

    The LLM-callable half of the propose/execute split (ADR-010). Takes no
    token and returns no token: its only side effects are moving the fault to
    ``awaiting_approval``, recording the agent's diagnosis summary, and
    posting the proposal to the activity feed. The actual execution path
    (``remediation_execute`` below) is never exposed to the LLM and validates
    a human-minted token independently of anything that happens here.

    Args:
        gateway_client:  Configured httpx.AsyncClient pointed at the Gateway.
        fault_event_id:  FaultEvent id being diagnosed.
        step_ids:        Ordered remediation step ids the agent recommends.
        summary:         Plain-language diagnosis summary for the operator
                         (shown on the Operator Dashboard before approve/deny).

    Returns:
        dict with status "proposal_recorded", or an error dict.
    """
    if summary:
        await gateway_client.patch(
            f"/api/faults/{fault_event_id}/diagnosis",
            json={"analysis": summary[:1000]},
        )
    r = await gateway_client.patch(
        f"/api/faults/{fault_event_id}/status",
        json={"status": "awaiting_approval"},
    )
    if r.status_code == 404:
        return {"status": "error", "error": "fault_not_found"}
    r.raise_for_status()

    steps_label = ", ".join(step_ids) if step_ids else "(scenario defaults)"
    await gateway_client.post(
        "/api/agent/activity",
        json={
            "fault_event_id": fault_event_id,
            "step": "present",
            "message": f"⏸ Remediation plan proposed — awaiting operator approval: {steps_label}",
        },
    )
    return {
        "status": "proposal_recorded",
        "fault_event_id": fault_event_id,
        "step_ids": step_ids,
        "note": "Execution requires human approval. Stop calling tools and wait.",
    }


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
                         Simulator for the given asset_id and returns whether
                         it actually succeeded.

    Returns:
        dict with keys: status ("resolved"), executed (list), asset_state.

    Raises:
        RemediationError: On any security check failure, or if the
            Simulator's clear doesn't confirm success (``simulator_clear_failed``)
            — reporting "resolved" without that confirmation is what let a
            still-faulted asset get marked healthy, which the next monitor
            poll would then re-detect as a brand new fault.
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

    # ── Execute: clear the fault, only then consume the token ──────────────
    # Clear before consuming so an infra hiccup here doesn't burn the human's
    # single-use approval for nothing — token_store.consume() only runs once
    # the Simulator has actually confirmed the asset is healthy again.
    executed = [{"step_id": sid, "status": "executed"} for sid in step_ids]
    cleared = await clear_fn(record.asset_id)
    if not cleared:
        raise RemediationError("simulator_clear_failed", fault_event_id=fault_event_id)

    token_store.consume(approval_token)

    return {
        "status": "resolved",
        "fault_event_id": fault_event_id,
        "executed": executed,
        "asset_state": "healthy",
    }
