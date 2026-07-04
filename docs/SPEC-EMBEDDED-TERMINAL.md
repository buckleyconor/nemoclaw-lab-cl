# Spec: Embedded Operator Terminal

**Status:** Implemented (`services/terminal/`, `services/gateway/terminal.py`, `ui/src/components/TerminalPanel.tsx`). Decision record: [ADR-012](adr/ADR-012.md). Roadmap: M12. Verification status: §10.

This document is the functional and technical specification for the embedded terminal panel in the lab dashboard. It is written to be implementable without further design decisions.

---

## 1. Requirement

An embedded terminal in the lab UI so the operator can configure the NemoClaw agent from the dashboard itself:

- **Placement:** below the Operator Dashboard panel.
- **Look and feel:** matches the rest of the UI — a clean, modern panel styled like the existing panels.
- **Function:** configure the NemoClaw agent settings (SOUL.md and SKILL.md) via the command line; paste commands/text into the terminal; use a text editor (nano, vi) with write permissions; run `nemoclaw`, `openclaw`, and `openshell` commands.
- **Behavior:** acts as a regular terminal — fixed height, scrollable history, always auto-scrolls to the bottom / command prompt.
- **Palette:** a terminal colour palette that fits the rest of the UI.

## 2. Architecture

```
Browser                     Gateway container (:8001)          Lab host
┌─────────────────┐         ┌──────────────────────┐          ┌──────────────────────────┐
│ TerminalPanel   │  WS     │ /api/terminal/ws     │   WS     │ terminal daemon (:8005)  │
│ (xterm.js)      │◄───────►│ (proxy; injects      │◄────────►│ host-local bind only     │
│                 │         │  bearer token)       │  token   │  └─ PTY ── bash -l       │
└─────────────────┘         └──────────────────────┘          │      (user democenter,    │
                                                              │       cwd = repo root)    │
                                                              └──────────────────────────┘
```

- The daemon is a **host process** (like the agent itself, ADR-011) — never a Compose service. The host is where the `nemoclaw`/`openclaw`/`openshell` CLIs, their state, and the Docker daemon that runs the sandboxes all live.
- The gateway hop uses `host.docker.internal`, the same route as the ADR-011 webhook wake-up (`OPENCLAW_HOOK_URL`). Proven working on the current host; for portability on other Linux hosts add `extra_hosts: ["host.docker.internal:host-gateway"]` to the gateway service.
- The proxy exists so the operator's existing single-port workflow (`ssh -L 8001:localhost:8001`) keeps working unchanged — no second tunnel, no second exposed port.

## 3. UI spec

### 3.1 Placement and chrome

- New component `ui/src/components/TerminalPanel.tsx`, rendered in `ui/src/App.tsx` directly below the Operator Dashboard block, under its own `.section-heading`: **"Agent Configuration Terminal"**.
- Panel chrome copies the `ActivityFeed.tsx` conventions:
  - Container: `background: var(--bg-card)`, `border: 1px solid var(--border)`, `borderRadius: 10`, `overflow: hidden`, flex column.
  - Header bar: `padding: 10px 14px`, `borderBottom: 1px solid var(--border)`, `fontSize: 10`, `fontWeight: 600`, `letterSpacing: .12em`, uppercase, `color: var(--text-dim)`, with the 3×12px `var(--nv-green)` accent bar span, then the label.
  - Header right side: connection status dot (● `var(--healthy)` connected / `var(--critical)` disconnected) + a small **Reconnect** button (visible when disconnected), styled like the Export Report button (mono font, 10px, bordered pill).
- **Fixed height: 420px** (matches ActivityFeed's `maxHeight: 420` for visual rhythm). The xterm viewport fills the panel body.
- Terminal well background: `var(--bg-panel)` (`#0f1624`) — one step darker than the card, so the terminal reads as a "screen" inside the panel.
- The panel renders **only when** `GET /api/terminal/enabled` returns `{"enabled": true}` — when disabled or the endpoint errors, neither the panel nor its section heading appears.

### 3.2 xterm theme

Frontend deps: `@xterm/xterm` + `@xterm/addon-fit` (plus the packaged `xterm.css`). Theme derived from the design tokens in `ui/src/index.css`; the bright variants reuse tints already present in component inline styles (ActivityFeed/OperatorDashboard step colours).

| xterm option | Value | Source |
|---|---|---|
| `background` | `#0f1624` | `--bg-panel` |
| `foreground` | `#e5e7eb` | `--text` |
| `cursor` | `#76b900` | `--nv-green` |
| `cursorAccent` | `#0f1624` | `--bg-panel` |
| `selectionBackground` | `#3b82f659` | `--accent` @ 35% |
| `black` | `#0a0e1a` | `--bg` |
| `red` | `#ef4444` | `--critical` |
| `green` | `#10b981` | `--healthy` |
| `yellow` | `#f59e0b` | `--warning` |
| `blue` | `#3b82f6` | `--accent` |
| `magenta` | `#8b5cf6` | awaiting-approval purple |
| `cyan` | `#38bdf8` | "present" step blue |
| `white` | `#e5e7eb` | `--text` |
| `brightBlack` | `#6b7280` | `--text-dim` |
| `brightRed` | `#f87171` | denied tint |
| `brightGreen` | `#34d399` | approve-hover tint |
| `brightYellow` | `#fbbf24` | lighter warning tint |
| `brightBlue` | `#60a5fa` | KB-article blue |
| `brightMagenta` | `#a78bfa` | "diagnose" step tint |
| `brightCyan` | `#7dd3fc` | lighter cyan tint |
| `brightWhite` | `#f9fafb` | near-white |

Other xterm options: `fontFamily`: the `--mono` stack (`"JetBrains Mono", "SF Mono", "Menlo", ui-monospace, monospace`); `fontSize: 12`; `scrollback: 5000`; `cursorBlink: true`.

### 3.3 Behavior

- **Autoscroll:** xterm's native behavior — pinned to bottom on new output; scrolling up to read history un-pins until the user returns to the bottom (or presses a key). This satisfies "always auto scrolls to the bottom to the command prompt" while still allowing history review.
- **Paste:** native xterm/browser clipboard — Ctrl+Shift+V and right-click paste. The async clipboard API requires a secure context; `localhost` qualifies, and all access is via the `ssh -L 8001` tunnel, so this just works. Multi-line paste is passed through raw (bracketed paste is handled by the shell/editor).
- **Resize:** `@xterm/addon-fit` + a `ResizeObserver` on the panel body; on fit, send a resize control message (§5.3) so the PTY's winsize tracks the rendered cols/rows. Editors reflow correctly.
- **Reconnect:** each WebSocket connection = one fresh PTY/shell. On disconnect (daemon restart, network blip, page reload) the panel shows the disconnected state and the Reconnect button starts a new session. Optional enhancement (not required for v1): launch the shell as `tmux new -A -s lab` so a session survives reloads.
- **Session end:** when the shell exits (user types `exit`), the daemon sends an exit control message and closes; the panel shows "session ended" + Reconnect.

## 4. Terminal daemon spec (`services/terminal/`)

New FastAPI app, run **on the host** with the repo's uv environment. Not in docker-compose.

- **Bind:** `TERMINAL_BIND:TERMINAL_PORT`, default `127.0.0.1:8005`. On a Linux host with a containerized gateway, `host.docker.internal` (`host-gateway`) resolves to the **docker bridge IP**, from which a loopback bind is unreachable — set `TERMINAL_BIND` to the bridge address (typically `172.17.0.1`) there. That address is still host-internal (containers + host only). Never bind a LAN-facing interface.
- **Auth:** requires env `TERMINAL_TOKEN` (refuses to start without it). Every WS handshake must carry `Authorization: Bearer <token>`; mismatch → close with policy violation before spawning anything.
- **Endpoint `WS /ws`:** on accept, spawn `/bin/bash -l` in a new PTY:
  - user: the invoking user (the lab operator account that owns `~/.local/bin/nemoclaw` etc. — a login shell so `~/.local/bin` lands on `PATH`),
  - cwd: repo root,
  - env: inherit + `TERM=xterm-256color`,
  - then pump bidirectionally between the PTY fd and the WebSocket (stdlib `pty`/`os` + `loop.add_reader`, or an equivalent async pump).
- **Lifecycle:** WS closed → `SIGHUP`+terminate the child process group; child exits → send `{"type":"exit","code":N}` text frame, then close the WS. No session persistence, no multiplexing — one WS, one PTY, one shell.
- **Health:** `GET /healthz` (no auth) → `{"status":"ok"}`, consistent with the other services.

## 5. Gateway changes (`services/gateway/`)

### 5.1 Config (env, wired through docker-compose + `.env.example`)

| Var | Default | Meaning |
|---|---|---|
| `TERMINAL_WS_URL` | *(unset)* | Daemon WS URL, e.g. `ws://host.docker.internal:8005/ws` |
| `TERMINAL_TOKEN` | *(unset)* | Shared bearer token (same handling pattern as `OPENCLAW_HOOK_TOKEN`) |
| `TERMINAL_ENABLED` | *(unset)* | Kill switch; `0` forces the feature off even when configured |

**Fail-safe default:** the feature is enabled only when `TERMINAL_WS_URL` **and** `TERMINAL_TOKEN` are both set **and** `TERMINAL_ENABLED != "0"`. Unconfigured deployments get no terminal — same fail-safe posture as the webhook wake-up.

### 5.2 Endpoints

- `GET /api/terminal/enabled` → `{"enabled": bool}` (always registered; drives panel visibility).
- `WS /api/terminal/ws` → proxy. Accept the browser WS, open a client WS to `TERMINAL_WS_URL` with `Authorization: Bearer $TERMINAL_TOKEN` (the token **never** reaches the browser), then run two pump tasks (browser→daemon, daemon→browser) until either side closes; propagate closes both ways. Client WS via the `websockets` package — already resolved in `uv.lock` through `uvicorn[standard]`, so no new dependency.

### 5.3 Wire protocol (browser ⇄ gateway ⇄ daemon, transparent proxy)

- **Binary frames:** raw terminal bytes, both directions.
- **Text frames:** JSON control messages:
  - client → server: `{"type":"resize","cols":C,"rows":R}` → daemon applies `TIOCSWINSZ`.
  - server → client: `{"type":"exit","code":N}` just before close.

## 6. Ops

- `deploy/scripts/run-terminal.sh`: generates `TERMINAL_TOKEN` if unset (`openssl rand -hex 24`), prints the two lines to add to `.env` (`TERMINAL_WS_URL`, `TERMINAL_TOKEN`) for the gateway, then execs `uv run uvicorn --factory services.terminal.main:create_app --host 127.0.0.1 --port 8005` (a factory rather than a module-level `app`, so importing the module never trips the token gate).
- Makefile target `make terminal` wrapping the script.
- If the daemon is down, the dashboard still works fully — the terminal panel just shows disconnected.
- **Hosts with ufw active (this GB10 host is one):** ufw's default-deny INPUT drops *all* container→host traffic, which blocks the gateway→daemon hop — and, discovered during M12 verification, has been silently breaking the ADR-011 webhook wake-up (`host.docker.internal:18790`) too, since that call is deliberately best-effort. Allow the compose subnet to reach the two host services:
  ```
  sudo ufw allow from 172.23.0.0/16 to 172.17.0.1 port 8005 proto tcp comment 'nemoclaw terminal daemon (ADR-012)'
  sudo ufw allow from 172.23.0.0/16 to any port 18790 proto tcp comment 'openclaw wake hook (ADR-011)'
  ```
  (`172.23.0.0/16` = the `nemoclaw-lab-cl_default` compose network; `172.17.0.1` = docker0, the daemon's `TERMINAL_BIND` on this host.)
- **Wake hook needs a relay too (ADR-011):** the ufw rule alone doesn't fix the webhook — openshell publishes the sandbox's hook port as an SSH forward hard-bound to `127.0.0.1:18790` and refuses a second forward on the same port, so containers can't reach it even with ufw open. Run `make hook-relay` (`deploy/scripts/hook-relay.py`), which binds `172.17.0.1:18790` and dials the loopback forward per connection — it survives sandbox/tunnel restarts and leaves the openshell-managed forward untouched.
- Systemd user unit: optional later hardening, out of scope for v1.

## 7. Security model

**Threat:** this is a full interactive shell on the lab host, as the lab user, reachable from the dashboard origin. Anyone who can open the dashboard can run anything the lab user can.

**Posture:** that is the *same trust boundary* as the existing Approve/Deny remediation buttons — the lab's current model is "whoever reaches port 8001 is the operator", and access is via a private network / SSH tunnel. Accepted for this single-operator demo lab, with these mandatory mitigations:

1. Daemon binds a host-local address only — loopback by default, or the docker bridge IP where the containerized gateway must reach it (§4); port 8005 is never published, port-forwarded, or firewalled open.
2. Gateway↔daemon bearer token, injected server-side; the browser never sees it. This prevents anything that can merely *reach* port 8005's host from using the daemon without the token, and keeps the daemon unusable if it is ever accidentally exposed.
3. Feature is off unless explicitly configured (§5.1), with `TERMINAL_ENABLED=0` as a hard kill switch.
4. **The M9 shared/multi-user deployment MUST run with the terminal disabled.** Re-enabling it there requires real per-user authentication and audit, which is explicitly out of scope here.
5. No terminal input/output recording in v1 — flagged as a consequence in ADR-012, revisit if the lab is ever used unattended.

## 8. Agent-configuration workflows this enables

The stated purpose is configuring the NemoClaw agent. The canonical flows, all inside the panel:

- **Edit the agent persona/skills (inside the sandbox):**
  `nemoclaw infra-sentinel connect` → `nano /sandbox/.openclaw/workspace/SOUL.md` (or `skills/<skill>/SKILL.md`) → save, `exit` → `nemoclaw infra-sentinel recover` to restart the agent gateway with the new persona.
- **Push files from the host into the sandbox:**
  edit under `openclaw/` in the repo checkout, then `nemoclaw infra-sentinel upload openclaw/SOUL.md /sandbox/.openclaw/workspace/SOUL.md`.
- **Sandbox lifecycle / diagnostics:** `nemoclaw list`, `nemoclaw infra-sentinel status|doctor|logs --follow|recover`, `openshell sandbox list`, `nemoclaw infra-sentinel policy-list` etc.

Cross-reference: the parked per-pack persona plan (`docs/PACK-EXPANSION-PLAN.md`, Part A) expects SOUL.md/SKILL.md to be **hand-configured per pack** — this terminal is the intended tool for that workflow.

## 9. Out of scope (v1)

- Multiple tabs / concurrent sessions per browser.
- Auto-connecting into the sandbox (the session starts as a host shell; the operator runs `connect` themselves).
- Session persistence across reloads (tmux wrapper noted as optional enhancement).
- Recording/auditing terminal input.
- Per-user authentication (prerequisite for any shared deployment — see §7).

## 10. Verification plan (for the implementation phase)

1. `make terminal` (or the script) starts the daemon; `docker compose up -d gateway` with the new env; open `http://localhost:8001`.
2. Panel renders below the Operator Dashboard, styled per §3; status dot green.
3. `nemoclaw list` prints both sandboxes; `openshell sandbox list` works (host PATH correct).
4. `nano /tmp/spec-test.txt` — opens full-screen, edit, save, `cat` confirms (PTY + editor + write perms).
5. Paste a multi-line command block — executes correctly.
6. Resize the browser window — terminal reflows; editor redraws correctly after resize.
7. Reload the page — panel reconnects to a fresh shell via Reconnect (or auto-connect).
8. `nemoclaw infra-sentinel connect` → edit SOUL.md with vi inside the sandbox → exit (the headline workflow).
9. Set `TERMINAL_ENABLED=0`, restart gateway — panel and heading absent; `/api/terminal/enabled` returns false.
10. Stop the daemon — panel shows disconnected; rest of the dashboard unaffected.
11. Full inject → detect → diagnose → approve → resolve demo flow still passes; plugin (`npm test`) and UI (`npm run build`) suites still pass.

**Verification status (2026-07-04):** items 3–5 and the PTY/editor/exit/resize mechanics verified end-to-end programmatically (WS client → gateway proxy → daemon → PTY: `nemoclaw list`, vi full-screen edit + write-back, `stty size` after resize, exit control frame); daemon-down degradation and the fail-safe/kill-switch gates covered live + by `tests/unit/test_terminal.py`; pytest (245), plugin (18), and UI build/lint all pass. **Update (2026-07-04, ufw rules applied):** the in-container gateway→daemon hop now works — full path verified programmatically from inside the gateway container (WS `/api/terminal/ws` → daemon PTY → command output + exit frame). The ADR-011 wake hook additionally needed the §6 relay (openshell's forward binds loopback only); with `make hook-relay` running, the hook endpoint answers from the gateway container (401 on a bad-token probe = full path to OpenClaw's auth). **Outstanding:** browser items (2, 6–8) to be confirmed visually.
