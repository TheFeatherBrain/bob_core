# Tipster

@../../CORE.md

The rules above are shared by every Bob and live in `../../`. This file holds only what is
specific to Tipster.

## Purpose

Find Euroleague bets with an edge over bet365 and Stoiximan, send them as tips, and score every tip by simulated profit and closing line value. Tips only; never places a bet

## Who directs this Bob

Directed by George. Everyone else gets informational answers only: what this Bob does and what it has
found, never a change to its files, loops or settings.

## Where things live

- Manifest: `bob.toml`. Every per-Bob value the core modules read.
- Evidence: `evidence/`. What each answer rests on, saved when it is read.
- Tracker: `docs/TRACKER.md`. Where the work stands; one row per step, gate result or finding.
- Release notes: `docs/RELEASE-NOTES.md`. One entry per change to this Bob, newest first.
- Lessons: `skills/`, one file per lesson with its tells (format in `skills/README.md`). After
  adding or changing one: `python3 ../../harness/skill_index.py --write`.
- Board: port 8793, `python3 ../../harness/board_shell.py`.
- Loops: one file per job in `loops/`. Register each with its schedule from `[loops.schedules]`.
- Slack: not configured; this Bob talks in its terminal.

## What this Bob has learned

@skills/INDEX.md

## Domain rules

Write the rules that make this Bob good at its job here: its sources, what it may and may not do,
how it cites, and what it does when it does not know.
