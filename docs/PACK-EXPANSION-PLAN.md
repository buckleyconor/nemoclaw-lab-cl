# Pack Expansion & Real-Data Plan

This document covers two things `docs/VERTICAL-PACK-GUIDE.md` does not:

1. **The infrastructure and agent-onboarding side of adding a pack** — how many stacks/sandboxes you need, how they're named and ported, and what's still hardcoded to the flagship GPU-cluster vertical.
2. **The plan for moving pack content from synthetic/placeholder to real logs and real KB articles** — sourcing, review, schema, and what changes on the telemetry side.

`VERTICAL-PACK-GUIDE.md` already owns the *content contract*: `pack.yaml`, `scenarios/*.yaml`, `kb/*.md`, `bundles/*.log`, and the UI wiring (`docs/index.html`, `lab-guide.html`). Read that first. This doc assumes you've read it and picks up where it stops.

---

## Current state (as of ADR-011 / this GB10 deployment)

- **One docker-compose stack serves exactly one pack.** `PACK_ID` (`docker-compose.yaml:15`) is a single global env var read independently at startup by all four services (`gateway`, `mcp-tools`, `orchestrator`, `simulator`). There is no per-request or per-session pack selection — switching packs today means restarting the stack with a different `PACK_ID`.
- **One OpenClaw sandbox serves exactly one pack**, for the same reason plus one more: `openclaw/SOUL.md`'s Identity paragraph hardcodes *"an autonomous AI agent deployed on a DELL PowerEdge XE9780L GPU compute cluster equipped with NVIDIA B300 GPUs."* This was flagged as a known gap in ADR-010's consequences and never resolved. `openclaw/skills/infra-sentinel-monitor/SKILL.md` has one similarly hardcoded phrase ("on the GPU cluster"). Everything else under `openclaw/` — `AGENTS.md`, the other three skill files, and all of the `nemoclaw-infra-tools` plugin's TypeScript — is already pack-agnostic and needs no changes per pack.
- **Six pack directories exist**, two of them demo-ready (`datacenter-xe9680`, `laptop-fleet`), four explicit scaffolds (`hpc-cluster`, `network-fabric`, `storage-nvme`, `edge-inference` — one `status: scaffold` scenario each, placeholder KB, no impact assessment).
- **All content in all six packs, including the two "active" ones, is synthetic placeholder content.** Every KB article and every log bundle in the repo carries a `PLACEHOLDER` / `SME:` comment. This is a flat, unenforced text convention — there's no schema field that distinguishes "fabricated for demo" from "sourced from a real incident/vendor doc."
- **Both monitoring adapters (`RedfishAdapter`, `GenericAdapter`) talk only to the synthetic simulator today**, not to any real device. `ADR-008` anticipated hardening `RedfishAdapter` for real endpoints and named it as future work; nothing has built on that yet.
- **The pack-facing docs (README's vertical table, `docs/index.html`'s cards, `lab-guide.html`'s `VERTICAL_CONFIG`) are already out of sync with what's on disk** — see [Known doc drift](#known-doc-drift-to-reconcile) below. Worth fixing alongside this plan, not blocking on it.

---

## Part A — Running more packs: infrastructure architecture

### The constraint

`PACK_ID` is resolved once per process at startup (`services/{gateway,mcp_tools,orchestrator,simulator}/main.py` each do `os.environ.get("PACK_ID", "datacenter-xe9680")` independently). The KB's FAISS index is also built once at `mcp-tools` startup from whichever pack that resolves to. None of this is designed to hold more than one pack's state per process.

### Two ways to add packs, and which one to use when

**Option A — one stack + one sandbox per pack (recommended near-term).**
This is what today's `laptop-fleet` onboarding would look like, and it requires **zero service code changes** — it's a direct exercise of ADR-007/008's "content not code, agent is vertical-blind" principle. Each pack gets:
- its own `docker compose -p nemoclaw-lab-{pack-id}` project, with `PACK_ID={pack-id}` and a non-overlapping port block
- its own OpenClaw sandbox (`SANDBOX_NAME=infra-sentinel-{pack-id}`), onboarded with `deploy/scripts/onboard-openclaw.sh`

Trade-off: N packs means N running stacks and N sandboxes on the host, which is exactly the bookkeeping (port collisions, ambient env var bleed, GPU contention) that made today's `laptop-fleet`-adjacent `infra-sentinel` onboarding require careful, deliberate sequencing around the pre-existing `the-king` sandbox. This scales linearly in operational overhead, not code.

**Option B — single stack, pack selected per request/session (larger effort, not recommended yet).**
Would mean threading a pack identifier through every request instead of reading it once from the environment: `gateway`/`orchestrator`/`mcp-tools` would need to resolve pack context per call, and `mcp-tools` would need to hold multiple packs' FAISS indices in memory simultaneously rather than building one at startup. This is a real refactor across all four services, not a config change, and there's no product requirement yet for concurrent-pack demos in a single stack. Don't build this speculatively — revisit only if "run two packs side by side without spinning up a second stack" becomes an actual requirement.

**Decision for the near term: Option A.** The rest of this section assumes it.

### Naming and port convention (proposed)

Today's onboarding hit two collision classes that a convention would prevent:
1. A stray bare-metal process from a sibling checkout squatting the default ports.
2. `nemoclaw`'s own default webhook port (18789) being taken by another sandbox, silently reassigned to 18790, requiring manual verification before wiring `OPENCLAW_HOOK_URL`.

Proposed convention, to write down once a second pack is actually onboarded (not enforced by tooling today):

| Pack | Compose project | Service ports (gateway/orch/sim/mcp) | Sandbox name |
|---|---|---|---|
| `datacenter-xe9680` | `nemoclaw-lab-cl` (current default) | 8001–8004 | `infra-sentinel` |
| `laptop-fleet` | `nemoclaw-lab-laptop-fleet` | 8011–8014 | `infra-sentinel-laptop-fleet` |
| next pack | `nemoclaw-lab-{id}` | 80{N}1–80{N}4 | `infra-sentinel-{id}` |

Do not rely on `nemoclaw`'s automatic port-conflict fallback (`! Port 18789 is taken. Using port N instead.`) as your source of truth for `OPENCLAW_HOOK_URL` — it self-reassigns silently, and the printed boilerplate in the script's final instructions still shows the default. Always confirm with `nemoclaw <sandbox> status` before wiring the Gateway, exactly as this session had to.

### Agent onboarding runbook (what today actually required)

`deploy/scripts/onboard-openclaw.sh` is now fixed (as of commit `e6dd08e`) for the bugs this session found: the `tools.profile: "minimal"` policy bug that silently dropped all plugin tools, the missing `plugins.allow` trust entry, and an invalid `gateway restart` subcommand. Onboarding a second/third pack's sandbox should be materially smoother than today's run. Known remaining risk, not yet fixed because it's upstream CLI behavior, not a repo bug:

- **Sandbox creation has a documented dashboard-readiness race** (NVIDIA's own `troubleshooting.mdx`: *"the onboard `Setting up OpenClaw inside sandbox` step times out"*). When it hits, the sandbox is created but `openclaw.json` never gets bootstrapped and the gateway process never starts. Recovery path (used today, works, not yet scripted): check `nemoclaw <sandbox> doctor`; if the gateway process isn't running, bootstrap config manually with `openclaw onboard --non-interactive --accept-risk --mode local --auth-choice skip --skip-bootstrap --skip-skills --skip-channels --skip-search --skip-ui --skip-daemon --gateway-port <actual-port>` inside the sandbox, then `nemoclaw <sandbox> recover`.
- **Config permission drift** (`/sandbox/.openclaw` reverting from the mutable `2770`/`660` contract to `700`/`600`) can recur after restarts. `nemoclaw <sandbox> doctor --fix` is supposed to repair it; when it reports "repair incomplete," the fallback is `chmod`/`chown` via `docker exec -u root` on the sandbox's own container, matching the working sandbox's permissions exactly. Scoped to the one container, never touches other sandboxes.

Multi-sandbox host safety checklist (condensed from what actually governed today's `the-king`-adjacent onboarding — repeat this before onboarding sandbox N+1 on a host that already runs sandboxes 1..N):
- [ ] `nemoclaw list` / `openshell sandbox list` before starting, to capture the baseline
- [ ] Check ambient env vars that a non-interactive onboard will silently pick up (`TELEGRAM_BOT_TOKEN` and any other messaging-channel credential) — strip them (`env -u VAR ...`) unless you intend the new sandbox to share that credential, which will break the existing sandbox's channel
- [ ] Assign a unique `SANDBOX_NAME` and confirm the assigned webhook port after creation, don't assume the default
- [ ] Set `NEMOCLAW_SANDBOX_GPU=0` unless the pack's own agent genuinely needs in-sandbox GPU inference — most packs route to an external vLLM endpoint and gain nothing from GPU passthrough except contention risk with other sandboxes
- [ ] After onboarding, re-check every *other* sandbox's `status` to confirm nothing else moved

### Closing the SOUL.md hardcoding gap

This is the one piece of actual code/content work needed before Option A onboarding can be done without hand-editing `openclaw/SOUL.md` per pack. Proposed approach, mirroring how the React dashboard already adapts from `/api/pack` (`VERTICAL-PACK-GUIDE.md`'s "UI adaptation" table):

1. Convert `openclaw/SOUL.md`'s Identity paragraph and `infra-sentinel-monitor/SKILL.md`'s description into small templates with placeholders for `pack.name`, `asset_noun`, `fleet_label`, and the `monitoring_adapter` type (Redfish-specific phrasing like "poll the Redfish event stream" doesn't hold for `generic`-adapter packs either — same fix, same pass).
2. Add a render step to `onboard-openclaw.sh` (or a small pre-step script) that substitutes these from the target pack's `pack.yaml` before upload, instead of uploading the single committed `SOUL.md` verbatim.
3. Everything else under `openclaw/` needs no change — confirmed pack-agnostic by direct read of `AGENTS.md`, the other three skill files, and the plugin's TypeScript source.

**Superseding note (2026-07-03):** discussion has since moved away from the
templating approach in favour of **hand-authoring** the two pack-specific
persona files per pack (e.g. under `packs/{pack-id}/openclaw/`), mirroring how
`VERTICAL_CONFIG` and KB/scenario content are already hand-authored — and the
day-to-day editing of SOUL.md/SKILL.md is expected to happen through the
**embedded operator terminal** planned in ADR-012 / `SPEC-EMBEDDED-TERMINAL.md`
(M12). Rework this subsection into that shape before implementing Part A.

---

## Part B — Real logs and real KB content

### Two separate axes — don't conflate them

1. **Is the KB/log *content* real** (sourced from an actual vendor KB or incident) **or synthetic** (authored for the demo)? This is a content-authoring question. It requires no code changes — it fits the exact same `pack.yaml`/`scenarios/*.yaml`/`kb/*.md`/`bundles/*.log` contract `VERTICAL-PACK-GUIDE.md` already documents.
2. **Is the *data source* real-time real telemetry** (a hardened adapter talking to live hardware) **or the synthetic simulator** (fault injected on demand via the dashboard button)? This is an infrastructure question — it means hardening `MonitoringAdapter` implementations and standing up real device access.

**Axis 1 is the near-term, high-value, low-risk work. Axis 2 is a separate, larger effort that should be scoped on its own once there's an actual live environment to point at.** The rest of this section treats them separately.

### Axis 1 — real KB articles and log bundles, still simulator-driven

**Sourcing.**
- Real KB articles: actual vendor support KB content (Dell/NVIDIA support articles, internal SME-authored runbooks) for the fault classes each pack's scenarios already model.
- Real log bundles: either (a) sanitized excerpts from actual device logs (iDRAC Lifecycle Log, DCGM diagnostics, syslog) captured during a real incident, or (b) SME-reconstructed realistic logs when a real capture isn't available or can't be shared, clearly distinguished from (a) by provenance metadata (see schema change below) — don't let a reconstructed log silently pass as a captured one.

**Redaction is mandatory, not optional.** Real device logs routinely contain serial numbers, asset tags, hostnames, and internal IPs tied to real infrastructure. Anything sourced from a real capture needs a redaction pass before it's committed to a repo used for demos — treat this as a hard gate in the review step below, not a courtesy.

**Schema change: replace the `PLACEHOLDER`/`SME:` text convention with a real field.** Today, `Scenario.status: "active" | "scaffold"` is the only schema-enforced signal, and it's orthogonal to whether content is real — a `status: active` scenario can (and every one currently does) still hold 100% fabricated KB/log text. Proposed addition to `KBArticle` and the log-bundle frontmatter/scenario metadata:

```yaml
content_status: synthetic   # synthetic | reconstructed | verified
content_source: null        # e.g. "Dell KB SLN12345" or "SME reconstruction, 2026-Q3" when not synthetic
```

This makes provenance queryable (CI check, a dashboard badge, a docs generator) instead of grep-dependent, and gives the Operator Dashboard a place to honestly disclose "this is demo content" vs "this is sourced from a real KB" if that's ever shown to an external audience.

**Review gate.** Real content carries real stakes that placeholder content doesn't — a `remediation_steps` list an SME didn't validate against the actual vendor guidance could plausibly get shown to an operator as a legitimate recommendation. Real (non-synthetic) KB/scenario content should go through an explicit SME sign-off before merge, separate from ordinary code review.

**Re-tune the KB index once real content lands.** `services/mcp_tools/kb_index.py` embeds `title + body_md[:500]` per article into a FAISS flat index and accepts a semantic match only above a hand-set threshold (`_DEFAULT_THRESHOLD = 0.60`), tuned against today's short, uniformly-structured synthetic articles. Real vendor KB articles will likely be longer and more heterogeneously structured. Don't assume the current threshold or the flat `title + first-500-chars` embedding strategy generalizes — re-validate (and likely add chunking for long articles) once the first batch of real content is in.

**Proposed pilot:** pick one scenario in the flagship `datacenter-xe9680` pack (already the most mature pack, already Redfish-flavored, most likely to have real vendor documentation available) and replace its KB article + log bundle with real sourced content end to end, including the schema field and redaction pass, before doing this across every scenario/pack. Use it to validate the review process and the FAISS re-tuning before scaling out.

### Axis 2 — real telemetry (separate, later effort)

Both `RedfishAdapter` and `GenericAdapter` (`services/mcp_tools/adapters/`) construct against `sim_url` only — there is no config path today for pointing either adapter at a real device endpoint instead. Moving to live telemetry requires, at minimum:
- a config surface for a per-adapter base URL independent of `sim_url` (doesn't exist today)
- credential/auth handling for real device access (no such plumbing exists anywhere in the stack today — this is new, not a small addition)
- hardening `RedfishAdapter`'s assumptions: it currently expects the simulator's specific JSON shapes for `Systems`/`LogServices`/`EventService`; real Redfish implementations vary meaningfully by vendor and firmware version, which is exactly the risk ADR-008 flagged when it scoped "hardening" as future work rather than folding it into the adapter's initial design
- a genuinely reachable, real, monitored environment to point at — this can't be built or validated against nothing

`MonitoringAdapterType` already has an unused `opcua` stub in the enum, anticipating a third protocol family (per ADR-008's "SNMP, OPC-UA" example) — a real adapter for either would follow the same `MonitoringAdapter` protocol, no agent or orchestrator changes required.

**Recommendation: don't start this until Axis 1 has shipped for at least one pack and there's a concrete real environment identified to integrate against.** Scope it as its own plan when that's true.

---

## Known doc drift to reconcile

Found while researching this plan — not blocking, but should be cleaned up so the docs don't actively mislead the next person adding a pack:

- **README.md's vertical table** (lines ~88–98) lists `oil-gas-rigs`, `healthcare-devices`, `finance-atm-fleet` as stubs — none of these directories exist under `packs/`. The three real scaffold packs that *do* exist (`hpc-cluster`, `network-fabric`, `storage-nvme`) aren't in the table at all.
- **`docs/index.html`'s "Coming Soon" cards** mirror the same stale names and don't link to the real scaffold packs (`data-coming="true"`, no `data-href`).
- **`docs/lab-guide.html`'s `VERTICAL_CONFIG`** has entries for only 2 of the 6 packs (`ai-infrastructure` → `datacenter-xe9680`, and `laptop-fleet`). Visiting `?vertical=edge-inference` or any of the other three scaffold packs today silently falls back to the GPU-cluster copy instead of failing or showing scaffold-appropriate content.

None of this blocks Part A or Part B above, but whoever picks up the SOUL.md templating work (Part A) is a natural owner for reconciling these listings at the same time, since it's the same "hardcoded to one vertical" class of gap.

---

## Suggested sequencing

1. Close the SOUL.md/skill hardcoding gap (Part A) — small, unblocks clean multi-pack agent onboarding without hand-editing persona files.
2. Onboard `laptop-fleet` as a second live stack + sandbox using the naming/port convention above and the now-fixed `onboard-openclaw.sh` — validates Option A end-to-end with a pack that already has real (if synthetic) scenario/KB/bundle content.
3. Run the Axis 1 pilot (real KB + log content for one `datacenter-xe9680` scenario) — validates the schema field, redaction step, and SME review gate before scaling to more scenarios/packs.
4. Reconcile the stale README/index.html/lab-guide listings.
5. Scope Axis 2 (real telemetry adapter hardening) separately, once a real environment to integrate against exists.
