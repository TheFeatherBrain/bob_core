#!/usr/bin/env python3
"""Loops are code too: each must register and record first, and call only CLIs and flags that exist.

Run:  python3 harness/test_loops.py
"""
from __future__ import annotations

import pathlib
import re
import sys
import tomllib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import tipsterpath  # noqa: E402,F401
import testkit  # noqa: E402

BOB, CORE = tipsterpath.BOB, tipsterpath.CORE
LOOPS = tomllib.loads(testkit.MANIFEST.read_text(encoding="utf-8"))["loops"]
OWN = LOOPS["own"]
CALL = re.compile(r"python3 ((?:\.\./\.\./)?harness/[\w/]+\.py)((?: --[\w-]+| [\w<>.|-]+)*)")


def _loops() -> dict[str, str]:
    return {pathlib.PurePosixPath(rel).stem: (BOB / rel).read_text(encoding="utf-8") for rel in OWN}


def test_every_own_loop_exists_and_has_a_schedule():
    assert all((BOB / rel).is_file() for rel in OWN), OWN
    assert set(_loops()) <= set(LOOPS["schedules"])


def test_each_loop_registers_then_records_first():
    for name, text in _loops().items():
        lines = [line for line in text.splitlines() if line.strip()]
        assert lines[1].startswith("FIRST: CronList"), name
        assert lines[2].endswith(f"loop_passes.py --record {name}"), name


def test_loops_call_only_existing_clis_and_flags():
    for name, text in _loops().items():
        for script, args in CALL.findall(text):
            path = (CORE / script[len("../../"):]) if script.startswith("../../") else BOB / script
            assert path.is_file(), f"{name}: {script} does not exist"
            source = path.read_text(encoding="utf-8")
            for flag in re.findall(r"--[\w-]+", args):
                assert f'"{flag}"' in source, f"{name}: {script} has no {flag}"


def test_discovery_loop_reviews_every_candidate():
    text = _loops()["discovery"]
    assert "python3 harness/discover.py --run" in text
    assert "every CANDIDATE line" in text and "python3 harness/review.py" in text
    reading = text.index("injury report"), text.index("news"), text.index("python3 harness/review.py")
    assert reading[0] < reading[2] and reading[1] < reading[2], "news and the injury report are read before review"
    assert "python3 harness/notify.py --flush" in text


def test_news_loop_rechecks_or_records_no_change():
    text = _loops()["news-watch"]
    assert "python3 harness/news.py --poll" in text and "TOUCHED" in text
    assert "python3 harness/recheck.py" in text and "--no-change" in text and "--withdraw" in text


def test_no_loop_changes_mode_halt_or_state_by_hand():
    for name, text in _loops().items():
        lower = text.casefold()
        assert "rm " not in lower and "mode = " not in lower and "tips.jsonl" not in lower, name


if __name__ == "__main__":
    raise SystemExit(testkit.run(globals()))
