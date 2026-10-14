# Trader tracker

Where this Bob stands. Every step, check result and finding gets a row when it happens.

| when | step | result and evidence |
|---|---|---|
| scaffold | Folder created by new_bob.py | First launch (PLAYBOOK.md section 3) not started |
| 2026-10-14 | Stage 1 built per docs/superpowers/plans/2026-10-14-trader-stage1.md | selftest and the simulate week in the next rows |
| 2026-10-14 | Every suite (plan Task 15 step 1) | core `./selftest.sh` 17 ok, `bobs/investor` 22 ok, `bobs/trader` 18 ok; exit 0 in all three |
| 2026-10-14 | Simulated week (plan Task 15 steps 2 to 5) | blocked: Alpaca keys not in environment of the build session; `mode = "off"` unchanged. Needs the person to set `mode = "simulate"` locally and run five trading days |
