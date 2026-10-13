#!/usr/bin/env python3
"""The Bob's own gates, driven the way Claude Code drives them; every refusal paired with an allow.

Run:  python3 harness/hooks/test_hooks.py
"""
from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import scoutpath  # noqa: E402,F401
import testkit  # noqa: E402


def run_gate(script: str, payload, bob: pathlib.Path | None = None) -> dict | None:
    bob = bob or testkit.temp_bob()
    data = payload if isinstance(payload, str) else json.dumps(payload)
    out = subprocess.run([sys.executable, str(HERE / script)], input=data, env=dict(os.environ), cwd=bob,
                         capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, f"{script} exited {out.returncode}: {out.stderr}"
    return json.loads(out.stdout) if out.stdout.strip() else None


def denied(decision) -> bool:
    return bool(decision) and decision["hookSpecificOutput"]["permissionDecision"] == "deny"


def call(tool: str, **tool_input) -> dict:
    return {"tool_name": tool, "tool_input": tool_input}


def test_edit_on_ledger_denied():
    assert denied(run_gate("protect_gate.py", call("Edit", file_path="data/state/calls.jsonl")))


def test_edit_on_thesis_allowed():
    assert not denied(run_gate("protect_gate.py", call("Edit", file_path="theses/NVDA.md")))


def test_write_on_manifest_denied():
    assert denied(run_gate("protect_gate.py", call("Write", file_path="bob.toml", content="x")))


def test_edit_on_own_hooks_denied():
    assert denied(run_gate("protect_gate.py", call("Edit", file_path="harness/hooks/protect_gate.py")))


def test_edit_on_evidence_by_absolute_path_denied():
    bob = testkit.temp_bob()
    target = bob / "data" / "evidence" / "2026-10-13" / "x" / "raw.txt"
    assert denied(run_gate("protect_gate.py", call("Write", file_path=str(target), content="x"), bob))


def test_shell_append_to_ledger_denied():
    assert denied(run_gate("protect_gate.py", call("Bash", command="echo '{}' >> data/state/calls.jsonl")))


def test_shell_read_of_ledger_allowed():
    assert not denied(run_gate("protect_gate.py", call("Bash", command="cat data/state/calls.jsonl")))


def test_shell_cli_that_writes_state_allowed():
    assert not denied(run_gate("protect_gate.py",
                               call("Bash", command="python3 harness/calls.py verdict NVDA --verdict watch --lane numbers")))


def test_powershell_remove_evidence_denied():
    assert denied(run_gate("protect_gate.py", call("PowerShell", command="Remove-Item data\\evidence\\2026-10-13 -Recurse")))


def test_protect_gate_fails_closed():
    assert denied(run_gate("protect_gate.py", "not json"))


def test_shell_write_bypasses_denied():
    for command in ("echo x &> data/state/calls.jsonl", "echo x 1> data/state/calls.jsonl",
                    "cd data && echo x >> state/calls.jsonl", "echo x > DATA/state/calls.jsonl",
                    "echo x > data/./state/calls.jsonl", "echo x | tee -a data/state/calls.jsonl",
                    "python3 - <<EOF\nopen('data/state/calls.jsonl', 'a').write('x')\nEOF",
                    "perl -pi -e 's/0.30/0.50/' bob.toml", "sed --in-place 's/a/b/' bob.toml",
                    "echo pass > harness/calls.py", "node -e \"require('fs').writeFileSync('bob.toml','')\"",
                    "ls; rm -f data/state/calls.jsonl", "python3 harness/calls.py list && cp x.py harness/calls.py",
                    "cd harness && rm calls.py", "cd data/evidence; rm -rf 2026-10-13",
                    "cd ../../harness && sed -i 's/a/b/' bobcfg.py"):
        assert denied(run_gate("protect_gate.py", call("Bash", command=command))), command


def test_ordinary_shell_work_allowed():
    for command in ("python3 harness/screen.py --run > run.log", "cat data/state/calls.jsonl | head -5",
                    "echo 'screen OK' >> docs/TRACKER.md", "python3 harness/calls.py list 2>&1 | tail -3",
                    "rm /tmp/acme.txt && python3 harness/calls.py list",
                    "cp /tmp/income_statement.txt /tmp/b", "rm /tmp/evidence-notes.txt",
                    "cd docs && echo 'note' >> TRACKER.md", "cd harness && python3 screen.py --card NVDA"):
        assert not denied(run_gate("protect_gate.py", call("Bash", command=command))), command


def test_settings_code_and_core_denied_by_path():
    bob = testkit.temp_bob()
    for target in (".claude/settings.local.json", "harness/scoutcfg.py", "harness/fundamentals/quality.py",
                   str(scoutpath.CORE / "harness" / "oscompat.py"), str(bob / "config" / "settings.json")):
        assert denied(run_gate("protect_gate.py", call("Edit", file_path=target), bob)), target


def test_notes_lessons_and_tracker_allowed_by_path():
    for target in ("docs/TRACKER.md", "skills/new-lesson.md", "theses/AAPL.md"):
        assert not denied(run_gate("protect_gate.py", call("Write", file_path=target, content="x"))), target


def test_gates_import_only_stdlib_at_module_level():
    import ast
    for script in ("protect_gate.py", "order_gate.py"):
        tree = ast.parse((HERE / script).read_text(encoding="utf-8"))
        names = {a.name.split(".")[0] for n in tree.body if isinstance(n, (ast.Import, ast.ImportFrom))
                 for a in n.names} | {n.module.split(".")[0] for n in tree.body
                                      if isinstance(n, ast.ImportFrom) and n.module}
        assert names <= {"__future__", "annotations", "json", "os", "pathlib", "re", "sys"}, (script, names)


def test_order_gate_also_sees_file_writes():
    import re
    settings = json.loads((scoutpath.BOB / ".claude" / "settings.json").read_text(encoding="utf-8"))
    matchers = [e["matcher"] for e in settings["hooks"]["PreToolUse"]
                if any("order_gate.py" in h["command"] for h in e["hooks"])]
    assert matchers and all(re.fullmatch(matchers[0], tool) for tool in ("Write", "Edit", "MultiEdit", "Bash")), matchers


def test_order_url_in_a_written_file_denied():
    assert denied(run_gate("order_gate.py", call("Write", file_path="tmp.py",
                                                 content="requests.post('https://paper-api.alpaca.markets/v2/orders')")))
    assert not denied(run_gate("order_gate.py", call("Write", file_path="theses/NVDA.md", content="# NVDA")))


def test_order_endpoint_denied():
    assert denied(run_gate("order_gate.py", call("Bash", command=(
        "curl -X POST https://paper-api.alpaca.markets/v2/orders -d '{\"symbol\":\"NVDA\"}'"))))


def test_live_host_denied():
    assert denied(run_gate("order_gate.py", call("WebFetch", url="https://api.alpaca.markets/v2/account", prompt="x")))


def test_alpaca_mcp_tool_denied():
    assert denied(run_gate("order_gate.py", call("mcp__alpaca__place_stock_order", symbol="NVDA")))


def test_data_and_calendar_reads_allowed():
    assert not denied(run_gate("order_gate.py", call("Bash", command=(
        "curl 'https://data.alpaca.markets/v2/stocks/bars?symbols=SPY&timeframe=1Day'"))))
    assert not denied(run_gate("order_gate.py", call("WebFetch", url="https://paper-api.alpaca.markets/v2/calendar", prompt="x")))


def test_order_gate_fails_closed():
    assert denied(run_gate("order_gate.py", "{broken"))


if __name__ == "__main__":
    raise SystemExit(testkit.run(globals()))
