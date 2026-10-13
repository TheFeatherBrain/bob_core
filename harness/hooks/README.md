# Hook gates

Claude Code hooks that stop a known class of mistake at the moment it is made. Each is a script
that reads the pending tool call (or the ending turn) as JSON on stdin and answers with a decision
on stdout.

| file | event | what it does |
|---|---|---|
| `commit_gate.py` | `PreToolUse`, Bash | Refuses `git commit -m` when the shell would rewrite the message first: an unquoted or double-quoted message holding a backtick, `$(` or `$NAME`. Fails open on its own parse error |
| `slack_send_gate.py` | `PreToolUse`, Slack send tools | Refuses a send that `harness/slack_lint.py` rejects: broken code fences, Markdown that Slack shows literally, prose over the word budget |
| `skill_gate.py` | `PreToolUse`, every tool | Never refuses. When the pending call matches a tell declared in `skills/<name>.md`, adds that lesson to the context (at most once per session per hour, 3 per call) and appends the match to the fire ledger the lessons review reads |
| `reflect_gate.py` | `Stop` | Blocks a turn whose text admits an error, a correction or a lesson while nothing under `skills/` or the memory directory changed in 15 minutes. At most 2 blocks per session |

`test_gates.py` drives each gate both ways: a case it must refuse and a case it must allow.

## Registering a gate

A gate does nothing until the Bob's `.claude/settings.json` names it. `automation/new_bob.py`
writes that file with `commit_gate.py` and `skill_gate.py` always and `slack_send_gate.py` when
the manifest has a `[slack]` section. `reflect_gate.py` is opt-in: a Bob that apologises as part of its job (a tutor,
say) would be blocked by it.

```json
{
  "hooks": {
    "PreToolUse": [
      { "matcher": "mcp__.*[Ss]lack.*__slack_(send_message|send_message_draft|schedule_message)",
        "hooks": [{ "type": "command",
                    "command": "cd \"$CLAUDE_PROJECT_DIR\" && python3 ../bob_core/harness/hooks/slack_send_gate.py" }] },
      { "matcher": "Bash",
        "hooks": [{ "type": "command",
                    "command": "cd \"$CLAUDE_PROJECT_DIR\" && python3 ../bob_core/harness/hooks/commit_gate.py" }] },
      { "matcher": "*",
        "hooks": [{ "type": "command",
                    "command": "cd \"$CLAUDE_PROJECT_DIR\" && python3 ../bob_core/harness/hooks/skill_gate.py" }] }
    ],
    "Stop": [
      { "hooks": [{ "type": "command",
                    "command": "cd \"$CLAUDE_PROJECT_DIR\" && python3 ../bob_core/harness/hooks/reflect_gate.py" }] }
    ]
  }
}
```

Check the Slack matcher against the tool names your Slack MCP plugin actually exposes. After
registering, prove it: trigger a matching tool and confirm the gate answers. A configured gate that
never fires looks exactly like one that passed.
