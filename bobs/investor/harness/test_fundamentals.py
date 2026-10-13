#!/usr/bin/env python3
"""Fundamentals: hand-worked numbers, so a slip in the maths shows as a wrong value.

Run:  python3 harness/test_fundamentals.py
"""
from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import scoutpath  # noqa: E402,F401
import testkit  # noqa: E402
from fundamentals import growth, quality, valuation  # noqa: E402

PREV = quality.AnnualFacts(net_income=5, operating_cash_flow=6, total_assets=100, current_assets=40,
                           current_liabilities=30, long_term_debt=30, shares_outstanding=10,
                           revenue=80, gross_profit=32)
CURR = quality.AnnualFacts(net_income=8, operating_cash_flow=12, total_assets=100, current_assets=50,
                           current_liabilities=30, long_term_debt=20, shares_outstanding=10,
                           revenue=100, gross_profit=45)


def test_yoy_growth():
    assert abs(growth.yoy_growth(120, 100) - 0.2) < 1e-12
    assert growth.yoy_growth(120, 0) is None and growth.yoy_growth(None, 100) is None


def test_piotroski_all_nine_pass():
    r = quality.piotroski_f_score(CURR, PREV)
    assert (r["score"], r["max_possible"]) == (9, 9), r


def test_piotroski_missing_input_skips_only_that_test():
    curr = quality.AnnualFacts(net_income=8, operating_cash_flow=12, total_assets=100, current_assets=50,
                               current_liabilities=30, long_term_debt=20, shares_outstanding=None,
                               revenue=100, gross_profit=45)
    r = quality.piotroski_f_score(curr, PREV)
    assert (r["score"], r["max_possible"]) == (8, 8) and r["components"]["no_dilution"] is None


def test_altman_hand_worked():
    facts = quality.AnnualFacts(total_assets=100, current_assets=50, current_liabilities=30,
                                retained_earnings=30, ebit=10, market_cap=200, total_liabilities=50,
                                revenue=100)
    # 1.2*0.2 + 1.4*0.3 + 3.3*0.1 + 0.6*4 + 1.0*1 = 4.39
    assert abs(quality.altman_z_score(facts) - 4.39) < 1e-9


def test_altman_none_without_current_assets():
    assert quality.altman_z_score(quality.AnnualFacts(total_assets=100)) is None


def test_dcf_hand_worked():
    # year 1: 110 / 1.1 = 100; terminal 110 / 0.10 = 1100, discounted 1000; total 1100
    assert abs(valuation.simplified_dcf(100, 0.10, 0.10, years=1, terminal_growth=0.0) - 1100) < 1e-9


def test_reverse_dcf_recovers_growth():
    target = valuation.simplified_dcf(100, 0.25, 0.10, years=10)
    assert abs(valuation.reverse_dcf(target, 100) - 0.25) < 1e-6


def test_reverse_dcf_refuses_bad_inputs():
    assert valuation.reverse_dcf(1000, -5) is None
    assert valuation.reverse_dcf(1e18, 1) is None


if __name__ == "__main__":
    raise SystemExit(testkit.run(globals()))
