# Trader skills

What this Bob has learned that generalises: one file per lesson, written when it is learned.

## How a lesson comes back

- `INDEX.md` lists every active lesson in one line and is imported by `CLAUDE.md`, so each session
  starts with it. Regenerate it after any change: `python3 ../../harness/skill_index.py --write`.
- A lesson's **tells** are patterns on tool calls. When a pending call matches one, the skill gate
  shows the whole lesson just before the call runs, and records the match.
- The lessons-review loop reads those matches: a lesson that keeps matching is proposed as a tool or
  a gate; one that never matches gets a better tell or is retired.

## Format

`skills/<lowercase-name>.md`:

```
+++
summary = "One line: the rule, stated so it changes what you do next"
tells = [
  { tool = "Bash", pattern = 'regex on the command that would repeat the mistake' },
]
+++

# Title

What happened: the failure, with the evidence path.
What generalises: the rule.
The tell: why the pattern above is the observable sign that the mistake is about to happen.
```

- `summary`: required, one line, at most 160 characters.
- `tells`: optional. `pattern` is searched in a Bash call's command, or in any other call's input as
  JSON. `tool` is a regex the tool name must fully match (`Bash`, `Read`, `mcp__.*__fetch`); leave it
  out to match any tool. Prefer a narrow pattern: a tell that matches every call is noise.
- `retired = "2026-10-13: why"`: keeps the file for the record, drops it from the index and the gate.

Write the failure as well as the fix. A lesson with no tell is recalled only through the index; say
in the file why no observable exists.
