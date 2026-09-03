#!/usr/bin/env bash
# Install + enable the inference-proxy watchdog timer (ADR-014).
#
# Run as root (the make target runs it with sudo):
#   sudo ./deploy/scripts/install-watchdog.sh      (or: sudo make install-inference-watchdog)
#
# Idempotent — safe to re-run after a repo move (re-substitutes the path).
set -euo pipefail

# Writes to /etc/systemd/system — fail in one line with the exact command
# rather than partway through with raw permission errors.
[[ "${EUID:-$(id -u)}" -eq 0 ]] \
  || { echo "must run as root: sudo make install-inference-watchdog" >&2; exit 1; }

cd "$(dirname "$0")/../.."
REPO="$(pwd)"
for u in nemoclaw-inference-watchdog.service nemoclaw-inference-watchdog.timer; do
  sed "s|__NEMOCLAW_REPO__|${REPO}|g" "deploy/systemd/${u}" > "/tmp/${u}.nemoclaw"
  install -m 0644 "/tmp/${u}.nemoclaw" "/etc/systemd/system/${u}"
  rm -f "/tmp/${u}.nemoclaw"
done

systemctl daemon-reload
systemctl enable --now nemoclaw-inference-watchdog.timer

echo "✓ inference watchdog installed and enabled (${REPO}):"
systemctl list-timers --no-pager | grep -E "inference-watchdog|Elapsed" || true
echo
echo "Watch:  journalctl -f -u nemoclaw-inference-watchdog"
