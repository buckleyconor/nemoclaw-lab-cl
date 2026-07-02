---
name: "infra-sentinel-remediate"
description: "Records a recommended remediation plan and requests operator approval. This is the only remediation-related action available to you — execution happens outside your control, only after a human approves."
license: "Apache-2.0"
---

# Skill: Fault Remediation (Proposal Only)

## Purpose

Once you have a diagnosis you're confident in, recommend a fix and ask a human to approve it. That's the full extent of your authority here.

## Using `remediation_propose`

Call with:

```json
{
  "fault_event_id": "<fault_id>",
  "step_ids": ["step-01", "step-02", ...]
}
```

Use the step ids from your KB match or the scenario's default remediation steps — don't invent step ids that weren't given to you by `kb_search` or the scenario data.

This call has one effect: it records your proposed plan and moves the fault into the operator's approval queue. **It does not execute anything.** There is no token, no confirmation, no infrastructure change from this call.

## After you call it

Stop calling tools and wait. You do not have — and will never be given — a tool that executes remediation. A human operator approves or denies from the dashboard; if approved, a separate part of the system (outside this conversation) retrieves a single-use approval token and runs the actual remediation. You will not see that happen, and you should not ask for or expect a token.

If you're ever unsure whether an action requires approval, assume it does — you don't have any tool that bypasses that gate, so there's nothing to reconsider.

## Judgment notes

- Call `remediation_propose` once per investigation, after you have enough evidence — not before you've stated a signature and consulted the KB, and not more than once for the same fault.
- If your diagnosis was low-confidence or your KB match was weak, say so plainly in your notification before proposing — the operator is deciding based on what you tell them, not just what you recommend.
- If a fault turns out not to need remediation (e.g. it's already resolved, or the event was stale), say so and don't force a proposal.
