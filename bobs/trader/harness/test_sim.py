#!/usr/bin/env python3
"""broker: one mode per adapter; the simulator fills at the next raw open with costs, sees splits.

Run:  python3 harness/test_sim.py
"""
from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import traderpath  # noqa: E402,F401
import testkit  # noqa: E402
from broker import base, sim  # noqa: E402

DAYS = ["2026-01-05", "2026-01-06", "2026-01-07", "2026-01-08", "2026-01-09", "2026-01-12", "2026-01-13"]
CFG = {"sim_starting_cash": 10000, "slippage_bps": 10, "commission_per_order": 1.0}


def _bars(bob: pathlib.Path, symbol: str, raw: list[float], split: list[float] | None = None) -> None:
    days = DAYS[:len(raw)]
    testkit.put(bob / "data" / "state" / "bars" / f"{symbol}.json", {
        "raw": [[d, c, c, c, c, 1000] for d, c in zip(days, raw)],
        "split": [[d, c] for d, c in zip(days, split or raw)], "all": [], "evidence": []})


def _sim(mode: str = "simulate") -> sim.Simulator:
    return sim.Simulator(mode, cfg=CFG)


def test_each_adapter_serves_one_mode():
    testkit.temp_bob()
    try:
        _sim("paper")
    except base.AdapterError as e:
        assert "simulate" in str(e)
    else:
        raise AssertionError("not refused")
    for mode in ("paper", "live"):
        try:
            base.adapter_for(mode)
        except base.AdapterError:
            pass
        else:
            raise AssertionError(f"{mode} must have no adapter in stage 1")


def test_buy_fills_at_next_open_with_slippage_and_commission():
    bob = testkit.temp_bob()
    _bars(bob, "SPY", [400.0] * 3)
    _bars(bob, "AAA", [100.0, 102.0, 104.0])
    broker = _sim()
    broker.submit({"order_id": "o1", "symbol": "AAA", "side": "buy", "qty": 10, "session": DAYS[0]})
    fill = broker.activities()[0]
    assert fill["kind"] == "filled" and fill["session"] == DAYS[1] and abs(fill["price"] - 102.102) < 1e-9
    assert broker.positions() == {"AAA": 10} and broker.account() == {"cash": round(10000 - 1021.02 - 1.0, 2)}
    assert sim.Simulator("simulate", cfg=CFG).activities() == broker.activities()


def test_order_waits_for_a_bar_then_expires():
    bob = testkit.temp_bob()
    _bars(bob, "SPY", [400.0] * 3)
    _bars(bob, "HALT", [50.0])
    broker = _sim()
    broker.submit({"order_id": "o1", "symbol": "HALT", "side": "buy", "qty": 1, "session": DAYS[0]})
    assert broker.activities() == []
    _bars(bob, "SPY", [400.0] * 7)
    assert broker.activities()[0]["kind"] == "expired" and broker.positions() == {}


def test_insufficient_cash_expires_the_buy():
    bob = testkit.temp_bob()
    _bars(bob, "SPY", [400.0] * 2)
    _bars(bob, "AAA", [100.0, 100.0])
    broker = _sim()
    broker.submit({"order_id": "o1", "symbol": "AAA", "side": "buy", "qty": 1000, "session": DAYS[0]})
    act = broker.activities()[0]
    assert act["kind"] == "expired" and "cash" in act["reason"] and broker.account()["cash"] == 10000


def test_split_is_seen_from_the_adjusted_series():
    bob = testkit.temp_bob()
    _bars(bob, "SPY", [400.0] * 2)
    _bars(bob, "BBB", [100.0, 100.0])
    broker = _sim()
    broker.submit({"order_id": "o1", "symbol": "BBB", "side": "buy", "qty": 10, "session": DAYS[0]})
    broker.activities()
    _bars(bob, "SPY", [400.0] * 4)
    _bars(bob, "BBB", [100.0, 100.0, 50.0, 50.0], split=[50.0, 50.0, 50.0, 50.0])
    split = [a for a in broker.activities() if a["kind"] == "split"]
    assert [(a["ratio"], a["session"]) for a in split] == [(2.0, DAYS[2])] and broker.positions() == {"BBB": 20.0}


def test_duplicate_client_order_id_refused():
    bob = testkit.temp_bob()
    _bars(bob, "SPY", [400.0])
    broker = _sim()
    order = {"order_id": "o1", "symbol": "AAA", "side": "buy", "qty": 1, "session": DAYS[0]}
    broker.submit(order)
    try:
        broker.submit(order)
    except base.AdapterError as e:
        assert "duplicate" in str(e)
    else:
        raise AssertionError("not refused")


def test_sell_never_sells_more_than_held():
    bob = testkit.temp_bob()
    _bars(bob, "SPY", [400.0] * 3)
    _bars(bob, "AAA", [100.0] * 3)
    broker = _sim()
    broker.submit({"order_id": "b", "symbol": "AAA", "side": "buy", "qty": 5, "session": DAYS[0]})
    broker.activities()
    broker.submit({"order_id": "s", "symbol": "AAA", "side": "sell", "qty": 9, "session": DAYS[1]})
    sell = broker.activities()[-1]
    assert sell["qty"] == 5 and broker.positions() == {}


def test_sell_filled_on_the_split_day_sells_every_share():
    bob = testkit.temp_bob()
    _bars(bob, "SPY", [400.0] * 2)
    _bars(bob, "BBB", [100.0, 100.0])
    broker = _sim()
    broker.submit({"order_id": "b", "symbol": "BBB", "side": "buy", "qty": 10, "session": DAYS[0]})
    broker.activities()
    broker.submit({"order_id": "s", "symbol": "BBB", "side": "sell", "qty": 10, "session": DAYS[1]})
    _bars(bob, "SPY", [400.0] * 3)
    _bars(bob, "BBB", [100.0, 100.0, 50.0], split=[50.0, 50.0, 50.0])
    sell = [a for a in broker.activities() if a.get("order_id") == "s"][0]
    assert sell["kind"] == "filled" and sell["qty"] == 20.0 and broker.positions() == {}


def test_buy_filled_on_the_split_day_keeps_its_value():
    bob = testkit.temp_bob()
    _bars(bob, "SPY", [400.0] * 2)
    _bars(bob, "BBB", [100.0, 50.0], split=[50.0, 50.0])
    broker = _sim()
    broker.submit({"order_id": "b", "symbol": "BBB", "side": "buy", "qty": 10, "session": DAYS[0]})
    assert broker.activities()[0]["qty"] == 20 and broker.positions() == {"BBB": 20}


def test_split_ratio_is_snapped_to_a_simple_fraction():
    bob = testkit.temp_bob()
    _bars(bob, "SPY", [400.0] * 2)
    _bars(bob, "CCC", [99.0, 99.0])
    broker = _sim()
    broker.submit({"order_id": "b", "symbol": "CCC", "side": "buy", "qty": 10, "session": DAYS[0]})
    broker.activities()
    _bars(bob, "SPY", [400.0] * 3)
    _bars(bob, "CCC", [99.0, 99.0, 33.0], split=[33.0001, 33.0001, 33.0])
    split = [a for a in broker.activities() if a["kind"] == "split"]
    assert [a["ratio"] for a in split] == [3.0] and broker.positions() == {"CCC": 30.0}


if __name__ == "__main__":
    raise SystemExit(testkit.run(globals()))
