# Tipster release notes

Newest first. Each change to this Bob or its tooling gets an entry saying what changed and what it
means for someone who relies on this Bob's output. "Nothing changes for you" is information too.

## First version

- Tipster v1 is built: stats, team model, calibration gate, pricing and stakes, the two-track
  ledger with HALT, discovery and Claude's review, news watch and rechecks, closing snapshots,
  settlement with CLV, the Telegram outbox, the board (port 8793), the weekly digest, and the
  protect and network gates. Plan: `docs/superpowers/plans/2026-10-20-tipster.md`.
- Calibration on 2023-24 to 2025-26: winner and handicap pass; totals fail and are not tipped.
- What it means for you: nothing runs yet. `mode` is off, the odds provider is not wired (it needs
  your OddsPapi key and the step 0 test), and Telegram needs your bot. See the tracker.

## Scaffold

Folder created by new_bob.py. Nothing runs yet; see PLAYBOOK.md section 3 for first launch.
