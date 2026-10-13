#!/usr/bin/env bash
# Start {bob.name} under its own Claude Code config dir, so its login, plugins, memory and loops
# never mix with another Bob's on the same machine.
set -euo pipefail
cd "$(dirname "$0")"
export CLAUDE_CONFIG_DIR="$HOME/.claude-{bob.slug}"
exec claude "$@"
