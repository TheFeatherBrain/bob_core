# Trader release notes

Newest first. Each change to this Bob or its tooling gets an entry saying what changed and what it
means for someone who relies on this Bob's output. "Nothing changes for you" is information too.

## Keys from a file outside the repo

- `[runtime] env_file` names `C:/Users/georg/src/george/bob-secrets.env`; `launch.sh` and the
  supervisor load the Alpaca data keys from it. A variable already set in the environment still wins.
- What it means for you: no `setx` needed. A malformed or misplaced file stops `launch.sh` with the
  line and key at fault (never the value); the supervisor logs it and keeps sessions alive.

## Stage 1 review fixes

- A held stock with no price for the day (a halted stock) no longer stops the run: it is valued
  at its last close with a WARN, buys wait, every other exit still runs. A missing SPY bar, or a
  held stock never priced at all, still fails the run.
- A torn ledger line, or a held stock with no split-adjusted close, can no longer leave a position
  without a working stop while the run says OK.
- One ledger per mode (`data/state/ledger-simulate.jsonl`, `ledger-paper.jsonl`, ...). The first
  run in a mode opens its ledger with the broker's cash; the daily loss limit and the "calls since"
  date look only at the current mode; lots closed by your corrections are not go-live trades.
- Mode off: `bars.py --refresh` reads nothing and needs no keys. A missing Scout state folder is
  a FAIL, not "no calls". Live approvals re-check HALT, the loss limit, a withdrawn call and a
  second sell of the same holding.
- What it means for you: nothing trades yet (`mode = "off"`). When you approve or correct, the
  CLIs are the same.

## Stage 1: core, simulator, Scout-calls strategy (mode off)

- Built: ledger, bars, Scout feed, simulator, reconcile, sizing, risk flags, caps, the scout_calls
  strategy, the daily run, HALT, live approval, the go-live report, both gates, the board, loops.
- What it means for you: nothing trades. `mode = "off"` is committed; switching to `simulate` is
  your change in bob.toml. Paper and live need adapters that are not built yet.

## Scaffold

Folder created by new_bob.py. Nothing runs yet; see PLAYBOOK.md section 3 for first launch.
