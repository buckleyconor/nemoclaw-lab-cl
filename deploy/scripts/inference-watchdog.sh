#!/usr/bin/env bash
# Inference-proxy watchdog (ADR-014) — invoked by
# deploy/systemd/nemoclaw-inference-watchdog.timer (root, every minute).
#
# Recovers the two nginx-side failure modes that leave the lab agent idle
# with LLM 503s ("inference service unavailable") in the sandbox's
# /tmp/gateway.log:
#
#   1. nginx not running — the boot race: nginx starts before Docker assigns
#      the bridge address the conf binds, the bind fails, and the service
#      stays dead (it does not retry on its own) until something restarts it.
#   2. nginx running stale sockets — a reload cannot change listen addresses
#      (EADDRINUSE), so after a re-render the previous conf keeps serving and
#      the sandbox's bridge hop dies with connection refused.
#
# A full `systemctl restart nginx` is the only action that fixes both, and
# only those trigger a restart: a 5xx on the authed smoke test alone (the
# shared LLM endpoint is down, or the key was rotated) is an EXTERNAL problem
# that a restart would not cure — restarting every minute would churn
# in-flight SSE streams for no gain, so that case is logged and left alone.
#
# All output goes to the journal: journalctl -u nemoclaw-inference-watchdog
set -uo pipefail

cd "$(dirname "$0")/../.."

# shellcheck source=deploy/scripts/lib/envfile.sh
source deploy/scripts/lib/envfile.sh
LLM_PROXY_PORT="$(env_get LLM_PROXY_PORT)"; LLM_PROXY_PORT="${LLM_PROXY_PORT:-18100}"
LLM_API_KEY="$(env_get LLM_API_KEY)"

log() { echo "inference-watchdog: $*"; }

smoke_code() {
  curl -s -o /dev/null -w '%{http_code}' --max-time 10 \
    -H "Authorization: Bearer ${LLM_API_KEY}" \
    "http://127.0.0.1:${LLM_PROXY_PORT}/v1/models" 2>/dev/null || true
}

# Intended non-loopback binds from the rendered conf — the addresses the
# sandbox dials (host.openshell.internal = the host IP on its bridge).
intended_binds() {
  local conf="/etc/nginx/conf.d/nemoclaw-inference-proxy-${LLM_PROXY_PORT}.conf"
  [[ -r "$conf" ]] || return 1
  grep -E '^[[:space:]]*listen[[:space:]]+' "$conf" \
    | sed -E 's/^[[:space:]]*listen[[:space:]]+//' | sed -E 's/(:[0-9]+).*//' \
    | grep -vE '^127\.' | sort -u
}

# The conf's non-loopback binds must actually be listening. Catches the
# stale-socket state where nginx runs the previous conf (e.g. bound to a
# specific bridge IP) while the conf on disk says 0.0.0.0 — loopback probes
# stay green while the sandbox's bridge hop is connection-refused.
binds_ok() {
  local addrs addr listening
  addrs="$(intended_binds)" || return 0   # no readable conf — smoke covers it
  [[ -z "$addrs" ]] && return 0
  listening="$(ss -tln 2>/dev/null | awk '{print $4}')"
  for addr in $addrs; do
    case "$addr" in
      0.0.0.0|'\[::\]')
        # wildcard must be bound — a specific-IP-only bind does not cover the
        # sandbox bridge, even though the port answers on other interfaces
        echo "$listening" | grep -qE "(0\.0\.0\.0|\[::\]):${LLM_PROXY_PORT}$" || return 1
        ;;
      *)
        echo "$listening" | grep -q "^${addr//./\.}:${LLM_PROXY_PORT}$" || return 1
        ;;
    esac
  done
  return 0
}

restart_and_verify() {
  local reason="$1" code
  log "unhealthy (${reason}) — restarting nginx"
  systemctl restart nginx
  sleep 2
  if binds_ok; then
    code="$(smoke_code)"
    if [[ "$code" == "200" ]]; then
      log "recovered: conf binds active, authed smoke 200"
      exit 0
    fi
    log "restarted; conf binds active but smoke returned '${code:-no response}' — upstream LLM endpoint may be down (external, not restartable)"
  else
    log "restarted but the conf's bind(s) are still missing — check: sudo nginx -t && journalctl -u nginx -e"
  fi
  exit 1
}

# 1. Service down (includes the boot-race failure state).
if ! systemctl is-active --quiet nginx; then
  restart_and_verify "nginx service not active — boot race or crash"
fi

# 2. Conf's non-loopback bind missing (stale-socket reload no-op).
if ! binds_ok; then
  restart_and_verify "conf bind missing — nginx running stale sockets from a no-op reload"
fi

# 3. Up + bound: verify the upstream hop. 200 = fully healthy.
code="$(smoke_code)"
if [[ "$code" == "200" ]]; then
  exit 0
fi

# 4. Up + bound but not 200: external problem (endpoint down / key rejected).
# Log loudly for `make doctor` and the journal; do NOT restart (would not
# help, and would churn live streams every minute).
log "WARNING: nginx up, :${LLM_PROXY_PORT} bound, but authed smoke returned '${code:-no response}' — LLM endpoint/key problem, not a proxy problem (run: make doctor)"
exit 0
