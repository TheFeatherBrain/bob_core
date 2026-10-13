#!/usr/bin/env python3
"""Is the out-of-session watchdog loaded, current, and ticking?

The only watchdog outside the Claude session is the LaunchAgent that runs `session_supervisor.py`.
A LaunchAgent that was never loaded, or stopped ticking, looks exactly like one that works unless
something checks. This reads the three things that decide it, each from its own source:

  loaded     `launchctl print gui/<uid>/<label>` answers, and its last exit code is 0
  current    the installed plist is byte-identical to the Bob's copy in supervisor/<label>.plist
  ticking    the supervisor log's newest tick is younger than two run intervals

    python3 harness/watchdog_check.py            # WATCHDOG: PASS | FAIL, with each problem
    python3 harness/watchdog_check.py --heal     # a stale tick alone: kickstart, then re-check
"""
from __future__ import annotations

import datetime as _dt
import os
import pathlib
import re
import subprocess
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import bobcfg  # noqa: E402

LABEL = bobcfg.get(bobcfg.config(), "runtime.launchd_label")
REPO = bobcfg.bob_root()
REPO_PLIST = REPO / "supervisor" / f"{LABEL}.plist"
INSTALLED_PLIST = pathlib.Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"
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


def last_tick_age(log_text: str, now: _dt.datetime) -> float | None:
    """Seconds since the newest supervisor tick in the log, or None if it holds none."""
    stamps = TICK.findall(log_text)
    if not stamps:
        return None
    return (now - _dt.datetime.fromisoformat(stamps[-1])).total_seconds()


def problems(info: dict, age: float | None, plist_same: bool) -> list[str]:
    out = []
    if not info.get("loaded"):
        return ["LaunchAgent not loaded: launchctl cannot find " + LABEL]
    if info.get("last_exit") not in (0, None):
        out.append(f"last exit code {info['last_exit']}")
    if not plist_same:
        out.append("installed plist differs from supervisor/" + REPO_PLIST.name)
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
    """Kickstart the agent and return the age of the tick it writes, or None if none appears."""
    before = TICK.findall(read_log())
    run(["launchctl", "kickstart", f"gui/{os.getuid()}/{LABEL}"])
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
    shown = subprocess.run(["launchctl", "print", f"gui/{os.getuid()}/{LABEL}"],
                           capture_output=True, text=True)
    info = parse_launchctl(shown.stdout + shown.stderr)
    read_log = lambda: LOG.read_text(errors="replace") if LOG.exists() else ""  # noqa: E731
    age = last_tick_age(read_log(), _dt.datetime.now())
    same = (INSTALLED_PLIST.exists() and REPO_PLIST.exists()
            and INSTALLED_PLIST.read_bytes() == REPO_PLIST.read_bytes())
    found = problems(info, age, same)
    print(f"loaded {info.get('loaded')}, runs {info.get('runs')}, last exit {info.get('last_exit')}, "
          f"pended {info.get('pended')}, newest tick {'none' if age is None else f'{age:.0f} s ago'}, "
          f"plist current {same}")
    for p in found:
        print(f"  {p}")
    if found and "--heal" in args and healable(info, age, same):
        new_age = heal(lambda a: subprocess.run(a, capture_output=True), read_log, _dt.datetime.now)
        stamp = _dt.datetime.now().isoformat(timespec="seconds")
        with HEAL_LOG.open("a") as fh:
            fh.write(f"{stamp} kickstart after {age:.0f} s stale, runs {info.get('runs')}, "
                     f"pended {info.get('pended')}, new tick {'none' if new_age is None else 'yes'}\n")
        if new_age is not None:
            print(f"WATCHDOG: HEALED by kickstart (tick was {age:.0f} s old; recorded in {HEAL_LOG})")
            return 0
        print("  kickstart wrote no tick within the wait")
    print(f"WATCHDOG: {'FAIL' if found else 'PASS'}")
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
