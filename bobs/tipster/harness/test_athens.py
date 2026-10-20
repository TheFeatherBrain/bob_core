#!/usr/bin/env python3
"""athens: Athens local time from UTC by the EU summer-time rule, with no tzdata needed.

Run:  python3 harness/test_athens.py
"""
from __future__ import annotations

import datetime as dt
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import tipsterpath  # noqa: E402,F401
import athens  # noqa: E402
import testkit  # noqa: E402

UTC = dt.timezone.utc


def test_autumn_change_2026():
    # 25 Oct 2026: clocks go back at 01:00 UTC (04:00 EEST becomes 03:00 EET)
    assert athens.local(dt.datetime(2026, 10, 25, 0, 59, tzinfo=UTC)).strftime("%H:%M %z") == "03:59 +0300"
    assert athens.local(dt.datetime(2026, 10, 25, 1, 0, tzinfo=UTC)).strftime("%H:%M %z") == "03:00 +0200"


def test_spring_change_2027():
    # 28 Mar 2027: clocks go forward at 01:00 UTC
    assert athens.local(dt.datetime(2027, 3, 28, 0, 59, tzinfo=UTC)).utcoffset() == dt.timedelta(hours=2)
    assert athens.local(dt.datetime(2027, 3, 28, 1, 0, tzinfo=UTC)).utcoffset() == dt.timedelta(hours=3)


def test_date_rolls_at_athens_midnight():
    assert athens.date(dt.datetime(2026, 10, 22, 21, 30, tzinfo=UTC)) == "2026-10-23"
    assert athens.date(dt.datetime(2026, 12, 22, 21, 30, tzinfo=UTC)) == "2026-12-22"


def test_matches_tzdata_where_available():
    try:
        import zoneinfo
        zone = zoneinfo.ZoneInfo("Europe/Athens")
    except Exception:  # noqa: BLE001 - no tzdata on this machine: nothing to compare with
        return
    t = dt.datetime(2024, 1, 1, tzinfo=UTC)
    while t.year < 2029:
        assert athens.local(t).utcoffset() == t.astimezone(zone).utcoffset(), t
        t += dt.timedelta(minutes=30)


def test_no_module_needs_zoneinfo():
    found = [p.name for p in tipsterpath.HERE.glob("*.py")
             if not p.name.startswith("test_") and "import zoneinfo" in p.read_text(encoding="utf-8")]
    assert found == [], found


if __name__ == "__main__":
    raise SystemExit(testkit.run(globals()))
