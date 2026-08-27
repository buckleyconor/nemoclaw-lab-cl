#!/usr/bin/env bash
# Run the embedded-terminal daemon (ADR-012) on the lab host.
#
# Backs the dashboard's "Agent Configuration Terminal" panel with a real PTY
# shell, so it must run on the host — where the nemoclaw/openclaw/openshell
# CLIs, their state, and the sandbox Docker daemon live — never in a container.
#
# Security (docs/SPEC-EMBEDDED-TERMINAL.md §7): binds a host-local address
# only, and every WS handshake must carry the TERMINAL_TOKEN bearer token,
# which only the Gateway proxy holds. Port 8005 must never be exposed off-host.
#
# Bind address: 127.0.0.1 by default. When the Gateway runs in a container
# (docker compose), host.docker.internal resolves to the docker bridge IP on
# Linux, so a loopback bind is unreachable from it — set TERMINAL_BIND to the
# bridge address (typically 172.17.0.1: `ip -4 addr show docker0`). That is
# still host-internal; never bind a LAN-facing interface.
set -euo pipefail

# Resolve before the cd — afterwards a relative $0 no longer points here.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${SCRIPT_DIR}/../.."

# shellcheck source=deploy/scripts/lib/envfile.sh
source "${SCRIPT_DIR}/lib/envfile.sh"

# Pick up TERMINAL_* from .env when not already set in the environment, so a
# `make terminal` restart reuses the token/bind the Gateway already knows.
if [[ -f .env ]]; then
  while IFS='=' read -r key value; do
    if [[ -z "${!key:-}" ]]; then
      export "$key"="$value"
    fi
  done < <(grep -E '^TERMINAL_(TOKEN|PORT|BIND)=' .env)
fi

PORT="${TERMINAL_PORT:-8005}"
BIND="${TERMINAL_BIND:-127.0.0.1}"

WS_URL="ws://host.docker.internal:${PORT}/ws"

# Write the Gateway's half of the handshake into .env rather than printing it
# for the operator to paste — a mistyped token presents as a silently
# disconnected terminal panel, with nothing in the logs pointing at .env.
if [[ -z "${TERMINAL_TOKEN:-}" ]]; then
  TERMINAL_TOKEN="$(openssl rand -hex 24)"
  echo "Generated a TERMINAL_TOKEN and wrote it to .env:"
  env_upsert TERMINAL_WS_URL "$WS_URL"
  env_upsert TERMINAL_TOKEN "$TERMINAL_TOKEN"
  echo "  TERMINAL_WS_URL=${WS_URL}"
  echo "  TERMINAL_TOKEN=${TERMINAL_TOKEN}"
  echo
  echo "Restart the gateway so its proxy picks them up:  docker compose up -d gateway"
  echo
else
  # Token already known (from .env or the environment). Only fill in the URL if
  # it is missing; never overwrite an operator-chosen one.
  env_set_checked TERMINAL_WS_URL "$WS_URL" || true
fi

command -v uv >/dev/null || {
  echo "uv not found — the terminal daemon runs via 'uv run uvicorn'. Install it and retry:" >&2
  echo "  curl -LsSf https://astral.sh/uv/install.sh | sh" >&2
  exit 1
}

export TERMINAL_TOKEN
exec uv run uvicorn --factory services.terminal.main:create_app \
  --host "${BIND}" --port "${PORT}"
