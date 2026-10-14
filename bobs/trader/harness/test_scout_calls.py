#!/usr/bin/env python3
"""scout_calls: buys new open calls once, sells by the rules fixed at entry, sells first.

Run:  python3 harness/test_scout_calls.py
"""
from __future__ import annotations

import datetime as dt
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import traderpath  # noqa: E402,F401
import ledger  # noqa: E402
import testkit  # noqa: E402
from strategies import base, scout_calls  # noqa: E402

NOW = dt.datetime(2026, 10, 14, 6, 30, tzinfo=dt.timezone.utc)
CFG = {"stop_loss": 0.20, "take_profit": 0, "max_hold_days": 365}
RULES = {"stop_loss": 0.20, "take_profit": 0, "max_hold_days": 365}


def _call(symbol: str, session: str) -> dict:
    return {"kind": "call", "id": f"{symbol}-{session}-1", "symbol": symbol, "session": session,
            "card": {"rank_pct": 95.0}}


def _held(symbol: str, price: float, opened: str, rules: dict = RULES) -> list[dict]:
    order = f"{opened}-scout_calls-buy-{symbol}"
    return [ledger.line("proposed", NOW, opened, order_id=order, strategy="scout_calls", symbol=symbol, side="buy",
                        call_id=f"{symbol}-call", exit_rules=rules, decision="accepted"),
            ledger.line("filled", NOW, opened, order_id=order, symbol=symbol, side="buy", qty=10, price=price,
                        commission=1.0)]


def _ctx(rows: list[dict], calls: list[dict], closes: dict, session: str = "2026-10-13",
         withdrawn: set | None = None, since: str = "2026-10-01") -> base.Context:
    return base.Context(session=session, today=dt.date.fromisoformat(session), book=ledger.replay(rows),
                        feed={"calls": calls, "withdrawn": withdrawn or set()}, rows=rows, cfg=CFG,
                        closes=closes, since=since)


def test_new_call_is_bought_with_its_card_and_exit_rules():
    out = scout_calls.propose(_ctx([], [_call("AAA", "2026-10-13")], {}))
    assert [(p["side"], p["symbol"], p["order_id"]) for p in out] == [("buy", "AAA", "2026-10-13-scout_calls-buy-AAA")]
    assert out[0]["exit_rules"] == RULES and out[0]["card"]["rank_pct"] == 95.0 and out[0]["qty"] is None


def test_calls_before_the_trader_started_and_held_names_are_skipped():
    rows = _held("BBB", 100.0, "2026-10-05")
    calls = [_call("OLD", "2026-09-30"), _call("BBB", "2026-10-02")]
    assert scout_calls.propose(_ctx(rows, calls, {"BBB": 100.0})) == []


def test_refused_buy_is_proposed_again_later():
    refused = [ledger.line("proposed", NOW, "2026-10-12", order_id="2026-10-12-scout_calls-buy-AAA", strategy="scout_calls",
                           symbol="AAA", side="buy", call_id="AAA-2026-10-12-1", decision="refused")]
    out = scout_calls.propose(_ctx(refused, [_call("AAA", "2026-10-12")], {}))
    assert [p["order_id"] for p in out] == ["2026-10-13-scout_calls-buy-AAA"]
    accepted = [{**refused[0], "decision": "accepted"}]
    assert scout_calls.propose(_ctx(accepted, [_call("AAA", "2026-10-12")], {})) == []


def test_stop_on_the_close_sells_and_one_cent_above_holds():
    rows = _held("AAA", 100.0, "2026-10-01")
    assert scout_calls.propose(_ctx(rows, [], {"AAA": 80.01})) == []
    out = scout_calls.propose(_ctx(rows, [], {"AAA": 80.0}))
    assert out[0]["side"] == "sell" and out[0]["qty"] == 10 and out[0]["reason"].startswith("stop")


def test_target_time_and_withdrawal_sell():
    with_target = _held("AAA", 100.0, "2026-10-01", {**RULES, "take_profit": 0.5})
    assert scout_calls.propose(_ctx(with_target, [], {"AAA": 150.0}))[0]["reason"].startswith("target")
    old = _held("AAA", 100.0, "2025-10-13")
    assert scout_calls.propose(_ctx(old, [], {"AAA": 100.0}))[0]["reason"].startswith("held")
    fresh = _held("AAA", 100.0, "2026-10-01")
    out = scout_calls.propose(_ctx(fresh, [], {"AAA": 100.0}, withdrawn={"AAA-call"}))
    assert out[0]["reason"] == "the Scout withdrew the call"


def test_sells_come_before_buys():
    rows = _held("AAA", 100.0, "2026-10-01")
    out = scout_calls.propose(_ctx(rows, [_call("CCC", "2026-10-13")], {"AAA": 70.0}))
    assert [p["side"] for p in out] == ["sell", "buy"]


if __name__ == "__main__":
    raise SystemExit(testkit.run(globals()))
