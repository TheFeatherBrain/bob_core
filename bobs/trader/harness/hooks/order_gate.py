#!/usr/bin/env python3
"""PreToolUse gate: orders reach a broker only through trade.py, and approvals are the person's.

trade.py and approve.py are the only doors to a broker: they run reconcile, the input checks and
the caps first. This gate closes the others the agent has: a shell, WebFetch, file writes (a script
holding an orders URL or importing an adapter could be written, then run) and MCP servers. It also
refuses any tool call naming approve.py or `ledger.py correct`: approving a live order and
correcting the books are the person's acts, run from the person's own terminal.

Refuses: any broker-looking MCP tool; any call input naming an Alpaca orders, positions,
account-configuration or watchlists endpoint, or the live host api.alpaca.markets; a shell command
or a code file (.py .sh .ps1 .cmd .bat) naming harness/broker/ or importing the broker package; a
shell command naming approve.py or `ledger.py correct`; a shell command or code file that imports
approve, calls ledger.correct, ledger.append or store.append_jsonl, or builds a "correction" line
(the same person-only acts, reached without naming the scripts).
Allows: trade.py --run, halt.py, ledger.py book/summary, market-data reads, and notes (the tracker,
lessons) that mention approve.py or the adapters: writing "the person should run approve.py" is
exactly what the loops ask for.

Fails CLOSED: input it cannot read is refused. Only the standard library is imported.
"""
from __future__ import annotations

import json
import pathlib
import re
import sys

ORDER_PATHS = re.compile(r"alpaca\.markets/v\d+/(orders|positions|account/configurations|watchlists)", re.I)
LIVE_HOST = re.compile(r"(?<![\w.-])api\.alpaca\.markets", re.I)
BROKER_MCP = re.compile(r"alpaca|broker|ibkr|interactive.?brokers|tastytrade|schwab|robinhood|etrade|trade|order", re.I)
ADAPTER = re.compile(r"harness[/\\]+broker[/\\]|\b(import|from)\s+broker\b|-m\s+broker\b", re.I)
PERSON_ONLY = re.compile(r"\bapprove\.py\b|\bledger\.py\b[^|;&]*\bcorrect\b", re.I)
PERSON_API = re.compile(r"\b(import|from)\s+approve\b|\bfrom\s+ledger\s+import\b[^\n;]*\bcorrect\b|"
                        r"\bledger\.(correct|append)\s*\(|\bstore\.append_jsonl\s*\(|['\"]correction['\"]")
SHELLS = {"Bash", "PowerShell"}
CODE_FILE = re.compile(r"\.(py|sh|ps1|cmd|bat)$", re.I)


def decide(payload: dict) -> str | None:
    tool = str(payload.get("tool_name", ""))
    if tool.startswith("mcp__") and BROKER_MCP.search(tool):
        return "broker tools are refused: orders go only through harness/trade.py --run"
    data = payload.get("tool_input") or {}
    text = json.dumps(data)
    if ORDER_PATHS.search(text) or LIVE_HOST.search(text):
        return "broker order, position and account endpoints and the live host are refused: orders go only through trade.py"
    if tool in SHELLS and PERSON_ONLY.search(text):
        return ("approve.py and ledger.py correct are the person's: they run them from their own terminal "
                "(or with ! in Claude Code). Record what needs approving or correcting in the tracker.")
    code = tool in SHELLS or bool(CODE_FILE.search(str(data.get("file_path") or "")))
    if code and ADAPTER.search(text):
        return "broker adapters run only inside trade.py and approve.py, never directly"
    if code and PERSON_API.search(" ".join(str(v) for v in data.values())):
        return ("approving, correcting and appending to the ledger are not done from code the agent writes: "
                "approve.py and ledger.py correct are the person's; trade.py and halt.py write the rest")
    return None


def main() -> int:
    try:
        sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
        import traderpath  # noqa: F401
        import oscompat
        oscompat.utf8_stdio()
        reason = decide(json.load(sys.stdin))
    except Exception as exc:  # noqa: BLE001 - fail closed
        reason = f"order_gate could not judge this call ({type(exc).__name__}); refusing"
    if reason:
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                                 "permissionDecisionReason": reason}}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
