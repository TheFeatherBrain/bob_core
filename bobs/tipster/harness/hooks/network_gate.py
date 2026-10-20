#!/usr/bin/env python3
"""PreToolUse gate: the Tipster never reaches a bookmaker, never calls the odds provider around its
budget, and never calls Telegram around its outbox.

Why bookmakers are refused everywhere (WebFetch, WebSearch, shell): bet365's terms forbid scraping,
and Tipster never logs in or bets (spec 8, 13). Why a direct network call to the odds host is
refused: odds requests are scarce and counted by budget.py before each one; a curl from a loop
spends the free quota unseen. Why api.telegram.org is refused outside telegram.py: messages go
through the outbox, which lints them and keeps them in order, and the bot token must never appear
on a command line.

Refuses:
- WebFetch to a bookmaker domain, the odds host or api.telegram.org.
- WebSearch whose query or allowed_domains name a bookmaker domain.
- A Bash or PowerShell command that names a bookmaker domain or api.telegram.org.
- A Bash or PowerShell command that makes a network call (curl, wget, Invoke-WebRequest,
  Invoke-RestMethod, python -c, ...) and names the odds host.
Allowed: the Bob's own CLIs (odds.py, notify.py, core's telegram.py), which do not name these
hosts on their command line, and reading code that mentions the odds host.

Fails CLOSED: this gate guards a cost and a terms-of-use line, so input it cannot read is refused.
"""
from __future__ import annotations

import json
import re
import sys
import urllib.parse

BOOKMAKER = re.compile(r"(?<![\w-])(bet365|stoiximan|betano|pamestoixima|novibet|pinnacle|bwin|interwetten)\.[a-z]", re.I)
TELEGRAM = re.compile(r"api\.telegram\.org", re.I)
ODDS_HOSTS = ("api.oddspapi.io",)
NETWORK = re.compile(r"\b(curl|wget|http|https|invoke-webrequest|iwr|invoke-restmethod|irm|nc|telnet|openssl)\b|"
                     r"\bpython3?(\.exe)?\s+(-c\b|-(\s|$)|<<)|\b(node|ruby|perl|deno|bun)\b[^|;&]*\s-e\b", re.I)
SHELL_TOOLS = {"Bash", "PowerShell"}


def _host(url: str) -> str:
    try:
        return (urllib.parse.urlsplit(url).hostname or "").casefold()
    except ValueError:
        return ""


def decide(payload: dict) -> str | None:
    tool = payload.get("tool_name", "")
    data = payload.get("tool_input") or {}
    if tool == "WebFetch":
        url = str(data.get("url", ""))
        host = _host(url)
        if BOOKMAKER.search(host + "/") or BOOKMAKER.search(url):
            return f"{host} is a bookmaker: Tipster never reads bookmaker sites. Odds come only through odds.py."
        if host in ODDS_HOSTS:
            return f"{host} is the odds provider: requests go through odds.py, which counts them against the budget."
        if TELEGRAM.search(url):
            return "Telegram is reached only through notify.py and core's telegram.py."
        return None
    if tool == "WebSearch":
        text = " ".join([str(data.get("query", ""))] + [str(d) for d in data.get("allowed_domains") or []])
        if BOOKMAKER.search(text):
            return "searching a bookmaker site is reading it; odds come only through odds.py"
        return None
    if tool not in SHELL_TOOLS:
        return None
    command = str(data.get("command", ""))
    if BOOKMAKER.search(command):
        return "this command names a bookmaker site; Tipster never reads or calls one"
    if TELEGRAM.search(command):
        return "Telegram is reached only through notify.py and core's telegram.py"
    if NETWORK.search(command) and any(h in command.casefold() for h in ODDS_HOSTS):
        return "this command calls the odds provider directly; use odds.py so the budget counts the request"
    return None


def _deny(reason: str) -> None:
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                             "permissionDecisionReason": reason}}))


def main() -> int:
    try:
        sys.stdin.reconfigure(encoding="utf-8")
        reason = decide(json.load(sys.stdin))
    except Exception as exc:  # noqa: BLE001 - fail closed on anything this gate cannot judge
        reason = f"network_gate could not judge this call ({type(exc).__name__}); refusing"
    if reason:
        _deny(reason)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
