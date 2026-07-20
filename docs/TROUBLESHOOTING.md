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

## "The terminal panel shows DISCONNECTED"

The terminal daemon is a host process (`make demo-up` starts it; or
manually: `TERMINAL_MODE=restricted SANDBOX_NAME=infra-sentinel make terminal`).
Log: `~/.local/state/nemoclaw-terminal.log`. Also check:

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

## After a host reboot

Compose services restart themselves (`restart: unless-stopped`), but the
host processes don't unless you installed the `deploy/systemd/` units:

```bash
make demo-up            # restarts anything that's down, then verifies
nemoclaw infra-sentinel recover   # if doctor still flags the wake-hook
```
