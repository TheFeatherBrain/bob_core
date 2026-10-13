#!/usr/bin/env python3
"""SIC code to Fama-French 12 industry, and which SIC codes are financial companies.

Why sectors: revenue growth and Piotroski scores differ by industry (a utility growing 6% is fast,
a software firm growing 6% is slow), so ranking them against all stocks rewards the industry, not
the company. Fama-French 12 is a published, fixed mapping: fine enough to separate chipmakers from
food companies, coarse enough that most groups hold dozens of listed names.

Why is_financial: Altman Z was fitted on manufacturers. For banks, insurers and real estate the
balance-sheet ratios it uses mean something else, so its distress veto would only be noise.

Source: Kenneth R. French, "Detail for 12 Industry Portfolios": Siccodes12.txt from the linked
Siccodes12.zip [ev:2026-10-13/ff12-siccodes-220848-6abc7941]; the page itself
[ev:2026-10-13/ff12-definition-220834-10096fe0].

Usage (library; screen.py calls it):
    sectors.sector(3674)        # "BusEq"
    sectors.is_financial(6022)  # True
"""
from __future__ import annotations

OTHER = "Other"
FF12: tuple[tuple[str, tuple[tuple[int, int], ...]], ...] = (
    ("NoDur", ((100, 999), (2000, 2399), (2700, 2749), (2770, 2799), (3100, 3199), (3940, 3989))),
    ("Durbl", ((2500, 2519), (2590, 2599), (3630, 3659), (3710, 3711), (3714, 3714), (3716, 3716),
               (3750, 3751), (3792, 3792), (3900, 3939), (3990, 3999))),
    ("Manuf", ((2520, 2589), (2600, 2699), (2750, 2769), (3000, 3099), (3200, 3569), (3580, 3629),
               (3700, 3709), (3712, 3713), (3715, 3715), (3717, 3749), (3752, 3791), (3793, 3799),
               (3830, 3839), (3860, 3899))),
    ("Enrgy", ((1200, 1399), (2900, 2999))),
    ("Chems", ((2800, 2829), (2840, 2899))),
    ("BusEq", ((3570, 3579), (3660, 3692), (3694, 3699), (3810, 3829), (7370, 7379))),
    ("Telcm", ((4800, 4899),)),
    ("Utils", ((4900, 4949),)),
    ("Shops", ((5000, 5999), (7200, 7299), (7600, 7699))),
    ("Hlth", ((2830, 2839), (3693, 3693), (3840, 3859), (8000, 8099))),
    ("Money", ((6000, 6999),)),
)
FINANCIAL = (6000, 6799)


def sector(sic: int | None) -> str:
    if sic is None:
        return OTHER
    return next((name for name, ranges in FF12 if any(lo <= sic <= hi for lo, hi in ranges)), OTHER)


def is_financial(sic: int | None) -> bool:
    return sic is not None and FINANCIAL[0] <= sic <= FINANCIAL[1]
