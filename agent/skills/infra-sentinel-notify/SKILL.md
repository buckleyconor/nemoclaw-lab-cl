---
name: "infra-sentinel-notify"
description: "Posts structured activity updates and approval requests to the operator dashboard. Manages the awaiting_approval gate by updating fault status and presenting the diagnosis summary to the operator. Use to request human approval after diagnosis, or to post any structured progress update during the fault lifecycle."
license: "Apache-2.0"
---

# Skill: Operator Notification

## Purpose

Communicate fault findings and remediation intent to the human operator via the dashboard activity feed and the approval gate. Ensure operators have enough context to make an informed decision before any action is taken.

## Approval Request Flow

After `infra-sentinel-diagnose` completes:

1. Post activity: `"Posting fault notification to operator dashboard"` (step: `present`)

2. Compose the approval request message:
   > **Fault:** `<canonical_signature>` on `<asset_id>`
   > **Diagnosis:** KB article `<kb_id>` matched with `<score%>` confidence.
   > **Proposed action:** `<N>` remediation steps — `<step_label_1>`, `<step_label_2>`, ...
   > **Expected outcome:** Asset returns to healthy state. Estimated duration: ~`<duration>`.

3. Post activity: `"⏸ Awaiting human approval — operator must Approve or Deny in the dashboard"` (step: `present`)

4. `PATCH /api/faults/<fault_id>/status` with `{ "status": "awaiting_approval" }`

5. Pass control to `infra-sentinel-remediate` to poll for the token.

## Activity Post Format

All activity updates are posted to `POST /api/agent/activity`:

```json
{
  "fault_event_id": "<fault_id>",
  "step": "<detect|diagnose|search_kb|present|remediate|resolved|denied>",
  "message": "<plain-language description>"
}
```

### Step Values

| Step | When to use |
|------|-------------|
| `detect` | Monitoring and log retrieval phase |
| `diagnose` | LLM analysis and signature extraction |
| `search_kb` | KB search and article retrieval |
| `present` | Approval request and operator communication |
| `remediate` | Step-by-step execution progress |
| `resolved` | Final healthy confirmation |
| `denied` | Operator denied remediation |

## Tone Guidelines

- Use `⚡` for initial fault detection.
- Use `✓` for completed steps.
- Use `▶` for in-progress steps.
- Use `✅` for positive resolution.
- Use `⏸` for human-in-the-loop gates.
- Use `⚠` for errors or partial failures.
- Keep messages under 120 characters.
- Do not use exclamation marks except in `✅` confirmations.

## Notes

- Every activity post must include a valid `fault_event_id`. Do not post activity before the fault is registered in the gateway.
- Do not post approval requests to external channels (Slack, email) without explicit operator configuration — the dashboard is the primary notification surface for this lab environment.
- The `awaiting_approval` status triggers the approval UI in the dashboard. The operator sees Approve / Deny buttons. Do not set this status until the full diagnosis is complete.
