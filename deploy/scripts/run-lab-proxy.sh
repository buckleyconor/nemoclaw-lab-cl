#!/usr/bin/env bash
# Render + install the agent-host lab proxy (ADR-011, K8s deployment).
#
# Bridges the sandbox's only permitted egress host (host.openshell.internal)
# to the cluster ingress, because `nemoclaw mcp add` cannot register internal
# endpoints (hardcoded private-IP SSRF guard, no override) and NemoClaw policy
# presets may pin allowed_ips on the bridge host only. See
# docs/TROUBLESHOOTING.md ("MCP server URL host ... private, local, or
# special-use IP address").
#
# Usage:
#   LAB_INGRESS_HOST=nemoclaw-demo-01.dell-demo.lab deploy/scripts/run-lab-proxy.sh
#
# Environment:
#   LAB_INGRESS_HOST   (required) the tenant's gateway ingress host
#   MCP_PROXY_PORT     host port for /mcp traffic     (default 8004)
#   GW_PROXY_PORT      host port for gateway traffic  (default 8001)
#   LAB_INGRESS_CA     path to the internal CA bundle for the ingress cert;
#                      unset = proxy_ssl_verify off (demo posture, warned)
#
# Per-tenant scaling (M9, 30 namespaces): one port pair per tenant sandbox —
# tenant i gets $((8004+i*10))/$((8001+i*10)), matching run-terminal-tenant.sh
# port conventions. Pass the pair via MCP_PROXY_PORT/GW_PROXY_PORT and use the
# same values as MCP_PORT/GATEWAY_PORT when running onboard-openclaw.sh.
set -euo pipefail

cd "$(dirname "$0")/../.."

: "${LAB_INGRESS_HOST:?set LAB_INGRESS_HOST to the tenant ingress host, e.g. nemoclaw-demo-01.dell-demo.lab}"
MCP_PROXY_PORT="${MCP_PROXY_PORT:-8004}"
GW_PROXY_PORT="${GW_PROXY_PORT:-8001}"

if [[ -n "${LAB_INGRESS_CA:-}" ]]; then
  [[ -r "$LAB_INGRESS_CA" ]] || { echo "LAB_INGRESS_CA '$LAB_INGRESS_CA' is not readable" >&2; exit 1; }
  PROXY_SSL_CONF="proxy_ssl_verify on;
        proxy_ssl_trusted_certificate ${LAB_INGRESS_CA};"
else
  echo "WARNING: LAB_INGRESS_CA not set — upstream TLS will NOT be verified (proxy_ssl_verify off)." >&2
  PROXY_SSL_CONF="proxy_ssl_verify off;"
fi
export LAB_INGRESS_HOST MCP_PROXY_PORT GW_PROXY_PORT PROXY_SSL_CONF

TEMPLATE="deploy/proxy/nemoclaw-lab-proxy.conf.template"
CONF="/etc/nginx/conf.d/nemoclaw-lab-proxy-${MCP_PROXY_PORT}.conf"
RENDERED="$(mktemp)"
trap 'rm -f "$RENDERED"' EXIT

# Restrict envsubst to our variables so nginx's own $vars survive untouched.
# shellcheck disable=SC2016  # literal on purpose: this is envsubst's
# allowlist of names to substitute, not a string to expand here.
envsubst '${LAB_INGRESS_HOST} ${MCP_PROXY_PORT} ${GW_PROXY_PORT} ${PROXY_SSL_CONF}' \
  < "$TEMPLATE" > "$RENDERED"

echo "Installing ${CONF} (ports ${MCP_PROXY_PORT}/${GW_PROXY_PORT} -> https://${LAB_INGRESS_HOST})"
sudo install -m 0644 "$RENDERED" "$CONF"
sudo nginx -t
sudo systemctl reload nginx

cat <<EOF

Proxy live. Allow the docker bridge through the firewall (mirrors the
inference proxy's rule on :18100) if not already present:

  sudo ufw allow from 172.16.0.0/12 to any port ${MCP_PROXY_PORT} proto tcp comment "nemoclaw lab proxy (mcp, docker bridge)"
  sudo ufw allow from 172.16.0.0/12 to any port ${GW_PROXY_PORT} proto tcp comment "nemoclaw lab proxy (gateway, docker bridge)"

Then onboard/point the sandbox at it:

  MCP_PORT=${MCP_PROXY_PORT} GATEWAY_PORT=${GW_PROXY_PORT} deploy/scripts/onboard-openclaw.sh ...

Smoke test (expect an MCP initialize result, not an nginx error page):

  curl -s http://127.0.0.1:${MCP_PROXY_PORT}/mcp -X POST \\
    -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' \\
    -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-03-26","capabilities":{},"clientInfo":{"name":"probe","version":"0"}}}'
EOF
