---
name: "infra-sentinel-remediate"
description: "Executes operator-approved remediation steps against a faulted cluster node. Polls for the human approval token, validates it, runs each remediation step in sequence via the MCP tools server, and marks the fault resolved. Use after the operator has been presented with the diagnosis and has approved action."
license: "Apache-2.0"
---

# Skill: Fault Remediation

## Purpose

Execute the approved remediation procedure step-by-step, report progress to the operator dashboard, and mark the fault resolved once all steps complete successfully.

## Prerequisites

- `fault_id`, `asset_id`, `remediation_steps` from `infra-sentinel-diagnose`
- Fault status has been set to `awaiting_approval` by `infra-sentinel-notify`

## Steps

### 1. Poll for Approval Token

`GET /api/faults/<fault_id>/token` — returns `{ "token": "<jwt>" }` once the operator has approved.

Check every 500ms. If the fault status transitions to `denied`, stop immediately.

**If denied:**
Post activity: `"Remediation denied by operator — fault logged, no action taken"` (step: `denied`)
Return `{ "status": "denied" }`.

**If no approval within 600 seconds:**
Escalate via notification channel and return `{ "status": "timeout" }`. Do not auto-remediate.

**If approved:**
Post activity: `"✅ Approval received — single-use token validated"` (step: `remediate`)

### 2. Announce Remediation Start

Post activity: `"Beginning remediation: <N> approved steps"` (step: `remediate`)

### 3. Execute Steps

For each step in `remediation_steps` (in order):

Post activity: `"  ▶ <step_label>…"` (step: `remediate`) — wait ~1.2 seconds.
Post activity: `"  ✓ <step_label> — complete"` (step: `remediate`) — wait ~1.5 seconds.

After announcing all steps, call:

```
remediation_execute(
  fault_event_id=<fault_id>,
  approval_token=<token>,
  step_ids=[<step-01>, <step-02>, ...]
)
```

The tool validates the token server-side and executes each step atomically. It returns:
```json
{ "status": "resolved" }
```
or
```json
{ "status": "error", "error": "<description>" }
```

### 4. Verify and Resolve

If the tool returns `{ "status": "resolved" }`:

Post activity: `"Remediation complete — verifying <asset_id> health…"` (step: `remediate`)

`PATCH /api/faults/<fault_id>/status` with `{ "status": "resolved" }`

Post activity: `"✅ <asset_id> returned to healthy state — fault cleared"` (step: `resolved`)

Return `{ "status": "resolved" }`.

If the tool returns an error:

Post activity: `"⚠ Remediation error: <error>"` (step: `remediate`)

Do not mark the fault resolved. Escalate via the notification channel. Return `{ "status": "error", "error": "<error>" }`.

## Notes

- The approval token is single-use. Do not retry `remediation_execute` with the same token if it fails — request a new approval.
- Steps must be executed in the order defined in the scenario. Do not skip or reorder.
- If a step is a no-op (already in the desired state), the MCP tool handles this gracefully — continue to the next step.
- The `PATCH status → resolved` triggers an asset state update (healthy) and SSE broadcast to the dashboard automatically.
