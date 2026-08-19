#!/usr/bin/env bash
# Onboard the real NemoClaw/OpenClaw agent runtime for the lab (ADR-011).
#
# Run AFTER `docker compose up` has the lab stack healthy. This script:
#   1. Builds a sandbox image with the nemoclaw-infra-tools plugin baked in
#   2. Onboards a NemoClaw sandbox against your vLLM/OpenAI-compatible endpoint
#   3. Applies the lab network-policy preset (mcp-tools:8004 + gateway:8001)
#   4. Seeds the agent workspace (SOUL.md, AGENTS.md, skills)
#   5. Locks down OpenClaw built-in tools and enables the webhook wake-up
#   6. Writes OPENCLAW_HOOK_URL / OPENCLAW_HOOK_TOKEN into .env, resolving the
#      real webhook port from the sandbox config (it self-reassigns off 18789)
#
# Requirements: nemoclaw CLI on a SUPPORTED host (Linux x86_64/aarch64 or
# Apple Silicon macOS — Intel macOS is unsupported by OpenShell), Docker,
# and a reachable OpenAI-compatible LLM endpoint.
#
# Environment:
#   LLM_BASE_URL   (required) e.g. http://192.168.68.131:8000/v1
#   LLM_MODEL      (required) e.g. qwen3.6-35b-a3b-fp8
#   LLM_API_KEY    (default: vllm)
#   SANDBOX_NAME   (default: infra-sentinel)
#   HOOK_TOKEN     (default: generated) webhook shared secret; written to .env
#                  as OPENCLAW_HOOK_TOKEN for docker compose
#   MCP_PORT       (default: 8004) host-side port the sandbox reaches MCP
#                  tools on via host.openshell.internal. Defaults match the
#                  single-Docker-host dev stack; for the K8s deployment point
#                  these at the per-tenant lab proxy (run-lab-proxy.sh), which
#                  carries the traffic to the cluster ingress. Do NOT try
#                  `nemoclaw mcp add` for internal endpoints — its private-IP
#                  SSRF guard has no override (docs/TROUBLESHOOTING.md).
#   GATEWAY_PORT   (default: 8001) same, for the gateway API

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

# shellcheck source=deploy/scripts/lib/envfile.sh
source "${REPO_ROOT}/deploy/scripts/lib/envfile.sh"
cd "$REPO_ROOT"  # env_upsert writes ./.env

SANDBOX_NAME="${SANDBOX_NAME:-infra-sentinel}"
LLM_API_KEY="${LLM_API_KEY:-vllm}"
MCP_PORT="${MCP_PORT:-8004}"
GATEWAY_PORT="${GATEWAY_PORT:-8001}"
HOOK_TOKEN="${HOOK_TOKEN:-$(openssl rand -hex 24)}"

: "${LLM_BASE_URL:?set LLM_BASE_URL to your OpenAI-compatible endpoint (e.g. http://host:8000/v1)}"
: "${LLM_MODEL:?set LLM_MODEL to the model id reported by the endpoint /v1/models}"

command -v nemoclaw >/dev/null || {
  echo "nemoclaw CLI not found. Install: curl -fsSL https://www.nvidia.com/nemoclaw.sh | bash" >&2
  exit 1
}

# ── 1. Stage the sandbox image build context ─────────────────────────────────
BUILD_DIR="$(mktemp -d)"
trap 'rm -rf "$BUILD_DIR"' EXIT

cp -R "$REPO_ROOT/openclaw/plugins/nemoclaw-infra-tools" "$BUILD_DIR/nemoclaw-infra-tools"
rm -rf "$BUILD_DIR/nemoclaw-infra-tools/node_modules" "$BUILD_DIR/nemoclaw-infra-tools/dist"

cat > "$BUILD_DIR/Dockerfile" <<'DOCKERFILE'
ARG SANDBOX_BASE=ghcr.io/nvidia/nemoclaw/sandbox-base:latest
FROM ${SANDBOX_BASE}

COPY nemoclaw-infra-tools/ /opt/nemoclaw-infra-tools/
WORKDIR /opt/nemoclaw-infra-tools
RUN npm ci --no-audit --no-fund && npm run build && npm prune --omit=dev

RUN mkdir -p /sandbox/.openclaw/extensions \
 && cp -a /opt/nemoclaw-infra-tools /sandbox/.openclaw/extensions/nemoclaw-infra-tools \
 && openclaw doctor --fix

WORKDIR /opt/nemoclaw
DOCKERFILE

# ── 2. Onboard against the LLM endpoint ──────────────────────────────────────
NEMOCLAW_PROVIDER=custom \
NEMOCLAW_ENDPOINT_URL="$LLM_BASE_URL" \
NEMOCLAW_MODEL="$LLM_MODEL" \
COMPATIBLE_API_KEY="$LLM_API_KEY" \
nemoclaw onboard \
  --non-interactive --yes --yes-i-accept-third-party-software \
  --agent openclaw \
  --name "$SANDBOX_NAME" \
  --from "$BUILD_DIR/Dockerfile"

# ── 3. Network policy: allow the sandbox to reach the lab services ───────────
# Unquoted heredoc: only ${MCP_PORT}/${GATEWAY_PORT} expand — the YAML has no
# other $ content. allowed_ips stays on host.openshell.internal, the one host
# NemoClaw policy presets may pin (policy guard #6073).
cat > "$BUILD_DIR/nemoclaw-lab.yaml" <<POLICY
preset:
  name: nemoclaw-lab
  description: "NemoClaw lab services (mcp-tools + gateway) via host gateway"

network_policies:
  nemoclaw_lab:
    name: nemoclaw_lab
    endpoints:
      - host: host.openshell.internal
        port: ${MCP_PORT}
        protocol: rest
        enforcement: enforce
        allowed_ips:
          - 10.0.0.0/8
          - 172.16.0.0/12
          - 192.168.0.0/16
        rules:
          - allow: { method: GET, path: "/**" }
          - allow: { method: POST, path: "/**" }
      - host: host.openshell.internal
        port: ${GATEWAY_PORT}
        protocol: rest
        enforcement: enforce
        allowed_ips:
          - 10.0.0.0/8
          - 172.16.0.0/12
          - 192.168.0.0/16
        rules:
          - allow: { method: GET, path: "/**" }
          - allow: { method: POST, path: "/**" }
          - allow: { method: PATCH, path: "/**" }
    binaries:
      - { path: /usr/local/bin/openclaw }
      - { path: /usr/local/bin/node }
      - { path: /usr/bin/node }
POLICY
nemoclaw "$SANDBOX_NAME" policy-add --from-file "$BUILD_DIR/nemoclaw-lab.yaml" --yes

# ── 4. Seed the agent workspace ──────────────────────────────────────────────
nemoclaw "$SANDBOX_NAME" upload "$REPO_ROOT/openclaw/SOUL.md" /sandbox/.openclaw/workspace/SOUL.md
nemoclaw "$SANDBOX_NAME" upload "$REPO_ROOT/openclaw/AGENTS.md" /sandbox/.openclaw/workspace/AGENTS.md
nemoclaw "$SANDBOX_NAME" upload "$REPO_ROOT/openclaw/skills" /sandbox/.openclaw/workspace/skills

# ── 5. Tool lockdown + plugin config + webhook wake-up ───────────────────────
# Built-in tools (exec/browser/web_search/...) are a new attack surface the
# bespoke ADR-010 agent never had. tools.profile sets a BASE allowlist that
# tools.allow can only narrow, never extend (confirmed against OpenClaw
# v2026.5.27's tool-policy engine) — profile "minimal" caps the base set at
# session_status alone, which silently drops the plugin's seven tools before
# tools.allow ever runs. Use "full" (no base restriction) and let
# tools.allow/tools.deny do all the work instead. deny wins over allow, so
# remediation-execute stays refused even if a future config edit widens the
# allowlist.
nemoclaw "$SANDBOX_NAME" exec -- openclaw config set tools.profile '"full"' --strict-json
nemoclaw "$SANDBOX_NAME" exec -- openclaw config set tools.allow \
  '["monitor_list_events","monitor_get_asset","monitor_list_assets","logs_get_bundle","kb_search","notify_post_activity","remediation_propose"]' --strict-json
nemoclaw "$SANDBOX_NAME" exec -- openclaw config set tools.deny \
  '["group:runtime","group:fs","group:web","group:ui","group:messaging","*remediation_execute*"]' --strict-json
nemoclaw "$SANDBOX_NAME" exec -- openclaw config set \
  plugins.entries.nemoclaw-infra-tools.config \
  "{\"mcpUrl\":\"http://host.openshell.internal:${MCP_PORT}/mcp\",\"gatewayUrl\":\"http://host.openshell.internal:${GATEWAY_PORT}\"}" --strict-json
# Without an explicit trust entry the plugin loads as untracked local code and
# its tools never register as callable ("no registered tools matched").
nemoclaw "$SANDBOX_NAME" exec -- openclaw config set plugins.allow \
  '["nemoclaw-infra-tools"]' --strict-json

nemoclaw "$SANDBOX_NAME" exec -- openclaw config set hooks \
  "{\"enabled\":true,\"token\":\"$HOOK_TOKEN\",\"path\":\"/hooks\"}" --strict-json

# Safety-net poll (webhook is the primary wake-up, fired by the Gateway on
# scenario injection): check the monitoring surface every minute.
nemoclaw "$SANDBOX_NAME" exec -- openclaw cron add \
  --name "infra-sentinel-safety-poll" \
  --cron "* * * * *" \
  --session main \
  --system-event "safety-net poll: run the Infrastructure Fault Response program" \
  --wake now || echo "WARN: cron add failed — webhook remains the only trigger"

# `nemoclaw <name> gateway restart` is not a real subcommand — recover is the
# host-side action that restarts the sandbox's gateway/dashboard.
nemoclaw "$SANDBOX_NAME" recover

# ── 7. Resolve the real webhook port and record it in .env ───────────────────
# 18789 is only the default: openclaw silently self-reassigns if it is taken
# (the reference GB10 host landed on 18790), and `nemoclaw <name> status` does
# NOT report the chosen port. The sandbox's own config is the only authority,
# so read it back rather than making the operator guess.
HOOK_PORT=""
OC_JSON="$(mktemp)"
if nemoclaw "$SANDBOX_NAME" download /sandbox/.openclaw/openclaw.json "$OC_JSON" >/dev/null 2>&1; then
  HOOK_PORT="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["gateway"]["port"])' \
    "$OC_JSON" 2>/dev/null || true)"
fi
rm -f "$OC_JSON"

echo
echo "── Onboarding complete ─────────────────────────────────────────────"
echo "Sandbox:        $SANDBOX_NAME"
echo "Webhook token:  $HOOK_TOKEN"

if [[ -n "$HOOK_PORT" ]]; then
  HOOK_URL="http://host.docker.internal:${HOOK_PORT}"
  echo "Webhook port:   $HOOK_PORT (read from the sandbox's openclaw.json)"
  echo
  echo "Wrote the Gateway's agent wiring to .env:"
  env_upsert OPENCLAW_HOOK_URL "$HOOK_URL"
  env_upsert OPENCLAW_HOOK_TOKEN "$HOOK_TOKEN"
  echo "  OPENCLAW_HOOK_URL=${HOOK_URL}"
  echo "  OPENCLAW_HOOK_TOKEN=${HOOK_TOKEN}"
  echo
  echo "Apply it:  docker compose up -d gateway"
  echo
  echo "On Linux the containerized Gateway can't reach openshell's loopback-bound"
  echo "forward directly — run the bridge relay on the same port:"
  echo "  HOOK_RELAY_PORT=${HOOK_PORT} make hook-relay   (or just: make demo-up)"
else
  # Non-fatal: onboarding itself succeeded, only the read-back failed.
  echo
  echo "! Could not read the webhook port back from the sandbox, so .env was not"
  echo "  updated. Find the port under \"gateway\".\"port\" in the sandbox config:"
  echo "    nemoclaw $SANDBOX_NAME download /sandbox/.openclaw/openclaw.json /tmp/oc.json"
  echo "  then add to .env and run 'docker compose up -d gateway':"
  echo "    OPENCLAW_HOOK_URL=http://host.docker.internal:<PORT>"
  echo "    OPENCLAW_HOOK_TOKEN=$HOOK_TOKEN"
  echo "  and start the relay on that same port: HOOK_RELAY_PORT=<PORT> make hook-relay"
fi

echo
echo "Status:  nemoclaw $SANDBOX_NAME status"
echo "Logs:    nemoclaw $SANDBOX_NAME logs --follow"
