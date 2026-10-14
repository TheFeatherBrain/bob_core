#!/usr/bin/env python3
"""approve: a person at a terminal, the id typed back, and the books, prices and caps checked again.

Run:  python3 harness/test_approve.py
"""
from __future__ import annotations

import datetime as dt
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import traderpath  # noqa: E402,F401
import approve  # noqa: E402
import ledger  # noqa: E402
import testkit  # noqa: E402
from broker import base  # noqa: E402

LIVE = {'mode = "off"': 'mode = "live"'}
NOW = dt.datetime(2026, 1, 9, 6, 30, tzinfo=dt.timezone.utc)
DAYS = ["2026-01-05", "2026-01-06", "2026-01-07", "2026-01-08"]
OID = "2026-01-08-scout_calls-buy-AAA"


class Tty:
    def __init__(self, tty: bool):
        self.tty = tty

    def isatty(self) -> bool:
        return self.tty


class FakeLive(base.Broker):
    MODE = "live"
    submitted: list = []
    cash: float = 100_000.0
    held: dict = {}

    def account(self) -> dict:
        return {"cash": FakeLive.cash}

    def positions(self) -> dict:
        return dict(FakeLive.held)

    def submit(self, order: dict) -> None:
        FakeLive.submitted.append(order)

    def activities(self) -> list:
        return []


def _world(replace: dict | None = None, qty: int = 40) -> pathlib.Path:
    bob = testkit.temp_bob({**LIVE, **(replace or {})})
    folder = bob / "data" / "state" / "bars"
    testkit.put(folder / "_calendar.json", {"sessions": [{"date": d, "close": "16:00"} for d in DAYS]})
    for symbol, price in (("SPY", 400.0), ("AAA", 100.0)):
        testkit.put(folder / f"{symbol}.json", {"raw": [[d, price, price, price, price, 10**6] for d in DAYS],
                                                "split": [], "all": [], "evidence": []})
    ledger.append([ledger.line("cash", NOW, DAYS[0], amount=100_000.0),
                   ledger.line("proposed", NOW, DAYS[-1], order_id=OID, strategy="scout_calls", symbol="AAA",
                               side="buy", qty=qty, price=100.0, decision="accepted", caps="ok", call_id="AAA-1"),
                   ledger.line("queued", NOW, DAYS[-1], order_id=OID)])
    testkit.put(testkit.scout_state(bob) / "calls.jsonl", "")
    FakeLive.submitted, FakeLive.cash, FakeLive.held = [], 100_000.0, {}
    return bob


def _approve(stdin=Tty(True), typed: str = OID):
    return approve.approve(OID, NOW, adapter=FakeLive, stdin=stdin, ask=lambda prompt: typed)


def _refused(fn) -> str:
    try:
        fn()
    except ledger.Refused as e:
        return str(e)
    raise AssertionError("not refused")


def test_person_at_a_terminal_approves_and_it_is_submitted():
    _world()
    rows = _approve()
    assert [r["kind"] for r in rows] == ["approved", "submitted"] and FakeLive.submitted[0]["qty"] == 40


def test_refused_without_a_terminal_or_with_the_wrong_id():
    _world()
    assert "terminal" in _refused(lambda: _approve(stdin=Tty(False)))
    assert "did not match" in _refused(lambda: _approve(typed="nope"))
    assert FakeLive.submitted == []


def test_caps_are_checked_again_at_approval_time():
    _world(qty=200)
    assert "max_position" in _refused(_approve)


def test_only_queued_orders_and_only_once():
    _world()
    _approve()
    assert "already" in _refused(_approve)


def test_a_buy_is_refused_under_halt_after_the_loss_limit_or_once_withdrawn():
    import promotion
    import trade
    import tradercfg
    _world()
    trade.halt_path().write_text("2026-01-08 person: stop\n", encoding="utf-8")
    assert "HALT" in _refused(_approve)
    _world()
    promotion.record_run(NOW, DAYS[-2], tradercfg.load(), "OK", 200_000.0)
    assert "down more than" in _refused(_approve)
    bob = _world()
    testkit.put(testkit.scout_state(bob) / "calls.jsonl", '{"kind": "withdrawn", "call_id": "AAA-1"}\n')
    assert "withdrew" in _refused(_approve)
    assert FakeLive.submitted == []


def test_a_second_sell_of_the_same_holding_is_refused():
    _world()
    ledger.append([
        ledger.line("filled", NOW, DAYS[1], order_id="b0", symbol="AAA", side="buy", qty=40, price=100.0, commission=1.0)]
        + [row for oid in ("s1", "s2") for row in (
            ledger.line("proposed", NOW, DAYS[-1], order_id=oid, strategy="scout_calls", symbol="AAA", side="sell",
                        qty=40, decision="accepted", caps="exempt"),
            ledger.line("queued", NOW, DAYS[-1], order_id=oid))])
    FakeLive.cash, FakeLive.held = 100_000.0 - 4001.0, {"AAA": 40}
    approve.approve("s1", NOW, adapter=FakeLive, stdin=Tty(True), ask=lambda p: "s1")
    assert "already" in _refused(lambda: approve.approve("s2", NOW, adapter=FakeLive, stdin=Tty(True), ask=lambda p: "s2"))
    assert len(FakeLive.submitted) == 1


def test_only_in_live_mode():
    _world()
    testkit.temp_bob()
    assert "live" in _refused(_approve)


if __name__ == "__main__":
    raise SystemExit(testkit.run(globals()))
