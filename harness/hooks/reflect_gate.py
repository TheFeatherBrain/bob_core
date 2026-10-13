#!/usr/bin/env python3
"""Stop gate: refuse to end a turn that found something and wrote nothing down.

Fires on a turn whose own text admits an error, a correction or a lesson ("I was wrong",
"correction:", "the rule is" ...). If nothing under the watched directories changed in the last
15 minutes, the turn is blocked once with the ask: write down what happened, what generalises, and
the observable that would let a future check catch it.

Watched by default: `<bob>/skills/` and the Bob's Claude Code memory directory. Override with
REFLECT_WATCH_DIRS (os.pathsep-separated). State: REFLECT_STATE, else the Bob's state home.

Capped at REFLECT_MAX blocks per session, under the harness ceiling of 8: a gate that can
deadlock gets disabled, and a disabled gate catches nothing.

Not every Bob wants this. A tutor that apologises to a learner would be blocked for it; register the
gate only where admissions really are lessons, or narrow SIGNALS.
"""
import contextlib
import fcntl
import json
import os
import pathlib
import re
import sys
import time

REFLECT_MAX = 2
#: How many sessions' counters the state file keeps; eviction is by last-block time.
REFLECT_SESSIONS_KEPT = 8
#: A write this recent under a watched directory means the lesson was recorded.
FRESH_S = 900

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import bobcfg  # noqa: E402

STATE = pathlib.Path(os.environ.get("REFLECT_STATE",
                                    str(bobcfg.state_home() / "reflect-gate-state.json")))
_REPO = bobcfg.bob_root()


def _project_dir(path: pathlib.Path) -> str:
    """Claude Code's encoding of a project directory: every non-alphanumeric becomes `-`."""
    return re.sub(r"[^a-zA-Z0-9]", "-", str(path))


_DEFAULT_WATCHED = [
    str(_REPO / "skills"),
    str(bobcfg.claude_home() / "projects" / _project_dir(_REPO) / "memory"),
]
WATCHED = [
    pathlib.Path(d)
    for d in os.environ.get("REFLECT_WATCH_DIRS", os.pathsep.join(_DEFAULT_WATCHED)).split(os.pathsep)
    if d
]

#: Admissions of something worth keeping, errors AND successes. Each alternative is narrow on
#: purpose (`my mistake`, not `mistake`), so a status line does not trip it.
SIGNALS = re.compile(
    r"\b(i was wrong|my error\b|correction to|correction:|got it backwards|i had it inverted"
    r"|caught it before|nearly (sent|published)|that is the defect"
    r"|worth a skills entry|generalises|the rule is"
    r"|my mistake\b|i overstated\b|i (gave|quoted|cited) was wrong"
    r"|i am retracting|i retract\b|i misread\b|i apologi[sz]e\b|scratch that\b"
    r"|(that|this) was incorrect\b|i got (that|it|this) wrong\b"
    r"|my earlier (claim|statement|reading|number) was (false|wrong)"
    r"|i conflated\b|disregard (the |my )?(previous|earlier|last)\b"
    r"|revised:|on reflection\b|oops\b|i need to correct\b|mea culpa\b)",
    re.I,
)


def newest_mtime() -> float | None:
    """The newest mtime under the watched roots, or None if none of them exist.

    None and 0.0 are different answers: "nothing was written" and "there is nowhere to write" must
    not look the same, or a missing directory blocks every signal-bearing turn.
    """
    newest = None
    for root in WATCHED:
        if not root.exists():
            continue
        newest = newest or 0.0
        for p in root.rglob("*"):
            if p.is_file():
                newest = max(newest, p.stat().st_mtime)
    return newest


def load_state() -> dict:
    """Every session's block count, keyed by session id. Several sessions may share the file, so
    each keeps its own counter; malformed entries are dropped rather than crashing the gate."""
    try:
        d = json.loads(STATE.read_text())
    except Exception:
        return {}
    if not isinstance(d, dict):
        return {}
    sessions = d.get("sessions")
    if not isinstance(sessions, dict):
        return {}
    return {k: v for k, v in sessions.items() if isinstance(v, dict)}


@contextlib.contextmanager
def state_lock():
    """An exclusive lock across the whole read-modify-write, so two sessions cannot restore each
    other's counters. On a SEPARATE file, because save_state renames over the state file.

    Fails open: if the lock cannot be taken the caller proceeds unlocked. An unlocked turn can
    miscount; a gate that refuses to run catches nothing.
    """
    lock = STATE.with_name(STATE.name + ".lock")
    try:
        lock.parent.mkdir(parents=True, exist_ok=True)
        handle = lock.open("a")
    except OSError:
        yield False
        return
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        yield True
    finally:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()


def save_state(sessions: dict) -> None:
    """Write the newest REFLECT_SESSIONS_KEPT sessions, atomically via a temp file and rename, so a
    reader sees the old file or the new one and never a splice of two writes."""
    newest = sorted(sessions.items(), key=lambda kv: kv[1].get("at", 0), reverse=True)
    payload = json.dumps({"sessions": dict(newest[:REFLECT_SESSIONS_KEPT])})
    STATE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE.with_name(f"{STATE.name}.{os.getpid()}.tmp")
    try:
        with tmp.open("w") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, STATE)
    finally:
        tmp.unlink(missing_ok=True)


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0
    if not isinstance(payload, dict):
        return 0  # valid JSON, wrong shape

    if payload.get("stop_hook_active"):
        return 0  # already inside a block; never stack

    msg = payload.get("last_assistant_message") or ""
    session = payload.get("session_id", "")

    if not SIGNALS.search(msg):
        return 0

    newest = newest_mtime()
    if newest is None:
        return 0  # no watched directory exists; the gate cannot observe a write
    if newest >= time.time() - FRESH_S:
        return 0  # something was written recently; the lesson was recorded

    with state_lock():
        sessions = load_state()
        entry = sessions.get(session)
        if not isinstance(entry, dict):
            entry = {"blocks": 0, "at": 0}
        blocks = entry.get("blocks", 0)
        if blocks >= REFLECT_MAX:
            return 0

        sessions[session] = {"blocks": blocks + 1, "at": time.time()}
        try:
            save_state(sessions)
        except OSError:
            # Block anyway: losing the counter costs at most a few extra blocks against a ceiling
            # the harness enforces at 8, whereas skipping the block silently disables the gate.
            pass
    print(json.dumps({
        "decision": "block",
        "reason": (
            "This turn admits an error, a correction or a lesson, and nothing under skills/ or "
            "memory/ changed. Write it down before finishing: skills/<name>.md with a summary, "
            "what happened, what generalises, and its tells - the pattern on a tool call that "
            "means it is about to repeat (format in skills/README.md). Then run "
            "skill_index.py --write, and stop."
        ),
    }))
    return 0


if __name__ == "__main__":
    sys.exit(main())
