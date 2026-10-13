#!/usr/bin/env bash
# Open the lab dashboard in the browser — after the lab is actually ready.
#
#   ./deploy/scripts/demo-browser.sh              qa (default): normal window
#   ./deploy/scripts/demo-browser.sh kiosk        fullscreen, chromeless, presenting
#   ./deploy/scripts/demo-browser.sh --stop       close the kiosk window
#   ./deploy/scripts/demo-browser.sh --install-autostart
#
# Why this is a script and not an autostart line with a delay: the host's entry
# carried X-GNOME-Autostart-Delay=120, which gnome-session on Ubuntu 24.04 does
# not implement (it is a GNOME 2 key — measured 2026-10-13: session up 10:47:33,
# browser started 10:47:40, i.e. 7s and not 120s). So the browser opened in the
# middle of the boot race — that boot had the terminal daemon crash-looping on
# the docker bridge IP from 10:47:39 to 10:47:42 — and a dashboard loaded while
# the gateway or daemon is down sits on the error page: the SPA does not retry.
# The gate is therefore the lab's own health verdict, not a guessed number of
# seconds; a wrong guess is invisible, which is the same shape as every other
# false-green in this repo's history.
#
# Kiosk mode uses its OWN profile (~/nemoclaw-kiosk-profile) plus -no-remote:
#   -no-remote  — without it the kiosk flags are forwarded to whatever Firefox
#                 window is already open, i.e. the operator's own session;
#   own profile — no session-restore of yesterday's tabs over the demo, and a
#                 seeded prefs.js skips the first-run onboarding that would
#                 otherwise be the first thing the audience sees.
# The profile lives under $HOME un-hidden on purpose: snap Firefox's `home`
# interface does not expose dotfiles, so ~/.config/... is unreadable to it.
#
# Kiosk is deliberately NOT the default: it strips chrome and blocks devtools,
# which is right for presenting and wrong for testing.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
# shellcheck source=deploy/scripts/lib/envfile.sh
source "$REPO/deploy/scripts/lib/envfile.sh"

GATEWAY_PORT="${GATEWAY_PORT:-$(env_get GATEWAY_PORT "$REPO/.env")}"
GATEWAY_PORT="${GATEWAY_PORT:-8001}"
DASHBOARD_URL="${DASHBOARD_URL:-$(env_get DASHBOARD_URL "$REPO/.env")}"
DASHBOARD_URL="${DASHBOARD_URL:-http://localhost:${GATEWAY_PORT}/lab/}"
HEALTH_URL="http://localhost:${GATEWAY_PORT}/api/lab-health"
WAIT_MAX="${DEMO_BROWSER_WAIT:-90}"
KIOSK_PROFILE="${KIOSK_PROFILE:-$HOME/nemoclaw-kiosk-profile}"
KIOSK_MONITOR="${DEMO_KIOSK_MONITOR:-}"
AUTOSTART="$HOME/.config/autostart/firefox.desktop"

log() { printf '  %s\n' "$*"; }

# ── wait for the lab, not for a stopwatch ────────────────────────────────────
red_checks() {
  # The trailing `|| true` is load-bearing, not tidiness: pipefail makes a dead
  # endpoint fail the pipeline, and a failing command substitution in an
  # assignment exits a `set -e` caller. Unreachable IS the normal boot case, and
  # without this the script died here instead of opening the browser — i.e. the
  # exact failure it exists to prevent.
  curl -s --max-time 3 "$HEALTH_URL" 2>/dev/null \
    | { command -v jq >/dev/null && jq -r '[.checks[]|select(.status!="ok")|.id]|join(", ")' || true; } \
    || true
}

wait_for_lab() {
  local waited=0 shown=0 detail
  while :; do
    if curl -sf --max-time 3 "$HEALTH_URL" 2>/dev/null | grep -q '"healthy"[[:space:]]*:[[:space:]]*true'; then
      (( waited > 0 )) && log "lab healthy after ${waited}s" || log "lab healthy"
      log "opening ${DASHBOARD_URL}"
      return 0
    fi
    (( waited >= WAIT_MAX )) && break
    if (( shown == 0 && waited >= 10 )); then
      # Name the red check rather than saying "not ready": the point of waiting
      # is to know what is holding the demo up.
      detail="$(red_checks)"
      log "waiting for the lab (${waited}s elapsed)${detail:+ — red: ${detail}}"
      shown=1
    fi
    sleep 2; waited=$((waited + 2))
  done
  # Open anyway. A presenter with a red check still needs a window on screen,
  # and the dashboard's own health panel will show what is down.
  detail="$(red_checks)" || true
  log "! not healthy after ${WAIT_MAX}s${detail:+ (red: ${detail})} — opening the dashboard anyway"
  return 0
}

# ── kiosk profile, seeded so the first thing on screen is the dashboard ──────
seed_kiosk_profile() {
  [[ -f "$KIOSK_PROFILE/prefs.js" ]] && return 0
  mkdir -p "$KIOSK_PROFILE"
  cat > "$KIOSK_PROFILE/prefs.js" <<PREFS
user_pref("browser.aboutwelcome.enabled", false);
user_pref("startup.homepage_welcome_url", "");
user_pref("startup.homepage_welcome_url.additional", "");
user_pref("startup.homepage_override_url", "");
user_pref("browser.startup.page", 1);
user_pref("browser.startup.homepage", "${DASHBOARD_URL}");
user_pref("browser.sessionstore.resume_from_crash", false);
user_pref("browser.sessionstore.resume_session_once", false);
user_pref("browser.shell.checkDefaultBrowser", false);
user_pref("browser.aboutConfig.showWarning", false);
user_pref("datareporting.policy.dataSubmissionEnabled", false);
user_pref("datareporting.policy.dataSubmissionPolicyBypassNotification", true);
user_pref("datareporting.healthreport.uploadEnabled", false);
user_pref("toolkit.telemetry.enabled", false);
user_pref("app.shield.optoutstudies.enabled", false);
user_pref("browser.urlbar.quicksuggest.enabled", false);
user_pref("extensions.getAddons.showPane", false);
user_pref("browser.migrate.ignoreShortcuts.enabled", true);
PREFS
  log "created kiosk profile ${KIOSK_PROFILE}"
}

install_autostart() {
  local template="$REPO/deploy/desktop/lab-browser-autostart.desktop" out
  [[ -f "$template" ]] || { echo "✗ missing template $template" >&2; return 1; }
  mkdir -p "$HOME/.config/autostart"
  if [[ -f "$AUTOSTART" && ! -s "$AUTOSTART" ]]; then rm -f "$AUTOSTART"; fi
  if [[ -f "$AUTOSTART" ]] && ! grep -q "demo-browser.sh" "$AUTOSTART"; then
    out="$AUTOSTART.bak-$(date +%Y%m%d-%H%M%S)"
    cp -a "$AUTOSTART" "$out"
    log "kept the existing entry at $out"
  fi
  sed "s|__NEMOCLAW_REPO__|$REPO|g" "$template" > "$AUTOSTART"
  command -v desktop-file-validate >/dev/null && desktop-file-validate "$AUTOSTART" \
    && log "validated"
  log "installed $AUTOSTART (mode: qa — see the file to make kiosk the boot default)"
}

case "${1:-qa}" in
  --stop)
    # Kiosk mode swallows the usual exit affordances, so stopping is by profile.
    if pkill -f -- "-profile $KIOSK_PROFILE" 2>/dev/null; then
      log "closed the kiosk browser"
    else
      log "no kiosk browser running"
    fi
    ;;
  --install-autostart) install_autostart ;;
  qa)
    wait_for_lab
    # Attach to the operator's own Firefox when one is open: QA needs bookmarks
    # and devtools, which the kiosk profile deliberately does not carry.
    exec /bin/firefox --new-window "$DASHBOARD_URL"
    ;;
  kiosk)
    wait_for_lab
    seed_kiosk_profile
    args=(--kiosk -no-remote -profile "$KIOSK_PROFILE" "$DASHBOARD_URL")
    [[ -n "$KIOSK_MONITOR" ]] && args+=(--kiosk-monitor "$KIOSK_MONITOR")
    # exec, not a child: `make demo-kiosk` in a terminal then ends the browser
    # when the terminal does, and Ctrl-C reaches Firefox rather than a shell.
    exec /bin/firefox "${args[@]}"
    ;;
  *)
    echo "usage: $0 [qa|kiosk|--stop|--install-autostart]" >&2
    exit 2
    ;;
esac
