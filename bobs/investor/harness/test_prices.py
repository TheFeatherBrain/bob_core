#!/usr/bin/env python3
"""prices: Eastern time without zoneinfo, holiday-aware staleness, re-adjusted history refetched.

Run:  python3 harness/test_prices.py
"""
from __future__ import annotations

import datetime as dt
import json
import pathlib
import sys
import tempfile
import urllib.parse

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import scoutpath  # noqa: E402,F401
import fetch  # noqa: E402
import marketclock  # noqa: E402
import prices  # noqa: E402
import testkit  # noqa: E402

UTC = dt.timezone.utc


def _utc(*args) -> dt.datetime:
    return dt.datetime(*args, tzinfo=UTC)


def test_et_offset_dst_edges():
    hours = lambda t: marketclock.et_offset(t).total_seconds() / 3600  # noqa: E731
    assert hours(_utc(2026, 3, 8, 6, 59)) == -5 and hours(_utc(2026, 3, 8, 7, 0)) == -4
    assert hours(_utc(2026, 11, 1, 5, 59)) == -4 and hours(_utc(2026, 11, 1, 6, 0)) == -5


def test_last_session_waits_for_close_plus_an_hour():
    sessions = [{"date": "2026-10-09", "close": "16:00"}, {"date": "2026-10-12", "close": "16:00"}]
    assert marketclock.last_session(sessions, _utc(2026, 10, 12, 21, 0)) == "2026-10-12"
    assert marketclock.last_session(sessions, _utc(2026, 10, 12, 20, 30)) == "2026-10-09"


def test_not_stale_over_weekend_and_holiday():
    sessions = [{"date": "2026-07-02", "close": "16:00"}, {"date": "2026-07-06", "close": "16:00"}]
    assert not marketclock.is_stale("2026-07-02", sessions, _utc(2026, 7, 4, 6, 30))
    weekend = [{"date": "2026-10-09", "close": "16:00"}]
    assert not marketclock.is_stale("2026-10-09", weekend, _utc(2026, 10, 10, 5, 30))


def test_stale_when_last_bar_is_behind_or_missing():
    sessions = [{"date": "2026-10-09", "close": "16:00"}, {"date": "2026-10-12", "close": "16:00"}]
    now = _utc(2026, 10, 13, 5, 30)
    assert marketclock.is_stale("2026-10-09", sessions, now) and marketclock.is_stale(None, sessions, now)


def test_merge_appends_and_flags_disagreement():
    old = [["2026-10-08", 10.0, 5], ["2026-10-09", 11.0, 5]]
    merged, changed = prices.merge(old, [["2026-10-09", 11.0, 6], ["2026-10-12", 12.0, 7]])
    assert [b[0] for b in merged] == ["2026-10-08", "2026-10-09", "2026-10-12"] and not changed
    _, changed = prices.merge(old, [["2026-10-09", 5.5, 6]])
    assert changed


class FakeBars:
    def __init__(self, recent: dict, full: dict, broken: str | None = None):
        self.recent, self.full, self.full_calls, self.starts, self.broken = recent, full, [], [], broken

    def bars(self, symbols, start, end):
        if self.broken in symbols:
            raise fetch.FetchError("u", 500, "HTTP 500")
        if (end - start).days <= prices.RECENT_DAYS:
            return {s: self.recent[s] for s in symbols if s in self.recent}, [pathlib.Path("ev/recent")]
        self.full_calls.append(list(symbols))
        self.starts.append(start)
        return {s: self.full[s] for s in symbols if s in self.full}, [pathlib.Path("ev/full")]


def _store(folder: pathlib.Path, symbol: str, bars: list) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{symbol}.json").write_text(json.dumps({"bars": bars, "evidence": ["ev/old"]}))


def test_overlap_disagreement_triggers_full_refetch():
    folder = pathlib.Path(tempfile.mkdtemp())
    _store(folder, "AAA", [["2026-10-08", 20.0, 1], ["2026-10-09", 22.0, 1]])
    _store(folder, "BBB", [["2026-10-09", 5.0, 1]])
    client = FakeBars(recent={"AAA": [["2026-10-09", 11.0, 1], ["2026-10-12", 11.5, 1]],
                              "BBB": [["2026-10-09", 5.0, 1], ["2026-10-12", 5.1, 1]]},
                      full={"AAA": [["2026-10-08", 10.0, 1], ["2026-10-09", 11.0, 1], ["2026-10-12", 11.5, 1]]})
    summary = prices.refresh(client, ["AAA", "BBB"], dt.date(2026, 10, 13), folder)
    assert client.full_calls == [["AAA"]] and summary == {"symbols": 2, "full": 1, "failed": []}
    assert prices.load("AAA", folder)["bars"][0] == ["2026-10-08", 10.0, 1]
    assert [b[0] for b in prices.load("BBB", folder)["bars"]] == ["2026-10-09", "2026-10-12"]


def test_new_symbol_gets_full_history():
    folder = pathlib.Path(tempfile.mkdtemp())
    client = FakeBars(recent={"NEW": [["2026-10-12", 3.0, 1]]}, full={"NEW": [["2025-01-02", 1.0, 1]]})
    prices.refresh(client, ["NEW"], dt.date(2026, 10, 13), folder)
    assert client.full_calls == [["NEW"]] and prices.load("NEW", folder)["evidence"] == [str(pathlib.Path("ev/full"))]


def test_gap_longer_than_the_window_triggers_full_refetch():
    folder = pathlib.Path(tempfile.mkdtemp())
    _store(folder, "AAA", [["2026-09-29", 20.0, 1], ["2026-09-30", 22.0, 1]])
    client = FakeBars(recent={"AAA": [["2026-10-12", 11.5, 1]]},
                      full={"AAA": [["2026-09-29", 10.0, 1], ["2026-09-30", 11.0, 1], ["2026-10-12", 11.5, 1]]})
    prices.refresh(client, ["AAA"], dt.date(2026, 10, 13), folder)
    assert client.full_calls == [["AAA"]] and prices.load("AAA", folder)["bars"][0] == ["2026-09-29", 10.0, 1]


def test_failed_chunk_keeps_the_rest_and_is_reported():
    folder = pathlib.Path(tempfile.mkdtemp())
    client = FakeBars(recent={}, full={"AAA": [["2026-10-12", 1.0, 1]], "CCC": [["2026-10-12", 3.0, 1]]},
                      broken="BAD")
    summary = prices.refresh(client, ["AAA", "BAD", "CCC"], dt.date(2026, 10, 13), folder, chunk=1)
    assert summary["failed"] == ["BAD"]
    assert prices.load("AAA", folder)["bars"] and prices.load("CCC", folder)["bars"]
    assert not prices.load("BAD", folder)["bars"]


def test_history_evidence_survives_nightly_updates():
    folder = pathlib.Path(tempfile.mkdtemp())
    first = FakeBars(recent={}, full={"NEW": [["2026-10-09", 1.0, 1]]})
    prices.refresh(first, ["NEW"], dt.date(2026, 10, 12), folder)
    prices.refresh(FakeBars(recent={"NEW": [["2026-10-09", 1.0, 1], ["2026-10-12", 1.1, 1]]}, full={}),
                   ["NEW"], dt.date(2026, 10, 13), folder)
    stored = prices.load("NEW", folder)
    full, recent = str(pathlib.Path("ev/full")), str(pathlib.Path("ev/recent"))
    assert stored["history_evidence"] == [full] and prices.evidence_of(stored) == [full, recent]


def test_full_refetch_reaches_back_to_kept_calls():
    folder = pathlib.Path(tempfile.mkdtemp())
    today = dt.date(2026, 10, 13)
    client = FakeBars(recent={}, full={"AAA": [["2024-06-04", 1.0, 1]], "BBB": [["2026-01-02", 1.0, 1]]})
    prices.refresh(client, ["AAA", "BBB"], today, folder, keep_from={"AAA": dt.date(2024, 6, 3)})
    assert sorted(client.starts) == [dt.date(2024, 6, 3), today - dt.timedelta(days=prices.HISTORY_DAYS)]


def test_client_refuses_trading_urls():
    called = []
    client = prices.Client({}, getter=lambda u, h: called.append(u) or "{}")
    try:
        client._read("alpaca-x", "https://paper-api.alpaca.markets/v2/orders")
    except fetch.FetchError as e:
        assert "refused" in str(e) and called == []
    else:
        raise AssertionError("an orders URL was read")


def test_bars_follows_pages_and_converts_dates():
    testkit.temp_bob()
    pages = [{"bars": {"SPY": [{"t": "2026-10-09T04:00:00Z", "c": 600.0, "v": 10}]}, "next_page_token": "p2"},
             {"bars": {"SPY": [{"t": "2026-10-12T04:00:00Z", "c": 601.0, "v": 11}],
                       "QQQ": [{"t": "2026-10-12T04:00:00Z", "c": 500.0, "v": 9}]}, "next_page_token": None}]
    urls = []

    def getter(url, headers):
        urls.append(url)
        return json.dumps(pages[len(urls) - 1])
    bars, evidence = prices.Client({}, getter=getter).bars(["SPY", "QQQ"], dt.date(2026, 10, 1), dt.date(2026, 10, 13))
    assert bars == {"SPY": [["2026-10-09", 600.0, 10], ["2026-10-12", 601.0, 11]], "QQQ": [["2026-10-12", 500.0, 9]]}
    assert len(evidence) == 2 and "adjustment=all" in urls[0] and "page_token=p2" in urls[1]


def _refresh_ends(now: dt.datetime) -> set[str]:
    """The `end` of every bars request `--refresh` makes at `now`, against a fake Alpaca whose
    calendar holds 2026-10-14 and 2026-10-15 (closes 16:00 ET)."""
    import os
    testkit.temp_bob()
    os.environ.update(ALPACA_PAPER_KEY_ID="k", ALPACA_PAPER_SECRET_KEY="s")
    ends: set[str] = set()

    def getter(url, headers):
        if "calendar" in url:
            return json.dumps([{"date": d, "open": "09:30", "close": "16:00"} for d in ("2026-10-14", "2026-10-15")])
        ends.add(dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query))["end"])
        return json.dumps({"bars": {}, "next_page_token": None})

    real_client, real_now = prices.Client, fetch.utcnow
    prices.Client = lambda headers: real_client(headers, getter=getter)
    fetch.utcnow = lambda: now
    try:
        assert prices.main(["--refresh"]) == 0
    finally:
        prices.Client, fetch.utcnow = real_client, real_now
    return ends


def test_refresh_during_the_session_stops_at_the_last_closed_one():
    assert _refresh_ends(_utc(2026, 10, 15, 15, 0)) == {"2026-10-14"}


def test_refresh_after_close_and_settle_includes_today():
    assert _refresh_ends(_utc(2026, 10, 15, 21, 30)) == {"2026-10-15"}


def test_credentials_missing_refused():
    try:
        prices.credentials({})
    except fetch.FetchError as e:
        assert "ALPACA_PAPER_KEY_ID" in str(e)
    else:
        raise AssertionError("missing key accepted")
    assert prices.credentials({"ALPACA_PAPER_KEY_ID": "k", "ALPACA_PAPER_SECRET_KEY": "s"}) == {
        "APCA-API-KEY-ID": "k", "APCA-API-SECRET-KEY": "s"}


if __name__ == "__main__":
    raise SystemExit(testkit.run(globals()))
