"""NemoClaw AI Infrastructure Sentinel — agent runtime (ADR-010).

run_agent_loop() drives one fault investigation as a bounded, LLM-driven
tool-calling loop. The system prompt is soul.md plus the five SKILL.md files
(agent/prompt.py); the tool catalogue comes from the MCP server via
AgentTools.list_llm_tool_schemas(). The LLM decides which tool to call next:
monitor.*, logs.get_bundle, kb.search, notify.post_activity,
remediation.propose.

Deterministic harness responsibilities — never delegated to the LLM:
  wake-up pre-poll    monitor.list_events; idle polls never spend an LLM turn
  fault registration  POST /api/faults on the first successful logs.get_bundle
  status transitions  diagnosing / resolved PATCHes to the Gateway
  approval gate       poll GET /api/faults/{id}/token; denied/timeout handling
  execution           remediation.execute with the token — the LLM never sees
                      this tool's schema or any token in its context
  turn-cap fallback   if the LLM never proposes within _MAX_LLM_TURNS, the
                      harness proposes the scenario's default steps so a demo
                      session never stalls
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field

import httpx

from agent.llm import LLMClient
from agent.prompt import build_system_prompt
from agent.tools import AgentTools, to_function_name, LLM_EXPOSED_TOOLS
from libs.common.pack_loader import LoadedPack

log = logging.getLogger(__name__)

_POLL_INTERVAL = 0.5
_DEFAULT_APPROVAL_TIMEOUT = 3600.0  # 1 hour — lab sessions need time for human review
_MAX_LLM_TURNS = 8  # hard cap per fault cycle (ADR-010)

# Underscored function names the harness will dispatch on the LLM's behalf.
# Anything else the model emits is refused (defense in depth on top of the
# schema allowlist in agent/tools.py).
_LLM_CALLABLE = frozenset(to_function_name(n) for n in LLM_EXPOSED_TOOLS)

_KICKOFF_PROMPT = (
    "A monitoring wake-up signal fired: at least one asset is reporting a "
    "fault. Investigate using your tools. Narrate what you find for the "
    "operator as you go. When you have enough evidence, propose remediation "
    "once, then stop calling tools and wait for the human decision."
)


@dataclass
class LoopResult:
    status: str           # "resolved" | "denied" | "timeout" | "no_fault"
    fault_id: str | None = None
    scenario_id: str | None = None


@dataclass
class _FaultContext:
    """Harness-tracked state for the current investigation."""

    fault_id: str | None = None
    scenario_id: str = ""
    asset_id: str = ""
    proposed_step_ids: list[str] | None = None
    llm_turns_used: int = 0
    refused_tool_calls: list[str] = field(default_factory=list)


async def _post_activity(
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


def _scenario_step_ids(loaded_pack: LoadedPack, scenario_id: str) -> list[str]:
    scenario = loaded_pack.scenarios_by_id.get(scenario_id)
    return [s.id for s in (scenario.remediation_steps if scenario else [])]


async def _register_fault_from_logs(
    gateway: httpx.AsyncClient,
    loaded_pack: LoadedPack,
    ctx: _FaultContext,
    result_text: str,
) -> str:
    """On the first successful log fetch, register the FaultEvent (harness-only).

    The fault_event_id is appended to the tool result so the LLM learns it the
    same way it learns everything else — from tool output, not from the harness
    whispering out-of-band.
    """
    try:
        bundle = json.loads(result_text)
    except json.JSONDecodeError:
        return result_text
    if not isinstance(bundle, dict) or "log_text" not in bundle:
        return result_text  # error payload — pass through untouched

    if ctx.fault_id is None:
        ctx.scenario_id = bundle.get("scenario_id", "")
        ctx.asset_id = bundle.get("asset_id", "")
        scenario = loaded_pack.scenarios_by_id.get(ctx.scenario_id)
        step_labels = [s.label for s in (scenario.remediation_steps if scenario else [])]

        fault_r = await gateway.post(
            "/api/faults",
            json={
                "scenario_id": ctx.scenario_id,
                "asset_id": ctx.asset_id,
                "log_extract": bundle.get("log_text", "")[:300],
                "remediation_step_labels": step_labels,
            },
        )
        fault_r.raise_for_status()
        ctx.fault_id = fault_r.json()["id"]
        log.info("Registered fault %s (scenario=%s)", ctx.fault_id, ctx.scenario_id)

        await gateway.patch(
            f"/api/faults/{ctx.fault_id}/status",
            json={"status": "diagnosing"},
        )
        # Verbose step-by-step narration so the operator can follow exactly
        # what the agent is doing (posted here because the fault id only
        # exists from this moment; timestamps keep the ordering).
        n_entries = len(bundle.get("log_text", "").splitlines())
        await _post_activity(
            gateway, ctx.fault_id, "detect",
            f"⚡ Fault detected — critical event received from {ctx.asset_id}",
        )
        await _post_activity(
            gateway, ctx.fault_id, "detect",
            f"Pulling hardware log files from {ctx.asset_id} (lifecycle log via MCP)…",
        )
        await _post_activity(
            gateway, ctx.fault_id, "detect",
            f"Log bundle retrieved — {n_entries} entries collected",
        )
        await _post_activity(
            gateway, ctx.fault_id, "diagnose",
            "Extracting the error from the log bundle — querying the Local LLM "
            "for signature analysis and fault assessment…",
        )

    bundle["fault_event_id"] = ctx.fault_id
    return json.dumps(bundle)


async def _record_kb_result(
    gateway: httpx.AsyncClient,
    ctx: _FaultContext,
    arguments: dict,
    result_text: str,
) -> None:
    """Narrate the KB outcome and persist the diagnosis on the FaultEvent
    (signature + KB match) so the Operator Dashboard can render it."""
    signature = str(arguments.get("signature", "")).strip()
    try:
        kb = json.loads(result_text)
    except json.JSONDecodeError:
        kb = None

    if isinstance(kb, dict) and kb.get("kb_id"):
        title = kb.get("title") or kb["kb_id"]
        score = float(kb.get("score") or 0.0)
        await _post_activity(
            gateway, ctx.fault_id, "search_kb",
            f"✓ Matched {kb['kb_id']} — {title} (confidence {score:.0%}, via {kb.get('via', '?')})",
        )
        diagnosis = {
            "kb_article_id": kb["kb_id"],
            "kb_title": title,
            "kb_score": score,
        }
    else:
        await _post_activity(
            gateway, ctx.fault_id, "search_kb",
            "No KB article matched — falling back to scenario default remediation steps",
        )
        diagnosis = {}

    if signature:
        diagnosis["error_signature"] = signature
    if diagnosis:
        try:
            await gateway.patch(f"/api/faults/{ctx.fault_id}/diagnosis", json=diagnosis)
        except Exception:
            log.warning("Failed to record diagnosis for fault %s", ctx.fault_id)


async def _dispatch(
    tools: AgentTools,
    gateway: httpx.AsyncClient,
    loaded_pack: LoadedPack,
    ctx: _FaultContext,
    name: str,
    arguments: dict,
) -> str:
    """Dispatch one model-issued tool call, with harness safety overrides."""
    if name not in _LLM_CALLABLE:
        # e.g. a hallucinated remediation_execute call — refuse, never forward.
        log.warning("LLM attempted un-exposed tool %r — refused", name)
        ctx.refused_tool_calls.append(name)
        return json.dumps(
            {
                "status": "error",
                "error": f"unknown_tool: {name}",
                "available_tools": sorted(_LLM_CALLABLE),
            }
        )

    if name in ("notify_post_activity", "remediation_propose"):
        if ctx.fault_id is None:
            return json.dumps(
                {
                    "status": "error",
                    "error": "no_registered_fault",
                    "hint": "Fetch the log bundle first — the fault event id is "
                            "issued when evidence is retrieved.",
                }
            )
        # The LLM cannot address a different fault than the one under investigation.
        arguments = {**arguments, "fault_event_id": ctx.fault_id}

    if name == "remediation_propose":
        # Sanitize to the scenario's allowlist; empty/garbage falls back to the
        # full default list. remediation.execute re-validates server-side
        # (SEC-05) — this just keeps the proposal meaningful for the operator.
        allowed = _scenario_step_ids(loaded_pack, ctx.scenario_id)
        requested = [s for s in list(arguments.get("step_ids") or []) if s in allowed]
        arguments["step_ids"] = requested or allowed

    if name == "kb_search" and not arguments.get("fallback_kb_id") and ctx.scenario_id:
        scenario = loaded_pack.scenarios_by_id.get(ctx.scenario_id)
        if scenario:
            arguments["fallback_kb_id"] = (
                scenario.kb_article_ref.replace("kb/", "").replace(".md", "")
            )

    # Deterministic step narration — the operator sees exactly what the agent
    # is doing regardless of how chatty the model feels this run.
    if name == "kb_search" and ctx.fault_id:
        await _post_activity(
            gateway, ctx.fault_id, "search_kb",
            f'Performing local KB semantic search (FAISS): "{arguments.get("signature", "")}"…',
        )
    elif name == "remediation_propose" and ctx.fault_id:
        await _post_activity(
            gateway, ctx.fault_id, "present",
            "Presenting problem details, impact assessment and KB remediation "
            "steps to the operator…",
        )

    result_text = await tools.call_llm_tool(name, arguments)

    if name == "logs_get_bundle":
        result_text = await _register_fault_from_logs(gateway, loaded_pack, ctx, result_text)
    elif name == "kb_search" and ctx.fault_id:
        await _record_kb_result(gateway, ctx, arguments, result_text)
    elif name == "remediation_propose":
        try:
            recorded = json.loads(result_text).get("status") == "proposal_recorded"
        except (json.JSONDecodeError, AttributeError):
            recorded = False
        if recorded:
            ctx.proposed_step_ids = list(arguments["step_ids"])
            log.info("LLM proposed remediation: %s", ctx.proposed_step_ids)

    return result_text


async def _run_llm_investigation(
    tools: AgentTools,
    gateway: httpx.AsyncClient,
    llm_client: LLMClient,
    loaded_pack: LoadedPack,
    ctx: _FaultContext,
) -> None:
    """The bounded ReAct loop: the LLM reasons and calls tools until it
    proposes remediation, stops on its own, or hits the turn cap."""
    schemas = await tools.list_llm_tool_schemas()
    # Defense in depth: hard-fail if the execute tool ever leaks into the
    # catalogue rather than silently handing it to the model.
    for schema in schemas:
        if schema["function"]["name"] == "remediation_execute":
            raise RuntimeError("remediation_execute must never be exposed to the LLM")

    messages: list[dict] = [
        {"role": "system", "content": build_system_prompt()},
        {"role": "user", "content": _KICKOFF_PROMPT},
    ]

    for _ in range(_MAX_LLM_TURNS):
        ctx.llm_turns_used += 1
        assistant = await llm_client.chat(messages, schemas)
        messages.append(assistant)

        # Plain prose from the model is surfaced to the operator as diagnosis
        # narration (once there is a fault to attach it to).
        content = (assistant.get("content") or "").strip()
        if content and ctx.fault_id:
            await _post_activity(gateway, ctx.fault_id, "diagnose", content[:300])

        tool_calls = assistant.get("tool_calls") or []
        if not tool_calls:
            break  # model stopped reasoning on its own

        for tc in tool_calls:
            fn = tc.get("function", {})
            try:
                arguments = json.loads(fn.get("arguments") or "{}")
            except json.JSONDecodeError:
                arguments = {}
            if not isinstance(arguments, dict):
                arguments = {}
            result_text = await _dispatch(
                tools, gateway, loaded_pack, ctx, fn.get("name", ""), arguments
            )
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tc.get("id", ""),
                    "content": result_text,
                }
            )

        if ctx.proposed_step_ids is not None:
            break  # proposal recorded — hand over to the approval gate


async def run_agent_loop(
    tools: AgentTools,
    gateway_client: httpx.AsyncClient,
    llm_client: LLMClient,
    loaded_pack: LoadedPack,
    *,
    approval_timeout: float = _DEFAULT_APPROVAL_TIMEOUT,
    poll_interval: float = _POLL_INTERVAL,
) -> LoopResult:
    # ── Wake-up pre-poll (deterministic; idle polls never spend an LLM turn) ──
    events = await tools.monitor_list_events()
    if not events:
        return LoopResult(status="no_fault")
    log.info("Fault signal present — starting LLM investigation")

    ctx = _FaultContext()
    await _run_llm_investigation(tools, gateway_client, llm_client, loaded_pack, ctx)

    # The LLM never got as far as evidence (e.g. the log bundle 404'd because
    # the scenario was cleared between polls) — nothing to gate on.
    if ctx.fault_id is None:
        log.info("Investigation ended without a registered fault (turns=%d)", ctx.llm_turns_used)
        return LoopResult(status="no_fault")

    # ── Turn-cap fallback (ADR-010): the demo's reliability floor ────────────
    if ctx.proposed_step_ids is None:
        default_steps = _scenario_step_ids(loaded_pack, ctx.scenario_id)
        log.warning(
            "LLM did not propose within %d turns — falling back to scenario defaults %s",
            _MAX_LLM_TURNS, default_steps,
        )
        await tools.call_llm_tool(
            "remediation_propose",
            {"fault_event_id": ctx.fault_id, "step_ids": default_steps},
        )
        ctx.proposed_step_ids = default_steps

    # ── Approval gate + execution — identical trust model to pre-ADR-010 ─────
    return await _await_approval_and_execute(
        tools, gateway_client, loaded_pack, ctx, approval_timeout, poll_interval
    )


async def _await_approval_and_execute(
    tools: AgentTools,
    gateway: httpx.AsyncClient,
    loaded_pack: LoadedPack,
    ctx: _FaultContext,
    approval_timeout: float,
    poll_interval: float,
) -> LoopResult:
    """Harness-only from here on: token poll, execution, resolution.

    The token is retrieved over a trusted server-to-server path and passed
    straight into remediation.execute — it never enters any LLM message.
    """
    fault_id = ctx.fault_id
    assert fault_id is not None

    deadline = asyncio.get_event_loop().time() + approval_timeout
    token_str: str | None = None

    while asyncio.get_event_loop().time() < deadline:
        token_r = await gateway.get(f"/api/faults/{fault_id}/token")
        if token_r.status_code == 200:
            token_str = token_r.json()["token"]
            log.info("Approval token received for fault %s", fault_id)
            break

        fault_check_r = await gateway.get(f"/api/faults/{fault_id}")
        if fault_check_r.status_code == 200:
            if fault_check_r.json().get("status") == "denied":
                log.info("Fault %s was denied", fault_id)
                await _post_activity(
                    gateway, fault_id, "denied",
                    "Remediation denied by operator — fault logged, no action taken",
                )
                return LoopResult(status="denied", fault_id=fault_id, scenario_id=ctx.scenario_id)

        await asyncio.sleep(poll_interval)

    if token_str is None:
        log.warning("Approval timeout for fault %s", fault_id)
        return LoopResult(status="timeout", fault_id=fault_id, scenario_id=ctx.scenario_id)

    # ── Execute remediation (runtime-posted narration per the notify skill) ──
    await _post_activity(gateway, fault_id, "remediate",
                         "✅ Approval received — single-use token validated", delay=0.5)

    scenario = loaded_pack.scenarios_by_id.get(ctx.scenario_id)
    step_labels = {s.id: s.label for s in (scenario.remediation_steps if scenario else [])}
    step_ids = ctx.proposed_step_ids or list(step_labels)

    await _post_activity(gateway, fault_id, "remediate",
                         f"Beginning remediation: {len(step_ids)} approved steps", delay=0.7)

    for step_id in step_ids:
        label = step_labels.get(step_id, step_id)
        await _post_activity(gateway, fault_id, "remediate", f"  ▶ {label}…", delay=1.2)
        await _post_activity(gateway, fault_id, "remediate", f"  ✓ {label} — complete", delay=1.5)

    result = await tools.remediation_execute(
        fault_event_id=fault_id,
        approval_token=token_str,
        step_ids=step_ids,
    )
    log.info("Remediation result: %s", result)

    if result.get("status") == "resolved":
        await _post_activity(gateway, fault_id, "remediate",
                             f"Remediation complete — verifying {ctx.asset_id} health…", delay=1.0)
        await gateway.patch(
            f"/api/faults/{fault_id}/status",
            json={"status": "resolved"},
        )
        await _post_activity(gateway, fault_id, "resolved",
                             f"✅ {ctx.asset_id} returned to healthy state — fault cleared", delay=0.8)
        log.info("Fault %s resolved", fault_id)
        return LoopResult(status="resolved", fault_id=fault_id, scenario_id=ctx.scenario_id)

    error = result.get("error", "unknown")
    log.error("Remediation failed: %s", error)
    await _post_activity(gateway, fault_id, "remediate", f"⚠ Remediation error: {error}")
    return LoopResult(status=f"error:{error}", fault_id=fault_id, scenario_id=ctx.scenario_id)
