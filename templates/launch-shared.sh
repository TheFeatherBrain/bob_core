#!/usr/bin/env bash
# Start {bob.name} in its own folder, under the Claude Code config dir it shares with other Bobs
# on this machine. It inherits their logins; its state files live under
# <config dir>/{runtime.state_subdir}, so they never mix with another Bob's.
set -euo pipefail
cd "$(dirname "$0")"
exec claude "$@"
