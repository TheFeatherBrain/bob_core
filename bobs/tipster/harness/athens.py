#!/usr/bin/env python3
"""Athens local time from a UTC instant, by the EU summer-time rule, using the standard library only.

Why not zoneinfo: on Windows, Python ships no time zone database; zoneinfo finds Europe/Athens only
through the third-party tzdata package, and a Bob without it would fail every loop at import. The
EU rule is fixed in law: summer time (UTC+3) from 01:00 UTC on the last Sunday of March to 01:00
UTC on the last Sunday of October; UTC+2 otherwise. Athens time is for display and "game day" only;
everything is stored and compared in UTC.

Usage (library):
    athens.local(when)   # aware datetime in Athens time
    athens.date(when)    # "YYYY-MM-DD" in Athens
"""
from __future__ import annotations

import datetime as dt

EET = dt.timezone(dt.timedelta(hours=2), "EET")
EEST = dt.timezone(dt.timedelta(hours=3), "EEST")


def _last_sunday_0100(year: int, month: int) -> dt.datetime:
    day = dt.date(year, month, 31)
    day -= dt.timedelta(days=(day.weekday() + 1) % 7)
    return dt.datetime(day.year, day.month, day.day, 1, 0, tzinfo=dt.timezone.utc)


def local(when: dt.datetime) -> dt.datetime:
    utc = when.astimezone(dt.timezone.utc)
    summer = _last_sunday_0100(utc.year, 3) <= utc < _last_sunday_0100(utc.year, 10)
    return utc.astimezone(EEST if summer else EET)


def date(when: dt.datetime) -> str:
    return local(when).date().isoformat()
