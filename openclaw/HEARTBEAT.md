# Heartbeat checklist

Run the standing Infrastructure Fault Response order on every heartbeat.

1. Call `monitor_list_events` FIRST. Never answer a heartbeat from memory or
   assumption — you cannot know whether anything needs attention until this call
   has returned.
2. If it returns no active events, reply exactly: HEARTBEAT_OK
3. If it returns any event, work that one event end to end, in this order, with
   these exact tools:
   1. `logs_get_bundle(asset_id)` — this is what mints the `fault_event_id`.
      Nothing downstream works without it; `remediation_propose` before it fails
      with `no_registered_fault`.
   2. `notify_post_activity(fault_event_id, step="detect", ...)`
   3. `kb_search(<primary error signature>)` — quote the article it matches.
   4. `notify_post_activity(fault_event_id, step="diagnose", ...)` and again with
      `step="search_kb"`.
   5. `remediation_propose(fault_event_id, step_ids=[...])`
   6. `notify_post_activity(fault_event_id, step="present", ...)`

   A heartbeat that found an event is not finished by *describing* it. Do not
   answer with a markdown table, a summary, or `monitor_get_asset` alone: the
   operator's dashboard only moves when you call `notify_post_activity`, and the
   approval card only exists once `remediation_propose` returns
   `proposal_recorded`. If you have not called both of those, you have not
   handled the fault.

   "I already mentioned this fault earlier in this conversation" is NOT a reason
   to skip the program. This conversation is not the operator's dashboard: the
   dashboard is reset independently and can be empty while you remember having
   reported the event. `monitor_list_events` returning an event is the only
   evidence that counts — report and propose again.
