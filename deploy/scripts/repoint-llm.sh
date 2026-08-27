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

[[ -n "$LLM_BASE_URL" && "$LLM_BASE_URL" != *YOUR_LLM_HOST* && "$LLM_BASE_URL" != *YOUR_VLLM_HOST* ]] \
  || die "LLM_BASE_URL is unset or still the .env.example placeholder. Set it in .env."
[[ -n "$LLM_MODEL" ]] || die "LLM_MODEL is unset in .env."
[[ -n "$LLM_API_KEY" && "$LLM_API_KEY" != "CHANGE_ME" ]] \
  || die "LLM_API_KEY is unset or still the .env.example placeholder."
command -v nemoclaw >/dev/null || die "nemoclaw CLI not found."
nemoclaw "$SANDBOX_NAME" status >/dev/null 2>&1 \
  || die "sandbox '${SANDBOX_NAME}' not found — nothing to repoint. First run: make bootstrap"

# ── 1. The real endpoint answers with this key and serves this model ─────────
BODY="$(mktemp)"
trap 'rm -f "$BODY"' EXIT
CODE="$(curl -s -o "$BODY" -w '%{http_code}' --max-time 10 \
  -H "Authorization: Bearer ${LLM_API_KEY}" "${LLM_BASE_URL%/}/models" || true)"
case "$CODE" in
  200) : ;;
  401|403) die "endpoint rejected the key (HTTP ${CODE} from ${LLM_BASE_URL%/}/models). Check LLM_API_KEY." ;;
  *) die "endpoint ${LLM_BASE_URL%/}/models is not answering (got '${CODE:-nothing}')." ;;
esac
grep -qF "$LLM_MODEL" "$BODY" \
  || echo "! endpoint /models does not list '${LLM_MODEL}' — continuing, but check the model id." >&2
echo "✓ endpoint ${LLM_BASE_URL} (authed)"

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
