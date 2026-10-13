#!/usr/bin/env python3
"""US Eastern time and the last completed trading session, without zoneinfo.

Why not zoneinfo: on Windows it needs the third-party tzdata package, and this Bob is stdlib only.
US daylight saving has one rule since 2007 (second Sunday of March to first Sunday of November, at
02:00 local), so it is computed here and tested at both edges.

Why sessions, not weekdays: a weekday rule calls a holiday "missing data" and a Saturday run
"stale". The session list comes from Alpaca's market calendar, early closes included.

Usage:
    python3 harness/marketclock.py      # print the current Eastern time
"""
from __future__ import annotations

import datetime as dt
import sys

SETTLE = dt.timedelta(minutes=60)


def _nth_sunday(year: int, month: int, n: int) -> dt.date:
    first = dt.date(year, month, 1)
    return first + dt.timedelta(days=(6 - first.weekday()) % 7 + 7 * (n - 1))


def et_offset(utc: dt.datetime) -> dt.timedelta:
    start = dt.datetime.combine(_nth_sunday(utc.year, 3, 2), dt.time(7), dt.timezone.utc)
    end = dt.datetime.combine(_nth_sunday(utc.year, 11, 1), dt.time(6), dt.timezone.utc)
    return dt.timedelta(hours=-4 if start <= utc < end else -5)


def to_et(utc: dt.datetime) -> dt.datetime:
    return (utc + et_offset(utc)).replace(tzinfo=None)


def last_session(sessions: list[dict], now_utc: dt.datetime) -> str | None:
    now_et = to_et(now_utc)
    done = [s["date"] for s in sessions
            if dt.datetime.combine(dt.date.fromisoformat(s["date"]),
                                   dt.time.fromisoformat(s["close"])) + SETTLE <= now_et]
    return max(done, default=None)


def is_stale(last_bar: str | None, sessions: list[dict], now_utc: dt.datetime) -> bool:
    expected = last_session(sessions, now_utc)
    return last_bar is None or expected is None or last_bar < expected


def main(argv: list[str]) -> int:
    print(to_et(dt.datetime.now(dt.timezone.utc)).isoformat(timespec="minutes"), "ET")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
