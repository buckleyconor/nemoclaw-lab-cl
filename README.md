# NemoClaw Sentinel Lab

Autonomous AIOps demo: a NemoClaw v0.0.70 agent monitors simulated hardware infrastructure, detects faults, analyses logs with a chain-of-thought LLM, matches Dell KB articles, and proposes remediation — blocked by a server-side human-in-the-loop approval gate until an operator decides.

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
                                  │ HTTP + SSE
               ┌──────────────────▼──────────────────────────┐
               │              Gateway  :8001                  │
               │  REST API · SSE multiplex · React SPA host  │
               │  In-memory state · HITL token store          │
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
               ┌──────────┴──────────┐
               │  NemoClaw Agent     │
               │  v0.0.70            │
               │  soul.md + 4 skills │
               │  Python loop        │
               └─────────────────────┘
                          │
               ┌──────────▼──────────┐
               │  Local LLM          │
               │  vLLM endpoint      │
               │  chain-of-thought   │
               └─────────────────────┘
```

## Project layout

```
agent/          NemoClaw agent: soul.md, skills/, loop.py, llm.py
libs/common/    Shared Pydantic models, pack loader
services/
  gateway/      FastAPI: REST API, SSE, HITL token store, SPA host
  orchestrator/ Scenario rotation and simulator coordination
  mcp_tools/    MCP Streamable HTTP: monitor, logs, kb, remediation tools
  simulator/    Redfish/generic hardware event emulator
packs/          Domain Pack content per vertical (YAML + Markdown + logs)
ui/             React + TypeScript dashboard (Vite, SSE-driven)
docs/           Lab guide (split-screen HTML), welcome page, ADRs
  adr/          Architecture Decision Records (9 decisions)
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

The agent is a NemoClaw v0.0.70 instance with a `soul.md` identity document and four skills:

| Skill | Phase | MCP tools |
|-------|-------|-----------|
| `infra-sentinel-monitor` | Detect | `monitor_list_events` |
| `infra-sentinel-diagnose` | Diagnose | `logs_get_bundle`, LLM, `kb_search` |
| `infra-sentinel-notify` | Present | Gateway activity API |
| `infra-sentinel-remediate` | Remediate | `remediation_execute` + HITL token |

The Python `agent/loop.py` implements the runtime — the skills are Markdown instruction documents the agent's character and reasoning style; the loop is the execution engine.

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
| `VLLM_BASE_URL` | `http://192.168.68.131:8000/v1` | LLM inference endpoint (OpenAI-compatible) |
| `VLLM_API_KEY` | `token-abc123` | API key for vLLM endpoint |
| `PACK_ID` | `datacenter-xe9680` | Active domain pack |
| `POLL_INTERVAL` | `5.0` | Agent poll interval in seconds |

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
