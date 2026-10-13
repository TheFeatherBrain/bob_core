# Scout upgrades: design

Date: 2026-10-14. Status: approved in conversation, awaiting written-spec review.

## 1. Intent

**What the person asked for.** Take the useful ideas found by comparing this repo with
`temple-stuart-accounting-main` (an AGPL Next.js finance app with an options scanner) into the
Investor Scout. Ideas and formulas only; no code is copied (AGPL, and that repo is TypeScript).
The person also wants the Bob family to trade stocks later; that is a separate Trader Bob with its
own spec (project B), not part of this one.

**Decisions made in conversation.**
- Two projects. **A (this spec): Scout upgrades.** B: a separate Trader Bob, built later, disabled
  until paper trading starts. The Scout stays read-only; `order_gate` is not touched (it already
  says a Trader Bob gets its own manifest, credentials and gate).
- Stocks only. No tax features (the person is in Greece; US wash-sale rules do not apply).
- **Market conditions are context only**: saved, shown, recorded with every call; they change no
  score. The Trader will later use them to shrink position sizes.
- **Sector ranking for growth and quality only.** Momentum, surprise and insider stay ranked across
  the whole universe: beating the market is what a call claims, and SUE is already scaled to each
  company's own history.
- **Agreement rule: at least 3 known groups above the 50th percentile**, otherwise the best verdict
  is "watch". Unknown groups count neither way.

**Dropped from the comparison list.** The SUE-relative surprise threshold: `signals.sue()` already
standardises each company's surprise by its own history.

**Assumptions (not stated by the person).**
- Fama-French 12 industries are fine-grained enough for sector ranking.
- Changing ranks is a scorecard change, so `scorecard_version` goes from 1 to 2. Calls made under
  version 1 stay in the ledger and are scored as before; the digest and board already split by
  version.

## 2. Placement

- All Scout changes live in `bobs/investor/`. One core change: `PLAYBOOK.md` (section 3.6), which is
  domain-free.
- Nothing in core imports from `bobs/`.
- New manifest keys go in `[scout]` and are validated by `scoutcfg.py`.

## 3. Components

### 3.1 Market conditions: `harness/macro.py` (new)

- `--refresh` reads a fixed list of FRED series from the keyless CSV endpoint
  `https://fred.stlouisfed.org/graph/fredgraph.csv?id=<SERIES>` through `fetch.read`, so every read
  is saved as evidence before use. Its own host allowlist (`FRED_HOST`), like `edgar.SEC_HOSTS` and
  the one in `prices.py`; any other URL is refused.
- Series, with the age at which the latest observation counts as stale (by release cycle):

  | id | what | stale after |
  |---|---|---|
  | `T10Y2Y` | 10Y minus 2Y Treasury | 7 days |
  | `BAMLH0A0HYM2` | high-yield credit spread | 7 days |
  | `VIXCLS` | VIX close | 7 days |
  | `NFCI` | Chicago Fed financial conditions | 14 days |
  | `ICSA` | initial jobless claims | 14 days |
  | `UNRATE` | unemployment rate | 75 days |
  | `CPIAUCSL` | CPI, all items | 75 days |
  | `FEDFUNDS` | effective fed funds rate | 75 days |

  Monthly series are dated the first of the month and published weeks after it ends, so 75 days is
  their normal age, not a fault.
- Writes `state/macro.json`: per series `value`, `observed` (date), `stale` (bool), `evidence`
  (path), plus `refreshed_at` (UTC). CPI is stored as year-on-year change computed from the series
  (13 observations needed); fewer means `null`.
- A series that cannot be read or parsed is `null` with `reason`, never a default or a carried-over
  value. The run prints `OK` if every series was read, `FAIL: ...` naming the ones that were not,
  and exits 1 in that case; the file is still written with what was read.
- No score uses it. It is shown on the board, in the weekly digest, and copied into every call
  (3.4).
- `loops/nightly-screen.txt` runs `python3 harness/macro.py --refresh` before `screen.py --run`. A
  macro failure is recorded in the tracker and does not stop the screen.

### 3.2 Sectors: `harness/sectors.py` (new) and `edgar.py`

- `edgar.py --sync` also caches each universe company's SIC code into `state/sec/sic.json`:
  `{cik: {"sic": int|null, "read": date, "evidence": path}}`. The code comes from EDGAR's company
  Atom feed (`browse-edgar?action=getcompany&CIK=...&output=atom`, the `<assigned-sic>` field, about
  10 KB), not the submissions JSON: every read is saved as evidence, and ~5,000 submissions files of
  up to several hundred KB each would add roughly 750 MB per refresh. A code is read once and
  refreshed after `SIC_REFRESH_DAYS = 90`. Reads go through the existing SEC limiter; the first sync
  costs about one call per company (about 10 minutes at 8 per second), later syncs only new or
  expired ones.
- `sectors.py` maps SIC to the Fama-French 12 industries, a fixed table of SIC ranges copied from
  Ken French's `Siccodes12` definition (the source file is saved as evidence and cited in the module
  docstring). A missing SIC, or one outside every range, maps to `Other` (FF12's own catch-all).
- `sectors.is_financial(sic)`: SIC 6000 to 6799 (banks, insurers, real estate, investment
  offices).

### 3.3 Scorecard changes: `scorecard.py`, `screen.py`

- **Peer groups.** `percentile_ranks` is called per sector for the growth and quality signals
  (`ttm_growth`, `acceleration`, `piotroski`). A sector with fewer than `min_sector_size` scored
  values for that signal (new key, default 15) is ranked against the whole universe instead. Each
  signal on the card records `peer`: the sector name or `"market"`.
- **Altman for financials.** For `is_financial` companies, `altman` is `null` with reason
  "not valid for financials", so the distress veto does not apply. This is what the original spec
  (section 5) already required.
- **Agreement gate.** A group agrees when its group percentile is above 50. Gate order becomes:
  1. coverage below `min_coverage`: "could not tell";
  2. Altman below `altman_veto`: "watch";
  3. rank below `buy_percentile`: "pass";
  4. fewer than `min_agreeing_groups` (new key, default 3) known groups agree: "watch", reason
     "N of M known groups above median, K needed";
  5. otherwise "eligible".
  The agreement check comes after the rank check so it only demotes names that would otherwise be
  eligible; placed before it, every low-ranked name would read "watch" instead of "pass".
- The card gains `sector`, `agreeing` (count) and `known_groups` (count). `screen.py --card` prints
  them.
- `scorecard_version = 2` in `bob.toml`; the existing `VERSIONED` check in `screen.py` gains the two
  new keys so changing either without a version bump fails.

### 3.4 Full scorecard with each call: `calls.py`, `ledger_score.py`

- A call line additionally stores:
  - `card`: every signal's `value`, `pct`, `peer` and `evidence`; every group percentile; `sector`,
    `agreeing`, `known_groups`, `altman`;
  - `macro`: a copy of `state/macro.json` at call time;
  - `screen_sha256`: the hash of `state/screen/<session>.json` as read.
  The call then stands on its own even if a re-run of the same session rewrites the screen file.
- Old calls (no `card`) stay valid; readers treat the field as optional.
- `ledger_score.py --by-signal`: excess return over SPY and QQQ at each horizon, grouped by which
  groups agreed and by sector. It refuses (prints why, exits 1) when fewer than `MIN_SCORED` calls
  carry a `card`, the same floor the overall score uses.

### 3.5 Unused-data check: `harness/data_audit.py` (new)

- Reports three things:
  1. XBRL concepts that `edgar.py` fetches (`QUARTERLY`, `RECENT_QUARTERLY`, `ANNUAL`, `INSTANT`)
     but nothing in `xbrl.py`, `signals.py`, `screen.py` or `fundamentals/` reads;
  2. per-signal coverage across the latest screen (share of scored companies with a value);
  3. FRED series in `macro.py` not shown by the board or digest.
- `--report` prints the three lists; the weekly digest includes them.
- `test_data_audit.py` fails when list 1 or list 3 is non-empty, so a fetched-but-unused concept
  breaks `./selftest.sh`. Coverage (list 2) is reported, never failed on: low coverage is a fact
  about the data, not a code fault.

### 3.6 PLAYBOOK (core)

- Every Bob design gets a "What we are not building" section.
- Any migration of a Bob's state, evidence or ledger starts with step 0: copy `state/` (and the
  ledger) to a dated backup folder, and record its path in the tracker.

## 4. Data flow

```
nightly-screen loop
  macro.py --refresh      -> evidence/  + state/macro.json
  edgar.py --sync         -> evidence/  + state/sec/ (frames, tickers, form4, sic.json)
  prices.py --refresh     -> evidence/  + state/prices/
  screen.py --run         -> state/screen/<session>.json (cards with sector, peer, agreeing)
calls.py verdict ...      -> calls.jsonl line with card + macro + screen_sha256
ledger_score.py --run     -> ledger_results.json   (--by-signal for attribution)
digest.py --write         -> macro block + data_audit report
```

## 5. Errors and guards

- FRED host allowlist in `macro.py`; FRED is read-only and keyless, so no credential is added.
- A stale or unread series is shown as such everywhere it appears; never filled in.
- Unknown SIC goes to `Other`, which is ranked market-wide if it is small. It never borrows another
  sector's ranks.
- The `VERSIONED` check refuses a run whose versioned keys changed without a `scorecard_version`
  bump.
- `data_audit` fails the self-test on dead fetches.

## 6. Testing

Plain-assert `test_*.py` suites, refuse/allow pairs. Each check is broken on purpose once (with the
editor) to confirm it fails, then restored.

| check | must refuse / flag | must allow |
|---|---|---|
| macro staleness | daily series observed 8 days ago is `stale` | 6 days ago is not |
| macro host | any non-FRED URL refused | FRED CSV URL read |
| macro unreadable | malformed CSV gives `null` + reason, exit 1 | valid CSV gives a value |
| CPI year-on-year | 12 observations gives `null` | 13 gives a value |
| sector fallback | sector with 14 values ranked market-wide, `peer = "market"` | 15 ranked in sector |
| financials | SIC 6022 bank gets `altman = null`, no veto | SIC 3674 chipmaker still vetoed below 1.8 |
| agreement | top-ranked card with 2 of 4 known groups above 50 gives "watch" | 3 of 4 is eligible; a low-ranked card stays "pass" |
| unknown groups | unknown group does not count as agreeing or disagreeing | |
| call line | test fails if `calls.py` writes a buy-worthy call missing `card`, `macro` or `screen_sha256` | all three present |
| old calls | | version-1 call without `card` still scored |
| by-signal floor | 19 calls with `card` refused | 20 reported |
| data audit | fetched concept never read fails | every fetched concept read passes |
| version guard | `min_agreeing_groups` changed, version unchanged: refused | changed with bump: runs |

## 7. Done when

1. `./selftest.sh` in `bobs/investor` and in core both pass.
2. A real `macro.py --refresh` saves evidence for all eight series and writes `state/macro.json`.
3. A real nightly screen produces cards with `sector`, `peer` and `agreeing`, and no financial
   company is vetoed by Altman.
4. `data_audit.py --report` lists nothing fetched-but-unused (or each item is removed from the
   fetch, or put to use, before this is done).
5. Release notes and tracker entries written; the investor README run table lists `macro.py` and
   `data_audit.py`.

## 8. What we are not building

- Score changes from market conditions, or regime-dependent weights.
- Sector ranking for momentum, surprise or insider.
- Paid data sources (Finnhub premium and the like) or FRED API keys.
- Options data or anything about volatility edges.
- Anything that places, previews or sizes an order. That is project B, the Trader Bob, with its own
  spec.
- Tax features.
