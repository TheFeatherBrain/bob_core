# Trader Bob, stage 1: design

Date: 2026-10-14. Status: approved in conversation, awaiting written-spec review.

## 1. Intent

**What the person asked for.** A Bob that trades US stocks, built now and switched off until paper
trading starts. It grows in two stages: stage 1 acts on the Investor Scout's calls; stage 2 adds
its own short-term strategy. The ideas come from comparing this repo with
`temple-stuart-accounting-main` (lots matched oldest-first, checking records against the broker,
position sizing with a no-trade floor, pre-trade risk flags, exit rules at entry); no code is
copied from it (AGPL, TypeScript).

**Decisions made in conversation.**
- A **separate Bob** (`bobs/trader`). The Scout stays read-only; its `order_gate` is not changed.
- **Both, in stages.** One shared core (guards, ledger, broker interface, caps, reconciliation)
  with strategies as plug-ins, each with its own budget and ledger tag. This spec is stage 1: the
  core plus the `scout_calls` strategy. Stage 2 (short-term) gets its own spec and must pass a
  backtest before it may run in paper.
- **Broker-neutral.** The core talks to a broker interface. The only adapter built now is a
  simulator. A real paper adapter (and later a live one) is written when paper trading starts.
- **Mode per stage**, in the manifest: `off` (default, nothing runs), `simulate` and `paper`
  (orders placed automatically within the caps), `live` (orders only proposed; each waits for the
  person's approval).
- **Go-live bar:** at least 6 months in paper and 20 closed trades; return after costs beats SPY's
  total return over the same dates; maximum drawdown no worse than SPY's; zero cap breaches and
  zero unresolved reconciliation mismatches. The bar lives in the manifest; changing it while in
  paper restarts the 6-month clock. The Bob reports; only the person changes the mode.
- Stocks only. No tax features (the person is in Greece).

**Assumptions (not stated by the person).**
- Stage 1 acts only on Scout calls made under scorecard v2 or later (lines with `card` and
  `macro`); older calls are ignored.
- One run a day after the Scout's nightly screen; no intraday runs. Stops are checked on daily
  closes, orders are for the next US open.
- The simulator does not credit dividends (understates returns: conservative). Paper and live take
  dividends from the broker's activity feed.
- Whether Alpaca opens live accounts for Greek residents is unchecked; it matters only for the live
  adapter, which is out of scope here.

## 2. Placement

- New Bob folder `bob_core/bobs/trader`, scaffolded with `automation/new_bob.py` into an empty temp
  folder and moved in (this spec already lives in the target folder), as the Scout was. Board port
  8792 (8791 is the Scout's, per `fleet.toml`).
- Nothing in core imports from `bobs/`, and the Trader never imports from `bobs/investor`. It reads
  the Scout's files as data through `scout_feed.py`.
- The Scout's state folder is found from the Scout's own manifest (`[trader] scout_manifest =
  "../investor/bob.toml"`, read with `tomllib`, then `[paths] data_root` and `state_dir`), never
  hard-coded, so moving the Scout's data root does not silently blind the Trader.
- Bob-specific settings in a `[trader]` section, validated by `tradercfg.py` (core's `bobcfg`
  ignores unknown sections).
- Module shape, encoding rules, refuse/allow test pairs and the `testkit` pattern follow the Scout.

## 3. Components

All stdlib Python in `bobs/trader/harness/`, each with a `test_*.py` suite or `--selftest`.

| module | job |
|---|---|
| `tradercfg.py` | load and validate `[trader]`: mode, caps, costs, exit rules, throttle, go-live bar, strategy budgets. Refuses unknown modes, caps that cannot all hold (e.g. `base_weight > max_position`), budgets not summing to at most 1 |
| `bars.py` | daily bars from Alpaca market data (read-only key), saved as evidence: raw open/high/low/close/volume for fills, split-adjusted closes for stops and returns, and a dividend-adjusted SPY series for the go-live comparison. Its own host allowlist (data host and calendar only) |
| `scout_feed.py` | read the Scout's `calls.jsonl` and `macro.json`: open v2 calls, withdrawals, the card and macro on each. Torn lines skipped and counted. Never writes |
| `broker/base.py` | the interface: `account()`, `positions()`, `submit(order)`, `orders(since)`, `activities(since)` (fills, dividends, splits), and `MODE` (the one mode the adapter serves) |
| `broker/sim.py` | simulator, `MODE = "simulate"`: fills market orders at the next session's raw open, applies `slippage_bps` and `commission_per_order`, detects splits from the raw/split-adjusted ratio and reports them as activities; its own state file under `state/sim/` |
| `strategies/base.py` | strategy interface: `propose(context) -> list[Proposal]`, a name used as ledger tag and budget key |
| `strategies/scout_calls.py` | entries for new open v2 calls not held; exits by the rules stored at entry |
| `risk_flags.py` | flags for one proposed buy (section 4, step 7) |
| `sizing.py` | target value of a buy (section 4, step 8) |
| `caps.py` | hard limits (section 4, step 9) |
| `ledger.py` | append-only ledger, replay into lots/positions/cash/realized gains (oldest lot first); `correct` subcommand for the person only |
| `reconcile.py` | replayed positions and cash vs the broker's; any difference is a mismatch |
| `trade.py` | the daily pipeline (`--run`) |
| `approve.py` | the person approves a queued live order by id |
| `halt.py` | create `state/HALT` with a reason |
| `promotion.py` | the go-live report |
| `trader_board.py` | board on port 8792 |
| `hooks/protect_gate.py`, `hooks/order_gate.py` | the guards (section 5) |

## 4. Daily pipeline: `trade.py --run`

Runs at 08:30 Athens time, Tuesday to Saturday, after the Scout's nightly screen. Every step
appends to the ledger, refusals included.

1. **Mode.** `off`: print `OK mode=off` and exit 0, with no broker or network call. Otherwise load
   the adapter for the mode; an adapter whose `MODE` differs, or whose credential is missing,
   refuses before any network call. `live` additionally needs a passing go-live report from the
   last 30 days or `override_promotion = "<reason>"` in the manifest (printed on every run).
2. **Collect.** Ask the adapter for activities since the last run; append `filled`, `expired`,
   `dividend` and `split` lines.
3. **Reconcile.** Replayed positions and cash must equal the broker's exactly (shares exactly,
   cash to the cent). A mismatch prints `FAIL: reconcile ...`, appends a `mismatch` line and stops
   the run: no orders until the person appends a `correction` (with `ledger.py correct`). Fails
   closed.
4. **Inputs.** No calendar, stale bars for SPY or for any held or candidate symbol, or an
   unreadable Scout ledger: `FAIL`, no orders. Missing or stale market conditions do not stop the
   run; sizing then uses the floor throttle.
5. **Daily loss.** Equity down more than `daily_loss_limit` since the last run: no new buys today;
   sells still run. `state/HALT` present: same.
6. **Proposals, sells first.** `scout_calls` proposes:
   - sells by the rules stored on the position at entry: split-adjusted close at or below
     `entry * (1 - stop_loss)`; at or above `entry * (1 + take_profit)` when `take_profit` is set;
     `max_hold_days` reached; the Scout withdrew the call;
   - buys for v2 buy-worthy calls since the last run that are open and not held.
   A re-run of the same session finds its own proposals and does not propose twice.
7. **Risk flags** on each buy: `vix` (VIX above `vix_flag`, or VIX missing or stale), `insider`
   (insider group below 50 on the card), `liquidity` (order value above `max_adv_share` of the
   20-day average dollar volume). Each flag is recorded on the order; flags named in `block_flags`
   (default `["liquidity"]`) refuse it. Two flags from the comparison are left out because they
   could never fire: earnings in the holding period (no source of forward earnings dates in the
   Scout's data) and weak factor agreement (every v2 buy-worthy call already passed the Scout's
   agreement gate).
8. **Sizing.** `value = equity * base_weight * conviction * throttle`, rounded down to whole shares
   at the last close.
   - `conviction = 0.5 + 0.5 * (rank_pct - buy_percentile) / (100 - buy_percentile)`, from the
     call's card (0.5 at the Scout's buy line, 1.0 at the top).
   - `throttle = 1 - 0.25 * bad`, floor `throttle_floor` (default 0.25), where `bad` counts:
     VIX above `vix_flag`, 10Y minus 2Y below 0, high-yield spread above `hy_spread_flag`,
     financial-conditions index above 0. A series that is missing or stale counts as bad.
   - Below `min_order_value`: skipped, with the reason.
9. **Caps**, checked per order with the orders already accepted in this run counted in: position
   value at most `max_position` of equity; total invested at most `max_invested`; cash after the
   order at least `min_cash`; at most `max_orders_per_day`; the strategy's invested value at most
   its budget share. A breach refuses the order with the figures.
10. **Mode action.** `simulate`, `paper`: submit through the adapter, ledger id as client order id.
    `live`: append `queued`. The person runs `approve.py ORDER_ID`, which re-runs steps 3, 4 and 9
    at approval time and only then submits.
11. **Record and report.** Order lines carry the Scout card, the macro snapshot, flags, exit rules,
    sizing inputs and cap figures. Output `OK placed=N refused=M queued=K` or `FAIL: ...`; exit 1
    on FAIL.

Market orders for the next open only; no limit, stop or bracket orders at the broker in stage 1
(stops are evaluated by the pipeline on closes).

## 5. Guards

| guard | blocks | when unsure |
|---|---|---|
| `protect_gate` (hook) | agent edits to the ledger, `state/`, evidence, `bob.toml`, `harness/`, `.claude/`, the hooks; same shell-command coverage and stated limits as the Scout's gate | refuses |
| `order_gate` (hook) | agent tool calls naming a broker trading endpoint or live host, any broker MCP tool, running `harness/broker/*` directly or importing it via `python -c`, and any call naming `approve.py` or `ledger.py correct`. `trade.py --run` and `halt.py` are allowed | refuses |
| adapter mode binding | an adapter used in a mode other than its `MODE`, or without its credential | refuses |
| live gate | `live` without a passing go-live report from the last 30 days and without `override_promotion` | refuses |
| caps | section 4, step 9 | refuses |
| reconcile | section 4, step 3 | refuses |
| `approve.py`, `ledger.py correct` | stdin not a terminal; the typed-back order id does not match | refuses |
| HALT | new buys while `state/HALT` exists; the agent may create it (`halt.py --reason`), never remove it | refuses buys |
| risk flags | recorded; only `block_flags` refuse | warns |
| market conditions | missing or stale shrink sizes | warns |

**Mode history.** Every run appends `{at, mode, caps fingerprint, go-live bar fingerprint}` to
`state/mode_history.jsonl`, so every mode or cap change is on record with its date. Paper time for
the go-live bar counts from the first `paper` entry with the current bar fingerprint.

**Credentials** (names only; values from the environment):

| name | access | mode |
|---|---|---|
| `ALPACA_DATA` | read-only, daily bars and calendar (env `ALPACA_PAPER_KEY_ID` / `ALPACA_PAPER_SECRET_KEY`, as the Scout) | simulate, paper, live |
| `BROKER_PAPER` | read-write, why: "paper orders" | paper (adapter not built in stage 1) |
| `BROKER_LIVE` | read-write, why: "live orders after the person approves each one" | live (adapter not built in stage 1) |

## 6. Records

**Ledger** `state/ledger.jsonl`, append-only, one JSON object per line, written under a lock.
Kinds: `proposed`, `refused`, `queued`, `approved`, `submitted`, `filled`, `expired`, `dividend`,
`split`, `cash`, `mismatch`, `correction`. Every line has `id`, `kind`, `at` (UTC), `session`,
`strategy` (where it applies).

- Lots, positions, cash and realized gains are rebuilt by replay, never stored separately. Sells
  consume the oldest lots first; a `split` line multiplies a lot's shares and divides its cost.
- The simulator's starting cash is a `cash` line written on the first `simulate` run from
  `sim_starting_cash`.
- A torn line is skipped and counted; the replay then disagrees with the broker and reconcile
  stops trading. Nothing is silently dropped.
- A `correction` names the line it corrects and the replay applies it; only `ledger.py correct`
  writes it, and only from a terminal.

**Costs.** `commission_per_order` and `slippage_bps` per side, in the manifest. The simulator
applies both to fills. The go-live report applies them again on top of paper fills, which are
usually better than real ones.

## 7. Go-live report: `promotion.py`

Writes `state/promotion/<date>.json` and prints one line per criterion: PASS, FAIL or could not
tell. The report passes only if every line is PASS.

| criterion | measured as |
|---|---|
| time | days in `paper` under the current bar fingerprint, at least `min_paper_days` (183) |
| trades | closed trades (a buy lot fully sold), at least `min_closed_trades` (20) |
| return | equity curve return after the cost estimate vs SPY dividend-adjusted return over the same dates; strategy must be higher |
| drawdown | the strategy's maximum peak-to-trough fall at most SPY's over the same dates |
| guards | zero filled orders that breached a cap, zero `mismatch` lines without a later `correction` |

## 8. Board and loops

- **Board** (port 8792): mode and HALT banners, positions with their exit rules and distance to
  the stop, the live approval queue, today's refusals with reasons, equity vs SPY, the latest
  go-live report. Same `board_shell` pattern as the Scout's board.
- **Loops:** `daily-trade` (`30 8 * * 2-6`): `bars.py --refresh`, `trade.py --run`, tracker row.
  `weekly-review` (`0 10 * * 6`): `promotion.py --report`, weekly digest of trades, refusals and
  the go-live report. Plus core's `health-checks` and `lessons-review`.

## 9. Manifest defaults (`[trader]`)

```toml
mode = "off"
scout_manifest = "../investor/bob.toml"
sim_starting_cash = 100_000
commission_per_order = 1.0
slippage_bps = 10
base_weight = 0.05
max_position = 0.10
max_invested = 0.80
min_cash = 0.20
daily_loss_limit = 0.03
max_orders_per_day = 10
min_order_value = 500
max_adv_share = 0.01
stop_loss = 0.20
take_profit = 0          # 0 means no target
max_hold_days = 365
vix_flag = 25
hy_spread_flag = 5.0
throttle_floor = 0.25
block_flags = ["liquidity"]
min_paper_days = 183
min_closed_trades = 20
override_promotion = ""

[trader.budgets]
scout_calls = 1.0
```

## 10. Testing

Plain-assert suites, refuse/allow pairs, every check broken once on purpose and restored.

| area | must refuse / flag | must allow |
|---|---|---|
| mode off | any network or broker call (getter that fails if called) | `OK mode=off`, exit 0 |
| adapter binding | sim adapter in `paper`; missing credential | sim adapter in `simulate` |
| live gate | `live` with no passing report in 30 days | with a passing report; with `override_promotion` |
| caps | each cap one cent over, including orders earlier in the same run | each cap exactly at the limit |
| reconcile | one share or one cent off; a torn line | exact match; mismatch cleared by a correction |
| exits | | stop on the split-adjusted close, target, 365 days, Scout withdrawal each produce a sell; a sell runs under HALT and after the daily loss limit |
| daily loss / HALT | a buy after the loss limit; a buy under HALT | sells in both cases |
| sizing | | conviction 0.5 at the buy line and 1.0 at rank 100; missing or stale macro gives the floor throttle; below `min_order_value` skipped with reason |
| risk flags | a `block_flags` flag refuses | other flags only recorded |
| split | a split without a `split` line causes a mismatch | with it, shares and cost adjust and reconcile passes |
| re-run | a second run in the same session proposes nothing new | |
| approve / correct | not a terminal; mistyped id; caps failing at approval time | terminal, matching id, caps passing |
| hooks | direct trading URL, live host, broker MCP tool, `python3 harness/broker/sim.py`, `approve.py`, `ledger.py correct`; edits to ledger, `bob.toml`, hooks | `trade.py --run`, `halt.py`, reading any file |
| scout feed | v1 calls (no `card`); torn lines counted | open v2 calls and withdrawals |
| end to end | | fabricated market and Scout calls, 60 simulated sessions: replayed ledger equals the simulator's positions and cash every day |

## 11. Done when (stage 1)

1. `./selftest.sh` in core, `bobs/investor` and `bobs/trader` all pass.
2. In `simulate`, one week of real daily runs on real Scout v2 calls produces fills and a clean
   reconcile every day, recorded in the tracker; the board shows positions and refusals.
3. `mode = "off"` is the committed default in `bob.toml`.
4. Release notes and tracker written; `fleet.toml` entry reviewed before commit.

## 12. What we are not building (stage 1)

- The short-term strategy (stage 2, its own spec and backtest).
- Any real broker adapter, paper or live (written when paper trading starts).
- Intraday runs, limit/stop/bracket orders at the broker, options, short selling, margin.
- Tax features, notifications, automatic promotion between modes.
- An earnings-date source (needed before an "earnings in the holding period" flag can exist).
- Any change to the Scout, except reading its files.
