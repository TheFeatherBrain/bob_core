#!/usr/bin/env python3
"""How much to buy: equity x base weight x conviction x market-conditions throttle, in whole shares.

Why conviction from the call's rank: a name at the top of the Scout's ranking earns a fuller
position than one just over its buy line, and the rank is computed by code, not judged.
Why the throttle: when VIX is high, the yield curve is inverted, credit spreads are wide or
financial conditions are tight, the same pick deserves less money. A series that is missing or
stale counts as bad: not knowing is the cautious case, never the calm one.

Usage (library; trade.py calls it):
    s = sizing.size(equity, price, card["rank_pct"], macro, cfg, buy_percentile)
"""
from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import traderpath  # noqa: E402,F401
import scout_feed  # noqa: E402

STEP = 0.25


def bad_signals(macro: dict, cfg: dict) -> list[str]:
    vix = scout_feed.macro_value(macro, "VIXCLS")
    curve = scout_feed.macro_value(macro, "T10Y2Y")
    credit = scout_feed.macro_value(macro, "BAMLH0A0HYM2")
    conditions = scout_feed.macro_value(macro, "NFCI")
    checks = (("vix", vix is None or vix > cfg["vix_flag"]), ("curve", curve is None or curve < 0),
              ("credit", credit is None or credit > cfg["hy_spread_flag"]),
              ("conditions", conditions is None or conditions > 0))
    return [name for name, bad in checks if bad]


def throttle(macro: dict, cfg: dict) -> float:
    return max(cfg["throttle_floor"], 1 - STEP * len(bad_signals(macro, cfg)))


def conviction(rank_pct: float | None, buy_percentile: float | None) -> float:
    if rank_pct is None or buy_percentile is None or buy_percentile >= 100:
        return 0.5
    return min(1.0, max(0.5, 0.5 + 0.5 * (rank_pct - buy_percentile) / (100 - buy_percentile)))


def size(equity: float, price: float, rank_pct: float | None, macro: dict, cfg: dict,
         buy_percentile: float | None) -> dict:
    c, t = conviction(rank_pct, buy_percentile), throttle(macro, cfg)
    target = equity * cfg["base_weight"] * c * t
    qty = int(target // price) if price > 0 else 0
    return {"qty": qty, "value": round(qty * price, 2), "target_value": round(target, 2), "conviction": c,
            "throttle": t, "bad_signals": bad_signals(macro, cfg)}
