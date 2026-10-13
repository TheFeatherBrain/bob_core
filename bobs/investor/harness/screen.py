#!/usr/bin/env python3
"""The numbers lane: score every listed company that passes the floors, keep the top of the list.

Refuses to screen rather than screen badly: no calendar, a stale SPY or QQQ, or too many companies
whose price stopped updating, and it writes nothing and exits 1. A call made on a partial or stale
market picture would sit in the ledger for a year.

Companies Alpaca never priced (warrants, odd share classes) are counted as "unpriced" and reported,
not failed on: they were never part of the picture. A price that was there and stopped is "stale"
and counts toward the failure limit.

Re-running the same session keeps the new entrants measured against the previous session, so a
resumed loop still sees which names to research.

Usage:
    python3 harness/screen.py --run           # nightly; prints OK ... or FAIL: ...
    python3 harness/screen.py --card NVDA     # the scorecard for one ticker, to paste in a thesis
"""
from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import scoutpath  # noqa: E402,F401
import edgar  # noqa: E402
import fetch  # noqa: E402
import insider  # noqa: E402
import marketclock  # noqa: E402
import prices  # noqa: E402
import scorecard  # noqa: E402
import scoutcfg  # noqa: E402
import signals  # noqa: E402
import xbrl  # noqa: E402
from fundamentals import quality  # noqa: E402

PIOTROSKI_MIN_TESTS = 6
VOLUME_DAYS = 20


def screen_dir() -> pathlib.Path:
    return scoutcfg.state_dir() / "screen"


def latest(folder: pathlib.Path | None = None) -> dict:
    return edgar.read_json((folder or screen_dir()) / "latest.json", {})


def candidates(tickers: list[dict]) -> list[dict]:
    seen, out = set(), []
    for row in tickers:
        if row["cik"] not in seen:
            seen.add(row["cik"])
            out.append({**row, "symbol": edgar.sec_to_alpaca(row["ticker"])})
    return out


def price_view(bars: list) -> dict | None:
    if not bars:
        return None
    tail = bars[-VOLUME_DAYS:]
    return {"last": bars[-1][0], "close": bars[-1][1], "closes": [b[1] for b in bars],
            "dollar_volume": sum(b[1] * b[2] for b in tail) / len(tail)}


def passes_floors(view: dict, market_cap: float | None, cfg: dict) -> bool:
    return (market_cap is not None and market_cap >= cfg["min_market_cap"]
            and view["close"] >= cfg["min_price"] and view["dollar_volume"] >= cfg["min_dollar_volume"])


def _ev(rows: list[dict]) -> list[str]:
    return list(dict.fromkeys(e for r in rows for e in r["evidence"]))


def raw_signals(series: dict, current, previous, closes: list[float], insider_value: float | None,
                evidence: dict) -> dict:
    rev, eps = series["revenue_q"], series["eps_q"]
    pio = quality.piotroski_f_score(current, previous) if current and previous else None
    pio_value = pio["score"] / pio["max_possible"] if pio and pio["max_possible"] >= PIOTROSKI_MIN_TESTS else None
    return {
        "ttm_growth": {"value": signals.ttm_growth(rev), "evidence": _ev(rev[-8:])},
        "acceleration": {"value": signals.acceleration(rev), "evidence": _ev(rev[-6:])},
        "eps_sue": {"value": signals.sue(eps), "evidence": _ev(eps[-9:])},
        "revenue_sue": {"value": signals.sue(rev), "evidence": _ev(rev[-9:])},
        "momentum_12_1": {"value": signals.momentum_12_1(closes), "evidence": evidence["prices"]},
        "high_proximity": {"value": signals.high_proximity(closes), "evidence": evidence["prices"]},
        "piotroski": {"value": pio_value, "evidence": evidence["annual"]},
        "insider": {"value": insider_value, "evidence": evidence["insider"]},
    }


def _benchmark_problem(sessions: list[dict], now_utc: dt.datetime, session: str) -> str | None:
    for symbol in prices.BENCHMARKS:
        bars = prices.load(symbol)["bars"]
        last = bars[-1][0] if bars else None
        if marketclock.is_stale(last, sessions, now_utc):
            return f"FAIL: {symbol} last bar {last}, expected {session}; no screen today"
    return None


def run(now_utc: dt.datetime) -> tuple[int, str]:
    cfg = scoutcfg.load()
    sec, out = edgar.sec_dir(), screen_dir()
    sessions = prices.sessions()
    session = marketclock.last_session(sessions, now_utc)
    if session is None:
        return 1, "FAIL: no market calendar; run prices.py --refresh"
    problem = _benchmark_problem(sessions, now_utc, session)
    if problem:
        return 1, problem
    tickers = edgar.read_json(sec / "tickers.json", {}).get("rows", [])
    frames = xbrl.load_frames(sec)
    if not tickers or not frames:
        return 1, "FAIL: no SEC tickers or frames; run edgar.py --sync"
    store = insider.load_store(sec)
    today = dt.date.fromisoformat(session)
    pool = candidates(tickers)
    raw, info, unpriced, stale = {}, {}, 0, 0
    for c in pool:
        stored = prices.load(c["symbol"])
        view = price_view(stored["bars"])
        if view is None:
            unpriced += 1
            continue
        if view["last"] < session:
            stale += 1
            continue
        shares, shares_ev = xbrl.latest_shares(frames, c["cik"])
        cap = shares * view["close"] if shares else None
        if not passes_floors(view, cap, cfg):
            continue
        current, previous, annual_ev = xbrl.annual_facts(frames, c["cik"])
        altman = quality.altman_z_score(dataclasses.replace(current, market_cap=cap)) if current else None
        insider_value, insider_ev = insider.signal_for(c["cik"], cap, store, today)
        raw[c["ticker"]] = raw_signals(xbrl.company_series(frames, c["cik"]), current, previous,
                                       view["closes"], insider_value,
                                       {"prices": stored["evidence"], "annual": annual_ev, "insider": insider_ev})
        info[c["ticker"]] = {"name": c["name"], "cik": c["cik"], "symbol": c["symbol"], "close": view["close"],
                             "market_cap": cap, "altman": altman, "shares_evidence": shares_ev}
    priced = len(pool) - unpriced
    if priced == 0 or stale / priced > cfg["max_missing_price_share"]:
        return 1, (f"FAIL: {stale} of {priced} priced companies have no current price "
                   f"(limit {cfg['max_missing_price_share']:.0%}); no screen today")
    cards = scorecard.build(raw, cfg["weights"], cfg["min_coverage"])
    for ticker, card in cards.items():
        card.update(info[ticker])
        card["gate"], card["reason"] = scorecard.gate(card, info[ticker]["altman"], cfg)
    ranked = sorted((t for t, c in cards.items() if c["rank_pct"] is not None), key=lambda t: -cards[t]["score"])
    shortlist = ranked[:cfg["top_n"]]
    before = latest(out)
    base = before.get("previous_shortlist", []) if before.get("session") == session else before.get("shortlist", [])
    doc = {"session": session, "generated_at": now_utc.isoformat(timespec="seconds"),
           "scorecard_version": cfg["scorecard_version"], "candidates": len(pool), "unpriced": unpriced,
           "stale_prices": stale, "scored": len(cards), "shortlist": shortlist, "previous_shortlist": base,
           "new_entrants": [t for t in shortlist if t not in base], "cards": cards}
    edgar.write_json(out / f"{session}.json", doc)
    edgar.write_json(out / "latest.json", doc)
    eligible = sum(c["gate"] == "eligible" for c in cards.values())
    return 0, (f"OK session={session} scored={len(cards)} eligible={eligible} shortlist={len(shortlist)} "
               f"new={doc['new_entrants']} stale={stale} unpriced={unpriced}")


def card_text(ticker: str, doc: dict) -> str:
    card = doc.get("cards", {}).get(ticker)
    if card is None:
        return f"{ticker}: not scored in session {doc.get('session')}"
    fmt = lambda v: "could not tell" if v is None else f"{v:.4g}"  # noqa: E731
    lines = [f"{ticker} ({card['name']}) session {doc['session']}, scorecard v{doc['scorecard_version']}",
             f"gate: {card['gate']} ({card['reason']})",
             f"score {fmt(card['score'])}, rank {fmt(card['rank_pct'])}, coverage {card['coverage']:.0%}, "
             f"Altman Z {fmt(card['altman'])}, market cap {fmt(card['market_cap'])} [ev:{card['shares_evidence']}]"]
    for group, names in scorecard.GROUP_SIGNALS.items():
        lines.append(f"{group}: {fmt(card['groups'][group])}")
        for name in names:
            s = card["signals"][name]
            lines.append(f"  {name}: value {fmt(s['value'])}, pct {fmt(s['pct'])} "
                         + " ".join(f"[ev:{e}]" for e in s["evidence"]))
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--run", action="store_true")
    ap.add_argument("--card")
    a = ap.parse_args(argv)
    if a.run:
        code, message = run(fetch.utcnow())
        print(message)
        return code
    if a.card:
        print(card_text(a.card.upper(), latest()))
        return 0
    ap.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
