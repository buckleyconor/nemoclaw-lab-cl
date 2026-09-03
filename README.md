# NemoClaw Lab

Autonomous AIOps demo: a real NemoClaw/OpenClaw agent (OpenShell-sandboxed) monitors simulated hardware infrastructure, detects faults, analyses logs with an LLM, matches Dell KB articles, and proposes remediation — blocked by a server-side human-in-the-loop approval gate until an operator decides.

One codebase. Swap the **Domain Pack** to switch verticals — GPU cluster, laptop fleet, edge nodes, oil-field rigs — with no code changes.

## Quick start (single Linux host)

These steps bring up everything on one Linux host with docker + git. Reference
targets: the production **Ubuntu 24.04 x86_64 VM** using the lab's **shared
inference endpoint** (private/internal, real API key), and the **NVIDIA GB10**
dev box (aarch64, ufw active) serving its own vLLM. The
host-specific parts are the ufw rules and the docker-bridge bind address, both
called out below.

### First-time setup

Four things, in order. Only the first needs a decision from you.

```bash
# 1. Point the lab at the LLM endpoint
cp .env.example .env
$EDITOR .env                  # set LLM_BASE_URL, LLM_MODEL and LLM_API_KEY (required)

# 2. Bring up the inference proxy — the sandbox's route to the LLM (ADR-014)
sudo apt-get install -y nginx           # if not already present
deploy/scripts/run-inference-proxy.sh

# 3. Build the stack, onboard the agent, start the host daemons, verify
make bootstrap

# 4. Run the `sudo ufw allow` commands bootstrap prints (if ufw is active),
#    then re-check
make doctor                   # expect: all checks passed
```

Then open <http://localhost:8001/lab/>.

> For the long-form version — full prerequisites, what runs in a container vs
> on the host, how to plug in an existing endpoint or serve a model on the same
> node, and verification — see
> **[docs/single-node-deployment.md](docs/single-node-deployment.md)**.

The agent cannot talk to a private/internal endpoint directly — NemoClaw's
SSRF guard refuses it, and its network policy can only pin
`host.openshell.internal`. The inference proxy bridges that alias to the real
endpoint, and it keeps the sandbox-facing URL stable so the endpoint can move
later without re-onboarding.

`make bootstrap` is idempotent and safe to re-run. It never runs `sudo` itself:
the ufw rules are printed for you to run. It will **not** re-onboard an existing
sandbox, since that rebuilds the image and resets the agent's config — use
`make bootstrap FORCE=1` when you do want that.

### Changing the LLM endpoint or model

`LLM_BASE_URL`/`LLM_MODEL`/`LLM_API_KEY` are baked into the agent's sandbox at
onboard time — editing `.env` alone does not move a running agent. To repoint
(the shared endpoint's models change over time):

```bash
$EDITOR .env                  # update LLM_BASE_URL / LLM_MODEL / LLM_API_KEY
make repoint-llm              # re-renders the proxy + syncs the sandbox, no rebuild
```

`make doctor` flags a `.env`-vs-sandbox model mismatch if a repoint was
forgotten. If a repoint ever fails, the full reset is `make bootstrap FORCE=1`
(re-onboards the sandbox).

<details>
<summary><strong>What <code>make bootstrap</code> does — and the manual equivalent</strong></summary>

Useful when a step fails partway and you need to resume by hand.

1. **Preflight** — docker (+compose plugin), the `nemoclaw` CLI, `.env` with a
   real `LLM_API_KEY`, that `LLM_BASE_URL` answers the key, and that the
   inference proxy is live. It fails here rather than 10 minutes into an image
   build, because the endpoint is baked into the sandbox at onboard time.
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
   container→host traffic, which breaks the terminal hop, the wake hook and the
   agent's LLM route **silently**: the lab looks fine while faults are never
   detected. The compose subnet is pinned in `docker-compose.yaml`
   (172.28.100.0/24), but the printed rules use the live-detected value in case
   you changed the pin.
7. **`make demo-up`** — starts the terminal daemon and hook-relay (skipping any
   already alive), then runs the `make doctor` preflight.

</details>

### Surviving a reboot

The compose services come back on their own (`restart: unless-stopped`). The
host processes do **not** — install the self-heal layer once:

```bash
sudo make install-selfheal
```

That one command installs and enables:

| Unit | Domain | What it does |
|------|--------|--------------|
| `nemoclaw-inference-watchdog.timer` | system | Every 60s: restarts nginx if the inference proxy is down or on stale sockets |
| `nemoclaw-terminal.service` | system | The embedded-terminal daemon (ADR-012), `Restart=on-failure` |
| `nemoclaw-gateway-<port>.service` | user | Boot-takeover of the OpenShell gateway |
| `nemoclaw-hook-forward.service` | user | Keeps openshell's sandbox port-forward alive |
| `nemoclaw-hook-relay.service` | user | Bridges that forward onto the docker bridge |
| `nemoclaw-doctor.timer` | user | Every 5 min: `make doctor-fix`, so a dead daemon self-heals |

It also runs `loginctl enable-linger` for the lab user (the user units must
survive logout) and migrates a host off the older all-system layout in place.
It is idempotent — re-run it after moving the checkout.

The wake-hook chain lives in the **user** manager on purpose: the `nemoclaw`
CLI needs the user session's D-Bus, and the gateway → forward → relay boot
ordering is only expressible within one manager domain (2026-08-28 incident).
Don't hand-copy units out of `deploy/systemd/` — they carry `__NEMOCLAW_*__`
placeholders that the installer substitutes.

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

### Day-to-day

Once onboarded, this is all you need:

```bash
make demo-up                  # compose stack + terminal daemon + hook-relay, then preflight
open http://localhost:8001/lab/
```

`make doctor` re-runs just the preflight — a red/green check of every moving
part (4 services, terminal daemon, hook-relay, sandbox wake-hook, LLM
endpoint, inference proxy, agent model drift) with the exact fix printed for
anything that's down. If faults are
ever "not being detected", run it first; the cause is almost always a dead
host process, not the agent. See [`docs/TROUBLESHOOTING.md`](docs/TROUBLESHOOTING.md).

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
               │  Terminal WS proxy                           │
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
               │  (host process)             │
               │  OpenShell sandbox          │
               │  SOUL.md + AGENTS.md +      │
               │  4 skills + infra plugin    │
               └──────────┬──────────────────┘
                          │
               ┌──────────▼──────────┐
               │  LLM endpoint       │
               │  shared lab / vLLM  │
               │  via host inference │
               │  proxy (ADR-014)    │
               └─────────────────────┘
```

The agent is **not** a Compose service: `deploy/scripts/onboard-openclaw.sh`
onboards the real NVIDIA NemoClaw stack — OpenClaw in an OpenShell sandbox —
as a peer host process that reaches MCP Tools and the Gateway via their
published ports. See `openclaw/README.md`.

A second host process (M12) is the **terminal daemon** (:8005, host-local
bind only, started with `make terminal`), backing the embedded operator
terminal in the dashboard for configuring the agent (SOUL.md/SKILL.md,
`nemoclaw`/`openclaw`/`openshell` CLIs). Like the agent, it is not a Compose
service — the CLIs and sandbox state live on the host. See
`docs/SPEC-EMBEDDED-TERMINAL.md`.

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
docs/           Lab guide (split-screen HTML), welcome page, specs
  adr/          Architecture Decision Records (14 decisions)
docker/         Dockerfiles (backend + gateway with UI build)
deploy/helm/    Helm chart for Kubernetes deployment
deploy/scripts/ Host-side ops: demo-up, doctor, switch-pack, terminal, hook-relay, onboarding
deploy/systemd/ User units so the host daemons survive reboots
tests/          Unit + integration + e2e test suite
```

## Active verticals

| Vertical | Pack ID |
|----------|---------|
| AI Infrastructure (XE9780L GPU cluster) | `datacenter-xe9680` |
| Laptop Fleet (Dell Precision) | `laptop-fleet` |
| Oil & Gas (drilling rig equipment) | `oil-rigs` |
| Healthcare (hospital biomedical devices) | `healthcare-devices` |
| Financial Services (bank ATM fleet) | `finance-atm-fleet` |
| Telco & 5G (RAN macro sites) | `telco-edge-5g-masts` |

See [`docs/VERTICAL-PACK-GUIDE.md`](docs/VERTICAL-PACK-GUIDE.md) to create or extend a vertical.

## Agent

The agent is real OpenClaw running in an OpenShell sandbox managed by NemoClaw. Its identity lives in `openclaw/SOUL.md`, its standing order in `openclaw/AGENTS.md` (auto-injected every session), and four skills teach the fault workflow. The agent dynamically decides which skill/tool to invoke and in what order.

| Skill | Phase | Tools |
|-------|-------|-------|
| `infra-sentinel-monitor` | Detect | `monitor_list_events`, `monitor_get_asset`, `monitor_list_assets` |
| `infra-sentinel-diagnose` | Diagnose | `logs_get_bundle`, `kb_search` (signature identified via the agent's own reasoning, not a separate LLM call) |
| `infra-sentinel-notify` | Narrate | `notify_post_activity` |
| `infra-sentinel-remediate` | Propose | `remediation_propose` (LLM-callable) → human approval → `remediation.execute` (Gateway-only, never exposed to the LLM) |

The `nemoclaw-infra-tools` OpenClaw plugin registers exactly those seven tools, bridging to the MCP Tools service as an MCP client. It also carries the deterministic harness side-work the old loop performed (fault registration on first log fetch, diagnosis persistence, narration pinning). OpenClaw's built-in tools (`exec`, `browser`, `web_search`, …) are denied via tool policy at onboard time. On approval, the Gateway's `post_decision()` mints the token and calls `remediation.execute` server-to-server immediately — no polling (see [HITL approval gate](#hitl-approval-gate) below).

## HITL approval gate

The approval token is minted **server-side** when an operator clicks Approve in the Operator Dashboard. It is:
- Single-use and bound to one specific `fault_event_id`
- Never placed in the LLM prompt or agent context
- Validated by the `remediation.execute` MCP tool before any step runs
- Invalidated after first use — replay attacks return `token_already_consumed`

## Run tests

```bash
uv run pytest                 # unit + integration (stub LLM)
uv run pytest tests/e2e/      # full fault lifecycle, all in-process (no stack, no LLM)
```

## Environment variables

| Variable | Default | Description |
|----------|---------|-------------|
| `PACK_ID` | `datacenter-xe9680` | Active domain pack |
| `OPENCLAW_HOOK_URL` | unset | OpenClaw webhook wake-up URL, `http://host.docker.internal:<port>` — port 18789 by default but openclaw self-reassigns if taken (confirm with `nemoclaw <sandbox> status`); on Linux, `make hook-relay` must bridge it to the container |
| `OPENCLAW_HOOK_TOKEN` | unset | Webhook shared secret (printed by `onboard-openclaw.sh`) |
| `LLM_BASE_URL` / `LLM_MODEL` / `LLM_API_KEY` | — | The OpenAI-compatible endpoint (key **required**). Baked into the sandbox at onboard time — editing these later does not move a running agent; run `make repoint-llm` (or `make bootstrap FORCE=1` for a full reset) |
| `LLM_PROXY_PORT` | `18100` | Host port of the inference proxy the sandbox reaches the LLM through (ADR-014); `LLM_DIRECT=1` bypasses the proxy for a genuinely public endpoint |
| `TERMINAL_BIND` | `127.0.0.1` | Address the terminal daemon binds. On Linux must be the docker bridge (`172.17.0.1`) — loopback is unreachable from the gateway container. Set by `make bootstrap` |
