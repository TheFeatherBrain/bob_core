# Investor Scout

A read-only Bob that screens US-listed stocks (NYSE, Nasdaq) every night from SEC filings and Alpaca
market data, writes cited theses for the names that rise to the top, and keeps an append-only ledger
of its calls, each scored against SPY and QQQ at 3, 6 and 12 months. It never trades.

Design: `docs/superpowers/specs/2026-10-13-investor-scout-design.md`. Shared rules: `../../CORE.md`.

## Run

Set the Alpaca paper keys in your environment (never in a file here): `ALPACA_PAPER_KEY_ID`,
`ALPACA_PAPER_SECRET_KEY`. Then from this folder:

| what | command |
|---|---|
| refresh data | `python3 harness/prices.py --refresh` and `python3 harness/edgar.py --sync` |
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
- Evidence grows every night; there is no retention policy yet.
