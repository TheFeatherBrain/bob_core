#!/usr/bin/env python3
"""digest: built from the ledger and computed results only, with the noise banner and interim labels.

Run:  python3 harness/test_digest.py
"""
from __future__ import annotations

import datetime as dt
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import scoutpath  # noqa: E402,F401
import digest  # noqa: E402
import testkit  # noqa: E402

TODAY = dt.date(2026, 10, 17)


def _world(scored_12m: int = 0) -> pathlib.Path:
    bob = testkit.temp_bob()
    state = bob / "data" / "state"
    (state / "screen").mkdir(parents=True)
    rows = [
        {"kind": "verdict", "ticker": "ACME", "verdict": "watch", "lane": "story", "session": "2026-10-05", "at": "2026-10-06T05:00:00+00:00", "source": "s"},
        {"kind": "verdict", "ticker": "ACME", "verdict": "buy-worthy", "lane": "story", "session": "2026-10-14", "at": "2026-10-15T05:00:00+00:00", "source": "s"},
        {"kind": "call", "id": "ACME-2026-10-14-1", "ticker": "ACME", "symbol": "ACME", "lane": "story", "session": "2026-10-14",
         "at": "2026-10-15T05:00:00+00:00", "score": 88.0},
        {"kind": "verdict", "ticker": "OLD", "verdict": "pass", "lane": "numbers", "session": "2026-10-15", "at": "2026-10-16T05:00:00+00:00", "source": None},
        {"kind": "withdrawn", "call_id": "OLD-2026-01-05-1", "ticker": "OLD", "session": "2026-10-15", "at": "2026-10-16T05:00:00+00:00", "reason": "verdict now pass"},
    ]
    (state / "calls.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    summary = {"3": {"n": 2, "avg_excess_spy": 0.04, "avg_excess_qqq": 0.01, "beat_spy": 0.5, "interim": True},
               "6": {"n": 0, "avg_excess_spy": None, "avg_excess_qqq": None, "beat_spy": None, "interim": True},
               "12": {"n": scored_12m, "avg_excess_spy": 0.1 if scored_12m else None, "avg_excess_qqq": 0.05 if scored_12m else None,
                      "beat_spy": 0.6 if scored_12m else None, "interim": False}}
    results = {"summary": summary, "by_lane": {"numbers": summary, "story": summary}, "calls": [
        {"id": "OLD-2026-01-05-1", "ticker": "OLD", "lane": "numbers", "horizons": {
            "3": {"status": "scored", "due": "2026-10-15", "excess_spy": 0.06, "excess_qqq": 0.05},
            "12": {"status": "pending", "due": "2027-01-06"}}}]}
    (state / "ledger_results.json").write_text(json.dumps(results))
    (state / "screen" / "2026-10-14.json").write_text(json.dumps({"session": "2026-10-14", "new_entrants": ["ACME", "ZED"]}))
    (state / "screen" / "2026-10-01.json").write_text(json.dumps({"session": "2026-10-01", "new_entrants": ["OLDNEWS"]}))
    (state / "screen" / "failures.jsonl").write_text(
        json.dumps({"at": "2026-10-02T05:30:00+00:00", "message": "FAIL: old"}) + "\n"
        + json.dumps({"at": "2026-10-16T05:30:00+00:00", "message": "FAIL: SPY last bar 2026-10-14"}) + "\n")
    return bob


def test_failed_screens_this_week_are_listed():
    _world()
    text, data = digest.build(TODAY)
    assert data["failed_screens"] == [{"at": "2026-10-16T05:30:00+00:00", "message": "FAIL: SPY last bar 2026-10-14"}]
    assert "- 2026-10-16: FAIL: SPY last bar 2026-10-14" in text


def test_lists_calls_changes_withdrawals_and_entrants():
    _world()
    text, data = digest.build(TODAY)
    assert [c["id"] for c in data["new_calls"]] == ["ACME-2026-10-14-1"]
    assert data["changes"] == [{"ticker": "ACME", "from": "watch", "to": "buy-worthy", "direction": "up"}]
    assert [w["call_id"] for w in data["withdrawn"]] == ["OLD-2026-01-05-1"]
    assert data["entrants"] == ["ACME", "ZED"]
    assert "ACME: watch -> buy-worthy (up)" in text


def test_matured_horizon_is_labelled_interim():
    _world()
    text, data = digest.build(TODAY)
    assert data["matured"] == [{"id": "OLD-2026-01-05-1", "ticker": "OLD", "months": 3, "excess_spy": 0.06, "excess_qqq": 0.05}]
    assert "OLD 3 months (interim): +6.0% vs SPY, +5.0% vs QQQ" in text


def test_noise_banner_below_twenty_scored_calls():
    _world(scored_12m=3)
    text, data = digest.build(TODAY)
    assert data["noise"] and "Too few scored calls to judge" in text
    _world(scored_12m=20)
    text, data = digest.build(TODAY)
    assert not data["noise"] and "Too few scored calls" not in text


def test_summary_numbers_come_from_results():
    _world()
    text, _ = digest.build(TODAY)
    assert "| 3 months (interim) | 2 | +4.0% | +1.0% | 50% |" in text
    assert "| 12 months | 0 | n/a | n/a | n/a |" in text


def test_write_creates_markdown_and_json():
    bob = _world()
    path = digest.write(TODAY)
    assert path == bob / "digests" / "2026-10-17.md" and (bob / "digests" / "2026-10-17.json").is_file()


if __name__ == "__main__":
    raise SystemExit(testkit.run(globals()))
