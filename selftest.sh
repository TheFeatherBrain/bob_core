#!/usr/bin/env bash
# Every module selftest and every test suite, against the example manifest.
set -uo pipefail
cd "$(dirname "$0")"
export BOB_MANIFEST="$PWD/bob.example.toml"
export CLAUDE_CONFIG_DIR="$(mktemp -d)"
failed=0
run() {
  if "$@" > /dev/null 2>&1; then echo "ok   $*"; else echo "FAIL $*"; failed=1; fi
}
for f in harness/*.py harness/hooks/*.py; do
  grep -qE "add_argument\(['\"]--selftest|['\"]--selftest['\"] in " "$f" && run python3 "$f" --selftest
done
run python3 harness/test_bobcfg.py
run python3 harness/test_board_shell.py
run python3 harness/test_envfile.py
run python3 harness/test_loop_passes.py
run python3 harness/test_route.py
run python3 harness/test_slack_state.py
run python3 harness/test_skills.py
run python3 harness/test_state_home_wiring.py
run python3 harness/test_text_encoding.py
run python3 harness/hooks/test_gates.py
run python3 automation/test_new_bob.py
rm -rf "$CLAUDE_CONFIG_DIR"
exit $failed
