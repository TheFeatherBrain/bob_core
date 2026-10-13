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


def _buy(owner: int, date: str, shares: float = 1000, price: float = 10, **extra) -> dict:
    return {"issuer_cik": 7, "owner_cik": owner, "owner": f"o{owner}", "date": date, "code": "P",
            "shares": shares, "price": price, "acquired": True, "plan": False, "evidence": f"ev/{owner}",
            "class": "opportunistic", **extra}


def test_opportunistic_buys_filter():
    txns = [_buy(1, "2026-10-01"),                       # kept
            _buy(1, "2026-09-01", plan=True),            # planned 10b5-1
            _buy(1, "2026-09-02", code="S", acquired=False),  # a sale
            _buy(2, "2026-10-02", **{"class": "routine"}),    # same month three years running
            _buy(3, "2026-10-02", **{"class": "unclassified"}),  # under three years on record
            _buy(1, "2026-06-01"),                       # outside 90 days
            _buy(4, "2026-10-03", **{"class": None})]    # never classified
    kept = insider.opportunistic_buys(txns, TODAY)
    assert [(t["owner_cik"], t["date"]) for t in kept] == [(1, "2026-10-01")]


def test_amended_filing_is_counted_once():
    sec = pathlib.Path(tempfile.mkdtemp())
    (sec / "form4").mkdir()
    original, amended = _buy(1, "2026-10-01"), {**_buy(1, "2026-10-01"), "evidence": "ev/amendment"}
    (sec / "form4" / "2026-10-01.json").write_text(json.dumps([original]))
    (sec / "form4" / "2026-10-02.json").write_text(json.dumps([amended]))
    (sec / "form4" / "coverage.json").write_text(json.dumps(_days(1, 90)))
    value, evidence = insider.signal_for(7, 1_000_000, insider.load_store(sec), TODAY)
    assert abs(value - 0.01) < 1e-12 and evidence == ["ev/1"]


def test_unparsed_filing_makes_the_issuer_could_not_tell():
    sec = pathlib.Path(tempfile.mkdtemp())
    (sec / "form4").mkdir()
    (sec / "form4" / "coverage.json").write_text(json.dumps(_days(1, 90)))
    (sec / "form4" / "unparsed.json").write_text(json.dumps({"2026-10-01": [{"path": "p", "ciks": [7]}]}))
    assert insider.signal_for(7, 1_000_000, insider.load_store(sec), TODAY) == (None, [])
    assert insider.signal_for(8, 1_000_000, insider.load_store(sec), TODAY)[0] == 0.0


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
    (sec / "form4" / "2026-10-01.json").write_text(json.dumps([_buy(1, "2026-10-01")]))
    (sec / "form4" / "coverage.json").write_text(json.dumps(_days(1, 30)))
    assert insider.signal_for(7, 1_000_000, insider.load_store(sec), TODAY) == (None, [])
    (sec / "form4" / "coverage.json").write_text(json.dumps(_days(1, 90)))
    value, evidence = insider.signal_for(7, 1_000_000, insider.load_store(sec), TODAY)
    assert abs(value - 0.01) < 1e-12 and evidence == ["ev/1"]


if __name__ == "__main__":
    raise SystemExit(testkit.run(globals()))
