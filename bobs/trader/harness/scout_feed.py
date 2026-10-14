#!/usr/bin/env python3
"""The Scout's calls and market conditions, read as data. Never written.

Why v2 calls only: the Trader sizes by the call's rank and checks its factor groups, and only calls
made under scorecard v2 carry their full card. A v1 call is counted and skipped, never guessed at.
Why a stale market series is None: a value that stopped updating looks exactly like a current one;
sizing treats None as the cautious case.

Usage (library; trade.py and bars.py call it):
    feed = scout_feed.load()
    calls = scout_feed.open_calls(feed, today)
"""
from __future__ import annotations

import datetime as dt
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import traderpath  # noqa: E402,F401
import store  # noqa: E402
import tradercfg  # noqa: E402

HOLD_DAYS = 365


class FeedError(Exception):
    pass


def load(state: pathlib.Path | None = None) -> dict:
    state = state or tradercfg.scout()["state_dir"]
    path = state / "calls.jsonl"
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        lines = []
    except OSError as e:
        raise FeedError(f"cannot read the Scout's ledger {path}: {e}") from None
    rows, torn = [], 0
    for text in lines:
        if not text.strip():
            continue
        try:
            rows.append(json.loads(text))
        except ValueError:
            torn += 1
    calls = [r for r in rows if r.get("kind") == "call"]
    v2 = [c for c in calls if isinstance(c.get("card"), dict)]
    return {"calls": v2, "withdrawn": {r["call_id"] for r in rows if r.get("kind") == "withdrawn"},
            "skipped_v1": len(calls) - len(v2), "torn": torn, "macro": store.read_json(state / "macro.json", {})}


def open_calls(feed: dict, today: dt.date, max_days: int = HOLD_DAYS) -> list[dict]:
    return [c for c in feed["calls"] if c["id"] not in feed["withdrawn"]
            and (today - dt.date.fromisoformat(c["session"])).days < max_days]


def macro_value(macro: dict, series: str) -> float | None:
    row = (macro.get("series") or {}).get(series) or {}
    if row.get("value") is None or row.get("stale") is not False:
        return None
    return float(row["value"])
