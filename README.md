# bob_core

**The shared kit for building Bobs.** A Bob is a long-running Claude Code agent with one job: a
tutor, an investment analyst, a log reader. Everything Bobs have in common lives here once: the
manifest, the rules, Slack handling, scheduled loops and their health, keeping the session alive,
a live board, release notes, and the hook gates that stop known mistakes.

A Bob adds only what is specific to it: its manifest (`bob.toml`), its domain rules (`CLAUDE.md`),
its own tools, and its own loops.

```
george/
  bob_core/            this kit
  tutor/               a Bob, scaffolded by new_bob.py
    bob.toml           the manifest: every per-Bob value
    CLAUDE.md          @../bob_core/CORE.md, then the Bob's own rules
    launch.sh          starts the Bob from its folder
    .claude/settings.json   hook gates, run from ../bob_core/harness/hooks/
    loops/             one prompt file per scheduled job
    skills/            what the Bob learns: one file per lesson, INDEX.md loaded every session
    docs/              TRACKER.md, RELEASE-NOTES.md
    supervisor/        wake prompt, session list, launchd plist or tick.cmd (when [runtime] is set)
    harness/           the Bob's own tools, e.g. a quotes reader or a lesson planner
```

## How it works

Nothing here runs by itself. There are three kinds of file:

1. **Markdown that steers the agent:** `CORE.md` (shared rules) and the loop prompts in
   `templates/loops/`.
2. **Small Python CLIs** in `harness/`, which the agent calls with Bash when its rules or a loop
   tell it to. Each reads the Bob's settings through `bobcfg.py`.
3. **Things that run outside the agent:** hook gates (Claude Code runs them around tool calls) and
   the session supervisor (launchd or Task Scheduler runs it every 20 minutes).

The agent is the brain. Core exists because an agent forgets, gets compacted, invents timestamps,
and misses scheduled runs without noticing. Each module turns something that would rely on
remembering into a tool that computes the answer or refuses a bad action.

## Modules

| module | what it does |
|---|---|
| `bobcfg.py` | Finds and validates the manifest; every per-Bob value comes through it. Required: `[bob]`, `[board]`, `[loops]`, `[paths]`. Optional, validated when present: `[slack]`, `[routing]`, `[runtime]`, `[credentials]` |
| `loop_passes.py` | Ledger of scheduled-job runs. Each loop records itself when it fires; `--audit` compares the ledger against the cron schedules, reports missed runs and own-loop files that are gone |
| `session_supervisor.py` | Run by launchd or Task Scheduler: resumes a dead Claude session, leaves live ones alone, refuses to guess when two sessions share a name, and removes a duplicate it created |
| `watchdog_check.py` | Checks the supervisor itself: scheduler job loaded, current, ticking. `--heal` starts a stalled job |
| `board_shell.py` | Live web board (HTTP + server-sent events, stdlib only). Default page: loop health and unposted release notes. A Bob's own panel module can take over the whole board |
| `release_digest.py` | Posts release notes in batches: everything above the last posted heading, never by clock |
| `slack_threads.py` | Thread-poll ledger: which thread roots are due, which are overdue, with their channel |
| `slack_seen.py` | Writes Slack timestamps into the seen-list and cursors only when a saved read contains them |
| `slack_roots.py` | Computes which threads in a channel read are not yet tracked, and appends them to the right roots file for that channel |
| `slack_lint.py` | Refuses Slack text that renders wrong (fences, Markdown) or breaks the register limits in `[slack.lint]` |
| `route.py` | When several Bobs share one Slack account: decides which Bob answers, by prefix then keyword. Reads the Bobs from `fleet.toml` |
| `evidence.py` | Saves one read as evidence: redacted raw content, the query, the UTC time window and a hash, under `<evidence_dir>/<date>/<id>/`. Never overwrites. A Bob's own tools call it |
| `redact.py` | Replaces credential material with named markers before text is saved as evidence |
| `skill_index.py` | The Bob's lessons: parses `skills/*.md`, writes and checks `skills/INDEX.md`, and `--stats` reads the fire ledger to flag lessons to promote (`PROMOTE?`), fix or retire (`QUIET`, `NO TELL`) |
| `did_i.py` | "Did this session run X?" answered from the transcript on disk, not the context window |
| `undefined_names.py` | Finds names a function reads that resolve to nothing, before they fail at runtime |
| `oscompat.py` | The OS calls that differ on Windows: a process liveness probe that never signals, file locks, TOML-safe and forward-slash path strings, UTF-8 standard streams |
| `hooks/` | `commit_gate.py`, `slack_send_gate.py`, `skill_gate.py`, `reflect_gate.py`; see `hooks/README.md` |

Platform: macOS and Windows. Plain Python 3.11+ with no dependencies. The supervisor runs under
launchd on macOS and Task Scheduler on Windows; `new_bob.py` writes the entry for the machine it
runs on. On Windows, run the shell scripts (`selftest.sh`, a Bob's `launch.sh`) from Git Bash,
which is also the shell Claude Code uses for hooks there.

## How a Bob learns

1. **Capture.** CORE.md says to write each lesson to `skills/<name>.md` with its tells; the opt-in
   `reflect_gate.py` blocks a turn that admits a mistake while nothing was written.
2. **Load.** `skills/INDEX.md` (one line per lesson) is imported by the Bob's CLAUDE.md, so it is in
   every session, including after a compaction.
3. **Recall.** `skill_gate.py` shows the whole lesson when a pending tool call matches a tell, and
   appends the match to `skill-fires.jsonl` in the state home.
4. **Review.** The `lessons-review` loop (weekly in the example) fixes, merges and retires lessons
   from `skill_index.py --stats`, and proposes a tool or a gate for any lesson that keeps matching.
5. **Promote.** A person approves the proposal; the lesson then becomes code, which no longer relies
   on being remembered. The agent never changes its own gates.

Health checks fail when a lesson file is malformed or the index is stale. The board lists each
lesson's matches.

## Making a Bob

```bash
cp bob.example.toml ~/tutor.toml          # edit it: purpose, port, sections you need
python3 automation/new_bob.py ~/tutor.toml --out ../tutor
```

The step-by-step is in [`PLAYBOOK.md`](PLAYBOOK.md).

## Checking core

```bash
./selftest.sh
```

Runs every module's `--selftest` and every test suite against `bob.example.toml`.
