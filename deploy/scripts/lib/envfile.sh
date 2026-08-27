#!/usr/bin/env bash
# Shared .env read/write helpers, sourced by the deploy scripts.
#
# Exists because onboarding used to hand secrets to the operator through the
# clipboard: run-terminal.sh printed TERMINAL_WS_URL/TERMINAL_TOKEN and
# onboard-openclaw.sh printed OPENCLAW_HOOK_URL/OPENCLAW_HOOK_TOKEN, and a
# fresh setup only worked if every one of them was pasted into .env correctly.
# The scripts already know the values, so they write them.
#
# Usage:
#   source "$(dirname "$0")/lib/envfile.sh"
#   env_get KEY [FILE]              -> prints the value, empty if unset
#   env_upsert KEY VALUE [FILE]     -> sets KEY=VALUE, replacing in place
#   env_set_checked KEY VALUE [FILE] -> upsert, but never silently overwrite a
#                                       differing operator-set value
#   bridge_ip                        -> prints the docker bridge IP

# Read a key. Ignores commented-out lines, so a `# KEY=...` placeholder from
# .env.example reads as unset (which is what it means).
env_get() {
  local key="$1" file="${2:-.env}"
  [[ -f "$file" ]] || return 0
  # `|| true`: grep exits 1 on no match, which would kill a `set -e` caller.
  { grep -E "^${key}=" "$file" 2>/dev/null || true; } | head -1 | cut -d= -f2-
}

# Set a key, preserving file order, comments and permissions.
#
# awk rather than `sed -i s/…/…/`: values here are URLs and hex tokens
# containing `/` and `:`, which would need escaping in a sed replacement, and
# `sed -i` on some platforms replaces the file (losing mode/ownership). Writing
# through `cat >` keeps the original inode and its 0600-ish permissions — .env
# holds secrets.
#
# A commented placeholder (`# KEY=…`, as shipped in .env.example) is replaced
# in place rather than leaving a duplicate below it.
env_upsert() {
  local key="$1" value="$2" file="${3:-.env}"
  [[ -f "$file" ]] || : > "$file"
  local tmp
  tmp="$(mktemp)"
  ENV_KEY="$key" ENV_VALUE="$value" awk '
    BEGIN { key = ENVIRON["ENV_KEY"]; value = ENVIRON["ENV_VALUE"]; done = 0 }
    !done && $0 ~ "^" key "="            { print key "=" value; done = 1; next }
    !done && $0 ~ "^#[[:space:]]*" key "=" { print key "=" value; done = 1; next }
                                         { print }
    END { if (!done) print key "=" value }
  ' "$file" > "$tmp"
  cat "$tmp" > "$file"
  rm -f "$tmp"
}

# Upsert, but refuse to clobber a value the operator set to something else.
# Returns 0 if written or already correct, 1 if left alone because it differed
# (caller decides how loudly to complain).
env_set_checked() {
  local key="$1" value="$2" file="${3:-.env}"
  local current
  current="$(env_get "$key" "$file")"
  if [[ -n "$current" && "$current" != "$value" ]]; then
    echo "  ! ${key} already set to '${current}' — leaving it alone." >&2
    echo "    (wanted '${value}'; edit ${file} by hand if that is wrong)" >&2
    return 1
  fi
  env_upsert "$key" "$value" "$file"
  return 0
}

# The docker bridge IP the host daemons bind/answer on. 172.17.0.1 is docker's
# stock default, but it is configurable (daemon.json "bip") — detect rather
# than assume, falling back to the default when docker0 isn't up yet.
bridge_ip() {
  local ip
  ip="$(ip -4 -o addr show docker0 2>/dev/null | awk '{print $4}' | cut -d/ -f1 | head -1)"
  echo "${ip:-172.17.0.1}"
}
