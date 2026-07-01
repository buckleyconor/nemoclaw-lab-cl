"""NemoClaw Infrastructure Sentinel — agent reasoning loop.

Implements the detect→diagnose→present→approve→remediate cycle described in §2.4.
The loop is agnostic to the underlying tool transport (MCP or direct) via
``AgentTools``, and agnostic to the LLM via ``LLMClient``.

Sequence:
  1. monitor.list_events()       — detect any active faults
  2. logs.get_bundle(asset_id)   — retrieve log bundle + scenario_id
  3. POST /api/faults            — register FaultEvent in Gateway
  4. LLM signature extraction    — LLM reads logs, returns candidate signature
  5. snap_to_known()             — map to canonical signature (deterministic)
  6. kb.search(signature)        — find KB article
  7. PATCH status → diagnosing   — update Gateway
  8. PATCH status → awaiting_approval
  9. Poll for approval token     — block until human decides
  10. remediation.execute(...)   — execute approved steps, clear fault
  11. PATCH status → resolved
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Callable, Coroutine

import httpx

from agent.llm import (
    LLMClient,
    SIGNATURE_EXTRACTION_SYSTEM,
    SIGNATURE_EXTRACTION_USER_TMPL,
)
from agent.snap import snap_to_known
from agent.tools import AgentTools
from libs.common.pack_loader import LoadedPack

log = logging.getLogger(__name__)

_POLL_INTERVAL = 0.5   # seconds between token availability polls
_DEFAULT_APPROVAL_TIMEOUT = 600.0   # 10 minutes max wait for human


@dataclass
class LoopResult:
    status: str           # "resolved" | "denied" | "timeout" | "no_fault"
    fault_id: str | None = None
    scenario_id: str | None = None


async def _post_activity(
    gateway: httpx.AsyncClient,
    fault_id: str,
    step: str,
    message: str,
) -> None:
    try:
        await gateway.post(
            "/api/agent/activity",
            json={"fault_event_id": fault_id, "step": step, "message": message},
        )
    except Exception:
        log.warning("Failed to post activity '%s'", step)


async def run_agent_loop(
    tools: AgentTools,
    gateway_client: httpx.AsyncClient,
    llm_client: LLMClient,
    loaded_pack: LoadedPack,
    *,
    approval_timeout: float = _DEFAULT_APPROVAL_TIMEOUT,
    poll_interval: float = _POLL_INTERVAL,
) -> LoopResult:
    """Run one pass of the agent monitoring loop.

    Returns immediately with ``status="no_fault"`` when no active faults are
    found.  Blocks until human approval (or timeout/denial) when a fault is
    detected.
    """
    # ── 1. Monitor ────────────────────────────────────────────────────────────
    log.info("Polling monitor.list_events()")
    events = await tools.monitor_list_events()
    if not events:
        log.info("No active faults detected.")
        return LoopResult(status="no_fault")

    event = events[0]
    asset_id: str = event["asset_id"]
    log.info("Fault detected on %s", asset_id)

    # ── 2. Get logs (also provides scenario_id) ───────────────────────────────
    log.info("Fetching logs for %s", asset_id)
    try:
        logs_data = await tools.logs_get_bundle(asset_id)
    except httpx.HTTPStatusError as exc:
        log.warning("logs.get_bundle failed: %s", exc)
        return LoopResult(status="no_fault")

    log_text: str = logs_data.get("log_text", "")
    scenario_id: str = logs_data.get("scenario_id", "")

    # ── 3. Register FaultEvent in Gateway ─────────────────────────────────────
    log.info("Registering fault event in Gateway for scenario=%s", scenario_id)
    fault_r = await gateway_client.post(
        "/api/faults",
        json={
            "scenario_id": scenario_id,
            "asset_id": asset_id,
            "log_extract": log_text[:300],
        },
    )
    fault_r.raise_for_status()
    fault_id: str = fault_r.json()["id"]

    await _post_activity(
        gateway_client, fault_id, "detect",
        f"Fault detected on {asset_id} (scenario: {scenario_id})"
    )

    # ── 4. LLM: extract signature ─────────────────────────────────────────────
    await _post_activity(gateway_client, fault_id, "diagnose", "Gathering logs and extracting error signature")
    log.info("Calling LLM for signature extraction")
    llm_output = await llm_client.complete(
        system=SIGNATURE_EXTRACTION_SYSTEM,
        user=SIGNATURE_EXTRACTION_USER_TMPL.format(log_text=log_text[:2000]),
    )
    log.info("LLM output: %r", llm_output)

    # ── 5. Snap-to-known ──────────────────────────────────────────────────────
    canonical = snap_to_known(llm_output, loaded_pack.signature_index)
    if canonical is None:
        # U-04 fallback: use scenario's own first signature
        scenario = loaded_pack.scenarios_by_id.get(scenario_id)
        canonical = scenario.error_signatures[0] if scenario else llm_output.strip()
        log.info("snap_to_known: no match — using fallback %r", canonical)
    else:
        log.info("snap_to_known: %r → %r", llm_output, canonical)

    # ── 6. KB search ──────────────────────────────────────────────────────────
    await _post_activity(gateway_client, fault_id, "search_kb", f"Searching KB for: {canonical}")
    scenario = loaded_pack.scenarios_by_id.get(scenario_id)
    fallback_kb_id = scenario.kb_article_ref.replace("kb/", "").replace(".md", "") if scenario else None
    kb_result = await tools.kb_search(canonical, fallback_kb_id=fallback_kb_id)

    if kb_result:
        log.info("KB match: %s (score=%.3f, via=%s)", kb_result["kb_id"], kb_result["score"], kb_result["via"])

    # ── 7. Update Gateway status: diagnosing ──────────────────────────────────
    await gateway_client.patch(
        f"/api/faults/{fault_id}/status",
        json={"status": "diagnosing"},
    )

    # ── 8. Present for approval ───────────────────────────────────────────────
    await _post_activity(
        gateway_client, fault_id, "present",
        f"Diagnosis complete — awaiting human approval for fault {fault_id}"
    )
    await gateway_client.patch(
        f"/api/faults/{fault_id}/status",
        json={"status": "awaiting_approval"},
    )
    log.info("Fault %s awaiting human approval", fault_id)

    # ── 9. Poll for approval token ────────────────────────────────────────────
    deadline = asyncio.get_event_loop().time() + approval_timeout
    token_str: str | None = None

    while asyncio.get_event_loop().time() < deadline:
        token_r = await gateway_client.get(f"/api/faults/{fault_id}/token")
        if token_r.status_code == 200:
            token_str = token_r.json()["token"]
            log.info("Approval token received for fault %s", fault_id)
            break

        # Check for denial
        fault_check_r = await gateway_client.get(f"/api/faults/{fault_id}")
        if fault_check_r.status_code == 200:
            current_status = fault_check_r.json().get("status")
            if current_status == "denied":
                log.info("Fault %s was denied", fault_id)
                return LoopResult(status="denied", fault_id=fault_id, scenario_id=scenario_id)

        await asyncio.sleep(poll_interval)

    if token_str is None:
        log.warning("Approval timeout for fault %s", fault_id)
        return LoopResult(status="timeout", fault_id=fault_id, scenario_id=scenario_id)

    # ── 10. Execute remediation ───────────────────────────────────────────────
    step_ids = [s.id for s in (scenario.remediation_steps if scenario else [])]
    await _post_activity(
        gateway_client, fault_id, "remediate",
        f"Executing {len(step_ids)} approved remediation steps"
    )

    result = await tools.remediation_execute(
        fault_event_id=fault_id,
        approval_token=token_str,
        step_ids=step_ids,
    )
    log.info("Remediation result: %s", result)

    # ── 11. Update Gateway status: resolved ───────────────────────────────────
    if result.get("status") == "resolved":
        await gateway_client.patch(
            f"/api/faults/{fault_id}/status",
            json={"status": "resolved"},
        )
        await _post_activity(
            gateway_client, fault_id, "resolved",
            f"Fault resolved on {asset_id}"
        )
        log.info("Fault %s resolved", fault_id)
        return LoopResult(status="resolved", fault_id=fault_id, scenario_id=scenario_id)
    else:
        error = result.get("error", "unknown")
        log.error("Remediation failed: %s", error)
        return LoopResult(status=f"error:{error}", fault_id=fault_id, scenario_id=scenario_id)
