#!/usr/bin/env bash
# One command to a working demo: brings up every moving part the lab needs,
# in order, then runs the doctor preflight to prove it.
#
#   1. docker compose stack (gateway/orchestrator/simulator/mcp-tools)
#   2. terminal daemon (restricted console, ADR-013) — host process
#   3. hook-relay (ADR-011 wake-hook bridge) — host process
#   4. deploy/scripts/doctor.sh
#
# Host processes are started with nohup and logged under ~/.local/state/, and
# are skipped if already answering — safe to re-run any time. This does NOT
# onboard the agent sandbox (one-time setup: deploy/scripts/onboard-openclaw.sh).
#
# Usage: make demo-up   (or deploy/scripts/demo-up.sh)
set -euo pipefail

cd "$(dirname "$0")/../.."

if [[ ! -f .env ]]; then
  echo "No .env found — copy .env.example to .env and fill it in first." >&2
  exit 1
fi

# shellcheck source=deploy/scripts/lib/envfile.sh
source deploy/scripts/lib/envfile.sh   # for bridge_ip
BRIDGE_IP="$(bridge_ip)"

# `|| true`: a missing var must yield "", not kill the script under set -e.
env_get() { { grep -E "^$1=" .env 2>/dev/null || true; } | head -1 | cut -d= -f2-; }
SANDBOX_NAME="$(env_get SANDBOX_NAME)"; SANDBOX_NAME="${SANDBOX_NAME:-infra-sentinel}"
TERMINAL_BIND="$(env_get TERMINAL_BIND)"; TERMINAL_BIND="${TERMINAL_BIND:-127.0.0.1}"
TERMINAL_WS_URL="$(env_get TERMINAL_WS_URL)"
HOOK_URL="$(env_get OPENCLAW_HOOK_URL)"

STATE_DIR="${HOME}/.local/state"
mkdir -p "$STATE_DIR"

alive() { curl -s -o /dev/null --max-time 3 "$1"; }

echo "── 1/4 docker compose stack ──────────────────────────────────────"
docker compose up -d

echo
echo "── 2/4 terminal daemon (restricted console) ──────────────────────"
if [[ -z "$TERMINAL_WS_URL" ]]; then
  echo "TERMINAL_WS_URL unset in .env — terminal feature off, skipping."
elif alive "http://${TERMINAL_BIND}:8005/healthz"; then
  echo "Already running on ${TERMINAL_BIND}:8005."
else
  TERMINAL_MODE=restricted SANDBOX_NAME="$SANDBOX_NAME" \
    nohup ./deploy/scripts/run-terminal.sh \
    >> "${STATE_DIR}/nemoclaw-terminal.log" 2>&1 &
  echo "Started (sandbox: ${SANDBOX_NAME}, log: ${STATE_DIR}/nemoclaw-terminal.log)."
fi

echo
echo "── 3/4 hook-relay (wake-hook bridge) ─────────────────────────────"
if [[ -z "$HOOK_URL" ]]; then
  echo "OPENCLAW_HOOK_URL unset in .env — webhook wake-up off, skipping."
else
  HOOK_PORT="${HOOK_URL##*:}"; HOOK_PORT="${HOOK_PORT%%/*}"
  if alive "http://${BRIDGE_IP}:${HOOK_PORT}/healthz" \
     || curl -s -o /dev/null --max-time 3 -X POST "http://${BRIDGE_IP}:${HOOK_PORT}/hooks/wake"; then
    echo "Already running on ${BRIDGE_IP}:${HOOK_PORT}."
  else
    HOOK_RELAY_PORT="$HOOK_PORT" HOOK_RELAY_BIND="$BRIDGE_IP" \
      nohup ./deploy/scripts/hook-relay.py \
      >> "${STATE_DIR}/nemoclaw-hook-relay.log" 2>&1 &
    echo "Started (port: ${HOOK_PORT}, log: ${STATE_DIR}/nemoclaw-hook-relay.log)."
  fi
fi

echo
echo "── 4/4 preflight ─────────────────────────────────────────────────"
sleep 2  # give freshly-started daemons a beat to bind
exec ./deploy/scripts/doctor.sh
