---
name: "infra-sentinel-notify"
description: "Posts structured activity updates to the operator dashboard feed. Defines the step categories, tone, and formatting conventions for narrating fault investigations to human operators. Use whenever you have learned something worth telling the operator during a fault lifecycle."
license: "Apache-2.0"
---

# Skill: Operator Notification

## Purpose

Communicate fault findings and remediation intent to the human operator via the dashboard activity feed. Ensure operators have enough context to make an informed decision before any action is taken.

## Activity Post Format

All activity updates go through the `notify_post_activity` tool:

```json
{
  "fault_event_id": "<from logs_get_bundle>",
  "step": "<detect|diagnose|search_kb|present>",
  "message": "<plain-language description>"
}
```

### Step Values

| Step | When to use |
|------|-------------|
| `detect` | Event discovery and log retrieval phase |
| `diagnose` | Signature extraction and analysis |
| `search_kb` | KB search and article retrieval |
| `present` | Proposal framing and operator communication |

Execution-phase categories (`remediate`, `resolved`, `denied`) are posted by
the platform, not by you — your narration ends at `present`.

## Tone Guidelines

- Use `⚡` for initial fault detection.
- Use `✓` for completed steps.
- Use `⏸` for the human-in-the-loop gate.
- Keep messages under 120 characters.
- No exclamation marks; urgency comes from content.

## Notes

- Every activity post needs the `fault_event_id` issued by `logs_get_bundle`.
  Posts before log retrieval are rejected — fetch evidence first.
- The dashboard is the only notification surface for this lab. Do not attempt
  to reach external channels.
- Narrate what you actually observed, in your own words. Do not fabricate
  progress you have not made.
