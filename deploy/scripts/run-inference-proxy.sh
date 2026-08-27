#!/usr/bin/env bash
# Render + install the agent-host inference proxy (ADR-014).
#
# Bridges the sandbox's only permitted egress host (host.openshell.internal)
# to the lab's shared inference endpoint, because the NemoClaw inference SSRF
# guard refuses private/internal endpoints (the alias is exempt) and policy
# presets may pin allowed_ips on the bridge host only. Same pattern as
# run-lab-proxy.sh; see docs/TROUBLESHOOTING.md ("Private/internal LLM
# endpoint").
#
# Usage:
#   deploy/scripts/run-inference-proxy.sh          (reads LLM_BASE_URL from .env)
#   LLM_BASE_URL=https://model.example.lab/api/qwen36/v1 deploy/scripts/run-inference-proxy.sh
#
# Environment (env wins over .env):
#   LLM_BASE_URL     (required) the real OpenAI-compatible endpoint, incl. path
#   LLM_PROXY_PORT   host port the sandbox reaches the LLM on  (default 18100)
#   LLM_CA           path to the CA bundle for the endpoint's TLS cert;
#                    unset = proxy_ssl_verify off (demo posture, warned)
#
# Idempotent: skips the install + nginx reload when the rendered conf matches
# what is already installed. Re-run after every LLM_BASE_URL change — this is
# the endpoint hop of `make repoint-llm`, which calls it for you.
set -euo pipefail

cd "$(dirname "$0")/../.."

# shellcheck source=deploy/scripts/lib/envfile.sh
source deploy/scripts/lib/envfile.sh

LLM_BASE_URL="${LLM_BASE_URL:-$(env_get LLM_BASE_URL)}"
LLM_PROXY_PORT="${LLM_PROXY_PORT:-$(env_get LLM_PROXY_PORT)}"
LLM_PROXY_PORT="${LLM_PROXY_PORT:-18100}"
LLM_MODEL="${LLM_MODEL:-$(env_get LLM_MODEL)}"
LLM_API_KEY="${LLM_API_KEY:-$(env_get LLM_API_KEY)}"

[[ -n "$LLM_BASE_URL" && "$LLM_BASE_URL" != *YOUR_LLM_HOST* && "$LLM_BASE_URL" != *YOUR_VLLM_HOST* ]] \
  || { echo "LLM_BASE_URL is unset or still the .env.example placeholder. Set it in .env." >&2; exit 1; }
LLM_BASE_URL="${LLM_BASE_URL%/}"

command -v nginx >/dev/null || {
  echo "nginx not found. Install it first: sudo apt-get install -y nginx" >&2
  exit 1
}

if [[ -n "${LLM_CA:-}" ]]; then
  [[ -r "$LLM_CA" ]] || { echo "LLM_CA '$LLM_CA' is not readable" >&2; exit 1; }
  PROXY_SSL_CONF="proxy_ssl_verify on;
        proxy_ssl_trusted_certificate ${LLM_CA};"
else
  [[ "$LLM_BASE_URL" == https://* ]] \
    && echo "WARNING: LLM_CA not set — upstream TLS will NOT be verified (proxy_ssl_verify off)." >&2
  PROXY_SSL_CONF="proxy_ssl_verify off;"
fi

# The sandbox reaches us on the docker bridge; loopback serves host-side probes.
BRIDGE_IP="$(ip -4 -o addr show docker0 2>/dev/null | awk '{print $4}' | cut -d/ -f1 | head -1)"
BRIDGE_IP="${BRIDGE_IP:-172.17.0.1}"

export LLM_BASE_URL LLM_PROXY_PORT BRIDGE_IP PROXY_SSL_CONF

TEMPLATE="deploy/proxy/nemoclaw-inference-proxy.conf.template"
CONF="/etc/nginx/conf.d/nemoclaw-inference-proxy-${LLM_PROXY_PORT}.conf"
RENDERED="$(mktemp)"
trap 'rm -f "$RENDERED"' EXIT

# Restrict envsubst to our variables so nginx's own $vars survive untouched.
envsubst '${LLM_BASE_URL} ${LLM_PROXY_PORT} ${BRIDGE_IP} ${PROXY_SSL_CONF}' \
  < "$TEMPLATE" > "$RENDERED"

if [[ -r "$CONF" ]] && cmp -s "$RENDERED" "$CONF"; then
  echo "${CONF} already up to date (port ${LLM_PROXY_PORT} -> ${LLM_BASE_URL}) — nothing to install."
else
  # A hand-written conf on the same port (the pre-ADR-014 GB10 setup) would
  # shadow or fight this one — surface it rather than silently coexisting.
  OTHER="$(grep -rlE "listen[^;]*[: ]${LLM_PROXY_PORT};" /etc/nginx/conf.d/ 2>/dev/null \
    | grep -vF "$CONF" || true)"
  [[ -n "$OTHER" ]] && echo "WARNING: other nginx conf(s) also listen on :${LLM_PROXY_PORT} — remove or port-move them:
${OTHER}" >&2

  echo "Installing ${CONF} (port ${LLM_PROXY_PORT} -> ${LLM_BASE_URL})"
  sudo install -m 0644 "$RENDERED" "$CONF"
  sudo nginx -t
  sudo systemctl reload nginx
fi

# Smoke test: the OpenAI models listing through the proxy, authed if a key is
# on hand. 200 = the whole chain works; anything else is fatal — the sandbox
# would hit the same wall.
CURL_AUTH=()
[[ -n "$LLM_API_KEY" ]] && CURL_AUTH=(-H "Authorization: Bearer ${LLM_API_KEY}")
BODY="$(mktemp)"
trap 'rm -f "$RENDERED" "$BODY"' EXIT
CODE="$(curl -s -o "$BODY" -w '%{http_code}' --max-time 10 \
  "${CURL_AUTH[@]}" "http://127.0.0.1:${LLM_PROXY_PORT}/v1/models" || true)"
if [[ "$CODE" != "200" ]]; then
  echo "✗ smoke test failed: GET 127.0.0.1:${LLM_PROXY_PORT}/v1/models returned '${CODE:-no response}'" >&2
  echo "  (401/403 = LLM_API_KEY rejected; 502/504 = ${LLM_BASE_URL} unreachable from this host)" >&2
  exit 1
fi
if [[ -n "$LLM_MODEL" ]] && ! grep -qF "$LLM_MODEL" "$BODY"; then
  echo "! proxy is up, but the endpoint's /v1/models does not list LLM_MODEL='${LLM_MODEL}'." >&2
  echo "  Check the model id in .env against what the endpoint actually serves." >&2
fi
echo "✓ proxy live: http://host.openshell.internal:${LLM_PROXY_PORT}/v1 -> ${LLM_BASE_URL}"

cat <<EOF

If ufw is active, allow the docker bridge through (the listener itself only
binds ${BRIDGE_IP} + loopback):

  sudo ufw allow from 172.16.0.0/12 to any port ${LLM_PROXY_PORT} proto tcp comment "nemoclaw inference proxy (docker bridge)"
EOF
