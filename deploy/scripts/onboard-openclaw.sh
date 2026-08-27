#!/usr/bin/env bash
# Onboard the real NemoClaw/OpenClaw agent runtime for the lab (ADR-011).
#
# Run AFTER `docker compose up` has the lab stack healthy. This script:
#   1. Builds a sandbox image with the nemoclaw-infra-tools plugin baked in
#   2. Onboards a NemoClaw sandbox against the OpenAI-compatible LLM endpoint
#      (via the host inference proxy by default — see LLM_PROXY_PORT below)
#   3. Applies the lab network-policy preset (mcp-tools:8004 + gateway:8001
#      + inference proxy:18100)
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
#   LLM_BASE_URL   (required) the real endpoint, e.g. https://model.example.lab/api/qwen36/v1
#   LLM_MODEL      (required) e.g. qwen3.6-35b-a3b-fp8
#   LLM_API_KEY    (required) the endpoint's real API key — no dummy default
#   LLM_PROXY_PORT (default: 18100) host inference proxy port; the sandbox is
#                  onboarded against http://host.openshell.internal:<port>/v1
#                  (run-inference-proxy.sh must be live first — the inference
#                  SSRF guard refuses private endpoints but exempts the alias,
#                  and policy guard #6073 can only pin that host anyway)
#   LLM_DIRECT     set to 1 to skip the proxy and bake LLM_BASE_URL verbatim
#                  (only for a genuinely public-resolving endpoint)
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
MCP_PORT="${MCP_PORT:-8004}"
GATEWAY_PORT="${GATEWAY_PORT:-8001}"
LLM_PROXY_PORT="${LLM_PROXY_PORT:-18100}"
HOOK_TOKEN="${HOOK_TOKEN:-$(openssl rand -hex 24)}"

: "${LLM_BASE_URL:?set LLM_BASE_URL to your OpenAI-compatible endpoint (e.g. https://host/api/.../v1)}"
: "${LLM_MODEL:?set LLM_MODEL to the model id reported by the endpoint /v1/models}"
: "${LLM_API_KEY:?set LLM_API_KEY — the lab endpoint requires a real key}"
[[ "$LLM_API_KEY" != "CHANGE_ME" ]] \
  || { echo "LLM_API_KEY is still the .env.example placeholder. Set the real key." >&2; exit 1; }

# The endpoint URL is BAKED into the sandbox here. Default route is the host
# inference proxy (stable sandbox-facing URL; repoint later via `make
# repoint-llm`); LLM_DIRECT=1 bakes the raw endpoint instead.
if [[ "${LLM_DIRECT:-0}" == "1" ]]; then
  LLM_SANDBOX_URL="${LLM_SANDBOX_URL:-$LLM_BASE_URL}"
else
  LLM_SANDBOX_URL="${LLM_SANDBOX_URL:-http://host.openshell.internal:${LLM_PROXY_PORT}/v1}"
  PROXY_CODE="$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 \
    -H "Authorization: Bearer ${LLM_API_KEY}" \
    "http://127.0.0.1:${LLM_PROXY_PORT}/v1/models" || true)"
  [[ "$PROXY_CODE" == "200" ]] || {
    echo "Inference proxy on :${LLM_PROXY_PORT} is not answering (/v1/models -> '${PROXY_CODE:-no response}')." >&2
    echo "It must be live BEFORE onboarding — the URL is baked into the sandbox. Run:" >&2
    echo "  deploy/scripts/run-inference-proxy.sh" >&2
    exit 1
  }
fi

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
# `nemoclaw onboard --from` uses this Dockerfile as the COMPLETE sandbox
# image — the CLI does not layer it over the managed runtime. Basing it on
# ghcr.io/nvidia/nemoclaw/sandbox-base is the documented "base_only_image"
# failure: /usr/local/bin/nemoclaw-start and the baked
# /sandbox/.openclaw/openclaw.json are missing, so the restart-safe startup
# clone exits 127. The full managed OpenClaw image for the installed CLI
# release (v0.0.109) carries the managed runtime (nemoclaw-start entrypoint,
# baked openclaw config + .config-hash, the managed nemoclaw extension).
ARG SANDBOX_BASE=ghcr.io/nvidia/nemoclaw/openclaw-sandbox:v0.0.109
FROM ${SANDBOX_BASE}

# NemoClaw >= v0.0.10x tool-disclosure contract: a custom Dockerfile must
# declare NEMOCLAW_TOOL_DISCLOSURE exactly once in the final stage and promote
# it to a runtime ENV (the CLI rewrites the ARG to the disclosure it applies,
# default 'progressive').
ARG NEMOCLAW_TOOL_DISCLOSURE=progressive
ENV NEMOCLAW_TOOL_DISCLOSURE=${NEMOCLAW_TOOL_DISCLOSURE}

COPY nemoclaw-infra-tools/ /opt/nemoclaw-infra-tools/
WORKDIR /opt/nemoclaw-infra-tools
RUN npm ci --no-audit --no-fund && npm run build && npm prune --omit=dev

RUN mkdir -p /sandbox/.openclaw/extensions \
 && cp -a /opt/nemoclaw-infra-tools /sandbox/.openclaw/extensions/nemoclaw-infra-tools \
 && openclaw doctor --fix

# /sandbox is chowned to the sandbox identity by the base image; the OpenShell
# supervisor validates that the workdir is writable by that identity (the
# image's WORKDIR is what the docker-driver flow feeds it as --workdir).
WORKDIR /sandbox
# OpenShell requires USER sandbox as the image default: the gateway publishes
# it as OPENSHELL_OCI_IMAGE_USER, and the restart-safe startup clone validates
# that marker is non-empty (the managed NemoClaw image declares it too).
USER sandbox
DOCKERFILE

# ── 2. Onboard against the LLM endpoint ──────────────────────────────────────
NEMOCLAW_PROVIDER=custom \
NEMOCLAW_ENDPOINT_URL="$LLM_SANDBOX_URL" \
NEMOCLAW_MODEL="$LLM_MODEL" \
COMPATIBLE_API_KEY="$LLM_API_KEY" \
nemoclaw onboard \
  --non-interactive --yes --yes-i-accept-third-party-software \
  --agent openclaw \
  --name "$SANDBOX_NAME" \
  --from "$BUILD_DIR/Dockerfile"

# ── 3. Network policy: allow the sandbox to reach the lab services ───────────
# Unquoted heredoc: only ${MCP_PORT}/${GATEWAY_PORT}/${LLM_PROXY_PORT} expand —
# the YAML has no other $ content. allowed_ips stays on host.openshell.internal,
# the one host NemoClaw policy presets may pin (policy guard #6073). The
# LLM_PROXY_PORT endpoint covers the inference proxy; openshell may route
# inference traffic host-side anyway, in which case the rule is inert.
cat > "$BUILD_DIR/nemoclaw-lab.yaml" <<POLICY
preset:
  name: nemoclaw-lab
  description: "NemoClaw lab services (mcp-tools + gateway + inference proxy) via host gateway"

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
      - host: host.openshell.internal
        port: ${LLM_PROXY_PORT}
        protocol: rest
        enforcement: enforce
        allowed_ips:
          - 10.0.0.0/8
          - 172.16.0.0/12
          - 192.168.0.0/16
        rules:
          - allow: { method: GET, path: "/v1/**" }
          - allow: { method: POST, path: "/v1/**" }
    binaries:
      - { path: /usr/local/bin/openclaw }
      - { path: /usr/local/bin/node }
      - { path: /usr/bin/node }
POLICY
nemoclaw "$SANDBOX_NAME" policy-add --from-file "$BUILD_DIR/nemoclaw-lab.yaml" --yes

# ── 4. Seed the agent workspace ──────────────────────────────────────────────
# v0.0.109 upload semantics (OpenShell transport): the destination is always
# the DIRECTORY the source extracts into — a file lands at <dest>/<name>, a
# directory at <dest>/<dirname>/. A file-path destination collides with the
# workspace templates the managed runtime seeds at first boot ("mkdir:
# cannot create directory ... File exists"). Overwrite is intended: the lab's
# SOUL.md/AGENTS.md/skills replace the managed defaults (ADR-011f).
nemoclaw "$SANDBOX_NAME" upload "$REPO_ROOT/openclaw/SOUL.md" /sandbox/.openclaw/workspace/
nemoclaw "$SANDBOX_NAME" upload "$REPO_ROOT/openclaw/AGENTS.md" /sandbox/.openclaw/workspace/
nemoclaw "$SANDBOX_NAME" upload "$REPO_ROOT/openclaw/skills" /sandbox/.openclaw/workspace/

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
