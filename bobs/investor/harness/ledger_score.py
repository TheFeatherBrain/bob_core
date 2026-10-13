#!/usr/bin/env python3
"""Score every call against SPY and QQQ at 3, 6 and 12 months; build the Scout basket curve.

The rules that keep the record honest:
- Entry is the close of the session after the call, never a price the Scout had already seen.
- Returns come from the current adjusted series for both the pick and the benchmark, so dividends
  and splits never make a pick look better or worse than it was.
- Every call is scored at fixed horizons, withdrawn or not.
- A delisted name (gone from SEC's list and no bars for a week) is scored at its last price.
- A listed name with no price near a horizon stays "no price"; an old price is never used instead.
- 3 and 6 months are labelled interim; 12 months is the verdict. Below 20 scored calls the board
  and digest say the result is noise.

The basket is equal weight across calls held at the time (entry to 12 months), rebalanced daily,
starting at 100 on the first entry date; SPY and QQQ are rebased to the same date.

Usage:
    python3 harness/ledger_score.py --run
"""
from __future__ import annotations

import argparse
import calendar
import datetime as dt
import pathlib
import statistics
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import scoutpath  # noqa: E402,F401
import calls  # noqa: E402
import edgar  # noqa: E402
import fetch  # noqa: E402
import marketclock  # noqa: E402
import prices  # noqa: E402
import scoutcfg  # noqa: E402

HORIZONS = (3, 6, 12)
MIN_SCORED = 20
STALE_PRICE_DAYS = 7
DELISTED_SESSIONS = 5


def _d(text: str) -> dt.date:
    return dt.date.fromisoformat(text)


def add_months(day: dt.date, n: int) -> dt.date:
    month0 = day.month - 1 + n
    year, month = day.year + month0 // 12, month0 % 12 + 1
    return dt.date(year, month, min(day.day, calendar.monthrange(year, month)[1]))


def entry(bars: list, session: str) -> tuple[str, float] | None:
    return next(((b[0], b[1]) for b in bars if b[0] > session), None)


def close_on_or_before(bars: list, day: str) -> tuple[str, float] | None:
    best = None
    for b in bars:
        if b[0] > day:
            break
        best = (b[0], b[1])
    return best


def score_call(call: dict, series: dict, listed: set[str], last_session: str, withdrawn: set[str]) -> dict:
    bars, spy, qqq = series.get(call["symbol"], []), series["SPY"], series["QQQ"]
    row = {k: call[k] for k in ("id", "ticker", "symbol", "lane", "session", "score")}
    row.update({"withdrawn": call["id"] in withdrawn, "entry_date": None, "entry_close": None,
                "delisted": False, "horizons": {str(h): {"status": "pending"} for h in HORIZONS}})
    start = entry(bars, call["session"])
    if start is None:
        return row
    spy0, qqq0 = close_on_or_before(spy, start[0]), close_on_or_before(qqq, start[0])
    if spy0 is None or qqq0 is None:
        return row
    row["entry_date"], row["entry_close"] = start
    row["delisted"] = (call["symbol"] not in listed and len(spy) > DELISTED_SESSIONS
                       and bars[-1][0] < spy[-DELISTED_SESSIONS - 1][0])
    for h in HORIZONS:
        due = add_months(_d(start[0]), h).isoformat()
        if due > last_session:
            row["horizons"][str(h)] = {"status": "pending", "due": due}
            continue
        pick = close_on_or_before(bars, due)
        if pick is None or (not row["delisted"] and (_d(due) - _d(pick[0])).days > STALE_PRICE_DAYS):
            row["horizons"][str(h)] = {"status": "no price", "due": due}
            continue
        ret = pick[1] / start[1] - 1
        spy_ret = close_on_or_before(spy, pick[0])[1] / spy0[1] - 1
        qqq_ret = close_on_or_before(qqq, pick[0])[1] / qqq0[1] - 1
        row["horizons"][str(h)] = {"status": "scored", "due": due, "price_date": pick[0], "return": ret,
                                   "excess_spy": ret - spy_ret, "excess_qqq": ret - qqq_ret}
    return row


def summarize(rows: list[dict]) -> dict:
    out = {}
    for h in HORIZONS:
        scored = [r["horizons"][str(h)] for r in rows if r["horizons"].get(str(h), {}).get("status") == "scored"]
        n = len(scored)
        out[str(h)] = {"n": n, "interim": h != 12,
                       "avg_excess_spy": statistics.fmean(s["excess_spy"] for s in scored) if n else None,
                       "avg_excess_qqq": statistics.fmean(s["excess_qqq"] for s in scored) if n else None,
                       "beat_spy": sum(s["excess_spy"] > 0 for s in scored) / n if n else None}
    return out


def _carried(bars: list, dates: list[str]) -> list[float | None]:
    """The pick's close on each basket date, carrying the last close over a missing bar: a halted
    day is flat and the move on resumption is counted in full, never skipped."""
    out, last, i = [], None, 0
    for day in dates:
        while i < len(bars) and bars[i][0] <= day:
            last = bars[i][1]
            i += 1
        out.append(last)
    return out


def basket(rows: list[dict], series: dict) -> dict:
    live = [r for r in rows if r["entry_date"]]
    if not live:
        return {"dates": [], "scout": [], "spy": [], "qqq": []}
    first = min(r["entry_date"] for r in live)
    dates = [b[0] for b in series["SPY"] if b[0] >= first]
    filled = {r["id"]: _carried(series.get(r["symbol"], []), dates) for r in live}
    ends = {r["id"]: add_months(_d(r["entry_date"]), 12).isoformat() for r in live}
    scout = [100.0]
    for i, (prev, day) in enumerate(zip(dates, dates[1:])):
        moves = [filled[r["id"]][i + 1] / filled[r["id"]][i] - 1 for r in live
                 if r["entry_date"] <= prev and day <= ends[r["id"]]
                 and filled[r["id"]][i] and filled[r["id"]][i + 1]]
        scout.append(scout[-1] * (1 + (sum(moves) / len(moves) if moves else 0.0)))
    rebased = {}
    for symbol in prices.BENCHMARKS:
        by_date = {b[0]: b[1] for b in series[symbol]}
        rebased[symbol] = [by_date[d] / by_date[dates[0]] * 100 for d in dates]
    return {"dates": dates, "scout": scout, "spy": rebased["SPY"], "qqq": rebased["QQQ"]}


def results_path() -> pathlib.Path:
    return scoutcfg.state_dir() / "ledger_results.json"


def run(now_utc: dt.datetime) -> dict:
    rows, torn = calls.read()
    made = [r for r in rows if r.get("kind") == "call"]
    withdrawn = {r["call_id"] for r in rows if r.get("kind") == "withdrawn"}
    symbols = {c["symbol"] for c in made} | set(prices.BENCHMARKS)
    series = {s: prices.load(s)["bars"] for s in symbols}
    listed = {edgar.sec_to_alpaca(t["ticker"])
              for t in edgar.read_json(edgar.sec_dir() / "tickers.json", {}).get("rows", [])}
    session = marketclock.last_session(prices.sessions(), now_utc) or ""
    scored = [{**score_call(c, series, listed, session, withdrawn),
               "evidence": prices.evidence_of(prices.load(c["symbol"]))} for c in made]
    doc = {"generated_at": now_utc.isoformat(timespec="seconds"), "session": session, "n_calls": len(made),
           "torn_lines": torn, "min_scored": MIN_SCORED, "calls": scored, "summary": summarize(scored),
           "by_lane": {lane: summarize([r for r in scored if r["lane"] == lane]) for lane in ("numbers", "story")},
           "basket": basket(scored, series)}
    edgar.write_json(results_path(), doc)
    return doc


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--run", action="store_true", required=True)
    ap.parse_args(argv)
    doc = run(fetch.utcnow())
    twelve = doc["summary"]["12"]
    print(f"OK calls={doc['n_calls']} scored_12m={twelve['n']} torn={doc['torn_lines']}")
    return 0


if __name__ == "__main__":
    import oscompat
    oscompat.utf8_stdio()
    raise SystemExit(main(sys.argv[1:]))
