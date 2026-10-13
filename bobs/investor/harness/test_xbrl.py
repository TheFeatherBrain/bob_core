#!/usr/bin/env python3
"""xbrl: series built from each fact's own dates, so offset fiscal years are not mislabelled.

Run:  python3 harness/test_xbrl.py
"""
from __future__ import annotations

import datetime as dt
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


class FakeSec:
    """Serves frames the way SEC files them: each fact under the calendar frame nearest its dates."""

    def __init__(self, facts: dict[tuple[str, str], list[dict]]):
        self.facts = facts

    def frame(self, tax, concept, unit, period):
        return self.facts.get((concept, period), []), pathlib.Path(f"ev/{concept}.{period}")


def _company(cik: int, years: list[tuple[str, str, str]], share_dates: list[tuple[str, str]]) -> dict:
    """years: (frame, start, end) for two fiscal years; share_dates: (frame, date) after each year end."""
    facts: dict[tuple[str, str], list[dict]] = {}

    def put(concept: str, period: str, row: dict) -> None:
        facts.setdefault((concept, period), []).append({"cik": cik, "accn": "a", **row})
    for i, (frame, start, end) in enumerate(years):
        grow = 1 + i
        for concept, val in (("NetIncomeLoss", 8 * grow), ("NetCashProvidedByUsedInOperatingActivities", 12 * grow),
                             ("Revenues", 100 * grow), ("GrossProfit", 45 * grow), ("OperatingIncomeLoss", 10 * grow)):
            put(concept, frame, {"start": start, "end": end, "val": val})
        instant = f"{frame}Q4I"
        for concept, val in (("Assets", 100), ("AssetsCurrent", 50 + i), ("LiabilitiesCurrent", 30),
                             ("LongTermDebtNoncurrent", 20 - i), ("Liabilities", 50),
                             ("RetainedEarningsAccumulatedDeficit", 30)):
            put(concept, instant, {"end": end, "val": val})
    for frame, day in share_dates:
        put("EntityCommonStockSharesOutstanding", frame, {"end": day, "val": 10})
    return facts


def _merge(*companies: dict) -> dict:
    out: dict = {}
    for facts in companies:
        for key, rows in facts.items():
            out.setdefault(key, []).extend(rows)
    return out


def test_frame_jobs_fetch_the_prior_year_balance_sheet():
    from fundamentals import quality
    sec = pathlib.Path(tempfile.mkdtemp())
    calendar = _company(1, [("CY2024", "2024-01-01", "2024-12-31"), ("CY2025", "2025-01-01", "2025-12-31")],
                        [("CY2025Q1I", "2025-02-10"), ("CY2026Q1I", "2026-02-10")])
    january = _company(2, [("CY2024", "2024-01-29", "2025-01-26"), ("CY2025", "2025-01-27", "2026-01-25")],
                       [("CY2025Q1I", "2025-03-05"), ("CY2026Q1I", "2026-03-05")])
    xbrl.edgar.sync_frames(FakeSec(_merge(calendar, january)), sec, dt.date(2026, 10, 13), refresh_days=3)
    frames = xbrl.load_frames(sec)
    for cik in (1, 2):
        current, previous, _ = xbrl.annual_facts(frames, cik)
        assert previous is not None and previous.total_assets == 100 and previous.shares_outstanding == 10, cik
        assert quality.piotroski_f_score(current, previous)["max_possible"] == 9, cik


def test_latest_shares_falls_back_to_diluted_shares_for_multi_class():
    sec = pathlib.Path(tempfile.mkdtemp())
    _frame(sec, "us-gaap.WeightedAverageNumberOfDilutedSharesOutstanding.shares.CY2026Q1",
           [_row("2026-01-01", "2026-03-31", 2150.0)])
    _frame(sec, "us-gaap.WeightedAverageNumberOfDilutedSharesOutstanding.shares.CY2026Q2",
           [_row("2026-04-01", "2026-06-30", 2160.0)])
    value, ev = xbrl.latest_shares(xbrl.load_frames(sec), CIK)
    assert value == 2160.0 and ev.endswith("CY2026Q2")
    assert xbrl.latest_shares({}, CIK) == (None, None)


def test_eps_q4_is_derived_like_revenue():
    sec = pathlib.Path(tempfile.mkdtemp())
    for i, (start, end) in enumerate((("2025-01-01", "2025-03-31"), ("2025-04-01", "2025-06-30"),
                                      ("2025-07-01", "2025-09-30"))):
        _frame(sec, f"us-gaap.EarningsPerShareDiluted.USD-per-shares.CY2025Q{i + 1}", [_row(start, end, 1.0)])
    _frame(sec, "us-gaap.EarningsPerShareDiluted.USD-per-shares.CY2025", [_row("2025-01-01", "2025-12-31", 4.5)])
    eps = xbrl.company_series(xbrl.load_frames(sec), CIK)["eps_q"]
    assert [(r["end"], r["val"], r["derived"]) for r in eps][-1] == ("2025-12-31", 1.5, True)


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
