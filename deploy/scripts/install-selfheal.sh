#!/usr/bin/env bash
# Install + enable the NemoClaw self-heal layer (run as root / sudo):
#
#   sudo ./deploy/scripts/install-selfheal.sh      (or: sudo make install-selfheal)
#
# What gets installed (system units under /etc/systemd/system/):
#   - nemoclaw-inference-watchdog.timer (root, 60s) — restarts nginx when the
#     inference proxy is down or running stale sockets (the 2026-08-28
#     boot-race / no-op-reload failure class)
#   - nemoclaw-doctor.timer (5 min, runs as the lab user) — doctor.sh --fix:
#     compose services, wake-hook forward (nemoclaw recover), model drift
#   - nemoclaw-terminal.service (lab user, Restart=on-failure)
#   - nemoclaw-hook-relay.service (lab user, Restart=on-failure)
#
# Idempotent — safe to re-run after a repo move (placeholders re-substitute).
# Existing nohup'd daemons (from `make demo-up`) are left alone: if their
# port is already held, the unit is enabled for the next boot only — the two
# never fight over a port.
set -euo pipefail

cd "$(dirname "$0")/../.."
REPO="$(pwd)"

# The lab user = whoever invoked sudo (owns the repo, .env, nemoclaw state).
LAB_USER="${SUDO_USER:-$(id -un)}"
[[ "$LAB_USER" == "root" ]] && LAB_USER="$(logname 2>/dev/null || id -un)"
LAB_HOME="$(getent passwd "$LAB_USER" | cut -d: -f6)"
[[ -n "$LAB_HOME" ]] || { echo "cannot resolve home for '$LAB_USER'" >&2; exit 1; }

# Values the units need — same .env, same defaults the run scripts use.
env_get() { { grep -E "^$1=" .env 2>/dev/null || true; } | head -1 | cut -d= -f2- | sed -E 's/[[:space:]]+#.*$//'; }
SANDBOX_NAME="$(env_get SANDBOX_NAME)"; SANDBOX_NAME="${SANDBOX_NAME:-infra-sentinel}"
HOOK_URL="$(env_get OPENCLAW_HOOK_URL)"
HOOK_PORT="${HOOK_URL##*:}"; HOOK_PORT="${HOOK_PORT%%/*}"; HOOK_PORT="${HOOK_PORT:-18790}"
TERMINAL_PORT="$(env_get TERMINAL_PORT)"; TERMINAL_PORT="${TERMINAL_PORT:-8005}"

sed_sub() { # $1=template, $2=output — substitute the __NEMOCLAW_*__ placeholders
  sed -e "s|__NEMOCLAW_REPO__|${REPO}|g" \
      -e "s|__NEMOCLAW_USER__|${LAB_USER}|g" \
      -e "s|__NEMOCLAW_HOME__|${LAB_HOME}|g" \
      -e "s|__NEMOCLAW_SANDBOX__|${SANDBOX_NAME}|g" \
      -e "s|__NEMOCLAW_HOOK_PORT__|${HOOK_PORT}|g" "$1" > "$2"
}

UNITS=(
  nemoclaw-inference-watchdog.service
  nemoclaw-inference-watchdog.timer
  nemoclaw-doctor.service
  nemoclaw-doctor.timer
  nemoclaw-terminal.service
  nemoclaw-hook-relay.service
)
for u in "${UNITS[@]}"; do
  OUT="$(mktemp)"
  sed_sub "deploy/systemd/${u}" "$OUT"
  install -m 0644 "$OUT" "/etc/systemd/system/${u}"
  rm -f "$OUT"
done

systemctl daemon-reload

# Timers: no port conflicts possible — enable + start unconditionally.
systemctl enable --now nemoclaw-inference-watchdog.timer
systemctl enable --now nemoclaw-doctor.timer

# Long-running daemons: never fight a nohup'd demo instance for the port.
port_held() { ss -tln 2>/dev/null | awk '{print $4}' | grep -qE "[:.]${1}$"; }

ensure_daemon() { # $1=unit, $2=port
  if port_held "$2"; then
    if systemctl is-active --quiet "$1"; then
      echo "✓ $1 already running"
    else
      systemctl enable "$1"
      echo "✓ $1 enabled (port ${2} held by a non-systemd instance — it keeps serving; the unit takes over at next boot, or: sudo systemctl restart $1)"
    fi
  else
    systemctl enable --now "$1"
    sleep 1
    if systemctl is-active --quiet "$1"; then
      echo "✓ $1 started"
    else
      echo "✗ $1 failed to start — journalctl -u $1 -e" >&2
    fi
  fi
}
ensure_daemon nemoclaw-terminal "$TERMINAL_PORT"
ensure_daemon nemoclaw-hook-relay "$HOOK_PORT"

echo
echo "✓ self-heal layer installed (lab user: ${LAB_USER}, repo: ${REPO}):"
systemctl list-timers --no-pager | grep -E "nemoclaw|Elapsed" || true
echo
echo "Watch:  journalctl -f -u nemoclaw-inference-watchdog -u nemoclaw-doctor"
