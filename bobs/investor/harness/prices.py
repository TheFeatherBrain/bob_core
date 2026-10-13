#!/usr/bin/env python3
"""Daily adjusted bars and the market calendar from Alpaca, kept per symbol under state/prices.

Read-only by construction: the client refuses any URL that is not the market-data host or the
calendar, so no bug or prompt can turn a price read into an order.

Why a 7-day nightly read: re-reading a year for ~7,000 symbols every night would store gigabytes
of evidence. The short read is compared with the stored overlap; if a split or dividend changed the
adjusted history, that symbol alone is re-read in full. Momentum and ledger returns are then never
computed across a jump that did not happen.

Keys come from the environment (ALPACA_PAPER_KEY_ID, ALPACA_PAPER_SECRET_KEY), never a file here.

Usage:
    python3 harness/prices.py --refresh          # calendar, then bars for the universe + SPY, QQQ
    python3 harness/prices.py --selftest-live    # SPY adjusted vs raw, calendar (network)
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import pathlib
import sys
import urllib.parse

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import scoutpath  # noqa: E402,F401
import edgar  # noqa: E402
import fetch  # noqa: E402
import marketclock  # noqa: E402
import scoutcfg  # noqa: E402

DATA_HOST = "https://data.alpaca.markets/"
BARS_URL = DATA_HOST + "v2/stocks/bars"
CALENDAR_URL = "https://paper-api.alpaca.markets/v2/calendar"
ALLOWED = (DATA_HOST, CALENDAR_URL + "?")
BENCHMARKS = ("SPY", "QQQ")
CHUNK = 200
RECENT_DAYS = 7
HISTORY_START = dt.date(2025, 1, 1)
HISTORY_DAYS = 400
TOLERANCE = 1e-4
KEEP_EVIDENCE = 5


def credentials(env=os.environ) -> dict:
    key, secret = env.get("ALPACA_PAPER_KEY_ID"), env.get("ALPACA_PAPER_SECRET_KEY")
    if not (key and secret):
        raise fetch.FetchError("", None, "ALPACA_PAPER_KEY_ID / ALPACA_PAPER_SECRET_KEY not set")
    return {"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret}


def _bar_date(stamp: str) -> str:
    utc = dt.datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    return marketclock.to_et(utc).date().isoformat()


class Client:
    def __init__(self, headers: dict, *, getter=fetch.get, base: pathlib.Path | None = None):
        self.headers, self.getter, self.base = headers, getter, base

    def _read(self, prefix: str, url: str) -> tuple[str, pathlib.Path]:
        if not url.startswith(ALLOWED):
            raise fetch.FetchError(url, None, "refused: prices reads only the Alpaca data host and calendar")
        return fetch.read(prefix, url, self.headers, getter=self.getter, base=self.base)

    def bars(self, symbols: list[str], start: dt.date, end: dt.date) -> tuple[dict, list[pathlib.Path]]:
        out: dict[str, list] = {}
        evidence: list[pathlib.Path] = []
        for i in range(0, len(symbols), CHUNK):
            token = None
            while True:
                query = {"symbols": ",".join(symbols[i:i + CHUNK]), "timeframe": "1Day",
                         "start": start.isoformat(), "end": end.isoformat(), "adjustment": "all",
                         "feed": "sip", "limit": 10000}
                if token:
                    query["page_token"] = token
                raw, where = self._read("alpaca-bars", f"{BARS_URL}?{urllib.parse.urlencode(query)}")
                evidence.append(where)
                doc = json.loads(raw)
                for symbol, rows in (doc.get("bars") or {}).items():
                    out.setdefault(symbol, []).extend([_bar_date(r["t"]), float(r["c"]), int(r["v"])] for r in rows)
                token = doc.get("next_page_token")
                if not token:
                    break
        return out, evidence

    def calendar(self, start: dt.date, end: dt.date) -> tuple[list[dict], pathlib.Path]:
        query = urllib.parse.urlencode({"start": start.isoformat(), "end": end.isoformat()})
        raw, where = self._read("alpaca-calendar", f"{CALENDAR_URL}?{query}")
        return [{"date": s["date"], "close": s["close"][:5]} for s in json.loads(raw)], where


def prices_dir() -> pathlib.Path:
    return scoutcfg.state_dir() / "prices"


def load(symbol: str, folder: pathlib.Path | None = None) -> dict:
    return edgar.read_json((folder or prices_dir()) / f"{symbol}.json", {"bars": [], "evidence": []})


def sessions(folder: pathlib.Path | None = None) -> list[dict]:
    return edgar.read_json((folder or prices_dir()) / "_calendar.json", {}).get("sessions", [])


def merge(old: list, new: list, tol: float = TOLERANCE) -> tuple[list, bool]:
    by_date = {b[0]: b for b in old}
    changed = any(b[0] in by_date and abs(by_date[b[0]][1] - b[1]) > tol * max(abs(b[1]), 1e-9) for b in new)
    by_date.update({b[0]: b for b in new})
    return [by_date[d] for d in sorted(by_date)], changed


def _save(folder: pathlib.Path, symbol: str, bars: list, evidence: list) -> None:
    edgar.write_json(folder / f"{symbol}.json", {"bars": bars, "evidence": [str(e) for e in evidence][-KEEP_EVIDENCE:]})


def refresh(client, symbols: list[str], today: dt.date, folder: pathlib.Path) -> dict:
    recent, recent_ev = client.bars(symbols, today - dt.timedelta(days=RECENT_DAYS), today)
    full_needed = []
    for symbol in symbols:
        stored = load(symbol, folder)
        if not stored["bars"]:
            full_needed.append(symbol)
            continue
        merged, changed = merge(stored["bars"], recent.get(symbol, []))
        if changed:
            full_needed.append(symbol)
            continue
        _save(folder, symbol, merged, stored["evidence"] + recent_ev)
    if full_needed:
        start = min(HISTORY_START, today - dt.timedelta(days=HISTORY_DAYS))
        full, full_ev = client.bars(full_needed, start, today)
        for symbol in full_needed:
            _save(folder, symbol, full.get(symbol, []), full_ev)
    return {"symbols": len(symbols), "full": len(full_needed)}


def universe_symbols() -> list[str]:
    rows = edgar.read_json(edgar.sec_dir() / "tickers.json", {}).get("rows", [])
    return sorted({edgar.sec_to_alpaca(r["ticker"]) for r in rows} | set(BENCHMARKS))


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--refresh", action="store_true")
    ap.add_argument("--selftest-live", action="store_true")
    a = ap.parse_args(argv)
    today = fetch.utcnow().date()
    try:
        client = Client(credentials())
        if a.selftest_live:
            adjusted, _ = client.bars(["SPY"], today - dt.timedelta(days=400), today)
            cal, _ = client.calendar(today - dt.timedelta(days=30), today)
            spy = adjusted.get("SPY", [])
            print(f"OK SPY bars={len(spy)} first={spy[0] if spy else None} sessions={len(cal)}")
            print("Compare the first close with an unadjusted chart: adjusted closes are lower before a dividend.")
            return 0 if spy and cal else 1
        if a.refresh:
            folder = prices_dir()
            cal, where = client.calendar(today - dt.timedelta(days=30), today + dt.timedelta(days=7))
            edgar.write_json(folder / "_calendar.json", {"fetched": today.isoformat(), "evidence": str(where), "sessions": cal})
            print(f"OK {refresh(client, universe_symbols(), today, folder)}")
            return 0
    except fetch.FetchError as e:
        print(f"FAIL: {e}")
        return 1
    ap.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
