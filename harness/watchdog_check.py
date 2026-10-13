#!/usr/bin/env python3
"""Is the out-of-session watchdog loaded, current, and ticking?

The only watchdog outside the Claude session is the scheduler job that runs
`session_supervisor.py`: a LaunchAgent on macOS, a Task Scheduler task on Windows, both named by
`[runtime] launchd_label`. A job that was never loaded, or stopped ticking, looks exactly like one
that works unless something checks. This reads the three things that decide it, each from its own
source:

  loaded     macOS: `launchctl print gui/<uid>/<label>` answers, and its last exit code is 0
             Windows: `Get-ScheduledTask <label>` answers, and its last result is 0
  current    macOS: the installed plist is byte-identical to the Bob's supervisor/<label>.plist
             Windows: the task runs the Bob's own supervisor/tick.cmd
  ticking    the supervisor log's newest tick is younger than two run intervals

    python3 harness/watchdog_check.py            # WATCHDOG: PASS | FAIL, with each problem
    python3 harness/watchdog_check.py --heal     # a stale tick alone: start the job, then re-check
    python3 harness/watchdog_check.py --selftest
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import pathlib
import re
import subprocess
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import bobcfg  # noqa: E402
import oscompat  # noqa: E402

LABEL = bobcfg.get(bobcfg.config(), "runtime.launchd_label")
REPO = bobcfg.bob_root()
REPO_PLIST = REPO / "supervisor" / f"{LABEL}.plist"
INSTALLED_PLIST = pathlib.Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"
REPO_TICK = REPO / "supervisor" / "tick.cmd"
#: Task Scheduler results that are not an exit code: "has not yet run" and "currently running".
TASK_PENDING_RESULTS = {0x41303, 0x41301}
TASK_QUERY = ("[Console]::OutputEncoding = [Text.Encoding]::UTF8; "
              "$t = Get-ScheduledTask -TaskName '{label}' -ErrorAction Stop; "
              "$i = $t | Get-ScheduledTaskInfo; "
              "[pscustomobject]@{{state = [string]$t.State; last = $i.LastTaskResult; "
              "missed = $i.NumberOfMissedRuns; execute = $t.Actions[0].Execute; "
              "interval = [string]$t.Triggers[0].Repetition.Interval}} | ConvertTo-Json -Compress")
LOG = pathlib.Path(bobcfg.get(bobcfg.config(), "runtime.agent_log"))
#: One line per kickstart, so a stall that keeps recurring is counted rather than silently healed.
HEAL_LOG = bobcfg.state_home() / "watchdog-heals.log"
TICK = re.compile(r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})\s+=== supervisor tick", re.M)


def parse_launchctl(text: str) -> dict:
    """The fields that decide 'loaded', from `launchctl print` output; loaded False if it refused."""
    if "Could not find service" in text or not re.search(r"^\s*path = ", text, re.M):
        return {"loaded": False}

    def num(key):
        m = re.search(rf"^\s*{key} = (-?\d+)", text, re.M)
        return int(m.group(1)) if m else None

    return {"loaded": True, "runs": num("runs"), "last_exit": num("last exit code"),
            "interval": num("run interval"),
            "pended": bool(re.search(r"^\s*pended nondemand spawn = ", text, re.M))}


def parse_task(text: str) -> dict:
    """The same fields as parse_launchctl, from the TASK_QUERY JSON; loaded False if it refused."""
    try:
        row = json.loads(text)
    except ValueError:
        return {"loaded": False}
    if not isinstance(row, dict):
        return {"loaded": False}
    last = row.get("last")
    m = re.fullmatch(r"P(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?)?", row.get("interval") or "")
    interval = (int(m.group(1) or 0) * 3600 + int(m.group(2) or 0) * 60 + int(m.group(3) or 0)
                if m else 0) or None
    return {"loaded": True, "runs": None,
            "last_exit": None if last in TASK_PENDING_RESULTS else last,
            "interval": interval, "pended": bool(row.get("missed")),
            "execute": row.get("execute") or ""}


def task_current(info: dict) -> bool:
    """The task runs this Bob's own tick.cmd, so a moved or renamed Bob is caught."""
    execute = info.get("execute", "").strip('"')
    return (REPO_TICK.is_file() and bool(execute)
            and os.path.normcase(os.path.abspath(execute)) == os.path.normcase(str(REPO_TICK)))


def query() -> dict:
    if oscompat.WINDOWS:
        shown = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command",
                                TASK_QUERY.format(label=LABEL.replace("'", "''"))],
                               capture_output=True, text=True, encoding="utf-8", errors="replace")
        return parse_task(shown.stdout if shown.returncode == 0 else "")
    shown = subprocess.run(["launchctl", "print", f"gui/{os.getuid()}/{LABEL}"],
                           capture_output=True, text=True, encoding="utf-8", errors="replace")
    return parse_launchctl(shown.stdout + shown.stderr)


def start_command() -> list[str]:
    if oscompat.WINDOWS:
        return ["schtasks", "/Run", "/TN", LABEL]
    return ["launchctl", "kickstart", f"gui/{os.getuid()}/{LABEL}"]


def is_current(info: dict) -> bool:
    if oscompat.WINDOWS:
        return task_current(info)
    return (INSTALLED_PLIST.exists() and REPO_PLIST.exists()
            and INSTALLED_PLIST.read_bytes() == REPO_PLIST.read_bytes())


def last_tick_age(log_text: str, now: _dt.datetime) -> float | None:
    """Seconds since the newest supervisor tick in the log, or None if it holds none."""
    stamps = TICK.findall(log_text)
    if not stamps:
        return None
    return (now - _dt.datetime.fromisoformat(stamps[-1])).total_seconds()


def problems(info: dict, age: float | None, plist_same: bool) -> list[str]:
    out = []
    if not info.get("loaded"):
        if oscompat.WINDOWS:
            return ["scheduled task not registered: Get-ScheduledTask cannot find " + LABEL]
        return ["LaunchAgent not loaded: launchctl cannot find " + LABEL]
    if info.get("last_exit") not in (0, None):
        out.append(f"last exit code {info['last_exit']}")
    if not plist_same:
        out.append(f"scheduled task does not run {REPO_TICK}" if oscompat.WINDOWS
                   else "installed plist differs from supervisor/" + REPO_PLIST.name)
    interval = info.get("interval") or 1200
    if age is None:
        out.append(f"no supervisor tick in {LOG}")
    elif age > 2 * interval:
        out.append(f"newest supervisor tick is {age:.0f} s old, over 2 x {interval} s")
    return out


def healable(info: dict, age: float | None, plist_same: bool) -> bool:
    """True when the ONLY problem is a stale tick on a loaded, current, cleanly exiting agent.

    launchd can leave an interval job pending (`pended nondemand spawn = interval`) for hours with
    nothing in its log; `launchctl kickstart` starts it at once. A kickstart restores the tick; it
    does not cure a crash, a missing agent or a drifted plist, so those stay plain failures.
    """
    if not info.get("loaded") or info.get("last_exit") not in (0, None) or not plist_same:
        return False
    return age is not None and age > 2 * (info.get("interval") or 1200)


def heal(run, read_log, now, wait_s: float = 30, poll_s: float = 2) -> float | None:
    """Start the job now and return the age of the tick it writes, or None if none appears."""
    before = TICK.findall(read_log())
    run(start_command())
    deadline = time.monotonic() + wait_s
    while True:
        text = read_log()
        if len(TICK.findall(text)) > len(before):
            return last_tick_age(text, now())
        if time.monotonic() >= deadline:
            return None
        time.sleep(poll_s)


def main(argv=None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if "--selftest" in args:
        return selftest()
    info = query()
    read_log = lambda: LOG.read_text(encoding="utf-8", errors="replace") if LOG.exists() else ""  # noqa: E731
    age = last_tick_age(read_log(), _dt.datetime.now())
    same = is_current(info)
    found = problems(info, age, same)
    print(f"loaded {info.get('loaded')}, runs {info.get('runs')}, last exit {info.get('last_exit')}, "
          f"pended {info.get('pended')}, newest tick {'none' if age is None else f'{age:.0f} s ago'}, "
          f"{'task' if oscompat.WINDOWS else 'plist'} current {same}")
    for p in found:
        print(f"  {p}")
    if found and "--heal" in args and healable(info, age, same):
        new_age = heal(lambda a: subprocess.run(a, capture_output=True), read_log, _dt.datetime.now)
        stamp = _dt.datetime.now().isoformat(timespec="seconds")
        with HEAL_LOG.open("a", encoding="utf-8") as fh:
            fh.write(f"{stamp} kickstart after {age:.0f} s stale, runs {info.get('runs')}, "
                     f"pended {info.get('pended')}, new tick {'none' if new_age is None else 'yes'}\n")
        if new_age is not None:
            print(f"WATCHDOG: HEALED by kickstart (tick was {age:.0f} s old; recorded in {HEAL_LOG})")
            return 0
        print("  kickstart wrote no tick within the wait")
    print(f"WATCHDOG: {'FAIL' if found else 'PASS'}")
    return 1 if found else 0


def selftest() -> int:
    """The parsers and the decision, against fixtures; no scheduler is queried or started."""
    bad = 0

    def check(ok: bool, label: str) -> None:
        nonlocal bad
        if not ok:
            bad += 1
            print(f"FAIL {label}")

    loaded = parse_launchctl("path = /x.plist\n\truns = 7\n\tlast exit code = 0\n\trun interval = 1200\n")
    check(loaded == {"loaded": True, "runs": 7, "last_exit": 0, "interval": 1200, "pended": False},
          f"launchctl fields: {loaded}")
    check(parse_launchctl("Could not find service") == {"loaded": False}, "launchctl refusal reads loaded")

    task = parse_task(json.dumps({"state": "Ready", "last": 0, "missed": 0,
                                  "execute": str(REPO_TICK), "interval": "PT20M"}))
    check(task["loaded"] and task["last_exit"] == 0 and task["interval"] == 1200,
          f"task fields: {task}")
    check(parse_task(json.dumps({"last": 0x41303, "interval": "PT1H30M"}))["interval"] == 5400,
          "task interval hours and minutes")
    check(parse_task(json.dumps({"last": 0x41303}))["last_exit"] is None,
          "a task that has not yet run reads as a failed exit")
    check(parse_task(json.dumps({"last": 1}))["last_exit"] == 1, "a failed task exit is hidden")
    check(parse_task("") == {"loaded": False}, "an empty task query reads loaded")

    fresh, stale = 60.0, 3 * 1200.0
    ok = {"loaded": True, "last_exit": 0, "interval": 1200}
    check(problems(ok, fresh, True) == [], "a healthy job has problems")
    check(len(problems(ok, stale, True)) == 1 and healable(ok, stale, True),
          "a stale tick alone is not healable")
    check(not healable(ok, stale, False), "a drifted job is healable")
    check(not healable({**ok, "last_exit": 1}, stale, True), "a crashing job is healable")
    check(problems({"loaded": False}, None, False)[0].startswith(
        ("scheduled task not registered", "LaunchAgent not loaded")), "missing job not reported")

    print(f"watchdog selftest: {bad} problem(s)")
    return 1 if bad else 0


if __name__ == "__main__":
    oscompat.utf8_stdio()
    sys.exit(main())
