# Trader release notes

Newest first. Each change to this Bob or its tooling gets an entry saying what changed and what it
means for someone who relies on this Bob's output. "Nothing changes for you" is information too.

## Stage 1: core, simulator, Scout-calls strategy (mode off)

- Built: ledger, bars, Scout feed, simulator, reconcile, sizing, risk flags, caps, the scout_calls
  strategy, the daily run, HALT, live approval, the go-live report, both gates, the board, loops.
- What it means for you: nothing trades. `mode = "off"` is committed; switching to `simulate` is
  your change in bob.toml. Paper and live need adapters that are not built yet.

## Scaffold

Folder created by new_bob.py. Nothing runs yet; see PLAYBOOK.md section 3 for first launch.
