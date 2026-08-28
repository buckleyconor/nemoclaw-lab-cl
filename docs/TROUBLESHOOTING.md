# Troubleshooting

Symptom-first guide to the failure modes this lab actually hits. First move
for almost everything: **`make doctor`** — it checks every dependency and
prints the fix for whatever is down. `make doctor-fix` applies those fixes
instead of just printing them.

**Self-healing:** install `deploy/systemd/nemoclaw-doctor.timer` to run
`doctor.sh --fix` every 5 minutes, so a dead terminal daemon, hook-relay, or
wake-hook forward recovers on its own before anyone notices "reconnect does
nothing" — see the header comment in `nemoclaw-doctor.service` for install
steps. A run that's still red after fixing shows up as a failed systemd
unit: `systemctl --user status nemoclaw-doctor` / `journalctl --user -u
nemoclaw-doctor -e`.

## "I inject a fault and it's never detected"

This is nearly always a dead host process, not the agent or its config.
Check in this order:

1. **`make doctor`.** If the *sandbox wake-hook* line is red, openshell's
   SSH port-forward died (it does not survive reboots or openshell
   restarts, and `nemoclaw <sandbox> status` will show `Connected: no`):

   ```bash
   nemoclaw infra-sentinel recover
   make hook-relay        # the relay dials the forward per-connection, restart it too
   ```

2. If the *hook-relay bridge* line is red, only the relay is down:

   ```bash
   make hook-relay        # or: HOOK_RELAY_PORT=<port> make hook-relay
   ```

   The relay exists because openshell publishes the webhook as a forward
   hard-bound to `127.0.0.1`, which the containerized Gateway can't reach —
   the relay re-exposes it on the docker bridge (`172.17.0.1`). Don't try to
   rebind the openshell forward itself; nemoclaw's recovery re-creates it
   loopback-bound.

3. **Agent config is blank.** Every lab entry (and "Ready for a different
   scenario?") intentionally resets SOUL.md/AGENTS.md/skills to blank — if
   the persona-paste exercise wasn't completed for the current pack, the
   agent has no instructions to poll anything. Verify:

   ```bash
   nemoclaw infra-sentinel exec --no-tty -- cat /sandbox/.openclaw/workspace/SOUL.md
   ```

   Empty output → redo Part 1 of the lab guide (all six files, items 1–6
   green in the terminal menu).

4. **Detection is working but slow (~1 minute).** The webhook missed and the
   cron safety-net poll caught it instead. Normal webhook-path latency is
   ~15–20 s. If cron-fallback happens repeatedly, check the Gateway logs for
   the `_fire_agent_webhook` POST at injection time and the hook path with
   the doctor probe.

**Healthy-path sanity check** (from the gateway container, bad token → 401
proves the whole container→relay→forward→openclaw chain):

```bash
docker exec nemoclaw-lab-cl-gateway-1 python3 -c "
import urllib.request
req = urllib.request.Request('http://host.docker.internal:18790/hooks/wake',
    method='POST', headers={'Authorization':'Bearer bad-token'})
try: urllib.request.urlopen(req, timeout=5)
except urllib.error.HTTPError as e: print(e.code)   # want: 401
except Exception as e: print('BROKEN:', e)"
```

## "The terminal panel is missing or shows DISCONNECTED"

The terminal daemon is a host process (`make demo-up` starts it; or
manually: `TERMINAL_MODE=restricted SANDBOX_NAME=infra-sentinel make terminal`).
Log: `~/.local/state/nemoclaw-terminal.log`. Also check:

- **Panel missing entirely** (UI shows no terminal at all): `TERMINAL_WS_URL`/
  `TERMINAL_TOKEN` are absent from `.env` because the daemon never ran on this
  host — its first run generates them, and it needs `uv` on the host (`curl
  -LsSf https://astral.sh/uv/install.sh | sh`). Run `make terminal` or
  `make demo-up` once, then `docker compose up -d gateway` so the proxy's
  fail-safe (URL+token both set, `TERMINAL_ENABLED != "0"`) opens the panel.
- `TERMINAL_WS_URL`/`TERMINAL_TOKEN` in `.env` must match what the daemon
  printed when it generated the token, and the gateway must have been
  restarted after adding them (`docker compose up -d gateway`).
- On Linux with ufw, the compose subnet needs allow rules to reach the
  bridge-bound daemon and relay (SPEC-EMBEDDED-TERMINAL.md §6):
  `172.23.0.0/16 → 172.17.0.1:8005` and `→ :18790`.

## "The guide shows a pack-mismatch banner / wrong vertical data"

The stack serves one pack at a time (`PACK_ID`, resolved at startup).
Normally "Start Your Lab" switches automatically; the banner is the fallback
when that didn't happen. Reload the guide page (it retries the switch), or
force it: `make switch-pack PACK_ID=<id>`.

## "hook-relay won't start: port already in use"

Something already holds the bridge port — usually a previous relay instance,
or (after `nemoclaw recover` printed a port-conflict error) a stale relay
squatting the port the recovery wants:

```bash
lsof -i :18790 -sTCP:LISTEN     # find it
kill <pid>                       # then restart whichever piece you killed
```

Note the layering: `127.0.0.1:<port>` belongs to openshell's SSH forward,
`172.17.0.1:<port>` to the relay. Both must be alive; `make doctor` checks
each hop separately.

## "A resolved fault immediately reappears with no injection"

Fixed (the `remediation.execute` → simulator clear call is now verified, and
a failed clear surfaces as a `simulator_clear_failed` remediation error
instead of silent false success). If you see the error in the activity feed,
the simulator rejected the clear — check `docker compose logs simulator` for
the `/control/clear` request around that timestamp.

## "MCP server URL host ... is a private, local, or special-use IP address"

`nemoclaw mcp add` (and the resolved-address variant of the same error)
refusing an internal endpoint like `mcp-*.dev.delllabs.local`. **This is a
hardcoded SSRF guard with no workaround at the CLI level** — confirmed from
the NemoClaw source (`src/lib/security/mcp-url-target.ts`): the blocklist
reads no flag, env var, or config key; `--no-probe` skips a different,
post-add probe; and the `host.openshell.internal` alias is rejected for MCP
by design. The inference-side escape hatches (`--no-verify`,
`NEMOCLAW_TRUSTED_PRIVATE_INFERENCE_HOSTS`) have **no MCP equivalent**, and
neither do the OpenClaw policy/gateway config keys
(`network.privateNetwork.allow`, `models.providers.*.request.allowPrivateNetwork`
— browser- and model-provider-scoped respectively).

Don't fight it: this lab never needed `mcp add`. The supported route is the
one `deploy/scripts/onboard-openclaw.sh` already uses — the OpenClaw plugin's
own `mcpUrl` config plus the `policy-add` preset via `host.openshell.internal`.
For the K8s deployment, front the cluster with the agent-host proxy:

```bash
# on the agent host, once per tenant (ports per run-lab-proxy.sh header)
LAB_INGRESS_HOST=nemoclaw-<ns>.dell-demo.lab deploy/scripts/run-lab-proxy.sh
MCP_PORT=8004 GATEWAY_PORT=8001 deploy/scripts/onboard-openclaw.sh
```

Requires `mcpTools.exposeMcp: true` in the Helm values (on in
values.prod.yaml) so the ingress routes `/mcp`. Details in
docs/ARCHITECTURE-K8S.md; `make doctor` probes the proxy end-to-end.

## Private/internal LLM endpoint (inference SSRF guard)

The **inference** side of the same guard: `nemoclaw onboard` /
`nemoclaw inference set` refuse an endpoint that resolves to a private,
loopback, link-local or special-use address (the lab's shared inference
endpoint is exactly that). Unlike MCP, inference has escape hatches and a
supported route — in order of preference:

1. **The host inference proxy (ADR-014) — the standard route, no bypass
   flags.** The `host.openshell.internal` alias is exempt from the inference
   guard (a plain `nemoclaw onboard` against
   `http://host.openshell.internal:18100/...` passed with no `--no-verify` on
   the reference host), and it is also the *only* host NemoClaw network-policy
   presets may pin (policy guard #6073) — so a direct private-endpoint route
   could not be policy'd even if registration succeeded. This is what
   `deploy/scripts/run-inference-proxy.sh` sets up and what
   `onboard-openclaw.sh` / `repoint-llm.sh` use by default.
2. **`nemoclaw inference set ... --no-verify`** — skips the CLI's
   registration-time reachability probe outright. `repoint-llm.sh` falls back
   to this automatically if the clean registration is refused (CLI versions
   differ). It does not solve *runtime* reachability — that is what the proxy
   is for.
3. **`NEMOCLAW_TRUSTED_PRIVATE_INFERENCE_HOSTS=<host>`** — exists, but its
   effect is unconfirmed (the sessions that exported it also used
   `--no-verify`). Don't rely on it.

Repoint failure modes (`make repoint-llm`):
- **proxy probe fails (502/504 or no response)** — nginx is down or the
  rendered conf points at a dead upstream; re-run
  `deploy/scripts/run-inference-proxy.sh` and check `sudo nginx -t`.
- **401/403 from the endpoint** — `LLM_API_KEY` in `.env` is wrong or was
  rotated server-side.
- **model check warning** — `LLM_MODEL` is not in the endpoint's `/v1/models`
  list; fix the id in `.env`.
- **sandbox still reports the old model** — `inference set` did not take;
  full reset with `make bootstrap FORCE=1`.

MCP has none of these hatches — see the section above; the two guards share
the design intent but not the enforcement strength.

## After a host reboot

Compose services restart themselves (`restart: unless-stopped`), but the
host processes don't unless you installed the `deploy/systemd/` units:

```bash
make demo-up            # restarts anything that's down, then verifies
nemoclaw infra-sentinel recover   # if doctor still flags the wake-hook
```

If `make doctor` flags the **inference proxy** line red after a reboot,
see below — the root watchdog timer (`sudo make install-inference-watchdog`)
fixes that class automatically within a minute.

## "Agent idle: LLM 503 'inference service unavailable' in the sandbox gateway log"

Symptom: the fault injects fine (simulator/orchestrator logs show 200s) but
the dashboard never detects it and the agent stays idle. The sandbox's
`/tmp/gateway.log` (inside the openshell container) shows the cron safety-net
waking every minute, each run dying with `status=503 … rawError=503
"inference service unavailable"`. The agent's LLM route — sandbox →
`host.openshell.internal:18100` → host nginx → shared endpoint — is broken.
Two root causes, both boot-related:

1. **nginx never came up (boot race).** nginx started before Docker assigned
   the bridge address the conf binds, and the bind failed (`journalctl -u
   nginx` shows `bind() to <ip>:18100 failed (99: Cannot assign requested
   address)`). The service does not retry — it stays dead until restarted.
2. **nginx runs stale sockets.** `systemctl reload` cannot change listen
   addresses: with the old sockets still up, a new (e.g. `0.0.0.0`) bind dies
   with `EADDRINUSE`, the reload aborts, and the previous conf keeps
   serving. Loopback probes look green while the sandbox's bridge hop is
   connection-refused. (`deploy/scripts/run-inference-proxy.sh` now applies
   conf changes with a restart instead of a reload, and defaults to
   `BRIDGE_IP=0.0.0.0` so there is no specific IP to race at boot.)

Diagnose:

```bash
systemctl is-active nginx
ss -tln | grep 18100    # compare with the `listen` lines in
                        # /etc/nginx/conf.d/nemoclaw-inference-proxy-*.conf
curl -s -o /dev/null -w "%{http_code}\n" \
  -H "Authorization: Bearer $(grep LLM_API_KEY .env | cut -d= -f2)" \
  http://127.0.0.1:18100/v1/models
docker exec <openshell container> sh -c 'curl -sk -w "%{http_code}\n" \
  -x http://10.200.0.1:3128 https://inference.local/v1/models'  # the agent's
  # own route, through the sandbox egress proxy (NEMOCLAW_PROXY_* env vars)
```

Fix — a full restart re-reads the conf and rebinds, curing both states:

```bash
sudo systemctl restart nginx
```

Prevention: `sudo make install-inference-watchdog` (or `sudo
./deploy/scripts/install-watchdog.sh`) installs a root timer
(`deploy/systemd/nemoclaw-inference-watchdog.*`) that restarts nginx within
60s whenever the service is down or the conf's non-loopback bind is missing
(the stale-socket no-op-reload state above — the watchdog compares the
`listen` lines in the rendered conf against `ss -tln`, so a green loopback
smoke never hides a dead sandbox hop). A 5xx on the authed smoke alone is
logged, not restarted: that is an upstream LLM problem a restart would not
cure. The script now also defaults to `BRIDGE_IP=0.0.0.0` (nothing to race
at boot) and applies conf changes with a restart, and `make doctor` fails the
inference-proxy line on both failure states, not just a failed loopback
smoke.

## "Sandbox wake-hook dead: `openshell forward start` hangs, forward stuck `dead`"

Symptom: `nemoclaw <sandbox> status` reports "the agent delivery chain could
not be proven (forward-recovery: the primary dashboard/API host forward could
not be re-established)"; doctor's *sandbox wake-hook* line is red (000).
The agent's 1-minute cron safety net still runs, so detection degrades to
~1-minute latency rather than failing. Cause (OpenShell LKG): the dashboard
forward is an openshell-managed SSH tunnel to the host loopback that does not
survive an `openshell-gateway` restart or a host reboot, does not
re-establish on its own, and leaves a stale `dead` record that blocks
re-creation — after which `openshell forward start --background` hangs
waiting on a tunnel that never comes up (Ctrl-C it; safe to abort).

Recovery (worked 2026-08-28, in order):

```bash
# 1. Free the port number — openshell's port-in-use check is not
#    interface-aware, so a hook-relay bound to 172.17.0.1:<port> blocks the
#    127.0.0.1:<port> forward from being created.
pkill -f hook-relay.py
openshell forward stop <port> <sandbox>     # clear the stale dead record
docker restart <openshell sandbox container>  # fresh daemon + supervisor session
nemoclaw <sandbox> recover                  # re-establishes the loopback forward
HOOK_RELAY_PORT=<port> make hook-relay      # re-bridge onto the docker bridge
```

`make doctor-fix` (run by `nemoclaw-doctor.timer` every 5 min when installed,
see `deploy/systemd/`) retries the recover + relay steps automatically, so
once the forward *can* be re-established it converges without a human.
Upstream fix pending: the sandbox forward client should reconnect after a
gateway restart.

## NemoClaw LKG (v0.0.109) onboarding contract walls

The installer's `lkg` channel is ahead of the versions this repo was written
against (ADR-011 names v0.0.56). `make bootstrap` on v0.0.109 hits three
contract changes — all fixed in-tree, listed here so a future re-onboard
(`make bootstrap FORCE=1`) doesn't look like a regression:

1. **Custom `--from` Dockerfile must base on the full managed image.**
   `nemoclaw onboard --from` uses the Dockerfile as the *complete* sandbox
   image; it does not layer it over the managed runtime. Basing on
   `ghcr.io/nvidia/nemoclaw/sandbox-base` is the documented "base_only_image"
   failure — the restart-safe startup clone then exits 127 because
   `/usr/local/bin/nemoclaw-start` and the baked `/sandbox/.openclaw/
   openclaw.json` are missing. Fix (in `onboard-openclaw.sh`): base on
   `ghcr.io/nvidia/nemoclaw/openclaw-sandbox:v<cli-version>` (pin to the
   installed CLI release) and keep the trailing `WORKDIR /sandbox` +
   `USER sandbox`.
2. **`nemoclaw <name> upload` destinations are directories.** The v0.0.109
   OpenShell transport extracts the source into the destination directory
   (file → `<dest>/<name>`, dir → `<dest>/<dirname>/`). A file-path
   destination collides with the workspace templates the managed runtime
   seeds at first boot: `mkdir: cannot create directory
   '/sandbox/.openclaw/workspace/SOUL.md': File exists`. Upload files to
   `/sandbox/.openclaw/workspace/`.
3. **Inference proxy bind ≠ docker0.** OpenShell puts the sandbox on its own
   bridge network (e.g. `172.18.0.0/16`) and `host.openshell.internal`
   resolves to that network's host IP — not docker0's. The proxy then looks
   dead from the sandbox (`connection refused` on :18100, `503` on the
   agent's inference route). `run-inference-proxy.sh` now honors a
   `BRIDGE_IP` env override; the robust setting is `0.0.0.0`:
   ```bash
   sudo -E BRIDGE_IP=0.0.0.0 ./deploy/scripts/run-inference-proxy.sh
   ```
   If that's not done yet, a stopgap TCP forwarder on the sandbox network's
   host IP → `127.0.0.1:18100` works until then — kill it before the
   `0.0.0.0` bind (address conflict).

Cosmetic: until the in-sandbox `openclaw-gateway` process is restarted, the
main session's execution trace may show the managed default model id
(`nvidia/nemotron-...`) instead of the `.env` model — the sandbox's
single-route inference router resolves the request to the route's model
anyway (doctor's "agent model" check reads the config, not the trace), and a
sandbox recreate picks up the configured model. `make repoint-llm` after a
model change needs `--no-verify` internally only because the *host* can't
resolve `host.openshell.internal` (container DNS does).
