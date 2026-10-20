# Tipster

Finds Euroleague bets with an edge over bet365 and Stoiximan, has Claude review each one (keep,
halve or veto, with evidence), and sends the kept ones to you on Telegram. Every tip is placed in a
simulated EUR 1,000 wallet on two tracks (what the model found, and what Claude kept) and scored by
profit and closing line value (CLV). It never places a bet. `mode = "off"` until you change it.

Design: `docs/superpowers/specs/2026-10-20-tipster-design.md`. Plan:
`docs/superpowers/plans/2026-10-20-tipster.md`. Shared rules: `../../CORE.md`.

## Run

Put `ODDS_API_KEY`, `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` in the file `[runtime] env_file`
names, outside every repo (never in a file here). `launch.sh` and the supervisor load it;
`python3 ../../harness/envfile.py --check` shows which keys it holds. Then from this folder:

| what | command |
|---|---|
| sync stats | `python3 harness/stats.py --sync E2026` |
| calibration gate | `python3 harness/calibrate.py --run`, saved result: `--report` |
| odds for the round | `python3 harness/odds.py --round` (spends one request) |
| request budget | `python3 harness/budget.py --status` |
| discovery sweep | `python3 harness/discover.py --run` (only when due) |
| review a candidate | `python3 harness/review.py c-3 --keep\|--halve\|--veto --reason "..." --evidence PATH` |
| news | `python3 harness/news.py --poll`, one game: `--recent E2026-69` |
| recheck after news | `python3 harness/recheck.py TIP --reason "..." --evidence PATH [--withdraw \| --no-change]` |
| closing snapshots | `python3 harness/closing.py --due` |
| settle | `python3 harness/settle.py --run` |
| send messages | `python3 harness/notify.py --flush` |
| stop new tips | `python3 harness/halt.py --reason "..."` (delete `data/state/HALT` yourself to resume) |
| weekly digest | `python3 harness/digest.py --write` |
| board | `python3 harness/tipster_board.py` (port 8793) |
| tests | `./selftest.sh` |

## Where things live

- `harness/`: the CLIs, one concern each, with `test_*.py` beside them; `harness/fixtures/`: recorded
  responses the tests run on. `harness/hooks/`: `protect_gate.py`, `network_gate.py`.
- `data/` (git-ignored): `evidence/` (every read, never overwritten), `state/` (`tips.jsonl`,
  `odds-requests.jsonl`, `calibration.json`, `model_params.json`, `outbox.jsonl`, caches) and
  `digests/`.

## Known limits

- **Odds cost one request per game.** OddsPapi gives Stoiximan prices only per game, so a sweep
  reads the fixture list once and each game of the next round once (about 28 requests a round,
  about 200 in a busy month, of 250 free). Closing prices come free from the provider's history at
  settlement (`closing_from_history = true`). Details: `docs/step0/FINDINGS.md`.
- Player props exist (Stoiximan) but are not tipped in v1; they need their own plan.
- Totals come from points for and against, not pace x ratings, and beat a plain season average
  only narrowly (calibration RMSE 17.02 against 17.07): expect few totals tips.
- The official injury report page refuses scripts (a bot challenge); the round's report is read
  through the Eurohoops article that carries it.
- Travel is not modelled (no club locations in the feed). The double-round flag is fitted and was
  not kept (it did not lower the out-of-sample error).
- News matches a game by club name or a player's full name (or a surname of 7 letters or more);
  Greek inflected names ("Ολυμπιακού") are not matched.
- `protect_gate`'s shell check is best effort: a shell command can always hide a path. The
  file-tool check and the CLIs' own refusals are the firm guards.
