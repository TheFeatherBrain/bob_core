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


def EARLIER(loop: str) -> dict:
    """A record from days before every test window: the ledger already existed, so firings in the
    window are judged instead of skipped as older than the ledger."""
    return {"loop": loop, "at": "2026-10-10T00:00:00"}


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


def test_a_daily_pass_claude_delays_thirty_minutes_is_not_missed():
    """Claude Code fires a recurring job up to half its period late, at most 30 minutes."""
    rows = [EARLIER("nightly-screen"), {"loop": "nightly-screen", "at": "2026-10-20T08:00:30"}]
    assert loop_passes.missed("nightly-screen", "30 7 * * 2-6", *day(2026, 10, 20), rows) == []


def test_a_daily_pass_later_than_the_delay_is_missed():
    rows = [EARLIER("nightly-screen"), {"loop": "nightly-screen", "at": "2026-10-20T08:15:00"}]
    assert loop_passes.missed("nightly-screen", "30 7 * * 2-6", *day(2026, 10, 20), rows) == [
        D(2026, 10, 20, 7, 30)]


def test_one_record_never_covers_two_hourly_firings():
    rows = [EARLIER("health-checks"), {"loop": "health-checks", "at": "2026-10-19T18:17:30"}]
    got = loop_passes.missed("health-checks", "17 * * * *", D(2026, 10, 19, 17, 0), D(2026, 10, 19, 18, 30), rows)
    assert got == [D(2026, 10, 19, 17, 17)], got


def test_one_record_never_covers_two_ten_minute_firings():
    rows = [EARLIER("slack-watch"), {"loop": "slack-watch", "at": "2026-10-19T10:07:00"}]
    got = loop_passes.missed("slack-watch", "*/10 * * * *", D(2026, 10, 19, 9, 45), D(2026, 10, 19, 10, 8), rows)
    assert got == [D(2026, 10, 19, 9, 50)], got


def test_tolerance_stays_below_the_period():
    for schedule in ("*/5 * * * *", "*/10 * * * *", "17 * * * *", "30 7 * * 2-6", "0 9 * * 6", "0 9 1 * *"):
        tol = loop_passes.tolerance_s(schedule)
        assert 0 < tol < loop_passes.period_s(schedule), (schedule, tol)
    assert loop_passes.tolerance_s("30 7 * * 2-6") == 40 * 60


def test_overdue_waits_out_claudes_delay():
    assert loop_passes.overdue("nightly-screen", "30 7 * * 2-6", D(2026, 10, 20, 7, 55), []) is None


def test_overdue_flags_a_daily_run_past_the_delay():
    assert loop_passes.overdue("nightly-screen", "30 7 * * 2-6", D(2026, 10, 20, 8, 15), []) == D(2026, 10, 20, 7, 30)


def test_catch_up_lists_only_named_loops_that_are_overdue():
    schedules = {"nightly-screen": "30 7 * * 2-6", "weekly-digest": "0 9 * * 6", "daily-trade": "30 8 * * 2-6"}
    rows = [EARLIER("health-checks"), {"loop": "nightly-screen", "at": "2026-10-20T07:58:00"}]
    got = loop_passes.catch_up(D(2026, 10, 20, 10, 0), rows, schedules, ["nightly-screen", "weekly-digest"])
    assert got == [("weekly-digest", D(2026, 10, 17, 9, 0))], got


def test_catch_up_skips_firings_from_before_the_bob_recorded_anything():
    """Firings before the ledger's first row came before the loops existed: nothing was missed."""
    schedules = {"weekly-digest": "0 9 * * 6", "health-checks": "17 * * * *"}
    rows = [{"loop": "health-checks", "at": "2026-10-19T13:17:56"}]
    assert loop_passes.catch_up(D(2026, 10, 19, 16, 0), rows, schedules, ["weekly-digest"]) == []


def test_catch_up_runs_a_firing_missed_after_the_bob_started():
    schedules = {"weekly-digest": "0 9 * * 6", "health-checks": "17 * * * *"}
    rows = [{"loop": "health-checks", "at": "2026-10-19T13:17:56"}]
    got = loop_passes.catch_up(D(2026, 10, 24, 11, 0), rows, schedules, ["weekly-digest"])
    assert got == [("weekly-digest", D(2026, 10, 24, 9, 0))], got


def test_catch_up_is_empty_when_every_named_loop_ran():
    schedules = {"nightly-screen": "30 7 * * 2-6", "weekly-digest": "0 9 * * 6"}
    rows = [{"loop": "nightly-screen", "at": "2026-10-20T07:58:00"},
            {"loop": "weekly-digest", "at": "2026-10-19T11:17:00"}]
    assert loop_passes.catch_up(D(2026, 10, 20, 10, 0), rows, schedules, list(schedules)) == []


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
