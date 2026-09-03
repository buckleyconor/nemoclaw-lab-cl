# Single-node deployment

How to bring the whole lab up on **one machine** — an NVIDIA GB10 (aarch64), a
Dell Precision workstation (x86_64), or an Ubuntu 24.04 VM. This is the
long-form version of the README's [Quick start](../README.md#quick-start-single-linux-host);
follow that if you already know the shape of the system, and this if you don't
or if something went wrong.

Decision record: [ADR-014](adr/ADR-014.md) (single-VM target),
[ADR-011](adr/ADR-011.md) (the agent is a host process, not a container).
When something is broken rather than un-built, go to
[TROUBLESHOOTING.md](TROUBLESHOOTING.md).

---

## 1. What runs where

The single most useful thing to understand before you start: **only four
things run in Docker Compose.** Everything else is a host process. That split
is deliberate — `nemoclaw onboard` drives Docker itself to manage its own
OpenShell sandbox, so nesting it inside Compose would mean Docker-in-Docker
and would undermine the sandbox's whole security purpose (ADR-011).

| | Component | Port | Notes |
|---|---|---|---|
| **container** | Gateway (API + React UI) | 8001 | The thing you open in a browser |
| **container** | Orchestrator | 8002 | Scenario rotation |
| **container** | Simulator | 8003 | Redfish-ish fleet mock |
| **container** | MCP tools | 8004 | The agent's seven tools + KB search |
| host process | OpenShell gateway | 8080 | Installed by the `nemoclaw` CLI |
| host process | Agent sandbox | — | `nemoclaw onboard`, one-time |
| host process | nginx inference proxy | 18100 | The sandbox's route to the LLM |
| host process | Terminal daemon | 8005 | Optional (ADR-012) |
| host process | Wake-hook relay | 18789/18790 | Bridges the sandbox hook to the containers |
| **external** | Your LLM endpoint | — | Never a container. See §3 |

Two consequences worth internalising now, because they cause most first-run
confusion:

- **The containers never talk to the LLM.** Only the agent sandbox does. If
  the model is misconfigured, the dashboard still looks perfectly healthy —
  the agent just silently never acts. The lab-health chip in the UI exists
  precisely to make that visible.
- **`host.docker.internal` and `host.openshell.internal` are different
  networks.** The first is how the gateway container reaches the host; the
  second is how the sandbox does. They resolve to different bridge addresses,
  which is why the proxy binds broadly and why the wake hook needs a relay.

---

## 2. Prerequisites

### Hardware

Any x86_64 or aarch64 Linux host. **The lab itself needs no GPU** — all four
services are CPU-only by design (ADR-003), and the KB search uses a small
ONNX embedding model baked into the image. A GPU matters only if this same
node is also serving the model (§3c).

Rough sizing: 4 cores and 8 GB RAM runs the lab comfortably. Serving a model
locally on top of that is what actually drives your requirements.

### Software

| Requirement | Why | Install |
|---|---|---|
| Docker + Compose plugin | The four services | `sudo apt-get install -y docker.io docker-compose-plugin` |
| Your user in the `docker` group | Scripts run docker unprivileged | `sudo usermod -aG docker $USER` then log out/in |
| `git` | Clone the repo | `sudo apt-get install -y git` |
| `python3`, `openssl`, `curl` | hook-relay, token generation, probes | Present on stock Ubuntu |
| `nginx` | The sandbox → LLM route (§3) | `sudo apt-get install -y nginx` |
| `nemoclaw` CLI | The agent runtime | `curl -fsSL https://www.nvidia.com/nemoclaw.sh \| bash` |
| `uv` | Runs the terminal daemon | `curl -LsSf https://astral.sh/uv/install.sh \| sh` |

`make bootstrap` checks all of these and tells you which is missing. `uv` is
only needed for the optional embedded terminal, so it warns rather than fails.

### CLI version ↔ sandbox image coupling

`nemoclaw onboard --from` treats our Dockerfile as the **complete** sandbox
image rather than layering it over the managed runtime, so the image it bases
on must match the CLI release driving the onboard. A mismatch fails roughly
ten minutes into the image build with a `base_only_image` / exit-127 error
that reads like a code bug.

The tag is derived from `nemoclaw --version` automatically
(`deploy/scripts/lib/nemoclaw.sh`), and `make bootstrap` prints the pairing:

```
  ✓ docker (+compose), nemoclaw v0.0.102, .env
  ✓ sandbox base   ghcr.io/nvidia/nemoclaw/openclaw-sandbox:v0.0.102
```

To pin a known-good image instead, export `SANDBOX_BASE` before onboarding.

### If Docker came from snap

Snap's Docker cannot see `/tmp`, and `/snap/bin` is missing from some PATHs.
Set both in `.env`:

```bash
NEMOCLAW_TMPDIR=/home/<user>/tmp-nemoclaw   # create it first
```

The scripts self-heal the PATH; this is the one thing they cannot.

---

## 3. Point the lab at an inference endpoint

The lab needs an **OpenAI-compatible endpoint that supports tool calling**.
Tool calling is not optional — the entire agent loop is tool calls. A model
without it will onboard cleanly and then never do anything, which is a
genuinely confusing failure.

Three variables drive everything, in `.env`:

```bash
LLM_BASE_URL=https://model.example.lab/api/qwen36/v1   # must include /v1
LLM_API_KEY=<real key>
LLM_MODEL=qwen3.6-35b-a3b-fp8                          # as the endpoint reports it
```

Verify before you go further — this is exactly what bootstrap will do, and
catching it here saves a long build:

```bash
curl -sS -H "Authorization: Bearer $LLM_API_KEY" "$LLM_BASE_URL/models" | head -40
```

You want HTTP 200 and your `LLM_MODEL` in the listing. `401`/`403` means the
key is wrong; a timeout means the host can't reach the endpoint at all.

> **These values are baked into the sandbox at onboard time.** Editing `.env`
> afterwards does not move a running agent — see §7 for `make repoint-llm`.

### 3a. An existing private or internal endpoint — the standard path

This covers the shared lab endpoint and any vLLM/NIM/TGI server running on
another machine on your network. It is the ADR-014 default.

You cannot point the sandbox straight at a private address. NemoClaw's
inference SSRF guard refuses endpoints that resolve to private ranges, and its
network-policy presets may only pin the host alias `host.openshell.internal`
(policy guard #6073). So a small nginx listener on this host bridges the two:

```
sandbox → http://host.openshell.internal:18100/v1/... → $LLM_BASE_URL/...
```

Bring it up:

```bash
deploy/scripts/run-inference-proxy.sh
```

It renders the config, restarts nginx, asserts the port is actually listening,
and smoke-tests `/v1/models` through the proxy. It is idempotent, and
`make bootstrap` offers to run it for you if you skip this step.

Two things worth knowing about it:

- **It binds all interfaces by default** (`BRIDGE_IP=0.0.0.0`). That is
  deliberate: the OpenShell sandbox gets its own bridge network, so the
  address it will dial isn't known in advance, and a specific-IP bind fails at
  boot before Docker has assigned it. The listener adds no auth of its own, so
  on a host with a public NIC **the firewall is what keeps it private** — see
  §6. Pass `BRIDGE_IP=<addr>` to bind one interface instead.
- **For a TLS upstream, set `LLM_CA`** to the CA bundle path. Without it the
  proxy does not verify the upstream certificate and says so loudly.

The proxy also decouples you from the endpoint: because the sandbox-facing URL
never changes, moving to a different endpoint later is an nginx re-render, not
a sandbox rebuild.

### 3b. An existing public endpoint

If your endpoint resolves publicly and has a real certificate, you can skip
the proxy:

```bash
LLM_DIRECT=1
```

`LLM_BASE_URL` is then baked into the sandbox verbatim. Only use this for a
genuinely public host — the SSRF guard will reject anything else, and you'll
have traded a working setup for a confusing error. With `LLM_DIRECT=1` the
UI's inference-proxy health check reports `skip` rather than a false red.

### 3c. Serving the model on this same node

The GB10 case: run vLLM or Ollama locally and point the lab at it.

Both bind a **private** address, so §3a applies unchanged — you still need the
inference proxy. Use the host's LAN/bridge IP rather than `127.0.0.1`, since
the proxy must reach it from the host network namespace.

**vLLM:**

```bash
vllm serve <model> --host 0.0.0.0 --port 8000 --enable-auto-tool-choice \
  --tool-call-parser hermes            # parser depends on the model family
```
```bash
LLM_BASE_URL=http://<host-ip>:8000/v1
LLM_MODEL=<the id vLLM reports at /v1/models>
LLM_API_KEY=<anything, unless you set --api-key>
```

Tool calling is off by default in vLLM. Without `--enable-auto-tool-choice`
and a matching `--tool-call-parser`, the agent will never call a tool.

**Ollama:**

```bash
LLM_BASE_URL=http://<host-ip>:11434/v1     # the /v1 suffix is required
LLM_MODEL=qwen3:32b                        # must be a tool-calling model
LLM_API_KEY=ollama                         # ignored by Ollama, but see below
```

Two gotchas: Ollama's OpenAI-compatible API lives under `/v1`, not at the
root; and although it needs no key, the deploy scripts reject an empty or
`CHANGE_ME` value — put any non-placeholder string there. Also set
`OLLAMA_HOST=0.0.0.0` so it listens beyond loopback.

Whichever you use, confirm tool calling actually works before onboarding:

```bash
curl -sS "$LLM_BASE_URL/chat/completions" -H "Authorization: Bearer $LLM_API_KEY" \
  -H 'Content-Type: application/json' -d '{
    "model": "'"$LLM_MODEL"'",
    "messages": [{"role":"user","content":"What is the weather in Cork?"}],
    "tools": [{"type":"function","function":{"name":"get_weather",
      "parameters":{"type":"object","properties":{"city":{"type":"string"}}}}}]
  }' | grep -q tool_calls && echo "✓ tool calling works" || echo "✗ no tool_calls in response"
```

---

## 4. Deployment steps

```bash
git clone https://github.com/buckleyconor/nemoclaw-lab-cl.git
cd nemoclaw-lab-cl
```

### Step 1 — configure

```bash
cp .env.example .env
$EDITOR .env          # set LLM_BASE_URL, LLM_MODEL, LLM_API_KEY (§3)
```

### Step 2 — bring up the inference proxy

```bash
deploy/scripts/run-inference-proxy.sh
```

Expect `✓ proxy live: http://host.openshell.internal:18100/v1 -> <your endpoint>`.
Skip this only if you set `LLM_DIRECT=1`. (`make bootstrap` will offer to run
it if you forget.)

### Step 3 — build, onboard, verify

```bash
make bootstrap
```

Five phases, ~10-15 minutes on a first run (most of it building the sandbox
image and the fastembed warmup):

1. **Preflight** — tooling, `.env`, an *authenticated* probe of your endpoint,
   the proxy, and the CLI/sandbox-image pairing. It fails here rather than ten
   minutes into a build, because the endpoint is baked in at onboard time.
2. **`TERMINAL_BIND`** — detected from `docker0` and written to `.env`. On
   Linux `host.docker.internal` resolves to the docker bridge, so a loopback
   bind would be unreachable from the gateway container.
3. **`docker compose up -d --build`** — the four services.
4. **Onboarding** — builds the sandbox image with the MCP plugin baked in,
   onboards it, applies the network policy, seeds `SOUL.md`/`AGENTS.md`/skills,
   locks the built-in tools down, enables the webhook, and reads the real hook
   port back out of the sandbox config (18789 is only a default — OpenClaw
   silently reassigns it if taken).
5. **Hand-off to `demo-up.sh`** — host daemons, then the doctor preflight.

`make bootstrap` is idempotent and safe to re-run. It never runs `sudo`
itself, except the one interactive prompt to bring up the proxy. It will
**not** re-onboard an existing sandbox — that rebuilds the image and resets
the agent's config. Use `make bootstrap FORCE=1` when you do want that.

### Step 4 — firewall

If `ufw` is active, bootstrap prints three `sudo ufw allow` rules. **Run them.**
See §6 for why skipping this produces a lab that looks healthy and does
nothing.

### Step 5 — survive reboots

```bash
sudo make install-selfheal
```

One sudo prompt. Installs the inference watchdog, the wake-hook chain, the
terminal daemon unit, and a `doctor --fix` timer. Without it, a reboot brings
the containers back but not the host processes — and nothing tells you. See
the README's *Surviving a reboot* for the full unit list.

### Step 6 — verify

```bash
make doctor
```

Expect every line green, then open <http://localhost:8001/lab/>.

---

## 5. Verifying it actually works

`make doctor` is the authoritative check — a red/green table of every moving
part, each failure printing its own fix. Beyond that:

```bash
# The gateway's own view, from inside the container
curl -s localhost:8001/api/lab-health | python3 -m json.tool
# → "healthy": true, with "ok" or "skip" for every check

# The active vertical
curl -s localhost:8001/api/pack | python3 -m json.tool
```

In the UI, the lab-health chip on the dashboard is the same data. Green means
the lab is working; red means the lab is broken, as distinct from "no faults
right now".

**The real end-to-end test** is injecting a fault and watching the agent
respond. Start a scenario from the lab guide, then watch the operator feed. A
correctly wired lab detects it in **15-20 seconds** via the webhook. If it
takes about a minute, the webhook is dead and you are seeing the cron safety
net — run `make doctor`, which will name the broken hop.

Note that `uv run pytest` covers the services, not the deployment: every test
runs in-process, including `tests/e2e/`. Deployment verification is `make
doctor`, not pytest.

---

## 6. Firewall

ufw's default-deny INPUT drops **all** container→host traffic. Every hop this
lab depends on crosses that boundary, and every one of them fails *silently*:
the terminal panel shows disconnected, the wake hook never fires so faults
look undetected, and the agent's LLM route dies. The dashboard shows a healthy
fleet and an idle agent — indistinguishable from "nothing is wrong right now".

The compose subnet is pinned to `172.28.100.0/24` so these rules are the same
on every host:

```bash
sudo ufw allow from 172.28.100.0/24 to 172.17.0.1 port 8005 proto tcp \
  comment 'nemoclaw terminal daemon (ADR-012)'
sudo ufw allow from 172.28.100.0/24 to any port 18790 proto tcp \
  comment 'openclaw wake hook (ADR-011)'
sudo ufw allow from 172.16.0.0/12 to any port 18100 proto tcp \
  comment 'nemoclaw inference proxy (ADR-014)'
```

Check your own values rather than pasting blind — `172.17.0.1` is docker0's
stock address but is configurable, and the hook port may be 18789 or 18790.
`make bootstrap` prints all three rules with the live-detected values.

The third rule is also what keeps the inference proxy private, since it binds
all interfaces by default (§3a).

---

## 7. Day-two operations

```bash
make demo-up                        # stack + host daemons + preflight
make doctor                         # red/green check of everything
make switch-pack PACK_ID=oil-rigs   # change vertical (recreates the stack)
make logs                           # tail all service logs
make down                           # stop
```

**Changing the endpoint or model.** The LLM settings are baked into the
sandbox, so editing `.env` alone moves nothing:

```bash
$EDITOR .env            # update LLM_BASE_URL / LLM_MODEL / LLM_API_KEY
make repoint-llm        # re-renders the proxy + syncs the sandbox, no rebuild
```

Takes seconds. `make doctor` flags a `.env`-vs-sandbox model mismatch if a
repoint was forgotten. If a repoint fails, the full reset is
`make bootstrap FORCE=1`. Note that whether a *changed API key* re-persists
through `inference set` is unproven — if key rotation doesn't take, fall back
to the full re-onboard.

**Switching verticals** recreates the stack (~15s). It has to: `PACK_ID` is
resolved at startup and the KB index and fault registries are pack-scoped.
No code changes or image rebuilds are involved — see [ADR-007](adr/ADR-007.md).

---

## 8. First-run traps

Three things bite on a fresh host specifically. Everything else lives in
[TROUBLESHOOTING.md](TROUBLESHOOTING.md), which is organised by symptom.

1. **CLI / sandbox image version skew.** Fails ~10 minutes into the image
   build with `base_only_image` or exit 127. Check the pairing bootstrap
   prints; pin with `SANDBOX_BASE` if needed (§2).
2. **The ufw rules were not applied.** The lab comes up green and the agent
   never acts. `make doctor` catches it; §6 fixes it.
3. **Onboarding ran before the proxy was live.** The endpoint URL is baked in
   at onboard time, so a proxy that came up afterwards doesn't retroactively
   fix the sandbox. `make repoint-llm` re-points it without a rebuild.

A useful habit: when anything seems wrong, run `make doctor` **first**. Both
failures logged during development were dead host processes, not code — and
they presented identically to an agent bug.
