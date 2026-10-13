#!/usr/bin/env python3
"""xbrl: series built from each fact's own dates, so offset fiscal years are not mislabelled.

Run:  python3 harness/test_xbrl.py
"""
from __future__ import annotations

import json
import pathlib
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import scoutpath  # noqa: E402,F401
import testkit  # noqa: E402
import xbrl  # noqa: E402

CIK = 1045810


def _frame(sec: pathlib.Path, stem: str, rows: list[dict]) -> None:
    (sec / "frames").mkdir(parents=True, exist_ok=True)
    (sec / "frames" / f"{stem}.json").write_text(json.dumps(
        {"fetched": "2026-10-13", "evidence": f"ev/{stem}", "period_end": "x", "rows": rows}))


def _row(start: str | None, end: str, val: float) -> dict:
    row = {"cik": CIK, "val": val, "end": end, "accn": "a"}
    if start:
        row["start"] = start
    return row


def test_load_frames_prefers_the_first_variant():
    sec = pathlib.Path(tempfile.mkdtemp())
    _frame(sec, "us-gaap.Revenues.USD.CY2026Q2", [_row("2026-04-01", "2026-06-30", 999)])
    _frame(sec, "us-gaap.RevenueFromContractWithCustomerExcludingAssessedTax.USD.CY2026Q2",
           [_row("2026-04-01", "2026-06-30", 100)])
    rows = xbrl.load_frames(sec)[("revenue", "quarter")][CIK]
    assert [r["val"] for r in rows] == [100]
    assert rows[0]["evidence"] == "ev/us-gaap.RevenueFromContractWithCustomerExcludingAssessedTax.USD.CY2026Q2"


def test_q4_derived_for_offset_fiscal_year():
    quarters = [_row("2025-01-27", "2025-04-27", 40), _row("2025-04-28", "2025-07-27", 45),
                _row("2025-07-28", "2025-10-26", 50)]
    annual = [_row("2025-01-27", "2026-01-25", 200)]
    for r in quarters + annual:
        r["evidence"] = "ev"
    series = xbrl.quarterly_series(quarters, annual)
    assert [(s["end"], s["val"], s["derived"]) for s in series] == [
        ("2025-04-27", 40, False), ("2025-07-27", 45, False), ("2025-10-26", 50, False), ("2026-01-25", 65, True)]
    assert series[-1]["start"] == "2025-10-27"


def test_no_derivation_without_three_quarters():
    quarters = [_row("2025-01-27", "2025-04-27", 40), _row("2025-07-28", "2025-10-26", 50)]
    annual = [_row("2025-01-27", "2026-01-25", 200)]
    for r in quarters + annual:
        r["evidence"] = "ev"
    assert [s["end"] for s in xbrl.quarterly_series(quarters, annual)] == ["2025-04-27", "2025-10-26"]


def test_six_month_ytd_values_are_not_quarters():
    rows = [_row("2025-01-01", "2025-06-30", 90)]
    rows[0]["evidence"] = "ev"
    assert xbrl.quarterly_series(rows, []) == []


def test_annual_facts_match_instants_by_date():
    sec = pathlib.Path(tempfile.mkdtemp())
    for year, start, end, ni, assets in (("CY2024", "2024-01-29", "2025-01-26", 70, 110),
                                         ("CY2025", "2025-01-27", "2026-01-25", 100, 150)):
        _frame(sec, f"us-gaap.NetIncomeLoss.USD.{year}", [_row(start, end, ni)])
        _frame(sec, f"us-gaap.Revenues.USD.{year}", [_row(start, end, ni * 2)])
    _frame(sec, "us-gaap.Assets.USD.CY2024Q4I", [_row(None, "2025-01-26", 110)])
    _frame(sec, "us-gaap.Assets.USD.CY2025Q4I", [_row(None, "2026-01-25", 150)])
    _frame(sec, "dei.EntityCommonStockSharesOutstanding.shares.CY2026Q1I", [_row(None, "2026-03-01", 24.3)])
    current, previous, evidence = xbrl.annual_facts(xbrl.load_frames(sec), CIK)
    assert (current.net_income, current.total_assets, current.revenue) == (100, 150, 200)
    assert (previous.net_income, previous.total_assets) == (70, 110)
    assert current.shares_outstanding == 24.3 and previous.shares_outstanding is None
    assert current.market_cap is None and evidence


def test_annual_facts_none_without_annual_rows():
    assert xbrl.annual_facts({}, CIK) == (None, None, [])


def test_latest_shares():
    sec = pathlib.Path(tempfile.mkdtemp())
    _frame(sec, "dei.EntityCommonStockSharesOutstanding.shares.CY2026Q1I", [_row(None, "2026-03-01", 24.3)])
    _frame(sec, "dei.EntityCommonStockSharesOutstanding.shares.CY2026Q2I", [_row(None, "2026-05-20", 24.4)])
    value, ev = xbrl.latest_shares(xbrl.load_frames(sec), CIK)
    assert value == 24.4 and ev.endswith("CY2026Q2I")


if __name__ == "__main__":
    raise SystemExit(testkit.run(globals()))
