#!/usr/bin/env bash
# Install + enable the NemoClaw self-heal layer (run as root / sudo):
#
#   sudo ./deploy/scripts/install-selfheal.sh      (or: sudo make install-selfheal)
#
# Two domains (2026-08-28 reboot incident — the reason for the split):
#
# System units (/etc/systemd/system/):
#   - nemoclaw-inference-watchdog.timer (root, 60s) — restarts nginx when the
#     inference proxy is down or running stale sockets
#
# USER units (~/.config/systemd/user/ of the lab user, linger enabled) — the
# whole wake-hook chain lives here because (a) the nemoclaw CLI needs the
# user session's D-Bus to drive the gateway unit (as a system unit it fell
# back to a rogue standalone gateway and every recover failed with "sandbox
# not found"), and (b) gateway -> forward -> relay boot ordering is only
# expressible inside one systemd manager domain:
#   - nemoclaw-gateway-<port>.service — boot-takeover of the shared OpenShell
#     gateway (skipped with a warning until its captured env file exists)
#   - nemoclaw-hook-forward.service — keeper of the loopback wake-hook
#     forward; owns the stop-relay/recreate/start-relay dance
#   - nemoclaw-hook-relay.service — bridge relay, gated on the forward
#   - nemoclaw-doctor.timer (5 min) — doctor.sh --fix as a safety net
#   - nemoclaw-terminal.service — embedded-terminal daemon, for the same D-Bus
#     reason: the restricted console's `nemoclaw <sandbox> download/upload/skill
#     install` and POST /wake-agent's `nemoclaw <sandbox> agent` all fail from a
#     system unit. It must NEVER be a system unit; see the template header.
#
# No sudoers drop-in any more: the old scoped systemctl verbs existed only so
# an unprivileged doctor could stop/start the SYSTEM relay unit — the relay
# is a user unit now. (If a recover from the doctor unit ever logs
# "a password is required ... lsof", widen with exact-argv NOPASSWD lines for
# that read-only command only.)
#
# Idempotent — safe to re-run after a repo move (placeholders re-substitute),
# and it migrates a host off the legacy all-system layout in place.
set -euo pipefail

# Writes to /etc/systemd/system — fail in one line with the exact command
# rather than partway through with raw permission errors.
[[ "${EUID:-$(id -u)}" -eq 0 ]] \
  || { echo "must run as root: sudo make install-selfheal" >&2; exit 1; }

cd "$(dirname "$0")/../.."
REPO="$(pwd)"

# The lab user = whoever invoked sudo (owns the repo, .env, nemoclaw state).
LAB_USER="${SUDO_USER:-$(id -un)}"
[[ "$LAB_USER" == "root" ]] && LAB_USER="$(logname 2>/dev/null || id -un)"
LAB_HOME="$(getent passwd "$LAB_USER" | cut -d: -f6)"
[[ -n "$LAB_HOME" ]] || { echo "cannot resolve home for '$LAB_USER'" >&2; exit 1; }
LAB_UID="$(id -u "$LAB_USER")"

# Values the units need — same .env, same reader, same defaults the run
# scripts use. One env_get implementation only (tests/unit/test_envfile.py
# enforces it): local copies have twice drifted from the library and made
# scripts disagree about the same .env.
# shellcheck source=deploy/scripts/lib/envfile.sh
source deploy/scripts/lib/envfile.sh
SANDBOX_NAME="$(env_get SANDBOX_NAME)"; SANDBOX_NAME="${SANDBOX_NAME:-infra-sentinel}"
HOOK_URL="$(env_get OPENCLAW_HOOK_URL)"
HOOK_PORT="${HOOK_URL##*:}"; HOOK_PORT="${HOOK_PORT%%/*}"; HOOK_PORT="${HOOK_PORT:-18790}"
TERMINAL_PORT="$(env_get TERMINAL_PORT)"; TERMINAL_PORT="${TERMINAL_PORT:-8005}"
# GATEWAY_NAME/PORT/UNIT come from the live CLI registry — derived below,
# where as_user exists.

sed_sub() { # $1=template, $2=output — substitute the __NEMOCLAW_*__ placeholders
  sed -e "s|__NEMOCLAW_REPO__|${REPO}|g" \
      -e "s|__NEMOCLAW_USER__|${LAB_USER}|g" \
      -e "s|__NEMOCLAW_HOME__|${LAB_HOME}|g" \
      -e "s|__NEMOCLAW_SANDBOX__|${SANDBOX_NAME}|g" \
      -e "s|__NEMOCLAW_HOOK_PORT__|${HOOK_PORT}|g" \
      -e "s|__NEMOCLAW_GATEWAY_PORT__|${GATEWAY_PORT}|g" \
      -e "s|__NEMOCLAW_GATEWAY_UNIT__|${GATEWAY_UNIT}|g" \
      -e "s|__NEMOCLAW_GATEWAY__|${GATEWAY_NAME}|g" "$1" > "$2"
}

# Run systemctl (or anything) inside the lab user's systemd session. linger
# guarantees the user manager exists without a login session.
loginctl enable-linger "$LAB_USER"
as_user() {
  runuser -u "$LAB_USER" -- env "XDG_RUNTIME_DIR=/run/user/${LAB_UID}" \
    "DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/${LAB_UID}/bus" "$@"
}

# ── Gateway identity (name + port) ───────────────────────────────────────────
# Read it from the CLI's own registry, never construct it: `nemoclaw onboard`
# passes no gateway name, so the CLI registers its default (plain `nemoclaw`)
# on whatever port it chose. Deriving `nemoclaw-<port>` here made every unit
# reference a gateway that does not exist — the keeper died with "Unknown
# gateway 'nemoclaw-18080'" and its Wants= dragged the takeover unit into an
# address-in-use crash-loop (2026-08-31). Prefer the row the CLI marks active
# with `*`; fall back to the first registered gateway.
GATEWAY_LIST="$(as_user "${LAB_HOME}/.local/bin/openshell" gateway list 2>/dev/null \
  | sed -E 's/\x1b\[[0-9;]*m//g' || true)"
# Every grep here is `|| true`: no match is an expected outcome (no gateway
# registered yet) that must reach the fallback below, not trip set -o pipefail.
GATEWAY_ROW="$(grep -E '^[[:space:]]*\*' <<<"$GATEWAY_LIST" | head -1 || true)"
[[ -n "$GATEWAY_ROW" ]] || GATEWAY_ROW="$(grep -E '://' <<<"$GATEWAY_LIST" | head -1 || true)"
GATEWAY_ROW="$(sed -E 's/^[[:space:]]*\*?[[:space:]]*//' <<<"$GATEWAY_ROW")"
GATEWAY_NAME="$(awk '{print $1}' <<<"$GATEWAY_ROW")"
GATEWAY_PORT="$(grep -oE '://[^[:space:]]+' <<<"$GATEWAY_ROW" | grep -oE ':[0-9]+$' | tr -d ':' || true)"

if [[ -n "$GATEWAY_NAME" && -n "$GATEWAY_PORT" ]]; then
  echo "✓ gateway from registry: ${GATEWAY_NAME} (port ${GATEWAY_PORT})"
else
  GATEWAY_NAME="${GATEWAY_NAME:-nemoclaw}"
  GATEWAY_PORT="${GATEWAY_PORT:-$(env_get NEMOCLAW_GATEWAY_PORT)}"
  GATEWAY_PORT="${GATEWAY_PORT:-8080}"
  echo "! could not read the gateway registry — assuming '${GATEWAY_NAME}' on ${GATEWAY_PORT}." >&2
  echo "  If the wake-hook keeper later logs \"Unknown gateway\", re-run this" >&2
  echo "  script once the gateway is up: openshell gateway list" >&2
fi
GATEWAY_UNIT="nemoclaw-gateway-${GATEWAY_PORT}.service"

# ── System units ─────────────────────────────────────────────────────────────
# nemoclaw-terminal is deliberately NOT here — see the header; it is installed
# as a user unit below. Having it in both domains is what caused the boot-time
# port race described in deploy/systemd/user/nemoclaw-terminal.service.
SYSTEM_UNITS=(
  nemoclaw-inference-watchdog.service
  nemoclaw-inference-watchdog.timer
)
for u in "${SYSTEM_UNITS[@]}"; do
  OUT="$(mktemp)"
  sed_sub "deploy/systemd/${u}" "$OUT"
  install -m 0644 "$OUT" "/etc/systemd/system/${u}"
  rm -f "$OUT"
done

# Migration off the legacy all-system layout: doctor + relay moved to the
# user domain (see header). The system relay is stopped LAST, right before
# the user chain starts, so the wake-hook path has no gap (below).
LEGACY_SYSTEM_RELAY=0
if [[ -f /etc/systemd/system/nemoclaw-hook-relay.service ]]; then
  LEGACY_SYSTEM_RELAY=1
fi
if systemctl is-enabled --quiet nemoclaw-doctor.timer 2>/dev/null; then
  systemctl disable --now nemoclaw-doctor.timer >/dev/null 2>&1 || true
fi
rm -f /etc/systemd/system/nemoclaw-doctor.service /etc/systemd/system/nemoclaw-doctor.timer

# The old scoped sudoers drop-in is obsolete (header) — remove it.
rm -f /etc/sudoers.d/nemoclaw-doctor

systemctl daemon-reload
systemctl enable --now nemoclaw-inference-watchdog.timer

# ── User units (the wake-hook chain) ─────────────────────────────────────────
USER_UNIT_DIR="${LAB_HOME}/.config/systemd/user"
install -d -o "$LAB_USER" -g "$LAB_USER" "$USER_UNIT_DIR"

install_user_unit() { # $1=template basename, $2=installed unit name
  local OUT; OUT="$(mktemp)"
  sed_sub "deploy/systemd/user/${1}" "$OUT"
  install -m 0644 -o "$LAB_USER" -g "$LAB_USER" "$OUT" "${USER_UNIT_DIR}/${2}"
  rm -f "$OUT"
}
install_user_unit nemoclaw-hook-forward.service nemoclaw-hook-forward.service
install_user_unit nemoclaw-hook-relay.service   nemoclaw-hook-relay.service
install_user_unit nemoclaw-doctor.service       nemoclaw-doctor.service
install_user_unit nemoclaw-doctor.timer         nemoclaw-doctor.timer
install_user_unit nemoclaw-terminal.service     nemoclaw-terminal.service

# The gateway boot-takeover unit needs the env captured from the running
# gateway process (see the template header) — install only when it exists.
GATEWAY_ENV="${LAB_HOME}/.config/nemoclaw/gateway-${GATEWAY_PORT}.env"
GATEWAY_READY=0
if [[ -f "$GATEWAY_ENV" ]]; then
  install_user_unit nemoclaw-gateway.service "$GATEWAY_UNIT"
  GATEWAY_READY=1
else
  echo "! ${GATEWAY_ENV} missing — skipping ${GATEWAY_UNIT}." >&2
  echo "  Capture it from the running gateway (template header has the command)," >&2
  echo "  then re-run: sudo make install-selfheal" >&2
fi

# Stale hand-written user units this layout supersedes.
# nemoclaw-terminal.service is NOT in this list: it belongs in the user domain
# now (installed just above), and deleting it here was what silently reverted
# the 2026-08-28 incident #2 fix — with it gone, the daemon fell back to a
# system unit, every `nemoclaw <sandbox> …` it runs failed with "Sandbox … does
# not exist", and POST /wake-agent degraded to the messageless wake hook.
for stale in nemoclaw-openshell-gateway.service; do
  if [[ -f "${USER_UNIT_DIR}/${stale}" ]]; then
    as_user systemctl --user disable --now "$stale" >/dev/null 2>&1 || true
    rm -f "${USER_UNIT_DIR}/${stale}"
    echo "✓ removed stale user unit ${stale}"
  fi
done

as_user systemctl --user daemon-reload
[[ "$GATEWAY_READY" -eq 1 ]] && as_user systemctl --user enable "$GATEWAY_UNIT" >/dev/null
as_user systemctl --user enable nemoclaw-hook-forward.service nemoclaw-hook-relay.service >/dev/null
as_user systemctl --user enable --now nemoclaw-doctor.timer >/dev/null

# ── Wake-hook port handoff (legacy system relay -> user chain) ───────────────
# Order is load-bearing: the system relay must release 172.17.0.1:<port>
# BEFORE the user chain starts, and the forward keeper itself frees the port
# number around the forward re-creation (interface-blind port check).
if [[ "$LEGACY_SYSTEM_RELAY" -eq 1 ]]; then
  systemctl disable --now nemoclaw-hook-relay.service >/dev/null 2>&1 || true
  rm -f /etc/systemd/system/nemoclaw-hook-relay.service
  systemctl daemon-reload
  echo "✓ migrated: system hook-relay unit removed (now a user unit)"
fi
# (Re)start the keeper: it stops the user relay, clears any stale forward
# record, recreates the loopback forward, and starts the relay again.
as_user systemctl --user restart nemoclaw-hook-forward.service || \
  echo "✗ forward keeper failed to start — journalctl --user -u nemoclaw-hook-forward -e" >&2

# ── Terminal daemon: never fight a nohup'd demo instance for the port ────────
# Retire the legacy SYSTEM copy first, and before the port check below: on a
# host that ran an older installer it is enabled and holding TERMINAL_PORT, so
# installing the user unit alongside it puts two daemons in two domains on one
# port — the boot race the template header warns about. Stopping it here frees
# the port for the user unit that starts a moment later.
if [[ -f /etc/systemd/system/nemoclaw-terminal.service ]]; then
  systemctl disable --now nemoclaw-terminal.service >/dev/null 2>&1 || true
  rm -f /etc/systemd/system/nemoclaw-terminal.service
  systemctl daemon-reload
  echo "✓ migrated: system terminal unit removed (now a user unit)"
fi

port_held() { ss -tln 2>/dev/null | awk '{print $4}' | grep -qE "[:.]${1}$"; }
if port_held "$TERMINAL_PORT"; then
  if as_user systemctl --user is-active --quiet nemoclaw-terminal; then
    echo "✓ nemoclaw-terminal already running"
  else
    as_user systemctl --user enable nemoclaw-terminal >/dev/null
    echo "✓ nemoclaw-terminal enabled (port ${TERMINAL_PORT} held by a non-systemd instance — it keeps serving; the unit takes over at next boot, or: systemctl --user restart nemoclaw-terminal)"
  fi
else
  as_user systemctl --user enable --now nemoclaw-terminal >/dev/null
  sleep 1
  if as_user systemctl --user is-active --quiet nemoclaw-terminal; then
    echo "✓ nemoclaw-terminal started"
  else
    echo "✗ nemoclaw-terminal failed to start — journalctl --user -u nemoclaw-terminal -e" >&2
  fi
fi

echo
echo "✓ self-heal layer installed (lab user: ${LAB_USER}, repo: ${REPO}):"
systemctl list-timers --no-pager | grep -E "nemoclaw|Elapsed" || true
as_user systemctl --user list-timers --no-pager | grep -E "nemoclaw|Elapsed" || true
echo
echo "Watch:  journalctl -f -u nemoclaw-inference-watchdog"
echo "        journalctl --user -f -u nemoclaw-doctor -u nemoclaw-hook-forward -u nemoclaw-hook-relay"
