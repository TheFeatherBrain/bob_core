#!/usr/bin/env python3
"""promotion: every criterion PASS or the report fails; changing the bar restarts the clock.

Run:  python3 harness/test_promotion.py
"""
from __future__ import annotations

import datetime as dt
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import traderpath  # noqa: E402,F401
import ledger  # noqa: E402
import promotion  # noqa: E402
import testkit  # noqa: E402
import tradercfg  # noqa: E402

NOW = dt.datetime(2026, 10, 14, 6, 30, tzinfo=dt.timezone.utc)
START = dt.date(2026, 3, 1)


def _days(n: int) -> list[str]:
    return [(START + dt.timedelta(days=i)).isoformat() for i in range(n)]


def _history(cfg: dict, days: list[str], end_equity: float = 110_000.0, bar: str | None = None) -> list[dict]:
    fp = bar or tradercfg.fingerprint(cfg, tradercfg.BAR)
    n = len(days)
    return [{"at": d, "session": d, "mode": "paper", "caps": "c", "bar": fp, "result": "OK",
             "equity": 100_000.0 + (end_equity - 100_000.0) * i / (n - 1)} for i, d in enumerate(days)]


def _trades(days: list[str], n: int = 20) -> list[dict]:
    rows = []
    for i in range(n):
        day = days[1 + i]
        oid = f"{day}-scout_calls-buy-S{i}"
        rows += [ledger.line("proposed", NOW, day, order_id=oid, strategy="scout_calls", symbol=f"S{i}", side="buy",
                             caps="ok", decision="accepted"),
                 ledger.line("submitted", NOW, day, order_id=oid),
                 ledger.line("filled", NOW, day, order_id=oid, symbol=f"S{i}", side="buy", qty=1, price=100.0, commission=1.0),
                 ledger.line("filled", NOW, days[-2], order_id=f"sell-{i}", symbol=f"S{i}", side="sell", qty=1,
                             price=101.0, commission=1.0)]
    return rows


def _spy(bob: pathlib.Path, days: list[str], end: float = 420.0) -> None:
    n = len(days)
    testkit.put(bob / "data" / "state" / "bars" / "SPY.json",
                {"raw": [], "split": [], "evidence": [], "all": [[d, 400.0 + (end - 400.0) * i / (n - 1)] for i, d in enumerate(days)]})


def _status(report: dict) -> dict:
    return {c["name"]: c["status"] for c in report["criteria"]}


def test_a_clean_record_passes_every_criterion():
    bob = testkit.temp_bob()
    cfg, days = tradercfg.load(), _days(200)
    _spy(bob, days)
    r = promotion.report(NOW, cfg, _trades(days), _history(cfg, days))
    assert r["passed"] and set(_status(r).values()) == {"PASS"}, r


def test_too_short_and_too_few_trades_fail():
    bob = testkit.temp_bob()
    cfg, days = tradercfg.load(), _days(150)
    _spy(bob, days)
    r = promotion.report(NOW, cfg, _trades(days, 19), _history(cfg, days))
    assert _status(r)["time"] == "FAIL" and _status(r)["trades"] == "FAIL" and not r["passed"]


def test_losing_to_spy_or_a_deeper_drawdown_fails():
    bob = testkit.temp_bob()
    cfg, days = tradercfg.load(), _days(200)
    _spy(bob, days, end=460.0)
    assert _status(promotion.report(NOW, cfg, _trades(days), _history(cfg, days)))["return"] == "FAIL"
    _spy(bob, days)
    history = _history(cfg, days)
    history[100]["equity"] = 80_000.0
    assert _status(promotion.report(NOW, cfg, _trades(days), history))["drawdown"] == "FAIL"


def test_a_submitted_order_without_a_passing_cap_check_fails_the_guards():
    bob = testkit.temp_bob()
    cfg, days = tradercfg.load(), _days(200)
    _spy(bob, days)
    rows = _trades(days) + [ledger.line("submitted", NOW, days[5], order_id="sneaky")]
    assert _status(promotion.report(NOW, cfg, rows, _history(cfg, days)))["guards"] == "FAIL"
    rows = _trades(days) + [ledger.line("mismatch", NOW, days[5], details=["x"])]
    assert _status(promotion.report(NOW, cfg, rows, _history(cfg, days)))["guards"] == "FAIL"


def test_missing_spy_is_could_not_tell_and_does_not_pass():
    testkit.temp_bob()
    cfg, days = tradercfg.load(), _days(200)
    r = promotion.report(NOW, cfg, _trades(days), _history(cfg, days))
    assert _status(r)["return"] == "could not tell" and not r["passed"]


def test_changing_the_bar_restarts_the_clock():
    bob = testkit.temp_bob()
    cfg, days = tradercfg.load(), _days(200)
    _spy(bob, days)
    history = _history(cfg, days[:100], bar="old bar") + _history(cfg, days[100:])
    assert _status(promotion.report(NOW, cfg, _trades(days[100:]), history))["time"] == "FAIL"


def test_changing_the_bar_and_back_still_restarts_the_clock():
    bob = testkit.temp_bob()
    cfg, days = tradercfg.load(), _days(200)
    _spy(bob, days)
    history = _history(cfg, days[:50]) + _history(cfg, days[50:100], bar="other bar") + _history(cfg, days[100:])
    assert _status(promotion.report(NOW, cfg, _trades(days[100:]), history))["time"] == "FAIL"


def test_latest_passing_looks_back_thirty_days():
    testkit.temp_bob()
    promotion.write({"at": "2026-09-10T06:00:00+00:00", "passed": True, "criteria": []})
    assert not promotion.latest_passing(NOW)
    promotion.write({"at": "2026-09-20T06:00:00+00:00", "passed": True, "criteria": []})
    assert promotion.latest_passing(NOW)


def test_record_run_and_history_helpers():
    testkit.temp_bob()
    cfg = tradercfg.load()
    promotion.record_run(NOW, None, cfg, "OK", None)
    promotion.record_run(NOW, "2026-10-12", {**cfg, "mode": "simulate"}, "OK", 100_000.0)
    promotion.record_run(NOW, "2026-10-13", {**cfg, "mode": "simulate"}, "FAIL", None)
    history = promotion.read_history()
    assert [h["mode"] for h in history] == ["off", "simulate", "simulate"]
    assert promotion.first_active_session(history, "simulate") == "2026-10-12"
    assert promotion.last_ok_equity(history, "simulate") == 100_000.0


def test_history_helpers_look_only_at_the_given_mode():
    testkit.temp_bob()
    cfg = tradercfg.load()
    promotion.record_run(NOW, "2026-10-01", {**cfg, "mode": "simulate"}, "OK", 200_000.0)
    promotion.record_run(NOW, "2026-10-12", {**cfg, "mode": "paper"}, "OK", 100_000.0)
    promotion.record_run(NOW, "2026-10-13", {**cfg, "mode": "simulate"}, "OK", 210_000.0)
    history = promotion.read_history()
    assert promotion.first_active_session(history, "paper") == "2026-10-12"
    assert promotion.last_ok_equity(history, "paper") == 100_000.0
    assert promotion.last_ok_equity(history, "live") is None


def test_lots_closed_by_a_correction_are_not_trades():
    bob = testkit.temp_bob()
    cfg, days = tradercfg.load(), _days(200)
    _spy(bob, days)
    rows = _trades(days, 19) + [
        ledger.line("filled", NOW, days[3], order_id="x", symbol="ZZZ", side="buy", qty=1, price=100.0, commission=1.0),
        ledger.line("correction", NOW, days[-2], clears=None, cash_delta=0.0, share_deltas={"ZZZ": [-1, 100.0]})]
    assert _status(promotion.report(NOW, cfg, rows, _history(cfg, days)))["trades"] == "FAIL"


if __name__ == "__main__":
    raise SystemExit(testkit.run(globals()))
