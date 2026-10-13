#!/usr/bin/env python3
"""PreToolUse gate: no Alpaca order, position or account change, and no live trading host, ever.

This Bob is read-only. prices.py already refuses any URL outside the data host and the calendar,
but the agent also has a shell, WebFetch, file writes (a script holding an orders URL could be
written, then run) and possibly MCP servers; this gate closes those doors. Paper keys can still
place paper orders, so this gate, not the key, is what keeps the Bob read-only.
It refuses any Alpaca MCP tool, any call input naming an orders, positions, account-configuration
or watchlists endpoint, and any mention of the live host api.alpaca.markets. Market data
(data.alpaca.markets) and the calendar stay allowed.

When a Trader Bob is built it gets its own manifest, credentials and its own order gate; nothing
here is loosened for it.

Fails CLOSED: input it cannot read, or a failure loading its helpers, is refused. Only the
standard library is imported at module level for that reason.
"""
from __future__ import annotations

import json
import pathlib
import re
import sys

ORDER_PATHS = re.compile(r"alpaca\.markets/v\d+/(orders|positions|account/configurations|watchlists)", re.I)
LIVE_HOST = re.compile(r"(?<![\w.-])api\.alpaca\.markets", re.I)
REASON = ("This Bob is read-only and never trades: Alpaca order, position and account endpoints, the "
          "live trading host and Alpaca tools are refused. Market data and the calendar are allowed.")


def decide(payload: dict) -> str | None:
    tool = str(payload.get("tool_name", ""))
    if tool.startswith("mcp__") and "alpaca" in tool.lower():
        return REASON
    text = json.dumps(payload.get("tool_input") or {})
    if ORDER_PATHS.search(text) or LIVE_HOST.search(text):
        return REASON
    return None


def main() -> int:
    try:
        sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
        import scoutpath  # noqa: F401
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
