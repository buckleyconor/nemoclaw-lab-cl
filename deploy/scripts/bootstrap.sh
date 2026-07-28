#!/usr/bin/env bash
# First-run setup for the GB10 lab host: clean checkout -> verified-green lab.
#
# The first-run sibling of demo-up.sh, and it delegates to it for the last leg
# rather than reimplementing the daemon-start/preflight logic. Same house rules:
# idempotent, skips whatever is already done, ends on the doctor preflight.
#
#   1. preflight  — docker, nemoclaw CLI, .env with LLM_BASE_URL/LLM_MODEL set
#   2. TERMINAL_BIND — detected from docker0 (loopback is unreachable from the
#                      containerized gateway on Linux, SPEC-EMBEDDED-TERMINAL §4)
#   3. docker compose up -d --build
#   4. onboard the agent sandbox (skipped if one already exists — see below)
#   5. exec demo-up.sh -> host daemons + doctor
#
# Deliberately NOT automated:
#   - your LLM endpoint: set LLM_BASE_URL/LLM_MODEL in .env first, this script
#     will not guess them
#   - anything needing sudo: the ufw rules and `loginctl enable-linger` are
#     PRINTED for you to run, never executed here
#
# Usage: make bootstrap            (or deploy/scripts/bootstrap.sh)
#        make bootstrap FORCE=1    re-onboard over an existing sandbox
set -euo pipefail

cd "$(dirname "$0")/../.."

# shellcheck source=deploy/scripts/lib/envfile.sh
source deploy/scripts/lib/envfile.sh

FORCE="${FORCE:-0}"
[[ "${1:-}" == "--force" ]] && FORCE=1

SANDBOX_NAME="$(env_get SANDBOX_NAME)"; SANDBOX_NAME="${SANDBOX_NAME:-infra-sentinel}"

die() { echo "✗ $*" >&2; exit 1; }

echo "── 1/5 preflight ─────────────────────────────────────────────────"

command -v docker >/dev/null || die "docker not found."
docker info >/dev/null 2>&1 || die "docker daemon not reachable (are you in the 'docker' group?)."
command -v nemoclaw >/dev/null \
  || die "nemoclaw CLI not found. Install: curl -fsSL https://www.nvidia.com/nemoclaw.sh | bash"

if [[ ! -f .env ]]; then
  die ".env not found. Run: cp .env.example .env — then set LLM_BASE_URL and LLM_MODEL."
fi

LLM_BASE_URL="$(env_get LLM_BASE_URL)"
LLM_MODEL="$(env_get LLM_MODEL)"
[[ -n "$LLM_BASE_URL" && "$LLM_BASE_URL" != *YOUR_VLLM_HOST* ]] \
  || die "LLM_BASE_URL is unset or still the .env.example placeholder. Set it in .env."
[[ -n "$LLM_MODEL" ]] || die "LLM_MODEL is unset in .env."

# Fail here rather than 10 minutes later inside `nemoclaw onboard`, which bakes
# this endpoint into the sandbox image.
if ! curl -s -o /dev/null --max-time 5 "${LLM_BASE_URL%/}/models"; then
  die "LLM endpoint ${LLM_BASE_URL%/}/models is not answering. Fix it before onboarding —
    the endpoint is baked into the sandbox at onboard time, not read at runtime."
fi
echo "  ✓ docker, nemoclaw, .env"
echo "  ✓ LLM endpoint  ${LLM_BASE_URL}  (${LLM_MODEL})"

echo
echo "── 2/5 terminal bind address ─────────────────────────────────────"
# host.docker.internal resolves to the docker bridge on Linux, so a loopback
# bind is unreachable from the gateway container (SPEC-EMBEDDED-TERMINAL §4).
BRIDGE_IP="$(ip -4 -o addr show docker0 2>/dev/null | awk '{print $4}' | cut -d/ -f1 | head -1)"
if [[ -n "$BRIDGE_IP" ]]; then
  if env_set_checked TERMINAL_BIND "$BRIDGE_IP"; then
    echo "  ✓ TERMINAL_BIND=${BRIDGE_IP} (docker0)"
  fi
else
  echo "  ! docker0 not found — leaving TERMINAL_BIND alone (defaults to 127.0.0.1,"
  echo "    which the gateway container cannot reach on Linux)."
fi

echo
echo "── 3/5 compose stack ─────────────────────────────────────────────"
docker compose up -d --build

echo
echo "── 4/5 agent sandbox ─────────────────────────────────────────────"
# Onboarding builds an image and replaces the agent's whole config. Never do
# that implicitly to a host that already has a working sandbox.
if nemoclaw "$SANDBOX_NAME" status >/dev/null 2>&1 && [[ "$FORCE" != "1" ]]; then
  echo "Sandbox '${SANDBOX_NAME}' already exists — skipping onboarding."
  echo "Re-onboard (rebuilds the image, resets agent config): make bootstrap FORCE=1"
else
  LLM_BASE_URL="$LLM_BASE_URL" LLM_MODEL="$LLM_MODEL" \
    LLM_API_KEY="$(env_get LLM_API_KEY)" SANDBOX_NAME="$SANDBOX_NAME" \
    ./deploy/scripts/onboard-openclaw.sh
  # onboard-openclaw.sh wrote OPENCLAW_HOOK_* into .env; the gateway needs a
  # restart to read them.
  echo
  echo "Restarting gateway to pick up the new agent wiring..."
  docker compose up -d gateway
fi

# ── Things that need privileges: print, never run ────────────────────────────
COMPOSE_NET="$(docker network inspect "$(basename "$PWD")_default" \
  --format '{{range .IPAM.Config}}{{.Subnet}}{{end}}' 2>/dev/null || true)"
if command -v ufw >/dev/null && systemctl is-active --quiet ufw 2>/dev/null; then
  echo
  echo "── ufw is active — these two rules are required ──────────────────"
  echo "Without them the container→host hops fail SILENTLY: the terminal panel"
  echo "shows disconnected and the wake hook never fires (faults look undetected)."
  echo "Run these yourself (they need sudo):"
  echo
  echo "  sudo ufw allow from ${COMPOSE_NET:-172.23.0.0/16} to ${BRIDGE_IP:-172.17.0.1} port 8005 proto tcp comment 'nemoclaw terminal daemon (ADR-012)'"
  echo "  sudo ufw allow from ${COMPOSE_NET:-172.23.0.0/16} to any port 18790 proto tcp comment 'openclaw wake hook (ADR-011)'"
  echo
  echo "(${COMPOSE_NET:-subnet} is this compose network, detected live — it is not"
  echo " pinned in docker-compose.yaml, so re-check it if the hook ever stops firing.)"
fi

echo
echo "── 5/5 host daemons + preflight ──────────────────────────────────"
echo "Handing off to demo-up.sh..."
echo
exec ./deploy/scripts/demo-up.sh
