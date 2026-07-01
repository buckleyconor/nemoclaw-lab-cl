---
name: "infra-sentinel-monitor"
description: "Continuously polls the Redfish event stream for active hardware fault signals on the GPU cluster. Detects new fault events and returns the asset_id and event metadata needed to initiate the diagnosis phase. Use at the start of each agent turn or when checking for new incidents."
license: "Apache-2.0"
---

# Skill: Infrastructure Monitor

## Purpose

Poll the Redfish hardware event bus to detect active fault conditions on any cluster node. Return the first unprocessed fault event for downstream diagnosis.

## Steps

1. Call the `monitor_list_events` MCP tool with no arguments.
2. Inspect the returned list.
   - If the list is empty, no active faults. Report status: `no_fault`. Done for this turn.
   - If one or more events are present, take the first event.
3. Extract the `asset_id` from the event. This identifies the affected node (e.g. `gpu-server-01`).
4. Post an activity update: `"Scanning Redfish event stream across all cluster nodes…"` (step: `detect`)
5. Post a second activity update with the asset and fault type: `"⚡ Critical event received from <asset_id>: hardware fault detected"` (step: `detect`)
6. Pass `asset_id` and the full event object to the `infra-sentinel-diagnose` skill.

## Output

```json
{
  "asset_id": "<string>",
  "event": { ... }
}
```

Or `{ "status": "no_fault" }` when nothing is active.

## Notes

- Only process one event per agent turn. Additional events will be processed in subsequent turns.
- Do not register a fault in the gateway until after log retrieval confirms the event is valid.
- The Redfish simulator rotates events; an event returned today may not be present in future polls. Capture all fields before proceeding.
