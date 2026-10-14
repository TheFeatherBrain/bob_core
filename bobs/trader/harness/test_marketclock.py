#!/usr/bin/env python3
"""marketclock (copied from the Scout): DST edges, the last session waits for close plus an hour.

Run:  python3 harness/test_marketclock.py
"""
from __future__ import annotations

import datetime as dt
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import traderpath  # noqa: E402,F401
import marketclock  # noqa: E402
import testkit  # noqa: E402


def _utc(*args) -> dt.datetime:
    return dt.datetime(*args, tzinfo=dt.timezone.utc)


def test_et_offset_dst_edges():
    hours = lambda t: marketclock.et_offset(t).total_seconds() / 3600  # noqa: E731
    assert hours(_utc(2026, 3, 8, 6, 59)) == -5 and hours(_utc(2026, 3, 8, 7, 0)) == -4
    assert hours(_utc(2026, 11, 1, 5, 59)) == -4 and hours(_utc(2026, 11, 1, 6, 0)) == -5


def test_last_session_waits_for_close_plus_an_hour():
    sessions = [{"date": "2026-10-09", "close": "16:00"}, {"date": "2026-10-12", "close": "16:00"}]
    assert marketclock.last_session(sessions, _utc(2026, 10, 12, 21, 0)) == "2026-10-12"
    assert marketclock.last_session(sessions, _utc(2026, 10, 12, 20, 30)) == "2026-10-09"


if __name__ == "__main__":
    raise SystemExit(testkit.run(globals()))
