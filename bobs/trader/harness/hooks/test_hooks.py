#!/usr/bin/env python3
"""The Trader's gates, driven the way Claude Code drives them; every refusal paired with an allow.

Run:  python3 harness/hooks/test_hooks.py
"""
from __future__ import annotations

import json
import os
import pathlib
import re
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import traderpath  # noqa: E402,F401
import testkit  # noqa: E402


def run_gate(script: str, payload, bob: pathlib.Path | None = None) -> dict | None:
    bob = bob or testkit.temp_bob()
    data = payload if isinstance(payload, str) else json.dumps(payload)
    out = subprocess.run([sys.executable, str(HERE / script)], input=data, env=dict(os.environ), cwd=bob,
                         capture_output=True, text=True, encoding="utf-8", timeout=30)
    assert out.returncode == 0, f"{script} exited {out.returncode}: {out.stderr}"
    return json.loads(out.stdout) if out.stdout.strip() else None


def denied(decision) -> bool:
    return bool(decision) and decision["hookSpecificOutput"]["permissionDecision"] == "deny"


def call(tool: str, **tool_input) -> dict:
    return {"tool_name": tool, "tool_input": tool_input}


def test_protect_denies_ledger_manifest_hooks_and_halt_removal():
    for payload in (call("Edit", file_path="data/state/ledger.jsonl"), call("Write", file_path="bob.toml", content="x"),
                    call("Edit", file_path="harness/hooks/order_gate.py"),
                    call("Bash", command="rm data/state/HALT"), call("Bash", command="echo x >> data/state/ledger.jsonl"),
                    call("Edit", file_path="data/state/sim/broker.json")):
        assert denied(run_gate("protect_gate.py", payload)), payload


def test_protect_allows_reads_clis_and_notes():
    for payload in (call("Bash", command="cat data/state/ledger.jsonl"), call("Bash", command="python3 harness/trade.py --run"),
                    call("Bash", command="python3 harness/halt.py --reason 'vix spike'"),
                    call("Write", file_path="docs/TRACKER.md", content="x"), call("Write", file_path="skills/x.md", content="x")):
        assert not denied(run_gate("protect_gate.py", payload)), payload


def test_order_gate_denies_broker_traffic_adapters_and_person_only_clis():
    for payload in (call("Bash", command="curl -X POST https://paper-api.alpaca.markets/v2/orders"),
                    call("WebFetch", url="https://api.alpaca.markets/v2/account", prompt="x"),
                    call("mcp__alpaca__place_stock_order", symbol="AAA"),
                    call("Bash", command="python3 harness/broker/sim.py"),
                    call("Bash", command="python3 -c 'from broker import sim; sim.Simulator(\"simulate\").submit({})'"),
                    call("Write", file_path="tmp.py", content="from broker import sim\n"),
                    call("Bash", command="python3 harness/approve.py 2026-01-08-scout_calls-buy-AAA"),
                    call("Bash", command="python3 harness/ledger.py correct --note x --cash-delta 5")):
        assert denied(run_gate("order_gate.py", payload)), payload


def test_order_gate_allows_the_daily_run_halt_and_reads():
    for payload in (call("Bash", command="python3 harness/trade.py --run"),
                    call("Bash", command="python3 harness/halt.py --reason x"),
                    call("Bash", command="python3 harness/ledger.py summary --days 7"),
                    call("Bash", command="curl 'https://data.alpaca.markets/v2/stocks/bars?symbols=SPY'"),
                    call("Edit", file_path="docs/TRACKER.md", old_string="a", new_string="b")):
        assert not denied(run_gate("order_gate.py", payload)), payload


def test_notes_may_name_person_only_clis_and_adapters():
    note = "person: please run approve.py 2026-01-08-scout_calls-buy-AAA; see harness/broker/sim.py"
    for payload in (call("Write", file_path="docs/TRACKER.md", content=note),
                    call("Edit", file_path="skills/lesson.md", old_string="a", new_string=note)):
        assert not denied(run_gate("order_gate.py", payload)), payload


def test_both_gates_fail_closed():
    assert denied(run_gate("protect_gate.py", "not json"))
    assert denied(run_gate("order_gate.py", "{broken"))


def test_gates_import_only_stdlib_at_module_level():
    import ast
    for script in ("protect_gate.py", "order_gate.py"):
        tree = ast.parse((HERE / script).read_text(encoding="utf-8"))
        names = {a.name.split(".")[0] for n in tree.body if isinstance(n, (ast.Import, ast.ImportFrom)) for a in n.names}
        names |= {n.module.split(".")[0] for n in tree.body if isinstance(n, ast.ImportFrom) and n.module}
        assert names <= {"__future__", "annotations", "json", "os", "pathlib", "re", "sys"}, (script, names)


def test_settings_register_both_gates_for_files_and_shells():
    settings = json.loads((traderpath.BOB / ".claude" / "settings.json").read_text(encoding="utf-8"))
    for script in ("protect_gate.py", "order_gate.py"):
        matchers = [e["matcher"] for e in settings["hooks"]["PreToolUse"] if any(script in h["command"] for h in e["hooks"])]
        assert matchers and all(re.fullmatch(matchers[0], t) for t in ("Write", "Edit", "Bash", "PowerShell")), (script, matchers)


if __name__ == "__main__":
    raise SystemExit(testkit.run(globals()))
