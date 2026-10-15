#!/usr/bin/env python3
"""bars: raw OHLCV for fills, split-adjusted closes for stops, dividend-adjusted SPY for the bar.

Run:  python3 harness/test_bars.py
"""
from __future__ import annotations

import datetime as dt
import json
import pathlib
import sys
import urllib.parse

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import traderpath  # noqa: E402,F401
import bars  # noqa: E402
import fetch  # noqa: E402
import testkit  # noqa: E402

TODAY = dt.date(2026, 10, 14)


def _days(n: int) -> list[str]:
    day, out = TODAY, []
    while len(out) < n:
        day -= dt.timedelta(days=1)
        if day.weekday() < 5:
            out.append(day.isoformat())
    return out[::-1]


class Market:
    """A fake Alpaca data host: closes per symbol and adjustment, and a log of what was asked."""

    def __init__(self, closes: dict[str, dict[str, dict[str, float]]]):
        self.closes, self.asked = closes, []

    def get(self, url: str, headers: dict) -> str:
        q = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query))
        if "calendar" in url:
            return json.dumps([{"date": d, "open": "09:30", "close": "16:00"} for d in _days(30)])
        self.asked.append((q["adjustment"], q["start"], q["symbols"]))
        out = {}
        for symbol in q["symbols"].split(","):
            series = self.closes.get(symbol, {}).get(q["adjustment"], {})
            out[symbol] = [{"t": f"{d}T04:00:00Z", "o": c, "h": c, "l": c, "c": c, "v": 1000}
                           for d, c in sorted(series.items()) if q["start"] <= d <= q["end"]]
        return json.dumps({"bars": out, "next_page_token": None})


def _flat(price: float, n: int = 300) -> dict[str, float]:
    return {d: price for d in _days(n)}


def test_only_the_data_host_and_calendar_are_read():
    testkit.temp_bob()
    client = bars.Client({}, getter=Market({}).get)
    try:
        client._read("x", "https://paper-api.alpaca.markets/v2/orders")
    except fetch.FetchError as e:
        assert "refused" in str(e)
    else:
        raise AssertionError("not refused")


def test_missing_keys_refused():
    try:
        bars.credentials({})
    except fetch.FetchError as e:
        assert "ALPACA_PAPER_KEY_ID" in str(e)
    else:
        raise AssertionError("not refused")


def test_refresh_stores_raw_split_and_spy_all():
    bob = testkit.temp_bob()
    market = Market({"AAA": {"raw": _flat(100), "split": _flat(50)},
                     "SPY": {"raw": _flat(400), "split": _flat(400), "all": _flat(390)}})
    folder = bob / "data" / "state" / "bars"
    summary = bars.refresh(bars.Client({}, getter=market.get), ["AAA", "SPY"], TODAY, folder)
    assert summary == {"symbols": 2, "failed": []}
    aaa = bars.load("AAA", folder)
    assert aaa["raw"][-1] == [_days(1)[0], 100.0, 100.0, 100.0, 100.0, 1000] and aaa["split"][-1][1] == 50.0
    assert aaa["all"] == [] and bars.load("SPY", folder)["all"][-1][1] == 390.0
    assert aaa["evidence"] and pathlib.Path(aaa["evidence"][0]).exists()


def test_second_refresh_reads_only_the_recent_window():
    bob = testkit.temp_bob()
    market = Market({"AAA": {"raw": _flat(100), "split": _flat(100)}})
    folder = bob / "data" / "state" / "bars"
    client = bars.Client({}, getter=market.get)
    bars.refresh(client, ["AAA"], TODAY, folder)
    market.asked.clear()
    bars.refresh(client, ["AAA"], TODAY, folder)
    starts = {start for _, start, _ in market.asked}
    assert starts == {(TODAY - dt.timedelta(days=bars.RECENT_DAYS)).isoformat()}, market.asked


def test_a_split_re_adjusts_history_and_triggers_a_full_split_refetch():
    bob = testkit.temp_bob()
    market = Market({"BBB": {"raw": _flat(100), "split": _flat(100)}})
    folder = bob / "data" / "state" / "bars"
    client = bars.Client({}, getter=market.get)
    bars.refresh(client, ["BBB"], TODAY, folder)
    market.closes["BBB"]["split"] = _flat(50)
    market.asked.clear()
    bars.refresh(client, ["BBB"], TODAY, folder)
    full = [a for a in market.asked if a[1] == (TODAY - dt.timedelta(days=bars.HISTORY_DAYS)).isoformat()]
    assert [a[0] for a in full] == ["split"] and bars.load("BBB", folder)["split"][0][1] == 50.0


def test_accessors_fresh_close_and_adv():
    bob = testkit.temp_bob()
    folder = bob / "data" / "state" / "bars"
    rows = [[d, 10.0, 10.0, 10.0, 10.0, 100] for d in _days(25)]
    testkit.put(folder / "AAA.json", {"raw": rows, "split": [[d, 5.0] for d in _days(25)], "all": [], "evidence": []})
    last = _days(1)[0]
    assert bars.fresh("AAA", last) and not bars.fresh("AAA", TODAY.isoformat()) and not bars.fresh("NONE", last)
    assert bars.close_on("AAA", last) == 10.0 and bars.close_on("AAA", last, "split") == 5.0
    assert bars.close_on("AAA", "1999-01-01") is None and bars.adv("AAA") == 1000.0
    testkit.put(folder / "NEW.json", {"raw": rows[:19], "split": [], "all": [], "evidence": []})
    assert bars.adv("NEW") is None


def _refresh_ends(now: dt.datetime) -> set[str]:
    """The `end` of every bars request `--refresh` makes at `now`, in simulate mode, against a fake
    Alpaca whose calendar holds 2026-10-14 and 2026-10-15 (closes 16:00 ET)."""
    import os
    bob = testkit.temp_bob({'mode = "off"': 'mode = "simulate"'})
    testkit.scout_state(bob).mkdir(parents=True)
    os.environ.update(ALPACA_PAPER_KEY_ID="k", ALPACA_PAPER_SECRET_KEY="s")
    ends: set[str] = set()

    def getter(url, headers):
        if "calendar" in url:
            return json.dumps([{"date": d, "open": "09:30", "close": "16:00"} for d in ("2026-10-14", "2026-10-15")])
        ends.add(dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query))["end"])
        return json.dumps({"bars": {}, "next_page_token": None})

    real_client, real_now = bars.Client, fetch.utcnow
    bars.Client = lambda headers: real_client(headers, getter=getter)
    fetch.utcnow = lambda: now
    try:
        assert bars.main(["--refresh"]) == 0
    finally:
        bars.Client, fetch.utcnow = real_client, real_now
    return ends


def test_refresh_during_the_session_stops_at_the_last_closed_one():
    assert _refresh_ends(dt.datetime(2026, 10, 15, 15, 0, tzinfo=dt.timezone.utc)) == {"2026-10-14"}


def test_refresh_after_close_and_settle_includes_today():
    assert _refresh_ends(dt.datetime(2026, 10, 15, 21, 30, tzinfo=dt.timezone.utc)) == {"2026-10-15"}


def test_refresh_in_mode_off_reads_nothing():
    import contextlib
    import io
    import os
    testkit.temp_bob()
    for key in ("ALPACA_PAPER_KEY_ID", "ALPACA_PAPER_SECRET_KEY"):
        os.environ.pop(key, None)
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = bars.main(["--refresh"])
    assert code == 0 and out.getvalue().strip() == "OK mode=off", out.getvalue()


if __name__ == "__main__":
    raise SystemExit(testkit.run(globals()))
