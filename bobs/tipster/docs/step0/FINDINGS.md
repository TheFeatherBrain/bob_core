# Step 0: odds provider test (plan Task 0)

Run 2026-10-20 on the Windows PC with a free OddsPapi key (`ODDS_API_KEY`, env file). Every
response is in `responses/`, with the key replaced by `{KEY}`. Five were trimmed before commit,
structure kept (see "Files"). Billable requests used: **9 of 250**, confirmed by the free
`/v4/account` endpoint before (0) and after (9).

**Decision: OddsPapi passes items 1 to 3 on the free plan. A month costs at most about 200 of
its 250 requests, so no paid plan is needed. Closing prices come free from history. Player props
exist (Stoiximan) and wait for their own plan.**

## The seven questions

| # | question | answer | rests on | requests |
|---|---|---|---|---|
| 1 | Euroleague fixtures for round 8 or later | Yes. Tournament 138 (sport 11, basketball) lists all 380 regular-season games of 2026-27. Rounds 7 and 8 already carry odds (`hasOdds: true`); later rounds are listed without odds yet | `sports.json`, `tournaments.json`, `fixtures-E2026.json` | 3 |
| 2 | bet365 and Stoiximan prices for moneyline, handicap, totals | Yes. One `/v4/odds` call for Paris v Valencia (round 7) returned bet365 (127 markets), bet365.gr (127) and Stoiximan (3: exactly the main moneyline, handicap and total). Main lines are marked `mainLine: true` | `odds-game.json` | 1 |
| 3 | Pinnacle prices for the same markets | Yes, close to tip-off. Absent on 20 Oct for the round 7 game (two days out); present in the closing history of a round 6 game (moneyline, handicap 3.0, total 177.5) | `odds-game.json`, `historical-game-pregame.json` | 0 |
| 4 | bet365 or Stoiximan player props for Euroleague | Yes, mostly Stoiximan: 79 player-prop markets (points, points + rebounds, points + assists + rebounds) in the round 6 closing history; bet365 4. Not yet posted two days before round 7 | `historical-game-pregame.json` (trimmed to game markets; counts from the untrimmed read) | 0 |
| 5 | What one request returns | `/v4/odds?fixtureId=..&bookmakers=a,b,c`: one game, every market, several books in one call. `/v4/odds-by-tournaments` takes **one** bookmaker (three gave HTTP 400, still billed): bet365 returned the whole round (10 games, all markets) with `verbosity=3`; Stoiximan returned the games with **no** markets, with or without `verbosity`. So Stoiximan is only available per game | `odds-game.json`, `odds-round-bet365-2games.json`, `odds-round-stoiximan.json`, `odds-round-stoiximan-v3.json` | 4 (one was the 400) |
| 6 | History endpoint: closing prices, and does it count | Yes and no: `/v4/historical-odds` returns every price change per line for up to 3 books (pinnacle, bet365, stoiximan here), including in-game prices after tip-off; the closing price is the last change before the start. It is free (docs, "Requests & Quota"; the account count did not move). Cooldown 5 s per call; data from January 2026 | `historical-game-pregame.json`, `account-redacted.json`, `account-after-redacted.json` | 0 |
| 7 | The real free quota | 250 requests a month, reset on the 1st; every call to a billable endpoint counts, errors included; `/v4/account` is free and never blocked | `account-redacted.json` | 0 (free) |

Other calls: `/v4/markets` (1 request) maps each market id to its type, period and line.

## How the data is encoded (for the parser)

- **Every line is its own market id.** `/v4/markets` gives `marketType` (`moneyline`, `spreads`,
  `totals`, ...), `period` (`result` = full game including overtime; also `p1`, `p1+p2`, ...)
  and `handicap` (the line). Example: 11470 = Handicap (incl. overtime) 3.5; 11174 = Over Under
  (incl. overtime) 184.5; 111 = Winner (incl. overtime).
- **Handicap is the home team's line.** Paris (home, moneyline 2.55) is +4.5 at bet365 (outcome
  "1"); Barcelona (home, 2.32) +3.5 at Stoiximan. Negative ids exist down to -80. This matches the
  Tipster board (`line` = home handicap).
- Outcome names: "1"/"2" for home/away, "Over"/"Under" for totals. Prices: `players["0"].price`
  (decimal), with `mainLine` and `active`.
- Team names differ from the official feed: 7 need aliases ("BC Olympiakos Piraeus",
  "KK Crvena zvezda Belgrade", "KK Partizan Belgrade", "BC Zalgiris Kaunas", "Anadolu Efes SK",
  "Besiktas JK", "Maccabi Tel-Aviv"). Fixtures also carry stable `participant1Id`/`participant2Id`.

## Cost of a month

Per round (10 games):

| step | requests |
|---|---|
| fixtures list for the provider's fixture ids, once per sweep | 2 |
| two discovery sweeps, `/v4/odds` per game with bet365, Stoiximan and Pinnacle together | 20 |
| rechecks (plan assumption: 6 tipped games) | 6 |
| closing snapshots: none, closing comes free from history after the game | 0 |
| **round** | **28** |

The busiest months of 2026-27 start 7 rounds (October, March): 7 x 28 = **196**, plus the market
table now and then: about **200 of 250**. The playoffs (best of five, April to May) have fewer
games per week. The budget (`budget.py`) refuses anything past the allowance, with closing (now
free) and rechecks protected first.

## Decisions (recorded per plan Task 0 step 5)

- Provider: **OddsPapi**, free plan, `odds_requests_per_month = 250`. No stop for the person: items
  1 to 3 pass and a month fits the free quota.
- `closing_from_history = true`: closing prices are read free from `/v4/historical-odds` at
  settlement (Pinnacle, bet365, Stoiximan), instead of a paid snapshot 30 minutes before tip-off.
- bet365: the plan's `bet365` is the international book; `bet365.gr` (the Greek site) is also
  offered and priced within a few cents. Which one Tipster prices against is the person's choice.
- **Props**: item 4 passes (Stoiximan). Per the plan, props get their own short plan before the
  done-when task; v1 stays on game markets until the person decides.

## Files

Raw reads were also saved as evidence under the Bob's `data/evidence/2026-10-20/` (git-ignored).
Trimmed before commit, structure unchanged:
- `account-redacted.json`, `account-after-redacted.json`: email and key fields removed.
- `markets-basketball-game.json`: full-game moneyline, handicap and total definitions of the
  12.9 MB market table.
- `historical-game-pregame.json`: Barcelona v Olympiacos (round 6), full-game markets, prices
  before tip-off only (from 21 MB).
- `fixtures-E2026.json`: the 2026-27 season of the 1,140 fixtures (2024 onward) returned.
- `odds-round-bet365-2games.json`: 2 of the 10 games of the bet365 round read.
