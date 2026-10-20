#!/usr/bin/env python3
"""The Tipster's gates, driven the way Claude Code drives them; every refusal paired with an allow.

Run:  python3 harness/hooks/test_hooks.py
"""
from __future__ import annotations

import ast
import json
import os
import pathlib
import re
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import tipsterpath  # noqa: E402,F401
import testkit  # noqa: E402

BOOKMAKERS = ("bet365.com", "www.stoiximan.gr", "betano.gr", "pamestoixima.gr", "novibet.gr", "pinnacle.com",
              "sports.bwin.com", "interwetten.com")


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


PROTECTED = ("data/state/tips.jsonl", "data/state/odds-requests.jsonl", "data/state/HALT", "data/state/calibration.json",
             "data/state/model_params.json", "data/state/outbox.jsonl", "data/state/candidates.json",
             "data/evidence/2026-10-21/x/raw.txt", "harness/review.py", "harness/hooks/network_gate.py",
             ".claude/settings.json", "bob.toml")


def test_protect_denies_edits_and_redirects_to_each_protected_path():
    for path in PROTECTED:
        assert denied(run_gate("protect_gate.py", call("Edit", file_path=path, old_string="a", new_string="b"))), path
        assert denied(run_gate("protect_gate.py", call("Bash", command=f"echo x >> {path}"))), path
    for payload in (call("Bash", command="rm data/state/HALT"), call("PowerShell", command="Remove-Item data/state/HALT"),
                    call("Bash", command="python3 -c \"open('tips.jsonl', 'a')\""),
                    call("Write", file_path=str(tipsterpath.CORE / "harness" / "telegram.py"), content="x")):
        assert denied(run_gate("protect_gate.py", payload)), payload


def test_protect_denies_ledger_writes_through_imports():
    for payload in (call("Bash", command="python3 -c \"import tips; tips.record_tip({'stake': 999})\""),
                    call("Bash", command="python3 -c 'import store; store.append_jsonl(p, rows)'"),
                    call("PowerShell", command="python -c \"from budget import spend\""),
                    call("Bash", command="cd harness && python3 - <<'EOF'\nimport notify\nnotify.queue('tip', 'x', 'y')\nEOF"),
                    call("Write", file_path="tmp.py", content="import tips\ntips.append({'kind': 'settled'})\n"),
                    call("Write", file_path="scratch/fix.py", content="from store import append_jsonl\n")):
        assert denied(run_gate("protect_gate.py", payload)), payload


def test_protect_allows_reads_clis_and_notes():
    for payload in (call("Bash", command="cat data/state/tips.jsonl"),
                    call("Bash", command="python3 harness/review.py c-1 --keep --reason 'full strength' --evidence data/evidence/x/raw.txt"),
                    call("Bash", command="python3 harness/recheck.py t-E2026-69-side-reviewed --no-change --reason x --evidence e"),
                    call("Bash", command="python3 harness/halt.py --reason 'wallet'"),
                    call("Bash", command="python3 harness/discover.py --run > discover.log"),
                    call("Write", file_path="docs/TRACKER.md", content="x"), call("Write", file_path="skills/x.md", content="x"),
                    call("Write", file_path="docs/notes.md", content="never `import tips` by hand"),
                    call("Bash", command="python3 -c \"import json; print(json.load(open('data/state/calibration.json'))['n'])\"")):
        assert not denied(run_gate("protect_gate.py", payload)), payload


def test_network_refuses_every_bookmaker_in_webfetch_and_shell():
    for host in BOOKMAKERS:
        assert denied(run_gate("network_gate.py", call("WebFetch", url=f"https://{host}/basketball", prompt="odds"))), host
        assert denied(run_gate("network_gate.py", call("Bash", command=f"curl -s https://{host}/odds"))), host
        assert denied(run_gate("network_gate.py", call("PowerShell", command=f"Invoke-WebRequest https://{host}/"))), host
    assert denied(run_gate("network_gate.py", call("WebSearch", query="euroleague odds", allowed_domains=["bet365.com"])))
    assert denied(run_gate("network_gate.py", call("WebSearch", query="site:stoiximan.gr olympiacos")))


def test_network_refuses_direct_odds_and_telegram_calls():
    for payload in (call("Bash", command="curl 'https://api.oddspapi.io/v4/odds?apiKey=$ODDS_API_KEY'"),
                    call("Bash", command="wget -qO- https://api.oddspapi.io/v4/fixtures"),
                    call("PowerShell", command="Invoke-RestMethod https://api.oddspapi.io/v4/fixtures"),
                    call("Bash", command="python3 -c \"import urllib.request; urllib.request.urlopen('https://api.oddspapi.io/x')\""),
                    call("WebFetch", url="https://api.oddspapi.io/v4/odds", prompt="x"),
                    call("Bash", command="curl https://api.telegram.org/bot123/sendMessage"),
                    call("WebFetch", url="https://api.telegram.org/bot1/getUpdates", prompt="x")):
        assert denied(run_gate("network_gate.py", payload)), payload


def test_network_allows_the_bobs_clis_and_ordinary_reading():
    for payload in (call("Bash", command="python3 harness/odds.py --round"),
                    call("Bash", command="python3 ../../harness/telegram.py send --file msg.txt"),
                    call("Bash", command="python3 harness/notify.py --flush"),
                    call("Bash", command="grep -n api.oddspapi.io harness/fetch.py"),
                    call("WebFetch", url="https://www.eurohoops.net/en/euroleague/1/x/", prompt="injuries"),
                    call("WebSearch", query="Panathinaikos injury report"),
                    call("Bash", command="python3 harness/review.py c-1 --keep --reason 'bet365 price ok' --evidence e")):
        assert not denied(run_gate("network_gate.py", payload)), payload


def test_both_gates_fail_closed():
    assert denied(run_gate("protect_gate.py", "not json"))
    assert denied(run_gate("network_gate.py", "{broken"))


def test_gates_import_only_stdlib_at_module_level():
    for script in ("protect_gate.py", "network_gate.py"):
        tree = ast.parse((HERE / script).read_text(encoding="utf-8"))
        names = {a.name.split(".")[0] for n in tree.body if isinstance(n, (ast.Import, ast.ImportFrom)) for a in n.names}
        names |= {n.module.split(".")[0] for n in tree.body if isinstance(n, ast.ImportFrom) and n.module}
        assert names <= {"__future__", "annotations", "json", "os", "pathlib", "re", "sys", "urllib"}, (script, names)


def test_settings_register_the_gates():
    settings = json.loads((tipsterpath.BOB / ".claude" / "settings.json").read_text(encoding="utf-8"))
    entries = settings["hooks"]["PreToolUse"]

    def matchers(script):
        return [e["matcher"] for e in entries if any(script in h["command"] for h in e["hooks"])]
    protect, network = matchers("protect_gate.py"), matchers("network_gate.py")
    assert protect and all(re.fullmatch(protect[0], t) for t in ("Write", "Edit", "Bash", "PowerShell"))
    assert network and all(re.fullmatch(network[0], t) for t in ("Bash", "PowerShell", "WebFetch", "WebSearch"))
    assert matchers("commit_gate.py") and matchers("skill_gate.py")
    assert settings["worktree"] == {"bgIsolation": "none"}


if __name__ == "__main__":
    raise SystemExit(testkit.run(globals()))
