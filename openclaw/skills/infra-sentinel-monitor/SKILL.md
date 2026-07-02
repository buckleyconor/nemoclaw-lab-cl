---
name: "infra-sentinel-monitor"
description: "Polls the Redfish event stream for active hardware fault signals on the GPU cluster. Detects new fault events and returns the asset_id and event metadata needed to initiate the diagnosis phase. Use at the start of each wake-up or when checking for new incidents."
license: "Apache-2.0"
---

# Skill: Infrastructure Monitor

## Purpose

Poll the Redfish hardware event bus to detect active fault conditions on any cluster node. Return the first unprocessed fault event for downstream diagnosis.

## Steps

1. Call the `monitor_list_events` tool with no arguments.
2. Inspect the returned list.
   - If the list is empty, no active faults. Report status: `no_fault`. Done for this turn.
   - If one or more events are present, take the first event.
3. Extract the `asset_id` from the event. This identifies the affected node (e.g. `gpu-server-01`).
4. Proceed to the `infra-sentinel-diagnose` skill with `asset_id` and the full event object.

## Output

```json
{
  "asset_id": "<string>",
  "event": { ... }
}
```

Or `{ "status": "no_fault" }` when nothing is active.

## Notes

- Only process one event per turn. Additional events will be processed in subsequent wake-ups.
- Dashboard narration (`notify_post_activity`) requires a `fault_event_id`, which is issued when the log bundle is retrieved — so do not narrate yet; fetch the logs first.
- The Redfish surface rotates events; an event returned now may not be present in future polls. Capture all fields before proceeding.
