#!/usr/bin/env python3
"""The prediction ledger: every thesis verdict, every call, every withdrawal, append-only.

A call is the Scout's claim that a stock will beat SPY and QQQ over 12 months. It is recorded only
when everything that makes it checkable is in place, and refused otherwise:
- the screen is from the last completed session (no calls on a stale picture);
- the ticker was scored that session and passed the gate (top 20%, coverage, no distress veto);
- theses/<TICKER>.md exists, passes thesis_lint, and states the same verdict and lane;
- a story-lane call names the saved evidence that started it;
- the ticker has no open call (one call per name per 12 months: no double counting);
- the same verdict was not already recorded this session (a resumed loop must not repeat itself).

A verdict that drops below buy-worthy withdraws the open call, and the call is still scored:
otherwise the Scout could quietly drop its losers. Nothing is ever rewritten; a correction is a
new line pointing at the old one. The entry price is not stored here: ledger_score.py takes the
next session's close from the adjusted series, so dividends and splits never skew a result.

Usage:
    python3 harness/calls.py verdict NVDA --verdict buy-worthy --lane numbers
    python3 harness/calls.py verdict ACME --verdict watch --lane story --source 2026-10-13/sec-index-...
    python3 harness/calls.py correct NVDA-2026-10-12-1 --note "thesis moved to theses/NVDA.md"
    python3 harness/calls.py list
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import scoutpath  # noqa: E402,F401
import fetch  # noqa: E402
import marketclock  # noqa: E402
import oscompat  # noqa: E402
import prices  # noqa: E402
import scoutcfg  # noqa: E402
import screen  # noqa: E402
import thesis_lint  # noqa: E402

HOLD_DAYS = 365


class Refused(Exception):
    pass


def ledger_path() -> pathlib.Path:
    return scoutcfg.state_dir() / "calls.jsonl"


def read(path: pathlib.Path | None = None) -> tuple[list[dict], int]:
    path = path or ledger_path()
    try:
        lines = path.read_text().splitlines()
    except FileNotFoundError:
        return [], 0
    rows, torn = [], 0
    for line in lines:
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except ValueError:
            torn += 1
    return rows, torn


def append(rows: list[dict], path: pathlib.Path | None = None) -> None:
    path = path or ledger_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as fh:
        oscompat.lock(fh)
        try:
            fh.write("".join(json.dumps(r, sort_keys=True) + "\n" for r in rows))
            fh.flush()
        finally:
            oscompat.unlock(fh)


def open_call(rows: list[dict], ticker: str, today: dt.date) -> dict | None:
    withdrawn = {r["call_id"] for r in rows if r.get("kind") == "withdrawn"}
    for row in reversed(rows):
        if (row.get("kind") == "call" and row["ticker"] == ticker and row["id"] not in withdrawn
                and (today - dt.date.fromisoformat(row["session"])).days < HOLD_DAYS):
            return row
    return None


def _check_thesis(ticker: str, verdict: str, lane: str) -> pathlib.Path:
    path = scoutcfg.bob_dir() / "theses" / f"{ticker}.md"
    if not path.is_file():
        raise Refused(f"no thesis at theses/{ticker}.md")
    text = path.read_text(encoding="utf-8")
    errors = thesis_lint.lint(text, scoutcfg.evidence_dir())
    if errors:
        raise Refused("thesis_lint: " + "; ".join(errors))
    head = thesis_lint.header(text)
    if head.get("verdict") != verdict or head.get("lane") != lane:
        raise Refused(f"thesis says {head.get('verdict')} / {head.get('lane')}, not {verdict} / {lane}")
    return path


def record_verdict(ticker: str, verdict: str, lane: str, source: str | None, now: dt.datetime) -> list[dict]:
    ticker = ticker.upper()
    if verdict not in thesis_lint.VERDICTS or lane not in thesis_lint.LANES:
        raise Refused(f"verdict one of {thesis_lint.VERDICTS}, lane one of {thesis_lint.LANES}")
    doc = screen.latest()
    session = doc.get("session")
    expected = marketclock.last_session(prices.sessions(), now)
    if session is None or session != expected:
        raise Refused(f"screen is for {session}, last completed session is {expected}; run the nightly screen first")
    card = doc.get("cards", {}).get(ticker)
    if card is None:
        raise Refused(f"{ticker} was not scored in session {session} (outside the universe floors?)")
    thesis_path = _check_thesis(ticker, verdict, lane)
    if lane == "story" and (not source or thesis_lint.resolve(source, scoutcfg.evidence_dir()) is None):
        raise Refused("story lane needs --source naming saved evidence that exists")
    rows, _ = read()
    if any(r.get("kind") == "verdict" and r["ticker"] == ticker and r["verdict"] == verdict
           and r["session"] == session for r in rows):
        raise Refused(f"{ticker} {verdict} already recorded for session {session}")
    at = now.isoformat(timespec="seconds")
    new = [{"kind": "verdict", "ticker": ticker, "verdict": verdict, "lane": lane, "session": session,
            "at": at, "source": source}]
    current = open_call(rows, ticker, dt.date.fromisoformat(session))
    if verdict == "buy-worthy":
        if card["gate"] != "eligible":
            raise Refused(f"gate is {card['gate']}: {card['reason']}")
        if current is not None:
            raise Refused(f"already called on {current['session']} ({current['id']}); one open call per name")
        number = sum(r.get("kind") == "call" and r["ticker"] == ticker for r in rows) + 1
        new.append({"kind": "call", "id": f"{ticker}-{session}-{number}", "ticker": ticker,
                    "symbol": card["symbol"], "lane": lane, "session": session, "at": at,
                    "scorecard_version": doc["scorecard_version"], "score": card["score"],
                    "coverage": card["coverage"], "rank_pct": card["rank_pct"],
                    "thesis": f"theses/{ticker}.md",
                    "thesis_sha256": hashlib.sha256(thesis_path.read_bytes()).hexdigest(), "source": source})
    elif current is not None:
        new.append({"kind": "withdrawn", "call_id": current["id"], "ticker": ticker, "session": session,
                    "at": at, "reason": f"verdict now {verdict}"})
    append(new)
    return new


def correct(call_id: str, note: str, now: dt.datetime) -> dict:
    rows, _ = read()
    if not any(r.get("kind") == "call" and r["id"] == call_id for r in rows):
        raise Refused(f"no call with id {call_id}")
    row = {"kind": "correction", "call_id": call_id, "at": now.isoformat(timespec="seconds"), "note": note}
    append([row])
    return row


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="command", required=True)
    v = sub.add_parser("verdict")
    v.add_argument("ticker")
    v.add_argument("--verdict", required=True)
    v.add_argument("--lane", required=True)
    v.add_argument("--source")
    c = sub.add_parser("correct")
    c.add_argument("call_id")
    c.add_argument("--note", required=True)
    sub.add_parser("list")
    a = ap.parse_args(argv)
    try:
        if a.command == "verdict":
            rows = record_verdict(a.ticker, a.verdict, a.lane, a.source, fetch.utcnow())
            print("OK recorded " + ", ".join(r.get("id") or r["kind"] for r in rows))
            return 0
        if a.command == "correct":
            print(f"OK {correct(a.call_id, a.note, fetch.utcnow())}")
            return 0
    except Refused as e:
        print(f"REFUSED: {e}")
        return 1
    rows, torn = read()
    withdrawn = {r["call_id"] for r in rows if r.get("kind") == "withdrawn"}
    for r in (r for r in rows if r.get("kind") == "call"):
        print(f"{r['id']:28} {r['lane']:8} score {r['score']:.0f} {'withdrawn' if r['id'] in withdrawn else 'open'}")
    if torn:
        print(f"WARN: {torn} torn line(s) skipped in {ledger_path()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
