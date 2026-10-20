# Tipster release notes

Newest first. Each change to this Bob or its tooling gets an entry saying what changed and what it
means for someone who relies on this Bob's output. "Nothing changes for you" is information too.

## Totals are tipped

- The totals model ran about 3 to 5 points low in recent seasons: team priors leaned on last
  season's scoring while scoring rose, and the pace x ratings formula missed totals by more than
  a plain average. Totals now come from points scored and allowed per game, shrunk toward each
  team's prior re-centred on this season's scoring level.
- The calibration check now passes all three markets on 2023-24 to 2025-26, including under the
  spec's strict rule. Totals edges will be rarer and smaller than side edges.
- What it means for you: over/under tips can now arrive alongside winner and handicap tips.

## Odds provider wired

- Step 0 is done (`docs/step0/FINDINGS.md`): OddsPapi on the free plan has Euroleague prices from
  bet365, Stoiximan and Pinnacle; a busy month needs about 200 of its 250 requests.
- `odds.py` now reads real odds. Closing prices for CLV come free from the provider's history
  when tips are settled, instead of a snapshot before tip-off.
- Mode off now stops every reader and sender, not only discovery; a vetoed game stays vetoed.
- What it means for you: Tipster can run a real round once you switch `mode = "on"`. Telegram is
  confirmed working.

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
