# Tipster: design

Date: 2026-10-20. Status: agreed in conversation, awaiting written-spec review.

## 1. Intent

**What the person asked for.** A Bob that finds Euroleague basketball bets the person would never
have spotted, by reading team and player history, news and the odds of many bookmakers, and sends
them as tips. The person places any real bet themselves, on bet365 or Stoiximan. In the first
season the question is whether there is an edge at all, so every tip is placed in a simulated
wallet and measured.

**What "working" means (the person's words, made exact).** Tips that win more than they lose,
counted as **profit in euros** from a simulated **€1,000 wallet**, shown next to **closing line
value (CLV)**. CLV is the early signal: published work (Buchdahl, on 1,214 real bets) puts CLV at
significance in about 50 bets, profit and loss in about 1,000. A season of about 150 tips can prove
or disprove an edge through CLV; it cannot prove profit.

**Decisions made in conversation.**
- Name **Tipster**, folder `bobs/tipster`. It only suggests; it never places a bet.
- Covers **game markets** (winner, handicap/spread, total points). **Player props** (points,
  rebounds, assists) are in v1 only if the step 0 odds test (section 3.1) shows bet365 or
  Stoiximan prop prices for Euroleague; otherwise they wait for a later version.
- Sources: team and player history, news (X/Twitter came up; see 3.4), odds from many bookmakers,
  stats from more than the official feed.
- **One ledger, simulated:** every tip counts as placed in the €1,000 wallet. No real-money ledger
  in v1. A tip is recorded at the **better of bet365's and Stoiximan's odds** at the moment it is
  tipped.
- **Stakes by edge:** quarter Kelly on the current simulated wallet, usually 1 to 4%; **hard cap
  10% per tip; 25% per round; automatic HALT when the wallet falls below €500.**
- **Delivery:** the board, and **Telegram, one-way** (Tipster sends; replies wait for a later
  version).
- **Tip as soon as an edge is found;** keep watching a tipped game until tip-off and send an update
  when something changes (an injury, a big odds move). Updates never change the ledger: a
  withdrawn tip still counts at its original odds.
- **Odds requests stay on a free plan at first.** News is watched often (free); odds are fetched
  only when there is a reason (section 6).
- Odds provider: the one that passes the step 0 test. **OddsPapi** is the lead candidate.
- From the other agent's plan, adopted: the ratings-and-pace model as the baseline, a minimum edge
  of 3%, a structured record for every tip.

**Assumptions and calls made by Claude (not stated by the person).**
- **Claude can only veto, never inflate.** Scripts compute every probability, edge and stake.
  Claude reads news and can cut a tip's stake or veto it, citing evidence; it can never raise a
  probability or a stake. Every tip is recorded on two tracks, "model" and "reviewed" (section
  5), so the ledger shows whether Claude's reading adds value.
- Tipster runs on the person's **personal Windows PC**. The work Mac's network filter blocks every
  odds provider, which also makes it the wrong place for this Bob. (The person confirmed: the PC is
  on and awake during game evenings, and it is the machine for step 0 and for building.)
- Modes are `off` and `on`, plus `HALT`. The Trader's `paper`/`live` modes do not apply: Tipster
  never touches a bookmaker.
- Regular season, Play-In, playoffs and Final Four are all in scope, under the same rules.
- The pre-tip-off cutoff for the last check is 30 minutes (the person said "some minutes").
- The model must pass a calibration check on past seasons before Tipster is switched `on`
  (section 4.4).

## 2. Placement

- New Bob folder `bob_core/bobs/tipster`, scaffolded with `automation/new_bob.py` into an empty
  temp folder and moved in (this spec already lives in the target folder), as the Investor and
  Trader were. Board port **8793**.
- Nothing in core imports from `bobs/`; Tipster imports from no other Bob.
- Bob-specific settings in a `[tipster]` manifest section, validated by `tipstercfg.py`.
- **One core change: Telegram.** A domain-free `harness/telegram.py` (send only) and an optional
  `[telegram]` manifest section, validated by `bobcfg` when present. The Investor's design already
  planned Telegram as a core channel; any Bob can use it.
- Secrets (odds API key, Telegram bot token, chat id) live in the `[runtime] env_file` outside
  every repo, loaded by `launch.sh` and the supervisor, as the Investor's Alpaca keys are.
- `[credentials]` names them with access levels: odds key read-only, Telegram bot read-write (why:
  sends tips to the person).

## 3. Data sources

Research on 2026-10-19 (details in the conversation; the odds providers could only be checked
through search results because the work network blocks them).

### 3.1 Odds: step 0 decides the provider

| candidate | claims | free tier | paid |
|---|---|---|---|
| **OddsPapi** (lead) | Euroleague with about 180 books incl. bet365, Stoiximan, Pinnacle; history endpoint said not to count against the quota | 250 requests/month (one source says 1,000/day) | about $20/month |
| Odds-API.io (backup) | bet365, Stoiximan, Pamestoixima | new free keys paused | £49/month, 2 books |
| The Odds API | **ruled out**: no bet365, no Greek Stoiximan, no Euroleague props | | |

**Step 0, before any code, run by Claude Code on the person's Windows PC with a free key** (the
person gets the key). Save every response as evidence; they become the recorded fixtures for the
tests. Record each answer in the tracker:
1. Euroleague fixtures for round 8 or later are listed.
2. bet365 and Stoiximan (Greek) prices exist for moneyline, handicap and totals.
3. Pinnacle prices exist for the same markets.
4. Player prop prices exist from bet365 or Stoiximan (decides whether props are in v1).
5. What one request returns: one game, one round or the whole competition, and per market or all
   markets (decides the request budget, section 6).
6. Whether the history endpoint returns closing prices and whether it counts against the quota
   (decides whether a pre-tip-off snapshot is needed).
7. The real free quota.

If no provider passes items 1 to 3, stop and bring it back to the person.

**Then compute the cost of one round** from items 5 to 7 (two discovery sweeps, a recheck per
tipped game, a closing snapshot per tipped game unless history covers it). If the free quota
cannot cover the rounds in a month, stop and bring it back to the person, who decides between the
paid plan (about $20/month) and reduced coverage (one sweep, fewer games, game markets only). The
plan's later steps follow that decision.

**Never read odds from bookmaker sites.** bet365's terms forbid scraping. Odds come only through the
chosen API.

### 3.2 Stats: official Euroleague API (confirmed live, no key)

- Schedule and results: `https://api-live.euroleague.net/v2/competitions/E/seasons/E2026/games`
  (all 380 games, scores, `played` flag).
- Box score per game: `/v3/competitions/E/seasons/E2026/games/{code}/stats`; legacy
  `https://live.euroleague.net/api/{Boxscore|PlaybyPlay|Points|Header}?gamecode=N&seasoncode=E2026`.
- Season stats (traditional, advanced, misc, scoring) under `/v3/competitions/E/statistics/...`.
  No pace or offensive/defensive ratings: Tipster computes them from box scores.
- History: games and box scores from 2000-01; play-by-play and shots from 2007-08.
- The league's data rights holder (Genius Sports) acts against unauthorised collection. For a
  personal tool: at most one request every 250 ms, cache every response, never refetch a finished
  game.
- Other stats sites: Basketball-Reference only within its 20 requests/minute limit, if needed at
  all. **RealGM is out** (its terms forbid automated extraction).

### 3.3 Injuries and news (confirmed live, free)

- **The official injury report**, one per round (`/en/euroleague/news/injury-report-round-N-el2627/`
  on euroleaguebasketball.net): out, questionable, game-time decision, available. HTML, rate-limited;
  read sparingly.
- RSS: Eurohoops `https://www.eurohoops.net/en/feed/` (also signals a new injury report),
  Sportando `https://sportando.basketball/en/feed/`, Gazzetta `https://www.gazzetta.gr/rss`,
  Novasports `https://www.novasports.gr/rss`. BasketNews only as a filler (mixes NBA, empty dates).

### 3.4 X/Twitter: not in v1

The official API is pay-per-use only, about $15/month for 30 to 50 accounts; scraper services
break X's terms. v1 uses the free sources above. X is added later only if the tracker shows
game-day news arriving too late through them.

## 4. Model

Scripts compute; Claude reads. Every number on a tip comes from a script and cites its evidence.

### 4.1 Baseline (from the other agent's plan)

- Possessions per team per game ≈ FGA + 0.44 × FTA − OREB + TOV, from box scores.
- Offensive and defensive rating per 100 possessions; pace per 40 minutes.
- Expected pace of a matchup = pace A × pace B / league pace.
- Expected points of A = expected pace × (ORtg A × DRtg B / league ORtg) / 100, and the same for B.

### 4.2 Additions

- **Home-court advantage**, estimated from past seasons in the official API.
- **Early-season shrinkage:** current-season ratings are blended with last season's and with the
  league average, weighted by games played, so 6 games do not count as a full season. The blend
  is the builder's choice, settled by the calibration check (4.4).
- **Schedule:** a flag for the second game of a double-round week, and travel; whether they enter
  the numbers is settled by the calibration check, not assumed.
- **Probabilities:** expected margin and total become P(win), P(cover) and P(over) through a
  distribution of outcomes around them, with its spread estimated from past seasons.

### 4.3 Edge and stake

- Fair probability from the model; offered odds = the better of bet365 and Stoiximan at that
  moment; **edge = P × odds − 1**. A tip needs **edge ≥ 3%** (`[tipster] min_edge`).
- Stake = quarter Kelly on the current simulated wallet, capped at 10% per tip, with all tips for
  one round together capped at 25% of the wallet at the round's first tip. Below the caps, the
  stake is rounded down to whole euros.
- Wallet below €500: HALT (section 8).

### 4.3a What counts as one tip (Claude's call)

- **Main lines only:** the handicap and total each book lists as its main line; no alternate lines.
- **Per book, then the better one:** the edge is computed on each of bet365's and Stoiximan's own
  line and price. When their lines differ (−5.5 against −6.5), the tip takes the book and line
  with the larger edge, and records both.
- **At most two tips per game:** one side tip (moneyline **or** handicap, whichever has the larger
  edge, since both come from the same expected margin) and one total. This keeps correlated stakes
  from stacking.
- **No re-tipping:** once a market family (side, or total) of a game is tipped, later sweeps only
  produce updates on that tip, never a second tip, even at a better price or line.

### 4.4 Calibration check before switching on (Claude's call)

Using only the free stats history: run the model as it would have run, game by game, on the
2023-24, 2024-25 and 2025-26 seasons, using only data before each game. Past bookmaker lines are not
needed: the gate is on the model's own predictions. It must:
- **winner:** beat a home-court-only baseline on Brier score for P(home win);
- **margin:** beat the home-court-only baseline on RMSE of the predicted margin;
- **total:** beat a baseline (the average of the two teams' season-to-date points for and against)
  on RMSE of the predicted total;
- **calibration:** for P(home win), and for P(margin > x) and P(total > y) at fixed lines spread
  across the usual range (the builder picks them, e.g. every 3 points), group predictions into
  10%-wide bands. In every band with at least 50 games, the observed share lies inside the 95%
  binomial interval around the band's mean predicted probability; and the overall expected
  calibration error is at most 3 points. (Claude's call; a fixed "± 5 points" would fail a
  well-calibrated model by chance.)

A market type that fails its part of the gate is not tipped; the others may be.

If the history endpoint of the chosen provider gives past Euroleague odds for free, also report
the edge it would have found and its CLV. This is a report, not a gate.

### 4.5 Player props (only if step 0 item 4 passes)

Expected minutes × per-minute rate, adjusted for the opponent and the expected pace. The builder
designs the details and adds props to the calibration check before they are tipped.

## 5. Claude's review: veto only, two tracks

- After a script finds an edge and before a tip is sent, Claude reads the latest injury report and
  news for both teams (each read saved as evidence) and decides: **keep**, **halve the stake**, or
  **veto**, with the cited reason.
- **Model track:** every tip the script found, at its computed stake, whatever Claude decided. Not
  sent.
- **Reviewed track:** what the person sees: kept and halved tips are sent; vetoed ones are not.
  The €1,000 wallet follows this track; the model track has its own €1,000 shadow wallet.
- The board and digest show both tracks, so it is visible whether vetoes saved or cost money.
- Claude never writes a probability, edge or stake. The tip record's numbers come from the script;
  Claude's part is the decision, the reason and the evidence paths.

## 6. Tip life and the request budget

**Odds requests are scarce; news is free.**

| when | what | costs odds requests |
|---|---|---|
| when a round's lines open (about 2 days before) | discovery sweep: odds for the round, model, review, tips | yes |
| game-day morning | second discovery sweep (lines and news have moved) | yes |
| every 30 to 60 min on game days, every few hours otherwise | news watch: RSS feeds, and the official injury report when Eurohoops signals a new one | no |
| when news touches a tipped game (a rotation player's status changes) | recheck that game's odds once; Claude reviews | yes, one |
| 30 min before tip-off, per tipped game | closing snapshot for CLV (skipped if step 0 item 6 shows free history with closing prices) | yes, one |
| tip-off | watching stops; pre-game betting is closed | no |
| after the game | settle from the official result and box score | no |

**Updates to the person** (Telegram and board) on a tipped game:
- **still good:** the change does not hurt it; says what changed.
- **weaker:** edge shrank but is still ≥ 3%; a lower stake suggestion.
- **withdraw:** edge below 3%, or Claude judges the news breaks the tip.

**A trap the builder must handle.** The team model does not know who is injured. When a key player
is ruled out, the market moves and the model's edge appears to **grow**. After material news, an
edge never increases a tip's stake and never creates a new tip on that game without Claude's
review; the update says "the market moved; the model does not see injuries".

The ledger never changes after a tip is recorded: updates are recorded as their own lines that
reference the tip, so it can be checked later whether withdraw alerts were right.

**Request budget** (`budget.py`, Claude's call):
- Every odds request is appended to a request ledger before it is made.
- `[tipster] odds_requests_per_month` sets the allowance. A request that would exceed it is
  refused.
- Priority when the budget runs low: closing snapshots for tipped games, then rechecks, then
  discovery.
- The board shows requests used and remaining. Moving to a paid plan is a change to that number,
  made by the person.

## 7. Ledgers and scoring

- `tips.jsonl`, append-only, under the Bob's state dir. One line per tip with: id, track, game,
  market, line, side, model probability, odds per book at tip time, odds taken (better of bet365
  and Stoiximan, and which), edge, stake, wallet before, Claude's decision and evidence, and the
  model inputs (both teams' ratings, pace, home advantage, shrinkage weight, expected margin and
  total) with evidence paths. The builder defines the exact fields; nothing outside this spec
  fixes them. Further lines for updates, closing snapshot and settlement, each
  referencing the tip id. Corrections are new lines; torn lines are skipped and reported.
- **Settlement** from the official API: win, loss, push (stake returned on a whole-number line),
  void (game postponed beyond the bookmakers' usual rule; the builder states the rule used).
- **CLV** = odds taken / fair closing odds − 1. Fair closing odds come from Pinnacle's closing
  prices with the margin removed (power method for two-way markets; the builder may use
  multiplicative, stated). When Pinnacle has no price for that market, the consensus of the
  available books is used and the tip is marked so.
- **Scores shown:** profit in euros, ROI, average CLV and the share of tips with positive CLV, each
  with n, per track, per market type. Banners: "too early to judge CLV" below 50 settled tips,
  "profit is mostly luck at this size" below 500.
- **Wallet:** €1,000 at the start, changed only by settlements. Stakes use the wallet at tip time.

## 8. Guards

- **Modes:** `[tipster] mode = "off" | "on"`. `off`: nothing reads or sends. The person changes it.
- **HALT:** `state/HALT` stops new tips (watching and settling continue). Created by
  `halt.py --reason`, and automatically when the reviewed wallet falls below €500. Only the person
  removes it.
- **Host allowlist** in Tipster's fetch module: the chosen odds API, the Euroleague API hosts, the
  league site (injury report), the RSS hosts in 3.3, Telegram. Any other host is refused. No
  bookmaker host is ever allowed.
- **protect_gate** (as in the Investor and Trader): refuses Edit and Write on the ledgers, the
  evidence dir, `bob.toml`, `.claude/settings.json` and the hooks.
- **Telegram goes only through `telegram.py`**, which lints the text (plain text, no parse mode,
  length limit). A send that fails is retried on the next pass; the tip stays recorded and on the
  board.
- Evidence for every read: odds responses, stats responses, feed items and injury report pages,
  through core's `evidence.save`.

## 9. Board (port 8793)

- Today and the next round: games, tips with stake, odds taken and at which book, edge, Claude's
  decision, latest update.
- Wallet curve (reviewed and model tracks), profit, ROI, CLV with n, and the banners.
- Settled tips with result and CLV.
- Request budget used and remaining; mode and HALT state.
- Calibration report from 4.4.

## 10. Loops

| loop | schedule (the builder sets exact times from the fixture list) |
|---|---|
| `discovery` | when lines open for a round, and game-day mornings |
| `news-watch` | every 30 to 60 min on game days, every few hours otherwise |
| `closing` | 30 min before each tipped game |
| `settle` | the morning after each game day |
| `weekly-digest` | weekly: both tracks, CLV, profit, n, budget |
| `health-checks`, `lessons-review` | core templates |

## 11. Testing

Plain-assert suites with refuse/allow pairs, recorded fixtures, no network; each check is broken
once on purpose to see it fail, then restored.

- Possessions, ratings and pace against a hand-worked real game.
- Edge, Kelly stake and every cap (10% tip, 25% round, rounding down), HALT below €500.
- Settlement: win, loss, push, void; CLV with the power method against a worked example.
- Budget: refused over the allowance; priority order when low.
- The injury trap: an edge that grows after material news never raises a stake or creates a tip
  without review.
- Claude's review: a veto keeps the tip on the model track only; a review that tries to write a
  probability is refused.
- Host allowlist: a bookmaker host refused; each allowed host passes.
- protect_gate on each protected path; Telegram lint.

## 12. Done when (v1)

1. Step 0 answers recorded in the tracker; provider chosen.
2. Tipster's and core's self-tests pass.
3. The calibration check passes and its report is on the board.
4. A live round runs end to end on the Windows PC: discovery, at least one tip on Telegram and the
   board, a news-triggered update or a recorded "no change", closing snapshot or free history,
   settlement, CLV computed.
5. The request budget stayed inside the allowance the person set after step 0.
6. Each guard seen refusing in a live session.

## 13. What we are not building

- Placing bets, logging in to a bookmaker, or reading bookmaker websites.
- A real-money ledger or marking tips as placed (later, with two-way Telegram).
- Replies to Telegram.
- X/Twitter (later, if free news proves too slow).
- Live (in-game) betting.
- Other competitions (EuroCup, domestic leagues).
- Automatic changes to the model, the minimum edge or the stake rules from results. Tipster may
  propose them as lessons citing the ledger; the person decides.
- Paid data, until the person raises the budget.

## 14. Left to the builder

Exact loop times; the shrinkage weights and outcome distribution (settled by the calibration
check); the margin-removal method if not power; the void rule; the props model if props pass step
0; module layout following the Investor and Trader.
