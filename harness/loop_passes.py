#!/usr/bin/env python3
"""Count the cron loop passes that were due and did not happen.

Each loop is a cron job that fires into an agent session as a turn. A turn arriving while the
session is inside long foreground work queues behind it, and if the session is still busy at the
next firing the earlier one is never executed. Nothing wrote at fire time, so the only trace a pass
left was whatever it wrote at the END - and a pass that did not run writes nothing at all.

Most loops cannot be audited from their own state at all, because that state moves only when
there is something to say - a post, a send, a new message - so a quiet pass and an absent pass
leave the same file.

So `record` is called when a pass FIRES, before it does any work, and `missed` compares the ledger
against the schedule rather than against itself. Comparing a ledger to itself is what makes a dead
loop look alive: a file that never changes reads exactly like a loop with nothing to report.
"""
from __future__ import annotations

import argparse
import datetime as dt
import functools
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import bobcfg  # noqa: E402

#: One ledger for every loop. The loop name is a column, not a filename, so a loop that has never
#: written is visibly absent rather than being a file nobody thought to look for.
LEDGER = bobcfg.state_home() / "loop-passes.jsonl"

#: Claude Code fires a recurring job up to half its period late, at most 30 minutes, to spread load;
#: CronList shows a 07:30 job's next run as 08:00. A tolerance below that reports a pass that ran
#: on Claude's schedule as missed, and a catch-up keyed off it runs the job twice.
MAX_DELAY_S = 30 * 60
#: Then the turn has to be picked up; at most ten minutes, and never more than a quarter period.
START_SLACK_S = 10 * 60

#: loop name -> the cron schedule its job is registered on, from the manifest's [loops.schedules].
SCHEDULES = dict(bobcfg.get(bobcfg.config(), "loops.schedules"))


def _field(spec: str, lo: int, hi: int) -> list[int]:
    """The values a single cron field matches, over the inclusive range lo..hi.

    `*/5` is a step over the whole range and `7,19` is a list. Reading the step's operand as a
    literal is the quiet failure: `*/5` would match the single minute 5, turning a five-minute loop
    into an hourly one that reports eleven of every twelve passes as fine."""
    out: set[int] = set()
    for part in spec.split(","):
        step = 1
        if "/" in part:
            part, _, raw = part.partition("/")
            step = int(raw)
        if part == "*":
            first, last = lo, hi
        elif "-" in part:
            a, _, b = part.partition("-")
            first, last = int(a), int(b)
        else:
            first = last = int(part)
        out.update(range(first, last + 1, step))
    return sorted(v for v in out if lo <= v <= hi)


def _day_matcher(dom: str, month: str, dow: str):
    """Whether a date is one the schedule runs on.

    Reading only minute and hour makes every schedule daily: a Saturday job is then "missed" every
    weekday and a backstop runs it every day. Cron's own rule applies when both day fields are
    restricted: a day matching either fires; when either is `*`, both must match. Weekday 0 and 7
    are both Sunday."""
    doms, months = set(_field(dom, 1, 31)), set(_field(month, 1, 12))
    dows = {d % 7 for d in _field(dow, 0, 7)}
    either = not dom.startswith("*") and not dow.startswith("*")

    def matches(day: dt.datetime) -> bool:
        in_dom, in_dow = day.day in doms, day.isoweekday() % 7 in dows
        return day.month in months and ((in_dom or in_dow) if either else (in_dom and in_dow))
    return matches


def firings(schedule: str, start: dt.datetime, end: dt.datetime) -> list[dt.datetime]:
    """Every time the schedule fires strictly after `start` and at or before `end`.

    Strictly after, because `start` is the moment of the last pass we have evidence for and that
    firing is already accounted for. Minute resolution, which is all cron offers."""
    minute, hour, dom, month, dow = schedule.split()
    minutes, hours = _field(minute, 0, 59), _field(hour, 0, 23)
    runs_on = _day_matcher(dom, month, dow)
    out: list[dt.datetime] = []
    cursor = start.replace(second=0, microsecond=0)
    while cursor <= end:
        if cursor > start and cursor.hour in hours and cursor.minute in minutes and runs_on(cursor):
            out.append(cursor)
        cursor += dt.timedelta(minutes=1)
    return out


@functools.cache
def period_s(schedule: str) -> int:
    """The shortest gap between two firings, over two weeks from a fixed Monday. A schedule firing
    at most once in that span (monthly, yearly) counts as two weeks: its delay is capped anyway."""
    span = dt.timedelta(days=14)
    start = dt.datetime(2026, 1, 5)
    fired = firings(schedule, start, start + span)
    gaps = [(b - a).total_seconds() for a, b in zip(fired, fired[1:])]
    return int(min(gaps, default=span.total_seconds()))


def tolerance_s(schedule: str) -> int:
    """How long after a firing its record may land: Claude's delay plus the time to start.

    Always below the period, so one record can never cover two firings: at most half the period
    for the delay and a quarter for the start."""
    period = period_s(schedule)
    return int(min(period / 2, MAX_DELAY_S) + min(period / 4, START_SLACK_S))


def missed(loop: str, schedule: str, start: dt.datetime, end: dt.datetime,
           rows, tolerance: int | None = None) -> list[dt.datetime]:
    """The firings of `loop` in the interval that no record of `loop` covers.

    A record covers a firing when it lands at or after it and within the tolerance. FORWARDS ONLY:
    a pass cannot have run before it was due, and a window that reaches backwards lets one late
    record certify every firing before it, so one late write would hide several missed firings.

    An empty ledger reports every firing rather than nothing. That is the same shape a loop which
    has stopped firing has, and reporting nothing for it is how a dead loop looks alive.

    But once a loop HAS records, firings before its earliest one are unjudgeable and are skipped:
    the ledger did not exist yet, so its silence is not evidence. Without this floor, a freshly
    seeded ledger reports every firing of the past day as missed and buries the real holes.

    The two rules together separate "absent" from "never observed": a loop with no rows has NEVER
    recorded, which is worth reporting; a firing older than a loop's first row was not observed."""
    tolerance = tolerance if tolerance is not None else tolerance_s(schedule)
    seen = [dt.datetime.fromisoformat(r["at"]) for r in rows if r.get("loop") == loop]
    floor = min(seen) if seen else None
    return [fire for fire in firings(schedule, start, end)
            if (floor is None or fire >= floor - dt.timedelta(seconds=tolerance))
            and not any(0 <= (s - fire).total_seconds() <= tolerance for s in seen)]


def overdue(loop: str, schedule: str, now: dt.datetime, rows,
            grace_s: int | None = None) -> dt.datetime | None:
    """The most recent firing of `loop`, when it is more than `grace_s` old and has no record at
    or after it; otherwise None. Only the most recent firing is judged: an older miss is history
    for `--audit`, and running the job now covers it anyway.

    A backstop for a job whose firing can arrive late or not at all: another loop's pass calls
    this and runs the job itself when it returns a firing. Only records at or after the firing
    count, for the same reason `missed` looks forwards only.

    The look-back is a week and a day: the last firing of a weekly job can be six days old, and a
    one-day window never sees it."""
    grace = grace_s if grace_s is not None else tolerance_s(schedule)
    fired = firings(schedule, now - dt.timedelta(days=8), now)
    if not fired or (now - fired[-1]).total_seconds() <= grace:
        return None
    last = fired[-1]
    seen = [dt.datetime.fromisoformat(r["at"]) for r in rows if r.get("loop") == loop]
    return None if any(s >= last for s in seen) else last


def catch_up(now: dt.datetime, rows, schedules: dict[str, str], names: list[str]) -> list[tuple[str, dt.datetime]]:
    """(loop, missed firing) for each loop in `names` whose latest firing has no pass.

    A firing that comes while the machine sleeps or the session is busy never runs, and nothing
    wrote at the time, so the job is simply skipped: a nightly screen that misses its slot loses
    the day. The health-checks pass runs each one listed here once, late; the late pass records
    itself, so the next check finds nothing to catch up.

    A firing before the ledger's first row of any loop came before this Bob recorded anything:
    its loops were not registered yet, so nothing was missed. Without this floor a new Bob's first
    health check runs every loop at once for slots from before it existed."""
    starts = [dt.datetime.fromisoformat(r["at"]) for r in rows if r.get("at")]
    floor = min(starts) if starts else None
    return [(name, late) for name in names
            if (late := overdue(name, schedules[name], now, rows)) is not None
            and (floor is None or late >= floor)]


def read(path: pathlib.Path = LEDGER) -> list[dict]:
    """Every row the ledger holds, malformed lines skipped.

    A half-written line from a torn append must not make the audit unanswerable - an audit that
    raises is an audit nobody runs."""
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def record(loop: str, at: dt.datetime | None = None, path: pathlib.Path = LEDGER) -> dict:
    """Append a fire-time row. Called at the START of a pass, before it does any work."""
    row = {"loop": loop, "at": (at or dt.datetime.now()).isoformat(timespec="seconds")}
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row) + "\n")
    return row


def audit(hours: int = 24, path: pathlib.Path = LEDGER) -> dict:
    """Per loop, the firings in the last `hours` that no record covers."""
    rows = read(path)
    end = dt.datetime.now()
    start = end - dt.timedelta(hours=hours)
    return {loop: missed(loop, schedule, start, end, rows)
            for loop, schedule in SCHEDULES.items()}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--record", metavar="LOOP", help="append a fire-time row for LOOP")
    parser.add_argument("--audit", action="store_true", help="report missed firings, exit 1 if any")
    parser.add_argument("--hours", type=int, default=24)
    parser.add_argument("--overdue", metavar="LOOP",
                        help="print the last firing of LOOP with no record after it; exit 1 if any")
    parser.add_argument("--catch-up", action="store_true",
                        help="list the [loops] catch_up loops whose latest firing never ran; exit 1 if any")
    args = parser.parse_args(argv)

    if args.catch_up:
        late = catch_up(dt.datetime.now(), read(), SCHEDULES, bobcfg.get(bobcfg.config(), "loops").get("catch_up", []))
        for loop, fire in late:
            print(f"CATCH UP {loop}: due {fire.isoformat(timespec='minutes')}, no pass since; "
                  f"run {loop_file(loop)} now")
        if not late:
            print("nothing to catch up")
        return 1 if late else 0

    if args.record:
        if args.record not in SCHEDULES:
            print(f"unknown loop {args.record}; known: {', '.join(sorted(SCHEDULES))}",
                  file=sys.stderr)
            return 2
        print(json.dumps(record(args.record)))
        return 0

    if args.overdue:
        if args.overdue not in SCHEDULES:
            print(f"unknown loop {args.overdue}", file=sys.stderr)
            return 2
        late = overdue(args.overdue, SCHEDULES[args.overdue], dt.datetime.now(), read())
        if late is None:
            print(f"{args.overdue}: not overdue")
            return 0
        print(f"{args.overdue}: OVERDUE, due {late.isoformat(timespec='minutes')}, no record since")
        return 1

    holes = audit(args.hours)
    total = sum(len(v) for v in holes.values())
    for loop in sorted(SCHEDULES):
        gone = holes[loop]
        mark = "ok  " if not gone else "MISS"
        print(f"  {mark} {loop:16} {SCHEDULES[loop]:14} {len(gone)} missed")
        for fire in gone[:8]:
            print(f"         due {fire.isoformat(timespec='minutes')}, no record")
        if len(gone) > 8:
            print(f"         ... and {len(gone) - 8} more")
    print(f"{total} missed pass(es) across {len(SCHEDULES)} loops in the last {args.hours}h")
    absent = missing_loop_files()
    for rel in absent:
        print(f"  MISSING loop file {rel}: listed in [loops] own, not on disk")
    return 1 if total or absent else 0


def loop_file(loop: str) -> str:
    """The prompt file a loop runs: its [loops] own entry, else the template's `loops/<name>.txt`."""
    own = bobcfg.get(bobcfg.config(), "loops").get("own", [])
    return next((rel for rel in own if bobcfg.loop_name(rel) == loop), f"loops/{loop}.txt")


def missing_loop_files() -> list[str]:
    """The Bob's own loop files that [loops] own lists and the folder does not hold. A loop whose
    prompt file is gone cannot be re-registered from it, and its schedule audits a ghost."""
    own = bobcfg.get(bobcfg.config(), "loops").get("own", [])
    return [rel for rel in own if not (bobcfg.bob_root() / rel).is_file()]


if __name__ == "__main__":
    import oscompat
    oscompat.utf8_stdio()
    sys.exit(main())
