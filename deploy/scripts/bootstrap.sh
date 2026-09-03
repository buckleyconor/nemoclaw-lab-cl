#!/usr/bin/env bash
# First-run setup for the lab host (reference targets: Ubuntu 24.04 x86_64 VM,
# NVIDIA GB10): clean checkout -> verified-green lab.
#
# The first-run sibling of demo-up.sh, and it delegates to it for the last leg
# rather than reimplementing the daemon-start/preflight logic. Same house rules:
# idempotent, skips whatever is already done, ends on the doctor preflight.
#
#   1. preflight  — docker (+compose plugin), nemoclaw CLI, .env with
#                   LLM_BASE_URL/LLM_MODEL/LLM_API_KEY set, inference proxy live
#   2. TERMINAL_BIND — detected from docker0 (loopback is unreachable from the
#                      containerized gateway on Linux, SPEC-EMBEDDED-TERMINAL §4)
#   3. docker compose up -d --build
#   4. onboard the agent sandbox (skipped if one already exists — see below)
#   5. exec demo-up.sh -> host daemons + doctor
#
# Deliberately NOT automated:
#   - your LLM endpoint: set LLM_BASE_URL/LLM_MODEL in .env first, this script
#     will not guess them
#   - anything needing sudo: the ufw rules and the self-heal layer
#     (install-selfheal) are PRINTED for you to run, never executed here
#     (demo-up additionally offers the self-heal install with one prompt
#     when it runs interactively)
#
# Usage: make bootstrap            (or deploy/scripts/bootstrap.sh)
#        make bootstrap FORCE=1    re-onboard over an existing sandbox
set -euo pipefail

cd "$(dirname "$0")/../.."

# shellcheck source=deploy/scripts/lib/envfile.sh
source deploy/scripts/lib/envfile.sh
# shellcheck source=deploy/scripts/lib/nemoclaw.sh
source deploy/scripts/lib/nemoclaw.sh

FORCE="${FORCE:-0}"
[[ "${1:-}" == "--force" ]] && FORCE=1

SANDBOX_NAME="$(env_get SANDBOX_NAME)"; SANDBOX_NAME="${SANDBOX_NAME:-infra-sentinel}"

die() { echo "✗ $*" >&2; exit 1; }

echo "── 1/5 preflight ─────────────────────────────────────────────────"

command -v docker >/dev/null || die "docker not found."
docker info >/dev/null 2>&1 || die "docker daemon not reachable (are you in the 'docker' group?)."
docker compose version >/dev/null 2>&1 \
  || die "docker compose plugin not found. Install: sudo apt-get install -y docker-compose-plugin"
command -v nemoclaw >/dev/null \
  || die "nemoclaw CLI not found. Install: curl -fsSL https://www.nvidia.com/nemoclaw.sh | bash"
# Load-bearing for hook-relay / doctor / token generation — stock Ubuntu has
# them, but a minimal VM image may not.
for tool in python3 openssl curl; do
  command -v "$tool" >/dev/null || die "$tool not found. Install: sudo apt-get install -y $tool"
done
# uv runs the terminal daemon (run-terminal.sh execs `uv run uvicorn`). Not
# fatal — the terminal is an optional feature — but finding out here beats
# finding out when the panel silently never connects.
command -v uv >/dev/null \
  || echo "  ! uv not found — the embedded terminal (ADR-012) will not start. Install: curl -LsSf https://astral.sh/uv/install.sh | sh"

if [[ ! -f .env ]]; then
  die ".env not found. Run: cp .env.example .env — then set LLM_BASE_URL and LLM_MODEL."
fi

LLM_BASE_URL="$(env_get LLM_BASE_URL)"
LLM_MODEL="$(env_get LLM_MODEL)"
LLM_API_KEY="$(env_get LLM_API_KEY)"
LLM_PROXY_PORT="$(env_get LLM_PROXY_PORT)"; LLM_PROXY_PORT="${LLM_PROXY_PORT:-18100}"
LLM_DIRECT="$(env_get LLM_DIRECT)"
[[ -n "$LLM_BASE_URL" && "$LLM_BASE_URL" != *YOUR_LLM_HOST* && "$LLM_BASE_URL" != *YOUR_VLLM_HOST* ]] \
  || die "LLM_BASE_URL is unset or still the .env.example placeholder. Set it in .env."
[[ -n "$LLM_MODEL" ]] || die "LLM_MODEL is unset in .env."
[[ -n "$LLM_API_KEY" && "$LLM_API_KEY" != "CHANGE_ME" ]] \
  || die "LLM_API_KEY is unset or still the .env.example placeholder — the lab endpoint needs a real key."

# Fail here rather than 10 minutes later inside `nemoclaw onboard`, which bakes
# this endpoint into the sandbox image. Authed probe: a bare one would pass on
# any HTTP noise (or 401 on a real-auth endpoint) and hide a bad key.
LLM_CODE="$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 \
  -H "Authorization: Bearer ${LLM_API_KEY}" "${LLM_BASE_URL%/}/models" || true)"
case "$LLM_CODE" in
  200) : ;;
  401|403) die "LLM endpoint rejected the key (HTTP ${LLM_CODE} from ${LLM_BASE_URL%/}/models). Check LLM_API_KEY in .env." ;;
  *) die "LLM endpoint ${LLM_BASE_URL%/}/models is not answering (got '${LLM_CODE:-nothing}'). Fix it before onboarding —
    the endpoint is baked into the sandbox at onboard time, not read at runtime." ;;
esac

# The sandbox reaches the LLM through the host inference proxy (ADR-014);
# onboarding bakes that URL in, so the proxy must be live first. On a genuinely
# fresh host it never is — so bring it up rather than sending the operator away
# and making them re-run the whole script (same offer-once posture demo-up.sh
# uses for the self-heal install).
proxy_code() {
  curl -s -o /dev/null -w '%{http_code}' --max-time 5 \
    -H "Authorization: Bearer ${LLM_API_KEY}" \
    "http://127.0.0.1:${LLM_PROXY_PORT}/v1/models" || true
}
if [[ "${LLM_DIRECT:-0}" != "1" ]]; then
  # /usr/sbin is not on every user's PATH even when the binary is there
  # (sudo's secure_path has it) — accept either, as run-inference-proxy.sh does.
  command -v nginx >/dev/null || [[ -x /usr/sbin/nginx ]] \
    || die "nginx not found — it carries the agent's LLM route (ADR-014).
    Install: sudo apt-get install -y nginx     (or set LLM_DIRECT=1 for a genuinely public endpoint)"

  PROXY_CODE="$(proxy_code)"
  if [[ "$PROXY_CODE" != "200" ]] && [[ -t 0 ]]; then
    echo "  ! inference proxy on :${LLM_PROXY_PORT} is not answering (got '${PROXY_CODE:-nothing}')."
    echo "    It must be live before onboarding — the URL is baked into the sandbox."
    read -r -p "    Bring it up now with run-inference-proxy.sh? (one sudo prompt) [Y/n] " REPLY
    case "${REPLY:-Y}" in
      [Nn]*) : ;;
      *) ./deploy/scripts/run-inference-proxy.sh || true   # it prints its own
         PROXY_CODE="$(proxy_code)" ;;                      # diagnosis; die below
    esac
  fi
  [[ "$PROXY_CODE" == "200" ]] \
    || die "inference proxy on :${LLM_PROXY_PORT} is not answering (got '${PROXY_CODE:-nothing}'). Bring it up first:
    deploy/scripts/run-inference-proxy.sh"
fi
# The sandbox image is built FROM the managed OpenClaw image for the CLI
# release driving the onboard. Surface the pairing here — a skew fails ~10
# minutes into the image build with a base_only_image / exit-127 symptom that
# reads like a code bug (docs/TROUBLESHOOTING.md, "onboarding contract walls").
BOOT_CLI_VERSION="$(cli_version)"
[[ -n "$BOOT_CLI_VERSION" ]] \
  || echo "  ! could not parse 'nemoclaw --version'; the sandbox base image will fall back to
    ${NEMOCLAW_SANDBOX_BASE_FALLBACK}. Set SANDBOX_BASE if onboarding fails on the base image."

echo "  ✓ docker (+compose), nemoclaw${BOOT_CLI_VERSION:+ v$BOOT_CLI_VERSION}, .env"
echo "  ✓ LLM endpoint  ${LLM_BASE_URL}  (${LLM_MODEL})"
[[ "${LLM_DIRECT:-0}" == "1" ]] || echo "  ✓ inference proxy  :${LLM_PROXY_PORT}"
echo "  ✓ sandbox base   $(sandbox_base)"

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
  LLM_BASE_URL="$LLM_BASE_URL" LLM_MODEL="$LLM_MODEL" LLM_API_KEY="$LLM_API_KEY" \
    LLM_PROXY_PORT="$LLM_PROXY_PORT" LLM_DIRECT="${LLM_DIRECT:-0}" \
    SANDBOX_NAME="$SANDBOX_NAME" \
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
  echo "── ufw is active — these rules are required ──────────────────────"
  echo "Without them the container→host hops fail SILENTLY: the terminal panel"
  echo "shows disconnected, the wake hook never fires (faults look undetected),"
  echo "and the agent cannot reach the LLM. Run these yourself (they need sudo):"
  echo
  echo "  sudo ufw allow from ${COMPOSE_NET:-172.28.100.0/24} to ${BRIDGE_IP:-172.17.0.1} port 8005 proto tcp comment 'nemoclaw terminal daemon (ADR-012)'"
  BOOT_HOOK_URL="$(env_get OPENCLAW_HOOK_URL)"
  BOOT_HOOK_PORT="${BOOT_HOOK_URL##*:}"; BOOT_HOOK_PORT="${BOOT_HOOK_PORT%%/*}"
  echo "  sudo ufw allow from ${COMPOSE_NET:-172.28.100.0/24} to any port ${BOOT_HOOK_PORT:-18790} proto tcp comment 'openclaw wake hook (ADR-011)'"
  echo "  sudo ufw allow from 172.16.0.0/12 to any port ${LLM_PROXY_PORT} proto tcp comment 'nemoclaw inference proxy (ADR-014)'"
  echo
  echo "(${COMPOSE_NET:-172.28.100.0/24} is this compose network. It is pinned to"
  echo " 172.28.100.0/24 in docker-compose.yaml, but the value above is the one"
  echo " detected live — re-check it if you changed the pin and the hook stops firing.)"
fi

if ! systemctl is-enabled --quiet nemoclaw-doctor.timer 2>/dev/null; then
  echo "── self-heal layer (recommended) ──────────────────────────────────"
  echo "Makes reboots self-heal instead of silently dying: inference"
  echo "watchdog (60s) + doctor --fix timer (5 min) + terminal/relay"
  echo "systemd units. One sudo prompt:"
  echo
  echo "  sudo make install-selfheal"
  echo
fi

echo
echo "── 5/5 host daemons + preflight ──────────────────────────────────"
echo "Handing off to demo-up.sh..."
echo
exec ./deploy/scripts/demo-up.sh
