# Tipster tracker

Where this Bob stands. Every step, check result and finding gets a row when it happens.

| when | step | result and evidence |
|---|---|---|
| scaffold | Folder created by new_bob.py | First launch (PLAYBOOK.md section 3) not started |
| 2026-10-20 | Step 0 odds provider test (plan Task 0) | blocked: no `ODDS_API_KEY` in the env file (`envfile.py --check` lists ALPACA keys only). `[tipster]` keeps the plan's defaults (oddspapi, 250/month, no history) until it runs |
| 2026-10-20 | Scaffold, `[tipster]` checks, fetch with host allowlist (plan Task 1) | `./selftest.sh` 4 ok; core `./selftest.sh` exit 0; `bobcfg.py` OK. Breaking the host check failed `test_bookmaker_host_refused`; breaking the share cap failed `test_tip_share_not_above_round_share` |
