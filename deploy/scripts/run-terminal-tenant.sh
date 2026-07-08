#!/usr/bin/env bash
# Run one tenant's restricted operator-console terminal daemon (ADR-013).
#
# M9 (Kubernetes, 30 tenants): each tenant gets its own OpenShell sandbox on
# this external agent host (ADR-011's existing "peer host process" pattern,
# scaled out per-tenant) AND its own terminal daemon process here, bound to
# TERMINAL_MODE=restricted so the PTY runs services.terminal.console's
# six-item edit menu instead of a login shell. One process per tenant keeps a
# daemon crash/restart scoped to that tenant only, and needs no new
# token-store/multiplexing code — it's exactly deploy/scripts/run-terminal.sh,
# parameterized.
#
# Usage: deploy/scripts/run-terminal-tenant.sh <tenant> <sandbox-name> <port>
#   tenant        short id used only for the persisted token file, e.g. "acme"
#   sandbox-name  the nemoclaw/openshell sandbox this tenant's console targets
#   port          host port this tenant's daemon binds (must be unique per tenant)
set -euo pipefail

if [[ $# -ne 3 ]]; then
  echo "Usage: $0 <tenant> <sandbox-name> <port>" >&2
  exit 1
fi

TENANT="$1"
SANDBOX_NAME="$2"
PORT="$3"

cd "$(dirname "$0")/../.."

STATE_DIR="${HOME}/.local/state"
mkdir -p "$STATE_DIR"
TOKEN_FILE="${STATE_DIR}/nemoclaw-terminal-${TENANT}.token"

BIND="${TERMINAL_BIND:-127.0.0.1}"

if [[ -f "$TOKEN_FILE" ]]; then
  TERMINAL_TOKEN="$(cat "$TOKEN_FILE")"
else
  TERMINAL_TOKEN="$(openssl rand -hex 24)"
  umask 077
  echo -n "$TERMINAL_TOKEN" > "$TOKEN_FILE"
fi

echo "Tenant:  ${TENANT}"
echo "Sandbox: ${SANDBOX_NAME}"
echo
echo "Set these in ${TENANT}'s Helm values overlay (or --set at deploy time):"
echo
echo "  --set terminal.wsUrl=ws://<this-host>:${PORT}/ws"
echo "  --set terminal.token=${TERMINAL_TOKEN}"
echo

export TERMINAL_TOKEN
export TERMINAL_MODE=restricted
export SANDBOX_NAME
export TERMINAL_WORKSPACE_DIR="${HOME}/nemoclaw-tenants/${TENANT}/work"
exec uv run uvicorn --factory services.terminal.main:create_app \
  --host "${BIND}" --port "${PORT}"
