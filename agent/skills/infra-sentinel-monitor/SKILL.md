---
name: "infra-sentinel-monitor"
description: "Checks for active hardware fault signals on the cluster. Call at the start of a session, or any time you want to check whether a new fault has appeared. Returns the asset_id and event metadata needed to begin diagnosis."
license: "Apache-2.0"
---

# Skill: Infrastructure Monitor

## Purpose

Detect active fault conditions on any monitored asset. This is usually your first move in an investigation, but you decide when to call it — nothing calls it for you.

## Using `monitor_list_events`

Call with no arguments. It returns a list of active fault events, each with an `asset_id` and event metadata.

- If the list is empty: there's nothing to investigate right now. Say so and stop — don't invent a fault.
- If one or more events are present: pick one to investigate. The event carries the `asset_id` you'll need for `logs_get_bundle`.

`monitor_get_asset` and `monitor_list_assets` give you supplementary detail on a specific asset or the full fleet, if you need more context than the event alone provides — use them when they'd actually change your next decision, not as a matter of routine.

## Judgment notes

- Investigate one fault at a time. If multiple events are active, note that in your narration, but focus your remediation proposal on the one you're actively diagnosing — other events remain for a future turn.
- Fault registration in the system happens automatically once you've committed to investigating an event; you don't need to do anything to "start the clock" beyond deciding to look into it.
- The simulated event stream rotates. An event present now may not be present on a later poll — don't assume you can defer and come back to it.
