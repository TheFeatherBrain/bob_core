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
import fetch  # noqa: E402
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


def test_parse_owner_filings_keeps_form4_with_accessions():
    text = json.dumps({"filings": {"recent": {
        "form": ["4", "10-K", "4/A", "4"], "filingDate": ["2026-03-02", "2026-02-20", "2025-03-05", "2024-03-11"],
        "accessionNumber": ["a-1", "a-2", "a-3", "a-4"]}}})
    parsed = edgar.parse_owner_filings(text)
    assert [f["accession"] for f in parsed["filings"]] == ["a-1", "a-3", "a-4"]
    assert parsed["complete_from"] == "2024-03-11"


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


def _form4(code: str, date: str) -> str:
    return FORM4.replace("<transactionCode>P<", f"<transactionCode>{code}<").replace("2026-10-09", date)


class FakeClient:
    def __init__(self, indexes: dict, docs: dict | None = None, owners: dict | None = None,
                 broken_days: set | None = None):
        self.indexes = indexes
        self.docs = docs or {}
        self.owners = owners or {}
        self.broken_days = broken_days or set()
        self.form4_paths: list[str] = []
        self.owner_calls: list[int] = []

    def daily_index(self, day):
        if day in self.broken_days:
            raise edgar.fetch.FetchError("u", 500, "HTTP 500")
        return self.indexes.get(day), pathlib.Path("ev/index")

    def form4(self, path):
        self.form4_paths.append(path)
        doc = self.docs.get(path, FORM4)
        if doc == 404:
            raise edgar.fetch.NotFound(path, 404, "404 not found")
        return edgar.parse_form4(doc), pathlib.Path("ev/form4")

    def owner_filings(self, cik):
        self.owner_calls.append(cik)
        found = self.owners.get(cik, {"filings": [], "complete_from": "2015-01-02"})
        if found == 404:
            raise edgar.fetch.NotFound(str(cik), 404, "404 not found")
        if found == "garbled":
            return edgar.parse_owner_filings("{not json"), pathlib.Path("ev/owner")
        return found, pathlib.Path("ev/owner")

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
    assert stored[0]["evidence"] == str(pathlib.Path("ev/form4")) and stored[0]["class"] == "opportunistic"
    assert json.loads((sec / "form4" / "coverage.json").read_text()) == ["2026-10-12"]


def _owner(owner: int, octobers: dict[int, str]) -> tuple[dict, dict]:
    """Submissions with one Form 4 filed each October named, and the documents behind them."""
    filings, docs = [], {}
    for year, code in octobers.items():
        acc = f"0000000000-{year % 100}-000001"
        filings.append({"date": f"{year}-10-06", "form": "4", "accession": acc})
        docs[edgar.owner_doc_path(owner, acc)] = _form4(code, f"{year}-10-03")
    return {"filings": filings, "complete_from": "2019-05-01"}, docs


def test_classify_buy_uses_open_market_trades_only():
    today, trade = dt.date(2026, 10, 13), dt.date(2026, 10, 9)
    routine, routine_docs = _owner(11, {2023: "P", 2024: "S", 2025: "P"})
    grants, grant_docs = _owner(12, {2023: "A", 2024: "A", 2025: "A"})
    client = FakeClient({}, docs={**routine_docs, **grant_docs},
                        owners={11: routine, 12: grants, 13: {"filings": [], "complete_from": "2025-01-02"}})
    sec = pathlib.Path(tempfile.mkdtemp())
    assert edgar.classify_buy(client, sec, 11, trade, today) == "routine"
    assert edgar.classify_buy(client, sec, 12, trade, today) == "opportunistic"
    assert edgar.classify_buy(client, sec, 13, trade, today) == "unclassified"
    reads = len(client.form4_paths)
    assert edgar.classify_buy(client, sec, 11, trade, today) == "routine" and len(client.form4_paths) == reads


def test_sync_form4_keeps_coverage_when_a_later_day_fails():
    sec = pathlib.Path(tempfile.mkdtemp())
    good, bad = dt.date(2026, 10, 12), dt.date(2026, 10, 13)
    client = FakeClient({good: edgar.parse_daily_index(INDEX)}, broken_days={bad})
    try:
        edgar.sync_form4(client, sec, [good, bad], {1045810}, dt.date(2026, 10, 14))
    except edgar.fetch.FetchError:
        pass
    assert json.loads((sec / "form4" / "coverage.json").read_text()) == ["2026-10-12"]


def test_malformed_form4_is_recorded_not_fatal():
    sec = pathlib.Path(tempfile.mkdtemp())
    day = dt.date(2026, 10, 12)
    path = "edgar/data/1045810/0001045810-26-000200.txt"
    client = FakeClient({day: edgar.parse_daily_index(INDEX)}, docs={path: "<XML><ownershipDocument>broken</XML>"})
    edgar.sync_form4(client, sec, [day], {1045810}, dt.date(2026, 10, 13))
    unparsed = json.loads((sec / "form4" / "unparsed.json").read_text())
    assert unparsed == {"2026-10-12": [{"path": path, "ciks": [1045810]}]}
    assert json.loads((sec / "form4" / "coverage.json").read_text()) == ["2026-10-12"]


def test_missing_form4_is_recorded_and_the_day_still_covered():
    sec = pathlib.Path(tempfile.mkdtemp())
    day = dt.date(2026, 10, 12)
    path = "edgar/data/1045810/0001045810-26-000200.txt"
    client = FakeClient({day: edgar.parse_daily_index(INDEX)}, docs={path: 404})
    edgar.sync_form4(client, sec, [day], {1045810}, dt.date(2026, 10, 13))
    assert json.loads((sec / "form4" / "coverage.json").read_text()) == ["2026-10-12"]
    assert json.loads((sec / "form4" / "unparsed.json").read_text()) == {"2026-10-12": [{"path": path, "ciks": [1045810]}]}


def test_unreadable_history_filing_makes_the_buy_unclassified_not_a_crash():
    today, trade = dt.date(2026, 10, 13), dt.date(2026, 10, 9)
    history, docs = _owner(11, {2023: "P", 2024: "P", 2025: "P"})
    broken = edgar.owner_doc_path(11, "0000000000-24-000001")
    for bad in ("<XML><ownershipDocument>broken</XML>", 404):
        sec = pathlib.Path(tempfile.mkdtemp())
        client = FakeClient({}, docs={**docs, broken: bad}, owners={11: history})
        assert edgar.classify_buy(client, sec, 11, trade, today) == "unclassified"
        assert "2024-10" not in json.loads((sec / "owners" / "11.json").read_text())["months"]


def test_missing_or_garbled_submissions_make_the_buy_unclassified():
    today, trade = dt.date(2026, 10, 13), dt.date(2026, 10, 9)
    for bad in (404, "garbled"):
        sec = pathlib.Path(tempfile.mkdtemp())
        client = FakeClient({}, owners={11: bad})
        assert edgar.classify_buy(client, sec, 11, trade, today) == "unclassified", bad
        assert not (sec / "owners" / "11.json").exists()


def test_classify_stops_at_the_first_year_without_a_trade():
    history, docs = _owner(11, {2023: "P", 2024: "P", 2025: "A"})
    client = FakeClient({}, docs=docs, owners={11: history})
    sec = pathlib.Path(tempfile.mkdtemp())
    assert edgar.classify_buy(client, sec, 11, dt.date(2026, 10, 9), dt.date(2026, 10, 13)) == "opportunistic"
    assert client.form4_paths == [edgar.owner_doc_path(11, "0000000000-25-000001")]


def test_sync_days_reach_back_to_the_last_day_covered():
    today = dt.date(2026, 10, 13)
    assert edgar.sync_days([], today) == edgar._days_back(today, 7)
    assert edgar.sync_days(["2026-10-10"], today) == edgar._days_back(today, 7)
    assert edgar.sync_days(["2026-09-20"], today)[0] == dt.date(2026, 9, 21)
    assert len(edgar.sync_days(["2025-01-01"], today)) == 90


def test_sync_tickers_stamps_the_utc_day_it_is_given():
    sec = pathlib.Path(tempfile.mkdtemp())

    class Tickers:
        def tickers(self):
            return edgar.parse_tickers(TICKERS), pathlib.Path("ev/tickers")
    edgar.sync_tickers(Tickers(), sec, dt.date(2026, 10, 13))
    assert json.loads((sec / "tickers.json").read_text())["fetched"] == "2026-10-13"


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


def test_document_refuses_non_sec_urls():
    called = []
    client = edgar.Client("Investor Scout a@b.test", getter=lambda u, h: called.append(u) or "x",
                          limiter=edgar.fetch.Limiter(1000))
    try:
        client.document("https://example.com/filing.htm")
    except edgar.fetch.FetchError as e:
        assert "refused" in str(e) and called == []
    else:
        raise AssertionError("a non-SEC URL was read")


def test_document_saves_sec_reads():
    testkit.temp_bob()
    client = edgar.Client("Investor Scout a@b.test", getter=lambda u, h: "<html>10-Q</html>",
                          limiter=edgar.fetch.Limiter(1000))
    where = client.document("https://www.sec.gov/Archives/edgar/data/1045810/x.htm")
    assert (where / "raw.txt").read_text() == "<html>10-Q</html>"


def test_eight_ks_lists_universe_filings_once():
    day = dt.date(2026, 10, 12)
    index = INDEX.replace("10-K        ACME WIDGETS INC  ", "8-K         ACME WIDGETS INC  ")
    client = FakeClient({day: edgar.parse_daily_index(index)})
    rows = edgar.eight_ks(client, [day, day], {1234567: "ACME"})
    assert [(r["ticker"], r["form"], r["date"]) for r in rows] == [("ACME", "8-K", "2026-10-12")]
    assert rows[0]["url"] == "https://www.sec.gov/Archives/edgar/data/1234567/0001234567-26-000123.txt"


ATOM = """<?xml version="1.0" encoding="ISO-8859-1" ?>
<feed xmlns="http://www.w3.org/2005/Atom">
<company-info>
<assigned-sic>3674</assigned-sic>
<assigned-sic-desc>SEMICONDUCTORS &amp; RELATED DEVICES</assigned-sic-desc>
<cik>0001045810</cik>
</company-info>
</feed>"""
SIC_DAY = dt.date(2026, 10, 14)


class SicClient:
    def __init__(self, answers: dict):
        self.answers = answers

    def sic(self, cik: int):
        answer = self.answers[cik]
        if isinstance(answer, Exception):
            raise answer
        return answer, pathlib.Path(f"ev/sic-{cik}")


def _not_found() -> fetch.NotFound:
    e = fetch.NotFound("u", 404, "404 not found")
    e.evidence = pathlib.Path("ev/404")
    return e


def test_parse_sic_reads_the_assigned_code_or_none():
    assert edgar.parse_sic(ATOM) == 3674
    assert edgar.parse_sic(ATOM.replace(">3674<", "><")) is None
    assert edgar.parse_sic("<feed></feed>") is None


def test_sync_sic_caches_and_refreshes_after_90_days():
    sec = pathlib.Path(tempfile.mkdtemp())
    client = SicClient({1: 3674, 2: 6022})
    assert edgar.sync_sic(client, sec, {1, 2}, SIC_DAY) == 2
    assert edgar.load_sic(sec) == {1: 3674, 2: 6022}
    assert edgar.sync_sic(client, sec, {1, 2}, SIC_DAY + dt.timedelta(days=89)) == 0
    assert edgar.sync_sic(client, sec, {1, 2}, SIC_DAY + dt.timedelta(days=90)) == 2


def test_sync_sic_caches_not_found_but_retries_network_errors():
    sec = pathlib.Path(tempfile.mkdtemp())
    client = SicClient({1: _not_found(), 2: fetch.FetchError("u", 503, "HTTP 503")})
    assert edgar.sync_sic(client, sec, {1, 2}, SIC_DAY) == 1
    assert edgar.load_sic(sec) == {1: None}
    client.answers[2] = 3674
    assert edgar.sync_sic(client, sec, {1, 2}, SIC_DAY) == 1
    assert edgar.load_sic(sec) == {1: None, 2: 3674}


def test_sync_sic_stops_on_forbidden_and_keeps_what_it_read():
    sec = pathlib.Path(tempfile.mkdtemp())
    client = SicClient({1: 3674, 2: fetch.Forbidden("u", 403, "403 forbidden")})
    try:
        edgar.sync_sic(client, sec, {1, 2}, SIC_DAY)
    except fetch.Forbidden:
        pass
    else:
        raise AssertionError("403 must stop the run")
    assert edgar.load_sic(sec) == {1: 3674}


def test_load_sic_of_a_torn_file_is_empty():
    sec = pathlib.Path(tempfile.mkdtemp())
    (sec / "sic.json").write_text("{torn", encoding="utf-8")
    assert edgar.load_sic(sec) == {}


if __name__ == "__main__":
    raise SystemExit(testkit.run(globals()))
