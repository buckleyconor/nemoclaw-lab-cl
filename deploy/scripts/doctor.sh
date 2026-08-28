#!/usr/bin/env bash
# Demo preflight — checks every moving part the lab needs and prints a
# red/green table with the fix for anything that's down.
#
# The failure class this exists for: the terminal daemon, hook-relay, and
# openshell's SSH port-forward are host processes that die silently (reboot,
# session end), and the only symptom is "faults are never detected" — which
# looks exactly like an agent/config bug. Both such incidents during
# development were host-process failures, not code. Run this first.
#
# Usage: make doctor        (report only)
#        make doctor-fix    (report + apply the fixes automatically)
#        deploy/scripts/doctor.sh [--fix]
# Exit code: 0 if everything passes (after fixing, when --fix is given),
# 1 otherwise. The nemoclaw-doctor.timer systemd unit runs `--fix` on a
# schedule so these failures self-heal before anyone hits "reconnect" —
# see deploy/systemd/nemoclaw-doctor.{service,timer}.
set -uo pipefail

cd "$(dirname "$0")/../.."

FIX=0
[[ "${1:-}" == "--fix" ]] && FIX=1

# No colors when not on a tty (journalctl, log files) — raw escape codes
# just add noise there.
if [[ -t 1 ]]; then
  GREEN=$'\033[32m'; RED=$'\033[31m'; YELLOW=$'\033[33m'; DIM=$'\033[2m'; RESET=$'\033[0m'
else
  GREEN=''; RED=''; YELLOW=''; DIM=''; RESET=''
fi
FAILURES=0
STATE_DIR="${HOME}/.local/state"
mkdir -p "$STATE_DIR"

# shellcheck source=deploy/scripts/lib/envfile.sh
source deploy/scripts/lib/envfile.sh   # for bridge_ip (env_get is redefined below)
BRIDGE_IP="$(bridge_ip)"

# Pull the vars we probe with from .env (never exported — read-only probe).
env_get() { grep -E "^$1=" .env 2>/dev/null | head -1 | cut -d= -f2-; }
HOOK_URL="$(env_get OPENCLAW_HOOK_URL)"
TERMINAL_WS_URL="$(env_get TERMINAL_WS_URL)"
TERMINAL_BIND="$(env_get TERMINAL_BIND)"; TERMINAL_BIND="${TERMINAL_BIND:-127.0.0.1}"
LLM_BASE_URL="$(env_get LLM_BASE_URL)"
[[ -z "$LLM_BASE_URL" ]] && LLM_BASE_URL="$(env_get VLLM_BASE_URL)"
LLM_API_KEY="$(env_get LLM_API_KEY)"
LLM_MODEL="$(env_get LLM_MODEL)"
LLM_PROXY_PORT="$(env_get LLM_PROXY_PORT)"; LLM_PROXY_PORT="${LLM_PROXY_PORT:-18100}"
LLM_DIRECT="$(env_get LLM_DIRECT)"
SANDBOX_NAME="$(env_get SANDBOX_NAME)"; SANDBOX_NAME="${SANDBOX_NAME:-infra-sentinel}"

pass() { printf "  %s✓%s %-34s %s\n" "$GREEN" "$RESET" "$1" "${2:-}"; }
fail() {
  printf "  %s✗%s %-34s %s\n" "$RED" "$RESET" "$1" "${2:-}"
  printf "      %sfix:%s %s\n" "$YELLOW" "$RESET" "$3"
  FAILURES=$((FAILURES + 1))
}
skip() { printf "  %s-%s %-34s %s%s%s\n" "$DIM" "$RESET" "$1" "$DIM" "$2" "$RESET"; }
fixing() { printf "      %sfixing:%s %s\n" "$YELLOW" "$RESET" "$1"; }

http_code() { curl -s -o /dev/null -w "%{http_code}" --max-time 4 "$@" 2>/dev/null; }

echo "NemoClaw demo preflight"
echo

# ── 1. Compose services (probe published healthz ports directly) ────────────
SERVICE_ORDER=(gateway orchestrator simulator mcp-tools)
declare -A SERVICE_PORT=( [gateway]=8001 [orchestrator]=8002 [simulator]=8003 [mcp-tools]=8004 )
declare -A SERVICE_UP

check_services() {
  local any_down=0
  for name in "${SERVICE_ORDER[@]}"; do
    if [[ "$(http_code "http://localhost:${SERVICE_PORT[$name]}/healthz")" == "200" ]]; then
      SERVICE_UP[$name]=1
    else
      SERVICE_UP[$name]=0
      any_down=1
    fi
  done
  return "$any_down"
}

check_services
if [[ $? -ne 0 && "$FIX" -eq 1 ]]; then
  fixing "docker compose up -d"
  docker compose up -d >/dev/null 2>&1
  sleep 3
  check_services || true
fi
for name in "${SERVICE_ORDER[@]}"; do
  if [[ "${SERVICE_UP[$name]}" -eq 1 ]]; then
    pass "$name (:${SERVICE_PORT[$name]})"
  else
    fail "$name (:${SERVICE_PORT[$name]})" "not answering" "docker compose up -d   (make up)"
  fi
done

# ── 2. Active pack ───────────────────────────────────────────────────────────
PACK_JSON=$(curl -s --max-time 4 http://localhost:8001/api/pack 2>/dev/null)
PACK_ID=$(printf '%s' "$PACK_JSON" | python3 -c "import json,sys; print(json.load(sys.stdin).get('id',''))" 2>/dev/null)
if [[ -n "$PACK_ID" ]]; then
  pass "active pack" "$PACK_ID"
else
  fail "active pack" "GET /api/pack failed" "gateway isn't serving — see above"
fi

# ── 3. Terminal daemon (host process, ADR-012/013) ──────────────────────────
if [[ -n "$TERMINAL_WS_URL" ]]; then
  TERM_HTTP="${TERMINAL_WS_URL/ws:\/\//http://}"; TERM_HTTP="${TERM_HTTP%/ws}"
  TERM_HTTP="${TERM_HTTP/host.docker.internal/$TERMINAL_BIND}"

  terminal_up() { [[ "$(http_code "${TERM_HTTP}/healthz")" == "200" ]]; }

  if ! terminal_up && [[ "$FIX" -eq 1 ]]; then
    fixing "TERMINAL_MODE=restricted SANDBOX_NAME=$SANDBOX_NAME make terminal"
    TERMINAL_MODE=restricted SANDBOX_NAME="$SANDBOX_NAME" \
      nohup ./deploy/scripts/run-terminal.sh >> "$STATE_DIR/nemoclaw-terminal.log" 2>&1 &
    disown
    sleep 3
  fi

  if terminal_up; then
    pass "terminal daemon" "$TERM_HTTP"
  else
    fail "terminal daemon" "not answering at $TERM_HTTP" \
      "TERMINAL_MODE=restricted SANDBOX_NAME=$SANDBOX_NAME make terminal"
  fi
else
  skip "terminal daemon" "TERMINAL_WS_URL unset — feature off"
fi

# ── 4/5. Wake-hook path (openshell forward + hook-relay bridge) ──────────────
if [[ -n "$HOOK_URL" ]]; then
  HOOK_PORT="${HOOK_URL##*:}"; HOOK_PORT="${HOOK_PORT%%/*}"

  probe_wake_hook() {
    CODE=$(http_code -X POST "http://127.0.0.1:${HOOK_PORT}/hooks/wake" -H "Authorization: Bearer doctor-probe")
    RELAY_CODE=$(http_code -X POST "http://${BRIDGE_IP}:${HOOK_PORT}/hooks/wake" -H "Authorization: Bearer doctor-probe")
  }

  probe_wake_hook
  if [[ ( "$CODE" != "401" || "$RELAY_CODE" != "401" ) && "$FIX" -eq 1 ]]; then
    fixing "recover loopback forward, then restart hook-relay"
    # `nemoclaw recover`'s port-in-use check is not interface-aware: if
    # hook-relay (bound to $BRIDGE_IP:$HOOK_PORT) is already up, recover sees
    # the port number taken and silently skips recreating its own loopback
    # forward on 127.0.0.1:$HOOK_PORT — so hook-relay must come down first.
    if command -v lsof >/dev/null 2>&1; then
      RELAY_PID=$(lsof -t -i "${BRIDGE_IP}:${HOOK_PORT}" -sTCP:LISTEN 2>/dev/null || true)
      if [[ -n "$RELAY_PID" ]]; then
        kill "$RELAY_PID" 2>/dev/null || true
        for _ in $(seq 1 10); do
          lsof -i "${BRIDGE_IP}:${HOOK_PORT}" -sTCP:LISTEN >/dev/null 2>&1 || break
          sleep 0.3
        done
      fi
    fi
    nemoclaw "$SANDBOX_NAME" recover >/dev/null 2>&1 || true
    HOOK_RELAY_PORT="$HOOK_PORT" HOOK_RELAY_BIND="$BRIDGE_IP" \
      nohup ./deploy/scripts/hook-relay.py >> "$STATE_DIR/nemoclaw-hook-relay.log" 2>&1 &
    disown
    sleep 2
    probe_wake_hook
  fi

  # (4) openshell's loopback SSH forward → openclaw's hook: bad-token probe
  # must 401. Connection refused = the forward died (it does not survive
  # reboots or openshell restarts).
  if [[ "$CODE" == "401" ]]; then
    pass "sandbox wake-hook (:$HOOK_PORT)" "401 on bad token = auth alive"
  else
    fail "sandbox wake-hook (:$HOOK_PORT)" "expected 401, got '${CODE:-no response}'" \
      "nemoclaw $SANDBOX_NAME recover   (restores the loopback forward)"
  fi
  # (5) hook-relay bridges that loopback forward onto the docker bridge so
  # the containerized Gateway can reach it (Linux only).
  if [[ "$RELAY_CODE" == "401" ]]; then
    pass "hook-relay bridge (:$HOOK_PORT)" "container->host path alive"
  else
    fail "hook-relay bridge (:$HOOK_PORT)" "expected 401, got '${RELAY_CODE:-no response}'" \
      "HOOK_RELAY_PORT=$HOOK_PORT make hook-relay"
  fi
else
  skip "sandbox wake-hook" "OPENCLAW_HOOK_URL unset — webhook wake-up off (cron-only)"
fi

# ── 6. Lab proxy (K8s deployment only, ADR-011) ──────────────────────────────
# The agent-host nginx proxy that carries sandbox MCP/gateway traffic to the
# cluster ingress (deploy/scripts/run-lab-proxy.sh). Gated on LAB_INGRESS_HOST
# in .env — the single-Docker-host dev stack doesn't run it.
LAB_INGRESS_HOST="$(env_get LAB_INGRESS_HOST)"
if [[ -n "$LAB_INGRESS_HOST" ]]; then
  MCP_PROXY_PORT="$(env_get MCP_PROXY_PORT)"; MCP_PROXY_PORT="${MCP_PROXY_PORT:-8004}"
  MCP_INIT='{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-03-26","capabilities":{},"clientInfo":{"name":"doctor","version":"0"}}}'
  MCP_CODE=$(http_code -X POST "http://127.0.0.1:${MCP_PROXY_PORT}/mcp" \
    -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' \
    -d "$MCP_INIT")
  if [[ "$MCP_CODE" == "200" ]]; then
    pass "lab proxy -> cluster MCP (:$MCP_PROXY_PORT)" "initialize OK via $LAB_INGRESS_HOST"
  else
    fail "lab proxy -> cluster MCP (:$MCP_PROXY_PORT)" "MCP initialize got '${MCP_CODE:-no response}'" \
      "LAB_INGRESS_HOST=$LAB_INGRESS_HOST MCP_PROXY_PORT=$MCP_PROXY_PORT deploy/scripts/run-lab-proxy.sh"
  fi
  # Sandbox-side reachability rides ufw — warn if the docker-bridge allow rule
  # is missing (best-effort: ufw needs root, so only check when we can).
  if sudo -n ufw status >/dev/null 2>&1; then
    if sudo -n ufw status | grep -q "$MCP_PROXY_PORT.*172\.16\.0\.0/12"; then
      pass "ufw docker-bridge rule (:$MCP_PROXY_PORT)"
    else
      fail "ufw docker-bridge rule (:$MCP_PROXY_PORT)" "sandbox cannot reach the proxy" \
        "sudo ufw allow from 172.16.0.0/12 to any port $MCP_PROXY_PORT proto tcp"
    fi
  else
    skip "ufw docker-bridge rule" "ufw not checkable without sudo — verify manually"
  fi
else
  skip "lab proxy" "LAB_INGRESS_HOST unset — single-host dev stack"
fi

# ── 7. LLM endpoint (authed — the shared lab endpoint 401s bare probes) ──────
LLM_AUTH=()
[[ -n "$LLM_API_KEY" ]] && LLM_AUTH=(-H "Authorization: Bearer ${LLM_API_KEY}")
if [[ -n "$LLM_BASE_URL" ]]; then
  LLM_CODE="$(http_code "${LLM_AUTH[@]}" "${LLM_BASE_URL%/}/models")"
  case "$LLM_CODE" in
    200) pass "LLM endpoint" "$LLM_BASE_URL" ;;
    401|403) fail "LLM endpoint" "key rejected (HTTP ${LLM_CODE})" \
      "check LLM_API_KEY in .env against the endpoint's real key" ;;
    *) fail "LLM endpoint" "no response from ${LLM_BASE_URL%/}/models" \
      "check the endpoint host is up and LLM_BASE_URL in .env is right" ;;
  esac
else
  skip "LLM endpoint" "LLM_BASE_URL unset in .env"
fi

# ── 8. Inference proxy + model drift (ADR-014) ───────────────────────────────
# The sandbox reaches the LLM via the host nginx proxy (run-inference-proxy.sh);
# LLM_DIRECT=1 means the raw endpoint was baked in instead. Not auto-fixed
# under --fix: the proxy install needs sudo and the self-heal timer runs
# unprivileged (same reason the ufw check is report-only).
if [[ -n "$LLM_BASE_URL" && "${LLM_DIRECT:-0}" != "1" ]]; then
  # nginx service + bind state: the smoke below only proves loopback. A boot
  # race (nginx dies before the bridge IP exists) or a no-op reload (a reload
  # cannot change listen addresses) leaves nginx loopback-serving while the
  # sandbox's bridge hop is connection-refused — the agent then idles with
  # 503s in /tmp/gateway.log. Verify the conf's non-loopback binds are live.
  if ! systemctl is-active --quiet nginx; then
    fail "inference proxy (:$LLM_PROXY_PORT)" "nginx service is not active" \
      "sudo systemctl restart nginx   (boot race: it died before the bridge IP existed)"
  else
    CONF_BINDS="$(grep -E '^[[:space:]]*listen[[:space:]]+' \
      "/etc/nginx/conf.d/nemoclaw-inference-proxy-${LLM_PROXY_PORT}.conf" 2>/dev/null \
      | sed -E 's/^[[:space:]]*listen[[:space:]]+//' | sed -E 's/(:[0-9]+).*//' \
      | grep -vE '^127\.' | sort -u)"
    LISTENING="$(ss -tln 2>/dev/null | awk '{print $4}')"
    BIND_FAIL=""
    for B in $CONF_BINDS; do
      if [[ "$B" == "0.0.0.0" || "$B" == "[::]" ]]; then
        # a wildcard bind covers the sandbox bridge; specific-IP-only does not
        echo "$LISTENING" | grep -qE "(0\.0\.0\.0|\[::\]):${LLM_PROXY_PORT}$" || BIND_FAIL="0.0.0.0:$LLM_PROXY_PORT"
      else
        echo "$LISTENING" | grep -q "^${B//./\.}:${LLM_PROXY_PORT}$" || BIND_FAIL="${B}:${LLM_PROXY_PORT}"
      fi
    done
    if [[ -n "$BIND_FAIL" ]]; then
      fail "inference proxy (:$LLM_PROXY_PORT)" "conf bind ${BIND_FAIL} not live — stale sockets from a no-op reload" \
        "sudo systemctl restart nginx   (the inference watchdog timer does this automatically)"
    fi
  fi
  PROXY_CODE="$(http_code "${LLM_AUTH[@]}" "http://127.0.0.1:${LLM_PROXY_PORT}/v1/models")"
  if [[ "$PROXY_CODE" == "200" ]]; then
    pass "inference proxy (:$LLM_PROXY_PORT)" "-> $LLM_BASE_URL"
  else
    fail "inference proxy (:$LLM_PROXY_PORT)" "/v1/models got '${PROXY_CODE:-no response}'" \
      "deploy/scripts/run-inference-proxy.sh"
  fi
fi
# Model drift: .env is what doctor/bootstrap trust, the sandbox's openclaw.json
# is what the agent actually uses — they diverge when .env is edited without a
# repoint. (Endpoint drift is NOT detectable this way: the sandbox stores a
# routed placeholder baseUrl, so the proxy probe above covers that hop.)
if [[ -n "$LLM_MODEL" ]] && nemoclaw "$SANDBOX_NAME" status >/dev/null 2>&1; then
  OC_JSON="$(mktemp)"
  if nemoclaw "$SANDBOX_NAME" download /sandbox/.openclaw/openclaw.json "$OC_JSON" >/dev/null 2>&1; then
    ACTIVE_MODEL="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["agents"]["defaults"]["model"]["primary"])' \
      "$OC_JSON" 2>/dev/null || true)"
    if [[ -z "$ACTIVE_MODEL" ]]; then
      skip "agent model drift" "could not parse the sandbox config"
    elif [[ "$ACTIVE_MODEL" == *"$LLM_MODEL" ]]; then
      pass "agent model" "$ACTIVE_MODEL"
    else
      fail "agent model" "sandbox runs '$ACTIVE_MODEL', .env says '$LLM_MODEL'" \
        "make repoint-llm"
    fi
  else
    skip "agent model drift" "sandbox config not readable"
  fi
  rm -f "$OC_JSON"
fi

echo
if [[ "$FAILURES" -eq 0 ]]; then
  echo "${GREEN}All checks passed — demo is ready.${RESET}"
else
  if [[ "$FIX" -eq 1 ]]; then
    echo "${RED}${FAILURES} check(s) still failing after auto-fix.${RESET} Needs a human — see docs/TROUBLESHOOTING.md"
  else
    echo "${RED}${FAILURES} check(s) failed.${RESET} Fix the items above, then re-run: make doctor"
    echo "Symptom guide: docs/TROUBLESHOOTING.md"
  fi
  exit 1
fi
