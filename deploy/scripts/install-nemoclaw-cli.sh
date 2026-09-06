#!/usr/bin/env bash
# Install the NemoClaw CLI *without* onboarding a sandbox (ADR-015).
#
# PROTOTYPE — opt-in, not yet wired into `make bootstrap`. See ADR-015
# "Consequences/Negative" before relying on this in an unattended deploy.
#
# Why this exists: the public installer is a three-phase script and phase 3
# ("Onboarding") runs by default for the openclaw agent. It calls a bare
# `nemoclaw onboard` — no `--from`, no `--name` — so it builds the *managed*
# sandbox, while this lab needs its own image (openclaw/Dockerfile) pointed at
# the inference proxy, which does not exist yet at install time. The published
# opt-out (`--defer-onboarding`) is Hermes-only and fatal for openclaw, and
# every other skip path requires a sandbox to already be registered, which is
# never true on a fresh host.
#
# The installer payload guards its own entrypoint:
#
#   if [[ "${BASH_SOURCE[0]:-}" == "$0" ]] ...; then main "$@"; fi
#
# so sourcing it defines every function without running phase 3. We then call
# phases 1-2 directly and stop. Onboarding stays where it belongs — in
# deploy/scripts/onboard-openclaw.sh, after run-inference-proxy.sh is live.
#
# Usage:
#   NEMOCLAW_ACCEPT_THIRD_PARTY_SOFTWARE=1 deploy/scripts/install-nemoclaw-cli.sh
#
# Environment:
#   NEMOCLAW_ACCEPT_THIRD_PARTY_SOFTWARE=1   (required) accept the third-party
#                    software notice. Required explicitly because phases 1-2
#                    skip the installer's own acceptance prompt, which lives in
#                    main(); this script will not bypass a licence gate for you.
#   NEMOCLAW_INSTALL_REF / NEMOCLAW_INSTALL_TAG   git ref to install
#                                                 (default: lkg, as upstream)
#   NEMOCLAW_NON_INTERACTIVE   default 1 here — this is an automation path
set -euo pipefail

NEMOCLAW_REPO_URL="${NEMOCLAW_REPO_URL:-https://github.com/NVIDIA/NemoClaw.git}"
PAYLOAD_MARKER="NEMOCLAW_VERSIONED_INSTALLER_PAYLOAD=1"
DEFAULT_INSTALL_REF="lkg"

die() { echo "  ✗ $*" >&2; exit 1; }
say() { echo "  $*"; }

[[ "${NEMOCLAW_ACCEPT_THIRD_PARTY_SOFTWARE:-}" == "1" ]] || die \
  "NEMOCLAW_ACCEPT_THIRD_PARTY_SOFTWARE=1 is required. Phases 1-2 skip the
     installer's own third-party software notice (it is prompted in main(),
     which this script deliberately does not run), so acceptance must be
     given explicitly here."

command -v git >/dev/null || die "git not found. Install: sudo apt-get install -y git"

# Upstream precedence: NEMOCLAW_INSTALL_REF wins, then NEMOCLAW_INSTALL_TAG,
# then "lkg" (install.sh resolve_release_tag).
REF="${NEMOCLAW_INSTALL_REF:-${NEMOCLAW_INSTALL_TAG:-$DEFAULT_INSTALL_REF}}"

TMPDIR_CLONE="$(mktemp -d)"
cleanup_clone() { rm -rf "${TMPDIR_CLONE:-}"; }
trap cleanup_clone EXIT

SOURCE_ROOT="${TMPDIR_CLONE}/source"
say "Cloning NemoClaw @ ${REF} …"
# Mirrors clone_nemoclaw_ref() in the upstream bootstrap, including the umask —
# git applies the process umask to the authoritative source checkout.
(
  umask 022
  git init --quiet "$SOURCE_ROOT"
  git -C "$SOURCE_ROOT" remote add origin "$NEMOCLAW_REPO_URL"
  git -C "$SOURCE_ROOT" fetch --quiet --depth 1 origin "+${REF}:refs/nemoclaw-install/target" \
    || die "install ref '${REF}' is not available from ${NEMOCLAW_REPO_URL}"
  git -C "$SOURCE_ROOT" -c advice.detachedHead=false checkout --quiet --detach refs/nemoclaw-install/target
)

PAYLOAD="${SOURCE_ROOT}/scripts/install.sh"
[[ -s "$PAYLOAD" ]] || die "installer payload missing at scripts/install.sh for ref '${REF}'"
head -1 "$PAYLOAD" | grep -qE '^#!.*(sh|bash)' || die "installer payload has no shell shebang"
# The marker is upstream's own contract for "this ref carries the versioned
# payload". Its absence means a legacy layout we have not adapted to.
grep -q "$PAYLOAD_MARKER" "$PAYLOAD" \
  || die "ref '${REF}' predates the versioned installer payload — unsupported here"

# NEMOCLAW_BOOTSTRAP_PAYLOAD=1 makes is_source_checkout() return false, so
# install_nemoclaw takes the managed "install from GitHub" branch into the
# state root — identical to a real curl|bash install — rather than npm-linking
# our throwaway clone.
export NEMOCLAW_BOOTSTRAP_PAYLOAD=1
export NEMOCLAW_INSTALL_REF="$REF" NEMOCLAW_INSTALL_TAG="$REF"
export NEMOCLAW_NON_INTERACTIVE="${NEMOCLAW_NON_INTERACTIVE:-1}"

say "Sourcing installer phases 1-2 (no onboarding) …"
# Sourced, not executed: BASH_SOURCE[0] is the payload while $0 stays this
# script, so the payload's trailing `main "$@"` guard is false.
# shellcheck source=/dev/null
. "$PAYLOAD"

# Sourcing installs the payload's own `trap _global_cleanup EXIT`, which
# replaced ours. Chain both so the clone is still removed on every exit path.
trap 'cleanup_clone; _global_cleanup' EXIT

install_nemoclaw_before_onboarding

command -v nemoclaw >/dev/null \
  || die "phases 1-2 completed but 'nemoclaw' is not on PATH — check ~/.local/bin"

# Assert the whole point of this script: no sandbox was created. If this ever
# trips, phase 3 leaked into the sourced path and the ADR-015 premise is void.
if [[ "$(registered_sandbox_count)" != "0" ]]; then
  die "a sandbox was registered during CLI install — onboarding was NOT skipped.
     Investigate before running onboard-openclaw.sh."
fi

say "✓ nemoclaw $(nemoclaw --version 2>/dev/null || echo '(version unavailable)') installed; no sandbox onboarded."
say "  Next: bring up the inference proxy, then deploy/scripts/onboard-openclaw.sh"
