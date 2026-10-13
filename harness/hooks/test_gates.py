#!/usr/bin/env python3
"""The hook gates, driven the way Claude Code drives them: JSON on stdin, decision on stdout.

Every gate is tested in both directions. A case it must refuse and a case it must allow together
show it discriminates; either alone passes a gate that always, or never, fires.

Run:  python3 harness/hooks/test_gates.py
"""
from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys
import tempfile
import time

HERE = pathlib.Path(__file__).resolve().parent
CORE = HERE.parent.parent
EXAMPLE = CORE / "bob.example.toml"


def _bob() -> pathlib.Path:
    """A throwaway Bob folder holding the example manifest and an empty skills/ directory."""
    bob = pathlib.Path(tempfile.mkdtemp()).resolve()
    (bob / "bob.toml").write_text(EXAMPLE.read_text())
    (bob / "skills").mkdir()
    return bob


def run_gate(script: str, payload, env_extra: dict | None = None, bob: pathlib.Path | None = None) -> dict | None:
    bob = bob or _bob()
    env = {k: v for k, v in os.environ.items() if not k.startswith("REFLECT_")}
    env.update(BOB_MANIFEST=str(bob / "bob.toml"), CLAUDE_CONFIG_DIR=str(bob / "config"))
    env.update(env_extra or {})
    data = payload if isinstance(payload, str) else json.dumps(payload)
    out = subprocess.run([sys.executable, str(HERE / script)], input=data, env=env, cwd=bob,
                         capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, f"{script} exited {out.returncode}: {out.stderr}"
    return json.loads(out.stdout) if out.stdout.strip() else None


def denied(decision: dict | None) -> bool:
    return bool(decision) and (decision.get("hookSpecificOutput") or {}).get("permissionDecision") == "deny"


def bash(command: str) -> dict:
    return {"tool_name": "Bash", "tool_input": {"command": command}}


# ---- commit_gate ------------------------------------------------------------------------------

def test_commit_backtick_in_double_quotes_is_denied():
    assert denied(run_gate("commit_gate.py", bash('git commit -m "fix `parse` crash"')))


def test_commit_dollar_paren_is_denied():
    assert denied(run_gate("commit_gate.py", bash('git commit -m "add $(date) stamp"')))


def test_commit_dollar_name_is_denied():
    assert denied(run_gate("commit_gate.py", bash('git commit -m "bump $VERSION"')))


def test_commit_single_quoted_is_allowed():
    assert run_gate("commit_gate.py", bash("git commit -m 'fix `parse` crash'")) is None


def test_commit_plain_message_is_allowed():
    assert run_gate("commit_gate.py", bash('git commit -m "fix parse crash"')) is None


def test_commit_from_file_is_allowed():
    assert run_gate("commit_gate.py", bash("git commit -F msg.txt")) is None


def test_non_commit_command_is_allowed():
    assert run_gate("commit_gate.py", bash('echo "`date`"')) is None


def test_later_command_quoting_is_not_the_commits():
    assert run_gate("commit_gate.py", bash('git commit -m "ok" && echo "`date`"')) is None


def test_commit_gate_fails_open_on_garbage():
    assert run_gate("commit_gate.py", "not json") is None


# ---- slack_send_gate --------------------------------------------------------------------------

def send(text: str, key: str = "message") -> dict:
    return {"tool_name": "mcp__slack__slack_send_message", "tool_input": {key: text}}


def test_slack_markdown_bold_is_denied():
    assert denied(run_gate("slack_send_gate.py", send("this is **bold**")))


def test_slack_odd_fence_is_denied():
    assert denied(run_gate("slack_send_gate.py", send("a\n```\nb")))


def test_slack_upload_comment_is_linted():
    assert denied(run_gate("slack_send_gate.py", send("## Heading", key="initial_comment")))


def test_slack_clean_message_is_allowed():
    assert run_gate("slack_send_gate.py", send("Done: 3 of 3 checks pass. *Next:* the weekly summary.")) is None


def test_slack_gate_ignores_input_without_text():
    assert run_gate("slack_send_gate.py", {"tool_name": "x", "tool_input": {}}) is None


def test_slack_lint_limits_come_from_the_manifest():
    message = send("one two three four five six seven")
    assert run_gate("slack_send_gate.py", message) is None          # under the default budget
    bob = _bob()
    (bob / "bob.toml").write_text(EXAMPLE.read_text().replace(
        "[slack.roots]", "[slack.lint]\nword_budget = 5\n\n[slack.roots]", 1))
    assert denied(run_gate("slack_send_gate.py", message, bob=bob))  # over a budget of 5


# ---- reflect_gate -----------------------------------------------------------------------------

def stop(message: str, session: str = "s1", active: bool = False) -> dict:
    return {"session_id": session, "last_assistant_message": message, "stop_hook_active": active}


def _stale_watch() -> tuple[pathlib.Path, dict]:
    """A watched directory whose newest file is an hour old, and the env pointing the gate at it."""
    tmp = pathlib.Path(tempfile.mkdtemp())
    watched = tmp / "skills"
    watched.mkdir()
    old = watched / "note.md"
    old.write_text("old lesson\n")
    past = time.time() - 3600
    os.utime(old, (past, past))
    return tmp, {"REFLECT_WATCH_DIRS": str(watched), "REFLECT_STATE": str(tmp / "state.json")}


def test_reflect_blocks_an_admission_with_nothing_written():
    _tmp, env = _stale_watch()
    out = run_gate("reflect_gate.py", stop("I was wrong about the count."), env)
    assert out and out.get("decision") == "block", out


def test_reflect_allows_a_turn_with_no_admission():
    _tmp, env = _stale_watch()
    assert run_gate("reflect_gate.py", stop("Summary posted. 3 items."), env) is None


def test_reflect_allows_an_admission_after_a_fresh_write():
    tmp, env = _stale_watch()
    (tmp / "skills" / "new.md").write_text("the lesson\n")
    assert run_gate("reflect_gate.py", stop("I was wrong about the count."), env) is None


def test_reflect_never_stacks_inside_a_block():
    _tmp, env = _stale_watch()
    assert run_gate("reflect_gate.py", stop("I was wrong.", active=True), env) is None


def test_reflect_is_capped_per_session():
    _tmp, env = _stale_watch()
    results = [run_gate("reflect_gate.py", stop("I was wrong.", session="cap"), env) for _ in range(4)]
    assert [bool(r) for r in results] == [True, True, False, False], results


def test_reflect_counts_sessions_separately():
    _tmp, env = _stale_watch()
    for _ in range(2):
        run_gate("reflect_gate.py", stop("I was wrong.", session="a"), env)
    assert run_gate("reflect_gate.py", stop("I was wrong.", session="b"), env)


def test_reflect_with_no_watched_directory_stays_silent():
    tmp = pathlib.Path(tempfile.mkdtemp())
    env = {"REFLECT_WATCH_DIRS": str(tmp / "absent"), "REFLECT_STATE": str(tmp / "state.json")}
    assert run_gate("reflect_gate.py", stop("I was wrong."), env) is None


# ---- skill_gate -------------------------------------------------------------------------------

LESSON = """+++
summary = "Print the full count before claiming absence"
tells = [{ tool = "Bash", pattern = '\\|\\s*head\\b' }]
+++
The tell: a pipe into head.
"""


def _lessons(**files: str) -> pathlib.Path:
    bob = _bob()
    for name, text in files.items():
        (bob / "skills" / f"{name.replace('_', '-')}.md").write_text(text)
    return bob


def lesson_shown(decision: dict | None, name: str) -> bool:
    ctx = ((decision or {}).get("hookSpecificOutput") or {}).get("additionalContext", "")
    return f"skills/{name}.md" in ctx


def fires(bob: pathlib.Path) -> list[dict]:
    path = bob / "config" / "skill-fires.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


def call(command: str, session: str = "s1", tool: str = "Bash") -> dict:
    return {"tool_name": tool, "tool_input": {"command": command}, "session_id": session}


def test_skill_gate_shows_the_lesson_whose_tell_matches_and_never_denies():
    bob = _lessons(capped_reads=LESSON)
    out = run_gate("skill_gate.py", call("grep x app.log | head -3"), bob=bob)
    assert lesson_shown(out, "capped-reads") and not denied(out), out
    assert "a pipe into head" in out["hookSpecificOutput"]["additionalContext"]


def test_skill_gate_is_silent_when_nothing_matches():
    bob = _lessons(capped_reads=LESSON)
    assert run_gate("skill_gate.py", call("grep -c x app.log"), bob=bob) is None
    assert run_gate("skill_gate.py", call("x | head", tool="Read"), bob=bob) is None  # tool regex
    assert fires(bob) == []


def test_skill_gate_shows_a_lesson_once_per_session_but_counts_every_match():
    bob = _lessons(capped_reads=LESSON)
    assert lesson_shown(run_gate("skill_gate.py", call("a | head", "s1"), bob=bob), "capped-reads")
    assert run_gate("skill_gate.py", call("b | head", "s1"), bob=bob) is None
    assert lesson_shown(run_gate("skill_gate.py", call("c | head", "s2"), bob=bob), "capped-reads")
    assert [(r["session"], r["injected"]) for r in fires(bob)] == \
        [("s1", True), ("s1", False), ("s2", True)], fires(bob)


def test_skill_gate_skips_retired_and_broken_lessons_but_keeps_the_rest():
    retired = LESSON.replace("+++\nThe", 'retired = "2026-10-01: done"\n+++\nThe')
    bob = _lessons(capped_reads=LESSON, old=retired, broken="no front matter")
    out = run_gate("skill_gate.py", call("a | head"), bob=bob)
    assert lesson_shown(out, "capped-reads") and not lesson_shown(out, "old"), out


def test_skill_gate_fails_open():
    bob = _lessons(capped_reads=LESSON)
    assert run_gate("skill_gate.py", "not json", bob=bob) is None
    assert run_gate("skill_gate.py", [1, 2], bob=bob) is None
    (bob / "bob.toml").write_text("not a manifest")
    assert run_gate("skill_gate.py", call("a | head"), bob=bob) is None


def main() -> int:
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"PASS {name}")
        except Exception as e:  # noqa: BLE001 - a crash is a failure, reported by name
            failed += 1
            print(f"FAIL {name}: {type(e).__name__}: {e}")
    print(f"{len(tests) - failed} of {len(tests)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
