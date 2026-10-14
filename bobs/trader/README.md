# Trader

Acts on the Investor Scout's buy-worthy calls (scorecard v2) through a broker-neutral core:
fixed sizing, hard caps, exit rules set at entry, and an exact check of its books against the
broker every run. `mode = "off"` until a person changes it. Stage 1 has only a simulator; paper and
live adapters are written when paper trading starts.

Design: `docs/superpowers/specs/2026-10-14-trader-stage1-design.md`. Shared rules: `../../CORE.md`.

## Run

Set the Alpaca data keys in your environment (never in a file here): `ALPACA_PAPER_KEY_ID`,
`ALPACA_PAPER_SECRET_KEY`. Then from this folder:

| what | command |
|---|---|
| refresh prices | `python3 harness/bars.py --refresh` |
| daily run | `python3 harness/trade.py --run` |
| books | `python3 harness/ledger.py book`, `python3 harness/ledger.py summary --days 7` |
| stop new buys | `python3 harness/halt.py --reason "..."` (delete `data/state/HALT` yourself to resume) |
| go-live report | `python3 harness/promotion.py --report` |
| approve a live order (you only) | `python3 harness/approve.py ORDER_ID` |
| correct the books (you only) | `python3 harness/ledger.py correct --note "..." --clears MISMATCH_ID ...` |
| board | `python3 harness/trader_board.py` (port 8792) |
| tests | `./selftest.sh` |

## Modes

| mode | what happens |
|---|---|
| off | nothing runs; no network or broker call |
| simulate | orders filled by the simulator at the next open, with costs |
| paper | orders to the paper broker (adapter not built yet) |
| live | orders proposed only; each waits for `approve.py`; refused without a passing go-live report from the last 30 days unless `override_promotion` is set |

## Known limits

- `fetch.py` and `marketclock.py` are copies of the Scout's; a fix in one must be made in both.
- The simulator credits no dividends (conservative) and fills every order at the next raw open.
- Stops are checked on daily closes; there are no stop orders at the broker in stage 1.
- `order_gate`'s shell check is best effort, like the Scout's: the CLIs' own refusals (terminal
  required, typed-back ids, caps) are the firm guards.
