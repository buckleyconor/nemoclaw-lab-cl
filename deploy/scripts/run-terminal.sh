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

cd "$(dirname "$0")/../.."

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

if [[ -z "${TERMINAL_TOKEN:-}" ]]; then
  TERMINAL_TOKEN="$(openssl rand -hex 24)"
  echo "Generated a TERMINAL_TOKEN. Add these lines to .env, then restart the"
  echo "gateway (docker compose up -d gateway) so its proxy can authenticate:"
  echo
  echo "  TERMINAL_WS_URL=ws://host.docker.internal:${PORT}/ws"
  echo "  TERMINAL_TOKEN=${TERMINAL_TOKEN}"
  echo
fi

export TERMINAL_TOKEN
exec uv run uvicorn --factory services.terminal.main:create_app \
  --host "${BIND}" --port "${PORT}"
