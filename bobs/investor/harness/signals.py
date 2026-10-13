#!/usr/bin/env python3
"""The numbers behind the scorecard, each a plain function that returns None when it cannot tell.

None is the point: a company with two quarters of history must land as "could not tell", not as
a weak or strong score. Every function checks it has enough consecutive data before computing.

- ttm_growth: last four quarters of revenue over the four before.
- acceleration: this quarter's year-on-year growth minus last quarter's (is growth speeding up?).
- sue: standardised surprise against a seasonal random walk (same quarter last year), the
  classic stand-in when free analyst estimates do not exist (Foster 1977; Bernard and Thomas 1989).
- momentum_12_1: return over 12 months excluding the latest month, which tends to reverse.
- high_proximity: last close over the 52-week high.

Usage (library; screen.py calls it, test_signals.py pins it):
    signals.ttm_growth(series["revenue_q"])
"""
from __future__ import annotations

import datetime as dt
import pathlib
import statistics
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import scoutpath  # noqa: E402,F401
from fundamentals.growth import yoy_growth  # noqa: E402

QUARTER_STEP = (80, 100)
YEAR_STEP = (345, 385)
SUE_MIN_HISTORY, SUE_MAX_HISTORY = 4, 8
YEAR_BARS, MONTH_BARS = 252, 21


def _days(a: dict, b: dict) -> int:
    return (dt.date.fromisoformat(b["end"]) - dt.date.fromisoformat(a["end"])).days


def _consecutive(series: list[dict], n: int) -> list[float] | None:
    if len(series) < n:
        return None
    tail = series[-n:]
    if not all(QUARTER_STEP[0] <= _days(a, b) <= QUARTER_STEP[1] for a, b in zip(tail, tail[1:])):
        return None
    return [r["val"] for r in tail]


def ttm_growth(q: list[dict]) -> float | None:
    v = _consecutive(q, 8)
    return yoy_growth(sum(v[4:]), sum(v[:4])) if v else None


def acceleration(q: list[dict]) -> float | None:
    v = _consecutive(q, 6)
    if not v:
        return None
    now, before = yoy_growth(v[5], v[1]), yoy_growth(v[4], v[0])
    return None if now is None or before is None else now - before


def sue(q: list[dict]) -> float | None:
    changes = []
    for i, row in enumerate(q):
        prior = next((p for p in reversed(q[:i]) if YEAR_STEP[0] <= _days(p, row) <= YEAR_STEP[1]), None)
        if prior is not None:
            changes.append((row["end"], row["val"] - prior["val"]))
    if not changes or changes[-1][0] != q[-1]["end"]:
        return None
    history = [c for _, c in changes[-1 - SUE_MAX_HISTORY:-1]]
    if len(history) < SUE_MIN_HISTORY:
        return None
    spread = statistics.stdev(history)
    return changes[-1][1] / spread if spread > 0 else None


def momentum_12_1(closes: list[float]) -> float | None:
    if len(closes) < YEAR_BARS + 1 or closes[-YEAR_BARS - 1] <= 0:
        return None
    return closes[-MONTH_BARS - 1] / closes[-YEAR_BARS - 1] - 1


def high_proximity(closes: list[float]) -> float | None:
    if len(closes) < YEAR_BARS:
        return None
    high = max(closes[-YEAR_BARS:])
    return closes[-1] / high if high > 0 else None
