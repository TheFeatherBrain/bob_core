#!/usr/bin/env python3
"""insider: only open-market, unplanned, out-of-pattern buys count, and only with full coverage.

Run:  python3 harness/test_insider.py
"""
from __future__ import annotations

import datetime as dt
import json
import pathlib
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import scoutpath  # noqa: E402,F401
import insider  # noqa: E402
import testkit  # noqa: E402

TODAY = dt.date(2026, 10, 13)
D = dt.date


def _buy(owner: int, date: str, shares: float = 1000, price: float = 10, **extra) -> dict:
    return {"issuer_cik": 7, "owner_cik": owner, "owner": f"o{owner}", "date": date, "code": "P",
            "shares": shares, "price": price, "acquired": True, "plan": False, "evidence": f"ev/{owner}", **extra}


def test_routine_means_same_month_in_each_of_three_prior_years():
    dates = [D(2023, 10, 2), D(2024, 10, 9), D(2025, 10, 1)]
    assert insider.is_routine(dates, D(2026, 10, 5))
    assert not insider.is_routine(dates[:1] + dates[2:], D(2026, 10, 5))


def test_opportunistic_buys_filter():
    owners = {1: [D(2025, 3, 1)], 2: [D(2023, 10, 2), D(2024, 10, 9), D(2025, 10, 1)], 3: []}
    txns = [_buy(1, "2026-10-01"),                       # kept
            _buy(1, "2026-09-01", plan=True),            # planned 10b5-1
            _buy(1, "2026-09-02", code="S", acquired=False),  # a sale
            _buy(2, "2026-10-02"),                       # routine October buyer
            _buy(1, "2026-06-01"),                       # outside 90 days
            _buy(9, "2026-10-03")]                       # owner history never read
    kept = insider.opportunistic_buys(txns, owners, TODAY)
    assert [(t["owner_cik"], t["date"]) for t in kept] == [(1, "2026-10-01")]


def test_signal_with_cluster_bonus():
    buys = [_buy(1, "2026-10-01", 1000, 10), _buy(2, "2026-10-02", 500, 20)]
    # (10,000 + 10,000) / 1,000,000 = 0.02, two insiders: x1.5 = 0.03
    assert abs(insider.insider_signal(buys, 1_000_000) - 0.03) < 1e-12


def test_signal_single_insider_and_no_buys():
    assert abs(insider.insider_signal([_buy(1, "2026-10-01", 1000, 10)], 1_000_000) - 0.01) < 1e-12
    assert insider.insider_signal([], 1_000_000) == 0.0
    assert insider.insider_signal([], None) is None


def _days(first: int, last: int) -> list[str]:
    return [(TODAY - dt.timedelta(days=k)).isoformat() for k in range(first, last + 1)]


def test_coverage_needs_every_settled_day():
    assert insider.covered(_days(1, 90), TODAY)
    assert insider.covered(_days(4, 90), TODAY)
    assert not insider.covered([d for d in _days(1, 90) if d != _days(10, 10)[0]], TODAY)


def test_signal_for_is_none_without_coverage():
    sec = pathlib.Path(tempfile.mkdtemp())
    (sec / "form4").mkdir()
    (sec / "owners").mkdir()
    (sec / "form4" / "2026-10-01.json").write_text(json.dumps([_buy(1, "2026-10-01")]))
    (sec / "owners" / "1.json").write_text(json.dumps({"fetched": "2026-10-02", "evidence": "e", "dates": ["2025-03-01"]}))
    (sec / "form4" / "coverage.json").write_text(json.dumps(_days(1, 30)))
    assert insider.signal_for(7, 1_000_000, insider.load_store(sec), TODAY) == (None, [])
    (sec / "form4" / "coverage.json").write_text(json.dumps(_days(1, 90)))
    value, evidence = insider.signal_for(7, 1_000_000, insider.load_store(sec), TODAY)
    assert abs(value - 0.01) < 1e-12 and evidence == ["ev/1"]


if __name__ == "__main__":
    raise SystemExit(testkit.run(globals()))
