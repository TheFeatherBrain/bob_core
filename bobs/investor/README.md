# Investor Scout

A read-only Bob that screens US-listed stocks (NYSE, Nasdaq) every night from SEC filings and Alpaca
market data, writes cited theses for the names that rise to the top, and keeps an append-only ledger
of its calls, each scored against SPY and QQQ at 3, 6 and 12 months. It never trades.

Design: `docs/superpowers/specs/2026-10-13-investor-scout-design.md`. Shared rules: `../../CORE.md`.

## Run

Set the Alpaca paper keys (`ALPACA_PAPER_KEY_ID`, `ALPACA_PAPER_SECRET_KEY`) in your environment
or in the file `[runtime] env_file` names, outside every repo (never in a file here). `launch.sh`
and the supervisor load that file; in a plain terminal, `python3 ../../harness/envfile.py --check`
shows which keys it holds. Then from this folder:

| what | command |
|---|---|
| refresh data | `python3 harness/prices.py --refresh` and `python3 harness/edgar.py --sync` |
| market conditions (context only) | `python3 harness/macro.py --refresh`, view: `--show` |
| results by group and sector | `python3 harness/ledger_score.py --by-signal` (needs 20 calls with a scorecard) |
| data fetched but never used | `python3 harness/data_audit.py --report` |
| screen | `python3 harness/screen.py --run`, one card: `--card NVDA` |
| record a verdict | `python3 harness/calls.py verdict NVDA --verdict buy-worthy --lane numbers` |
| score the ledger | `python3 harness/ledger_score.py --run` |
| weekly digest | `python3 harness/digest.py --write` |
| board | `python3 harness/scout_board.py` (port 8791) |
| tests | `./selftest.sh` |

## Where things live

- `harness/`: the CLIs, one concern each, with `test_*.py` beside them. `harness/hooks/`: the gates.
- `theses/`: one Markdown thesis per ticker. `digests/`: weekly digests.
- `data/` (git-ignored): `evidence/` (every read, never overwritten) and `state/` (caches, the screen,
  `calls.jsonl`, `ledger_results.json`).

## Known limits

- A citation is checked to exist, not to say what the line claims.
- The insider factor is "could not tell" until 90 days of Form 4s are read (`edgar.py --backfill-form4`).
- Evidence grows every night. `python3 harness/evidence_report.py` shows the size by month and which
  old folders nothing cites; deleting them is a person's decision (the weekly digest reports the total).
- Shares for companies with several share classes fall back to diluted weighted-average shares
  (frames carry no per-class cover counts); companies with neither are counted as `no_shares`.
- `protect_gate`'s shell check is best effort: a shell command can always hide a path. The
  file-tool check and the CLIs' own refusals are the firm guards.
- Market conditions (FRED) are context only: shown and stored with each call, never in a score.
- Growth and quality rank within the company's Fama-French 12 sector once its SIC code is cached
  and the sector has 15 known values; until then they rank against all stocks, and the card says so.
- Altman Z is not computed for SIC 6000 to 6799 (banks, insurers, real estate); the card says why.
