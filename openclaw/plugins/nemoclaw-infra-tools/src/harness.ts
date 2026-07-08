/**
 * Deterministic harness side-work around the LLM's tool calls, ported from
 * agent/loop.py (ADR-010) into the plugin boundary (ADR-011):
 *
 *   fault registration   POST /api/faults on the first successful log fetch;
 *                        the fault_event_id is appended to the tool result so
 *                        the model learns it from tool output, like everything
 *                        else it knows
 *   status transition    PATCH diagnosing once evidence exists
 *   step narration       activity posts the operator sees regardless of how
 *                        chatty the model feels this run
 *   diagnosis persistence PATCH /api/faults/{id}/diagnosis after kb.search so
 *                        the Operator Dashboard renders signature + KB match
 *   fault-id pinning     notify/propose calls are forced onto the fault under
 *                        investigation — the LLM cannot address another fault
 *
 * None of this is visible to or steerable by the model.
 */

export type Investigation = {
  faultId: string;
  scenarioId: string;
  assetId: string;
  /** true once recordSignature has PATCHed error_signature for this investigation. */
  signatureRecorded: boolean;
  /** true once recordKbResult has run for this investigation. */
  diagnosed: boolean;
  /** true once remediation_propose has succeeded for this investigation. */
  proposed: boolean;
};

let current: Investigation | null = null;

/** Test hook. */
export function resetInvestigation(): void {
  current = null;
}

export function currentInvestigation(): Investigation | null {
  return current;
}

async function gatewayFetch(
  gatewayUrl: string,
  method: string,
  path: string,
  body: unknown,
): Promise<Response> {
  return fetch(`${gatewayUrl}${path}`, {
    method,
    headers: { "content-type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
}

export async function postActivity(
  gatewayUrl: string,
  faultId: string,
  step: string,
  message: string,
): Promise<void> {
  try {
    await gatewayFetch(gatewayUrl, "POST", "/api/agent/activity", {
      fault_event_id: faultId,
      step,
      message,
    });
  } catch {
    // Narration is best-effort; losing a feed line must not fail the tool call.
  }
}

async function faultIsTerminal(gatewayUrl: string, faultId: string): Promise<boolean> {
  try {
    const r = await gatewayFetch(gatewayUrl, "GET", `/api/faults/${faultId}`, undefined);
    if (!r.ok) return true; // unknown fault → stale investigation
    const fault = (await r.json()) as { status?: string };
    return fault.status === "resolved" || fault.status === "denied";
  } catch {
    return false; // gateway unreachable — keep current investigation
  }
}

/**
 * Called at the top of monitor_list_events — the tool AGENTS.md says every
 * wake-up must start with. Clears a lingering investigation the Gateway
 * already considers resolved/denied *before* the model gets a chance to call
 * notify_post_activity or kb_search again this turn. Remediation resolves a
 * fault entirely server-side (services/gateway/executor.py) with no
 * involvement from this plugin, so nothing else tells the harness "that
 * investigation is over" — without this, a model that keeps narrating out of
 * habit on a later wake-up would have its diagnose/search_kb messages pinned
 * to the stale fault_event_id, showing up in the feed as if still
 * investigating a fault the hardware already reports healthy.
 */
export async function clearStaleInvestigation(gatewayUrl: string): Promise<void> {
  if (current && (await faultIsTerminal(gatewayUrl, current.faultId))) {
    current = null;
  }
}

/**
 * Called with the parsed logs.get_bundle result. Registers the fault event on
 * first evidence and stamps fault_event_id into the bundle. Returns the
 * (possibly augmented) bundle.
 *
 * The webhook wake-up plus the cron safety-net poll (AGENTS.md) both start
 * every turn with monitor_list_events, which keeps reporting an active fault
 * until it actually clears — so this can be called many times for the same
 * fault while it sits in awaiting_approval. It must stay idempotent: no new
 * fault, no repeat detect/diagnose narration, no duplicate work. It instead
 * posts one lightweight "waiting" line per repeat wake-up and tells the model
 * via investigation_stage that there's nothing left to do this turn.
 */
export async function registerFaultFromLogs(
  gatewayUrl: string,
  bundle: Record<string, unknown>,
): Promise<Record<string, unknown>> {
  if (typeof bundle.log_text !== "string") {
    return bundle; // error payload — pass through untouched
  }

  if (current && (await faultIsTerminal(gatewayUrl, current.faultId))) {
    current = null; // previous investigation concluded — this is a new one
  }

  if (current === null) {
    const scenarioId = typeof bundle.scenario_id === "string" ? bundle.scenario_id : "";
    const assetId = typeof bundle.asset_id === "string" ? bundle.asset_id : "";
    const logText = bundle.log_text;
    // The Orchestrator computes log_highlight server-side as a window of
    // real lines around wherever the scenario's own error signature actually
    // appears (dated to today, hostname included) — that's what the
    // Operator Dashboard quotes as evidence, so the operator can check it
    // against the error signature before approving. Fall back to a raw
    // slice only if an older Orchestrator hasn't started sending it yet.
    const logExtract =
      typeof bundle.log_highlight === "string" && bundle.log_highlight
        ? bundle.log_highlight
        : logText.slice(0, 300);

    const r = await gatewayFetch(gatewayUrl, "POST", "/api/faults", {
      scenario_id: scenarioId,
      asset_id: assetId,
      log_extract: logExtract,
    });
    if (!r.ok) {
      throw new Error(`fault registration failed: HTTP ${r.status}`);
    }
    const fault = (await r.json()) as { id: string };
    current = {
      faultId: fault.id,
      scenarioId,
      assetId,
      signatureRecorded: false,
      diagnosed: false,
      proposed: false,
    };

    await gatewayFetch(gatewayUrl, "PATCH", `/api/faults/${fault.id}/status`, {
      status: "diagnosing",
    });

    const entries = logText.split("\n").length;
    await postActivity(gatewayUrl, fault.id, "detect",
      `⚡ Fault detected — critical event received from ${assetId}`);
    await postActivity(gatewayUrl, fault.id, "detect",
      `Pulling hardware log files from ${assetId} (lifecycle log via MCP)…`);
    await postActivity(gatewayUrl, fault.id, "detect",
      `Log bundle retrieved — ${entries} entries collected`);
    await postActivity(gatewayUrl, fault.id, "diagnose",
      "Analysing the log bundle — extracting the primary error signature…");
  } else if (current.proposed) {
    // Repeat wake-up for a fault that's already fully diagnosed and proposed
    // — narrate that we checked and are still waiting, but do no other work.
    await postActivity(gatewayUrl, current.faultId, "waiting",
      `⏳ Checked in on ${current.assetId} — still awaiting operator decision, no new action needed.`);
  }

  return {
    ...bundle,
    fault_event_id: current.faultId,
    investigation_stage: current.proposed
      ? "awaiting_operator_decision"
      : current.diagnosed
      ? "diagnosed"
      : "new",
  };
}

/**
 * Called with the signature the model extracted from the log bundle, at the
 * moment it hands that signature to kb_search — i.e. as the log bundle passes
 * the diagnose stage, *before* the KB search runs. Persisting the signature
 * in its own PATCH gives the Operator Dashboard its staged population order:
 * the ERROR SIGNATURE card fills in first while KNOWLEDGE BASE still shows
 * "Searching…". Idempotent per investigation.
 */
export async function recordSignature(
  gatewayUrl: string,
  signature: string,
): Promise<void> {
  if (current === null || current.diagnosed || current.signatureRecorded) return;
  if (!signature) return;

  await postActivity(gatewayUrl, current.faultId, "diagnose",
    `Primary error signature extracted: "${signature}"`);
  try {
    await gatewayFetch(gatewayUrl, "PATCH",
      `/api/faults/${current.faultId}/diagnosis`, { error_signature: signature });
    current.signatureRecorded = true;
  } catch {
    // Display-only; recordKbResult still carries the signature as a fallback.
  }
}

/**
 * Called with the kb.search arguments and parsed result. Narrates the KB
 * outcome and persists the diagnosis so the Operator Dashboard renders it.
 * Idempotent per investigation — a repeat wake-up that calls kb_search again
 * (the model isn't required to check investigation_stage) must not re-post
 * the same "matched KBxxx" line or re-PATCH the same diagnosis every time.
 */
export async function recordKbResult(
  gatewayUrl: string,
  signature: string,
  kb: Record<string, unknown> | null,
): Promise<void> {
  if (current === null || current.diagnosed) return;

  const diagnosis: Record<string, unknown> = {};
  if (kb && typeof kb.kb_id === "string" && kb.kb_id) {
    const title = typeof kb.title === "string" && kb.title ? kb.title : kb.kb_id;
    const score = typeof kb.score === "number" ? kb.score : 0;
    await postActivity(gatewayUrl, current.faultId, "search_kb",
      `✓ Matched ${kb.kb_id} — ${title} (confidence ${Math.round(score * 100)}%, via ${kb.via ?? "?"})`);
    diagnosis.kb_article_id = kb.kb_id;
    diagnosis.kb_title = title;
    diagnosis.kb_score = score;
  } else {
    await postActivity(gatewayUrl, current.faultId, "search_kb",
      "No KB article matched — falling back to scenario default remediation steps");
  }

  // Fallback only — recordSignature normally persisted this already, in its
  // own earlier PATCH, so the dashboard populates signature before KB match.
  if (signature && !current.signatureRecorded) diagnosis.error_signature = signature;
  if (Object.keys(diagnosis).length > 0) {
    try {
      await gatewayFetch(gatewayUrl, "PATCH",
        `/api/faults/${current.faultId}/diagnosis`, diagnosis);
    } catch {
      // Diagnosis persistence is display-only; do not fail the tool call.
    }
  }
  current.diagnosed = true;
}

/** Result returned to the model when it calls remediation_propose a second
 * time for a fault that's already awaiting an operator decision. */
export function alreadyProposedResult(): Record<string, unknown> {
  return {
    status: "already_proposed",
    note: "This fault already has a remediation proposal awaiting operator " +
      "decision. Do not propose again — stop calling tools and wait for the next wake-up.",
  };
}

/** Marks the current investigation as proposed once remediation_propose
 * actually succeeds server-side (call after checking the result, not before). */
export function markProposed(): void {
  if (current) current.proposed = true;
}

/** Error result matching agent/loop.py's no_registered_fault refusal. */
export function noRegisteredFaultError(): Record<string, unknown> {
  return {
    status: "error",
    error: "no_registered_fault",
    hint: "Fetch the log bundle first — the fault event id is issued when evidence is retrieved.",
  };
}
