#!/usr/bin/env bash
# Every test suite and module selftest of this Bob. Tests build their own temp Bob; no network.
set -uo pipefail
cd "$(dirname "$0")"
export CLAUDE_CONFIG_DIR="$(mktemp -d)"
failed=0
run() {
  if "$@" > /dev/null 2>&1; then echo "ok   $*"; else echo "FAIL $*"; failed=1; fi
}
for f in harness/test_*.py harness/hooks/test_*.py; do
  [ -e "$f" ] && run python3 "$f"
done
for f in harness/*.py harness/hooks/*.py; do
  case "$f" in */test_*) continue ;; esac
  [ -e "$f" ] && grep -qE "add_argument\(['\"]--selftest['\"]" "$f" && run python3 "$f" --selftest
done
rm -rf "$CLAUDE_CONFIG_DIR"
exit $failed
