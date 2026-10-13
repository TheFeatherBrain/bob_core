#!/usr/bin/env python3
"""PreToolUse gate on Bash: refuse a `git commit -m` whose message the shell would rewrite.

A commit message that carries a word in backticks, `$(...)` or `$NAME` inside double quotes is
rewritten by the shell before git sees it: the backticked word runs as a command and vanishes from
the message, and nothing errors. The loss is invisible at every layer below the author, which is why
it is a gate rather than a habit.

What it refuses: `git commit` with `-m`/`--message` where the message is unquoted or double-quoted
and contains a backtick, `$(` or `$NAME`. Single-quoted messages are left alone, because the shell
rewrites nothing inside them. The fix it suggests is `git commit -F <file>`.

Fails OPEN on its own parse error: a gate that cannot parse the command must not block work.
"""
from __future__ import annotations

import json
import re
import sys

#: Backtick substitution, `$(...)`, and `$NAME` / `${NAME}`. Each one makes the shell replace text
#: inside the message with something else, or with nothing.
REWRITES = re.compile(r"`|\$\(|\$\{?[A-Za-z_]")

COMMIT = re.compile(r"\bgit\s+(?:-C\s+\S+\s+)?commit\b")

REASON = ("This `git commit -m` message contains text the shell rewrites before git sees it "
          "(a backtick, $(...) or $NAME), so the commit would record a message you did not "
          "write. Write the message to a file and use `git commit -F <file>`, or single-quote it "
          "if it has no single quotes of its own.")


def _message_args(command: str) -> list[str]:
    """The raw, still-quoted `-m` values in a git commit command, in order."""
    out: list[str] = []
    for m in re.finditer(r"git\s+commit\b", command):
        tail = command[m.end():]
        # Stop at a command separator: a later command's quoting is its own business.
        tail = re.split(r"(?<!\\)(?:&&|\|\||;|\n)", tail)[0]
        for opt in re.finditer(r"(?:^|\s)(?:-m|--message)(?:=|\s+)(?=(\S))", tail):
            rest = tail[opt.end():]
            quote = rest[0] if rest[:1] in ("'", '"') else ""
            if quote:
                end = rest.find(quote, 1)
                out.append(rest[: end + 1] if end > 0 else rest)
            else:
                out.append(rest.split(" ")[0])
    return out


def would_be_rewritten(command: str) -> bool:
    for raw in _message_args(command):
        if raw.startswith("'"):
            continue                      # single quotes: the shell rewrites nothing
        body = raw[1:-1] if raw.startswith('"') and raw.endswith('"') else raw
        if REWRITES.search(body):
            return True
    return False


def _deny(reason: str) -> None:
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "deny",
        "permissionDecisionReason": reason,
    }}))


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return 0                                   # fail open; never block on our own bug
    if not isinstance(payload, dict):
        return 0
    command = ((payload.get("tool_input") or {}).get("command") or "")
    try:
        if COMMIT.search(command) and would_be_rewritten(command):
            _deny(REASON)
    except Exception:                              # noqa: BLE001 - fail open on a parse bug
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
