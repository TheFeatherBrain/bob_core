# TICKER: Company name

Verdict: buy-worthy | watch | pass | could not tell
Lane: numbers | story
Date: YYYY-MM-DD

## Scorecard

Paste the output of `python3 harness/screen.py --card TICKER` here, unchanged.

## Bull case

- One claim per line, ending with its evidence: revenue grew 94% year on year [ev:2026-10-13/sec-frame-cy2026q2-revenues-063012-1a2b3c4d]

## Bear case

- At least one cited claim. A thesis with no bear case is not finished.

## Reverse DCF

- `python3 harness/fundamentals/valuation.py --reverse --target <market cap> --fcf <cash flow>` and its inputs, cited.
- Then: is that growth believable, and why (cited)?

## What would change my mind

- Observable, dated conditions: "two quarters of revenue growth under 20%", not "if things get worse".

## Sources

- Story lane only: the filing or article that started this, as [ev:...].
