# Trader tracker

Where this Bob stands. Every step, check result and finding gets a row when it happens.

| when | step | result and evidence |
|---|---|---|
| scaffold | Folder created by new_bob.py | First launch (PLAYBOOK.md section 3) not started |
| 2026-10-14 | Stage 1 built per docs/superpowers/plans/2026-10-14-trader-stage1.md | selftest and the simulate week in the next rows |
| 2026-10-14 | Every suite (plan Task 15 step 1) | core `./selftest.sh` 17 ok, `bobs/investor` 22 ok, `bobs/trader` 18 ok; exit 0 in all three |
| 2026-10-14 | Simulated week (plan Task 15 steps 2 to 5) | blocked: Alpaca keys not in environment of the build session; `mode = "off"` unchanged. Needs the person to set `mode = "simulate"` locally and run five trading days |
| 2026-10-15 | Keys from `[runtime] env_file` (commit 18995e2) | `bob.toml` names `C:/Users/georg/src/george/bob-secrets.env`; `launch.sh` and the supervisor load `ALPACA_PAPER_KEY_ID`, `ALPACA_PAPER_SECRET_KEY` from it. Core selftest 18 of 18, trader 18 of 18. The key blocker on the simulated week is gone; it still waits for the person to set `mode = "simulate"` |
| 2026-10-15 | Price reads end at the last completed session; SEC "no such file" 403 is not found (commit 22448e3) | `bars.py` and `fetch.py` changed with tests (`test_bars.py`, `test_fetch.py`); trader selftest 18 of 18. Not run live: mode is off |
| 2026-10-19 | Tracker caught up | the two 10-15 rows above were missing (written from the commits and release notes) |
