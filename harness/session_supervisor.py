#!/usr/bin/env python3
"""Keep the working sessions alive. Do no work of its own.

Run every 20 minutes by the Bob's LaunchAgent, or its Task Scheduler task on Windows (both named
by `[runtime] launchd_label`). Its whole job is: are
the sessions that do the thinking still up, and if not, bring them back. It does no work of its
own and writes no evidence. The intelligence belongs in the sessions; this only keeps the lights
on.

Configuration, all from the manifest's [runtime]:

    sessions_file   JSON, {"sessions": [{"id": "<full session id>", "name": "<session name>"}]}
    wake_prompt     the prompt a resumed session receives
    claude_bin      the claude CLI (default: `claude` on PATH)
    env_file        optional; secrets loaded before any resume (see envfile.py)

Three rules, and the second is the one that needed deciding:

1. ALIVE MEANS LEAVE IT. A live session is the goal state, so the tick does nothing to it. Killing
   and resuming a healthy session every 20 minutes would interrupt whatever it is mid-way through,
   which could leave a task half done.
2. ABSENT MEANS RESUME. A resume MAY come back under a new id: `--resume` on a session whose
   process is still alive can start a copy while the original stays `done`, and
   every other logged resume kept its id. The tick reads the id the resume prints and handles both:
   the same id leaves the config alone, a new one is written back and the old one kept in
   `previous_ids`. Without that the config goes stale after ONE restart and the supervisor spends
   every later tick resuming a corpse - which looks identical, in the log, to working.

   Unless another LIVE session carries the entry's name, whether the pinned entry is absent or still
   listed as finished or dead. Then the pin is stale, not dead - the work moved to that session -
   and resuming would run a second session under the same name. The tick logs `stale`, resumes
   nothing, writes a STALE file for a loop to alert on, and leaves the repin to a human. It does not rebind to the other session: that is the
   two-strangers call rule 3 declines to make. Names match after dropping the CLI's duplicate
   suffix (`main (2)` is `main`), and a `failed` session does not count as live for this
   rule: it cannot be doing the work.
3. A DUPLICATE THIS TICK CREATED GETS KILLED. `claude --bg --resume` on a session that is already
   running starts a copy and says so. Two copies sharing one checkout is a recorded failure here -
   a shared file collides even with path-scoped adds and an id goes missing silently - so the copy
   is removed and the original is left. Names match as in rule 2.

   Only a copy THIS TICK made. A pre-existing session that merely shares a name is left alone:
   deciding which of two strangers is stale needs a start time the listing does not carry, and
   "last in the listing wins" is an assumption about ordering, not a fact.

Run:  python3 harness/session_supervisor.py            # act
      python3 harness/session_supervisor.py --dry-run  # decide and print, touch nothing
      python3 harness/session_supervisor.py --selftest # prove the decisions against fixtures
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import pathlib
import re
import shutil
import socket
import subprocess
import sys
import time

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import bobcfg  # noqa: E402
import envfile  # noqa: E402
import oscompat  # noqa: E402

_CFG = bobcfg.config()
REPO = bobcfg.bob_root()
#: Machine-level file prefix this Bob owns, from the manifest; two Bobs never share a lock.
_TMP = bobcfg.get(_CFG, "runtime.supervisor_tmp_prefix")
CONFIG = bobcfg.bob_file(_CFG, "runtime.sessions_file")
WAKE = bobcfg.bob_file(_CFG, "runtime.wake_prompt")
#: launchd runs with a minimal PATH, so an explicit `runtime.claude_bin` is the reliable choice.
CLAUDE = pathlib.Path((_CFG.get("runtime") or {}).get("claude_bin")
                      or shutil.which("claude") or "claude")
LOCK = pathlib.Path(f"{_TMP}.lock")
#: Ticks a session must look dead on, consecutively, before it is resumed. ONE observation is not
#: a death: a transient failure of `claude agents --json`, or a session momentarily absent from the
#: listing while alive, would otherwise start a second copy of a session that is merely busy - and
#: two copies doing the same work collide. Two ticks
#: costs 20 minutes of downtime in a real death and removes the whole false-positive class.
CONFIRM_TICKS = 2
STRIKES = pathlib.Path(f"{_TMP}-strikes.json")
#: Present while the last tick on the live config found a stale pin, for a loop to alert on;
#: removed by the first tick that finds none.
#: A tick given any other `--config` uses a sibling of that file instead - see `stale_file`.
STALE = pathlib.Path(f"{_TMP}-stale.json")
#: Prepended to every log line; the selftest sets it so its lines cannot be read as real actions.
LOG_PREFIX = ""

#: `state` values that mean the session has FINISHED. Everything else that is still listed with a
#: live pid is up.
#:
#: Liveness is read from `pid` and `state`, NOT from `status`: a session that has run and exited
#: can still report `status: idle` - the same value a healthy waiting session
#: reports - while `state` read `done`. A supervisor keyed on `status` would have called every dead
#: session healthy and never resumed anything, which is the failure it exists to prevent, wearing
#: the shape of a clean log.
#:
#: The listing has TWO row shapes and only `pid` and `startedAt` appear in both. Background
#: sessions (`kind: background`) carry `id`, `state` and `pid`; the socket-attached ones carry
#: `sessionId` and `pid` and no `state` at all. So `pid` is the primary test and `state` refines it
#: when present.
FINISHED_STATES = frozenset({"done", "exited", "stopped"})
#: `state` values that do not make a same-name session a live twin, on top of FINISHED_STATES.
TWIN_IGNORED_STATES = frozenset({"failed"})
#: The suffix the CLI appends to disambiguate sessions that share a name: `main (2)`.
DUPLICATE_SUFFIX = re.compile(r" \(\d+\)$")


def is_alive(row: dict) -> tuple[bool, str]:
    """(alive, why) for one listing row."""
    state = row.get("state")
    if state in FINISHED_STATES:
        return False, f"state {state!r}"
    pid = row.get("pid")
    if pid:
        try:
            up = oscompat.pid_alive(int(pid))
        except ValueError:
            up = False
        if not up:
            return False, f"pid {pid} is gone"
        return True, f"pid {pid} up" + (f", state {state!r}" if state else "")
    # No pid to check and not finished: listed is the only evidence there is. Say so rather than
    # implying a check happened.
    return True, f"listed, no pid to verify (state {state!r})"


def log(message: str) -> None:
    print(f"{LOG_PREFIX}{_dt.datetime.now().isoformat(timespec='seconds')}  {message}", flush=True)


def log_action(action: str, sid: str, why: str) -> None:
    log(f"  {action:<7} {sid[:8]}  {why}")


def live_sessions() -> dict[str, dict]:
    """Session id -> row, for every session the CLI currently lists."""
    out = subprocess.run([str(CLAUDE), "agents", "--json"],
                         capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)
    if out.returncode != 0:
        raise RuntimeError(f"claude agents --json exited {out.returncode}: {out.stderr[:200]}")
    rows = json.loads(out.stdout)
    rows = rows if isinstance(rows, list) else rows.get("agents") or rows.get("sessions") or []
    return {r["sessionId"]: r for r in rows if r.get("sessionId")}


def run_claude(cmd: list[str]) -> tuple[bool, str]:
    """(ok, stdout) for one CLI job command.

    A timeout, a launch error or a nonzero exit is logged and returned as not-ok, never raised: one
    failed command must not stop the rest of the tick, and in particular must not skip the rebind.
    """
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120, cwd=str(REPO))
    except subprocess.TimeoutExpired:
        log("          FAILED timeout")
        return False, ""
    except OSError as exc:
        log(f"          FAILED {exc}")
        return False, ""
    if out.returncode != 0:
        log(f"          FAILED rc={out.returncode} {out.stderr.strip()[:160]}")
        return False, out.stdout
    log(f"          ok {out.stdout.strip()[:160]}")
    return True, out.stdout


def base_name(name: str) -> str:
    """The session name without the CLI's duplicate suffix."""
    return DUPLICATE_SUFFIX.sub("", name)


def same_name(a: str | None, b: str | None) -> bool:
    """Both named, and equal once the CLI's duplicate suffix is dropped from each."""
    return bool(a and b) and base_name(a) == base_name(b)


def live_twins(sid: str, name: str | None, live: dict[str, dict]) -> list[str]:
    """Short ids of the OTHER live sessions carrying `name`, in listing order."""
    return [row.get("id") or short_of(other)
            for other, row in live.items()
            if other != sid and same_name(row.get("name"), name)
            and row.get("state") not in TWIN_IGNORED_STATES and is_alive(row)[0]]


def decide(configured: list[dict],
           live: dict[str, dict]) -> tuple[list[tuple[str, str, str]], list[dict]]:
    """(decisions, stale pins): one (action, session_id, why) per configured session, and one
    {name, pinned, twin} per `stale` decision.

    Pure: takes the listing, returns decisions, touches nothing. That is what makes `--selftest`
    able to score it against fixtures instead of against a live machine.
    """
    decisions: list[tuple[str, str, str]] = []
    stale: list[dict] = []
    for entry in configured:
        sid, name = entry["id"], entry.get("name")
        label = name or "?"
        row = live.get(sid)
        alive, why = is_alive(row) if row is not None else (False, "absent from the listing")
        if alive:
            decisions.append(("leave", sid, f"{label}: {why}"))
            continue
        # Down, but another live session carries the name: the pin is stale, not dead. Resuming
        # would run a second session under that name, and choosing which of them is the real one
        # is the stranger problem above - so refuse and leave it to a human.
        twins = live_twins(sid, name, live)
        if twins:
            decisions.append(("stale", sid, f"{label}: live as {', '.join(twins)}; "
                                            f"pinned id stale, not resuming - repin by hand"))
            stale.append({"name": label, "pinned": short_of(sid), "twin": ", ".join(twins)})
            continue
        decisions.append(("resume", sid, f"{label}: {why}"))
    return decisions, stale


def stale_file(config_path: pathlib.Path) -> pathlib.Path:
    """STALE for the live config; `<STALE stem>.<config stem>.json` beside STALE for any other, so
    a run against a throwaway config can neither raise nor clear the live alert, and never leaves
    a file in the directory that holds the config."""
    if config_path.resolve() == CONFIG.resolve():
        return STALE
    return STALE.with_name(f"{STALE.stem}.{config_path.stem}.json")


def record_stale(entries: list[dict], path: pathlib.Path) -> None:
    """Write `path` when this tick found stale pins; remove it when it found none."""
    try:
        if entries:
            path.write_text(json.dumps({
                "at": _dt.datetime.now().isoformat(timespec="seconds"),
                "entries": entries}, indent=2) + "\n", encoding="utf-8")
        else:
            path.unlink(missing_ok=True)
    except OSError as exc:
        log(f"  could not update {path}: {exc}")


def copies_to_kill(before: dict[str, dict], after: dict[str, dict], resumed: list[str],
                   produced: set[str] | frozenset[str] = frozenset()) -> list[tuple[str, str, str]]:
    """Sessions that appeared DURING this tick, next to one we resumed, sharing its name.

    This is the whole dedupe rule. It compares the listing before the resume with the listing
    after, so a session the supervisor did not create can never be a candidate - which is what
    stops it killing a stranger that happens to share a name. `produced` holds the short ids the
    resumes reported starting: those are the sessions this tick meant to create, never copies.
    """
    kills = []
    for sid in resumed:
        name = (before.get(sid) or after.get(sid) or {}).get("name")
        for new_sid, row in after.items():
            if new_sid not in before and new_sid != sid and same_name(row.get("name"), name) \
                    and (row.get("id") or short_of(new_sid)) not in produced \
                    and is_alive(row)[0]:
                kills.append(("kill", new_sid, f"{name}: copy made by this tick's resume of "
                                               f"{sid[:8]}"))
    return kills


def short_of(session_id: str) -> str:
    """The 8-character form the job commands take."""
    return session_id.split("-")[0]


def confirm(decisions, dry_run: bool):
    """Hold a `resume` until the session has looked dead on CONFIRM_TICKS ticks in a row.

    The strike count lives outside the process because each tick is a new one. A session that comes
    back alive clears its own strikes, so a busy spell never accumulates toward a restart.
    """
    try:
        strikes = json.loads(STRIKES.read_text(encoding="utf-8")) if STRIKES.exists() else {}
    except (OSError, ValueError):
        strikes = {}
    held, out = [], []
    for action, sid, why in decisions:
        if action != "resume":
            if strikes.pop(sid, None):
                log(f"  {sid[:8]} is back; strikes cleared")
            out.append((action, sid, why))
            continue
        strikes[sid] = strikes.get(sid, 0) + 1
        if strikes[sid] < CONFIRM_TICKS:
            held.append((sid, why, strikes[sid]))
        else:
            out.append((action, sid, f"{why}; dead on {strikes[sid]} consecutive ticks"))
    for sid, why, n in held:
        log_action("hold", sid, f"{why}; strike {n} of {CONFIRM_TICKS}, not resuming yet")
    if not dry_run:
        try:
            STRIKES.write_text(json.dumps(strikes), encoding="utf-8")
        except OSError as exc:
            log(f"  could not persist strikes: {exc}")
    return out


def apply(decisions, dry_run: bool, *, before: dict[str, dict],
          config_path: pathlib.Path) -> int:
    """Run the decisions; the number of commands that failed.

    `before` is the listing the decisions were made on; `config_path` is the config the tick read,
    and the only one a rebind writes.
    """
    wake = WAKE.read_text(encoding="utf-8")
    failures = 0
    resumed: list[str] = []
    rebinds: list[tuple[str, str]] = []
    for action, sid, why in decisions:
        # Only `resume` runs a command here. `leave`, `stale` and any action this function does not
        # know are logged and left; copies are killed below, after the re-list.
        if action != "resume":
            log_action(action, sid, why)
            continue
        log_action(f"WOULD {action}" if dry_run else action, sid, why)
        if dry_run:
            continue
        # `--resume` takes the full session id.
        ok, stdout = run_claude([str(CLAUDE), "--bg", "--resume", sid, wake])
        if not ok:
            failures += 1
            continue
        resumed.append(sid)
        message, pair = resume_followup(sid, stdout)
        log(message)
        if pair:
            rebinds.append(pair)

    # Re-list ONLY if something was resumed. A tick that changed nothing has nothing to dedupe,
    # and re-listing anyway would invite a decision on a listing nobody acted against.
    if not resumed:
        return failures
    try:
        after = live_sessions()
    except Exception as exc:  # noqa: BLE001
        log(f"  could not re-list to check for copies, none killed: {exc}")
        after = None
    if after is not None:
        produced = {new for _old, new in rebinds}
        for action, sid, why in copies_to_kill(before, after, resumed, produced):
            log_action(action, sid, why)
            # `claude rm` takes the SHORT id and rejects a full uuid ("No job matching ...").
            ok, _stdout = run_claude([str(CLAUDE), "rm", short_of(sid)])
            failures += not ok
    if rebinds:
        rebind(rebinds, config_path, after)
    return failures


def _new_id_from(stdout: str) -> str | None:
    """The short id the CLI prints as `backgrounded . <id>`, with the colour codes stripped."""
    plain = re.sub(r"\x1b\[[0-9;]*m", "", stdout)
    found = re.search(r"backgrounded\s*\S?\s*([0-9a-f]{8})\b", plain)
    return found.group(1) if found else None


def resume_followup(sid: str, stdout: str) -> tuple[str, tuple[str, str] | None]:
    """(log line, rebind pair or None) for one successful resume. Pure, so `--selftest` can score
    what the log says about a resume without running one."""
    new = _new_id_from(stdout)
    if new and new == short_of(sid):
        return (f"          resumed under the same id {new}; config unchanged", None)
    if new:
        return (f"          resumed as {new} - NOT the same id; config will be updated",
                (sid, new))
    return ("          WARNING could not read the new id from the resume output; the config still "
            "points at the old one and the next tick will resume a corpse", None)


def rebind(pairs: list[tuple[str, str]], config_path: pathlib.Path,
           after: dict[str, dict] | None) -> None:
    """Point `config_path` at the ids the resumes actually produced.

    Resolves each short id back to a full session id through `after`, the listing taken after the
    resumes, because `--resume` needs the full form while `rm`/`stop` need the short one. Lists
    once more only when the caller has no such listing.
    """
    if after is None:
        try:
            after = live_sessions()
        except Exception as exc:  # noqa: BLE001
            log(f"  rebind FAILED, no listing to resolve {', '.join(n for _o, n in pairs)}: {exc}; "
                f"{config_path.name} keeps the old id and the next tick will resume a corpse")
            return
    short_to_full = {str(r.get("id")): sid for sid, r in after.items() if r.get("id")}
    data = json.loads(config_path.read_text(encoding="utf-8"))
    if rebind_entries(data, pairs, short_to_full):
        config_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        log(f"  rebind written to {config_path}")


def rebind_entries(data: dict, pairs: list[tuple[str, str]],
                   short_to_full: dict[str, str]) -> int:
    """Apply the rebinds to the parsed config in place; the number of entries changed. Pure apart
    from the log, so `--selftest` can score it without the live config or listing."""
    changed = 0
    for old, new_short in pairs:
        full = short_to_full.get(new_short)
        if not full:
            log(f"  rebind FAILED for {old[:8]}: {new_short} is not in the listing")
            continue
        if full == old:
            continue
        for entry in data["sessions"]:
            if entry["id"] == old:
                entry.setdefault("previous_ids", []).append(old)
                entry["id"] = full
                entry["rebound_at"] = _dt.datetime.now().isoformat(timespec="seconds")
                changed += 1
                log(f"  rebind {old[:8]} -> {full[:8]}")
    return changed


def take_lock() -> bool:
    """A lock FILE outlives the process that wrote it, so the pid inside it is what answers."""
    if LOCK.exists():
        held = LOCK.read_text(encoding="utf-8").strip()
        if held.isdigit():
            if oscompat.pid_alive(int(held)):
                log(f"a supervisor tick is already running as pid {held}; exiting")
                return False
            log(f"reclaiming stale lock from pid {held}")
    LOCK.parent.mkdir(parents=True, exist_ok=True)
    LOCK.write_text(str(os.getpid()), encoding="utf-8")
    return True


#: `[board] keep_up`: this tick also keeps the Bob's board serving, so a reboot or a crash does not
#: leave the page down until someone starts it by hand.
KEEP_BOARD = bool((_CFG.get("board") or {}).get("keep_up"))
BOARD_PORT = bobcfg.get(_CFG, "board.port")
BOARD_CMD = [sys.executable, str(HERE / "board_shell.py"), "--quiet"]
#: The board's own output, in the Bob's state; never the agent log, which the watchdog parses.
BOARD_LOG = bobcfg.state_home() / "board.log"
BOARD_WAIT_S = 15


def port_answers(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=1):
            return True
    except OSError:
        return False


def board_action(keep_up: bool, up: bool) -> str | None:
    """'start' for a kept-up board whose port is silent, 'leave' for one that answers, None for a
    Bob that does not keep its board up. The port, not a pid file, decides: a board a person
    started by hand counts as up, so the tick never starts a second one on the same port."""
    if not keep_up:
        return None
    return "leave" if up else "start"


def keep_board_up(dry_run: bool, *, answers=port_answers, spawn=oscompat.spawn_detached,
                  sleep=time.sleep) -> None:
    """Start the board when it should be up and is not, then wait for its port.

    One start per tick, never a retry loop: a board that crashes at start would otherwise be
    spawned again every few seconds. The next tick tries again and the log says why it failed."""
    if not KEEP_BOARD:
        return
    action = board_action(KEEP_BOARD, answers(BOARD_PORT))
    if action == "leave":
        log(f"  leave   board    :{BOARD_PORT} answers")
        return
    if dry_run:
        log(f"  start   board    :{BOARD_PORT} silent; dry run, not starting")
        return
    pid = spawn(BOARD_CMD, REPO, BOARD_LOG)
    for _ in range(int(BOARD_WAIT_S / 0.5)):
        sleep(0.5)
        if answers(BOARD_PORT):
            log(f"  start   board    pid {pid}, :{BOARD_PORT} answers")
            return
    log(f"  FAILED  board    pid {pid} started, :{BOARD_PORT} silent after {BOARD_WAIT_S} s; see {BOARD_LOG}")


def keep_board_up_safely(dry_run: bool) -> None:
    """Fails open: keeping sessions alive is this job, and a board that cannot start must never
    turn a good tick into a failed one."""
    try:
        keep_board_up(dry_run)
    except Exception as exc:  # noqa: BLE001
        log(f"  FAILED  board    {type(exc).__name__}: {exc}")


def load_env_file() -> None:
    """Put `[runtime] env_file` into this process's environment, so every session it resumes
    inherits the Bob's keys the way a session started by launch.sh does.

    Fails open: a bad or missing file is logged and the tick goes on. Keeping sessions alive is
    this job; a session without its keys still runs its other loops and reports the failed reads.
    """
    try:
        envfile.load_into(os.environ)
    except envfile.EnvFileError as exc:
        for err in exc.errors:
            log(f"WARN env_file: {err}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--config", help="alternate session list, for proving a resume against a "
                                     "throwaway session instead of the live ones")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args(argv)
    if args.selftest:
        return selftest()

    log(f"=== supervisor tick (dry-run={args.dry_run}) ===")
    load_env_file()
    if not args.dry_run and not take_lock():
        return 0
    try:
        config_path = pathlib.Path(args.config) if args.config else CONFIG
        configured = json.loads(config_path.read_text(encoding="utf-8"))["sessions"]
        live = live_sessions()
        log(f"{len(live)} session(s) listed, {len(configured)} supervised")
        decisions, stale = decide(configured, live)
        decisions = confirm(decisions, args.dry_run)
        if not args.dry_run:
            record_stale(stale, stale_file(config_path))
        failures = apply(decisions, args.dry_run, before=live, config_path=config_path)
        if not args.config:
            keep_board_up_safely(args.dry_run)
        return 1 if failures else 0
    except Exception as exc:  # noqa: BLE001 - a tick that dies loudly beats one that dies quietly
        log(f"FAILED: {type(exc).__name__}: {exc}")
        return 1
    finally:
        if not args.dry_run and LOCK.exists() and LOCK.read_text(encoding="utf-8").strip() == str(os.getpid()):
            LOCK.unlink()


def selftest() -> int:
    """The decisions, against fixtures. A supervisor that cannot be scored offline gets tested in
    production, which for this one means finding out at 3am that it killed the wrong session.

    Every module path the tick writes is pointed into a temporary directory for the whole run, and
    `subprocess.run` refuses unless a case swaps in its own fake, so no case can reach a live file
    or launch a real command.
    """
    import contextlib
    import io
    import tempfile
    from unittest import mock

    global LOG_PREFIX
    LOG_PREFIX = "[selftest] "
    bad = 0
    checks = 0

    def check(ok: bool, label: str, detail: str = "") -> None:
        nonlocal bad, checks
        checks += 1
        if not ok:
            print(f"  FAIL  {label}" + (f"\n        {detail}" if detail else ""))
            bad += 1

    @contextlib.contextmanager
    def scenario(label: str):
        """A case that crashes is a failed check, not a crashed selftest."""
        try:
            yield
        except Exception as exc:  # noqa: BLE001
            check(False, label, f"raised {type(exc).__name__}: {exc}")

    def swap(**values):
        """Module globals replaced for one block, restored on exit whatever happens."""
        return mock.patch.dict(globals(), values)

    def refuse(cmd, **_kw):
        raise RuntimeError(f"selftest reached a real command: {cmd[:3]}")


    pid, dead_pid = os.getpid(), 999999
    main_full = "f62d1198-6fec-434e-b2fa-b2d1ff4519da"
    old_full = "f1a39308-1078-4553-b287-9db7240518c2"
    new_full = "0d7a0b14-54d9-4d22-bcfe-15d219382a26"
    copy_full = "c0ffee00-0000-0000-0000-000000000000"

    def row(sid, name=None, state="blocked", pid=pid, **extra):
        """One listing row; `state=None` or `pid=None` leaves the key out, as the CLI does."""
        fields = {"sessionId": sid, "name": name, "state": state, "pid": pid, **extra}
        return {k: v for k, v in fields.items() if v is not None}

    def listing(*rows):
        return {r["sessionId"]: r for r in rows}

    def ids(config_path):
        return json.loads(config_path.read_text(encoding="utf-8"))["sessions"][0]["id"]

    cfg = [{"id": "aaa", "name": "main"}, {"id": "bbb", "name": "worker"}]
    worker = row("bbb", "worker")
    interactive_main = row(main_full, "main", state=None, status="busy")
    bg_main = row(new_full, "main", pid=None, id="0d7a0b14")
    stale_listing = listing(interactive_main, worker)
    old_row = row(old_full, "main", state="done", id="f1a39308")
    new_row = row(new_full, "main", state="working", id="0d7a0b14")
    copy_row = row(copy_full, "main", state="working", id="c0ffee00")

    cases = [
        ("both alive -> leave both",
         listing(row("aaa", "main"), row("bbb", "worker", state="working")),
         [("leave", "aaa"), ("leave", "bbb")]),
        ("one absent -> resume only that one",
         listing(row("aaa", "main")), [("leave", "aaa"), ("resume", "bbb")]),
        ("both absent -> resume both",
         {}, [("resume", "aaa"), ("resume", "bbb")]),
        ("a finished session counts as down",
         listing(row("aaa", "main", state="done"), worker),
         [("resume", "aaa"), ("leave", "bbb")]),
        ("a live pid with no state is treated as up",
         listing(row("aaa", "main", state=None), worker),
         [("leave", "aaa"), ("leave", "bbb")]),
        ("a dead pid counts as down",
         listing(row("aaa", "main", pid=dead_pid), worker),
         [("resume", "aaa"), ("leave", "bbb")]),
        ("a pre-existing session sharing a name is NOT touched",
         listing(row("aaa", "main"), worker, row("ccc", "worker")),
         [("leave", "aaa"), ("leave", "bbb")]),
        # A pinned id that is absent while ANOTHER live session carries its name is a stale pin,
        # not a death: resuming it starts a second session under that name. Both listing shapes.
        ("pinned id absent, name live as an interactive session -> stale, not resumed",
         stale_listing, [("stale", "aaa"), ("leave", "bbb")]),
        ("pinned id absent, name live as a background session -> stale, not resumed",
         listing(bg_main, worker), [("stale", "aaa"), ("leave", "bbb")]),
        ("pinned id absent, same-name session FINISHED -> still resumed",
         listing(row("xxx", "main", state="done"), worker),
         [("resume", "aaa"), ("leave", "bbb")]),
        # A pinned row that is still LISTED but finished or dead is the same stale pin as an absent
        # one when its name is live elsewhere.
        ("pinned id listed FINISHED, name live elsewhere -> stale, not resumed",
         listing(row("aaa", "main", state="done"), interactive_main, worker),
         [("stale", "aaa"), ("leave", "bbb")]),
        ("pinned id listed with a dead pid, name live elsewhere -> stale, not resumed",
         listing(row("aaa", "main", pid=dead_pid), interactive_main, worker),
         [("stale", "aaa"), ("leave", "bbb")]),
        # The CLI disambiguates same-name sessions as `name (2)`, `name (3)`.
        ("name live under the CLI duplicate suffix -> stale, not resumed",
         listing(row("ggg", "main (2)"), worker), [("stale", "aaa"), ("leave", "bbb")]),
        ("a longer name is not a twin -> still resumed",
         listing(row("hhh", "main_old"), worker), [("resume", "aaa"), ("leave", "bbb")]),
        # A failed session cannot be doing the work, so it must not hold the real one down.
        ("a FAILED same-name session with a live pid does not block the resume",
         listing(row("iii", "main", state="failed"), worker),
         [("resume", "aaa"), ("leave", "bbb")]),
        ("two live twins -> stale, not resumed",
         listing(interactive_main, row(new_full, "main (2)", id="0d7a0b14"), worker),
         [("stale", "aaa"), ("leave", "bbb")]),
        ("a same-name session with a dead pid does not block the resume",
         listing(row("jjj", "main", pid=dead_pid), worker),
         [("resume", "aaa"), ("leave", "bbb")]),
        ("a live row with no name is not a twin",
         listing(row("kkk", state="working"), worker), [("resume", "aaa"), ("leave", "bbb")]),
        ("pinned id absent, name live as a background session carrying a pid -> stale",
         listing(row(new_full, "main", id="0d7a0b14", kind="background"), worker),
         [("stale", "aaa"), ("leave", "bbb")]),
    ]

    def fake_cli(listings, rm="ok", calls=None, resume="ok"):
        """subprocess.run stand-in. `listings` holds one entry per `agents --json` call: rows, or
        None for a call that fails; calls past the end fail too. Every resume reports 0d7a0b14
        unless `resume="oserror"`, which fails to launch; `rm` is ok, fail, timeout or oserror."""
        remaining = iter(listings)

        def run(cmd, **_kw):
            if calls is not None:
                calls.append(cmd)
            if cmd[1:3] == ["agents", "--json"]:
                rows = next(remaining, None)
                if rows is None:
                    return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="listing failed")
                return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(rows), stderr="")
            if "--resume" in cmd:
                if resume == "oserror":
                    raise FileNotFoundError(2, "No such file or directory", cmd[0])
                return subprocess.CompletedProcess(
                    cmd, 0, stdout="backgrounded · 0d7a0b14 · re-register cron loops", stderr="")
            if rm == "timeout":
                raise subprocess.TimeoutExpired(cmd, 120)
            if rm == "oserror":
                raise PermissionError(13, "Permission denied", cmd[0])
            return subprocess.CompletedProcess(cmd, 0 if rm == "ok" else 1, stdout="",
                                               stderr="" if rm == "ok" else "rm failed")
        return run

    with tempfile.TemporaryDirectory() as tmp_dir, \
            swap(STALE=pathlib.Path(tmp_dir) / "stale.json",
                 STRIKES=pathlib.Path(tmp_dir) / "strikes.json",
                 LOCK=pathlib.Path(tmp_dir) / "lock",
                 CONFIG=pathlib.Path(tmp_dir) / "live-sessions.json",
                 WAKE=pathlib.Path(tmp_dir) / "wake-prompt.md"), \
            mock.patch("subprocess.run", refuse):
        tmp = pathlib.Path(tmp_dir)
        WAKE.write_text("selftest wake prompt\n", encoding="utf-8")

        # The stale-pin line has to name every live session, or a human cannot repin from the log.
        with scenario("stale-pin reason"):
            why = [w for _a, s, w in decide(cfg, stale_listing)[0] if s == "aaa"]
            check(bool(why) and "live as f62d1198" in why[0] and "not resuming" in why[0],
                  "stale-pin reason does not name the live session", f"got  {why}")
            two = listing(interactive_main, row(new_full, "main (2)", id="0d7a0b14"), worker)
            why = [w for _a, s, w in decide(cfg, two)[0] if s == "aaa"]
            check(bool(why) and "f62d1198" in why[0] and "0d7a0b14" in why[0],
                  "stale-pin reason with two twins does not name both", f"got  {why}")
            # The suffix is stripped on the configured side too.
            got = [(a, s) for a, s, _w in decide([{"id": "aaa", "name": "main (3)"}],
                                                 stale_listing)[0]]
            check(got == [("stale", "aaa")],
                  "a configured name carrying a duplicate suffix does not match its live twin",
                  f"got  {got}")
            _decisions, stale = decide(cfg, stale_listing)
            check(stale == [{"name": "main", "pinned": "aaa", "twin": "f62d1198"}],
                  "decide() does not report the stale pin", f"got  {stale}")

        # Only `resume` may reach a command from the decision loop; everything else logs.
        for action in ("stale", "no-such-action", "kill"):
            with scenario(f"a {action!r} decision"):
                calls: list = []
                with mock.patch("subprocess.run", fake_cli([], calls=calls)):
                    apply([(action, "aaa", "main: fixture")], dry_run=False, before={},
                          config_path=tmp / "unused.json")
                check(not calls, f"a {action!r} decision ran a command",
                      f"got  {[c[:4] for c in calls]}")

        # A dry run logs the resume it would make and runs nothing: every `--dry-run` on the live
        # machine depends on this.
        with scenario("a dry-run resume"):
            calls = []
            with mock.patch("subprocess.run", fake_cli([[old_row, new_row]], calls=calls)), \
                    contextlib.redirect_stdout(io.StringIO()):
                failures = apply([("resume", old_full, "main: fixture")], dry_run=True,
                                 before=listing(old_row), config_path=tmp / "unused.json")
            check(not calls and failures == 0, "a dry-run resume ran a command",
                  f"got  {[c[:4] for c in calls]} failures={failures}")

        def resume_tick(listings, rm="ok", resume="ok"):
            """One resume of the pinned `done` row through apply(), against a tick config that is
            not the live one. The resume reports 0d7a0b14."""
            calls: list = []
            tick_cfg, live_cfg = tmp / "tick-sessions.json", tmp / "live-sessions.json"
            for path in (tick_cfg, live_cfg):
                path.write_text(json.dumps({"sessions": [{"id": old_full, "name": "main"}]}), encoding="utf-8")
            buf = io.StringIO()
            with mock.patch("subprocess.run", fake_cli(listings, rm, calls, resume)), \
                    contextlib.redirect_stdout(buf):
                failures = apply([("resume", old_full, "main: state 'done'")], dry_run=False,
                                 before=listing(old_row), config_path=tick_cfg)
            return {"calls": calls, "failures": failures, "log": buf.getvalue(),
                    "id": ids(tick_cfg), "live_id": ids(live_cfg),
                    "listings": sum(c[1:3] == ["agents", "--json"] for c in calls),
                    "rms": [c[2] for c in calls if c[1:2] == ["rm"]]}

        # The session this tick resumed is never killed as a copy of itself; the rebind uses the
        # listing apply() already took, and writes the tick's config, not the live one.
        with scenario("resume under a new id"):
            got = resume_tick([[old_row, new_row]])
            check(not got["rms"], "the session this tick resumed was killed as a copy",
                  f"got  {got['rms']}")
            check(got["id"] == new_full, "the config was not rebound to the resumed session",
                  f"got  {got['id'][:8]}")
            check(got["listings"] == 1,
                  "a resume re-listed for the rebind although apply() already had the listing",
                  f"listings={got['listings']}")
            check(got["live_id"] == old_full, "the rebind wrote the live config, not the tick's",
                  f"live config id={got['live_id'][:8]}")

        # A copy rm that times out or fails, or a post-resume listing that fails, costs the dedupe
        # and nothing else: the rebind still lands.
        three = [old_row, new_row, copy_row]
        for label, listings, rm in (("a timed-out copy rm", [three], "timeout"),
                                    ("a failed copy rm", [three], "fail"),
                                    ("a copy rm that cannot launch", [three], "oserror")):
            with scenario(label):
                got = resume_tick(listings, rm)
                check(got["rms"] == ["c0ffee00"] and got["id"] == new_full
                      and got["failures"] == 1, f"{label} skipped the rebind",
                      f"rms={got['rms']} id={got['id'][:8]} failures={got['failures']}")
        # A resume that cannot launch is one failure, not a raise; there is nothing to rebind to.
        with scenario("a resume that cannot launch"):
            got = resume_tick([three], resume="oserror")
            check(got["failures"] == 1 and got["id"] == old_full and got["listings"] == 0,
                  "a resume that cannot launch was not counted as one failure",
                  f"failures={got['failures']} id={got['id'][:8]} listings={got['listings']}")
        with scenario("a failed post-resume listing"):
            got = resume_tick([None, [old_row, new_row]])
            check(got["id"] == new_full, "a failed post-resume listing skipped the rebind",
                  f"id={got['id'][:8]}")
        with scenario("no listing at all"):
            got = resume_tick([])
            check("rebind FAILED" in got["log"] and got["id"] == old_full,
                  "no listing at all: the rebind failure is not logged",
                  f"id={got['id'][:8]} log tail={got['log'][-200:]!r}")

        # What the log says after a resume. The CLI prints the SHORT id; the config holds the FULL.
        followup_cases = [
            ("a resume under the same id is not reported as a new id, and rebinds nothing",
             "backgrounded · f1a39308 · re-register cron loops", False),
            ("a resume under a different id is reported and rebinds",
             "backgrounded · 0d7a0b14 · re-register cron loops", True),
        ]
        for label, stdout, want_new in followup_cases:
            with scenario(label):
                message, pair = resume_followup(old_full, stdout)
                check(("NOT the same id" in message) == want_new and bool(pair) == want_new,
                      label, f"got  {message.strip()!r} pair={pair}")

        # The rebind itself, on a parsed config rather than the live file.
        rebind_cases = [
            ("a rebind to the same id leaves previous_ids alone",
             [(old_full, "f1a39308")], {"f1a39308": old_full}, old_full, []),
            ("a rebind to a new id records the old one once",
             [(old_full, "0d7a0b14")], {"0d7a0b14": new_full}, new_full, [old_full]),
        ]
        for label, pairs, short_to_full, want_id, want_prev in rebind_cases:
            with scenario(label):
                data = {"sessions": [{"id": old_full, "name": "main", "previous_ids": []}]}
                rebind_entries(data, pairs, short_to_full)
                entry = data["sessions"][0]
                check(entry["id"] == want_id and entry["previous_ids"] == want_prev, label,
                      f"want id={want_id[:8]} previous_ids={want_prev}\n"
                      f"        got  id={entry['id'][:8]} previous_ids={entry['previous_ids']}")

        # The dedupe seam, scored on before/after pairs rather than on one listing.
        new_worker = row("ddd", "worker")
        stranger = row("ccc", "worker")
        dedupe_cases = [
            ("a copy that appeared beside our resume is killed",
             listing(worker), listing(worker, new_worker), ["bbb"], set(), ["ddd"]),
            ("a stranger that was there BEFORE is left alone",
             listing(worker, stranger), listing(worker, stranger), ["bbb"], set(), []),
            ("a new session with a DIFFERENT name is left alone",
             listing(worker), listing(worker, row("eee", "other_agent")), ["bbb"], set(), []),
            ("nothing resumed means nothing killed",
             {}, listing(row("fff", "worker")), [], set(), []),
            ("a session the resume reported producing is not a copy",
             listing(old_row), listing(old_row, new_row), [old_full], {"0d7a0b14"}, []),
            ("a copy listed under the CLI duplicate suffix is killed",
             listing(old_row), listing(old_row, dict(copy_row, name="main (2)")),
             [old_full], set(), [copy_full]),
            ("the produced session is not killed under the CLI duplicate suffix either",
             listing(old_row), listing(old_row, dict(new_row, name="main (2)")),
             [old_full], {"0d7a0b14"}, []),
        ]
        for label, before, after, resumed, produced, want in dedupe_cases:
            with scenario(label):
                got = [s for _a, s, _w in copies_to_kill(before, after, resumed, produced)]
                check(got == want, label, f"want {want}\n        got  {got}")

        # The confirmation gate, which is what stops a busy session being restarted as a dead one.
        with scenario("confirmation gate"), swap(STRIKES=tmp / "strikes-a.json"):
            first = confirm([("resume", "aaa", "absent")], dry_run=False)
            check(not first, "a resume fired on the FIRST tick", f"got  {first}")
            second = confirm([("resume", "aaa", "absent")], dry_run=False)
            check([a for a, _s, _w in second] == ["resume"],
                  f"a resume did not fire on tick {CONFIRM_TICKS}", f"got  {second}")
        with scenario("strikes cleared"), swap(STRIKES=tmp / "strikes-b.json"):
            confirm([("resume", "bbb", "absent")], dry_run=False)
            confirm([("leave", "bbb", "back up")], dry_run=False)
            again = confirm([("resume", "bbb", "absent")], dry_run=False)
            check(not again, "strikes were not cleared when the session came back",
                  f"got  {again}")

        # The stale file, through main(): written or removed by a real tick on the live config,
        # never by a dry run, and never the live file for a tick given another config.
        live_stale = tmp / "stale.json"
        alt = tmp / "alt-sessions.json"
        alt_stale = tmp / "stale.alt-sessions.json"
        leave_listing = listing(row("aaa", "main"))

        def tick(argv, rows, calls=None):
            one = {"sessions": [{"id": "aaa", "name": "main"}]}
            for path in (CONFIG, alt):
                path.write_text(json.dumps(one), encoding="utf-8")
            with mock.patch("subprocess.run", fake_cli([list(rows.values())], calls=calls)):
                main(argv)

        def state(path):
            return path.read_text(encoding="utf-8") if path.exists() else None

        with scenario("stale file, live config"):
            live_stale.unlink(missing_ok=True)
            tick([], stale_listing)
            written = json.loads(state(live_stale) or "null")
            check(bool(written) and "at" in written and written.get("entries") == [
                {"name": "main", "pinned": "aaa", "twin": "f62d1198"}],
                  "a tick that decided stale did not write the stale file", f"got  {written}")
            tick([], leave_listing)
            check(not live_stale.exists(), "a tick with no stale decision left the stale file")
        with scenario("stale file, dry run"):
            for argv, stale_path in ((["--dry-run"], live_stale),
                                     (["--dry-run", "--config", str(alt)], alt_stale)):
                stale_path.unlink(missing_ok=True)
                tick(argv, stale_listing)
                check(not stale_path.exists(), f"{argv} wrote {stale_path.name}")
                stale_path.write_text("sentinel", encoding="utf-8")
                tick(argv, leave_listing)
                check(state(stale_path) == "sentinel", f"{argv} removed {stale_path.name}")
                stale_path.unlink(missing_ok=True)
        with scenario("stale file, alternate config"):
            live_stale.write_text("sentinel", encoding="utf-8")
            tick(["--config", str(alt)], stale_listing)
            check(state(live_stale) == "sentinel",
                  "a tick on an alternate config wrote the live stale file")
            check(alt_stale.exists(),
                  "a tick on an alternate config wrote no stale file of its own")
            tick(["--config", str(alt)], leave_listing)
            check(state(live_stale) == "sentinel",
                  "a tick on an alternate config removed the live stale file")
            check(not alt_stale.exists(), "a tick on an alternate config left its own stale file")
        with scenario("stale file for a config inside the repo"):
            got = stale_file(HERE / "other-sessions.json")
            check(got.parent == STALE.parent and not got.is_relative_to(REPO),
                  "an alternate config inside the repo puts its stale file in the repo",
                  f"got  {got}")

        # A dry run reads the strikes and writes none: a persisted dry-run strike would let the
        # next real tick resume on its first observation. With one strike already recorded the
        # dry run reaches a resume decision, and still runs nothing but the listing.
        with scenario("strikes file, dry run"), swap(STRIKES=tmp / "strikes-dry.json"):
            for seed in ('{"aaa": 1}', None):
                STRIKES.unlink(missing_ok=True)
                if seed is not None:
                    STRIKES.write_text(seed, encoding="utf-8")
                    os.utime(STRIKES, ns=(10**18, 10**18))
                calls = []
                tick(["--dry-run"], {}, calls)
                if seed is None:
                    check(not STRIKES.exists(), "a dry-run tick created the strikes file")
                else:
                    check(STRIKES.read_text(encoding="utf-8") == seed
                          and STRIKES.stat().st_mtime_ns == 10**18,
                          "a dry-run tick wrote the strikes file",
                          f"got  {STRIKES.read_text(encoding="utf-8")!r} mtime_ns={STRIKES.stat().st_mtime_ns}")
                check(len(calls) == 1, "a dry-run tick ran more than the listing",
                      f"got  {[c[1:4] for c in calls]}")

        for label, live, want in cases:
            with scenario(label):
                got = [(a, s) for a, s, _why in decide(cfg, live)[0]]
                check(got == want, label, f"want {want}\n        got  {got}")

        with scenario("board decisions"):
            check(board_action(False, False) is None, "a Bob without keep_up had its board started")
            check(board_action(True, True) == "leave", "an answering board was not left alone")
            check(board_action(True, False) == "start", "a silent board was not started")

        def board_tick(keep: bool, up_after: list[bool], dry_run: bool = False) -> list:
            """One keep_board_up call; `up_after` is what each port probe answers, in order."""
            spawned, probes = [], iter(up_after)
            with swap(KEEP_BOARD=keep):
                keep_board_up(dry_run, answers=lambda _port: next(probes),
                              spawn=lambda cmd, cwd, log_path: spawned.append(cmd) or 4242,
                              sleep=lambda _s: None)
            return spawned

        with scenario("board keep-up"):
            check(board_tick(True, [False, False, True]) == [BOARD_CMD], "a silent board was not spawned once")
            check(board_tick(True, [True]) == [], "an answering board got a second copy")
            check(board_tick(True, [False, True], dry_run=True) == [], "a dry-run tick started a board")
            check(board_tick(False, []) == [], "a Bob without keep_up probed or started a board")
            check(board_tick(True, [False] * 100) == [BOARD_CMD], "a board that never answers was spawned again")

    print(f"{checks - bad}/{checks} checks ({len(cases)} decide fixtures); {bad} problem(s)")
    return 1 if bad else 0


if __name__ == "__main__":
    oscompat.utf8_stdio()
    raise SystemExit(main())
