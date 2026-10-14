#!/usr/bin/env python3
"""ledger: replay rebuilds lots oldest-first, splits and dividends apply, corrections are person-only.

Run:  python3 harness/test_ledger.py
"""
from __future__ import annotations

import datetime as dt
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import traderpath  # noqa: E402,F401
import ledger  # noqa: E402
import testkit  # noqa: E402

NOW = dt.datetime(2026, 10, 14, 6, 30, tzinfo=dt.timezone.utc)


class Tty:
    def __init__(self, tty: bool):
        self.tty = tty

    def isatty(self) -> bool:
        return self.tty


def _buy(order_id: str, symbol: str, qty: float, price: float, session: str) -> list[dict]:
    return [ledger.line("proposed", NOW, session, order_id=order_id, strategy="scout_calls", symbol=symbol,
                        side="buy", call_id=f"{symbol}-call", exit_rules={"stop_loss": 0.2}),
            ledger.line("filled", NOW, session, order_id=order_id, symbol=symbol, side="buy", qty=qty,
                        price=price, commission=1.0)]


def _sell(order_id: str, symbol: str, qty: float, price: float, session: str) -> dict:
    return ledger.line("filled", NOW, session, order_id=order_id, symbol=symbol, side="sell", qty=qty,
                       price=price, commission=1.0)


def test_append_and_read_round_trip_with_torn_line_counted():
    testkit.temp_bob()
    ledger.append([ledger.line("cash", NOW, "2026-10-13", amount=100.0)])
    with ledger.path().open("a", encoding="utf-8") as fh:
        fh.write("{torn\n")
    rows, torn = ledger.read()
    assert torn == 1 and rows[0]["kind"] == "cash" and rows[0]["id"].startswith("cash-2026-10-13-")


def test_sells_consume_the_oldest_lot_first():
    rows = ([ledger.line("cash", NOW, "2026-01-01", amount=10000.0)]
            + _buy("b1", "AAA", 10, 100.0, "2026-01-02") + _buy("b2", "AAA", 10, 110.0, "2026-01-05")
            + [_sell("s1", "AAA", 15, 120.0, "2026-01-09")])
    book = ledger.replay(rows)
    assert book.positions() == {"AAA": 5.0} and book.lots_of("AAA")[0].price == 110.0
    assert [(c["qty"], c["buy_price"], c["full"]) for c in book.closed] == [(10, 100.0, True), (5, 110.0, False)]
    assert abs(book.cash - (10000 - 1001 - 1101 + 1799)) < 1e-9
    assert book.lots_of("AAA")[0].strategy == "scout_calls" and book.lots_of("AAA")[0].call_id == "AAA-call"


def test_split_multiplies_shares_and_divides_cost():
    rows = _buy("b1", "BBB", 10, 100.0, "2026-01-02") + [ledger.line("split", NOW, "2026-02-02", symbol="BBB", ratio=2.0)]
    lot = ledger.replay(rows).lots_of("BBB")[0]
    assert (lot.qty, lot.price) == (20.0, 50.0)


def test_dividend_and_cash_lines_add_cash():
    rows = [ledger.line("cash", NOW, "s", amount=500.0), ledger.line("dividend", NOW, "s", symbol="X", amount=12.34)]
    assert ledger.replay(rows).cash == 512.34


def test_fill_amount_is_rounded_to_cents_and_signed():
    assert ledger.fill_amount("buy", 3, 33.3333, 1.0) == -101.0
    assert ledger.fill_amount("sell", 3, 33.3333, 1.0) == 99.0


def test_correction_adjusts_and_clears_a_mismatch():
    mismatch = ledger.line("mismatch", NOW, "s", details=["cash off"])
    fix = ledger.line("correction", NOW, "s", clears=mismatch["id"], cash_delta=-5.0,
                      share_deltas={"ZZZ": [3, 10.0]}, note="missed fill")
    assert ledger.unresolved_mismatches([mismatch]) == [mismatch]
    assert ledger.unresolved_mismatches([mismatch, fix]) == []
    book = ledger.replay([ledger.line("cash", NOW, "s", amount=100.0), fix])
    assert book.cash == 95.0 and book.positions() == {"ZZZ": 3.0} and book.lots_of("ZZZ")[0].strategy is None


def test_correct_refuses_without_a_terminal():
    testkit.temp_bob()
    try:
        ledger.correct("x", NOW, cash_delta=1.0, stdin=Tty(False), ask=lambda p: "CORRECT")
    except ledger.Refused as e:
        assert "terminal" in str(e)
    else:
        raise AssertionError("not refused")
    assert ledger.read() == ([], 0)


def test_correct_needs_the_typed_word_and_then_appends():
    testkit.temp_bob()
    try:
        ledger.correct("x", NOW, cash_delta=1.0, stdin=Tty(True), ask=lambda p: "yes")
    except ledger.Refused:
        pass
    else:
        raise AssertionError("not refused")
    row = ledger.correct("x", NOW, cash_delta=1.0, stdin=Tty(True), ask=lambda p: "CORRECT")
    assert ledger.read()[0] == [row] and row["kind"] == "correction" and row["cash_delta"] == 1.0


def test_each_mode_keeps_its_own_ledger_and_off_reads_the_last_active_one():
    import promotion
    import tradercfg
    testkit.temp_bob({'mode = "off"': 'mode = "paper"'})
    assert ledger.path().name == "ledger-paper.jsonl"
    testkit.temp_bob()
    assert ledger.path().name == "ledger-simulate.jsonl"
    cfg = tradercfg.load()
    promotion.record_run(NOW, "2026-10-12", {**cfg, "mode": "paper"}, "OK", 1.0)
    promotion.record_run(NOW, None, cfg, "OK", None)
    assert ledger.path().name == "ledger-paper.jsonl"


if __name__ == "__main__":
    raise SystemExit(testkit.run(globals()))
