#!/usr/bin/env bash
# Start Tipster under its own Claude Code config dir, so its login, plugins, memory and loops
# never mix with another Bob's on the same machine.
set -euo pipefail
cd "$(dirname "$0")"
export CLAUDE_CONFIG_DIR="$HOME/.claude-tipster"
# Secrets from [runtime] env_file, if set. envfile.py validates the file first, so each line is
# plain KEY=value; it is read, never sourced, and a variable already set wins.
env_file="$(python3 "../../harness/envfile.py" --path)"
if [ -n "$env_file" ]; then
  while IFS= read -r line || [ -n "$line" ]; do
    line="${line%$'\r'}"
    case "$line" in ''|'#'*) continue ;; esac
    key="${line%%=*}"
    [ -n "${!key+x}" ] || export "$line"
  done < "$env_file"
fi
exec claude "$@"
