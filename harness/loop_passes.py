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
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import bobcfg  # noqa: E402

#: One ledger for every loop. The loop name is a column, not a filename, so a loop that has never
#: written is visibly absent rather than being a file nobody thought to look for.
LEDGER = bobcfg.state_home() / "loop-passes.jsonl"

#: A pass starts a little after it fires - the turn has to be picked up. Ten minutes is generous
#: for that and still below the shortest hourly interval, which is what keeps one late record from
#: covering the firing before it.
TOLERANCE_S = 600

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


def firings(schedule: str, start: dt.datetime, end: dt.datetime) -> list[dt.datetime]:
    """Every time the schedule fires strictly after `start` and at or before `end`.

    Strictly after, because `start` is the moment of the last pass we have evidence for and that
    firing is already accounted for. Minute resolution, which is all cron offers."""
    minute, hour, *_rest = schedule.split()
    minutes, hours = _field(minute, 0, 59), _field(hour, 0, 23)
    out: list[dt.datetime] = []
    cursor = start.replace(second=0, microsecond=0)
    while cursor <= end:
        if cursor > start and cursor.hour in hours and cursor.minute in minutes:
            out.append(cursor)
        cursor += dt.timedelta(minutes=1)
    return out


def missed(loop: str, schedule: str, start: dt.datetime, end: dt.datetime,
           rows, tolerance_s: int = TOLERANCE_S) -> list[dt.datetime]:
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
    seen = [dt.datetime.fromisoformat(r["at"]) for r in rows if r.get("loop") == loop]
    floor = min(seen) if seen else None
    return [fire for fire in firings(schedule, start, end)
            if (floor is None or fire >= floor - dt.timedelta(seconds=tolerance_s))
            and not any(0 <= (s - fire).total_seconds() <= tolerance_s for s in seen)]


def overdue(loop: str, schedule: str, now: dt.datetime, rows,
            grace_s: int = TOLERANCE_S) -> dt.datetime | None:
    """The most recent firing of `loop`, when it is more than `grace_s` old and has no record at
    or after it; otherwise None. Only the most recent firing is judged: an older miss is history
    for `--audit`, and running the job now covers it anyway.

    A backstop for a job whose firing can arrive late or not at all: another loop's pass calls
    this and runs the job itself when it returns a firing. Only records at or after the firing
    count, for the same reason `missed` looks forwards only."""
    fired = firings(schedule, now - dt.timedelta(days=1), now)
    if not fired or (now - fired[-1]).total_seconds() <= grace_s:
        return None
    last = fired[-1]
    seen = [dt.datetime.fromisoformat(r["at"]) for r in rows if r.get("loop") == loop]
    return None if any(s >= last for s in seen) else last


def read(path: pathlib.Path = LEDGER) -> list[dict]:
    """Every row the ledger holds, malformed lines skipped.

    A half-written line from a torn append must not make the audit unanswerable - an audit that
    raises is an audit nobody runs."""
    if not path.exists():
        return []
    out = []
    for line in path.read_text().splitlines():
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
    with path.open("a") as fh:
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
    args = parser.parse_args(argv)

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


def missing_loop_files() -> list[str]:
    """The Bob's own loop files that [loops] own lists and the folder does not hold. A loop whose
    prompt file is gone cannot be re-registered from it, and its schedule audits a ghost."""
    own = bobcfg.get(bobcfg.config(), "loops").get("own", [])
    return [rel for rel in own if not (bobcfg.bob_root() / rel).is_file()]


if __name__ == "__main__":
    sys.exit(main())
