#!/usr/bin/env bash
# Repoint a LIVE agent sandbox at the LLM_* values in .env — no rebuild (ADR-014).
#
# Onboarding bakes the endpoint URL into the sandbox, but the URL it bakes is
# the host inference proxy's (stable): moving to a new lab endpoint is a proxy
# re-render + reload, and only a model-id change needs `nemoclaw inference set`.
# This script does both, verifies each hop, and is safe to re-run (idempotent).
#
#   1. authed probe of the real endpoint     GET $LLM_BASE_URL/models   -> 200
#   2. re-render the inference proxy         run-inference-proxy.sh (no-op if unchanged)
#   3. authed probe through the proxy        GET 127.0.0.1:$LLM_PROXY_PORT/v1/models -> 200
#   4. nemoclaw inference set                sync model/key into the sandbox
#   5. read back openclaw.json               confirm the model actually changed
#
# Usage: make repoint-llm      (after editing LLM_* in .env)
# Fallback if anything here fails: make bootstrap FORCE=1  (full re-onboard)
#
# Environment (env wins over .env):
#   LLM_BASE_URL / LLM_MODEL / LLM_API_KEY  (required)
#   SANDBOX_NAME    (default: infra-sentinel)
#   LLM_PROXY_PORT  (default: 18100)
set -euo pipefail

cd "$(dirname "$0")/../.."

# shellcheck source=deploy/scripts/lib/envfile.sh
source deploy/scripts/lib/envfile.sh

die() { echo "✗ $*" >&2; exit 1; }

LLM_BASE_URL="${LLM_BASE_URL:-$(env_get LLM_BASE_URL)}"
LLM_MODEL="${LLM_MODEL:-$(env_get LLM_MODEL)}"
LLM_API_KEY="${LLM_API_KEY:-$(env_get LLM_API_KEY)}"
SANDBOX_NAME="${SANDBOX_NAME:-$(env_get SANDBOX_NAME)}"; SANDBOX_NAME="${SANDBOX_NAME:-infra-sentinel}"
LLM_PROXY_PORT="${LLM_PROXY_PORT:-$(env_get LLM_PROXY_PORT)}"; LLM_PROXY_PORT="${LLM_PROXY_PORT:-18100}"

# The CLI must target the gateway `nemoclaw onboard` created. Left unset, it
# targets the DEFAULT gateway on :8080 — which can answer, but against a DB that
# never held this sandbox, so every lookup reads as "not found" and the operator
# is sent off to re-onboard a healthy sandbox (2026-08-28 incident; doctor.sh
# exports the same thing for the same reason).
_gateway_port="$(env_get NEMOCLAW_GATEWAY_PORT)"
if [[ -n "$_gateway_port" ]]; then export NEMOCLAW_GATEWAY_PORT="$_gateway_port"; fi

[[ -n "$LLM_BASE_URL" && "$LLM_BASE_URL" != *YOUR_LLM_HOST* && "$LLM_BASE_URL" != *YOUR_VLLM_HOST* ]] \
  || die "LLM_BASE_URL is unset or still the .env.example placeholder. Set it in .env."
[[ -n "$LLM_MODEL" ]] || die "LLM_MODEL is unset in .env."
[[ -n "$LLM_API_KEY" && "$LLM_API_KEY" != "CHANGE_ME" ]] \
  || die "LLM_API_KEY is unset or still the .env.example placeholder."
command -v nemoclaw >/dev/null || die "nemoclaw CLI not found."
# Registration, NOT health. `nemoclaw <name> status` exits non-zero whenever the
# agent delivery chain can't be proven — a stale host forward is enough — and
# that is a degraded-but-present sandbox. The old guard read that exit code as
# "not found" and told the operator to run `make bootstrap FORCE=1`, a full
# re-onboard, for a sandbox that only needed its forward healed. `nemoclaw list`
# reports the registry and exits 0 regardless of chain health, so it answers the
# question this guard actually asks: is there a sandbox here to repoint at all?
nemoclaw list 2>/dev/null | grep -qE "^[[:space:]]*${SANDBOX_NAME}[[:space:]]*\*" \
  || die "sandbox '${SANDBOX_NAME}' is not registered — nothing to repoint. First run: make bootstrap"
# A degraded delivery chain does not block `inference set`, but it will still
# stop the agent reporting anything, so name the actual fix rather than leaving
# the operator to guess that the re-onboard is the only lever.
if ! nemoclaw "$SANDBOX_NAME" status >/dev/null 2>&1; then
  echo "! '${SANDBOX_NAME}' is registered, but its agent delivery chain is not currently proven." >&2
  echo "  Repoint will still apply. If the dashboard stays unresponsive afterwards:" >&2
  echo "    nemoclaw ${SANDBOX_NAME} recover" >&2
fi

# ── 1. The real endpoint answers with this key and serves this model ─────────
# Gateways disagree on which header carries the SAME key: plain vLLM/OpenAI
# reads `Authorization: Bearer`, Kong-style ones read only `x-api-key` and
# answer a Bearer request with 401 "No API key found in request". Probe both
# instead of making the operator declare which — this hop talks to the endpoint
# directly (unlike the inference proxy, which now translates the style), and a
# wrong guess otherwise dies here, before the sandbox is touched, reading
# exactly like a bad key. This is the probe's only job; the header style is not
# a property of the key.
BODY="$(mktemp)"
trap 'rm -f "$BODY"' EXIT
probe_models() { curl -s -o "$BODY" -w '%{http_code}' --max-time 10 -H "$1" "${LLM_BASE_URL%/}/models" || true; }
CODE="$(probe_models "Authorization: Bearer ${LLM_API_KEY}")"
AUTH_STYLE="Authorization: Bearer"
if [[ "$CODE" == "401" || "$CODE" == "403" ]]; then
  CODE="$(probe_models "x-api-key: ${LLM_API_KEY}")"
  AUTH_STYLE="x-api-key"
fi
case "$CODE" in
  200) : ;;
  401|403) die "endpoint rejected the key (HTTP ${CODE} from ${LLM_BASE_URL%/}/models; tried both Authorization: Bearer and x-api-key). Check LLM_API_KEY." ;;
  *) die "endpoint ${LLM_BASE_URL%/}/models is not answering (got '${CODE:-nothing}')." ;;
esac
# Hard stop, not a warning: this command exists to point the live agent at
# these LLM settings, and a model id the endpoint does not serve is a broken
# repoint, not a cosmetic one. The agent bakes the id and then 404s on every
# single call — while lab-health keeps reporting the LLM route green, because
# its probe only treats >=500 as a failure. Seen in the wild: a model id that
# lost its `NVIDIA-` prefix and gained a capitalised namespace produced a dead
# agent that looked healthy.
grep -qF "$LLM_MODEL" "$BODY" \
  || die "endpoint ${LLM_BASE_URL%/}/models does not serve LLM_MODEL='${LLM_MODEL}'. The sandbox would 404 on every LLM call. Check the id against what /models actually returns (exact string — namespace case and prefixes both matter)."
echo "✓ endpoint ${LLM_BASE_URL} (authed via ${AUTH_STYLE})"

# ── 2+3. Proxy carries the new endpoint (re-render is a no-op if unchanged) ──
LLM_BASE_URL="$LLM_BASE_URL" LLM_PROXY_PORT="$LLM_PROXY_PORT" \
  LLM_MODEL="$LLM_MODEL" LLM_API_KEY="$LLM_API_KEY" \
  ./deploy/scripts/run-inference-proxy.sh

# ── 4. Sync the sandbox's model + key ────────────────────────────────────────
# Try the clean registration first — host.openshell.internal passed the SSRF
# guard without flags at onboard on the reference host; --no-verify is only a
# fallback for CLI versions that still balk at the alias.
SANDBOX_URL="http://host.openshell.internal:${LLM_PROXY_PORT}/v1"
inference_set() {
  COMPATIBLE_API_KEY="$LLM_API_KEY" nemoclaw inference set \
    --provider compatible-endpoint \
    --model "$LLM_MODEL" \
    --endpoint-url "$SANDBOX_URL" \
    --credential-env COMPATIBLE_API_KEY \
    --inference-api openai-completions \
    --sandbox "$SANDBOX_NAME" \
    "$@"
}
if ! inference_set; then
  echo "! inference set failed — retrying with --no-verify (skips the CLI's reachability probe)." >&2
  inference_set --no-verify || die "inference set failed even with --no-verify. Fallback: make bootstrap FORCE=1"
fi

# ── 5. Read back what the sandbox will actually use ──────────────────────────
OC_JSON="$(mktemp)"
trap 'rm -f "$BODY" "$OC_JSON"' EXIT
nemoclaw "$SANDBOX_NAME" download /sandbox/.openclaw/openclaw.json "$OC_JSON" >/dev/null 2>&1 \
  || die "could not read the sandbox config back to verify. Fallback: make bootstrap FORCE=1"
ACTIVE_MODEL="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["agents"]["defaults"]["model"]["primary"])' \
  "$OC_JSON" 2>/dev/null || true)"
[[ "$ACTIVE_MODEL" == *"$LLM_MODEL" ]] \
  || die "sandbox still reports model '${ACTIVE_MODEL:-unknown}', expected '*${LLM_MODEL}'. Fallback: make bootstrap FORCE=1"

echo
echo "✓ repointed '${SANDBOX_NAME}': ${ACTIVE_MODEL} via ${SANDBOX_URL} -> ${LLM_BASE_URL}"
echo "  (key rotation note: if the agent starts failing auth, 'inference set' did"
echo "   not re-persist the new key — re-onboard with: make bootstrap FORCE=1)"
