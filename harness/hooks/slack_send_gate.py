#!/usr/bin/env python3
"""PreToolUse gate on Slack send tools: refuse a send whose text slack_lint rejects.

A linter that runs only when someone remembers to call it sits beside the path rather than across
it. This puts it across: every send is linted, and a rejected one is denied with the findings.
"""
import json
import sys
import pathlib

HERE = pathlib.Path(__file__).resolve().parent
# `HERE.parent` is where slack_lint lives; `HERE` lets a test copy both files side by side.
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))

#: The keys the Slack MCP tools carry prose in: `message` for a send, `initial_comment` for a file
#: upload. Reading the wrong key lints an empty string, which lints clean and approves every send.
#: Check these against your Slack MCP plugin's tool schema when you register the gate.
MESSAGE_KEYS = ("message", "initial_comment")


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return 0                                  # fail open; never block on our own bug

    tool_input = payload.get("tool_input") or {}
    parts = [tool_input.get(key) for key in MESSAGE_KEYS]
    message = "\n".join(p for p in parts if isinstance(p, str) and p.strip())
    if not message.strip():
        return 0

    try:
        from slack_lint import lint
    except ImportError:
        return 0

    findings = lint(message)
    if not findings:
        return 0

    detail = "\n".join(f"  {f}" for f in findings)
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": (
                f"slack_lint rejects this message ({len(findings)} finding(s)). "
                f"Fix the text and send again.\n{detail}"
            ),
        }
    }))
    return 0


if __name__ == "__main__":
    import oscompat
    oscompat.utf8_stdio()
    sys.exit(main())
