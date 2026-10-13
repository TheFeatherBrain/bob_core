# Investor Scout: design

Date: 2026-10-13. Status: approved in conversation, awaiting written-spec review.

## 1. Intent

**What the person asked for.** An investor Bob that finds good US stocks for the long term before they move (Palantir, NVIDIA as examples), and later a separate Bob that trades short time frames.

**Decisions made in conversation.**
- Build the long-term **Scout** first. The short-term **Trader** is a later, separate Bob, paper trading only until it beats buy-and-hold net of costs on criteria agreed in advance.
- The Scout is **read-only and never trades**.
- Universe: **US-listed only** (NYSE, Nasdaq).
- Output: the local **board** plus a **weekly digest**. Telegram comes later; the digest stays channel-neutral so it can plug in.
- Success measure: every buy-worthy call is scored against **both SPY and QQQ** total return at **3, 6 and 12 months**. 12 months is the verdict; 3 and 6 are labelled interim.
- Prices from **Alpaca market data** (free paper account, read-only key).
- Candidate discovery: **two lanes, one scorecard** (numbers lane plus story lane, both through the same gate).
- Market-cap floor lowered from $300M to **$200M**.

**Assumptions (not stated by the person).**
- The person places any real trade themselves; the Scout's output is research, not advice to act automatically.
- Times are the person's local time (Europe); loops run after the US close.

**Why this shape.** Published evidence (Alpha Arena, StockBench, FINSABER) shows LLM agents mostly fail to beat buy-and-hold at short-term trading, and do better as readers of filings and appliers of explicit rules. Big long-term winners are rare (about 4% of US stocks created all net wealth since 1926; Bessembinder 2018), so the Scout keeps a scored basket and an honest track record rather than betting on single stories.

## 2. Placement and reuse

- New Bob folder `bob_core/bobs/investor`, inside the core repo for now (the person's choice; split into its own repo later). Scaffolded with `automation/new_bob.py --out bobs/investor`; `{core}` renders as `../..`. **Core code is not changed for v1.**
- Boundary inside one repo: core stays domain-free outside `bobs/`; nothing in `harness/`, `automation/`, `templates/` or `CORE.md` may import from or mention `bobs/`. Core's `selftest.sh` only globs top-level `harness/*.py`, so it does not pick up Bob modules; the Bob gets its own test runner.
- Repo hygiene: `.gitignore` gets entries for Bob runtime data (evidence dir, digests if large, any local key file). `[paths] data_root` may point outside the repo instead. The project `CLAUDE.md` is updated to describe `bobs/` (it currently says Bobs are sibling folders).
- `fleet.toml` gets the Bob's entry from `new_bob.py`; review the absolute path before committing.
- Later split: `git subtree split --prefix=bobs/investor` (or `git filter-repo`) keeps history; after the split `{core}` changes from `../..` to `../bob_core`, so re-render the imports and hook paths that use it.
- `new_bob.py` refuses a non-empty `--out`. This spec already lives in `bobs/investor/`, so the plan scaffolds into an empty temp folder and moves the result in (or moves `docs/` aside first). Do not change `new_bob.py` for this.
- Bob-specific settings go in a `[scout]` manifest section, read through `bobcfg.get("scout.<key>")`. `bobcfg` validates known sections and ignores unknown ones (to confirm with a test in the plan).
- **From `trader`** (`george/trader/app/engine`), port these stdlib-only files into the Bob's `harness/fundamentals/`, with their tests rewritten in core's plain-assert style: `fundamental/ratios.py`, `growth.py`, `quality.py`, `valuation.py`, `scoring/factors.py`, `composite.py`. Not ported: `scoring/weights.py` (pandas), `scoring/calibration.py` (numpy), and all technical, risk, regime and options modules (left for the Trader Bob). `trader` stays as a reference, not a dependency.
- **Not reused:** OpenProphet (CC BY-NC licence; prompt-only limits; agent can raise its own permissions) and the mcpmarket "autonomous-trading" skill (stubbed safety code). Both are kept as examples of what the guards in section 6 prevent.

## 3. Components

All stdlib Python, in the Bob's `harness/`, each with `--selftest` or a `test_*.py` suite, following core's module shape.

| Module | Job |
|---|---|
| `edgar.py` | SEC client. User-Agent with contact (`scout.sec_contact`), under 10 req/s (token bucket at 8/s), every fetch saved via `evidence.save`. Covers `company_tickers_exchange.json`, frames, companyfacts, submissions, daily index, Form 4. |
| `prices.py` | Alpaca data API only: daily bars for the universe plus SPY and QQQ (batched multi-symbol), market calendar. Saved as evidence. Staleness check against the calendar. |
| `fundamentals/` | Ported `trader` code (section 2). |
| `signals.py` | Revenue growth and acceleration; earnings and revenue surprise; momentum; 52-week-high proximity; opportunistic insider buying. |
| `scorecard.py` | Deterministic score per ticker (section 5). Each factor carries value, percentile and evidence id. |
| `screen.py` | Numbers lane: builds the universe, scores it, writes a ranked shortlist and a diff against the previous run. |
| `calls.py` | Prediction ledger, append-only `calls.jsonl`. Records and refuses calls (section 6). |
| `ledger_score.py` | 3/6/12-month excess return vs SPY and QQQ per call and for the equal-weight basket. Writes a results file the board and digest read. |
| `thesis_lint.py` | Refuses a thesis with an uncited number or an evidence id that does not resolve. |
| `digest.py` | Weekly digest as `digests/<date>.md` plus `<date>.json`. |
| `scout_board.py` | Board panel (`[board] panels = ["scout_board"]`), built on `board_shell.BaseHandler`. |

Markdown that steers the agent: `CLAUDE.md` (imports `@../../CORE.md`), `templates/thesis.md`, and loop files under `loops/`. Theses live at `theses/<TICKER>.md`.

Manifest highlights:
- `[bob] purpose` says read-only, never trades.
- `[credentials] ALPACA_PAPER = { access = "read-only", why = ... }`. Key and secret come from environment variables, never from the manifest; `redact.py` scrubs them from evidence.
- `[loops] templates = ["health-checks", "lessons-review"]`, `own = ["loops/nightly-screen.txt", "loops/story-lane.txt", "loops/weekly-digest.txt"]`, each with a `[loops.schedules]` entry.
- No `[slack]` section.

## 4. Data flow and loops

Rule: scripts do the numbers, Claude does the reading. Each loop runs its CLIs, checks for `OK`, then does judgement work.

**Universe.** NYSE and Nasdaq listings from `company_tickers_exchange.json`. Floors in `[scout]`: market cap at least **$200M** (SEC shares outstanding times latest close), price at least **$5**, average daily dollar volume at least **$1M**.

**`nightly-screen`** (Tue to Sat, early morning local, after the US close and SEC's overnight index).
1. `prices.py --refresh`.
2. `edgar.py --sync`: yesterday's daily index, new Form 4s, new quarterly figures (frames only when a new quarter appears).
3. `screen.py`.
4. `ledger_score.py`.
5. Claude: for at most **5 new names a day** entering the top 20, read the latest 10-Q/10-K and earnings release, write or update `theses/<TICKER>.md` (bull case, bear case, reverse DCF, what would change the view; every claim cites an evidence id), set a verdict, and run `calls.py --record` if it became buy-worthy.

**`story-lane`** (Mon, Wed, Fri). Claude scans new 8-Ks for universe names (items 1.01, 2.02, 7.01) and may use web search, saving each result as evidence. At most **3 proposals a week**, each with a cited source, through the same scorecard and thesis template, tagged `lane=story`.

**`weekly-digest`** (Saturday morning). `digest.py`: new buy-worthy calls, upgrades and downgrades, calls that reached 3/6/12 months, Scout vs SPY vs QQQ with n, numbers lane vs story lane.

**Core loops kept:** `health-checks`, `lessons-review`.

**Ledger rules.**
- A call is recorded when a verdict **becomes** buy-worthy.
- Entry price is the **next session's close**, so the result never uses information the Scout already had.
- Each call is scored at fixed 3/6/12-month horizons. A later negative view is recorded as `withdrawn` and the call is still scored.
- Append-only; corrections are new lines that reference the old one. Writes use `oscompat.lock`; torn lines are skipped and reported.
- Each entry records `lane` and `scorecard_version`.

## 5. Scorecard

Each factor is a percentile rank (0 to 100) within the day's universe. The score is the weighted average of the factors that have data. **Coverage** is the share of total weight that had data.

| Group | Weight | Signal | Source |
|---|---|---|---|
| Growth | 30% | TTM revenue growth, plus acceleration (latest quarter's YoY growth minus the previous quarter's YoY growth) | XBRL |
| Surprise | 15% | Standardised EPS and revenue surprise against a seasonal random walk (same quarter last year, scaled by the volatility of that change over the prior 8 quarters) | XBRL |
| Momentum | 20% | 12-month return excluding the latest month; proximity to 52-week high | Alpaca |
| Quality | 20% | Piotroski F-score | XBRL |
| Insider | 15% | Net open-market purchases (code P) in the last 90 days by opportunistic insiders (no routine same-month pattern in the prior 3 years; Cohen, Malloy and Pomorski 2012), excluding 10b5-1 plan trades, with a cluster bonus for several insiders | Form 4 |

**Not in the score.**
- DCF. The thesis instead runs a **reverse DCF** stating the growth the price implies; Claude judges its plausibility with evidence.
- Altman Z is a **distress veto**: below 1.8, the best verdict is "watch". Not valid for banks and insurers ("could not tell").

**Verdicts.**
- **buy-worthy**: score in the top 20% of the universe, coverage at least 70%, no distress veto, thesis with cited bull and bear cases.
- **watch**: interesting but fails part of the gate (always the outcome for a story-lane name that fails the numbers).
- **pass**: scored and rejected.
- **could not tell**: coverage below 70%, whatever the score.

**Weights.** Fixed in `[scout]`, no automatic fitting in v1. Changing them bumps `scorecard_version`; old calls keep their version. The Scout may propose new weights as a lesson citing ledger evidence; only the person approves.

## 6. Errors and guards

**Data failures.**

| Situation | Behaviour |
|---|---|
| SEC 403 | Stop all SEC calls this run, `FAIL: SEC 403 (check User-Agent / rate)`, no retry |
| Timeout or 5xx | Up to 3 retries with backoff, then `FAIL` for that piece |
| SPY or QQQ missing, or over 5% of the universe missing prices | `screen.py` fails; no new calls that day; digest records the gap |
| Company lacks recent XBRL figures | That factor is "could not tell"; coverage drops |
| Price older than one trading day (Alpaca calendar) | Marked stale on the board; refused as entry price |
| No price at a horizon date | Cell stays "pending (no price)"; never back-filled with an older price |
| Delisted or acquired | Scored at last traded price, marked `delisted` |
| Torn ledger line | Skipped and reported |

Splits and dividends: handled by adjusted bars (to be confirmed in the live smoke check).

**Guards.**
- `calls.py --record` refuses unless: the ticker scored today and passed the gate; `theses/<TICKER>.md` exists with bull and bear sections; every cited evidence id resolves; the entry price is not stale.
- `thesis_lint.py` refuses an uncited number. It checks a citation exists, not that it is correct (core's known limit: no machine claim verification).
- PreToolUse hooks in the Bob's `harness/hooks/` deny Edit and Write on `calls.jsonl`, the `[scout]` weights, the evidence dir and the hooks dir. These are state-protecting, so they refuse (fail closed).
- Read-only by construction: no Alpaca MCP server installed; `prices.py` only uses the data host; a Bash guard refuses commands naming Alpaca order endpoints.
- Board and digest numbers (n, interim labels, excess returns) come from `ledger_score.py`, not from Claude's prose.

## 7. Board

- Watchlist: shortlist with score, coverage, verdict, lane, link to thesis.
- Performance panel: three lines rebased to 100 at the first call (Scout equal-weight basket, SPY total return, QQQ total return); toggles for 3/6/12 months and all time; n shown; "too few calls to judge" banner below 20 scored calls; interim results labelled; stale prices marked.
- Per-call table: ticker, call date, entry, excess vs SPY and QQQ at 3/6/12 months (green, red, grey for pending), links to thesis and evidence.
- Charts are inline SVG generated by the board (no external libraries).

## 8. Testing

- Recorded fixtures (small JSON from SEC and Alpaca: one companyfacts, one Form 4, one frames day, SPY/QQQ bars, calendar). No network in tests.
- Golden numbers: Piotroski, revenue acceleration, surprise and insider signal worked by hand for one real company; code must match.
- Ported `trader` fundamentals tests in plain-assert style.
- Ledger maths on synthetic series with known answers, including next-session entry, delisting, missing horizon price, withdrawn calls.
- Refuse/allow pairs for every guard: `calls.py`, `thesis_lint`, each hook path, the Bash order guard. Each guard is broken deliberately with the editor, its test seen failing, then reverted.
- Manual live smoke checks: `edgar.py --selftest-live`, `prices.py --selftest-live` (one company, SPY; confirms adjusted bars).

## 9. Done when (v1)

1. The Bob's tests pass and core's `./selftest.sh` still passes.
2. A live `nightly-screen` completes: ranked shortlist with evidence folders on disk.
3. At least one thesis passes `thesis_lint`; at least one call is in the ledger with entry at the next session's close.
4. The board shows the watchlist, theses, the Scout vs SPY vs QQQ chart with n and the banner, and the per-call table.
5. A weekly digest is generated.
6. Each hook is seen refusing in a live session.
7. Loops run under Task Scheduler on Windows (first live check of core's unverified Windows supervisor path).

## 10. Out of scope (v1)

- Trader Bob (short-term, paper-only, own manifest, credentials and order gate).
- Telegram (planned as a generic core channel) and Slack.
- Automatic weight fitting.
- Non-US listings.
- Historical backtest of the numbers lane (needs point-in-time data and delisted companies; its own spec, v1.1).
