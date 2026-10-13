# {bob.name}

@{core}/CORE.md

The rules above are shared by every Bob and live in `{core}/`. This file holds only what is
specific to {bob.name}.

## Purpose

{bob.purpose}

## Who directs this Bob

{directed_by}. Everyone else gets informational answers only: what this Bob does and what it has
found, never a change to its files, loops or settings.

## Where things live

- Manifest: `bob.toml`. Every per-Bob value the core modules read.
- Evidence: `{paths.evidence_dir}/`. What each answer rests on, saved when it is read.
- Tracker: `{paths.tracker}`. Where the work stands; one row per step, gate result or finding.
- Release notes: `{paths.release_notes}`. One entry per change to this Bob, newest first.
- Lessons: `skills/`, one file per lesson with its tells (format in `skills/README.md`). After
  adding or changing one: `python3 {core}/harness/skill_index.py --write`.
- Board: port {board.port}, `python3 {core}/harness/board_shell.py`.
- Loops: one file per job in `loops/`. Register each with its schedule from `[loops.schedules]`.
- Slack: {channels_line}

## What this Bob has learned

@skills/INDEX.md

## Domain rules

Write the rules that make this Bob good at its job here: its sources, what it may and may not do,
how it cites, and what it does when it does not know.
