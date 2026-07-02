---
name: "infra-sentinel-notify"
description: "Posts plain-language progress updates to the operator dashboard activity feed. Use any time you've learned something worth telling the operator, not just at fixed checkpoints."
license: "Apache-2.0"
---

# Skill: Operator Notification

## Purpose

Keep the operator informed of what you're doing and why, in your own words. This is the mechanism by which the operator builds situational awareness before they're asked to approve anything.

## Using `notify_post_activity`

Call with:

```json
{
  "fault_event_id": "<fault_id>",
  "step": "<detect|diagnose|search_kb|present|remediate|resolved|denied>",
  "message": "<plain-language description>"
}
```

`step` categorizes the update for the dashboard's activity feed; `message` is free text you compose — there is no fixed template to fill in. Write what actually happened, not a canned phrase.

### Step values

| Step | When to use |
|------|-------------|
| `detect` | Noting a fault event or initial evidence |
| `diagnose` | Reasoning about the signature or KB match |
| `search_kb` | KB search results |
| `present` | Summarizing your findings and proposed action for the operator |
| `remediate` | Progress once a proposal is made (posted by the runtime once execution begins — you won't usually post this one yourself) |
| `resolved` / `denied` | Terminal states (posted by the runtime once the outcome is known) |

## Style

Keep messages concise and factual — under ~120 characters where possible. Calm, not alarmist; urgency comes from content, not punctuation. You may use `⚡` for a newly detected fault, `✓` for a completed finding, `⏸` when you're about to hand off to human approval, and `⚠` for something that went wrong — but these are conventions, not requirements.

## Judgment notes

- Narrate as often as it's genuinely useful — after fetching logs, after identifying a signature, after a KB match or miss. You don't need permission to explain yourself, and you don't need to wait for a fixed checkpoint.
- Before proposing remediation, post one summary update the operator can read to understand your recommendation without digging through the whole feed: what you found, what you intend to propose, and what they should expect afterward.
- This tool only posts text to the dashboard. It has no effect on fault status or infrastructure state.
