#!/usr/bin/env python3
"""Daily bars and the market calendar from Alpaca market data, for the few symbols the Trader needs.

Three series, because each answers a different question:
- raw open/high/low/close/volume: what a fill would actually have cost (the simulator fills at the
  next raw open) and what the position is worth today;
- split-adjusted closes: what a stop compares against, so a 2-for-1 split is not a 50% drop;
- dividend-adjusted closes for SPY only: SPY's total return for the go-live comparison.

Read-only by construction: the client refuses any URL that is not the market-data host or the
calendar. Keys come from the environment (ALPACA_PAPER_KEY_ID, ALPACA_PAPER_SECRET_KEY), the same
read-only data keys the Scout uses.

Why mode off reads nothing: off means nothing runs, so a switched-off Bob makes no network call
and needs no keys.

Why a short nightly window: re-reading 400 days every night would grow evidence for nothing. The
window is compared with the stored overlap; if a split or dividend re-adjusted history, that
series alone is re-read in full.

Usage:
    python3 harness/bars.py --refresh     # calendar, then SPY, held symbols and open Scout calls
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import pathlib
import statistics
import sys
import urllib.parse

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import traderpath  # noqa: E402,F401
import fetch  # noqa: E402
import marketclock  # noqa: E402
import scout_feed  # noqa: E402
import store  # noqa: E402
import tradercfg  # noqa: E402

DATA_HOST = "https://data.alpaca.markets/"
BARS_URL = DATA_HOST + "v2/stocks/bars"
CALENDAR_URL = "https://paper-api.alpaca.markets/v2/calendar"
ALLOWED = (DATA_HOST, CALENDAR_URL + "?")
BENCHMARK = "SPY"
CHUNK = 200
RECENT_DAYS = 10
HISTORY_DAYS = 400
TOLERANCE = 1e-4
KEEP_EVIDENCE = 10
ADV_DAYS = 20
SERIES = (("raw", "raw"), ("split", "split"))


def credentials(env=os.environ) -> dict:
    key, secret = env.get("ALPACA_PAPER_KEY_ID"), env.get("ALPACA_PAPER_SECRET_KEY")
    if not (key and secret):
        raise fetch.FetchError("", None, "ALPACA_PAPER_KEY_ID / ALPACA_PAPER_SECRET_KEY not set")
    return {"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret}


def _bar_date(stamp: str) -> str:
    utc = dt.datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    return marketclock.to_et(utc).date().isoformat()


def _row(bar: dict, adjustment: str) -> list:
    day = _bar_date(bar["t"])
    if adjustment == "raw":
        return [day, float(bar["o"]), float(bar["h"]), float(bar["l"]), float(bar["c"]), int(bar["v"])]
    return [day, float(bar["c"])]


class Client:
    def __init__(self, headers: dict, *, getter=fetch.get):
        self.headers, self.getter = headers, getter

    def _read(self, prefix: str, url: str) -> tuple[str, pathlib.Path]:
        if not url.startswith(ALLOWED):
            raise fetch.FetchError(url, None, "refused: bars reads only the Alpaca data host and calendar")
        return fetch.read(prefix, url, self.headers, getter=self.getter)

    def bars(self, symbols: list[str], start: dt.date, end: dt.date,
             adjustment: str) -> tuple[dict[str, list[list]], list[pathlib.Path]]:
        out: dict[str, list[list]] = {}
        evidence: list[pathlib.Path] = []
        for i in range(0, len(symbols), CHUNK):
            token = None
            while True:
                query = {"symbols": ",".join(symbols[i:i + CHUNK]), "timeframe": "1Day",
                         "start": start.isoformat(), "end": end.isoformat(), "adjustment": adjustment,
                         "feed": "sip", "limit": 10000}
                if token:
                    query["page_token"] = token
                raw, where = self._read(f"alpaca-bars-{adjustment}", f"{BARS_URL}?{urllib.parse.urlencode(query)}")
                evidence.append(where)
                doc = json.loads(raw)
                for symbol, rows in (doc.get("bars") or {}).items():
                    out.setdefault(symbol, []).extend(_row(r, adjustment) for r in rows)
                token = doc.get("next_page_token")
                if not token:
                    break
        return out, evidence

    def calendar(self, start: dt.date, end: dt.date) -> tuple[list[dict], pathlib.Path]:
        query = urllib.parse.urlencode({"start": start.isoformat(), "end": end.isoformat()})
        raw, where = self._read("alpaca-calendar", f"{CALENDAR_URL}?{query}")
        return [{"date": s["date"], "close": s["close"][:5]} for s in json.loads(raw)], where


def bars_dir() -> pathlib.Path:
    return tradercfg.state_dir() / "bars"


def load(symbol: str, folder: pathlib.Path | None = None) -> dict:
    doc = store.read_json((folder or bars_dir()) / f"{symbol}.json", {})
    return {"raw": doc.get("raw", []), "split": doc.get("split", []), "all": doc.get("all", []),
            "evidence": doc.get("evidence", [])}


def sessions(folder: pathlib.Path | None = None) -> list[dict]:
    return store.read_json((folder or bars_dir()) / "_calendar.json", {}).get("sessions", [])


def _close(row: list) -> float:
    return row[4] if len(row) == 6 else row[1]


def merge(old: list, new: list) -> tuple[list, bool]:
    by_date = {r[0]: r for r in old}
    changed = any(r[0] in by_date and abs(_close(by_date[r[0]]) - _close(r)) > TOLERANCE * max(abs(_close(r)), 1e-9)
                  for r in new)
    by_date.update({r[0]: r for r in new})
    return [by_date[d] for d in sorted(by_date)], changed


def _save(folder: pathlib.Path, symbol: str, doc: dict) -> None:
    doc["evidence"] = [str(e) for e in doc["evidence"]][-KEEP_EVIDENCE:]
    store.write_json(folder / f"{symbol}.json", doc)


def _update(client, symbols: list[str], adjustment: str, key: str, today: dt.date, folder: pathlib.Path,
            failed: list[str]) -> None:
    window = today - dt.timedelta(days=RECENT_DAYS)
    try:
        recent, recent_ev = client.bars(symbols, window, today, adjustment)
    except fetch.FetchError:
        failed.extend(symbols)
        return
    full = []
    for symbol in symbols:
        doc = load(symbol, folder)
        rows = recent.get(symbol, [])
        merged, changed = merge(doc[key], rows)
        if not doc[key] or doc[key][-1][0] < window.isoformat() or changed:
            full.append(symbol)
            continue
        doc[key] = merged
        doc["evidence"] += recent_ev
        _save(folder, symbol, doc)
    if not full:
        return
    try:
        history, history_ev = client.bars(full, today - dt.timedelta(days=HISTORY_DAYS), today, adjustment)
    except fetch.FetchError:
        failed.extend(full)
        return
    for symbol in full:
        doc = load(symbol, folder)
        doc[key] = history.get(symbol, [])
        doc["evidence"] += history_ev
        _save(folder, symbol, doc)


def refresh(client, symbols: list[str], today: dt.date, folder: pathlib.Path) -> dict:
    failed: list[str] = []
    for adjustment, key in SERIES:
        _update(client, symbols, adjustment, key, today, folder, failed)
    if BENCHMARK in symbols:
        _update(client, [BENCHMARK], "all", "all", today, folder, failed)
    return {"symbols": len(symbols), "failed": sorted(set(failed))}


def fresh(symbol: str, session: str) -> bool:
    raw = load(symbol)["raw"]
    return bool(raw) and raw[-1][0] == session


def close_on(symbol: str, session: str, key: str = "raw") -> float | None:
    return next((_close(r) for r in load(symbol)[key] if r[0] == session), None)


def last_close(symbol: str, session: str) -> float | None:
    return next((_close(r) for r in reversed(load(symbol)["raw"]) if r[0] <= session), None)


def adv(symbol: str) -> float | None:
    raw = load(symbol)["raw"][-ADV_DAYS:]
    if len(raw) < ADV_DAYS:
        return None
    return statistics.fmean(r[4] * r[5] for r in raw)


def needed_symbols() -> list[str]:
    import ledger
    held = set(ledger.replay(ledger.read()[0]).positions())
    feed = scout_feed.load()
    calls = {c["symbol"] for c in scout_feed.open_calls(feed, dt.date.today())}
    return sorted({BENCHMARK} | held | calls)


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--refresh", action="store_true", required=True)
    ap.parse_args(argv)
    if tradercfg.load()["mode"] == "off":
        print("OK mode=off")
        return 0
    today = fetch.utcnow().date()
    try:
        client = Client(credentials())
        folder = bars_dir()
        cal, where = client.calendar(today - dt.timedelta(days=30), today + dt.timedelta(days=10))
        store.write_json(folder / "_calendar.json", {"fetched": today.isoformat(), "evidence": str(where), "sessions": cal})
        symbols = needed_symbols()
        summary = refresh(client, symbols, today, folder)
    except (fetch.FetchError, scout_feed.FeedError) as e:
        print(f"FAIL: {e}")
        return 1
    if summary["failed"] and len(summary["failed"]) == len(symbols):
        print(f"FAIL: every bars request failed ({len(symbols)} symbols)")
        return 1
    print(f"OK symbols={summary['symbols']} failed={summary['failed']}")
    return 0


if __name__ == "__main__":
    import oscompat
    oscompat.utf8_stdio()
    raise SystemExit(main(sys.argv[1:]))
