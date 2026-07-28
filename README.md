# NemoClaw Lab

Autonomous AIOps demo: a real NemoClaw/OpenClaw agent (OpenShell-sandboxed, ADR-011) monitors simulated hardware infrastructure, detects faults, analyses logs with a local LLM, matches Dell KB articles, and proposes remediation — blocked by a server-side human-in-the-loop approval gate until an operator decides.

One codebase. Swap the **Domain Pack** to switch verticals — GPU cluster, laptop fleet, edge nodes, oil-field rigs — with no code changes.

## Quick start (GB10 lab host)

These steps are the bring-up for the reference lab host — an **NVIDIA GB10
(`promaxgb10`, aarch64 Linux, ufw active)** serving its own vLLM endpoint. Other
Linux hosts work the same way; the GB10-specific parts are the ufw rules and the
docker-bridge bind address, both called out below. macOS on Intel is **not**
supported (OpenShell ships no x86_64 macOS assets — ADR-011).

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

### First-time setup

Three things, in order. Only the first needs a decision from you.

```bash
# 1. Point the lab at your LLM (see "Configuring the LLM endpoint" below)
cp .env.example .env
$EDITOR .env                  # set LLM_BASE_URL and LLM_MODEL

# 2. Build the stack, onboard the agent, start the host daemons, verify
make bootstrap

# 3. Run the two `sudo ufw allow` commands bootstrap prints, then re-check
make doctor                   # expect: all checks passed
```

`make bootstrap` is idempotent — safe to re-run. It will **not** re-onboard an
existing sandbox (that rebuilds the image and resets the agent's config); use
`make bootstrap FORCE=1` if you actually want that. It never runs `sudo`
itself: the ufw rules are printed for you to run.

Then open <http://localhost:8001/lab/>.

<details>
<summary><strong>What <code>make bootstrap</code> does — and the manual equivalent</strong></summary>

Useful when a step fails partway and you need to resume by hand.

1. **Preflight** — docker, the `nemoclaw` CLI, `.env`, and that `LLM_BASE_URL`
   actually answers. It fails here rather than 10 minutes into an image build,
   because the endpoint is baked into the sandbox at onboard time.
2. **`TERMINAL_BIND`** — detected from `docker0` (`172.17.0.1` here) and written
   to `.env`. On Linux `host.docker.internal` resolves to the docker bridge, so
   a loopback bind is unreachable from the gateway container
   (`docs/SPEC-EMBEDDED-TERMINAL.md` §4).
3. **`docker compose up -d --build`** — the four services.
4. **`deploy/scripts/onboard-openclaw.sh`** — builds the sandbox image with the
   MCP plugin baked in, onboards it against your LLM, seeds the workspace, locks
   down the built-in tools, then reads the webhook port back out of the
   sandbox's `openclaw.json` and writes `OPENCLAW_HOOK_URL`/`OPENCLAW_HOOK_TOKEN`
   into `.env`. (18789 is only the default — openclaw silently self-reassigns if
   it is taken, and this host landed on **18790**. `nemoclaw <sandbox> status`
   does not report the chosen port, which is why the script reads the config.)
5. **`docker compose up -d gateway`** — pick up the new `.env`.
6. **ufw rules** — printed, not run. ufw's default-deny INPUT drops *all*
   container→host traffic, which breaks the terminal hop and the wake hook
   **silently**: the lab looks fine while faults are never detected. The printed
   subnet is detected live, because the compose network is not pinned in
   `docker-compose.yaml`.
7. **`make demo-up`** — starts the terminal daemon and hook-relay (skipping any
   already alive), then runs the `make doctor` preflight.

</details>

### Surviving a reboot

The compose services come back on their own (`restart: unless-stopped`). The two
host daemons do **not** — install the user units once:

```bash
mkdir -p ~/.config/systemd/user
cp deploy/systemd/nemoclaw-*.{service,timer} ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now nemoclaw-terminal nemoclaw-hook-relay
sudo loginctl enable-linger $USER     # keep them alive without a login session
```

Optionally also `systemctl --user enable --now nemoclaw-doctor.timer`, which
runs `make doctor-fix` on a schedule so a dead daemon self-heals.

One piece can't be unit-managed: openshell's sandbox port-forward. After a
reboot, recover it **before** starting the relay —

```bash
nemoclaw <sandbox> recover     # recreates the loopback forward
make hook-relay                # then bridge it onto the docker bridge
make doctor
```

That order matters: `recover`'s port check is not interface-aware, so if the
relay already holds `172.17.0.1:18790` it wrongly concludes the port is taken
and skips recreating its own `127.0.0.1:18790` forward — while still printing
success. `make doctor` catches the result as two failed checks.

## Configuring the LLM endpoint

**Set it in `.env`:**

```bash
LLM_BASE_URL=http://192.168.68.131:8000/v1   # OpenAI-compatible, e.g. vLLM
LLM_MODEL=qwen3.6-35b-a3b-fp8                # must match the id from /v1/models
LLM_API_KEY=vllm
```

**Only one thing reads these: `deploy/scripts/onboard-openclaw.sh`, and only at
onboard time.** It passes them to `nemoclaw onboard`, which bakes the endpoint
into the agent sandbox. The Compose services never contact the LLM at all
(ADR-011) — the agent does, from inside its sandbox.

**So editing `.env` afterwards does not move a running agent.** To change the
endpoint or model, re-onboard:

```bash
make bootstrap FORCE=1        # or: deploy/scripts/onboard-openclaw.sh
```

Two things that look like bugs but aren't:

- `make doctor`'s "LLM endpoint" check tests the URL **in `.env`**, not the one
  the agent is using. Green there means your endpoint is reachable — it does not
  prove the agent is pointed at it. After changing `.env` without re-onboarding,
  the two disagree.
- Inside the sandbox the endpoint reads as `https://inference.local/v1`, not the
  URL you set. That is OpenShell routing it through its own TLS proxy. To see
  what the agent is really configured with, read the sandbox's own config:

  ```bash
  nemoclaw <sandbox> download /sandbox/.openclaw/openclaw.json /tmp/oc.json
  python3 -m json.tool /tmp/oc.json | grep -A3 '"inference"'
  ```

The welcome page lists all available verticals. Click one to open the split-screen lab guide + live dashboard — switching verticals restarts the stack on the right pack automatically.

## Architecture

For the Kubernetes deployment view — pods, services, ports, NetworkPolicies,
and the out-of-cluster host processes on Charmed K8s — see
[`docs/ARCHITECTURE-K8S.md`](docs/ARCHITECTURE-K8S.md) (also available as a
standalone page: [`docs/ARCHITECTURE-K8S.html`](docs/ARCHITECTURE-K8S.html)).

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
| `LLM_BASE_URL` / `LLM_MODEL` / `LLM_API_KEY` | — | Read by `onboard-openclaw.sh` only, and only **at onboard time** — the value is baked into the sandbox, so editing these later does not move a running agent. Re-onboard to change. See [Configuring the LLM endpoint](#configuring-the-llm-endpoint) |
| `TERMINAL_BIND` | `127.0.0.1` | Address the terminal daemon binds. On Linux must be the docker bridge (`172.17.0.1`) — loopback is unreachable from the gateway container. Set by `make bootstrap` |

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
