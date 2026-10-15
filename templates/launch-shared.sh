#!/usr/bin/env bash
# Start {bob.name} in its own folder, under the Claude Code config dir it shares with other Bobs
# on this machine. It inherits their logins; its state files live under
# <config dir>/{runtime.state_subdir}, so they never mix with another Bob's.
set -euo pipefail
cd "$(dirname "$0")"
# Secrets from [runtime] env_file, if set. envfile.py validates the file first, so each line is
# plain KEY=value; it is read, never sourced, and a variable already set wins.
env_file="$(python3 "{core}/harness/envfile.py" --path)"
if [ -n "$env_file" ]; then
  while IFS= read -r line || [ -n "$line" ]; do
    line="${line%$'\r'}"
    case "$line" in ''|'#'*) continue ;; esac
    key="${line%%=*}"
    [ -n "${!key+x}" ] || export "$line"
  done < "$env_file"
fi
exec claude "$@"
