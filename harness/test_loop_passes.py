#!/usr/bin/env python3
"""loop_passes.py reads cron schedules to decide which loop passes were due.

Reading only minute and hour turns every schedule into a daily one: a Saturday digest is "missed"
every weekday, and the `--overdue` backstop would run a weekly job every day. Each field gets a
refuse/allow pair: the schedule must not fire on a day it excludes and must fire on one it names.

Dates used: 2026-10-17 is a Saturday, 2026-10-19 a Monday, 2026-10-20 a Tuesday.

Run:  BOB_MANIFEST=$PWD/bob.example.toml python3 harness/test_loop_passes.py
"""
from __future__ import annotations

import datetime as dt
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import loop_passes  # noqa: E402

D = dt.datetime


def day(y: int, m: int, d: int) -> tuple[dt.datetime, dt.datetime]:
    """The whole calendar day: firings are strictly after start, so start one minute early."""
    start = D(y, m, d) - dt.timedelta(minutes=1)
    return start, start + dt.timedelta(days=1)


def test_saturday_schedule_does_not_fire_on_monday():
    assert loop_passes.firings("0 9 * * 6", *day(2026, 10, 19)) == []


def test_saturday_schedule_fires_on_saturday():
    assert loop_passes.firings("0 9 * * 6", *day(2026, 10, 17)) == [D(2026, 10, 17, 9, 0)]


def test_weekday_range_skips_monday():
    assert loop_passes.firings("30 7 * * 2-6", *day(2026, 10, 19)) == []


def test_weekday_range_fires_on_tuesday():
    assert loop_passes.firings("30 7 * * 2-6", *day(2026, 10, 20)) == [D(2026, 10, 20, 7, 30)]


def test_sunday_is_both_0_and_7():
    sunday = day(2026, 10, 18)
    assert loop_passes.firings("0 9 * * 0", *sunday) == [D(2026, 10, 18, 9, 0)]
    assert loop_passes.firings("0 9 * * 7", *sunday) == [D(2026, 10, 18, 9, 0)]
    assert loop_passes.firings("0 9 * * 7", *day(2026, 10, 19)) == []


def test_day_of_month_skips_other_days():
    assert loop_passes.firings("0 9 1 * *", *day(2026, 10, 19)) == []


def test_day_of_month_fires_on_its_day():
    assert loop_passes.firings("0 9 1 * *", *day(2026, 10, 1)) == [D(2026, 10, 1, 9, 0)]


def test_month_skips_other_months():
    assert loop_passes.firings("0 9 1 1 *", *day(2026, 10, 1)) == []


def test_month_fires_in_its_month():
    assert loop_passes.firings("0 9 1 1 *", *day(2026, 1, 1)) == [D(2026, 1, 1, 9, 0)]


def test_day_of_month_and_weekday_both_set_match_either():
    """Cron's rule: with both fields restricted, a day matching either one fires."""
    assert loop_passes.firings("0 9 1 * 1", *day(2026, 10, 19)) == [D(2026, 10, 19, 9, 0)]
    assert loop_passes.firings("0 9 1 * 1", *day(2026, 10, 1)) == [D(2026, 10, 1, 9, 0)]
    assert loop_passes.firings("0 9 1 * 1", *day(2026, 10, 20)) == []


def test_hourly_schedule_still_fires_every_hour():
    assert len(loop_passes.firings("17 * * * *", *day(2026, 10, 19))) == 24


def test_audit_does_not_report_a_weekly_loop_on_an_off_day():
    assert loop_passes.missed("weekly-digest", "0 9 * * 6", *day(2026, 10, 19), rows=[]) == []


def test_audit_reports_a_weekly_loop_missed_on_its_day():
    assert loop_passes.missed("weekly-digest", "0 9 * * 6", *day(2026, 10, 17), rows=[]) == [
        D(2026, 10, 17, 9, 0)]


def test_overdue_does_not_flag_a_weekly_loop_that_ran():
    rows = [{"loop": "weekly-digest", "at": "2026-10-17T09:02:00"}]
    assert loop_passes.overdue("weekly-digest", "0 9 * * 6", D(2026, 10, 19, 10, 0), rows) is None


def test_overdue_flags_a_weekly_loop_missed_days_ago():
    """The last firing of a weekly job can be up to a week old; a one-day look-back never sees it."""
    assert loop_passes.overdue("weekly-digest", "0 9 * * 6", D(2026, 10, 19, 10, 0), []) == D(
        2026, 10, 17, 9, 0)


def main() -> int:
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"PASS {name}")
        except Exception as e:  # noqa: BLE001 - a crash is a failure, reported by name
            failed += 1
            print(f"FAIL {name}: {type(e).__name__}: {e}")
    print(f"{len(tests) - failed} of {len(tests)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
