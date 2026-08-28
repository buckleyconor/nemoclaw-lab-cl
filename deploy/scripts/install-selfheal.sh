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
#   - nemoclaw-terminal.service (lab user, Restart=on-failure)
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

cd "$(dirname "$0")/../.."
REPO="$(pwd)"

# The lab user = whoever invoked sudo (owns the repo, .env, nemoclaw state).
LAB_USER="${SUDO_USER:-$(id -un)}"
[[ "$LAB_USER" == "root" ]] && LAB_USER="$(logname 2>/dev/null || id -un)"
LAB_HOME="$(getent passwd "$LAB_USER" | cut -d: -f6)"
[[ -n "$LAB_HOME" ]] || { echo "cannot resolve home for '$LAB_USER'" >&2; exit 1; }
LAB_UID="$(id -u "$LAB_USER")"

# Values the units need — same .env, same defaults the run scripts use.
env_get() { { grep -E "^$1=" .env 2>/dev/null || true; } | head -1 | cut -d= -f2- | sed -E 's/[[:space:]]+#.*$//'; }
SANDBOX_NAME="$(env_get SANDBOX_NAME)"; SANDBOX_NAME="${SANDBOX_NAME:-infra-sentinel}"
HOOK_URL="$(env_get OPENCLAW_HOOK_URL)"
HOOK_PORT="${HOOK_URL##*:}"; HOOK_PORT="${HOOK_PORT%%/*}"; HOOK_PORT="${HOOK_PORT:-18790}"
TERMINAL_PORT="$(env_get TERMINAL_PORT)"; TERMINAL_PORT="${TERMINAL_PORT:-8005}"
GATEWAY_PORT="$(env_get NEMOCLAW_GATEWAY_PORT)"; GATEWAY_PORT="${GATEWAY_PORT:-18080}"
GATEWAY_NAME="nemoclaw-${GATEWAY_PORT}"
GATEWAY_UNIT="nemoclaw-gateway-${GATEWAY_PORT}.service"

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

# ── System units ─────────────────────────────────────────────────────────────
SYSTEM_UNITS=(
  nemoclaw-inference-watchdog.service
  nemoclaw-inference-watchdog.timer
  nemoclaw-terminal.service
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

# Stale hand-written user units this layout supersedes. The terminal daemon
# is a SYSTEM unit; nemoclaw-openshell-gateway.service was an untagged unit
# that spawns a rogue DEFAULT gateway (empty DB) — never enable it.
for stale in nemoclaw-openshell-gateway.service nemoclaw-terminal.service; do
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
port_held() { ss -tln 2>/dev/null | awk '{print $4}' | grep -qE "[:.]${1}$"; }
if port_held "$TERMINAL_PORT"; then
  if systemctl is-active --quiet nemoclaw-terminal; then
    echo "✓ nemoclaw-terminal already running"
  else
    systemctl enable nemoclaw-terminal >/dev/null
    echo "✓ nemoclaw-terminal enabled (port ${TERMINAL_PORT} held by a non-systemd instance — it keeps serving; the unit takes over at next boot, or: sudo systemctl restart nemoclaw-terminal)"
  fi
else
  systemctl enable --now nemoclaw-terminal >/dev/null
  sleep 1
  if systemctl is-active --quiet nemoclaw-terminal; then
    echo "✓ nemoclaw-terminal started"
  else
    echo "✗ nemoclaw-terminal failed to start — journalctl -u nemoclaw-terminal -e" >&2
  fi
fi

echo
echo "✓ self-heal layer installed (lab user: ${LAB_USER}, repo: ${REPO}):"
systemctl list-timers --no-pager | grep -E "nemoclaw|Elapsed" || true
as_user systemctl --user list-timers --no-pager | grep -E "nemoclaw|Elapsed" || true
echo
echo "Watch:  journalctl -f -u nemoclaw-inference-watchdog"
echo "        journalctl --user -f -u nemoclaw-doctor -u nemoclaw-hook-forward -u nemoclaw-hook-relay"
