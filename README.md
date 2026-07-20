# NemoClaw Lab

Autonomous AIOps demo: a real NemoClaw/OpenClaw agent (OpenShell-sandboxed, ADR-011) monitors simulated hardware infrastructure, detects faults, analyses logs with a local LLM, matches Dell KB articles, and proposes remediation — blocked by a server-side human-in-the-loop approval gate until an operator decides.

One codebase. Swap the **Domain Pack** to switch verticals — GPU cluster, laptop fleet, edge nodes, oil-field rigs — with no code changes.

## Quick start

Day-to-day (everything already onboarded once):

```bash
make demo-up                  # compose stack + terminal daemon + hook-relay, then preflight
open http://localhost:8001/lab/
```

`make doctor` re-runs just the preflight — a red/green check of every moving
part (4 services, terminal daemon, hook-relay, sandbox wake-hook, LLM
endpoint) with the exact fix printed for anything that's down. If faults are
ever "not being detected", run it first; the cause is almost always a dead
host process, not the agent. See [`docs/TROUBLESHOOTING.md`](docs/TROUBLESHOOTING.md).

First-time setup (once per host):

```bash
cp .env.example .env                      # set LLM_BASE_URL / LLM_MODEL / LLM_API_KEY
docker compose up -d --build
make terminal                             # prints TERMINAL_WS_URL/TERMINAL_TOKEN → add to .env
deploy/scripts/onboard-openclaw.sh        # onboards the agent sandbox; prints OPENCLAW_HOOK_* → add to .env
                                          # (confirm the webhook port with `nemoclaw <sandbox> status` —
                                          #  default 18789, self-reassigns if taken)
docker compose up -d gateway              # pick up the new .env values
make demo-up                              # starts host daemons + verifies everything
```

Optional: install the systemd user units in `deploy/systemd/` so the terminal
daemon and hook-relay survive reboots (the compose services already restart
via `restart: unless-stopped`; openshell's sandbox port-forward is the one
piece that can't be unit-managed — `make doctor` detects it and prints the
`nemoclaw <sandbox> recover` fix).

The welcome page lists all available verticals. Click one to open the split-screen lab guide + live dashboard — switching verticals restarts the stack on the right pack automatically.

## Architecture

```
Pack (content)  →  Framework (agent + services)  →  Lab UI
```

```
               ┌─────────────────────────────────────────────┐
               │                Lab Browser UI                │
               │  Welcome Page · Lab Guide · Dashboard (SPA) │
               └──────────────────┬──────────────────────────┘
                                  │ HTTP + SSE + WS (terminal)
               ┌──────────────────▼──────────────────────────┐
               │              Gateway  :8001                  │
               │  REST API · SSE multiplex · React SPA host  │
               │  In-memory state · HITL token store          │
               │  Terminal WS proxy (ADR-012)                 │
               └───┬──────────────────────┬──────────────────┘
                   │                      │
      ┌────────────▼──────┐    ┌──────────▼──────────┐
      │  Orchestrator     │    │  MCP Tools  :8004    │
      │  :8002            │    │  monitor · logs      │
      │  Scenario         │    │  kb · remediation    │
      │  rotation         │    │  fastembed + FAISS   │
      └────────┬──────────┘    └──────────┬───────────┘
               │                          │
      ┌────────▼──────────────────────────▼───────────┐
      │              Simulator  :8003                  │
      │  Fake Redfish / generic event surface          │
      │  Emits fault events + iDRAC log bundles        │
      └───────────────────────────────────────────────┘
                          ▲
               ┌──────────┴──────────────────┐
               │  NemoClaw / OpenClaw agent  │
               │  (host process, ADR-011)    │
               │  OpenShell sandbox          │
               │  SOUL.md + AGENTS.md +      │
               │  4 skills + infra plugin    │
               └──────────┬──────────────────┘
                          │
               ┌──────────▼──────────┐
               │  Local LLM          │
               │  vLLM endpoint      │
               │  (OpenClaw provider)│
               └─────────────────────┘
```

The agent is **not** a Compose service: `deploy/scripts/onboard-openclaw.sh`
onboards the real NVIDIA NemoClaw stack — OpenClaw in an OpenShell sandbox —
as a peer host process that reaches MCP Tools and the Gateway via their
published ports. See `docs/adr/ADR-011.md` and `openclaw/README.md`.

A second host process (M12) is the **terminal daemon** (:8005, host-local
bind only, started with `make terminal`), backing the embedded operator
terminal in the dashboard for configuring the agent (SOUL.md/SKILL.md,
`nemoclaw`/`openclaw`/`openshell` CLIs). Like the agent, it is not a Compose
service — the CLIs and sandbox state live on the host. See
`docs/adr/ADR-012.md` and `docs/SPEC-EMBEDDED-TERMINAL.md`.

## Project layout

```
openclaw/       OpenClaw agent runtime: SOUL.md, AGENTS.md, skills/ (4),
                plugins/nemoclaw-infra-tools (tool plugin, MCP client)
libs/common/    Shared Pydantic models, pack loader
services/
  gateway/      FastAPI: REST API, SSE, HITL token store, SPA host
  orchestrator/ Scenario rotation and simulator coordination
  mcp_tools/    MCP Streamable HTTP: monitor, logs, kb, remediation tools
  simulator/    Redfish/generic hardware event emulator
packs/          Domain Pack content per vertical (YAML + Markdown + logs)
ui/             React + TypeScript dashboard (Vite, SSE-driven)
docs/           Lab guide (split-screen HTML), welcome page, ADRs
  adr/          Architecture Decision Records (11 decisions)
docker/         Dockerfiles (backend + gateway with UI build)
deploy/helm/    Helm chart for Kubernetes deployment
deploy/scripts/ Host-side ops: demo-up, doctor, switch-pack, terminal, hook-relay, onboarding
deploy/systemd/ User units so the host daemons survive reboots
tests/          Unit + integration + e2e test suite
```

## Active verticals

| Vertical | Pack ID | Status |
|----------|---------|--------|
| AI Infrastructure (XE9780L GPU cluster) | `datacenter-xe9680` | ✅ Active |
| Laptop Fleet (Dell Precision) | `laptop-fleet` | ✅ Active |
| Oil & Gas (drilling rig equipment) | `oil-rigs` | ✅ Active |
| Healthcare (hospital biomedical devices) | `healthcare-devices` | ✅ Active |
| Financial Services (bank ATM fleet) | `finance-atm-fleet` | ✅ Active |
| Telco & 5G (RAN macro sites) | `telco-edge-5g-masts` | ✅ Active |
| HPC Cluster | `hpc-cluster` | scaffold |
| Network Fabric | `network-fabric` | scaffold |
| Storage (NVMe) | `storage-nvme` | scaffold |
| Edge Inference | `edge-inference` | scaffold |

See [`docs/VERTICAL-PACK-GUIDE.md`](docs/VERTICAL-PACK-GUIDE.md) to create or extend a vertical.

## Agent

The agent is real OpenClaw running in an OpenShell sandbox managed by NemoClaw (ADR-011). Its identity lives in `openclaw/SOUL.md`, its standing order in `openclaw/AGENTS.md` (auto-injected every session), and four skills teach the fault workflow. The agent dynamically decides which skill/tool to invoke and in what order.

| Skill | Phase | Tools |
|-------|-------|-------|
| `infra-sentinel-monitor` | Detect | `monitor_list_events`, `monitor_get_asset`, `monitor_list_assets` |
| `infra-sentinel-diagnose` | Diagnose | `logs_get_bundle`, `kb_search` (signature identified via the agent's own reasoning, not a separate LLM call) |
| `infra-sentinel-notify` | Narrate | `notify_post_activity` |
| `infra-sentinel-remediate` | Propose | `remediation_propose` (LLM-callable) → human approval → `remediation.execute` (Gateway-only, never exposed to the LLM) |

The `nemoclaw-infra-tools` OpenClaw plugin registers exactly those seven tools, bridging to the MCP Tools service as an MCP client. It also carries the deterministic harness side-work the old loop performed (fault registration on first log fetch, diagnosis persistence, narration pinning). OpenClaw's built-in tools (`exec`, `browser`, `web_search`, …) are denied via tool policy at onboard time. On approval, the Gateway's `post_decision()` mints the token and calls `remediation.execute` server-to-server immediately — no polling (see [HITL approval gate](#hitl-approval-gate) below and ADR-004, ADR-010, ADR-011).

## HITL approval gate

The approval token is minted **server-side** when an operator clicks Approve in the Operator Dashboard. It is:
- Single-use and bound to one specific `fault_event_id`
- Never placed in the LLM prompt or agent context
- Validated by the `remediation.execute` MCP tool before any step runs
- Invalidated after first use — replay attacks return `token_already_consumed`

## Run tests

```bash
uv run pytest                 # unit + integration (stub LLM)
uv run pytest tests/e2e/      # end-to-end (requires running stack)
```

## Environment variables

| Variable | Default | Description |
|----------|---------|-------------|
| `PACK_ID` | `datacenter-xe9680` | Active domain pack |
| `OPENCLAW_HOOK_URL` | unset | OpenClaw webhook wake-up URL, `http://host.docker.internal:<port>` — port 18789 by default but openclaw self-reassigns if taken (confirm with `nemoclaw <sandbox> status`); on Linux, `make hook-relay` must bridge it to the container |
| `OPENCLAW_HOOK_TOKEN` | unset | Webhook shared secret (printed by `onboard-openclaw.sh`) |
| `LLM_BASE_URL` / `LLM_MODEL` / `LLM_API_KEY` | — | Consumed by `onboard-openclaw.sh` only; the Compose stack no longer talks to the LLM |

## Milestones

| Milestone | Status |
|-----------|--------|
| M0 Repo & scaffolding | ✅ Complete |
| M1 Pack contract + data model + flagship pack | ✅ Complete |
| M2 Simulator engine + Redfish surface | ✅ Complete |
| M3 Scenario Orchestrator | ✅ Complete |
| M4 MCP tool servers + semantic KB | ✅ Complete |
| M5 Gateway + React dashboard + approval gate | ✅ Complete |
| M6 NemoClaw agent integration (v0.0.70) | ✅ Complete |
| M7 Lab guide + welcome page (multi-vertical) | ✅ Complete |
| M8 Extensibility: second pack (laptop-fleet) | ✅ Complete |
| M9 Prod hardening (Kubernetes, 30 users) | ⬜ Planned — terminal sub-scope resolved by ADR-013 (M13); in-cluster agent/sandbox story still deferred (ADR-011(g)) |
| M10 LLM-driven skill-calling agent (ADR-010) | ✅ Complete — validated live against vLLM/Qwen tool-calling |
| M11 Real NemoClaw/OpenClaw agent runtime (ADR-011) | 🚧 Implemented — spike partially validated (MCP interop, plugin manifest, docs-level unknowns); live sandbox run blocked on Intel-Mac host (OpenShell has no macOS x86_64 assets), needs a supported host |
| M12 Embedded operator terminal (ADR-012) | 🚧 Implemented — daemon + proxy + panel verified end-to-end host-side; the in-container gateway→daemon hop needs a ufw allow rule on this host (SPEC §6) |
| M13 Restricted per-tenant terminal for M9 (ADR-013) | 🚧 Implemented — `TERMINAL_MODE=restricted` console + per-tenant daemon script + Helm secret/values; live end-to-end verification against a real sandbox still outstanding |
