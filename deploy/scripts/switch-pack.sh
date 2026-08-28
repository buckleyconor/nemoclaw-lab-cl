#!/usr/bin/env bash
# Switch which pack the local docker-compose stack serves.
#
# PACK_ID is resolved once per process at startup by gateway/orchestrator/
# mcp-tools/simulator (each service's own _resolve_pack_dir()) and cached for
# the life of the process — there is no live pack-switching in one running
# stack (docs/PACK-EXPANSION-PLAN.md's "Option A"). This script is the
# sanctioned way to do the restart: update .env, then fully recreate the
# stack so mcp-tools' FAISS index and the gateway's in-memory fault/activity
# registry (both pack-scoped) don't survive the switch.
#
# Usage: deploy/scripts/switch-pack.sh <pack-id>
set -euo pipefail

cd "$(dirname "$0")/../.."

# Self-heal PATH like doctor.sh does (2026-08-28 incident): when the terminal
# daemon spawns this script from a systemd unit whose PATH lacks /snap/bin,
# snap-installed docker isn't found and the switch dies silently mid-way.
case ":$PATH:" in *:/snap/bin:*) ;; *) PATH="$PATH:/snap/bin" ;; esac
case ":$PATH:" in *:"$HOME/.local/bin":*) ;; *) PATH="$HOME/.local/bin:$PATH" ;; esac
export PATH

if [[ $# -ne 1 ]]; then
  echo "Usage: $0 <pack-id>" >&2
  echo "Available packs:" >&2
  ls -1 packs/ >&2
  exit 1
fi

PACK_ID="$1"

if [[ ! -f "packs/${PACK_ID}/pack.yaml" ]]; then
  echo "Unknown pack: ${PACK_ID} (no packs/${PACK_ID}/pack.yaml)" >&2
  echo "Available packs:" >&2
  ls -1 packs/ >&2
  exit 1
fi

if [[ ! -f .env ]]; then
  echo ".env not found — copy .env.example to .env first." >&2
  exit 1
fi

# Fail BEFORE mutating .env: a sed that lands followed by a docker failure
# leaves .env and the running stack disagreeing about the pack, and the
# dashboard's mismatch banner pointing at a switch that never happened.
if ! docker compose version >/dev/null 2>&1; then
  echo "docker compose not usable from this environment (PATH=$PATH) — aborting before touching .env" >&2
  exit 1
fi

if grep -q '^PACK_ID=' .env; then
  sed -i.bak "s/^PACK_ID=.*/PACK_ID=${PACK_ID}/" .env
  rm -f .env.bak
else
  echo "PACK_ID=${PACK_ID}" >> .env
fi

echo "Switching stack to PACK_ID=${PACK_ID}..."
docker compose down
docker compose up -d

echo
echo "Stack is now serving '${PACK_ID}'. Confirm with:"
echo "  curl -s http://localhost:8001/api/pack | python3 -m json.tool"
echo
echo "Note: this only restarts the docker-compose stack. The OpenClaw sandbox"
echo "(SOUL.md/AGENTS.md/skills) is managed separately — reset it via the"
echo "embedded terminal's option 7, or the lab guide's 'Ready for a different"
echo "scenario?' button, before starting the next pack's persona exercise."
