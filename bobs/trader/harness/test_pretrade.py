#!/usr/bin/env python3
"""sizing, risk flags and caps: cautious when blind, each cap refuses one cent over and allows at it.

Run:  python3 harness/test_pretrade.py
"""
from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import traderpath  # noqa: E402,F401
import caps  # noqa: E402
import risk_flags  # noqa: E402
import sizing  # noqa: E402
import testkit  # noqa: E402

CFG = {"base_weight": 0.05, "max_position": 0.10, "max_invested": 0.80, "min_cash": 0.20, "max_orders_per_day": 10,
       "commission_per_order": 1.0, "budgets": {"scout_calls": 1.0}, "vix_flag": 25, "hy_spread_flag": 5.0,
       "throttle_floor": 0.25, "max_adv_share": 0.01, "block_flags": ["liquidity"]}


def _series(**values) -> dict:
    return {"series": {k: {"value": v, "stale": False} for k, v in values.items()}}


CALM = _series(VIXCLS=15.0, T10Y2Y=0.5, BAMLH0A0HYM2=3.0, NFCI=-0.5)


def test_conviction_runs_from_half_at_the_buy_line_to_one_at_the_top():
    assert sizing.conviction(80.0, 80) == 0.5 and sizing.conviction(100.0, 80) == 1.0
    assert sizing.conviction(90.0, 80) == 0.75 and sizing.conviction(None, 80) == 0.5


def test_throttle_counts_bad_signals_and_missing_ones_count_as_bad():
    assert sizing.throttle(CALM, CFG) == 1.0
    stressed = _series(VIXCLS=30.0, T10Y2Y=-0.2, BAMLH0A0HYM2=3.0, NFCI=-0.5)
    assert sizing.bad_signals(stressed, CFG) == ["vix", "curve"] and sizing.throttle(stressed, CFG) == 0.5
    assert sizing.throttle({}, CFG) == 0.25
    assert sizing.bad_signals(_series(T10Y2Y=0.5, BAMLH0A0HYM2=3.0, NFCI=-0.5), CFG) == ["vix"]


def test_size_rounds_down_to_whole_shares():
    s = sizing.size(100_000, 101.0, 100.0, CALM, CFG, 80)
    assert s["target_value"] == 5000.0 and s["qty"] == 49 and s["value"] == 4949.0


def test_flags_and_which_ones_block():
    card = {"groups": {"insider": 40.0}}
    found = risk_flags.flags(card, _series(VIXCLS=30.0), 2000.0, 100_000.0, CFG)
    assert found == ["vix", "insider", "liquidity"] and risk_flags.blocking(found, CFG) == ["liquidity"]
    assert risk_flags.flags({"groups": {"insider": None}}, CALM, 500.0, 100_000.0, CFG) == []
    assert risk_flags.flags({}, CALM, 500.0, None, CFG) == ["liquidity"]


def _exp(**over) -> caps.Exposure:
    base = {"equity": 100_000.0, "cash": 100_000.0, "values": {}, "orders": 0, "by_strategy": {}}
    return caps.Exposure(**{**base, **over})


def test_position_cap_refuses_one_cent_over_and_allows_at_it():
    assert caps.check_buy(_exp(), "AAA", 10_000.00, "scout_calls", CFG) is None
    assert "max_position" in caps.check_buy(_exp(), "AAA", 10_000.01, "scout_calls", CFG)


def test_invested_cash_orders_and_budget_caps():
    full = _exp(cash=20_000.0, values={"X": 75_000.0})
    assert "max_invested" in caps.check_buy(full, "AAA", 5_001.0, "scout_calls", CFG)
    poor = _exp(cash=24_000.0, values={"X": 70_000.0})
    assert caps.check_buy(poor, "AAA", 3_999.0, "scout_calls", CFG) is None
    assert "min_cash" in caps.check_buy(poor, "AAA", 3_999.02, "scout_calls", CFG)
    assert "orders today" in caps.check_buy(_exp(orders=10), "AAA", 100.0, "scout_calls", CFG)
    assert "budget" in caps.check_buy(_exp(), "AAA", 100.0, "other", CFG)


def test_orders_accepted_earlier_in_the_run_count():
    exp = _exp()
    caps.accept(exp, "AAA", 6_000.0, "scout_calls", CFG)
    assert "max_position" in caps.check_buy(exp, "AAA", 4_000.01, "scout_calls", CFG)
    assert exp.orders == 1 and exp.cash == 100_000.0 - 6_001.0
    caps.count_sell(exp)
    assert exp.orders == 2


if __name__ == "__main__":
    raise SystemExit(testkit.run(globals()))
