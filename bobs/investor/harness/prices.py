#!/usr/bin/env python3
"""Daily adjusted bars and the market calendar from Alpaca, kept per symbol under state/prices.

Read-only by construction: the client refuses any URL that is not the market-data host or the
calendar, so no bug or prompt can turn a price read into an order.

Why a 7-day nightly read: re-reading a year for ~7,000 symbols every night would store gigabytes
of evidence. The short read is compared with the stored overlap; if a split or dividend changed the
adjusted history, that symbol alone is re-read in full. Momentum and ledger returns are then never
computed across a jump that did not happen.

Why bars end at the last completed session, not today: the free data plan refuses any request that
reaches the last 15 minutes of SIP data (403), and a run during market hours would otherwise store
a half-day bar as that session's close.

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
    return edgar.read_json((folder or prices_dir()) / f"{symbol}.json",
                           {"bars": [], "evidence": [], "history_evidence": []})


def evidence_of(stored: dict) -> list[str]:
    """The full-history read the old closes came from, then the recent nightly reads: a return
    over a year cites both ends."""
    return list(dict.fromkeys(stored.get("history_evidence", []) + stored.get("evidence", [])))


def sessions(folder: pathlib.Path | None = None) -> list[dict]:
    return edgar.read_json((folder or prices_dir()) / "_calendar.json", {}).get("sessions", [])


def merge(old: list, new: list, tol: float = TOLERANCE) -> tuple[list, bool]:
    by_date = {b[0]: b for b in old}
    changed = any(b[0] in by_date and abs(by_date[b[0]][1] - b[1]) > tol * max(abs(b[1]), 1e-9) for b in new)
    by_date.update({b[0]: b for b in new})
    return [by_date[d] for d in sorted(by_date)], changed


def _save(folder: pathlib.Path, symbol: str, bars: list, evidence: list, history: list) -> None:
    edgar.write_json(folder / f"{symbol}.json", {
        "bars": bars, "evidence": [str(e) for e in evidence][-KEEP_EVIDENCE:],
        "history_evidence": [str(e) for e in history]})


def _chunks(items: list, size: int) -> list[list]:
    return [items[i:i + size] for i in range(0, len(items), size)]


def _needs_full(stored: dict, recent: list, window_start: dt.date) -> bool:
    """A full refetch when there is nothing stored, when the stored history stops before this read
    begins (a gap: a split inside it would go unseen), or when the overlap disagrees (a split or
    dividend re-adjusted history)."""
    if not stored["bars"] or stored["bars"][-1][0] < window_start.isoformat():
        return True
    return merge(stored["bars"], recent)[1]


def refresh(client, symbols: list[str], today: dt.date, folder: pathlib.Path,
            keep_from: dict[str, dt.date] | None = None, chunk: int = CHUNK) -> dict:
    """Nightly update, saved chunk by chunk: a failed chunk is reported and the rest kept, so one
    rejected symbol or a timeout never costs the whole night. Full refetches start HISTORY_DAYS
    back, or earlier where `keep_from` says a call needs older closes."""
    window_start = today - dt.timedelta(days=RECENT_DAYS)
    failed: list[str] = []
    full_needed: list[str] = []
    for part in _chunks(symbols, chunk):
        try:
            recent, recent_ev = client.bars(part, window_start, today)
        except fetch.FetchError:
            failed += part
            continue
        for symbol in part:
            stored = load(symbol, folder)
            if _needs_full(stored, recent.get(symbol, []), window_start):
                full_needed.append(symbol)
                continue
            merged, _ = merge(stored["bars"], recent.get(symbol, []))
            _save(folder, symbol, merged, stored["evidence"] + recent_ev, stored.get("history_evidence", []))
    default_start = today - dt.timedelta(days=HISTORY_DAYS)
    by_start: dict[dt.date, list[str]] = {}
    for symbol in full_needed:
        by_start.setdefault(min(default_start, (keep_from or {}).get(symbol, default_start)), []).append(symbol)
    for start, group in sorted(by_start.items()):
        for part in _chunks(group, chunk):
            try:
                full, full_ev = client.bars(part, start, today)
            except fetch.FetchError:
                failed += part
                continue
            for symbol in part:
                _save(folder, symbol, full.get(symbol, []), full_ev, full_ev)
    return {"symbols": len(symbols), "full": len(full_needed), "failed": failed}


def keep_from_calls(rows: list[dict]) -> dict[str, dt.date]:
    """The earliest call session per symbol, and for SPY and QQQ the earliest of all: ledger
    returns need closes back to every entry."""
    out: dict[str, dt.date] = {}
    for row in (r for r in rows if r.get("kind") == "call"):
        day = dt.date.fromisoformat(row["session"])
        for symbol in (row["symbol"], *BENCHMARKS):
            out[symbol] = min(out.get(symbol, day), day)
    return out


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
            cal, _ = client.calendar(today - dt.timedelta(days=30), today)
            end = dt.date.fromisoformat(marketclock.last_session(cal, fetch.utcnow()) or today.isoformat())
            adjusted, _ = client.bars(["SPY"], today - dt.timedelta(days=400), end)
            spy = adjusted.get("SPY", [])
            print(f"OK SPY bars={len(spy)} first={spy[0] if spy else None} sessions={len(cal)}")
            print("Compare the first close with an unadjusted chart: adjusted closes are lower before a dividend.")
            return 0 if spy and cal else 1
        if a.refresh:
            folder = prices_dir()
            cal, where = client.calendar(today - dt.timedelta(days=30), today + dt.timedelta(days=7))
            edgar.write_json(folder / "_calendar.json", {"fetched": today.isoformat(), "evidence": str(where), "sessions": cal})
            last = marketclock.last_session(cal, fetch.utcnow())
            if last is None:
                print("FAIL: no completed session in the market calendar")
                return 1
            import calls
            symbols = universe_symbols()
            summary = refresh(client, symbols, dt.date.fromisoformat(last), folder,
                              keep_from_calls(calls.read()[0]))
            failed = summary.pop("failed")
            if len(failed) == len(symbols):
                print(f"FAIL: every bars request failed ({len(failed)} symbols)")
                return 1
            print(f"OK {summary} failed={len(failed)}")
            if failed:
                print(f"WARN: no update for {', '.join(failed[:20])}{' ...' if len(failed) > 20 else ''}; "
                      "screen.py counts them as stale")
            return 0
    except fetch.FetchError as e:
        print(f"FAIL: {e}")
        return 1
    ap.print_help()
    return 2


if __name__ == "__main__":
    import oscompat
    oscompat.utf8_stdio()
    raise SystemExit(main(sys.argv[1:]))
