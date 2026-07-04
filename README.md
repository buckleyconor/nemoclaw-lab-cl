# NemoClaw Sentinel Lab

Autonomous AIOps demo: a real NemoClaw/OpenClaw agent (OpenShell-sandboxed, ADR-011) monitors simulated hardware infrastructure, detects faults, analyses logs with a local LLM, matches Dell KB articles, and proposes remediation — blocked by a server-side human-in-the-loop approval gate until an operator decides.

One codebase. Swap the **Domain Pack** to switch verticals — GPU cluster, laptop fleet, edge nodes, oil-field rigs — with no code changes.

## Quick start (Docker Compose)

```bash
cp .env.example .env          # set VLLM_BASE_URL and VLLM_API_KEY
docker compose up -d
open http://localhost:8001/lab/
```

The welcome page lists all available verticals. Click one to open the split-screen lab guide + live dashboard.

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
tests/          Unit + integration + e2e test suite
```

## Active verticals

| Vertical | Pack ID | Status |
|----------|---------|--------|
| AI Infrastructure (XE9780L GPU cluster) | `datacenter-xe9680` | ✅ Active |
| Laptop Fleet (Dell Precision) | `laptop-fleet` | ✅ Active |
| Oil & Gas | `oil-gas-rigs` | stub |
| Healthcare | `healthcare-devices` | stub |
| Financial Services | `finance-atm-fleet` | stub |
| Edge Computing | `edge-inference` | partial |

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
| `OPENCLAW_HOOK_URL` | unset | OpenClaw gateway URL for webhook wake-up (e.g. `http://host.docker.internal:18789`) |
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
| M9 Prod hardening (Kubernetes, 30 users) | ⬜ Planned |
| M10 LLM-driven skill-calling agent (ADR-010) | ✅ Complete — validated live against vLLM/Qwen tool-calling |
| M11 Real NemoClaw/OpenClaw agent runtime (ADR-011) | 🚧 Implemented — spike partially validated (MCP interop, plugin manifest, docs-level unknowns); live sandbox run blocked on Intel-Mac host (OpenShell has no macOS x86_64 assets), needs a supported host |
| M12 Embedded operator terminal (ADR-012) | 🚧 Implemented — daemon + proxy + panel verified end-to-end host-side; the in-container gateway→daemon hop needs a ufw allow rule on this host (SPEC §6) |
