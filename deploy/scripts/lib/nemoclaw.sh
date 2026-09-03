#!/usr/bin/env bash
# Shared NemoClaw CLI helpers, sourced by the deploy scripts.
#
# Exists because the sandbox base image tag is COUPLED to the installed CLI
# release. `nemoclaw onboard --from` uses our Dockerfile as the complete
# sandbox image, so it must be based on the managed OpenClaw image for the
# CLI that is driving the onboard (docs/TROUBLESHOOTING.md, "NemoClaw LKG
# onboarding contract walls"). A hardcoded tag silently rots every time the
# installer's `lkg` channel moves: onboarding then fails ~10 minutes into an
# image build with a base_only_image / exit-127 symptom that reads like a
# code bug rather than a version skew.
#
# Usage:
#   source "$(dirname "$0")/lib/nemoclaw.sh"
#   cli_version            -> prints the installed CLI version, e.g. 0.0.102
#                             (empty if the CLI is absent or unparseable)
#   sandbox_base           -> prints the sandbox base image ref to build FROM

# Fallback tag used only when `nemoclaw --version` cannot be parsed. This is
# the LKG the onboarding contract in docs/TROUBLESHOOTING.md was written
# against; it is a last resort, not the normal path.
NEMOCLAW_SANDBOX_BASE_FALLBACK="${NEMOCLAW_SANDBOX_BASE_FALLBACK:-ghcr.io/nvidia/nemoclaw/openclaw-sandbox:v0.0.109}"

# Bare semver of the installed CLI, no leading "v". Empty when the CLI is
# missing or prints something we don't recognise — callers decide whether
# that is fatal.
cli_version() {
  command -v nemoclaw >/dev/null 2>&1 || return 0
  # `nemoclaw --version` prints e.g. "nemoclaw v0.0.102"; take the first
  # semver-looking token so a future format change degrades to "unknown"
  # rather than producing a bogus image tag.
  nemoclaw --version 2>/dev/null \
    | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' | head -1
}

# The image the generated sandbox Dockerfile bases on. An explicit
# SANDBOX_BASE in the environment always wins (pin a known-good image, or
# point at a mirror); otherwise the tag tracks the installed CLI.
sandbox_base() {
  if [[ -n "${SANDBOX_BASE:-}" ]]; then
    echo "$SANDBOX_BASE"
    return 0
  fi
  local ver
  ver="$(cli_version)"
  if [[ -n "$ver" ]]; then
    echo "ghcr.io/nvidia/nemoclaw/openclaw-sandbox:v${ver}"
  else
    echo "$NEMOCLAW_SANDBOX_BASE_FALLBACK"
  fi
}
