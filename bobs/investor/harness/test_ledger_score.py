#!/usr/bin/env python3
"""ledger_score: next-session entry, fixed horizons, delistings scored, gaps never back-filled.

Run:  python3 harness/test_ledger_score.py
"""
from __future__ import annotations

import datetime as dt
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import scoutpath  # noqa: E402,F401
import ledger_score as ls  # noqa: E402
import testkit  # noqa: E402

CALL = {"kind": "call", "id": "PICK-2026-01-01-1", "ticker": "PICK", "symbol": "PICK", "lane": "numbers",
        "session": "2026-01-01", "score": 90.0}


def _weekdays(first: str, last: str) -> list[str]:
    day, end, out = dt.date.fromisoformat(first), dt.date.fromisoformat(last), []
    while day <= end:
        if day.weekday() < 5:
            out.append(day.isoformat())
        day += dt.timedelta(days=1)
    return out


def _series(pick_until: str = "2026-07-31", pick_after_april: float = 110.0) -> dict:
    days = _weekdays("2026-01-01", "2026-07-31")
    step = lambda before, after: [[d, before if d < "2026-04-01" else after, 1] for d in days]  # noqa: E731
    pick = [b for b in step(100.0, pick_after_april) if b[0] <= pick_until]
    return {"PICK": pick, "SPY": step(100.0, 104.0), "QQQ": step(100.0, 105.0)}


def test_add_months_clamps_month_end():
    assert ls.add_months(dt.date(2026, 1, 31), 1) == dt.date(2026, 2, 28)
    assert ls.add_months(dt.date(2026, 1, 31), 12) == dt.date(2027, 1, 31)


def test_entry_is_the_next_session_close():
    assert ls.entry([["2026-01-01", 99.0, 1], ["2026-01-02", 100.0, 1]], "2026-01-01") == ("2026-01-02", 100.0)


def test_excess_returns_hand_worked():
    row = ls.score_call(CALL, _series(), {"PICK"}, "2026-07-31", set())
    three = row["horizons"]["3"]
    # pick +10%, SPY +4%, QQQ +5%
    assert three["status"] == "scored" and abs(three["excess_spy"] - 0.06) < 1e-12 and abs(three["excess_qqq"] - 0.05) < 1e-12
    assert row["horizons"]["6"]["status"] == "scored" and row["horizons"]["12"]["status"] == "pending"
    assert row["entry_date"] == "2026-01-02" and row["entry_close"] == 100.0


def test_withdrawn_calls_are_still_scored():
    row = ls.score_call(CALL, _series(), {"PICK"}, "2026-07-31", {CALL["id"]})
    assert row["withdrawn"] and row["horizons"]["3"]["status"] == "scored"


def test_delisted_is_scored_at_last_price():
    series = _series(pick_until="2026-03-02")
    for b in series["PICK"]:
        b[1] = 50.0 if b[0] >= "2026-02-02" else 100.0
    row = ls.score_call(CALL, series, set(), "2026-07-31", set())
    three = row["horizons"]["3"]
    assert row["delisted"] and three["status"] == "scored" and abs(three["excess_spy"] - (-0.54)) < 1e-12


def test_listed_but_missing_price_is_never_back_filled():
    row = ls.score_call(CALL, _series(pick_until="2026-03-02"), {"PICK"}, "2026-07-31", set())
    assert not row["delisted"] and row["horizons"]["3"]["status"] == "no price"


def test_summary_and_interim_labels():
    rows = [{"horizons": {"3": {"status": "scored", "excess_spy": 0.10, "excess_qqq": 0.0}}},
            {"horizons": {"3": {"status": "scored", "excess_spy": -0.02, "excess_qqq": -0.04}}},
            {"horizons": {"3": {"status": "pending"}}}]
    s = ls.summarize(rows)
    assert s["3"]["n"] == 2 and abs(s["3"]["avg_excess_spy"] - 0.04) < 1e-12 and s["3"]["beat_spy"] == 0.5
    assert s["3"]["interim"] and not s["12"]["interim"] and s["12"]["n"] == 0 and s["12"]["avg_excess_spy"] is None


def test_basket_follows_a_single_pick():
    series = _series()
    row = ls.score_call(CALL, series, {"PICK"}, "2026-07-31", set())
    curve = ls.basket([row], series)
    assert curve["dates"][0] == "2026-01-02" and curve["scout"][0] == 100.0
    assert abs(curve["scout"][-1] - 110.0) < 1e-9 and abs(curve["spy"][-1] - 104.0) < 1e-9


def test_run_writes_results_from_the_ledger():
    bob = testkit.temp_bob()
    state = bob / "data" / "state"
    for symbol, bars in _series().items():
        (state / "prices").mkdir(parents=True, exist_ok=True)
        (state / "prices" / f"{symbol}.json").write_text(json.dumps({"bars": bars, "evidence": []}))
    (state / "prices" / "_calendar.json").write_text(json.dumps({"sessions": [{"date": "2026-07-31", "close": "16:00"}]}))
    (state / "calls.jsonl").write_text(json.dumps(CALL) + "\n")
    doc = ls.run(dt.datetime(2026, 8, 1, 12, tzinfo=dt.timezone.utc))
    assert doc["n_calls"] == 1 and doc["summary"]["3"]["n"] == 1 and doc["session"] == "2026-07-31"
    assert json.loads(ls.results_path().read_text())["by_lane"]["numbers"]["3"]["n"] == 1


if __name__ == "__main__":
    raise SystemExit(testkit.run(globals()))
