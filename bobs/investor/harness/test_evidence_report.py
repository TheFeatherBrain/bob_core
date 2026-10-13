#!/usr/bin/env python3
"""evidence_report: counts what is stored, and never offers a cited folder for pruning.

Run:  python3 harness/test_evidence_report.py
"""
from __future__ import annotations

import datetime as dt
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import scoutpath  # noqa: E402,F401
import evidence_report  # noqa: E402
import testkit  # noqa: E402

TODAY = dt.date(2026, 10, 13)


def _folder(bob: pathlib.Path, rel: str, size: int) -> pathlib.Path:
    where = bob / "data" / "evidence" / rel
    where.mkdir(parents=True)
    (where / "raw.txt").write_text("x" * size, encoding="utf-8")
    (where / "meta.json").write_text("{}", encoding="utf-8")
    return where


def _world() -> pathlib.Path:
    bob = testkit.temp_bob()
    _folder(bob, "2026-01-05/cited-by-thesis", 10)
    in_state = _folder(bob, "2026-01-05/cited-by-state", 20)
    _folder(bob, "2026-01-05/old-uncited", 30)
    _folder(bob, "2026-10-12/new-uncited", 40)
    (bob / "theses" / "NVDA.md").write_text("- revenue [ev:2026-01-05/cited-by-thesis]\n", encoding="utf-8")
    state = bob / "data" / "state" / "prices"
    state.mkdir(parents=True)
    (state / "NVDA.json").write_text(json.dumps({"evidence": [str(in_state)]}), encoding="utf-8")
    return bob


def test_totals_and_months():
    _world()
    report = evidence_report.build(TODAY, older_than_days=90)
    assert report["folders"] == 4 and report["months"] == {"2026-01": 3, "2026-10": 1}
    assert report["bytes"] >= 100


def test_only_old_uncited_folders_are_candidates():
    _world()
    report = evidence_report.build(TODAY, older_than_days=90)
    assert report["cited"] == 2 and report["candidates"] == ["2026-01-05/old-uncited"]


def test_report_never_deletes():
    bob = _world()
    evidence_report.main(["--older-than", "1"])
    assert (bob / "data" / "evidence" / "2026-01-05" / "old-uncited" / "raw.txt").is_file()


if __name__ == "__main__":
    raise SystemExit(testkit.run(globals()))
