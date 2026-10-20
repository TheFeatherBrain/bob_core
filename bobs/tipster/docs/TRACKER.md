# Tipster tracker

Where this Bob stands. Every step, check result and finding gets a row when it happens.

| when | step | result and evidence |
|---|---|---|
| scaffold | Folder created by new_bob.py | First launch (PLAYBOOK.md section 3) not started |
| 2026-10-20 | Step 0 odds provider test (plan Task 0) | blocked: no `ODDS_API_KEY` in the env file (`envfile.py --check` lists ALPACA keys only). `[tipster]` keeps the plan's defaults (oddspapi, 250/month, no history) until it runs |
| 2026-10-20 | Scaffold, `[tipster]` checks, fetch with host allowlist (plan Task 1) | `./selftest.sh` 4 ok; core `./selftest.sh` exit 0; `bobcfg.py` OK. Breaking the host check failed `test_bookmaker_host_refused`; breaking the share cap failed `test_tip_share_not_above_round_share` |
| 2026-10-20 | Core `telegram.py` and `[telegram]` (plan Task 2) | core `./selftest.sh` 22 ok, exit 0; removing the token scrub failed `test_error_never_contains_token` and `--selftest`. Live send blocked: no bot yet (the person creates it with BotFather and adds `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` to the env file) |
| 2026-10-20 | Euroleague stats client (plan Task 3) | 11 of 11 tests; live `stats.py --sync E2026`: `OK season=E2026 played=60 fetched=60 refused=0`. Findings: the API answers 403 to Python's default User-Agent (a named one is sent); `isNeutralVenue` is set on only 2 of 4 E2024 Final Four games and 0 of 4 in E2015, so every FF game counts as neutral |
