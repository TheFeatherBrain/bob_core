#!/usr/bin/env python3
"""edgar: parsers on SEC's real formats, frame caching, and Form 4 coverage that never lies.

Run:  python3 harness/test_edgar.py
"""
from __future__ import annotations

import datetime as dt
import json
import pathlib
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import scoutpath  # noqa: E402,F401
import edgar  # noqa: E402
import testkit  # noqa: E402

TICKERS = json.dumps({"fields": ["cik", "name", "ticker", "exchange"], "data": [
    [1045810, "NVIDIA CORP", "NVDA", "Nasdaq"], [1067983, "BERKSHIRE HATHAWAY INC", "BRK-B", "NYSE"],
    [1, "SOME OTC CO", "SOTC", "OTC"], [2, "NO TICKER", "", "Nasdaq"]]})

FRAME = json.dumps({"data": [
    {"accn": "0001-26-1", "cik": 1045810, "entityName": "NVIDIA", "loc": "US-CA",
     "start": "2026-04-28", "end": "2026-07-27", "val": 46743000000},
    {"accn": "0001-26-2", "cik": 2, "entityName": "X", "loc": "US-NY", "end": "2026-06-30", "val": 5}]})

INDEX = """Description:           Daily Index of EDGAR Dissemination Feed by Form Type
Last Data Received:    Oct 12, 2026

Form Type   Company Name                                                  CIK         Date Filed  File Name
---------------------------------------------------------------------------------------------------------------------------------------------
10-K        ACME WIDGETS INC                                              1234567     20261012    edgar/data/1234567/0001234567-26-000123.txt
4           NVIDIA CORP                                                   1045810     20261012    edgar/data/1045810/0001045810-26-000200.txt
4           HUANG JEN HSUN                                                1197649     20261012    edgar/data/1045810/0001045810-26-000200.txt
SC 13G      SOME FUND LP                                                  7654321     20261012    edgar/data/7654321/0007654321-26-000001.txt
"""

FORM4 = """<SEC-DOCUMENT>
<DOCUMENT>
<TYPE>4
<TEXT>
<XML>
<?xml version="1.0"?>
<ownershipDocument>
  <issuer><issuerCik>0001045810</issuerCik><issuerTradingSymbol>NVDA</issuerTradingSymbol></issuer>
  <reportingOwner><reportingOwnerId><rptOwnerCik>0001197649</rptOwnerCik><rptOwnerName>HUANG JEN HSUN</rptOwnerName></reportingOwnerId></reportingOwner>
  <aff10b5One>0</aff10b5One>
  <nonDerivativeTable>
    <nonDerivativeTransaction>
      <transactionDate><value>2026-10-09</value></transactionDate>
      <transactionCoding><transactionCode>P</transactionCode></transactionCoding>
      <transactionAmounts>
        <transactionShares><value>1000</value></transactionShares>
        <transactionPricePerShare><value>180.5</value></transactionPricePerShare>
        <transactionAcquiredDisposedCode><value>A</value></transactionAcquiredDisposedCode>
      </transactionAmounts>
    </nonDerivativeTransaction>
  </nonDerivativeTable>
</ownershipDocument>
</XML>
</TEXT>
</DOCUMENT>
</SEC-DOCUMENT>"""

SUBMISSIONS = json.dumps({"filings": {"recent": {
    "form": ["4", "10-K", "4/A", "4"], "filingDate": ["2026-03-02", "2026-02-20", "2025-03-05", "2024-03-11"]}}})


def test_parse_tickers_keeps_only_nyse_nasdaq_with_a_ticker():
    rows = edgar.parse_tickers(TICKERS)
    assert [r["ticker"] for r in rows] == ["NVDA", "BRK-B"] and rows[0]["cik"] == 1045810


def test_sec_to_alpaca_symbol():
    assert edgar.sec_to_alpaca("BRK-B") == "BRK.B" and edgar.sec_to_alpaca("nvda") == "NVDA"


def test_parse_frame_keeps_start_only_for_durations():
    rows = edgar.parse_frame(FRAME)
    assert rows[0]["start"] == "2026-04-28" and rows[0]["val"] == 46743000000.0
    assert "start" not in rows[1] and rows[1]["cik"] == 2


def test_parse_daily_index_handles_spaced_form_types():
    rows = edgar.parse_daily_index(INDEX)
    assert [r["form"] for r in rows] == ["10-K", "4", "4", "SC 13G"]
    assert rows[1]["cik"] == 1045810 and rows[1]["date"] == "2026-10-12"
    assert rows[3]["path"] == "edgar/data/7654321/0007654321-26-000001.txt"


def test_parse_form4_purchase():
    (t,) = edgar.parse_form4(FORM4)
    assert (t["issuer_cik"], t["owner_cik"], t["code"], t["date"]) == (1045810, 1197649, "P", "2026-10-09")
    assert t["shares"] == 1000 and t["price"] == 180.5 and t["acquired"] and not t["plan"]


def test_parse_form4_plan_flag():
    (t,) = edgar.parse_form4(FORM4.replace("<aff10b5One>0</aff10b5One>", "<aff10b5One>true</aff10b5One>"))
    assert t["plan"]


def test_parse_form4_without_xml_is_empty():
    assert edgar.parse_form4("<SEC-DOCUMENT>no xml</SEC-DOCUMENT>") == []


def test_parse_owner_dates_keeps_form4_only():
    assert edgar.parse_owner_dates(SUBMISSIONS) == [dt.date(2026, 3, 2), dt.date(2025, 3, 5), dt.date(2024, 3, 11)]


def test_recent_quarters_excludes_the_current_one():
    qs = edgar.recent_quarters(dt.date(2026, 10, 13), 13)
    assert len(qs) == 13 and qs[-1] == (2026, 3) and qs[0] == (2023, 3)
    assert edgar.quarter_end(2026, 1) == dt.date(2026, 3, 31) and edgar.quarter_end(2025, 4) == dt.date(2025, 12, 31)


def test_frame_jobs_cover_every_concept_variant():
    jobs = edgar.frame_jobs(dt.date(2026, 10, 13))
    periods = {(j["name"], j["period"]) for j in jobs}
    assert ("revenue", "CY2026Q3") in periods and ("revenue", "CY2025") in periods
    assert ("shares", "CY2026Q3I") in periods and ("eps", "CY2023Q3") in periods
    assert not any(j["period"] == "CY2026" for j in jobs)


class FakeClient:
    def __init__(self, indexes: dict):
        self.indexes = indexes
        self.form4_paths: list[str] = []
        self.owner_calls: list[int] = []

    def daily_index(self, day):
        return self.indexes.get(day), pathlib.Path("ev/index")

    def form4(self, path):
        self.form4_paths.append(path)
        return edgar.parse_form4(FORM4), pathlib.Path("ev/form4")

    def owner_dates(self, cik):
        self.owner_calls.append(cik)
        return [dt.date(2025, 3, 5)], pathlib.Path("ev/owner")

    def frame(self, tax, concept, unit, period):
        return [{"cik": 1, "val": 1.0, "end": "2026-06-30"}], pathlib.Path("ev/frame")


def test_sync_form4_reads_universe_issuers_once():
    sec = pathlib.Path(tempfile.mkdtemp())
    day = dt.date(2026, 10, 12)
    client = FakeClient({day: edgar.parse_daily_index(INDEX)})
    summary = edgar.sync_form4(client, sec, [day], {1045810, 1197649}, dt.date(2026, 10, 13))
    assert client.form4_paths == ["edgar/data/1045810/0001045810-26-000200.txt"]
    assert client.owner_calls == [1197649] and summary["purchases"] == 1
    stored = json.loads((sec / "form4" / "2026-10-12.json").read_text())
    assert stored[0]["evidence"] == str(pathlib.Path("ev/form4"))
    assert json.loads((sec / "form4" / "coverage.json").read_text()) == ["2026-10-12"]


def test_sync_form4_weekend_404_is_covered_recent_weekday_404_is_not():
    sec = pathlib.Path(tempfile.mkdtemp())
    saturday, monday = dt.date(2026, 10, 10), dt.date(2026, 10, 12)
    edgar.sync_form4(FakeClient({}), sec, [saturday, monday], set(), dt.date(2026, 10, 13))
    assert json.loads((sec / "form4" / "coverage.json").read_text()) == ["2026-10-10"]


def test_sync_frames_fetches_missing_and_keeps_settled():
    sec = pathlib.Path(tempfile.mkdtemp())
    today = dt.date(2026, 10, 13)
    first = edgar.sync_frames(FakeClient({}), sec, today, refresh_days=3)
    again = edgar.sync_frames(FakeClient({}), sec, today, refresh_days=3)
    later = edgar.sync_frames(FakeClient({}), sec, today + dt.timedelta(days=3), refresh_days=3)
    assert first == len(edgar.frame_jobs(today)) and again == 0
    recent = [j for j in edgar.frame_jobs(today) if (today + dt.timedelta(days=3) - j["period_end"]).days <= edgar.SETTLED_DAYS]
    assert later == len(recent) and 0 < later < first


def test_client_sends_contact_and_turns_404_into_empty_frame():
    testkit.temp_bob()
    seen = {}

    def getter(url, headers):
        seen.update(headers)
        raise edgar.fetch.NotFound(url, 404, "404 not found")
    client = edgar.Client("Investor Scout a@b.test", getter=getter, limiter=edgar.fetch.Limiter(1000))
    rows, where = client.frame("us-gaap", "Revenues", "USD", "CY2026Q3")
    assert rows == [] and where.is_dir() and seen["User-Agent"] == "Investor Scout a@b.test"


if __name__ == "__main__":
    raise SystemExit(testkit.run(globals()))
