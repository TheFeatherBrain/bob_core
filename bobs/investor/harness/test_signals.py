#!/usr/bin/env python3
"""signals: each formula against numbers worked by hand, and None whenever the input is short.

Run:  python3 harness/test_signals.py
"""
from __future__ import annotations

import datetime as dt
import math
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import scoutpath  # noqa: E402,F401
import signals  # noqa: E402
import testkit  # noqa: E402


def _q(values: list[float], gap_after: int | None = None) -> list[dict]:
    end, out = dt.date(2023, 3, 31), []
    for i, v in enumerate(values):
        out.append({"end": end.isoformat(), "val": v})
        end += dt.timedelta(days=182 if i == gap_after else 91)
    return out


def test_ttm_growth():
    assert abs(signals.ttm_growth(_q([10, 10, 10, 10, 12, 12, 12, 12])) - 0.2) < 1e-12


def test_ttm_growth_needs_eight_consecutive_quarters():
    assert signals.ttm_growth(_q([10] * 7)) is None
    assert signals.ttm_growth(_q([10] * 8, gap_after=3)) is None


def test_acceleration():
    # latest 15 vs 10 a year ago = +50%; previous 13 vs 10 = +30%; acceleration +20 points
    assert abs(signals.acceleration(_q([10, 10, 11, 12, 13, 15])) - 0.2) < 1e-12


def test_sue_hand_worked():
    # year-on-year changes 1, 0, 1, 0, 1 then latest 2; sample stdev of the five = sqrt(0.3)
    value = signals.sue(_q([1, 1, 1, 1, 2, 1, 2, 1, 3, 3]))
    assert abs(value - 2 / math.sqrt(0.3)) < 1e-9


def test_sue_needs_history_and_variation():
    assert signals.sue(_q([1, 1, 1, 1, 2, 1, 2])) is None
    assert signals.sue(_q([1, 1, 1, 1, 2, 2, 2, 2, 3, 3])) is None


def test_momentum_skips_the_last_month():
    closes = [100.0] * 253
    closes[-22] = 130.0
    assert abs(signals.momentum_12_1(closes) - 0.3) < 1e-12
    assert signals.momentum_12_1(closes[1:]) is None


def test_high_proximity():
    closes = [100.0] * 252
    closes[10] = 125.0
    assert abs(signals.high_proximity(closes) - 0.8) < 1e-12
    assert signals.high_proximity(closes[1:]) is None


if __name__ == "__main__":
    raise SystemExit(testkit.run(globals()))
