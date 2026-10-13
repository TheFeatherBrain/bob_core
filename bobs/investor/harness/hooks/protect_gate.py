#!/usr/bin/env python3
"""PreToolUse gate: the Scout never edits its own record, evidence, settings or guards.

The ledger, the saved evidence, the manifest (which holds the scorecard weights) and these hooks
change only through their CLIs or through a person. An agent that could rewrite them could make
its track record look better, change what "buy-worthy" means, or switch this gate off: exactly
the prompt-only limits that sank the trading agents this Bob was designed against.

Refuses: Edit/Write/MultiEdit/NotebookEdit on any file under the state dir, the evidence dir,
harness/hooks/, bob.toml or .claude/settings.json; a Bash or PowerShell command that names one of
those paths and also writes (redirect, tee, sed -i, rm, mv, cp, Set-Content, Remove-Item, ...).
Reading them is allowed, and so is running the Bob's own CLIs.

Fails CLOSED: this gate protects state, so input it cannot read is refused.
"""
from __future__ import annotations

import json
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import scoutpath  # noqa: E402,F401
import scoutcfg  # noqa: E402

FILE_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}
SHELL_TOOLS = {"Bash", "PowerShell"}
WRITES = re.compile(r"(?<![0-9&])>|\btee\b|\bsed\b[^|;&]*\s-i|\brm\b|\bmv\b|\bcp\b|\btruncate\b|\bdd\b|"
                    r"\bln\b|Set-Content|Add-Content|Out-File|Remove-Item|Move-Item|Copy-Item|Rename-Item|"
                    r"Clear-Content|\bpython3?\s+-c\b|\bgit\s+(checkout|restore|reset|clean)\b", re.I)


def protected() -> list[pathlib.Path]:
    bob = scoutcfg.bob_dir()
    return [scoutcfg.state_dir(), scoutcfg.evidence_dir(), bob / "harness" / "hooks", bob / "bob.toml",
            bob / ".claude" / "settings.json"]


def _inside(path: pathlib.Path, roots: list[pathlib.Path]) -> bool:
    resolved = path.resolve()
    return any(resolved == root.resolve() or resolved.is_relative_to(root.resolve()) for root in roots)


def decide(payload: dict, cwd: pathlib.Path) -> str | None:
    tool = payload.get("tool_name", "")
    data = payload.get("tool_input") or {}
    roots = protected()
    if tool in FILE_TOOLS:
        target = data.get("file_path") or data.get("notebook_path")
        if target and _inside(cwd / target, roots):
            return (f"{target} is protected (ledger, evidence, manifest, settings or hooks). Use the "
                    "Bob's CLIs (calls.py, evidence.save) or ask the person who directs this Bob.")
        return None
    if tool in SHELL_TOOLS:
        command = str(data.get("command", "")).replace("\\", "/")
        bob = scoutcfg.bob_dir().resolve()
        names = {r.resolve().relative_to(bob).as_posix() for r in roots if r.resolve().is_relative_to(bob)}
        named = sorted(n for n in names if n in command)
        if named and WRITES.search(command):
            return (f"this command writes near protected paths ({', '.join(named)}). Only the Bob's CLIs "
                    "change the ledger and evidence; manifest, settings and hooks change by a person.")
    return None


def _deny(reason: str) -> None:
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                             "permissionDecisionReason": reason}}))


def main() -> int:
    try:
        payload = json.load(sys.stdin)
        reason = decide(payload, pathlib.Path.cwd())
    except Exception as exc:  # noqa: BLE001 - fail closed on anything this gate cannot judge
        reason = f"protect_gate could not judge this call ({type(exc).__name__}); refusing, as state gates do"
    if reason:
        _deny(reason)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
