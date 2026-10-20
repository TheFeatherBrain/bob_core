# Tipster tracker

Where this Bob stands. Every step, check result and finding gets a row when it happens.

| when | step | result and evidence |
|---|---|---|
| scaffold | Folder created by new_bob.py | First launch (PLAYBOOK.md section 3) not started |
| 2026-10-20 | Step 0 odds provider test (plan Task 0) | blocked: no `ODDS_API_KEY` in the env file (`envfile.py --check` lists ALPACA keys only). `[tipster]` keeps the plan's defaults (oddspapi, 250/month, no history) until it runs |
| 2026-10-20 | Scaffold, `[tipster]` checks, fetch with host allowlist (plan Task 1) | `./selftest.sh` 4 ok; core `./selftest.sh` exit 0; `bobcfg.py` OK. Breaking the host check failed `test_bookmaker_host_refused`; breaking the share cap failed `test_tip_share_not_above_round_share` |
| 2026-10-20 | Core `telegram.py` and `[telegram]` (plan Task 2) | core `./selftest.sh` 22 ok, exit 0; removing the token scrub failed `test_error_never_contains_token` and `--selftest`. Live send blocked: no bot yet (the person creates it with BotFather and adds `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` to the env file) |
| 2026-10-20 | Euroleague stats client (plan Task 3) | 11 of 11 tests; live `stats.py --sync E2026`: `OK season=E2026 played=60 fetched=60 refused=0`. Findings: the API answers 403 to Python's default User-Agent (a named one is sent); `isNeutralVenue` is set on only 2 of 4 E2024 Final Four games and 0 of 4 in E2015, so every FF game counts as neutral |
| 2026-10-20 | Ratings, model, calibration code (plan Tasks 4 to 6) | suites pass; the live calibration run waits for the E2015 to E2025 box-score sync (in progress) |
| 2026-10-20 | History sync E2015 to E2025 | 3,377 played games, every box score fetched, 0 refused (`stats.py --sync` per season) |
| 2026-10-20 | Calibration gate, live (plan Task 6, spec 4.4) | `calibrate.py --run`, 1,063 games of E2023 to E2025, walk-forward: **moneyline PASS, handicap PASS, total FAIL** (total RMSE 17.26 vs baseline 17.07; observed share over the line about 8 points above the model in every band: the model's totals run about 3 points low). Totals are not tipped. Live params fitted on E2015 to E2025: home_adv 3.88, k 12, double_adj 0 (not kept), sigma_margin 11.82, sigma_total 17.24. Band and ECE rules allow for sampling noise (ledger ruling; the person should confirm). No past odds, so no edge or CLV report |
| 2026-10-20 | Odds board, team aliases, request budget (plan Task 7) | budget, alias table (20 clubs, English and Greek), board assembly and key hiding built and tested with a fake provider. The OddsPapi parser is not built: its docs do not show how handicap and total lines are encoded, so it is written from the step 0 recorded responses. Until then `odds.py --round` refuses before spending a request |
| 2026-10-20 | Pricing, ledger, discovery, review (plan Tasks 8 to 10) | suites pass; discovery and review run end to end against a fake provider |
| 2026-10-20 | News watch and rechecks (plan Task 11) | four feeds recorded as fixtures and parsed; finding: the official injury report page answers scripts with a bot challenge (HTTP 429, `X-Vercel-Mitigated: challenge`), so it is not read by script; the Eurohoops injury-report article (full text in its feed) is flagged instead. Rechecks never raise a stake; a grown edge is labelled "the market moved" |
| 2026-10-20 | Settlement, outbox, board, digest, gates (plan Tasks 12 to 15) | suites pass; the board was checked in a browser against a demo ledger (every section showed the numbers written down beforehand) |
| 2026-10-20 | Loops, domain rules, lessons, README (plan Task 16) | five own loops, `test_loops.py` 6 of 6; two lessons seeded (`no-guessed-team`, `news-is-not-a-number`) |
