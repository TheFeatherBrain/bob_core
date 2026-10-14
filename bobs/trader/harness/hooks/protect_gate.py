#!/usr/bin/env python3
"""PreToolUse gate: the Trader never edits its own ledger, state, evidence, settings, code or guards.

The ledger, the simulator's state, HALT, the saved evidence, the manifest (which holds the mode,
the caps and the go-live bar), the Bob's code, core's code and the Claude settings change only
through their CLIs or through a person. An agent that could rewrite them could make its books agree
with the broker by itself, loosen a cap, switch modes, release the HALT brake or switch this gate
off.

Refuses:
- Edit/Write/MultiEdit/NotebookEdit on any file under the state dir, the evidence dir, harness/,
  .claude/, bob.toml, core's harness/, or the Claude config dir's settings files. Paths are
  compared case-insensitively after resolving `.` and `..`.
- A Bash or PowerShell command whose redirect or tee target (`>`, `>>`, `&>`, `2>`, `tee -a`)
  resolves to one of those paths, from the working directory.
- A command segment (split on && || ; |, heredocs kept whole) that writes in another way (rm, mv,
  cp, sed -i/--in-place, perl -i, python -c or -, node -e, Set-Content, Remove-Item, git
  checkout/restore/reset/clean, ...) and names a protected path or file (ledger-paper.jsonl, bob.toml,
  data/state, ...) as a whole path component; a write in any segment after a `cd` (pushd,
  Set-Location) into a protected folder; and a `cd` plus redirect that names one.
Reading them is allowed, and so is running the Bob's own CLIs, including with output redirected
to a log.

The shell check is best effort: a shell is a programming language, and a determined command can
always hide a path (variables, encodings, a script written first and run later). The file-tool
check and the CLIs' own refusals are the firm guards; this one catches the ordinary slips.

Fails CLOSED: this gate protects state, so input it cannot read, or a failure loading the Bob's
own modules, is refused. Only the standard library is imported at module level for that reason.
"""
from __future__ import annotations

import json
import os
import pathlib
import re
import sys

FILE_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}
SHELL_TOOLS = {"Bash", "PowerShell"}
REDIRECT = re.compile(r"(?:\d|&)?>>?\|?\s*(['\"]?)([^\s'\";&|<>]+)\1")
TEE = re.compile(r"\btee\b((?:\s+-[\w-]+)*)\s+(['\"]?)([^\s'\";&|<>]+)\2", re.I)
OTHER_WRITES = re.compile(
    r"\brm\b|\bmv\b|\bcp\b|\btruncate\b|\bdd\b|\bln\b|\bchmod\b|\btouch\b|\binstall\b|"
    r"\bsed\b[^|;&]*\s(-i|--in-place)|\bperl\b[^|;&]*\s-\w*i|"
    r"\bpython3?(\.exe)?\s+(-c\b|-(\s|$)|<<)|\b(node|ruby|deno|bun)\b[^|;&]*\s-e\b|"
    r"set-content|add-content|out-file|remove-item|move-item|copy-item|rename-item|clear-content|new-item|"
    r"\bgit\s+(checkout|restore|reset|clean|apply|stash)\b", re.I)
SEGMENT = re.compile(r"&&|\|\||;|\|")
CHANGE_DIR = re.compile(r"(^|[;&|(\s])(cd|pushd|set-location|sl)\s", re.I)
LEAVES = ("ledger-simulate.jsonl", "ledger-paper.jsonl", "ledger-live.jsonl", "mode_history.jsonl", "halt", "broker.json", "bob.toml", "settings.json",
          "settings.local.json", "state/", "evidence/")
NOT_FILES = {"/dev/null", "nul", "$null"}


def _modules():
    """The Bob's own modules, loaded inside main's try so a broken import is refused, not allowed."""
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
    import traderpath  # noqa: F401
    import bobcfg
    import tradercfg
    return traderpath, bobcfg, tradercfg


def protected() -> list[pathlib.Path]:
    traderpath, bobcfg, tradercfg = _modules()
    bob, home = tradercfg.bob_dir(), bobcfg.claude_home()
    return [tradercfg.state_dir(), tradercfg.evidence_dir(), bob / "harness", bob / ".claude", bob / "bob.toml",
            traderpath.CORE / "harness", home / "settings.json", home / "settings.local.json"]


def _key(path: pathlib.Path) -> str:
    return os.path.normcase(str(path.resolve())).casefold()


def _inside(path: pathlib.Path, roots: list[pathlib.Path]) -> bool:
    key = _key(path)
    return any(key == root or key.startswith(root.rstrip(os.sep) + os.sep) for root in map(_key, roots))


def _names(roots: list[pathlib.Path], bob: pathlib.Path) -> set[str]:
    """How a command could spell a protected path from the Bob folder: relative forms and leaves."""
    out = set(LEAVES)
    for root in roots:
        rel = os.path.relpath(root.resolve(), bob.resolve()).replace("\\", "/").casefold()
        out.add(rel)
        out.add(root.name.casefold())
    return {n for n in out if n not in ("", ".")}


def _targets(command: str) -> list[str]:
    found = [m[1] for m in REDIRECT.findall(command)] + [m[2] for m in TEE.findall(command)]
    return [t for t in found if t.casefold() not in NOT_FILES]


def decide(payload: dict, cwd: pathlib.Path) -> str | None:
    tool = payload.get("tool_name", "")
    data = payload.get("tool_input") or {}
    if tool not in FILE_TOOLS | SHELL_TOOLS:
        return None
    roots = protected()
    if tool in FILE_TOOLS:
        target = data.get("file_path") or data.get("notebook_path")
        if target and _inside(cwd / target, roots):
            return (f"{target} is protected (ledger, evidence, manifest, settings or code). "
                    "Use the Bob's CLIs (trade.py, halt.py) or ask the person who directs this Bob.")
        return None
    command = str(data.get("command", "")).replace("\\", "/")
    targets = _targets(command)
    hit = next((t for t in targets if _inside(cwd / t, roots)), None)
    if hit:
        return (f"this command writes to {hit}, which is protected. "
                "Only the Bob's CLIs change the ledger and state; manifest, settings and code change by a person.")
    flat = re.sub(r"/(\./)+", "/", command).casefold()
    names = _names(roots, _modules()[2].bob_dir())
    segments = [flat] if "<<" in flat else SEGMENT.split(flat)
    named: set[str] = set()
    entered: list[str] = []
    for seg in segments:
        writes = bool(OTHER_WRITES.search(seg) or _targets(seg))
        if writes and entered:
            named.update(entered)
        if OTHER_WRITES.search(seg):
            named.update(_named(seg, names))
        if CHANGE_DIR.search(seg):
            entered += _named(seg, names)
    if not named and targets and CHANGE_DIR.search(flat):
        named.update(_named(flat, names))
    named = sorted(named)
    if named:
        return (f"this command writes and names protected paths ({', '.join(named)}). "
                "Only the Bob's CLIs change the ledger and state; manifest, settings and code change by a person.")
    return None


def _named(text: str, names: set[str]) -> list[str]:
    """Protected names that appear as whole path components, so `income_statement` is not `state`."""
    return sorted(n for n in names if re.search(rf"(?<![\w.-]){re.escape(n)}(?![\w-])", text))


def _deny(reason: str) -> None:
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                             "permissionDecisionReason": reason}}))


def main() -> int:
    try:
        _modules()
        import oscompat
        oscompat.utf8_stdio()
        payload = json.load(sys.stdin)
        reason = decide(payload, pathlib.Path.cwd())
    except Exception as exc:  # noqa: BLE001 - fail closed on anything this gate cannot judge
        reason = f"protect_gate could not judge this call ({type(exc).__name__}); refusing, as state gates do"
    if reason:
        _deny(reason)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
