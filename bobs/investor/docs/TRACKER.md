# Investor Scout tracker

Where this Bob stands. Every step, check result and finding gets a row when it happens.

| when | step | result and evidence |
|---|---|---|
| scaffold | Folder created by new_bob.py | First launch (PLAYBOOK.md section 3) not started |
| 2026-10-14 | Scout upgrades (scorecard v2) built per docs/superpowers/plans/2026-10-14-scout-upgrades.md | selftest result and live checks in the next rows |
| 2026-10-14 | v2 check 1: all suites (after review fixes, 52bf46a) | core `./selftest.sh` 17 of 17 ok; `bobs/investor/selftest.sh` 22 of 22 ok |
| 2026-10-14 | v2 check 2: live `macro.py --refresh` | FAIL: `could not tell T10Y2Y, BAMLH0A0HYM2, VIXCLS, NFCI, ICSA, UNRATE, CPIAUCSL, FEDFUNDS; stale=[]` after 31s. First read timed out, rest "FRED unreachable; not asked". No evidence saved (a timeout saves none). Cause: FRED's CSV endpoint stalls for User-Agent `investor-scout`; by curl the same URL answers 200 in under 0.5s for `investor-scout/1.0 (gk@gmail.com)`, the SEC contact string and `Python-urllib/3.12`. Changing `macro.HEADERS` is the person's call |
| 2026-10-14 | v2 check 2 rerun: User-Agent set to `investor-scout/1.0 (gk@gmail.com)` (George's choice) | `OK series=8 stale=[]`; eight lines each with evidence, e.g. VIXCLS 15.21 on 2026-10-12 [ev:2026-10-14/fred-vixcls-104740-306bddf8], CPI year on year 3.35 for 2026-08-01 [ev:2026-10-14/fred-cpiaucsl-104742-aa4d11e0] |
| 2026-10-14 | v2 check 3: live sync and screen | blocked: Alpaca keys not in environment (`ALPACA_PAPER_KEY_ID`, `ALPACA_PAPER_SECRET_KEY`). SIC feed verified once by hand: NVIDIA atom read 17332 bytes, `assigned-sic` 3674 [ev:2026-10-13/sec-sic-check-220927-ba9285c5] |
| 2026-10-14 | v2 check 4: `data_audit.py --report` | OK; fetched but never read: none; FRED series not shown: none; coverage: no screen yet (check 3 blocked) |
| 2026-10-15 | Keys from `[runtime] env_file` (core `envfile.py`, launch.sh, supervisor) | `envfile.py --check`: `OK C:\Users\georg\src\george\bob-secrets.env keys=ALPACA_PAPER_KEY_ID,ALPACA_PAPER_SECRET_KEY`; `launch.sh` with a fake `claude` saw both keys set (values not printed). Core selftest 18 of 18, investor 22 of 22, trader 18 of 18. Check 3 (live sync) unblocked, not yet run |
| 2026-10-14 | v2 board look | not done: browser extension not connected. Served HTML has the Market conditions heading and list; `/api/state` macro = not refreshed line. A person should look once |
