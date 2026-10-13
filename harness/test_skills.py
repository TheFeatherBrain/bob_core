#!/usr/bin/env python3
"""The lessons loop: a lesson file parses or is refused, the index tracks the files, and the review
flags come from the fire ledger.

The last case drives the whole loop as a Bob would: write a lesson, regenerate the index, make the
call its tell names through the skill gate in three sessions, and read PROMOTE? back from --stats.

Run:  python3 harness/test_skills.py
"""
from __future__ import annotations

import datetime as dt
import json
import os
import pathlib
import subprocess
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parent
EXAMPLE = (HERE.parent / "bob.example.toml").read_text()
sys.path.insert(0, str(HERE))

import skill_index  # noqa: E402

UTC = dt.timezone.utc
NOW = dt.datetime(2026, 10, 13, 12, 0, tzinfo=UTC)

LESSON = """+++
summary = "A capped read is a floor; print the full count before claiming absence"
tells = [{ tool = "Bash", pattern = '\\|\\s*head\\b' }]
+++

# Capped reads

What happened: a `| head` hid the match. The tell: a pipe into head.
"""


def bob() -> tuple[pathlib.Path, dict]:
    root = pathlib.Path(tempfile.mkdtemp(prefix="skills-")).resolve()
    (root / "bob.toml").write_text(EXAMPLE)
    (root / "skills").mkdir()
    env = {**os.environ, "BOB_MANIFEST": str(root / "bob.toml"), "CLAUDE_CONFIG_DIR": str(root / "config")}
    return root, env


def write(directory: pathlib.Path, name: str, text: str) -> pathlib.Path:
    path = directory / f"{name}.md"
    path.write_text(text)
    return path


def refused(text: str, needle: str, name: str = "lesson") -> None:
    root, _env = bob()
    path = write(root / "skills", name, text)
    try:
        skill_index.parse(path)
    except ValueError as e:
        assert needle in str(e), f"{needle!r} not in {e}"
        return
    raise AssertionError(f"accepted a lesson that should fail on {needle!r}")


def run(env: dict, cwd: pathlib.Path, *args: str, stdin: str | None = None) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, *args], cwd=cwd, env=env, input=stdin,
                          capture_output=True, text=True, timeout=30)


# ---- parse ------------------------------------------------------------------------------------

def test_a_good_lesson_parses_with_its_tell():
    root, _env = bob()
    s = skill_index.parse(write(root / "skills", "capped-reads", LESSON))
    assert s.summary.startswith("A capped read") and s.active and len(s.tells) == 1
    assert s.tells[0].matches("Bash", "grep x log | head -5")
    assert not s.tells[0].matches("Read", "grep x log | head -5"), "tool must restrict the tell"
    assert not s.tells[0].matches("Bash", "grep -c x log")
    assert s.body.startswith("# Capped reads")


def test_missing_front_matter_is_refused():
    refused("# no front matter\n", "+++")


def test_unclosed_front_matter_is_refused():
    refused('+++\nsummary = "x"\n', "closing")


def test_missing_summary_is_refused():
    refused("+++\ntells = []\n+++\n", "summary")


def test_bad_regex_is_refused():
    refused('+++\nsummary = "x"\ntells = [{ pattern = "(" }]\n+++\n', "regex")


def test_unknown_key_is_refused():
    refused('+++\nsummary = "x"\nseverity = 3\n+++\n', "unknown")


def test_unknown_tell_key_is_refused():
    refused('+++\nsummary = "x"\ntells = [{ pattern = "a", when = "b" }]\n+++\n', "unknown")


def test_bad_file_name_is_refused():
    refused(LESSON, "file name", name="Capped_Reads")


def test_one_broken_file_does_not_hide_the_others():
    root, _env = bob()
    write(root / "skills", "good", LESSON)
    write(root / "skills", "broken", "no front matter")
    skills, errors = skill_index.load(root / "skills")
    assert [s.name for s in skills] == ["good"] and len(errors) == 1 and "broken" in errors[0]


# ---- index ------------------------------------------------------------------------------------

def test_index_lists_active_lessons_and_drops_retired_ones():
    root, _env = bob()
    write(root / "skills", "capped-reads", LESSON)
    write(root / "skills", "old", '+++\nsummary = "old rule"\nretired = "2026-10-01: superseded"\n+++\n')
    write(root / "skills", "no-tell", '+++\nsummary = "a rule with no observable"\n+++\n')
    text = skill_index.render(skill_index.load(root / "skills")[0])
    assert "[capped-reads](capped-reads.md)" in text and "recalled on: Bash" in text, text
    assert "no tell" in text and "old rule" not in text, text


def test_check_fails_on_missing_then_stale_index_and_passes_after_write():
    root, env = bob()
    check = lambda: run(env, root, str(HERE / "skill_index.py"), "--check")  # noqa: E731
    first = check()
    assert first.returncode == 1 and "missing" in first.stdout, first.stdout
    assert run(env, root, str(HERE / "skill_index.py"), "--write").returncode == 0
    assert check().returncode == 0
    write(root / "skills", "capped-reads", LESSON)
    stale = check()
    assert stale.returncode == 1 and "stale" in stale.stdout, stale.stdout
    run(env, root, str(HERE / "skill_index.py"), "--write")
    ok = check()
    assert ok.returncode == 0 and "1 lesson" in ok.stdout, ok.stdout


def test_check_fails_on_a_malformed_lesson():
    root, env = bob()
    write(root / "skills", "broken", "no front matter")
    run(env, root, str(HERE / "skill_index.py"), "--write")
    out = run(env, root, str(HERE / "skill_index.py"), "--check")
    assert out.returncode == 1 and "broken.md" in out.stdout, out.stdout


# ---- stats ------------------------------------------------------------------------------------

def rows(name: str, *days_ago: int) -> list[dict]:
    return [{"at": (NOW - dt.timedelta(days=d)).isoformat(), "skill": name, "session": f"s{i}"}
            for i, d in enumerate(days_ago)]


def lesson_born(root: pathlib.Path, name: str, text: str, days_ago: int) -> skill_index.Skill:
    path = write(root / "skills", name, text)
    stamp = (NOW - dt.timedelta(days=days_ago)).timestamp()
    os.utime(path, (stamp, stamp))
    return skill_index.parse(path)


def test_three_matches_in_the_window_propose_promotion_two_do_not():
    root, _env = bob()
    s = lesson_born(root, "capped-reads", LESSON, 90)
    three = skill_index.stats([s], rows("capped-reads", 1, 2, 3), now=NOW)[0]
    two = skill_index.stats([s], rows("capped-reads", 1, 2, 45), now=NOW)[0]
    assert three["flags"] == ["PROMOTE?"] and three["matches"] == 3 and three["sessions"] == 3, three
    assert two["flags"] == [] and two["matches"] == 2, two


def test_an_old_lesson_that_never_matched_is_quiet_a_new_one_is_not():
    root, _env = bob()
    old = lesson_born(root, "old", LESSON, 90)
    new = lesson_born(root, "new", LESSON, 2)
    flags = {r["skill"]: r["flags"] for r in skill_index.stats([old, new], [], now=NOW)}
    assert flags == {"new": [], "old": ["QUIET"]}, flags


def test_a_lesson_without_a_tell_is_flagged():
    root, _env = bob()
    s = lesson_born(root, "no-tell", '+++\nsummary = "x"\n+++\n', 2)
    assert skill_index.stats([s], [], now=NOW)[0]["flags"] == ["NO TELL"]


def test_torn_and_unzoned_ledger_rows_are_skipped():
    root, _env = bob()
    ledger = root / "fires.jsonl"
    good = json.dumps(rows("capped-reads", 1)[0])
    ledger.write_text(f'{good}\n{{"at": "2026-10-12T12:00:00", "skill": "capped-reads"}}\n{{"at": "2026\n')
    s = lesson_born(root, "capped-reads", LESSON, 90)
    got = skill_index.stats([s], skill_index.fires(ledger), now=NOW)[0]
    assert got["matches"] == 1, got


# ---- the loop, end to end ---------------------------------------------------------------------

def test_lesson_written_indexed_recalled_counted_and_proposed():
    root, env = bob()
    write(root / "skills", "capped-reads", LESSON)
    assert run(env, root, str(HERE / "skill_index.py"), "--write").returncode == 0
    assert "capped-reads" in (root / "skills" / "INDEX.md").read_text()
    call = json.dumps({"tool_name": "Bash", "tool_input": {"command": "grep err app.log | head -3"},
                       "session_id": ""})
    for session in ("a", "b", "c"):
        out = run(env, root, str(HERE / "hooks" / "skill_gate.py"),
                  stdin=call.replace('"session_id": ""', f'"session_id": "{session}"'))
        context = json.loads(out.stdout)["hookSpecificOutput"]["additionalContext"]
        assert "skills/capped-reads.md" in context and "a pipe into head" in context, context
    stats = run(env, root, str(HERE / "skill_index.py"), "--stats")
    assert "capped-reads: 3 match(es) in 3 session(s)" in stats.stdout and "PROMOTE?" in stats.stdout, \
        stats.stdout


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
