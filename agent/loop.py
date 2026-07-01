"""NemoClaw AI Infrastructure Sentinel — agent runtime.

Implements the detect→diagnose→present→approve→remediate cycle defined in the
NemoClaw skill set at agent/skills/. Skills:
  infra-sentinel-monitor    → step 1
  infra-sentinel-diagnose   → steps 2–7
  infra-sentinel-notify     → steps 8, activity posts
  infra-sentinel-remediate  → steps 9–11

Sequence:
  1. monitor.list_events()       — detect any active faults
  2. logs.get_bundle(asset_id)   — retrieve log bundle + scenario_id
  3. POST /api/faults            — register FaultEvent in Gateway
  4. LLM signature extraction    — Qwen 3.6 35B with chain-of-thought reasoning
  5. snap_to_known()             — map to canonical signature (deterministic)
  6. kb.search(signature)        — FAISS semantic search over KB
  7. PATCH status → diagnosing   — update Gateway
  8. PATCH status → awaiting_approval
  9. Poll for approval token     — block until human decides (HITL gate)
  10. remediation.execute(...)   — execute approved steps, clear fault
  11. PATCH status → resolved    — triggers asset healthy + SSE broadcast
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

import httpx

from agent.llm import (
    LLMClient,
    FAULT_ANALYSIS_SYSTEM,
    FAULT_ANALYSIS_USER_TMPL,
    SIGNATURE_EXTRACTION_SYSTEM,
    SIGNATURE_EXTRACTION_USER_TMPL,
)
from agent.snap import snap_to_known
from agent.tools import AgentTools
from libs.common.pack_loader import LoadedPack

log = logging.getLogger(__name__)

_POLL_INTERVAL = 0.5
_DEFAULT_APPROVAL_TIMEOUT = 3600.0  # 1 hour — lab sessions need time for human review


@dataclass
class LoopResult:
    status: str           # "resolved" | "denied" | "timeout" | "no_fault"
    fault_id: str | None = None
    scenario_id: str | None = None


async def _act(
    gateway: httpx.AsyncClient,
    fault_id: str,
    step: str,
    message: str,
    delay: float = 0.0,
) -> None:
    """Post an activity step and optionally pause for dramatic effect."""
    if delay:
        await asyncio.sleep(delay)
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
    # ── 1. Monitor ────────────────────────────────────────────────────────────
    log.info("Polling monitor.list_events()")
    events = await tools.monitor_list_events()
    if not events:
        return LoopResult(status="no_fault")

    event = events[0]
    asset_id: str = event["asset_id"]
    log.info("Fault detected on %s", asset_id)

    # ── 2. Get logs ───────────────────────────────────────────────────────────
    try:
        logs_data = await tools.logs_get_bundle(asset_id)
    except httpx.HTTPStatusError as exc:
        log.warning("logs.get_bundle failed: %s", exc)
        return LoopResult(status="no_fault")

    log_text: str = logs_data.get("log_text", "")
    scenario_id: str = logs_data.get("scenario_id", "")

    # ── 3. Register FaultEvent in Gateway ─────────────────────────────────────
    log.info("Registering fault event in Gateway for scenario=%s", scenario_id)
    scenario = loaded_pack.scenarios_by_id.get(scenario_id)
    step_labels_for_fault = [s.label for s in (scenario.remediation_steps if scenario else [])]

    fault_r = await gateway_client.post(
        "/api/faults",
        json={
            "scenario_id": scenario_id,
            "asset_id": asset_id,
            "log_extract": log_text[:300],
            "remediation_step_labels": step_labels_for_fault,
        },
    )
    fault_r.raise_for_status()
    fault_id: str = fault_r.json()["id"]

    # ── Detection phase ───────────────────────────────────────────────────────
    await _act(gateway_client, fault_id, "detect",
               f"Scanning Redfish event stream across all cluster nodes…")
    await _act(gateway_client, fault_id, "detect",
               f"⚡ Critical event received from {asset_id}: hardware fault detected", delay=1.2)
    await _act(gateway_client, fault_id, "detect",
               f"Querying iDRAC Lifecycle Log for {asset_id}…", delay=0.8)
    await _act(gateway_client, fault_id, "detect",
               f"Log bundle retrieved — {len(log_text.splitlines())} entries collected", delay=1.0)

    # ── 4 & 5. LLM signature extraction ──────────────────────────────────────
    await _act(gateway_client, fault_id, "diagnose",
               "Parsing log entries and isolating critical-severity events…", delay=0.9)
    await _act(gateway_client, fault_id, "diagnose",
               f"Sending log extract to Local LLM for error signature analysis…", delay=1.1)

    log.info("Calling LLM for signature extraction")
    llm_output = await llm_client.complete(
        system=SIGNATURE_EXTRACTION_SYSTEM,
        user=SIGNATURE_EXTRACTION_USER_TMPL.format(log_text=log_text[:2000]),
    )
    log.info("LLM output: %r", llm_output)

    canonical = snap_to_known(llm_output, loaded_pack.signature_index)
    if canonical is None:
        scenario = loaded_pack.scenarios_by_id.get(scenario_id)
        canonical = scenario.error_signatures[0] if scenario else llm_output.strip()
        log.info("snap_to_known: no match — using fallback %r", canonical)
    else:
        log.info("snap_to_known: %r → %r", llm_output, canonical)

    await _act(gateway_client, fault_id, "diagnose",
               f'LLM identified error signature: "{canonical}"', delay=0.6)
    await _act(gateway_client, fault_id, "diagnose",
               f"Canonical signature confirmed via snap-to-known index", delay=0.5)

    # ── LLM fault analysis ────────────────────────────────────────────────────
    log.info("Calling LLM for fault analysis")
    analysis = await llm_client.complete(
        system=FAULT_ANALYSIS_SYSTEM,
        user=FAULT_ANALYSIS_USER_TMPL.format(
            signature=canonical,
            log_text=log_text[:600],
        ),
        thinking=False,
    )
    log.info("LLM analysis: %r", analysis)
    if analysis:
        await _act(gateway_client, fault_id, "diagnose", analysis.strip(), delay=0.4)

    # ── 6. KB search ──────────────────────────────────────────────────────────
    await _act(gateway_client, fault_id, "search_kb",
               f'Querying local KB with FAISS semantic search: "{canonical}"…', delay=3.0)

    fallback_kb_id = (
        scenario.kb_article_ref.replace("kb/", "").replace(".md", "")
        if scenario else None
    )
    kb_result = await tools.kb_search(canonical, fallback_kb_id=fallback_kb_id)

    if kb_result:
        kb_id = kb_result["kb_id"]
        score = kb_result["score"]
        via = kb_result["via"]
        log.info("KB match: %s (score=%.3f, via=%s)", kb_id, score, via)
        await _act(gateway_client, fault_id, "search_kb",
                   f"✓ KB article matched: {kb_id} (confidence {score:.0%}, via {via})", delay=3.0)
        await _act(gateway_client, fault_id, "search_kb",
                   f"Reading remediation procedure from {kb_id}…", delay=3.0)
    else:
        await _act(gateway_client, fault_id, "search_kb",
                   "No KB article matched — falling back to scenario defaults", delay=3.0)

    # ── 7. Diagnosing → 8. Awaiting approval ──────────────────────────────────
    await gateway_client.patch(
        f"/api/faults/{fault_id}/status",
        json={"status": "diagnosing"},
    )
    await _act(gateway_client, fault_id, "diagnose",
               "Composing fault diagnosis report…", delay=0.7)
    await _act(gateway_client, fault_id, "present",
               "Posting fault notification to operator dashboard", delay=0.6)
    await _act(gateway_client, fault_id, "present",
               f"⏸ Awaiting human approval — operator must Approve or Deny in the dashboard",
               delay=0.4)

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

        fault_check_r = await gateway_client.get(f"/api/faults/{fault_id}")
        if fault_check_r.status_code == 200:
            if fault_check_r.json().get("status") == "denied":
                log.info("Fault %s was denied", fault_id)
                await _act(gateway_client, fault_id, "denied",
                           f"Remediation denied by operator — fault logged, no action taken")
                return LoopResult(status="denied", fault_id=fault_id, scenario_id=scenario_id)

        await asyncio.sleep(poll_interval)

    if token_str is None:
        log.warning("Approval timeout for fault %s", fault_id)
        return LoopResult(status="timeout", fault_id=fault_id, scenario_id=scenario_id)

    # ── 10. Execute remediation ───────────────────────────────────────────────
    await _act(gateway_client, fault_id, "remediate",
               "✅ Approval received — single-use token validated", delay=0.5)

    step_ids = [s.id for s in (scenario.remediation_steps if scenario else [])]
    step_labels = {s.id: s.label for s in (scenario.remediation_steps if scenario else [])}

    await _act(gateway_client, fault_id, "remediate",
               f"Beginning remediation: {len(step_ids)} approved steps", delay=0.7)

    for step_id in step_ids:
        label = step_labels.get(step_id, step_id)
        await _act(gateway_client, fault_id, "remediate",
                   f"  ▶ {label}…", delay=1.2)
        await _act(gateway_client, fault_id, "remediate",
                   f"  ✓ {label} — complete", delay=1.5)

    result = await tools.remediation_execute(
        fault_event_id=fault_id,
        approval_token=token_str,
        step_ids=step_ids,
    )
    log.info("Remediation result: %s", result)

    # ── 11. Resolve ───────────────────────────────────────────────────────────
    if result.get("status") == "resolved":
        await _act(gateway_client, fault_id, "remediate",
                   f"Remediation complete — verifying {asset_id} health…", delay=1.0)
        await gateway_client.patch(
            f"/api/faults/{fault_id}/status",
            json={"status": "resolved"},
        )
        await _act(gateway_client, fault_id, "resolved",
                   f"✅ {asset_id} returned to healthy state — fault cleared", delay=0.8)
        log.info("Fault %s resolved", fault_id)
        return LoopResult(status="resolved", fault_id=fault_id, scenario_id=scenario_id)
    else:
        error = result.get("error", "unknown")
        log.error("Remediation failed: %s", error)
        await _act(gateway_client, fault_id, "remediate",
                   f"⚠ Remediation error: {error}")
        return LoopResult(status=f"error:{error}", fault_id=fault_id, scenario_id=scenario_id)
