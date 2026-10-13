#!/usr/bin/env python3
"""Did THIS session actually run that? Ask the transcript, not the context window.

Usage::

    python3 harness/did_i.py my_tool.py             # my own invocations, any project dir
    python3 harness/did_i.py slack_send --everyone  # every session on this machine
    python3 harness/did_i.py "git commit" --since 17:00
    python3 harness/did_i.py --selftest

**A context window is a cache of a session's history, not the history.** Compaction evicts silently
and leaves no gap marker, so "I do not recall doing it" and "I did not do it" are the same sentence
from the inside. An agent asked "did you run X?" will check its context, find nothing, and deny
work it did. The only cure is to read the record rather than the recollection.

Three traps, each of which alone produces a confident wrong answer:

* **One project directory is not enough.** Claude Code keys transcripts by working directory, so
  the same agent working in `~/` and in `~/my_bob` writes to two different directories.
* **Transcript timestamps are UTC.** An evening run in local time can appear as the next day.
* **"No match" is not "could not look".** A permission error on a directory looks exactly like a
  clean result. This prints what it searched and refuses to report a bare zero.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import bobcfg  # noqa: E402

PROJECTS = bobcfg.claude_home() / "projects"


def own_session_id() -> str | None:
    """This session's transcript uuid, from the scratchpad path the harness hands every session.

    Returns `None` rather than guessing. A wrong answer here mislabels someone else's work as
    yours, which is the same defect pointing the other way.
    """
    for var in ("CLAUDE_SCRATCHPAD_DIR", "CLAUDE_JOB_DIR", "TMPDIR"):
        value = os.environ.get(var) or ""
        found = re.search(r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})", value)
        if found:
            return found.group(1)
    return None


def to_local(ts: str) -> str:
    """`2026-09-22T00:43:55` (UTC, as transcripts store it) -> local `09-21 20:43:55`."""
    try:
        when = _dt.datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return ts
    if when.tzinfo is None:
        when = when.replace(tzinfo=_dt.timezone.utc)
    return when.astimezone().strftime("%m-%d %H:%M:%S")


def invocations(line: str, pattern: re.Pattern) -> list[tuple[str, str, str]]:
    """`(utc_ts, tool_name, text)` for each tool CALL in one transcript line that matches.

    Only `tool_use` blocks count. A pattern appearing in prose, in a tool RESULT, or in a file this
    session merely read is not evidence that this session ran it.
    """
    if not pattern.search(line):
        return []
    try:
        record = json.loads(line)
    except ValueError:
        return []
    ts = (record.get("timestamp") or "")[:19]
    content = (record.get("message") or {}).get("content")
    if not isinstance(content, list):
        return []
    out = []
    for block in content:
        if not isinstance(block, dict) or block.get("type") != "tool_use":
            continue
        name = str(block.get("name") or "")
        payload = block.get("input") or {}
        text = str(payload.get("command") or payload.get("file_path") or json.dumps(payload))
        if pattern.search(text) or pattern.search(name):
            out.append((ts, name, " ".join(text.split())))
    return out


def search(pattern: str, root: pathlib.Path = PROJECTS,
           since: str | None = None) -> dict:
    """Every matching tool call across every project directory, with what was searched.

    `searched` and `unreadable` are returned beside the hits on purpose. A count of hits with no
    denominator cannot distinguish "did not happen" from "did not look".
    """
    rx = re.compile(pattern)
    hits: list[dict] = []
    searched, unreadable = 0, []
    if not root.exists():
        return {"hits": [], "searched": 0, "unreadable": [str(root)], "pattern": pattern}
    for path in sorted(root.glob("*/*.jsonl")):
        try:
            with path.open(encoding="utf-8", errors="replace") as handle:
                searched += 1
                for line in handle:
                    for ts, name, text in invocations(line, rx):
                        hits.append({"utc": ts, "local": to_local(ts), "tool": name,
                                     "session": path.stem, "project": path.parent.name,
                                     "text": text[:110]})
        except OSError as exc:
            unreadable.append(f"{path}: {exc.__class__.__name__}")
    if since:
        hits = [h for h in hits if h["local"][6:] >= since]
    hits.sort(key=lambda h: h["utc"])
    return {"hits": hits, "searched": searched, "unreadable": unreadable, "pattern": pattern}


def _selftest() -> int:
    import tempfile
    ok = True

    def check(name: str, cond: bool) -> None:
        nonlocal ok
        print(f"  {'ok  ' if cond else 'FAIL'}  {name}")
        ok = ok and cond

    root = pathlib.Path(tempfile.mkdtemp(prefix="didi-"))
    (root / "-Users-bob").mkdir()
    (root / "-Users-bob-repo").mkdir()

    def rec(ts, kind, text, tool="Bash"):
        if kind == "tool_use":
            content = [{"type": "tool_use", "name": tool, "input": {"command": text}}]
        else:
            content = [{"type": "text", "text": text}]
        return json.dumps({"timestamp": ts, "message": {"content": content}}) + "\n"

    (root / "-Users-bob" / "aaa.jsonl").write_text(
        rec("2026-09-22T00:43:55Z", "tool_use", "nohup python3 my_tool.py --full"), encoding="utf-8")
    (root / "-Users-bob-repo" / "bbb.jsonl").write_text(
        rec("2026-09-21T18:00:00Z", "text", "I should run my_tool.py later"), encoding="utf-8")

    got = search("my_tool", root)
    check("a tool CALL is a hit", len(got["hits"]) == 1)
    check("a MENTION in prose is not a hit",
          all(h["session"] != "bbb" for h in got["hits"]))
    check("BOTH project directories are searched", got["searched"] == 2)
    check("UTC is converted to local",
          got["hits"][0]["local"].endswith(to_local("2026-09-22T00:43:55")[-8:]))
    # Near midnight UTC the local date differs from the UTC date: west of UTC just after midnight
    # is the previous day, east of UTC just before midnight is the next. At UTC+0 it never does.
    offset = _dt.datetime(2026, 9, 22, tzinfo=_dt.timezone.utc).astimezone().utcoffset()
    near_midnight = "2026-09-22T00:43:55" if offset < _dt.timedelta(0) else "2026-09-22T23:43:55"
    check("the local date can differ from the UTC date",
          to_local(near_midnight)[:5] != "09-22" or offset == _dt.timedelta(0))

    empty = search("no-such-command-anywhere", root)
    check("a clean result still reports what was searched", empty["searched"] == 2)
    check("a clean result is empty, not an error", empty["hits"] == [])

    missing = search("anything", root / "does-not-exist")
    check("an absent root is reported UNREADABLE, not clean",
          missing["unreadable"] and missing["searched"] == 0)

    check("own_session_id returns None rather than guessing",
          own_session_id() is None or re.fullmatch(
              r"[0-9a-f-]{36}", own_session_id() or "") is not None)
    return 0 if ok else 1


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("pattern", nargs="?", help="regex matched against the tool call")
    parser.add_argument("--everyone", action="store_true",
                        help="every session, not only this one")
    parser.add_argument("--since", help="local HH:MM:SS floor, today")
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args(argv)
    if args.selftest:
        return _selftest()
    if not args.pattern:
        parser.error("a pattern is required")

    me = own_session_id()
    got = search(args.pattern, since=args.since)
    hits = got["hits"] if args.everyone else [h for h in got["hits"] if h["session"] == me]

    if got["unreadable"]:
        print("COULD NOT SEARCH, so a zero below means nothing:")
        for item in got["unreadable"]:
            print(f"  {item}")
    scope = "every session" if args.everyone else (f"session {me[:8]}" if me else "UNKNOWN session")
    print(f"{len(hits)} tool call(s) matching {args.pattern!r} in {scope}, "
          f"across {got['searched']} transcript(s)")
    if me is None and not args.everyone:
        print("  own session id could not be resolved - re-run with --everyone and read the "
              "session column, rather than trusting this zero")
    for hit in hits:
        mine = "*" if hit["session"] == me else " "
        print(f"  {mine} {hit['local']}  {hit['session'][:8]}  {hit['tool']:<6} {hit['text']}")
    return 0


if __name__ == "__main__":
    import oscompat
    oscompat.utf8_stdio()
    raise SystemExit(main())
