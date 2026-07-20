# Vertical Pack Guide

This document explains how to create a new industry vertical for the NemoClaw Lab. All verticals use the **same agent, the same services, the same UI, and the same lab guide** — only the content changes.

---

## The Domain Pack contract

A pack lives at `packs/{pack-id}/` and contains:

```
packs/{pack-id}/
├── pack.yaml               # Pack metadata and asset list
├── simulator-profile.yaml  # Simulator emitter configuration
├── scenarios/
│   ├── scn-{slug}.yaml     # One file per fault scenario
│   └── ...
├── kb/
│   ├── KB{number}.md       # One KB article per scenario (minimum)
│   └── ...
└── bundles/
    ├── scn-{slug}.log      # Raw log text for each scenario
    └── ...
```

---

## `pack.yaml`

```yaml
id: my-vertical                    # Must match the directory name
name: "Human-readable pack name"   # Shown in the dashboard header

asset_noun:
  singular: device                 # e.g. server, laptop, rig, node
  plural: devices

fleet_label: "Fleet Health"        # Label above the health grid

monitoring_adapter: generic        # "redfish" or "generic"
                                   # Use "redfish" only for server/IPMI verticals

theme: dell-default                # Reserved for future UI theme switching

assets:
  - id: device-01
  - id: device-02
  - id: device-03
```

**Rules:**
- `id` must be a slug (lowercase, hyphens only) that matches the directory name.
- `monitoring_adapter: redfish` enables Redfish-specific log parsing. Use `generic` for everything else.
- Asset IDs are what the simulator, agent, and UI use to identify nodes in the fleet grid.

---

## `scenarios/{scn-slug}.yaml`

```yaml
id: scn-{slug}                    # Unique within the pack
pack_id: my-vertical              # Must match pack.yaml id

target_asset: device-01           # Which asset this scenario affects

fault_type: {short_slug}          # e.g. driver_tdr, thermal_throttle

emit:
  event:
    severity: Critical            # Critical | Warning | Info
    message_id: "OEM.1.0.{code}" # Used by Redfish adapter; free text for generic
  log_entries:
    - severity: Critical
      message: "Exact log line the LLM will see"
    - severity: Warning
      message: "Secondary log line"

log_bundle_ref: bundles/scn-{slug}.log   # Path to the full log file

error_signatures:
  - "Primary signature"          # First entry is used as the fallback if LLM output
  - "Alternate phrasing"         # doesn't snap to a known signature

kb_article_ref: kb/KB{number}.md

remediation_steps:
  - id: step_one                 # Step ID must match the KB article's step list
    label: "Human-readable step description shown in Operator Dashboard"
  - id: step_two
    label: "Second step"
```

**Rules:**
- `error_signatures` are indexed into the `snap_to_known` lookup. Every signature listed here is searchable. The first entry is the canonical form returned to the UI.
- `remediation_steps[].id` values are the allowlist registered with `mcp_tools` on approval. The remediation tool rejects any step ID not in this list.
- `log_entries` are emitted by the simulator to build the log bundle. They should contain the exact string patterns the LLM needs to identify the fault.

---

## `kb/{KB-number}.md`

```markdown
---
title: "Full article title — must contain the error signature words"
remediation_step_ids: [step_one, step_two, step_three]
---

# Full article title

> PLACEHOLDER — replace with real content.

## Summary
One paragraph describing the fault.

## Affected products
- Device model + hardware spec

## Symptoms
- Observable symptom 1
- Observable symptom 2

## Root cause
Explanation of why this fault occurs.

## Remediation steps

### Step 1 — step_one label
Detail...

### Step 2 — step_two label
Detail...

## References
Links to real vendor KB, if available.
```

**Rules:**
- The `title` frontmatter field is what FAISS indexes. It must contain the key terms from `error_signatures` for semantic search to score well.
- `remediation_step_ids` must match the `id` fields in `scenarios/{scn}.yaml`. These are what the Gateway registers with MCP Tools on approval.
- The article body is shown in the Operator Dashboard (KB article section) after the agent matches it.

---

## `bundles/{scn-slug}.log`

A plain-text log file simulating what the device would actually emit. The simulator returns this as the `log_text` when `logs.get_bundle` is called for this scenario.

```
# Log bundle for scn-{slug}
# Replace this placeholder with a real log extract from the target device.

2026-01-15T10:23:44Z CRITICAL device-01: [PRIMARY ERROR MESSAGE HERE]
2026-01-15T10:23:45Z WARNING  device-01: [SECONDARY CONTEXT]
2026-01-15T10:23:46Z INFO     device-01: [RELATED STATUS]
```

**Rules:**
- The file must contain the strings listed in `error_signatures` so the LLM can identify the fault.
- More realistic log context helps the LLM write a better fault analysis paragraph.
- SMEs should replace `# PLACEHOLDER` comments with real log extracts from actual device incidents.

---

## Adding the vertical to the welcome page

Edit `docs/index.html` and add a card to the `.cards` grid:

```html
<div class="card" style="--card-accent:#your-colour" data-href="./lab-guide.html?vertical=your-vertical-id">
  <div class="card-icon">🏭</div>
  <div class="card-body">
    <div class="card-title">Your Vertical Name</div>
    <div class="card-desc">One sentence describing the fleet and what kinds of faults the agent handles.</div>
  </div>
  <span class="badge active">Active</span>
  <span class="card-arrow">→</span>
</div>
```

Replace the `soon` class cards when the vertical is ready. The `--card-accent` colour sets the card top border and hover glow.

---

## Adding vertical content to the lab guide

`docs/lab-guide.html` reads `?vertical=` from the URL and swaps in vertical-specific text from a `VERTICAL_CONFIG` JavaScript object near the bottom of the `<script>` block. Add an entry:

```js
"your-vertical-id": {
  eyebrow: "NemoClaw Your Vertical Agent",
  brand: "DELL × PARTNER",
  tagline: "One sentence — what the agent watches and what you decide.",
  meta: `<span><span class="dot"></span>Live Demo</span><span>Device spec · N Assets</span><span>NemoClaw v0.0.70</span>`,

  p1lead: `<strong>NemoClaw Your Vertical Agent</strong> monitors ... one sentence summary ...`,

  p1story: `<h3>The story</h3>
    <p>Context paragraph — what devices are running, what you inject, what the agent does.</p>
    <p>Then it <strong>stops</strong>. Nothing gets fixed until you click <strong>Approve Self-Heal</strong>.</p>
    <p>The approval gate is <em>server-side</em>: a single-use token bound to that fault event.</p>`,

  p2fleet: `All N [device] tiles should be green. The Agent Activity feed and Operator Dashboard should be empty.`,

  p3intro: `This lab is self-driving. Click below to inject a fault into the fleet.`,

  beat1: `<strong>Fault injected — [device] tile goes red</strong>
    <p>...</p>
    <span class="ui-cue">→ Watch: [Fleet Label] grid (top left of dashboard)</span>`,

  beat2: `<strong>DETECT — NemoClaw agent spots the event</strong>
    <p>...</p>
    <span class="ui-cue">→ Activity feed: DETECT phase lights up</span>`,

  beat4: `<strong>DIAGNOSE — NemoClaw agent analyses the logs</strong>
    <p>... canonical fault class — <code>Your Sig 1</code>, <code>Your Sig 2</code>, etc.</p>
    <span class="ui-cue">→ Activity feed: "LLM identified error signature: …"</span>`,

  beatResolve: `<strong>[Device] goes green</strong>
    <p>Simulator reports the asset healthy.</p>
    <span class="ui-cue">→ Grid: tile turns green</span>`,

  simChipLabel: "Your Simulator",
  amapSim: {
    role: "fake [device type] · event surface",
    body: "Simulated [fleet description]. Emits events and log entries on fault injection. Safe to reset at any time."
  },

  scenarios: [
    ["scn-your-slug", "Fault description", "Error Signature", "device-01"],
  ],
  scenarioNote: "Background context about which devices remain healthy.",
},
```

All keys correspond to `data-vi="key"` attributes in the HTML — the JS swaps `innerHTML` at page load.

---

## Lab guide structure — unchanged across all verticals

The 5-part lab guide structure is fixed and applies to every vertical:

| Part | Title | Content |
|------|-------|---------|
| 1 | Welcome | What the agent does, the story, the stack architecture |
| 2 | Dashboard | Panel layout, how to reset, what to expect before a fault |
| 3 | Trigger & Watch | Fault injection button, 6 agent beats, HITL callout |
| 4 | Your Decision | Operator Dashboard read-out, Approve/Deny, what happens after |
| 5 | Wrap-Up | Scenario catalogue, quick reference, agent architecture |

Only the *content* inside some beats changes per vertical (device types, fault examples, simulator name). The agent phases (DETECT → DIAGNOSE → SEARCH KB → PRESENT → REMEDIATE → RESOLVED), the Operator Dashboard layout, and the HITL approval flow are identical.

---

## UI adaptation — automatic from pack metadata

The React dashboard reads `/api/pack` on load and adapts without any frontend code changes:

| Pack field | UI element it drives |
|------------|---------------------|
| `name` | Dashboard header pack label |
| `asset_noun.singular/plural` | Fleet grid labels |
| `fleet_label` | Section heading above the fleet grid |
| `assets[].id` | One tile per asset in the fleet grid |

---

## Agent adaptation — automatic from pack content

The agent is vertical-blind. The only vertical-specific behaviour comes from the pack:

- **Error signatures** — `scenarios[].error_signatures` populate the `snap_to_known` index used to canonicalise LLM output.
- **Remediation steps** — `scenarios[].remediation_steps` drive the step labels in the Operator Dashboard and the allowlist registered with MCP Tools.
- **KB articles** — `kb/*.md` are indexed into FAISS at startup for semantic search.
- **Log bundles** — `bundles/*.log` are served by the simulator when the agent calls `logs.get_bundle`.

No changes to `agent/loop.py`, `agent/skills/`, or `agent/soul.md` are required for a new vertical using the same fault lifecycle pattern (detect → diagnose → present → remediate).

This remains true after the move to an LLM-driven, dynamic skill/tool-calling loop (ADR-010): the MCP tool catalog, the skill files, and `soul.md` are pack-agnostic, so a new vertical still requires only pack content — no agent code or prompt changes. (The one known exception, tracked separately, is that `soul.md`'s Identity section currently names the flagship GPU-cluster vertical explicitly; see ADR-010's consequences.)

---

## Checklist for a new vertical

- [ ] Create `packs/{id}/pack.yaml`
- [ ] Create `packs/{id}/simulator-profile.yaml`
- [ ] Author at least 2 scenarios in `packs/{id}/scenarios/`
- [ ] Author one KB article per scenario in `packs/{id}/kb/`
- [ ] Create log bundles in `packs/{id}/bundles/`
- [ ] Add a card to `docs/index.html`
- [ ] Add an entry to `VERTICAL_CONFIG` in `docs/lab-guide.html`
- [ ] Set `PACK_ID={id}` in `.env` and restart services
- [ ] Verify: `curl http://localhost:8001/api/pack` returns the new pack
- [ ] Verify: fleet grid shows the correct number of asset tiles
- [ ] Inject a fault and confirm the full detect → diagnose → present → remediate flow
