#!/usr/bin/env python3
"""sectors: Fama-French 12 by SIC range, no code in two sectors, financials are SIC 6000 to 6799.

Run:  python3 harness/test_sectors.py
"""
from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import scoutpath  # noqa: E402,F401
import sectors  # noqa: E402
import testkit  # noqa: E402


def test_known_codes_map_to_their_industry():
    cases = {3674: "BusEq", 7372: "BusEq", 2834: "Hlth", 3841: "Hlth", 1311: "Enrgy", 6022: "Money",
             4911: "Utils", 4813: "Telcm", 5411: "Shops", 2080: "NoDur", 3711: "Durbl", 3560: "Manuf",
             2820: "Chems"}
    for sic, name in cases.items():
        assert sectors.sector(sic) == name, (sic, sectors.sector(sic))


def test_unknown_and_unmapped_codes_are_other():
    assert sectors.sector(None) == "Other"
    assert sectors.sector(9999) == "Other" and sectors.sector(8111) == "Other"


def test_no_code_is_in_two_sectors():
    for sic in range(10000):
        hits = [name for name, ranges in sectors.FF12 if any(lo <= sic <= hi for lo, hi in ranges)]
        assert len(hits) <= 1, (sic, hits)


def test_financial_is_6000_to_6799():
    assert sectors.is_financial(6000) and sectors.is_financial(6022) and sectors.is_financial(6799)
    assert not sectors.is_financial(6800) and not sectors.is_financial(5999)
    assert not sectors.is_financial(3674) and not sectors.is_financial(None)


if __name__ == "__main__":
    raise SystemExit(testkit.run(globals()))
