#!/usr/bin/env python3
"""PreToolUse gate: put a lesson in front of the agent when the pending call matches its tell.

The index in CLAUDE.md carries one line per lesson; this gate carries the whole lesson, at the one
moment it matters: just before the agent repeats the call the lesson was written about. Tells are
declared in each `skills/<name>.md` (see harness/skill_index.py), so a new lesson needs no change
here.

Never denies. A lesson is advice; a gate that blocks on advice stalls the work it is meant to
improve and gets removed. Fails open on any error of its own for the same reason.

Each match is appended to the fire ledger, shown or not, so the lessons review can see which
lessons keep being needed. A lesson is shown at most once per session every REARM_S, so a tell on a
common call does not flood the context. MAX_LESSONS caps how many one call can show.
"""
import json
import os
import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

REARM_S = 3600
MAX_LESSONS = 3
BODY_MAX = 1500
SESSIONS_KEPT = 8


def call_text(tool_input) -> str:
    """What a tell's pattern is searched in: a Bash command as typed, anything else as JSON."""
    if isinstance(tool_input, dict) and isinstance(tool_input.get("command"), str):
        return tool_input["command"]
    return json.dumps(tool_input, sort_keys=True)


def load_state(path: pathlib.Path) -> dict:
    try:
        d = json.loads(path.read_text())
    except Exception:
        return {}
    return {k: v for k, v in d.items() if isinstance(v, dict)} if isinstance(d, dict) else {}


def save_state(path: pathlib.Path, state: dict) -> None:
    """The newest SESSIONS_KEPT sessions, written atomically. Unlocked: two sessions racing can at
    worst show one lesson twice, which costs less than a lock on every tool call."""
    newest = sorted(state.items(), key=lambda kv: max(kv[1].values(), default=0), reverse=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    try:
        tmp.write_text(json.dumps(dict(newest[:SESSIONS_KEPT])))
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0
    if not isinstance(payload, dict):
        return 0
    tool = payload.get("tool_name")
    if not isinstance(tool, str):
        return 0
    session = str(payload.get("session_id", ""))
    text = call_text(payload.get("tool_input", {}))

    try:
        import bobcfg
        import skill_index
        skills, _errors = skill_index.load()
        hits = [s for s in skills if s.active and any(t.matches(tool, text) for t in s.tells)]
        if not hits:
            return 0
        state_path = bobcfg.state_home() / "skill-gate-state.json"
        state = load_state(state_path)
        shown = state.setdefault(session, {})
        now = time.time()
        due = [s for s in hits if now - shown.get(s.name, 0) >= REARM_S][:MAX_LESSONS]
        shown_now = {s.name for s in due}
        for s in hits:
            skill_index.record_fire(s.name, tool, session, injected=s.name in shown_now)
        if not due:
            return 0
        for s in due:
            shown[s.name] = now
        save_state(state_path, state)
    except Exception:
        return 0

    blocks = []
    for s in due:
        body = s.body if len(s.body) <= BODY_MAX else s.body[:BODY_MAX] + "\n[...] (read the file)"
        blocks.append(f"LESSON skills/{s.name}.md applies to this call: {s.summary}\n{body}")
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse",
                                             "additionalContext": "\n\n".join(blocks)}}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
