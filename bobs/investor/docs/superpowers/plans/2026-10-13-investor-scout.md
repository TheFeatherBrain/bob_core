# Investor Scout Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A read-only Bob under `bob_core/bobs/investor` that screens US-listed stocks nightly from SEC and Alpaca data, writes cited theses, keeps an append-only prediction ledger scored against SPY and QQQ, and shows it all on a board plus a weekly digest.

**Architecture:** Deterministic stdlib CLIs in the Bob's `harness/` fetch (saving every read through core's `evidence.save`), compute signals and a percentile scorecard, and refuse bad calls. Claude only reads filings and writes theses, driven by loop files. Hooks in the Bob's `harness/hooks/` refuse edits to state and any Alpaca order traffic.

**Tech Stack:** Python 3.11+ standard library only (`urllib`, `json`, `tomllib`, `statistics`, `xml.etree`), core's `bobcfg`, `evidence`, `oscompat`, `board_shell`, `loop_passes`. Bash for the test runner (Git Bash on Windows).

**Spec:** `bobs/investor/docs/superpowers/specs/2026-10-13-investor-scout-design.md`

## Global Constraints

- Stdlib only. No `pip install`, no `requirements.txt`.
- Python 3.11+ (`tomllib`). Must run on Windows (Git Bash) and macOS.
- No `zoneinfo`: Windows Python has no tz database without the `tzdata` package. US Eastern time is computed by `marketclock.py` (Task 6).
- Core code (`harness/`, `automation/`, `templates/`, `CORE.md`) is not changed, except the project `.claude/CLAUDE.md` and `.gitignore` in Task 1. Nothing in core may reference `bobs/`.
- Every per-Bob value comes from `bobs/investor/bob.toml` (core sections plus `[scout]`). Never hard-code thresholds, weights or paths in modules.
- Every network read goes through `fetch.save_read` (which calls core `evidence.save`). Missing data is `None` ("could not tell"), never `0`.
- Ledger files are append-only JSONL, written under `oscompat.lock`; torn lines are skipped and counted, never fatal.
- Tests: standalone `test_*.py` scripts with bare `assert`, run by `testkit.run(globals())`, no network, temp dirs via `testkit.temp_bob()`. Guards have refuse/allow pairs, and each guard is broken on purpose with the editor, seen failing, then reverted.
- Module shape (core's): docstring with purpose + `Usage:`, `from __future__ import annotations`, path bootstrap, `main(argv) -> int`, `raise SystemExit(main(sys.argv[1:]))`. Docstrings explain *why*.
- Never write an em-dash (U+2014) in code, comments, docs or commit messages.
- Commit straight to `main` after each task. Message ends with `Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>`. Use `git commit -F <file>` for multi-line messages (core's `commit_gate` refuses rewritable `-m` text).
- Thresholds (from the spec, verbatim): market cap >= $200M, price >= $5, average daily dollar volume >= $1M, top 20 shortlist, at most 5 new theses a day, at most 3 story proposals a week, buy-worthy needs score in top 20% and coverage >= 70% and Altman Z not below 1.8, weights growth 30 / surprise 15 / momentum 20 / quality 20 / insider 15, horizons 3/6/12 months vs SPY and QQQ, "too few calls" banner below 20 scored calls, SEC under 10 req/s (bucket at 8/s), 3 retries on timeout/5xx, no retry on SEC 403, screen fails if SPY or QQQ is missing/stale or more than 5% of the universe lacks a current price.

## Deviations from the spec (decided while planning; tell the person at hand-off)

1. **Ported from `trader`:** only `growth.py`, `quality.py` and `valuation.py`. `ratios.py`, `scoring/factors.py` and `scoring/composite.py` are not ported: `scorecard.py` replaces them, and porting unused code breaks the "no dead code" rule.
2. **Golden numbers** are hand-worked on synthetic series written into the tests (exact expected values in this plan). The real-company check is a live step in Task 19 (compare NVDA figures against its filing).
3. **Market calendar** comes from Alpaca's `GET /v2/calendar` on the paper trading host (it is not on the data host). The order guard still refuses every orders/positions/account path and the live host.
4. **Screening data uses SEC frames, not companyfacts**, to keep nightly evidence small: frames that ended more than 270 days ago are fetched once and cached; recent ones are refreshed every `frames_refresh_days`.
5. **Prices:** nightly fetch is the last 7 calendar days for every symbol; a full 400-day refetch happens only for new symbols or when the overlap disagrees (a split or dividend re-adjusted history). Ledger returns are recomputed from the current adjusted series, so the ledger stores the session date, not a price.
6. **Verdicts:** `calls.py verdict` records every thesis verdict in the ledger (kind `verdict`), records a `call` when it becomes buy-worthy, and a `withdrawn` row when an open call's verdict drops. The digest reads upgrades and downgrades from these rows.
7. **Insider coverage:** the insider factor is "could not tell" for every company until the Form 4 store covers 90 days (`edgar.py --backfill-form4 --days 90`, run once in Task 19).
8. **Two SEC reads the spec implies but did not name:** `edgar.py --read URL` (any SEC filing, saved as evidence before Claude reads it) and `edgar.py --eight-k` (recent 8-Ks for the story lane). Task 17.
9. **Weekly and daily limits** (3 story proposals a week, 5 new theses a day) are prose rules in the loop files for v1, not tool refusals. Per "rules -> tools -> guards", they become a `calls.py` refusal if the lessons review shows them being broken.

## Review Focus

1. **SEC and Alpaca spell share classes differently** (`BRK-B` vs `BRK.B`). Expected: the same company maps to one symbol, and its price is found. Pinned in Task 4 (`test_sec_to_alpaca_symbol`).
2. **Fiscal years that do not match calendar years** (NVDA's year ends in late January). Expected: quarterly series built from each fact's own start/end dates, with Q4 derived from the annual figure; never from the frame's calendar label. Pinned in Task 5 (`test_q4_derived_for_offset_fiscal_year`).
3. **A loop re-run on the same day** (after a crash or a resumed session). Expected: evidence ids do not collide, and the same verdict for the same ticker is not recorded twice. Pinned in Task 2 (`test_save_read_same_url_twice_same_day`) and Task 12 (`test_same_verdict_same_day_refused`).
4. **Weekends and US market holidays.** Expected: Saturday's run after a Friday close is not stale, and the day after a holiday does not flag the holiday as missing. Pinned in Task 6 (`test_not_stale_over_weekend_and_holiday`).
5. **A split or dividend between nightly runs.** Expected: history is refetched in full, so momentum and ledger returns are not computed across a price jump that is not real. Pinned in Task 6 (`test_overlap_disagreement_triggers_full_refetch`).

## File map (all under `bob_core/bobs/investor/`)

| File | Responsibility | Task |
|---|---|---|
| `bob.toml` | Manifest incl. `[scout]` | 1 |
| `harness/scoutpath.py` | Puts Bob + core harness on `sys.path`, pins `BOB_MANIFEST` | 1 |
| `harness/scoutcfg.py` | Validates and returns `[scout]`; state/evidence dirs | 1 |
| `harness/testkit.py` | Temp Bob, test runner | 1 |
| `selftest.sh` | Runs every Bob test and selftest | 1 |
| `harness/fetch.py` | HTTP GET with retries, 403/404 errors, rate limiter, `save_read` | 2 |
| `harness/fundamentals/{__init__,growth,quality,valuation}.py` | Ported from `trader`, plus `reverse_dcf` | 3 |
| `harness/edgar.py` | SEC client, parsers, frames cache, Form 4 store, CLI `--sync` | 4 |
| `harness/xbrl.py` | Per-company quarterly series and annual facts from cached frames | 5 |
| `harness/marketclock.py` | US Eastern time without `zoneinfo`; last completed session | 6 |
| `harness/prices.py` | Alpaca bars and calendar, price store, staleness, CLI `--refresh` | 6 |
| `harness/signals.py` | Growth, acceleration, SUE, momentum, 52-week proximity | 7 |
| `harness/insider.py` | Opportunistic insider buying signal and coverage | 8 |
| `harness/scorecard.py` | Percentiles, weighted score, coverage, gate | 9 |
| `harness/screen.py` | Universe, nightly screen, shortlist, diff, `--card` | 10 |
| `harness/thesis_lint.py`, `templates/thesis.md` | Thesis structure and citation check | 11 |
| `harness/calls.py` | Verdict and call ledger with refusals | 12 |
| `harness/ledger_score.py` | 3/6/12-month excess returns, basket curve | 13 |
| `harness/hooks/{protect_gate,order_gate,test_hooks}.py`, `.claude/settings.json` | State and order guards | 14 |
| `harness/digest.py` | Weekly digest MD + JSON | 15 |
| `harness/scout_board.py` | Board panel and page | 16 |
| `harness/edgar.py` (extended) | Filing reader (`--read`) and 8-K list (`--eight-k`) | 17 |
| `loops/*.txt`, `CLAUDE.md`, `skills/*.md`, `README.md`, `harness/test_loops.py` | Agent steering | 18 |
| (live) | Smoke checks, backfill, first runs, scheduler | 19 |

---

## Milestone A: Foundation and data

### Task 1: Scaffold the Bob, `[scout]` config, test kit

**Files:**
- Create: `bobs/investor/bob.toml` (through `new_bob.py`), `harness/scoutpath.py`, `harness/scoutcfg.py`, `harness/testkit.py`, `harness/test_scoutcfg.py`, `selftest.sh`
- Modify: `bob_core/.gitignore`, `bob_core/.claude/CLAUDE.md`, `bob_core/fleet.toml` (appended by `new_bob.py`)

**Interfaces:**
- Produces: `scoutpath.HERE`, `scoutpath.BOB`, `scoutpath.CORE` (`pathlib.Path`); `scoutcfg.load() -> dict` (the validated `[scout]` table), `scoutcfg.validate(scout) -> list[str]`, `scoutcfg.state_dir() -> Path`, `scoutcfg.evidence_dir() -> Path`, `scoutcfg.bob_dir() -> Path`, `scoutcfg.GROUPS`; `testkit.temp_bob(replace: dict[str, str] | None = None) -> Path`, `testkit.run(namespace: dict) -> int`.
- Every later module starts with this bootstrap (hooks use `.parent.parent`):

```python
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import scoutpath  # noqa: E402,F401
```

- [ ] **Step 1: Move the docs aside so `new_bob.py` gets an empty folder**

`new_bob.py` computes `{core}` from `--out`, so it must scaffold into the real `bobs/investor`, which must be empty. From `bob_core/` in Git Bash:

```bash
ASIDE=$(mktemp -d) && echo "$ASIDE" && mv bobs/investor/docs "$ASIDE/" && ls -A bobs/investor
```

Expected: the temp path printed, then nothing listed.

- [ ] **Step 2: Find the paths the manifest needs**

Run: `where claude` (in PowerShell) and `echo $TEMP` (Git Bash). Ask the person for the name and email SEC should see in the User-Agent (SEC blocks requests without a real contact). Use these values in Step 3 for `claude_bin`, `agent_log`, `supervisor_tmp_prefix` and `sec_contact`.

- [ ] **Step 3: Write the manifest to the aside folder**

Create `$ASIDE/investor.toml` (use forward slashes in Windows paths):

```toml
# Investor Scout: read-only long-term US stock scout. Every per-Bob value lives here.
# Credential tables carry names only, never values.

[bob]
slug = "investor"
name = "Investor Scout"
purpose = "Find US-listed stocks with long-term upside before the market does. Read-only, never trades; every number cites saved evidence and every call is scored against SPY and QQQ"
owner = "George"

[board]
port = 8791
title = "Investor Scout"
panels = ["scout_board"]

[loops]
templates = ["health-checks", "lessons-review"]
own = ["loops/nightly-screen.txt", "loops/story-lane.txt", "loops/weekly-digest.txt"]

[loops.schedules]
"nightly-screen" = "30 7 * * 2-6"
"story-lane" = "0 12 * * 1,3,5"
"weekly-digest" = "0 9 * * 6"
"health-checks" = "17 * * * *"
"lessons-review" = "23 9 * * 1"

[paths]
data_root = "data"
evidence_dir = "evidence"
state_dir = "state"
tracker = "docs/TRACKER.md"
release_notes = "docs/RELEASE-NOTES.md"

[runtime]
launchd_label = "com.george.bob-investor"
agent_log = "<TEMP from Step 2>/bob-investor-agent.log"
supervisor_tmp_prefix = "<TEMP from Step 2>/bob-investor-supervisor"
sessions_file = "supervisor/sessions.json"
wake_prompt = "supervisor/wake-prompt.md"
claude_bin = "<path from where claude>"

[credentials]
ALPACA_PAPER = { access = "read-only", why = "daily bars and the market calendar; never orders" }

[scout]
sec_contact = "<name> <email from the person>"
min_market_cap = 200_000_000
min_price = 5.0
min_dollar_volume = 1_000_000
top_n = 20
max_new_theses_per_day = 5
story_max_per_week = 3
buy_percentile = 80
min_coverage = 0.70
altman_veto = 1.8
max_missing_price_share = 0.05
frames_refresh_days = 3
scorecard_version = 1

[scout.weights]
growth = 0.30
surprise = 0.15
momentum = 0.20
quality = 0.20
insider = 0.15
```

The `<...>` markers are the three machine values and the contact from Step 2; replace all four before Step 4. `new_bob.py` validates the manifest and refuses it if anything is malformed.

- [ ] **Step 4: Scaffold and put the docs back**

```bash
python3 automation/new_bob.py "$ASIDE/investor.toml" --out bobs/investor
mv "$ASIDE/docs/superpowers" bobs/investor/docs/
ls bobs/investor bobs/investor/docs
```

Expected: `ok: Investor Scout scaffolded at ... core at ..\..` (or `../..`), then `CLAUDE.md bob.toml docs launch.sh loops skills supervisor` and `RELEASE-NOTES.md TRACKER.md superpowers`. Check `grep -n "\.\./\.\./CORE.md" bobs/investor/CLAUDE.md` prints the import line.

- [ ] **Step 5: Write the failing config test**

Create `bobs/investor/harness/test_scoutcfg.py`:

```python
#!/usr/bin/env python3
"""[scout] must be checked before any module trusts it: a bad weight changes every score silently.

Run:  python3 harness/test_scoutcfg.py
"""
from __future__ import annotations

import pathlib
import sys
import tomllib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import scoutpath  # noqa: E402,F401
import bobcfg  # noqa: E402
import scoutcfg  # noqa: E402
import testkit  # noqa: E402


def _scout() -> dict:
    return dict(tomllib.loads(testkit.MANIFEST.read_text())["scout"])


def test_real_manifest_passes_core_and_scout_checks():
    cfg = tomllib.loads(testkit.MANIFEST.read_text())
    assert bobcfg.validate(cfg) == [], bobcfg.validate(cfg)
    assert scoutcfg.validate(cfg["scout"]) == [], scoutcfg.validate(cfg["scout"])


def test_weights_not_summing_to_one_refused():
    scout = _scout()
    scout["weights"] = {**scout["weights"], "growth": 0.5}
    assert any("summing to 1" in e for e in scoutcfg.validate(scout))


def test_unknown_weight_group_refused():
    scout = _scout()
    scout["weights"] = {**scout["weights"], "hype": 0.0}
    assert any("scout.weights" in e for e in scoutcfg.validate(scout))


def test_missing_sec_contact_refused():
    scout = _scout()
    scout.pop("sec_contact")
    assert any("sec_contact" in e for e in scoutcfg.validate(scout))


def test_coverage_above_one_refused():
    scout = _scout()
    scout["min_coverage"] = 70
    assert any("min_coverage" in e for e in scoutcfg.validate(scout))


def test_missing_section_refused():
    assert scoutcfg.validate(None) == ["scout: required table"]


def test_data_dirs_land_under_data_root():
    bob = testkit.temp_bob()
    assert scoutcfg.state_dir() == bob / "data" / "state"
    assert scoutcfg.evidence_dir() == bob / "data" / "evidence"
    assert scoutcfg.load()["top_n"] == 20


if __name__ == "__main__":
    raise SystemExit(testkit.run(globals()))
```

- [ ] **Step 6: Run it to verify it fails**

Run: `cd bobs/investor && python3 harness/test_scoutcfg.py`
Expected: `ModuleNotFoundError: No module named 'scoutpath'`.

- [ ] **Step 7: Write `scoutpath.py`**

```python
"""Put this Bob's harness and core's harness on sys.path, and pin bobcfg to this Bob's manifest.

Core's bobcfg looks for bob.toml above core's own folder, then above the working directory. This
Bob lives inside core (bobs/investor), so a tool started from the repo root would find no manifest,
and one started anywhere else could find the wrong one. Importing this module first pins
BOB_MANIFEST to this Bob's own, unless it is already set (tests set it to a temp copy).

Usage (first lines of every module in harness/):
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
    import scoutpath  # noqa: E402,F401
"""
from __future__ import annotations

import os
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
BOB = HERE.parent
CORE = BOB.parents[1]

for _path in (str(CORE / "harness"), str(HERE)):
    if _path not in sys.path:
        sys.path.insert(0, _path)
os.environ.setdefault("BOB_MANIFEST", str(BOB / "bob.toml"))
```

- [ ] **Step 8: Write `scoutcfg.py`**

```python
#!/usr/bin/env python3
"""Read and check this Bob's [scout] section: thresholds, weights and the SEC contact.

Core's bobcfg validates the sections every Bob has and ignores [scout]. Without this check a typo in
a weight would silently change every score, and a missing SEC contact gets the machine's IP blocked
by SEC on the first request.

Usage:
    python3 harness/scoutcfg.py      # OK, or FAIL: <error> per problem in [scout]
"""
from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import scoutpath  # noqa: E402,F401
import bobcfg  # noqa: E402

GROUPS = ("growth", "surprise", "momentum", "quality", "insider")
POSITIVE = ("min_market_cap", "min_price", "min_dollar_volume")
COUNTS = ("top_n", "max_new_theses_per_day", "story_max_per_week", "scorecard_version",
          "frames_refresh_days")


def _num(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def validate(scout) -> list[str]:
    if not isinstance(scout, dict):
        return ["scout: required table"]
    errors: list[str] = []
    contact = scout.get("sec_contact")
    if not (isinstance(contact, str) and "@" in contact and " " in contact.strip()):
        errors.append('scout.sec_contact: "<name> <email>"; SEC refuses requests without a contact')
    errors += [f"scout.{k}: a number above 0" for k in POSITIVE
               if not (_num(scout.get(k)) and scout[k] > 0)]
    errors += [f"scout.{k}: an integer of at least 1" for k in COUNTS
               if not (isinstance(scout.get(k), int) and not isinstance(scout[k], bool) and scout[k] >= 1)]
    for key, low, high in (("buy_percentile", 0, 100), ("min_coverage", 0, 1),
                           ("max_missing_price_share", 0, 1), ("altman_veto", 0, 10)):
        if not (_num(scout.get(key)) and low < scout[key] <= high):
            errors.append(f"scout.{key}: a number above {low} and at most {high}")
    weights = scout.get("weights")
    if not isinstance(weights, dict) or set(weights) != set(GROUPS):
        errors.append(f"scout.weights: exactly {list(GROUPS)}")
    elif not all(_num(w) and w >= 0 for w in weights.values()) or abs(sum(weights.values()) - 1) > 1e-9:
        errors.append("scout.weights: non-negative numbers summing to 1")
    return errors


def load() -> dict:
    scout = bobcfg.config().get("scout")
    errors = validate(scout)
    if errors:
        raise bobcfg.ManifestError(errors)
    return scout


def state_dir() -> pathlib.Path:
    return bobcfg.path(bobcfg.config(), "state_dir")


def evidence_dir() -> pathlib.Path:
    return bobcfg.path(bobcfg.config(), "evidence_dir")


def bob_dir() -> pathlib.Path:
    return bobcfg.bob_root()


def main(argv: list[str]) -> int:
    try:
        load()
    except bobcfg.ManifestError as e:
        for err in e.errors:
            print(f"FAIL: {err}")
        return 1
    print("OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
```

- [ ] **Step 9: Write `testkit.py`**

```python
"""Shared test helpers: a throwaway Bob folder and core's test runner.

Every test runs against a temp copy of this Bob's real manifest. A test can then never write into
the real data folder, and a manifest change that breaks a module breaks that module's tests too.
"""
from __future__ import annotations

import os
import pathlib
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import scoutpath  # noqa: E402,F401
import bobcfg  # noqa: E402

MANIFEST = scoutpath.BOB / "bob.toml"


def temp_bob(replace: dict[str, str] | None = None) -> pathlib.Path:
    """A temp Bob folder with the real manifest (text-replaced by `replace`) and an empty theses/;
    BOB_MANIFEST and CLAUDE_CONFIG_DIR point at it until the next call."""
    bob = pathlib.Path(tempfile.mkdtemp()).resolve()
    text = MANIFEST.read_text()
    for old, new in (replace or {}).items():
        assert old in text, f"{old!r} is not in the manifest"
        text = text.replace(old, new)
    (bob / "bob.toml").write_text(text)
    (bob / "theses").mkdir()
    os.environ["BOB_MANIFEST"] = str(bob / "bob.toml")
    os.environ["CLAUDE_CONFIG_DIR"] = str(bob / "config")
    bobcfg.config.cache_clear()
    return bob


def run(namespace: dict) -> int:
    tests = [(n, f) for n, f in sorted(namespace.items()) if n.startswith("test_") and callable(f)]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"PASS {name}")
        except Exception as e:  # noqa: BLE001 - a crash is a failure, reported by name
            failed += 1
            print(f"FAIL {name}: {type(e).__name__}: {e}")
    print(f"{len(tests) - failed} of {len(tests)} passed")
    return 1 if failed else 0
```

- [ ] **Step 10: Run the test to verify it passes**

Run: `cd bobs/investor && python3 harness/test_scoutcfg.py`
Expected: `7 of 7 passed`.

- [ ] **Step 11: Break-proof the check**

With the editor, change `abs(sum(weights.values()) - 1) > 1e-9` to `abs(sum(weights.values()) - 1) > 1`. Run the test: `test_weights_not_summing_to_one_refused` must FAIL. Revert with the editor; run again: `7 of 7 passed`.

- [ ] **Step 12: Write `selftest.sh`**

```bash
#!/usr/bin/env bash
# Every test suite and module selftest of this Bob. Tests build their own temp Bob; no network.
set -uo pipefail
cd "$(dirname "$0")"
export CLAUDE_CONFIG_DIR="$(mktemp -d)"
failed=0
run() {
  if "$@" > /dev/null 2>&1; then echo "ok   $*"; else echo "FAIL $*"; failed=1; fi
}
for f in harness/test_*.py harness/hooks/test_*.py; do
  [ -e "$f" ] && run python3 "$f"
done
for f in harness/*.py harness/hooks/*.py; do
  case "$f" in */test_*) continue ;; esac
  [ -e "$f" ] && grep -qE "add_argument\(['\"]--selftest['\"]" "$f" && run python3 "$f" --selftest
done
rm -rf "$CLAUDE_CONFIG_DIR"
exit $failed
```

Run: `chmod +x bobs/investor/selftest.sh && bobs/investor/selftest.sh`
Expected: `ok   python3 harness/test_scoutcfg.py`, exit 0.

- [ ] **Step 13: Repo hygiene and core docs**

Append to `bob_core/.gitignore`:

```
# Runtime data of Bobs living in this repo (evidence, state): large and machine-specific
bobs/*/data/
```

In `bob_core/.claude/CLAUDE.md`, under "Project Structure", add after the `templates/` line:

```
bobs/              Bobs kept inside this repo for now (bobs/investor); each has its own selftest.sh. Core never imports from bobs/
```

and change the first rule bullet's "Bob-specific code goes in the Bob's folder" to "Bob-specific code goes in the Bob's folder (`bobs/<slug>` or a sibling repo)".

Run core's tests: `./selftest.sh` from `bob_core/`. Expected: every line `ok`, exit 0.

- [ ] **Step 14: Show the fleet entry, then commit**

Run `git diff fleet.toml` and show the person the appended entry (it holds an absolute local path). Then:

```bash
git add .gitignore .claude/CLAUDE.md fleet.toml bobs/investor
git status --short
```

Check no `data/` files are staged. Write the message to `$ASIDE/msg.txt`:

```
Scaffold Investor Scout Bob with [scout] config and test kit

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
```

Run: `git commit -F "$ASIDE/msg.txt"`

Shell variables do not survive between tool calls: reuse the literal path Step 1 printed wherever this task says `$ASIDE`. Later tasks write their commit message the same way, to a temp file outside the repo.

---

### Task 2: `fetch.py`: HTTP with retries, rate limit, evidence

**Files:**
- Create: `bobs/investor/harness/fetch.py`, `bobs/investor/harness/test_fetch.py`

**Interfaces:**
- Consumes: core `evidence.save(id, raw, query=, window=, extra=, base=, now=) -> Path`, `evidence.ID`.
- Produces:
  - `class FetchError(Exception)` with `.url`, `.status`; subclasses `Forbidden` (403), `NotFound` (404; carries `.evidence: Path` after `read`).
  - `class Limiter(per_second: float, clock=time.monotonic, sleep=time.sleep)` with `.wait()`.
  - `get(url: str, headers: dict, *, retries=3, backoff=1.0, timeout=60, opener=urllib.request.urlopen, sleep=time.sleep) -> str`
  - `evidence_id(prefix: str, url: str, now: dt.datetime) -> str`
  - `save_read(prefix: str, url: str, raw: str, started: dt.datetime, extra: dict | None = None, base: Path | None = None, now: dt.datetime | None = None) -> Path`
  - `read(prefix: str, url: str, headers: dict, *, limiter: Limiter | None = None, getter=get, base: Path | None = None) -> tuple[str, Path]`
  - `utcnow() -> dt.datetime`

- [ ] **Step 1: Write the failing tests**

Create `bobs/investor/harness/test_fetch.py`:

```python
#!/usr/bin/env python3
"""fetch: 403 is never retried, transient failures are, and every read lands as unique evidence.

Run:  python3 harness/test_fetch.py
"""
from __future__ import annotations

import datetime as dt
import pathlib
import socket
import sys
import tempfile
import urllib.error

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import scoutpath  # noqa: E402,F401
import evidence  # noqa: E402
import fetch  # noqa: E402
import testkit  # noqa: E402

NOW = dt.datetime(2026, 10, 13, 6, 30, tzinfo=dt.timezone.utc)


class _Resp:
    def __init__(self, body: str):
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self) -> bytes:
        return self.body.encode()


def _opener(outcomes: list):
    calls = []

    def opener(request, timeout):
        calls.append(request.full_url)
        item = outcomes.pop(0)
        if isinstance(item, int):
            raise urllib.error.HTTPError(request.full_url, item, "status", {}, None)
        if isinstance(item, BaseException):
            raise item
        return _Resp(item)
    return opener, calls


def _get(outcomes: list):
    opener, calls = _opener(outcomes)
    sleeps: list[float] = []
    try:
        return fetch.get("https://x.test/a", {}, opener=opener, sleep=sleeps.append), calls, sleeps
    except fetch.FetchError as e:
        return e, calls, sleeps


def test_403_is_not_retried():
    result, calls, _ = _get([403, "ok"])
    assert isinstance(result, fetch.Forbidden) and len(calls) == 1


def test_5xx_is_retried_with_backoff():
    result, calls, sleeps = _get([503, 502, "ok"])
    assert result == "ok" and len(calls) == 3 and sleeps == [1.0, 2.0]


def test_gives_up_after_three_retries():
    result, calls, _ = _get([500, 500, 500, 500, "never"])
    assert isinstance(result, fetch.FetchError) and result.status == 500 and len(calls) == 4


def test_timeout_is_retried():
    result, calls, _ = _get([socket.timeout("slow"), "ok"])
    assert result == "ok" and len(calls) == 2


def test_404_is_not_found_and_not_retried():
    result, calls, _ = _get([404, "ok"])
    assert isinstance(result, fetch.NotFound) and len(calls) == 1


def test_other_4xx_fails_without_retry():
    result, calls, _ = _get([400, "ok"])
    assert isinstance(result, fetch.FetchError) and result.status == 400 and len(calls) == 1


def test_limiter_spaces_requests_evenly():
    now = [0.0]
    sleeps: list[float] = []

    def sleep(s):
        sleeps.append(round(s, 6))
        now[0] += s
    limiter = fetch.Limiter(8, clock=lambda: now[0], sleep=sleep)
    for _ in range(3):
        limiter.wait()
    assert sleeps == [0.125, 0.125]


def test_evidence_id_is_valid_for_core():
    ask = fetch.evidence_id("sec-frame-us-gaap.RevenueFromContractWithCustomerExcludingAssessedTax", "https://u", NOW)
    assert evidence.ID.match(ask) and len(ask) <= 64, ask


def test_save_read_same_url_twice_same_day():
    base = pathlib.Path(tempfile.mkdtemp())
    first = fetch.save_read("sec-index", "https://u", "a", NOW, base=base, now=NOW)
    second = fetch.save_read("sec-index", "https://u", "b", NOW, base=base, now=NOW)
    assert first != second and (second / "raw.txt").read_text() == "b"


def test_read_saves_empty_evidence_on_404():
    testkit.temp_bob()

    def getter(url, headers):
        raise fetch.NotFound(url, 404, "404 not found")
    try:
        fetch.read("sec-index", "https://u/form.idx", {}, getter=getter)
    except fetch.NotFound as e:
        assert (e.evidence / "query.txt").read_text().startswith("GET https://u/form.idx")
        assert (e.evidence / "raw.txt").read_text() == ""
    else:
        raise AssertionError("NotFound was swallowed")


def test_read_returns_body_and_evidence():
    testkit.temp_bob()
    raw, where = fetch.read("alpaca-bars", "https://d/bars", {}, getter=lambda u, h: '{"bars":{}}')
    assert raw == '{"bars":{}}' and (where / "meta.json").is_file()


if __name__ == "__main__":
    raise SystemExit(testkit.run(globals()))
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd bobs/investor && python3 harness/test_fetch.py`
Expected: `ModuleNotFoundError: No module named 'fetch'`.

- [ ] **Step 3: Write `fetch.py`**

```python
#!/usr/bin/env python3
"""HTTP GET for this Bob's readers, and the one place a read becomes evidence.

Why one module: SEC answers 403 when it blocks an IP, and retrying a 403 is how a short block
becomes a long one, so 403 is never retried; timeouts and 5xx are, a few times. Every reader must
also save what it read before using it, including an empty 404 ("not found" is evidence only with
its query and time on file). Keeping both here means no reader can forget either.

Usage (from a reader):
    raw, where = fetch.read("sec-frame", url, headers, limiter=limiter)
"""
from __future__ import annotations

import datetime as dt
import hashlib
import pathlib
import re
import socket
import sys
import time
import urllib.error
import urllib.request

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import scoutpath  # noqa: E402,F401
import evidence  # noqa: E402

RETRY_STATUS = {429, 500, 502, 503, 504}
ID_CHARS = re.compile(r"[^a-z0-9._-]+")


class FetchError(Exception):
    def __init__(self, url: str, status: int | None, why: str):
        super().__init__(f"{why}: {url}")
        self.url = url
        self.status = status
        self.evidence: pathlib.Path | None = None


class Forbidden(FetchError):
    pass


class NotFound(FetchError):
    pass


class Limiter:
    """At most `per_second` requests per second, evenly spaced. SEC allows 10/s per IP; a steady
    8/s stays under it with room for clock jitter."""

    def __init__(self, per_second: float, clock=time.monotonic, sleep=time.sleep):
        self.gap = 1.0 / per_second
        self.clock = clock
        self.sleep = sleep
        self.next_at: float | None = None

    def wait(self) -> None:
        now = self.clock()
        if self.next_at is not None and now < self.next_at:
            self.sleep(self.next_at - now)
            now = self.next_at
        self.next_at = now + self.gap


def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def get(url: str, headers: dict, *, retries: int = 3, backoff: float = 1.0, timeout: float = 60,
        opener=urllib.request.urlopen, sleep=time.sleep) -> str:
    attempt = 0
    while True:
        try:
            with opener(urllib.request.Request(url, headers=headers), timeout=timeout) as resp:
                return resp.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as e:
            if e.code == 403:
                raise Forbidden(url, 403, "403 forbidden (check User-Agent / rate); not retried") from None
            if e.code == 404:
                raise NotFound(url, 404, "404 not found") from None
            if e.code not in RETRY_STATUS or attempt >= retries:
                raise FetchError(url, e.code, f"HTTP {e.code}") from None
        except (urllib.error.URLError, socket.timeout, TimeoutError, ConnectionError) as e:
            if attempt >= retries:
                raise FetchError(url, None, f"{type(e).__name__}: {e}") from None
        attempt += 1
        sleep(backoff * 2 ** (attempt - 1))


def evidence_id(prefix: str, url: str, now: dt.datetime) -> str:
    """Readable prefix, time and a hash of the URL: unique per read, at most 64 characters."""
    stem = ID_CHARS.sub("-", prefix.lower()).strip("-.")[:40]
    return f"{stem}-{now:%H%M%S}-{hashlib.sha1(url.encode()).hexdigest()[:8]}"


def save_read(prefix: str, url: str, raw: str, started: dt.datetime, extra: dict | None = None,
              base: pathlib.Path | None = None, now: dt.datetime | None = None) -> pathlib.Path:
    """Save one read. A re-run in the same second gets a -2, -3 suffix rather than an error: a
    loop resumed after a crash must not die on its own earlier evidence."""
    now = now or utcnow()
    ask = evidence_id(prefix, url, now)
    meta = {"source": prefix.split("-")[0], "url": url, **(extra or {})}
    for n in range(1, 100):
        try:
            return evidence.save(ask if n == 1 else f"{ask}-{n}", raw, query=f"GET {url}",
                                 window=(started.isoformat(), now.isoformat()), extra=meta,
                                 base=base, now=now)
        except FileExistsError:
            continue
    raise FileExistsError(f"{ask}: 99 reads of the same URL in one second")


def read(prefix: str, url: str, headers: dict, *, limiter: Limiter | None = None, getter=get,
         base: pathlib.Path | None = None) -> tuple[str, pathlib.Path]:
    if limiter is not None:
        limiter.wait()
    started = utcnow()
    try:
        raw = getter(url, headers)
    except NotFound as e:
        e.evidence = save_read(prefix, url, "", started, {"status": 404}, base)
        raise
    return raw, save_read(prefix, url, raw, started, None, base)
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd bobs/investor && python3 harness/test_fetch.py`
Expected: `11 of 11 passed`.

- [ ] **Step 5: Break-proof the 403 rule**

With the editor, change `if e.code == 403:` to `if e.code == 999:`. Run the tests: `test_403_is_not_retried` must FAIL. Revert; `11 of 11 passed`.

- [ ] **Step 6: Commit**

```bash
git add bobs/investor/harness/fetch.py bobs/investor/harness/test_fetch.py
git commit -F <msg file: "Add fetch: retries, 403 refusal, rate limit, evidence per read" + Co-Authored-By line>
```

---

### Task 3: Port fundamentals from `trader`, add reverse DCF

**Files:**
- Create: `bobs/investor/harness/fundamentals/__init__.py`, `growth.py`, `quality.py`, `valuation.py` (copied from `C:/Users/georg/src/george/trader/app/engine/fundamental/`), `bobs/investor/harness/test_fundamentals.py`

**Interfaces:**
- Produces (unchanged from `trader`): `growth.yoy_growth(current, previous) -> float | None`, `growth.cagr`, `growth.series_yoy`, `growth.series_cagr`; `quality.AnnualFacts` (frozen dataclass, all fields `float | None`: `net_income, operating_cash_flow, total_assets, current_assets, current_liabilities, long_term_debt, shares_outstanding, revenue, gross_profit, retained_earnings, ebit, market_cap, total_liabilities`), `quality.piotroski_f_score(current, previous) -> {"score": int, "max_possible": int, "components": dict}`, `quality.altman_z_score(facts) -> float | None`; `valuation.simplified_dcf(fcf, growth_rate, discount_rate, *, years=5, terminal_growth=0.025) -> float | None`.
- New: `valuation.reverse_dcf(target_value: float, fcf: float, discount_rate: float = 0.10, *, years: int = 10, terminal_growth: float = 0.025) -> float | None` (the growth rate the target value implies; `None` when not between -50% and +200% a year or inputs invalid). CLI: `python3 harness/fundamentals/valuation.py --reverse --target <value> --fcf <cash flow> [--discount 0.10]`.

- [ ] **Step 1: Copy the three files and an empty package marker**

```bash
mkdir -p bobs/investor/harness/fundamentals
cp ../trader/app/engine/fundamental/growth.py ../trader/app/engine/fundamental/quality.py ../trader/app/engine/fundamental/valuation.py bobs/investor/harness/fundamentals/
```

Create `bobs/investor/harness/fundamentals/__init__.py`:

```python
"""Fundamental maths ported from george/trader (app/engine/fundamental), stdlib only.

Kept close to the original so fixes can be compared; this Bob adds reverse_dcf in valuation.py.
"""
```

With the editor, add this line to the end of each copied file's module docstring: `Ported from george/trader app/engine/fundamental on 2026-10-13.` In `valuation.py`, delete `RelativeValuation` and `relative_to_sector` (no caller in this Bob) and the docstring paragraph about them.

- [ ] **Step 2: Write the failing tests (hand-worked numbers)**

Create `bobs/investor/harness/test_fundamentals.py`:

```python
#!/usr/bin/env python3
"""Ported fundamentals: hand-worked numbers, so a porting slip shows as a wrong value.

Run:  python3 harness/test_fundamentals.py
"""
from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import scoutpath  # noqa: E402,F401
import testkit  # noqa: E402
from fundamentals import growth, quality, valuation  # noqa: E402

PREV = quality.AnnualFacts(net_income=5, operating_cash_flow=6, total_assets=100, current_assets=40,
                           current_liabilities=30, long_term_debt=30, shares_outstanding=10,
                           revenue=80, gross_profit=32)
CURR = quality.AnnualFacts(net_income=8, operating_cash_flow=12, total_assets=100, current_assets=50,
                           current_liabilities=30, long_term_debt=20, shares_outstanding=10,
                           revenue=100, gross_profit=45)


def test_yoy_growth():
    assert abs(growth.yoy_growth(120, 100) - 0.2) < 1e-12
    assert growth.yoy_growth(120, 0) is None and growth.yoy_growth(None, 100) is None


def test_piotroski_all_nine_pass():
    r = quality.piotroski_f_score(CURR, PREV)
    assert (r["score"], r["max_possible"]) == (9, 9), r


def test_piotroski_missing_input_skips_only_that_test():
    curr = quality.AnnualFacts(net_income=8, operating_cash_flow=12, total_assets=100, current_assets=50,
                               current_liabilities=30, long_term_debt=20, shares_outstanding=None,
                               revenue=100, gross_profit=45)
    r = quality.piotroski_f_score(curr, PREV)
    assert (r["score"], r["max_possible"]) == (8, 8) and r["components"]["no_dilution"] is None


def test_altman_hand_worked():
    facts = quality.AnnualFacts(total_assets=100, current_assets=50, current_liabilities=30,
                                retained_earnings=30, ebit=10, market_cap=200, total_liabilities=50,
                                revenue=100)
    # 1.2*0.2 + 1.4*0.3 + 3.3*0.1 + 0.6*4 + 1.0*1 = 4.39
    assert abs(quality.altman_z_score(facts) - 4.39) < 1e-9


def test_altman_none_without_current_assets():
    assert quality.altman_z_score(quality.AnnualFacts(total_assets=100)) is None


def test_dcf_hand_worked():
    # year 1: 110 / 1.1 = 100; terminal 110 / 0.10 = 1100, discounted 1000; total 1100
    assert abs(valuation.simplified_dcf(100, 0.10, 0.10, years=1, terminal_growth=0.0) - 1100) < 1e-9


def test_reverse_dcf_recovers_growth():
    target = valuation.simplified_dcf(100, 0.25, 0.10, years=10)
    assert abs(valuation.reverse_dcf(target, 100) - 0.25) < 1e-6


def test_reverse_dcf_refuses_bad_inputs():
    assert valuation.reverse_dcf(1000, -5) is None
    assert valuation.reverse_dcf(1e18, 1) is None


if __name__ == "__main__":
    raise SystemExit(testkit.run(globals()))
```

- [ ] **Step 3: Run to verify it fails**

Run: `cd bobs/investor && python3 harness/test_fundamentals.py`
Expected: the copied-code tests PASS, `test_reverse_dcf_*` FAIL with `AttributeError: module 'fundamentals.valuation' has no attribute 'reverse_dcf'`.

- [ ] **Step 4: Add `reverse_dcf` and the CLI to `valuation.py`**

Append:

```python
def reverse_dcf(
    target_value: float,
    fcf: float,
    discount_rate: float = 0.10,
    *,
    years: int = 10,
    terminal_growth: float = 0.025,
) -> float | None:
    """The yearly growth over ``years`` that makes ``simplified_dcf`` equal ``target_value``.

    A plain DCF called Palantir and NVIDIA overvalued for years. Asking what the price already
    assumes ("35% a year for 10 years") turns that into a claim a thesis can argue with evidence.
    Bisection between -50% and +200%; outside that range the answer is None, not a clamp.
    """
    low, high = -0.5, 2.0
    if fcf is None or fcf <= 0 or target_value is None or target_value <= 0:
        return None

    def value(g: float) -> float | None:
        return simplified_dcf(fcf, g, discount_rate, years=years, terminal_growth=terminal_growth)

    v_low, v_high = value(low), value(high)
    if v_low is None or v_high is None or not v_low <= target_value <= v_high:
        return None
    for _ in range(200):
        mid = (low + high) / 2
        if value(mid) < target_value:
            low = mid
        else:
            high = mid
    return (low + high) / 2


def main(argv: list[str]) -> int:
    import argparse
    ap = argparse.ArgumentParser(description="Implied growth from a value and a cash flow.")
    ap.add_argument("--reverse", action="store_true", required=True)
    ap.add_argument("--target", type=float, required=True, help="market cap or enterprise value")
    ap.add_argument("--fcf", type=float, required=True, help="trailing free (or operating) cash flow")
    ap.add_argument("--discount", type=float, default=0.10)
    a = ap.parse_args(argv)
    g = reverse_dcf(a.target, a.fcf, a.discount)
    if g is None:
        print("FAIL: no growth rate between -50% and +200% a year explains this value")
        return 1
    print(f"OK implied growth {g:.1%} a year for 10 years, then 2.5%, at a {a.discount:.0%} discount rate")
    return 0


if __name__ == "__main__":
    import sys
    raise SystemExit(main(sys.argv[1:]))
```

- [ ] **Step 5: Run to verify it passes**

Run: `cd bobs/investor && python3 harness/test_fundamentals.py`
Expected: `8 of 8 passed`. Also `python3 harness/fundamentals/valuation.py --reverse --target 50000 --fcf 1000` prints `OK implied growth ...`.

- [ ] **Step 6: Commit**

```bash
git add bobs/investor/harness/fundamentals bobs/investor/harness/test_fundamentals.py
git commit -F <msg file: "Port growth, quality and valuation from trader; add reverse DCF" + Co-Authored-By line>
```

---

### Task 4: `edgar.py`: SEC client, parsers, frames cache, Form 4 store

**Files:**
- Create: `bobs/investor/harness/edgar.py`, `bobs/investor/harness/test_edgar.py`

**Interfaces:**
- Consumes: `fetch.read`, `fetch.Limiter`, `fetch.NotFound`, `fetch.Forbidden`, `fetch.FetchError`, `scoutcfg.load()`, `scoutcfg.state_dir()`.
- Produces:
  - Constants: `CONCEPTS: dict[str, list[tuple[str, str, str]]]` (signal input name -> `(taxonomy, concept, unit)` variants in priority order), `QUARTERLY`, `ANNUAL`, `INSTANT` (tuples of names), `EXCHANGES = {"NYSE", "Nasdaq"}`.
  - Parsers: `parse_tickers(text) -> list[{"cik": int, "name": str, "ticker": str, "exchange": str}]`, `parse_frame(text) -> list[{"cik": int, "val": float, "end": "YYYY-MM-DD", "start"?: "YYYY-MM-DD", "accn": str}]`, `parse_daily_index(text) -> list[{"form", "name", "cik": int, "date": "YYYY-MM-DD", "path"}]`, `parse_form4(text) -> list[{"issuer_cik": int, "owner_cik": int, "owner": str, "date": "YYYY-MM-DD", "code": str, "shares": float|None, "price": float|None, "acquired": bool, "plan": bool}]`, `parse_owner_dates(text) -> list[dt.date]`.
  - `sec_to_alpaca(ticker: str) -> str`
  - Periods: `recent_quarters(today, n) -> list[tuple[int, int]]` (completed calendar quarters, oldest first), `quarter_end(year, q) -> dt.date`, `frame_jobs(today) -> list[dict]` with keys `name, tax, concept, unit, period, period_end`.
  - `class Client(contact: str, *, getter=fetch.get, limiter=None, base=None)` with `tickers()`, `frame(tax, concept, unit, period)`, `daily_index(day) -> (rows | None, evidence)`, `owner_dates(cik)`, `form4(path)`; each returns `(parsed, evidence_path)`.
  - Store (under `<state_dir>/sec/`): `tickers.json` = `{"fetched": date, "evidence": str, "rows": [...]}`; `frames/<tax>.<concept>.<unit>.<period>.json` = `{"fetched", "evidence", "period_end", "rows"}`; `form4/<YYYY-MM-DD>.json` = list of Form 4 rows each with `"evidence"`; `form4/coverage.json` = sorted list of ISO dates read; `owners/<cik>.json` = `{"fetched", "evidence", "dates": [...]}`.
  - Functions: `sec_dir(state=None) -> Path`, `sync_tickers(client, sec) -> list[dict]`, `sync_frames(client, sec, today, refresh_days) -> int`, `sync_form4(client, sec, days, ciks, today) -> dict`, `read_json(path, default)`, `write_json(path, obj)` (atomic replace; for caches, never for ledgers).

- [ ] **Step 1: Write the failing tests**

Create `bobs/investor/harness/test_edgar.py`:

```python
#!/usr/bin/env python3
"""edgar: parsers on SEC's real formats, frame caching, and Form 4 coverage that never lies.

Run:  python3 harness/test_edgar.py
"""
from __future__ import annotations

import datetime as dt
import json
import pathlib
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import scoutpath  # noqa: E402,F401
import edgar  # noqa: E402
import testkit  # noqa: E402

TICKERS = json.dumps({"fields": ["cik", "name", "ticker", "exchange"], "data": [
    [1045810, "NVIDIA CORP", "NVDA", "Nasdaq"], [1067983, "BERKSHIRE HATHAWAY INC", "BRK-B", "NYSE"],
    [1, "SOME OTC CO", "SOTC", "OTC"], [2, "NO TICKER", "", "Nasdaq"]]})

FRAME = json.dumps({"data": [
    {"accn": "0001-26-1", "cik": 1045810, "entityName": "NVIDIA", "loc": "US-CA",
     "start": "2026-04-28", "end": "2026-07-27", "val": 46743000000},
    {"accn": "0001-26-2", "cik": 2, "entityName": "X", "loc": "US-NY", "end": "2026-06-30", "val": 5}]})

INDEX = """Description:           Daily Index of EDGAR Dissemination Feed by Form Type
Last Data Received:    Oct 12, 2026

Form Type   Company Name                                                  CIK         Date Filed  File Name
---------------------------------------------------------------------------------------------------------------------------------------------
10-K        ACME WIDGETS INC                                              1234567     20261012    edgar/data/1234567/0001234567-26-000123.txt
4           NVIDIA CORP                                                   1045810     20261012    edgar/data/1045810/0001045810-26-000200.txt
4           HUANG JEN HSUN                                                1197649     20261012    edgar/data/1045810/0001045810-26-000200.txt
SC 13G      SOME FUND LP                                                  7654321     20261012    edgar/data/7654321/0007654321-26-000001.txt
"""

FORM4 = """<SEC-DOCUMENT>
<DOCUMENT>
<TYPE>4
<TEXT>
<XML>
<?xml version="1.0"?>
<ownershipDocument>
  <issuer><issuerCik>0001045810</issuerCik><issuerTradingSymbol>NVDA</issuerTradingSymbol></issuer>
  <reportingOwner><reportingOwnerId><rptOwnerCik>0001197649</rptOwnerCik><rptOwnerName>HUANG JEN HSUN</rptOwnerName></reportingOwnerId></reportingOwner>
  <aff10b5One>0</aff10b5One>
  <nonDerivativeTable>
    <nonDerivativeTransaction>
      <transactionDate><value>2026-10-09</value></transactionDate>
      <transactionCoding><transactionCode>P</transactionCode></transactionCoding>
      <transactionAmounts>
        <transactionShares><value>1000</value></transactionShares>
        <transactionPricePerShare><value>180.5</value></transactionPricePerShare>
        <transactionAcquiredDisposedCode><value>A</value></transactionAcquiredDisposedCode>
      </transactionAmounts>
    </nonDerivativeTransaction>
  </nonDerivativeTable>
</ownershipDocument>
</XML>
</TEXT>
</DOCUMENT>
</SEC-DOCUMENT>"""

SUBMISSIONS = json.dumps({"filings": {"recent": {
    "form": ["4", "10-K", "4/A", "4"], "filingDate": ["2026-03-02", "2026-02-20", "2025-03-05", "2024-03-11"]}}})


def test_parse_tickers_keeps_only_nyse_nasdaq_with_a_ticker():
    rows = edgar.parse_tickers(TICKERS)
    assert [r["ticker"] for r in rows] == ["NVDA", "BRK-B"] and rows[0]["cik"] == 1045810


def test_sec_to_alpaca_symbol():
    assert edgar.sec_to_alpaca("BRK-B") == "BRK.B" and edgar.sec_to_alpaca("nvda") == "NVDA"


def test_parse_frame_keeps_start_only_for_durations():
    rows = edgar.parse_frame(FRAME)
    assert rows[0]["start"] == "2026-04-28" and rows[0]["val"] == 46743000000.0
    assert "start" not in rows[1] and rows[1]["cik"] == 2


def test_parse_daily_index_handles_spaced_form_types():
    rows = edgar.parse_daily_index(INDEX)
    assert [r["form"] for r in rows] == ["10-K", "4", "4", "SC 13G"]
    assert rows[1]["cik"] == 1045810 and rows[1]["date"] == "2026-10-12"
    assert rows[3]["path"] == "edgar/data/7654321/0007654321-26-000001.txt"


def test_parse_form4_purchase():
    (t,) = edgar.parse_form4(FORM4)
    assert (t["issuer_cik"], t["owner_cik"], t["code"], t["date"]) == (1045810, 1197649, "P", "2026-10-09")
    assert t["shares"] == 1000 and t["price"] == 180.5 and t["acquired"] and not t["plan"]


def test_parse_form4_plan_flag():
    (t,) = edgar.parse_form4(FORM4.replace("<aff10b5One>0</aff10b5One>", "<aff10b5One>true</aff10b5One>"))
    assert t["plan"]


def test_parse_form4_without_xml_is_empty():
    assert edgar.parse_form4("<SEC-DOCUMENT>no xml</SEC-DOCUMENT>") == []


def test_parse_owner_dates_keeps_form4_only():
    assert edgar.parse_owner_dates(SUBMISSIONS) == [dt.date(2026, 3, 2), dt.date(2025, 3, 5), dt.date(2024, 3, 11)]


def test_recent_quarters_excludes_the_current_one():
    qs = edgar.recent_quarters(dt.date(2026, 10, 13), 13)
    assert len(qs) == 13 and qs[-1] == (2026, 3) and qs[0] == (2023, 3)
    assert edgar.quarter_end(2026, 1) == dt.date(2026, 3, 31) and edgar.quarter_end(2025, 4) == dt.date(2025, 12, 31)


def test_frame_jobs_cover_every_concept_variant():
    jobs = edgar.frame_jobs(dt.date(2026, 10, 13))
    periods = {(j["name"], j["period"]) for j in jobs}
    assert ("revenue", "CY2026Q3") in periods and ("revenue", "CY2025") in periods
    assert ("shares", "CY2026Q3I") in periods and ("eps", "CY2023Q3") in periods
    assert not any(j["period"] == "CY2026" for j in jobs)


class FakeClient:
    def __init__(self, indexes: dict):
        self.indexes = indexes
        self.form4_paths: list[str] = []
        self.owner_calls: list[int] = []

    def daily_index(self, day):
        return self.indexes.get(day), pathlib.Path("ev/index")

    def form4(self, path):
        self.form4_paths.append(path)
        return edgar.parse_form4(FORM4), pathlib.Path("ev/form4")

    def owner_dates(self, cik):
        self.owner_calls.append(cik)
        return [dt.date(2025, 3, 5)], pathlib.Path("ev/owner")

    def frame(self, tax, concept, unit, period):
        return [{"cik": 1, "val": 1.0, "end": "2026-06-30"}], pathlib.Path("ev/frame")


def test_sync_form4_reads_universe_issuers_once():
    sec = pathlib.Path(tempfile.mkdtemp())
    day = dt.date(2026, 10, 12)
    client = FakeClient({day: edgar.parse_daily_index(INDEX)})
    summary = edgar.sync_form4(client, sec, [day], {1045810, 1197649}, dt.date(2026, 10, 13))
    assert client.form4_paths == ["edgar/data/1045810/0001045810-26-000200.txt"]
    assert client.owner_calls == [1197649] and summary["purchases"] == 1
    stored = json.loads((sec / "form4" / "2026-10-12.json").read_text())
    assert stored[0]["evidence"] == str(pathlib.Path("ev/form4"))
    assert json.loads((sec / "form4" / "coverage.json").read_text()) == ["2026-10-12"]


def test_sync_form4_weekend_404_is_covered_recent_weekday_404_is_not():
    sec = pathlib.Path(tempfile.mkdtemp())
    saturday, monday = dt.date(2026, 10, 10), dt.date(2026, 10, 12)
    edgar.sync_form4(FakeClient({}), sec, [saturday, monday], set(), dt.date(2026, 10, 13))
    assert json.loads((sec / "form4" / "coverage.json").read_text()) == ["2026-10-10"]


def test_sync_frames_fetches_missing_and_keeps_settled():
    sec = pathlib.Path(tempfile.mkdtemp())
    today = dt.date(2026, 10, 13)
    first = edgar.sync_frames(FakeClient({}), sec, today, refresh_days=3)
    again = edgar.sync_frames(FakeClient({}), sec, today, refresh_days=3)
    later = edgar.sync_frames(FakeClient({}), sec, today + dt.timedelta(days=3), refresh_days=3)
    assert first == len(edgar.frame_jobs(today)) and again == 0
    recent = [j for j in edgar.frame_jobs(today) if (today + dt.timedelta(days=3) - j["period_end"]).days <= edgar.SETTLED_DAYS]
    assert later == len(recent) and 0 < later < first


def test_client_sends_contact_and_turns_404_into_empty_frame():
    testkit.temp_bob()
    seen = {}

    def getter(url, headers):
        seen.update(headers)
        raise edgar.fetch.NotFound(url, 404, "404 not found")
    client = edgar.Client("Investor Scout a@b.test", getter=getter, limiter=edgar.fetch.Limiter(1000))
    rows, where = client.frame("us-gaap", "Revenues", "USD", "CY2026Q3")
    assert rows == [] and where.is_dir() and seen["User-Agent"] == "Investor Scout a@b.test"


if __name__ == "__main__":
    raise SystemExit(testkit.run(globals()))
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd bobs/investor && python3 harness/test_edgar.py`
Expected: `ModuleNotFoundError: No module named 'edgar'`.

- [ ] **Step 3: Write `edgar.py`**

```python
#!/usr/bin/env python3
"""Read SEC EDGAR for this Bob: the listed universe, XBRL frames, the daily index, Form 4s.

Why frames: one frames call returns one figure for every company in a period, so screening ~3,000
companies costs ~120 calls instead of 3,000 companyfacts downloads of several MB each. Frames that
ended long ago rarely change and are fetched once; recent ones are refreshed every few days (late
filers, amendments).

Why a Form 4 store with coverage: "no insider bought" is only true if every day of the window was
read. The store records the days read; the insider factor stays "could not tell" until the whole
window is there. A missing index is counted as read only for weekends or when it is old enough
that SEC has certainly published it; otherwise the day is retried next run.

Every read is saved as evidence (fetch.read) before it is parsed.

Usage:
    python3 harness/edgar.py --sync                      # tickers, frames, last 7 days of Form 4s
    python3 harness/edgar.py --backfill-form4 --days 90  # one-off: fill the insider window
    python3 harness/edgar.py --selftest-live             # one real read of each kind (network)
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import pathlib
import re
import sys
import xml.etree.ElementTree as ET

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import scoutpath  # noqa: E402,F401
import fetch  # noqa: E402
import scoutcfg  # noqa: E402

TICKERS_URL = "https://www.sec.gov/files/company_tickers_exchange.json"
FRAME_URL = "https://data.sec.gov/api/xbrl/frames/{tax}/{concept}/{unit}/{period}.json"
INDEX_URL = "https://www.sec.gov/Archives/edgar/daily-index/{year}/QTR{q}/form.{ymd}.idx"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik:010d}.json"
ARCHIVE_URL = "https://www.sec.gov/Archives/{path}"
EXCHANGES = {"NYSE", "Nasdaq"}
SETTLED_DAYS = 270
INDEX_PUBLISHED_DAYS = 3
OWNER_REFRESH_DAYS = 30
QUARTERS_BACK, YEARS_BACK, INSTANTS_BACK = 13, 3, 6

CONCEPTS: dict[str, list[tuple[str, str, str]]] = {
    "revenue": [("us-gaap", "RevenueFromContractWithCustomerExcludingAssessedTax", "USD"),
                ("us-gaap", "Revenues", "USD"), ("us-gaap", "SalesRevenueNet", "USD")],
    "eps": [("us-gaap", "EarningsPerShareDiluted", "USD-per-shares")],
    "net_income": [("us-gaap", "NetIncomeLoss", "USD")],
    "ocf": [("us-gaap", "NetCashProvidedByUsedInOperatingActivities", "USD")],
    "gross_profit": [("us-gaap", "GrossProfit", "USD")],
    "ebit": [("us-gaap", "OperatingIncomeLoss", "USD")],
    "assets": [("us-gaap", "Assets", "USD")],
    "current_assets": [("us-gaap", "AssetsCurrent", "USD")],
    "current_liabilities": [("us-gaap", "LiabilitiesCurrent", "USD")],
    "long_term_debt": [("us-gaap", "LongTermDebtNoncurrent", "USD")],
    "liabilities": [("us-gaap", "Liabilities", "USD")],
    "retained_earnings": [("us-gaap", "RetainedEarningsAccumulatedDeficit", "USD")],
    "shares": [("dei", "EntityCommonStockSharesOutstanding", "shares")],
}
QUARTERLY = ("revenue", "eps")
ANNUAL = ("revenue", "net_income", "ocf", "gross_profit", "ebit")
INSTANT = ("assets", "current_assets", "current_liabilities", "long_term_debt", "liabilities",
           "retained_earnings", "shares")


# ---- parsers ------------------------------------------------------------------------------------

def parse_tickers(text: str) -> list[dict]:
    doc = json.loads(text)
    rows = [dict(zip(doc["fields"], r)) for r in doc["data"]]
    return [{"cik": int(r["cik"]), "name": r["name"], "ticker": r["ticker"], "exchange": r["exchange"]}
            for r in rows if r.get("exchange") in EXCHANGES and r.get("ticker")]


def sec_to_alpaca(ticker: str) -> str:
    """SEC writes share classes with a hyphen (BRK-B), Alpaca with a dot (BRK.B)."""
    return ticker.upper().replace("-", ".")


def parse_frame(text: str) -> list[dict]:
    out = []
    for r in json.loads(text).get("data", []):
        row = {"cik": int(r["cik"]), "val": float(r["val"]), "end": r["end"], "accn": r.get("accn")}
        if "start" in r:
            row["start"] = r["start"]
        out.append(row)
    return out


def parse_daily_index(text: str) -> list[dict]:
    lines = text.splitlines()
    try:
        first = next(i for i, line in enumerate(lines) if line.startswith("-----")) + 1
    except StopIteration:
        return []
    out = []
    for line in lines[first:]:
        parts = line.rsplit(None, 3)
        if len(parts) != 4 or not parts[1].isdigit():
            continue
        head = re.split(r"\s{2,}", parts[0].strip(), maxsplit=1)
        form, name = head[0], head[1] if len(head) > 1 else ""
        out.append({"form": form, "name": name, "cik": int(parts[1]),
                    "date": f"{parts[2][:4]}-{parts[2][4:6]}-{parts[2][6:]}", "path": parts[3]})
    return out


def parse_form4(text: str) -> list[dict]:
    """Non-derivative transactions of one Form 4. The 10b5-1 box (aff10b5One, on forms since
    2023) marks pre-planned trades, which carry no information about the insider's view."""
    m = re.search(r"<XML>(.*?)</XML>", text, re.S | re.I)
    if not m:
        return []
    root = ET.fromstring(m.group(1).strip().encode())

    def val(node, path: str) -> str | None:
        el = node.find(path) if node is not None else None
        return el.text.strip() if el is not None and el.text else None

    owner = root.find("reportingOwner/reportingOwnerId")
    common = {"issuer_cik": int(val(root, "issuer/issuerCik") or 0),
              "owner_cik": int(val(owner, "rptOwnerCik") or 0),
              "owner": val(owner, "rptOwnerName"),
              "plan": (val(root, "aff10b5One") or "").lower() in ("1", "true")}
    out = []
    for t in root.findall("nonDerivativeTable/nonDerivativeTransaction"):
        shares = val(t, "transactionAmounts/transactionShares/value")
        price = val(t, "transactionAmounts/transactionPricePerShare/value")
        out.append({**common,
                    "date": (val(t, "transactionDate/value") or "")[:10],
                    "code": val(t, "transactionCoding/transactionCode"),
                    "shares": float(shares) if shares else None,
                    "price": float(price) if price else None,
                    "acquired": val(t, "transactionAmounts/transactionAcquiredDisposedCode/value") == "A"})
    return out


def parse_owner_dates(text: str) -> list[dt.date]:
    recent = json.loads(text)["filings"]["recent"]
    return [dt.date.fromisoformat(d) for form, d in zip(recent["form"], recent["filingDate"])
            if form in ("4", "4/A")]


# ---- periods ------------------------------------------------------------------------------------

def quarter_of(day: dt.date) -> int:
    return (day.month - 1) // 3 + 1


def quarter_end(year: int, q: int) -> dt.date:
    return dt.date(year, q * 3, (31, 30, 30, 31)[q - 1])


def recent_quarters(today: dt.date, n: int) -> list[tuple[int, int]]:
    """The last n completed calendar quarters, oldest first; the current quarter is never one."""
    year, q, out = today.year, quarter_of(today), []
    for _ in range(n):
        year, q = (year - 1, 4) if q == 1 else (year, q - 1)
        out.append((year, q))
    return out[::-1]


def frame_jobs(today: dt.date) -> list[dict]:
    jobs = []

    def add(name: str, period: str, end: dt.date) -> None:
        jobs.extend({"name": name, "tax": tax, "concept": concept, "unit": unit, "period": period,
                     "period_end": end} for tax, concept, unit in CONCEPTS[name])
    for name in QUARTERLY:
        for y, q in recent_quarters(today, QUARTERS_BACK):
            add(name, f"CY{y}Q{q}", quarter_end(y, q))
    for name in ANNUAL:
        for y in range(today.year - YEARS_BACK, today.year):
            add(name, f"CY{y}", dt.date(y, 12, 31))
    for name in INSTANT:
        for y, q in recent_quarters(today, INSTANTS_BACK):
            add(name, f"CY{y}Q{q}I", quarter_end(y, q))
    return jobs


# ---- client -------------------------------------------------------------------------------------

class Client:
    def __init__(self, contact: str, *, getter=fetch.get, limiter: fetch.Limiter | None = None,
                 base: pathlib.Path | None = None):
        self.headers = {"User-Agent": contact, "Accept-Encoding": "identity"}
        self.getter = getter
        self.limiter = limiter or fetch.Limiter(8)
        self.base = base

    def _read(self, prefix: str, url: str) -> tuple[str, pathlib.Path]:
        return fetch.read(prefix, url, self.headers, limiter=self.limiter, getter=self.getter,
                          base=self.base)

    def tickers(self) -> tuple[list[dict], pathlib.Path]:
        raw, where = self._read("sec-tickers", TICKERS_URL)
        return parse_tickers(raw), where

    def frame(self, tax: str, concept: str, unit: str, period: str) -> tuple[list[dict], pathlib.Path]:
        url = FRAME_URL.format(tax=tax, concept=concept, unit=unit, period=period)
        try:
            raw, where = self._read(f"sec-frame-{period}-{concept}", url)
        except fetch.NotFound as e:
            return [], e.evidence
        return parse_frame(raw), where

    def daily_index(self, day: dt.date) -> tuple[list[dict] | None, pathlib.Path]:
        url = INDEX_URL.format(year=day.year, q=quarter_of(day), ymd=f"{day:%Y%m%d}")
        try:
            raw, where = self._read("sec-index", url)
        except fetch.NotFound as e:
            return None, e.evidence
        return parse_daily_index(raw), where

    def owner_dates(self, cik: int) -> tuple[list[dt.date], pathlib.Path]:
        raw, where = self._read(f"sec-owner-{cik}", SUBMISSIONS_URL.format(cik=cik))
        return parse_owner_dates(raw), where

    def form4(self, path: str) -> tuple[list[dict], pathlib.Path]:
        raw, where = self._read("sec-form4", ARCHIVE_URL.format(path=path))
        return parse_form4(raw), where


# ---- store --------------------------------------------------------------------------------------

def sec_dir(state: pathlib.Path | None = None) -> pathlib.Path:
    return (state or scoutcfg.state_dir()) / "sec"


def read_json(path: pathlib.Path, default):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return default


def write_json(path: pathlib.Path, obj) -> None:
    """Replace a cache file atomically: a crash mid-write leaves the old copy, not half a file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, default=str))
    os.replace(tmp, path)


def sync_tickers(client, sec: pathlib.Path) -> list[dict]:
    rows, where = client.tickers()
    write_json(sec / "tickers.json", {"fetched": dt.date.today().isoformat(), "evidence": str(where), "rows": rows})
    return rows


def frame_file(sec: pathlib.Path, job: dict) -> pathlib.Path:
    return sec / "frames" / f"{job['tax']}.{job['concept']}.{job['unit']}.{job['period']}.json"


def sync_frames(client, sec: pathlib.Path, today: dt.date, refresh_days: int) -> int:
    fetched = 0
    for job in frame_jobs(today):
        path = frame_file(sec, job)
        cached = read_json(path, None)
        if cached is not None:
            if (today - job["period_end"]).days > SETTLED_DAYS:
                continue
            if (today - dt.date.fromisoformat(cached["fetched"])).days < refresh_days:
                continue
        rows, where = client.frame(job["tax"], job["concept"], job["unit"], job["period"])
        write_json(path, {"fetched": today.isoformat(), "evidence": str(where),
                      "period_end": job["period_end"].isoformat(), "rows": rows})
        fetched += 1
    return fetched


def _index_settled(day: dt.date, today: dt.date) -> bool:
    return day.weekday() >= 5 or (today - day).days > INDEX_PUBLISHED_DAYS


def sync_form4(client, sec: pathlib.Path, days: list[dt.date], ciks: set[int], today: dt.date) -> dict:
    folder = sec / "form4"
    coverage = set(read_json(folder / "coverage.json", []))
    summary = {"days_read": 0, "filings": 0, "purchases": 0}
    for day in sorted(days):
        if day.isoformat() in coverage:
            continue
        rows, _ = client.daily_index(day)
        if rows is None:
            if _index_settled(day, today):
                coverage.add(day.isoformat())
            continue
        paths = sorted({r["path"] for r in rows if r["form"] in ("4", "4/A") and r["cik"] in ciks})
        found = []
        for path in paths:
            txns, where = client.form4(path)
            found += [{**t, "evidence": str(where)} for t in txns if t["issuer_cik"] in ciks]
        for owner in sorted({t["owner_cik"] for t in found if t["code"] == "P" and not t["plan"]}):
            _refresh_owner(client, sec, owner, today)
        write_json(folder / f"{day.isoformat()}.json", found)
        coverage.add(day.isoformat())
        summary["days_read"] += 1
        summary["filings"] += len(paths)
        summary["purchases"] += sum(t["code"] == "P" and not t["plan"] for t in found)
    write_json(folder / "coverage.json", sorted(coverage))
    return summary


def _refresh_owner(client, sec: pathlib.Path, cik: int, today: dt.date) -> None:
    path = sec / "owners" / f"{cik}.json"
    cached = read_json(path, None)
    if cached and (today - dt.date.fromisoformat(cached["fetched"])).days < OWNER_REFRESH_DAYS:
        return
    dates, where = client.owner_dates(cik)
    write_json(path, {"fetched": today.isoformat(), "evidence": str(where), "dates": [d.isoformat() for d in dates]})


# ---- cli ----------------------------------------------------------------------------------------

def _days_back(today: dt.date, n: int) -> list[dt.date]:
    return [today - dt.timedelta(days=i) for i in range(n, 0, -1)]


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--sync", action="store_true")
    ap.add_argument("--backfill-form4", action="store_true")
    ap.add_argument("--days", type=int, default=90)
    ap.add_argument("--selftest-live", action="store_true")
    a = ap.parse_args(argv)
    cfg = scoutcfg.load()
    client = Client(cfg["sec_contact"])
    sec = sec_dir()
    today = fetch.utcnow().date()
    try:
        if a.selftest_live:
            rows, _ = client.tickers()
            (y, q), = recent_quarters(today, 2)[:1]
            frame, _ = client.frame("us-gaap", "Revenues", "USD", f"CY{y}Q{q}")
            index, _ = client.daily_index(today - dt.timedelta(days=7 + today.weekday()))
            print(f"OK tickers={len(rows)} frame CY{y}Q{q} rows={len(frame)} "
                  f"index rows={'none' if index is None else len(index)}")
            return 0 if rows and frame else 1
        if a.sync:
            rows = sync_tickers(client, sec)
            frames = sync_frames(client, sec, today, cfg["frames_refresh_days"])
            form4 = sync_form4(client, sec, _days_back(today, 7), {r["cik"] for r in rows}, today)
            print(f"OK tickers={len(rows)} frames_fetched={frames} form4={form4}")
            return 0
        if a.backfill_form4:
            rows = read_json(sec / "tickers.json", {}).get("rows") or sync_tickers(client, sec)
            form4 = sync_form4(client, sec, _days_back(today, a.days), {r["cik"] for r in rows}, today)
            print(f"OK backfill {a.days} days form4={form4}")
            return 0
    except fetch.Forbidden:
        print("FAIL: SEC 403 (check User-Agent / rate); stopped all SEC calls for this run")
        return 1
    except fetch.FetchError as e:
        print(f"FAIL: {e}")
        return 1
    ap.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd bobs/investor && python3 harness/test_edgar.py`
Expected: `14 of 14 passed`.

- [ ] **Step 5: Break-proof the coverage rule**

With the editor, change `_index_settled` to `return True`. Run: `test_sync_form4_weekend_404_is_covered_recent_weekday_404_is_not` must FAIL. Revert; `14 of 14 passed`.

- [ ] **Step 6: Commit**

```bash
git add bobs/investor/harness/edgar.py bobs/investor/harness/test_edgar.py
git commit -F <msg file: "Add edgar: SEC readers, frames cache and Form 4 store with coverage" + Co-Authored-By line>
```

---

### Task 5: `xbrl.py`: per-company series and annual facts from cached frames

**Files:**
- Create: `bobs/investor/harness/xbrl.py`, `bobs/investor/harness/test_xbrl.py`

**Interfaces:**
- Consumes: `edgar.CONCEPTS`, `edgar.read_json`, `quality.AnnualFacts`.
- Produces:
  - `load_frames(sec: Path) -> dict[tuple[str, str], dict[int, list[dict]]]`: `(name, kind)` with kind `"quarter" | "annual" | "instant"` -> cik -> rows sorted by `end`; each row `{"cik", "val", "end", "start"?, "accn", "evidence": str}`. When two concept variants give the same `(start, end)`, the higher-priority variant wins.
  - `quarterly_series(quarters: list[dict], annuals: list[dict]) -> list[{"start", "end", "val", "evidence": list[str], "derived": bool}]`: 80 to 100 day periods by their own dates; a missing last quarter of a 350 to 380 day year is derived as annual minus the three quarters inside it.
  - `company_series(frames, cik) -> {"revenue_q": [...], "eps_q": [...]}`
  - `annual_facts(frames, cik) -> tuple[AnnualFacts | None, AnnualFacts | None, list[str]]` (current year, previous year, evidence); `market_cap` is left `None` (screen fills it).
  - `latest_shares(frames, cik) -> tuple[float | None, str | None]` (value, evidence).

- [ ] **Step 1: Write the failing tests**

Create `bobs/investor/harness/test_xbrl.py`:

```python
#!/usr/bin/env python3
"""xbrl: series built from each fact's own dates, so offset fiscal years are not mislabelled.

Run:  python3 harness/test_xbrl.py
"""
from __future__ import annotations

import json
import pathlib
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import scoutpath  # noqa: E402,F401
import testkit  # noqa: E402
import xbrl  # noqa: E402

CIK = 1045810


def _frame(sec: pathlib.Path, stem: str, rows: list[dict]) -> None:
    (sec / "frames").mkdir(parents=True, exist_ok=True)
    (sec / "frames" / f"{stem}.json").write_text(json.dumps(
        {"fetched": "2026-10-13", "evidence": f"ev/{stem}", "period_end": "x", "rows": rows}))


def _row(start: str | None, end: str, val: float) -> dict:
    row = {"cik": CIK, "val": val, "end": end, "accn": "a"}
    if start:
        row["start"] = start
    return row


def test_load_frames_prefers_the_first_variant():
    sec = pathlib.Path(tempfile.mkdtemp())
    _frame(sec, "us-gaap.Revenues.USD.CY2026Q2", [_row("2026-04-01", "2026-06-30", 999)])
    _frame(sec, "us-gaap.RevenueFromContractWithCustomerExcludingAssessedTax.USD.CY2026Q2",
           [_row("2026-04-01", "2026-06-30", 100)])
    rows = xbrl.load_frames(sec)[("revenue", "quarter")][CIK]
    assert [r["val"] for r in rows] == [100]
    assert rows[0]["evidence"] == "ev/us-gaap.RevenueFromContractWithCustomerExcludingAssessedTax.USD.CY2026Q2"


def test_q4_derived_for_offset_fiscal_year():
    quarters = [_row("2025-01-27", "2025-04-27", 40), _row("2025-04-28", "2025-07-27", 45),
                _row("2025-07-28", "2025-10-26", 50)]
    annual = [_row("2025-01-27", "2026-01-25", 200)]
    for r in quarters + annual:
        r["evidence"] = "ev"
    series = xbrl.quarterly_series(quarters, annual)
    assert [(s["end"], s["val"], s["derived"]) for s in series] == [
        ("2025-04-27", 40, False), ("2025-07-27", 45, False), ("2025-10-26", 50, False), ("2026-01-25", 65, True)]
    assert series[-1]["start"] == "2025-10-27"


def test_no_derivation_without_three_quarters():
    quarters = [_row("2025-01-27", "2025-04-27", 40), _row("2025-07-28", "2025-10-26", 50)]
    annual = [_row("2025-01-27", "2026-01-25", 200)]
    for r in quarters + annual:
        r["evidence"] = "ev"
    assert [s["end"] for s in xbrl.quarterly_series(quarters, annual)] == ["2025-04-27", "2025-10-26"]


def test_six_month_ytd_values_are_not_quarters():
    rows = [_row("2025-01-01", "2025-06-30", 90)]
    rows[0]["evidence"] = "ev"
    assert xbrl.quarterly_series(rows, []) == []


def test_annual_facts_match_instants_by_date():
    sec = pathlib.Path(tempfile.mkdtemp())
    for year, start, end, ni, assets in (("CY2024", "2024-01-29", "2025-01-26", 70, 110),
                                         ("CY2025", "2025-01-27", "2026-01-25", 100, 150)):
        _frame(sec, f"us-gaap.NetIncomeLoss.USD.{year}", [_row(start, end, ni)])
        _frame(sec, f"us-gaap.Revenues.USD.{year}", [_row(start, end, ni * 2)])
    _frame(sec, "us-gaap.Assets.USD.CY2024Q4I", [_row(None, "2025-01-26", 110)])
    _frame(sec, "us-gaap.Assets.USD.CY2025Q4I", [_row(None, "2026-01-25", 150)])
    _frame(sec, "dei.EntityCommonStockSharesOutstanding.shares.CY2026Q1I", [_row(None, "2026-03-01", 24.3)])
    current, previous, evidence = xbrl.annual_facts(xbrl.load_frames(sec), CIK)
    assert (current.net_income, current.total_assets, current.revenue) == (100, 150, 200)
    assert (previous.net_income, previous.total_assets) == (70, 110)
    assert current.shares_outstanding == 24.3 and previous.shares_outstanding is None
    assert current.market_cap is None and evidence


def test_annual_facts_none_without_annual_rows():
    assert xbrl.annual_facts({}, CIK) == (None, None, [])


def test_latest_shares():
    sec = pathlib.Path(tempfile.mkdtemp())
    _frame(sec, "dei.EntityCommonStockSharesOutstanding.shares.CY2026Q1I", [_row(None, "2026-03-01", 24.3)])
    _frame(sec, "dei.EntityCommonStockSharesOutstanding.shares.CY2026Q2I", [_row(None, "2026-05-20", 24.4)])
    value, ev = xbrl.latest_shares(xbrl.load_frames(sec), CIK)
    assert value == 24.4 and ev.endswith("CY2026Q2I")


if __name__ == "__main__":
    raise SystemExit(testkit.run(globals()))
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd bobs/investor && python3 harness/test_xbrl.py`
Expected: `ModuleNotFoundError: No module named 'xbrl'`.

- [ ] **Step 3: Write `xbrl.py`**

```python
#!/usr/bin/env python3
"""Turn cached SEC frames into per-company quarterly series and annual facts.

Why dates, not frame labels: SEC files each fact under the calendar frame closest to it, so
NVIDIA's quarter ending 27 July sits in CY2025Q2 while a calendar company's sits there too, and a
fiscal year ending in January lands in the previous calendar year's frame. Matching on each fact's
own start and end avoids comparing the wrong quarters.

Why derive the fourth quarter: most companies never tag Q4 on its own; the 10-K reports the year.
Q4 is the year minus the three quarters inside it, and is marked derived.

Usage:
    python3 harness/xbrl.py --cik 1045810     # print the series and annual facts for one company
"""
from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import scoutpath  # noqa: E402,F401
import edgar  # noqa: E402
from fundamentals.quality import AnnualFacts  # noqa: E402

VARIANT = {v: (name, i) for name, variants in edgar.CONCEPTS.items() for i, v in enumerate(variants)}
QUARTER_DAYS, YEAR_DAYS = (80, 100), (350, 380)
INSTANT_TOLERANCE_DAYS = 20
SHARES_WINDOW_DAYS = 120
FIELDS = {"net_income": "net_income", "ocf": "operating_cash_flow", "revenue": "revenue",
          "gross_profit": "gross_profit", "ebit": "ebit"}
INSTANT_FIELDS = {"assets": "total_assets", "current_assets": "current_assets",
                  "current_liabilities": "current_liabilities", "long_term_debt": "long_term_debt",
                  "liabilities": "total_liabilities", "retained_earnings": "retained_earnings"}


def _date(text: str) -> dt.date:
    return dt.date.fromisoformat(text)


def _span(row: dict) -> int | None:
    return (_date(row["end"]) - _date(row["start"])).days if row.get("start") else None


def _kind(period: str) -> str:
    return "instant" if period.endswith("I") else "quarter" if "Q" in period else "annual"


def load_frames(sec: pathlib.Path) -> dict[tuple[str, str], dict[int, list[dict]]]:
    best: dict[tuple, tuple[int, dict]] = {}
    for path in sorted((sec / "frames").glob("*.json")):
        tax, rest = path.stem.split(".", 1)
        concept, unit, period = rest.rsplit(".", 2)
        name, priority = VARIANT.get((tax, concept, unit), (None, 0))
        if name is None:
            continue
        doc = edgar.read_json(path, {})
        for row in doc.get("rows", []):
            key = (name, _kind(period), row["cik"], row.get("start"), row["end"])
            if key not in best or priority < best[key][0]:
                best[key] = (priority, {**row, "evidence": doc.get("evidence")})
    out: dict[tuple[str, str], dict[int, list[dict]]] = {}
    for (name, kind, cik, _, _), (_, row) in best.items():
        out.setdefault((name, kind), {}).setdefault(cik, []).append(row)
    for by_cik in out.values():
        for rows in by_cik.values():
            rows.sort(key=lambda r: r["end"])
    return out


def quarterly_series(quarters: list[dict], annuals: list[dict]) -> list[dict]:
    qs = [r for r in quarters if _span(r) is not None and QUARTER_DAYS[0] <= _span(r) <= QUARTER_DAYS[1]]
    by_end = {r["end"]: {"start": r["start"], "end": r["end"], "val": r["val"],
                         "evidence": [r["evidence"]], "derived": False} for r in qs}
    for year in annuals:
        span = _span(year)
        if span is None or not YEAR_DAYS[0] <= span <= YEAR_DAYS[1] or year["end"] in by_end:
            continue
        inside = sorted((q for q in qs if q["start"] >= year["start"] and q["end"] <= year["end"]),
                        key=lambda q: q["end"])
        if len(inside) != 3 or (_date(year["end"]) - _date(inside[-1]["end"])).days < QUARTER_DAYS[0]:
            continue
        by_end[year["end"]] = {
            "start": (_date(inside[-1]["end"]) + dt.timedelta(days=1)).isoformat(), "end": year["end"],
            "val": year["val"] - sum(q["val"] for q in inside),
            "evidence": [year["evidence"], *(q["evidence"] for q in inside)], "derived": True}
    return sorted(by_end.values(), key=lambda r: r["end"])


def _rows(frames: dict, name: str, kind: str, cik: int) -> list[dict]:
    return frames.get((name, kind), {}).get(cik, [])


def company_series(frames: dict, cik: int) -> dict:
    return {"revenue_q": quarterly_series(_rows(frames, "revenue", "quarter", cik),
                                          _rows(frames, "revenue", "annual", cik)),
            "eps_q": quarterly_series(_rows(frames, "eps", "quarter", cik), [])}


def _closest(rows: list[dict], target: dt.date, before: int, after: int) -> dict | None:
    """The row whose end is nearest `target`, within `before` days earlier and `after` days later."""
    near = [r for r in rows if -before <= (_date(r["end"]) - target).days <= after]
    return min(near, key=lambda r: abs((_date(r["end"]) - target).days), default=None)


def _facts_for(frames: dict, cik: int, end: str) -> tuple[AnnualFacts, list[str]]:
    target = _date(end)
    values: dict[str, float] = {}
    evidence: list[str] = []
    for name, field in FIELDS.items():
        row = next((r for r in _rows(frames, name, "annual", cik) if r["end"] == end), None)
        if row:
            values[field] = row["val"]
            evidence.append(row["evidence"])
    for name, field in INSTANT_FIELDS.items():
        row = _closest(_rows(frames, name, "instant", cik), target, INSTANT_TOLERANCE_DAYS, INSTANT_TOLERANCE_DAYS)
        if row:
            values[field] = row["val"]
            evidence.append(row["evidence"])
    shares = _closest(_rows(frames, "shares", "instant", cik), target, 0, SHARES_WINDOW_DAYS)
    if shares:
        values["shares_outstanding"] = shares["val"]
        evidence.append(shares["evidence"])
    return AnnualFacts(**values), evidence


def annual_facts(frames: dict, cik: int) -> tuple[AnnualFacts | None, AnnualFacts | None, list[str]]:
    years = [r for name in ("net_income", "revenue") for r in _rows(frames, name, "annual", cik)
             if YEAR_DAYS[0] <= (_span(r) or 0) <= YEAR_DAYS[1]]
    if not years:
        return None, None, []
    latest = max(r["end"] for r in years)
    current, evidence = _facts_for(frames, cik, latest)
    prior_end = next((r["end"] for r in sorted(years, key=lambda r: r["end"], reverse=True)
                      if 335 <= (_date(latest) - _date(r["end"])).days <= 395), None)
    if prior_end is None:
        return current, None, evidence
    previous, more = _facts_for(frames, cik, prior_end)
    return current, previous, evidence + more


def latest_shares(frames: dict, cik: int) -> tuple[float | None, str | None]:
    rows = _rows(frames, "shares", "instant", cik)
    return (rows[-1]["val"], rows[-1]["evidence"]) if rows else (None, None)


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--cik", type=int, required=True)
    a = ap.parse_args(argv)
    frames = load_frames(edgar.sec_dir())
    current, previous, _ = annual_facts(frames, a.cik)
    print(json.dumps({"series": company_series(frames, a.cik),
                      "current": dataclasses.asdict(current) if current else None,
                      "previous": dataclasses.asdict(previous) if previous else None,
                      "shares": latest_shares(frames, a.cik)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd bobs/investor && python3 harness/test_xbrl.py`
Expected: `7 of 7 passed`.

- [ ] **Step 5: Break-proof the date matching**

With the editor, change `QUARTER_DAYS, YEAR_DAYS = (80, 100), (350, 380)` to `QUARTER_DAYS, YEAR_DAYS = (80, 200), (350, 380)`. Run: `test_six_month_ytd_values_are_not_quarters` must FAIL. Revert; `7 of 7 passed`.

- [ ] **Step 6: Commit**

```bash
git add bobs/investor/harness/xbrl.py bobs/investor/harness/test_xbrl.py
git commit -F <msg file: "Add xbrl: date-matched quarterly series, derived Q4, annual facts" + Co-Authored-By line>
```

---

### Task 6: `marketclock.py` and `prices.py`: Alpaca bars, calendar, staleness

**Files:**
- Create: `bobs/investor/harness/marketclock.py`, `bobs/investor/harness/prices.py`, `bobs/investor/harness/test_prices.py`

**Interfaces:**
- Consumes: `fetch.read`, `fetch.FetchError`, `edgar.read_json`, `edgar.sec_dir`, `edgar.sec_to_alpaca`, `scoutcfg.state_dir()`.
- Produces:
  - `marketclock.et_offset(utc: dt.datetime) -> dt.timedelta`, `marketclock.to_et(utc) -> dt.datetime` (naive, Eastern), `marketclock.last_session(sessions: list[dict], now_utc) -> str | None` (sessions are `{"date": "YYYY-MM-DD", "close": "HH:MM"}`; a session counts once its close plus 60 minutes has passed), `marketclock.is_stale(last_bar: str | None, sessions, now_utc) -> bool`.
  - `prices.BENCHMARKS = ("SPY", "QQQ")`, `prices.credentials(env=os.environ) -> dict`, `class prices.Client(headers, *, getter=fetch.get, base=None)` with `bars(symbols, start, end) -> (dict[str, list[[date, close, volume]]], list[Path])` and `calendar(start, end) -> (list[{"date", "close"}], Path)`.
  - `prices.merge(old, new) -> (bars, changed: bool)`, `prices.refresh(client, symbols, today, folder) -> {"symbols": int, "full": int}`, `prices.prices_dir() -> Path`, `prices.load(symbol, folder=None) -> {"bars": [...], "evidence": [...]}`, `prices.sessions(folder=None) -> list[dict]`.
  - Store: `<state>/prices/<SYMBOL>.json` = `{"bars": [[date, adj_close, volume], ...], "evidence": [up to 5 paths]}`; `<state>/prices/_calendar.json` = `{"fetched", "evidence", "sessions": [...]}`.

- [ ] **Step 1: Write the failing tests**

Create `bobs/investor/harness/test_prices.py`:

```python
#!/usr/bin/env python3
"""prices: Eastern time without zoneinfo, holiday-aware staleness, re-adjusted history refetched.

Run:  python3 harness/test_prices.py
"""
from __future__ import annotations

import datetime as dt
import json
import pathlib
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import scoutpath  # noqa: E402,F401
import fetch  # noqa: E402
import marketclock  # noqa: E402
import prices  # noqa: E402
import testkit  # noqa: E402

UTC = dt.timezone.utc


def _utc(*args) -> dt.datetime:
    return dt.datetime(*args, tzinfo=UTC)


def test_et_offset_dst_edges():
    hours = lambda t: marketclock.et_offset(t).total_seconds() / 3600  # noqa: E731
    assert hours(_utc(2026, 3, 8, 6, 59)) == -5 and hours(_utc(2026, 3, 8, 7, 0)) == -4
    assert hours(_utc(2026, 11, 1, 5, 59)) == -4 and hours(_utc(2026, 11, 1, 6, 0)) == -5


def test_last_session_waits_for_close_plus_an_hour():
    sessions = [{"date": "2026-10-09", "close": "16:00"}, {"date": "2026-10-12", "close": "16:00"}]
    assert marketclock.last_session(sessions, _utc(2026, 10, 12, 21, 0)) == "2026-10-12"
    assert marketclock.last_session(sessions, _utc(2026, 10, 12, 20, 30)) == "2026-10-09"


def test_not_stale_over_weekend_and_holiday():
    sessions = [{"date": "2026-07-02", "close": "16:00"}, {"date": "2026-07-06", "close": "16:00"}]
    assert not marketclock.is_stale("2026-07-02", sessions, _utc(2026, 7, 4, 6, 30))
    weekend = [{"date": "2026-10-09", "close": "16:00"}]
    assert not marketclock.is_stale("2026-10-09", weekend, _utc(2026, 10, 10, 5, 30))


def test_stale_when_last_bar_is_behind_or_missing():
    sessions = [{"date": "2026-10-09", "close": "16:00"}, {"date": "2026-10-12", "close": "16:00"}]
    now = _utc(2026, 10, 13, 5, 30)
    assert marketclock.is_stale("2026-10-09", sessions, now) and marketclock.is_stale(None, sessions, now)


def test_merge_appends_and_flags_disagreement():
    old = [["2026-10-08", 10.0, 5], ["2026-10-09", 11.0, 5]]
    merged, changed = prices.merge(old, [["2026-10-09", 11.0, 6], ["2026-10-12", 12.0, 7]])
    assert [b[0] for b in merged] == ["2026-10-08", "2026-10-09", "2026-10-12"] and not changed
    _, changed = prices.merge(old, [["2026-10-09", 5.5, 6]])
    assert changed


class FakeBars:
    def __init__(self, recent: dict, full: dict):
        self.recent, self.full, self.full_calls = recent, full, []

    def bars(self, symbols, start, end):
        if (end - start).days <= prices.RECENT_DAYS:
            return {s: self.recent[s] for s in symbols if s in self.recent}, [pathlib.Path("ev/recent")]
        self.full_calls.append(list(symbols))
        return {s: self.full[s] for s in symbols if s in self.full}, [pathlib.Path("ev/full")]


def _store(folder: pathlib.Path, symbol: str, bars: list) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{symbol}.json").write_text(json.dumps({"bars": bars, "evidence": ["ev/old"]}))


def test_overlap_disagreement_triggers_full_refetch():
    folder = pathlib.Path(tempfile.mkdtemp())
    _store(folder, "AAA", [["2026-10-08", 20.0, 1], ["2026-10-09", 22.0, 1]])
    _store(folder, "BBB", [["2026-10-09", 5.0, 1]])
    client = FakeBars(recent={"AAA": [["2026-10-09", 11.0, 1], ["2026-10-12", 11.5, 1]],
                              "BBB": [["2026-10-09", 5.0, 1], ["2026-10-12", 5.1, 1]]},
                      full={"AAA": [["2026-10-08", 10.0, 1], ["2026-10-09", 11.0, 1], ["2026-10-12", 11.5, 1]]})
    summary = prices.refresh(client, ["AAA", "BBB"], dt.date(2026, 10, 13), folder)
    assert client.full_calls == [["AAA"]] and summary == {"symbols": 2, "full": 1}
    assert prices.load("AAA", folder)["bars"][0] == ["2026-10-08", 10.0, 1]
    assert [b[0] for b in prices.load("BBB", folder)["bars"]] == ["2026-10-09", "2026-10-12"]


def test_new_symbol_gets_full_history():
    folder = pathlib.Path(tempfile.mkdtemp())
    client = FakeBars(recent={"NEW": [["2026-10-12", 3.0, 1]]}, full={"NEW": [["2025-01-02", 1.0, 1]]})
    prices.refresh(client, ["NEW"], dt.date(2026, 10, 13), folder)
    assert client.full_calls == [["NEW"]] and prices.load("NEW", folder)["evidence"] == [str(pathlib.Path("ev/full"))]


def test_client_refuses_trading_urls():
    called = []
    client = prices.Client({}, getter=lambda u, h: called.append(u) or "{}")
    try:
        client._read("alpaca-x", "https://paper-api.alpaca.markets/v2/orders")
    except fetch.FetchError as e:
        assert "refused" in str(e) and called == []
    else:
        raise AssertionError("an orders URL was read")


def test_bars_follows_pages_and_converts_dates():
    testkit.temp_bob()
    pages = [{"bars": {"SPY": [{"t": "2026-10-09T04:00:00Z", "c": 600.0, "v": 10}]}, "next_page_token": "p2"},
             {"bars": {"SPY": [{"t": "2026-10-12T04:00:00Z", "c": 601.0, "v": 11}],
                       "QQQ": [{"t": "2026-10-12T04:00:00Z", "c": 500.0, "v": 9}]}, "next_page_token": None}]
    urls = []

    def getter(url, headers):
        urls.append(url)
        return json.dumps(pages[len(urls) - 1])
    bars, evidence = prices.Client({}, getter=getter).bars(["SPY", "QQQ"], dt.date(2026, 10, 1), dt.date(2026, 10, 13))
    assert bars == {"SPY": [["2026-10-09", 600.0, 10], ["2026-10-12", 601.0, 11]], "QQQ": [["2026-10-12", 500.0, 9]]}
    assert len(evidence) == 2 and "adjustment=all" in urls[0] and "page_token=p2" in urls[1]


def test_credentials_missing_refused():
    try:
        prices.credentials({})
    except fetch.FetchError as e:
        assert "ALPACA_PAPER_KEY_ID" in str(e)
    else:
        raise AssertionError("missing key accepted")
    assert prices.credentials({"ALPACA_PAPER_KEY_ID": "k", "ALPACA_PAPER_SECRET_KEY": "s"}) == {
        "APCA-API-KEY-ID": "k", "APCA-API-SECRET-KEY": "s"}


if __name__ == "__main__":
    raise SystemExit(testkit.run(globals()))
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd bobs/investor && python3 harness/test_prices.py`
Expected: `ModuleNotFoundError: No module named 'marketclock'`.

- [ ] **Step 3: Write `marketclock.py`**

```python
#!/usr/bin/env python3
"""US Eastern time and the last completed trading session, without zoneinfo.

Why not zoneinfo: on Windows it needs the third-party tzdata package, and this Bob is stdlib only.
US daylight saving has one rule since 2007 (second Sunday of March to first Sunday of November, at
02:00 local), so it is computed here and tested at both edges.

Why sessions, not weekdays: a weekday rule calls a holiday "missing data" and a Saturday run
"stale". The session list comes from Alpaca's market calendar, early closes included.

Usage:
    python3 harness/marketclock.py      # print the current Eastern time
"""
from __future__ import annotations

import datetime as dt
import sys

SETTLE = dt.timedelta(minutes=60)


def _nth_sunday(year: int, month: int, n: int) -> dt.date:
    first = dt.date(year, month, 1)
    return first + dt.timedelta(days=(6 - first.weekday()) % 7 + 7 * (n - 1))


def et_offset(utc: dt.datetime) -> dt.timedelta:
    start = dt.datetime.combine(_nth_sunday(utc.year, 3, 2), dt.time(7), dt.timezone.utc)
    end = dt.datetime.combine(_nth_sunday(utc.year, 11, 1), dt.time(6), dt.timezone.utc)
    return dt.timedelta(hours=-4 if start <= utc < end else -5)


def to_et(utc: dt.datetime) -> dt.datetime:
    return (utc + et_offset(utc)).replace(tzinfo=None)


def last_session(sessions: list[dict], now_utc: dt.datetime) -> str | None:
    now_et = to_et(now_utc)
    done = [s["date"] for s in sessions
            if dt.datetime.combine(dt.date.fromisoformat(s["date"]),
                                   dt.time.fromisoformat(s["close"])) + SETTLE <= now_et]
    return max(done, default=None)


def is_stale(last_bar: str | None, sessions: list[dict], now_utc: dt.datetime) -> bool:
    expected = last_session(sessions, now_utc)
    return last_bar is None or expected is None or last_bar < expected


def main(argv: list[str]) -> int:
    print(to_et(dt.datetime.now(dt.timezone.utc)).isoformat(timespec="minutes"), "ET")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
```

- [ ] **Step 4: Write `prices.py`**

```python
#!/usr/bin/env python3
"""Daily adjusted bars and the market calendar from Alpaca, kept per symbol under state/prices.

Read-only by construction: the client refuses any URL that is not the market-data host or the
calendar, so no bug or prompt can turn a price read into an order.

Why a 7-day nightly read: re-reading a year for ~7,000 symbols every night would store gigabytes
of evidence. The short read is compared with the stored overlap; if a split or dividend changed the
adjusted history, that symbol alone is re-read in full. Momentum and ledger returns are then never
computed across a jump that did not happen.

Keys come from the environment (ALPACA_PAPER_KEY_ID, ALPACA_PAPER_SECRET_KEY), never a file here.

Usage:
    python3 harness/prices.py --refresh          # calendar, then bars for the universe + SPY, QQQ
    python3 harness/prices.py --selftest-live    # SPY adjusted vs raw, calendar (network)
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import pathlib
import sys
import urllib.parse

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import scoutpath  # noqa: E402,F401
import edgar  # noqa: E402
import fetch  # noqa: E402
import marketclock  # noqa: E402
import scoutcfg  # noqa: E402

DATA_HOST = "https://data.alpaca.markets/"
BARS_URL = DATA_HOST + "v2/stocks/bars"
CALENDAR_URL = "https://paper-api.alpaca.markets/v2/calendar"
ALLOWED = (DATA_HOST, CALENDAR_URL + "?")
BENCHMARKS = ("SPY", "QQQ")
CHUNK = 200
RECENT_DAYS = 7
HISTORY_START = dt.date(2025, 1, 1)
HISTORY_DAYS = 400
TOLERANCE = 1e-4
KEEP_EVIDENCE = 5


def credentials(env=os.environ) -> dict:
    key, secret = env.get("ALPACA_PAPER_KEY_ID"), env.get("ALPACA_PAPER_SECRET_KEY")
    if not (key and secret):
        raise fetch.FetchError("", None, "ALPACA_PAPER_KEY_ID / ALPACA_PAPER_SECRET_KEY not set")
    return {"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret}


def _bar_date(stamp: str) -> str:
    utc = dt.datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    return marketclock.to_et(utc).date().isoformat()


class Client:
    def __init__(self, headers: dict, *, getter=fetch.get, base: pathlib.Path | None = None):
        self.headers, self.getter, self.base = headers, getter, base

    def _read(self, prefix: str, url: str) -> tuple[str, pathlib.Path]:
        if not url.startswith(ALLOWED):
            raise fetch.FetchError(url, None, "refused: prices reads only the Alpaca data host and calendar")
        return fetch.read(prefix, url, self.headers, getter=self.getter, base=self.base)

    def bars(self, symbols: list[str], start: dt.date, end: dt.date) -> tuple[dict, list[pathlib.Path]]:
        out: dict[str, list] = {}
        evidence: list[pathlib.Path] = []
        for i in range(0, len(symbols), CHUNK):
            token = None
            while True:
                query = {"symbols": ",".join(symbols[i:i + CHUNK]), "timeframe": "1Day",
                         "start": start.isoformat(), "end": end.isoformat(), "adjustment": "all",
                         "feed": "sip", "limit": 10000}
                if token:
                    query["page_token"] = token
                raw, where = self._read("alpaca-bars", f"{BARS_URL}?{urllib.parse.urlencode(query)}")
                evidence.append(where)
                doc = json.loads(raw)
                for symbol, rows in (doc.get("bars") or {}).items():
                    out.setdefault(symbol, []).extend([_bar_date(r["t"]), float(r["c"]), int(r["v"])] for r in rows)
                token = doc.get("next_page_token")
                if not token:
                    break
        return out, evidence

    def calendar(self, start: dt.date, end: dt.date) -> tuple[list[dict], pathlib.Path]:
        query = urllib.parse.urlencode({"start": start.isoformat(), "end": end.isoformat()})
        raw, where = self._read("alpaca-calendar", f"{CALENDAR_URL}?{query}")
        return [{"date": s["date"], "close": s["close"][:5]} for s in json.loads(raw)], where


def prices_dir() -> pathlib.Path:
    return scoutcfg.state_dir() / "prices"


def load(symbol: str, folder: pathlib.Path | None = None) -> dict:
    return edgar.read_json((folder or prices_dir()) / f"{symbol}.json", {"bars": [], "evidence": []})


def sessions(folder: pathlib.Path | None = None) -> list[dict]:
    return edgar.read_json((folder or prices_dir()) / "_calendar.json", {}).get("sessions", [])


def merge(old: list, new: list, tol: float = TOLERANCE) -> tuple[list, bool]:
    by_date = {b[0]: b for b in old}
    changed = any(b[0] in by_date and abs(by_date[b[0]][1] - b[1]) > tol * max(abs(b[1]), 1e-9) for b in new)
    by_date.update({b[0]: b for b in new})
    return [by_date[d] for d in sorted(by_date)], changed


def _save(folder: pathlib.Path, symbol: str, bars: list, evidence: list) -> None:
    edgar.write_json(folder / f"{symbol}.json", {"bars": bars, "evidence": [str(e) for e in evidence][-KEEP_EVIDENCE:]})


def refresh(client, symbols: list[str], today: dt.date, folder: pathlib.Path) -> dict:
    recent, recent_ev = client.bars(symbols, today - dt.timedelta(days=RECENT_DAYS), today)
    full_needed = []
    for symbol in symbols:
        stored = load(symbol, folder)
        if not stored["bars"]:
            full_needed.append(symbol)
            continue
        merged, changed = merge(stored["bars"], recent.get(symbol, []))
        if changed:
            full_needed.append(symbol)
            continue
        _save(folder, symbol, merged, stored["evidence"] + recent_ev)
    if full_needed:
        start = min(HISTORY_START, today - dt.timedelta(days=HISTORY_DAYS))
        full, full_ev = client.bars(full_needed, start, today)
        for symbol in full_needed:
            _save(folder, symbol, full.get(symbol, []), full_ev)
    return {"symbols": len(symbols), "full": len(full_needed)}


def universe_symbols() -> list[str]:
    rows = edgar.read_json(edgar.sec_dir() / "tickers.json", {}).get("rows", [])
    return sorted({edgar.sec_to_alpaca(r["ticker"]) for r in rows} | set(BENCHMARKS))


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--refresh", action="store_true")
    ap.add_argument("--selftest-live", action="store_true")
    a = ap.parse_args(argv)
    today = fetch.utcnow().date()
    try:
        client = Client(credentials())
        if a.selftest_live:
            adjusted, _ = client.bars(["SPY"], today - dt.timedelta(days=400), today)
            cal, _ = client.calendar(today - dt.timedelta(days=30), today)
            spy = adjusted.get("SPY", [])
            print(f"OK SPY bars={len(spy)} first={spy[0] if spy else None} sessions={len(cal)}")
            print("Compare the first close with an unadjusted chart: adjusted closes are lower before a dividend.")
            return 0 if spy and cal else 1
        if a.refresh:
            folder = prices_dir()
            cal, where = client.calendar(today - dt.timedelta(days=30), today + dt.timedelta(days=7))
            edgar.write_json(folder / "_calendar.json", {"fetched": today.isoformat(), "evidence": str(where), "sessions": cal})
            print(f"OK {refresh(client, universe_symbols(), today, folder)}")
            return 0
    except fetch.FetchError as e:
        print(f"FAIL: {e}")
        return 1
    ap.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
```

- [ ] **Step 5: Run to verify it passes**

Run: `cd bobs/investor && python3 harness/test_prices.py`
Expected: `10 of 10 passed`.

- [ ] **Step 6: Break-proof the URL refusal and the re-adjust rule**

With the editor, change `if not url.startswith(ALLOWED):` to `if False:`. Run: `test_client_refuses_trading_urls` must FAIL. Revert. Then change `if changed:` in `refresh` to `if False:`. Run: `test_overlap_disagreement_triggers_full_refetch` must FAIL. Revert; `10 of 10 passed`.

- [ ] **Step 7: Commit**

```bash
git add bobs/investor/harness/marketclock.py bobs/investor/harness/prices.py bobs/investor/harness/test_prices.py
git commit -F <msg file: "Add prices and marketclock: Alpaca bars, calendar, staleness, re-adjust refetch" + Co-Authored-By line>
```

---

## Milestone B: Scoring

### Task 7: `signals.py`: growth, acceleration, surprise, momentum, 52-week proximity

**Files:**
- Create: `bobs/investor/harness/signals.py`, `bobs/investor/harness/test_signals.py`

**Interfaces:**
- Consumes: `growth.yoy_growth`; series rows from `xbrl.quarterly_series` (`{"end": "YYYY-MM-DD", "val": float, ...}`); closes as `list[float]`, oldest first.
- Produces: `ttm_growth(q) -> float | None` (needs 8 consecutive quarters), `acceleration(q) -> float | None` (needs 6), `sue(q) -> float | None` (needs a year-ago match for the latest quarter plus at least 4 earlier year-on-year changes), `momentum_12_1(closes) -> float | None` (needs 253), `high_proximity(closes) -> float | None` (needs 252).

- [ ] **Step 1: Write the failing tests (hand-worked numbers)**

Create `bobs/investor/harness/test_signals.py`:

```python
#!/usr/bin/env python3
"""signals: each formula against numbers worked by hand, and None whenever the input is short.

Run:  python3 harness/test_signals.py
"""
from __future__ import annotations

import datetime as dt
import math
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import scoutpath  # noqa: E402,F401
import signals  # noqa: E402
import testkit  # noqa: E402


def _q(values: list[float], gap_after: int | None = None) -> list[dict]:
    end, out = dt.date(2023, 3, 31), []
    for i, v in enumerate(values):
        out.append({"end": end.isoformat(), "val": v})
        end += dt.timedelta(days=182 if i == gap_after else 91)
    return out


def test_ttm_growth():
    assert abs(signals.ttm_growth(_q([10, 10, 10, 10, 12, 12, 12, 12])) - 0.2) < 1e-12


def test_ttm_growth_needs_eight_consecutive_quarters():
    assert signals.ttm_growth(_q([10] * 7)) is None
    assert signals.ttm_growth(_q([10] * 8, gap_after=3)) is None


def test_acceleration():
    # latest 15 vs 10 a year ago = +50%; previous 13 vs 10 = +30%; acceleration +20 points
    assert abs(signals.acceleration(_q([10, 10, 11, 12, 13, 15])) - 0.2) < 1e-12


def test_sue_hand_worked():
    # year-on-year changes 1, 0, 1, 0, 1 then latest 2; sample stdev of the five = sqrt(0.3)
    value = signals.sue(_q([1, 1, 1, 1, 2, 1, 2, 1, 3, 3]))
    assert abs(value - 2 / math.sqrt(0.3)) < 1e-9


def test_sue_needs_history_and_variation():
    assert signals.sue(_q([1, 1, 1, 1, 2, 1, 2])) is None
    assert signals.sue(_q([1, 1, 1, 1, 2, 2, 2, 2, 3, 3])) is None


def test_momentum_skips_the_last_month():
    closes = [100.0] * 253
    closes[-22] = 130.0
    assert abs(signals.momentum_12_1(closes) - 0.3) < 1e-12
    assert signals.momentum_12_1(closes[1:]) is None


def test_high_proximity():
    closes = [100.0] * 252
    closes[10] = 125.0
    assert abs(signals.high_proximity(closes) - 0.8) < 1e-12
    assert signals.high_proximity(closes[1:]) is None


if __name__ == "__main__":
    raise SystemExit(testkit.run(globals()))
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd bobs/investor && python3 harness/test_signals.py`
Expected: `ModuleNotFoundError: No module named 'signals'`.

- [ ] **Step 3: Write `signals.py`**

```python
#!/usr/bin/env python3
"""The numbers behind the scorecard, each a plain function that returns None when it cannot tell.

None is the point: a company with two quarters of history must land as "could not tell", not as
a weak or strong score. Every function checks it has enough consecutive data before computing.

- ttm_growth: last four quarters of revenue over the four before.
- acceleration: this quarter's year-on-year growth minus last quarter's (is growth speeding up?).
- sue: standardised surprise against a seasonal random walk (same quarter last year), the
  classic stand-in when free analyst estimates do not exist (Foster 1977; Bernard and Thomas 1989).
- momentum_12_1: return over 12 months excluding the latest month, which tends to reverse.
- high_proximity: last close over the 52-week high.

Usage (library; screen.py calls it, test_signals.py pins it):
    signals.ttm_growth(series["revenue_q"])
"""
from __future__ import annotations

import datetime as dt
import pathlib
import statistics
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import scoutpath  # noqa: E402,F401
from fundamentals.growth import yoy_growth  # noqa: E402

QUARTER_STEP = (80, 100)
YEAR_STEP = (345, 385)
SUE_MIN_HISTORY, SUE_MAX_HISTORY = 4, 8
YEAR_BARS, MONTH_BARS = 252, 21


def _days(a: dict, b: dict) -> int:
    return (dt.date.fromisoformat(b["end"]) - dt.date.fromisoformat(a["end"])).days


def _consecutive(series: list[dict], n: int) -> list[float] | None:
    if len(series) < n:
        return None
    tail = series[-n:]
    if not all(QUARTER_STEP[0] <= _days(a, b) <= QUARTER_STEP[1] for a, b in zip(tail, tail[1:])):
        return None
    return [r["val"] for r in tail]


def ttm_growth(q: list[dict]) -> float | None:
    v = _consecutive(q, 8)
    return yoy_growth(sum(v[4:]), sum(v[:4])) if v else None


def acceleration(q: list[dict]) -> float | None:
    v = _consecutive(q, 6)
    if not v:
        return None
    now, before = yoy_growth(v[5], v[1]), yoy_growth(v[4], v[0])
    return None if now is None or before is None else now - before


def sue(q: list[dict]) -> float | None:
    changes = []
    for i, row in enumerate(q):
        prior = next((p for p in reversed(q[:i]) if YEAR_STEP[0] <= _days(p, row) <= YEAR_STEP[1]), None)
        if prior is not None:
            changes.append((row["end"], row["val"] - prior["val"]))
    if not changes or changes[-1][0] != q[-1]["end"]:
        return None
    history = [c for _, c in changes[-1 - SUE_MAX_HISTORY:-1]]
    if len(history) < SUE_MIN_HISTORY:
        return None
    spread = statistics.stdev(history)
    return changes[-1][1] / spread if spread > 0 else None


def momentum_12_1(closes: list[float]) -> float | None:
    if len(closes) < YEAR_BARS + 1 or closes[-YEAR_BARS - 1] <= 0:
        return None
    return closes[-MONTH_BARS - 1] / closes[-YEAR_BARS - 1] - 1


def high_proximity(closes: list[float]) -> float | None:
    if len(closes) < YEAR_BARS:
        return None
    high = max(closes[-YEAR_BARS:])
    return closes[-1] / high if high > 0 else None
```

Index arithmetic: `closes[-22]` is the close 21 sessions (one month) before the latest, `closes[-253]` the close 252 sessions (one year) before the latest; `test_momentum_skips_the_last_month` pins both.

- [ ] **Step 4: Run to verify it passes**

Run: `cd bobs/investor && python3 harness/test_signals.py`
Expected: `7 of 7 passed`.

- [ ] **Step 5: Break-proof the short-history rule**

With the editor, change `if len(series) < n:` to `if len(series) < 2:`. Run: `test_ttm_growth_needs_eight_consecutive_quarters` must FAIL. Revert; `7 of 7 passed`.

- [ ] **Step 6: Commit**

```bash
git add bobs/investor/harness/signals.py bobs/investor/harness/test_signals.py
git commit -F <msg file: "Add signals: growth, acceleration, SUE, momentum, 52-week proximity" + Co-Authored-By line>
```

---

### Task 8: `insider.py`: opportunistic insider buying

**Files:**
- Create: `bobs/investor/harness/insider.py`, `bobs/investor/harness/test_insider.py`

**Interfaces:**
- Consumes: the Form 4 store from Task 4 (`<sec>/form4/<date>.json`, `coverage.json`, `<sec>/owners/<cik>.json`), `edgar.read_json`.
- Produces: `is_routine(owner_dates: list[dt.date], trade: dt.date) -> bool`, `opportunistic_buys(txns, owners: dict[int, list[dt.date]], today, days=90) -> list[dict]`, `insider_signal(buys, market_cap) -> float | None`, `covered(coverage: list[str], today, days=90) -> bool`, `load_store(sec) -> tuple[dict[int, list[dict]], dict[int, list[dt.date]], list[str]]`, `signal_for(cik, market_cap, store, today) -> tuple[float | None, list[str]]`.

- [ ] **Step 1: Write the failing tests**

Create `bobs/investor/harness/test_insider.py`:

```python
#!/usr/bin/env python3
"""insider: only open-market, unplanned, out-of-pattern buys count, and only with full coverage.

Run:  python3 harness/test_insider.py
"""
from __future__ import annotations

import datetime as dt
import json
import pathlib
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import scoutpath  # noqa: E402,F401
import insider  # noqa: E402
import testkit  # noqa: E402

TODAY = dt.date(2026, 10, 13)
D = dt.date


def _buy(owner: int, date: str, shares: float = 1000, price: float = 10, **extra) -> dict:
    return {"issuer_cik": 7, "owner_cik": owner, "owner": f"o{owner}", "date": date, "code": "P",
            "shares": shares, "price": price, "acquired": True, "plan": False, "evidence": f"ev/{owner}", **extra}


def test_routine_means_same_month_in_each_of_three_prior_years():
    dates = [D(2023, 10, 2), D(2024, 10, 9), D(2025, 10, 1)]
    assert insider.is_routine(dates, D(2026, 10, 5))
    assert not insider.is_routine(dates[:1] + dates[2:], D(2026, 10, 5))


def test_opportunistic_buys_filter():
    owners = {1: [D(2025, 3, 1)], 2: [D(2023, 10, 2), D(2024, 10, 9), D(2025, 10, 1)], 3: []}
    txns = [_buy(1, "2026-10-01"),                       # kept
            _buy(1, "2026-09-01", plan=True),            # planned 10b5-1
            _buy(1, "2026-09-02", code="S", acquired=False),  # a sale
            _buy(2, "2026-10-02"),                       # routine October buyer
            _buy(1, "2026-06-01"),                       # outside 90 days
            _buy(9, "2026-10-03")]                       # owner history never read
    kept = insider.opportunistic_buys(txns, owners, TODAY)
    assert [(t["owner_cik"], t["date"]) for t in kept] == [(1, "2026-10-01")]


def test_signal_with_cluster_bonus():
    buys = [_buy(1, "2026-10-01", 1000, 10), _buy(2, "2026-10-02", 500, 20)]
    # (10,000 + 10,000) / 1,000,000 = 0.02, two insiders: x1.5 = 0.03
    assert abs(insider.insider_signal(buys, 1_000_000) - 0.03) < 1e-12


def test_signal_single_insider_and_no_buys():
    assert abs(insider.insider_signal([_buy(1, "2026-10-01", 1000, 10)], 1_000_000) - 0.01) < 1e-12
    assert insider.insider_signal([], 1_000_000) == 0.0
    assert insider.insider_signal([], None) is None


def _days(first: int, last: int) -> list[str]:
    return [(TODAY - dt.timedelta(days=k)).isoformat() for k in range(first, last + 1)]


def test_coverage_needs_every_settled_day():
    assert insider.covered(_days(1, 90), TODAY)
    assert insider.covered(_days(4, 90), TODAY)
    assert not insider.covered([d for d in _days(1, 90) if d != _days(10, 10)[0]], TODAY)


def test_signal_for_is_none_without_coverage():
    sec = pathlib.Path(tempfile.mkdtemp())
    (sec / "form4").mkdir()
    (sec / "owners").mkdir()
    (sec / "form4" / "2026-10-01.json").write_text(json.dumps([_buy(1, "2026-10-01")]))
    (sec / "owners" / "1.json").write_text(json.dumps({"fetched": "2026-10-02", "evidence": "e", "dates": ["2025-03-01"]}))
    (sec / "form4" / "coverage.json").write_text(json.dumps(_days(1, 30)))
    assert insider.signal_for(7, 1_000_000, insider.load_store(sec), TODAY) == (None, [])
    (sec / "form4" / "coverage.json").write_text(json.dumps(_days(1, 90)))
    value, evidence = insider.signal_for(7, 1_000_000, insider.load_store(sec), TODAY)
    assert abs(value - 0.01) < 1e-12 and evidence == ["ev/1"]


if __name__ == "__main__":
    raise SystemExit(testkit.run(globals()))
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd bobs/investor && python3 harness/test_insider.py`
Expected: `ModuleNotFoundError: No module named 'insider'`.

- [ ] **Step 3: Write `insider.py`**

```python
#!/usr/bin/env python3
"""Opportunistic insider buying: the factor with the strongest evidence in the scorecard.

Cohen, Malloy and Pomorski (2012): insiders who trade in the same month year after year (bonuses,
tax dates) carry no information; those who break their pattern earn about 0.8% a month abnormal.
So only open-market purchases (code P) count, pre-planned 10b5-1 trades are dropped, and a buyer
who traded in the same month in each of the three prior years is routine. Several insiders buying
together gets a bonus. Buys by an owner whose history was never read are left out, not guessed.

"No buying" is only true if every day of the window was read, so the signal is None until the
Form 4 store covers the window (the last 3 days may lag while SEC publishes its index).

Usage:
    python3 harness/insider.py --cik 1045810 --market-cap 4.4e12
"""
from __future__ import annotations

import argparse
import datetime as dt
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import scoutpath  # noqa: E402,F401
import edgar  # noqa: E402

WINDOW_DAYS = 90
ROUTINE_YEARS = (1, 2, 3)
CLUSTER_BONUS = 1.5
INDEX_LAG_DAYS = 3


def is_routine(owner_dates: list[dt.date], trade: dt.date) -> bool:
    return all(any(d.year == trade.year - k and d.month == trade.month for d in owner_dates)
               for k in ROUTINE_YEARS)


def opportunistic_buys(txns: list[dict], owners: dict[int, list[dt.date]], today: dt.date,
                       days: int = WINDOW_DAYS) -> list[dict]:
    start = today - dt.timedelta(days=days)
    return [t for t in txns
            if t["code"] == "P" and t["acquired"] and not t["plan"] and t["shares"] and t["price"]
            and start <= dt.date.fromisoformat(t["date"]) <= today
            and t["owner_cik"] in owners
            and not is_routine(owners[t["owner_cik"]], dt.date.fromisoformat(t["date"]))]


def insider_signal(buys: list[dict], market_cap: float | None) -> float | None:
    if not market_cap or market_cap <= 0:
        return None
    value = sum(t["shares"] * t["price"] for t in buys)
    bonus = CLUSTER_BONUS if len({t["owner_cik"] for t in buys}) >= 2 else 1.0
    return value / market_cap * bonus


def covered(coverage: list[str], today: dt.date, days: int = WINDOW_DAYS) -> bool:
    have = set(coverage)
    return all((today - dt.timedelta(days=k)).isoformat() in have for k in range(INDEX_LAG_DAYS + 1, days + 1))


def load_store(sec: pathlib.Path) -> tuple[dict[int, list[dict]], dict[int, list[dt.date]], list[str]]:
    by_issuer: dict[int, list[dict]] = {}
    for path in sorted((sec / "form4").glob("????-??-??.json")):
        for t in edgar.read_json(path, []):
            by_issuer.setdefault(t["issuer_cik"], []).append(t)
    owners = {int(p.stem): [dt.date.fromisoformat(d) for d in edgar.read_json(p, {}).get("dates", [])]
              for p in (sec / "owners").glob("*.json")}
    return by_issuer, owners, edgar.read_json(sec / "form4" / "coverage.json", [])


def signal_for(cik: int, market_cap: float | None, store: tuple, today: dt.date) -> tuple[float | None, list[str]]:
    by_issuer, owners, coverage = store
    if not covered(coverage, today):
        return None, []
    buys = opportunistic_buys(by_issuer.get(cik, []), owners, today)
    return insider_signal(buys, market_cap), [t["evidence"] for t in buys]


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--cik", type=int, required=True)
    ap.add_argument("--market-cap", type=float, required=True)
    a = ap.parse_args(argv)
    value, evidence = signal_for(a.cik, a.market_cap, load_store(edgar.sec_dir()), dt.date.today())
    print(f"{'could not tell (Form 4 window not fully read)' if value is None else value} {evidence}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd bobs/investor && python3 harness/test_insider.py`
Expected: `6 of 6 passed`.

- [ ] **Step 5: Break-proof the 10b5-1 and coverage rules**

With the editor, delete `and not t["plan"]` from `opportunistic_buys`. Run: `test_opportunistic_buys_filter` must FAIL. Revert. Then change `if not covered(coverage, today):` to `if False:`. Run: `test_signal_for_is_none_without_coverage` must FAIL. Revert; `6 of 6 passed`.

- [ ] **Step 6: Commit**

```bash
git add bobs/investor/harness/insider.py bobs/investor/harness/test_insider.py
git commit -F <msg file: "Add insider: opportunistic open-market buying with coverage check" + Co-Authored-By line>
```

---

### Task 9: `scorecard.py`: percentiles, weighted score, coverage, gate

**Files:**
- Create: `bobs/investor/harness/scorecard.py`, `bobs/investor/harness/test_scorecard.py`

**Interfaces:**
- Consumes: `scoutcfg.GROUPS`.
- Produces:
  - `GROUP_SIGNALS = {"growth": ("ttm_growth", "acceleration"), "surprise": ("eps_sue", "revenue_sue"), "momentum": ("momentum_12_1", "high_proximity"), "quality": ("piotroski",), "insider": ("insider",)}`
  - `percentile_ranks(values: dict[str, float | None]) -> dict[str, float | None]` (0 to 100, ties averaged, a single value is 50, None stays None)
  - `build(raw, weights, min_coverage) -> dict[str, dict]` where `raw[ticker][signal] = {"value": float | None, "evidence": list[str]}` and each card is `{"score": float | None, "coverage": float, "rank_pct": float | None, "groups": {group: pct | None}, "signals": {signal: {"value", "pct", "evidence"}}}`
  - `gate(card, altman: float | None, cfg: dict) -> tuple[str, str]`: status one of `"eligible"`, `"could not tell"`, `"watch"`, `"pass"`, plus a reason.

- [ ] **Step 1: Write the failing tests**

Create `bobs/investor/harness/test_scorecard.py`:

```python
#!/usr/bin/env python3
"""scorecard: missing groups shrink coverage, never the score; the gate follows the spec's order.

Run:  python3 harness/test_scorecard.py
"""
from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import scoutpath  # noqa: E402,F401
import scorecard  # noqa: E402
import testkit  # noqa: E402

WEIGHTS = {"growth": 0.30, "surprise": 0.15, "momentum": 0.20, "quality": 0.20, "insider": 0.15}
CFG = {"min_coverage": 0.70, "altman_veto": 1.8, "buy_percentile": 80}
ALL = [s for names in scorecard.GROUP_SIGNALS.values() for s in names]


def _raw(value: float, only: tuple[str, ...] | None = None) -> dict:
    return {s: {"value": value if only is None or s in only else None, "evidence": [f"ev/{s}"]} for s in ALL}


def test_percentiles_with_ties_and_gaps():
    assert scorecard.percentile_ranks({"a": 1, "b": 2, "c": 3, "d": None}) == {"a": 0.0, "b": 50.0, "c": 100.0, "d": None}
    assert scorecard.percentile_ranks({"a": 1, "b": 1, "c": 2}) == {"a": 25.0, "b": 25.0, "c": 100.0}
    assert scorecard.percentile_ranks({"a": 7}) == {"a": 50.0}


def test_full_coverage_scores_and_ranks():
    cards = scorecard.build({"A": _raw(1), "B": _raw(2), "C": _raw(3)}, WEIGHTS, 0.70)
    assert [round(cards[t]["score"], 9) for t in "ABC"] == [0.0, 50.0, 100.0]
    assert all(abs(cards[t]["coverage"] - 1.0) < 1e-9 for t in "ABC")
    assert [cards[t]["rank_pct"] for t in "ABC"] == [0.0, 50.0, 100.0]
    assert cards["C"]["signals"]["insider"]["evidence"] == ["ev/insider"]


def test_missing_groups_shrink_coverage_not_score():
    raw = {"A": _raw(1), "B": _raw(2),
           "C": _raw(3, only=("ttm_growth", "acceleration", "momentum_12_1", "high_proximity"))}
    cards = scorecard.build(raw, WEIGHTS, 0.70)
    assert abs(cards["C"]["coverage"] - 0.5) < 1e-9 and cards["C"]["score"] == 100.0
    # B: growth 50, surprise 100, momentum 50, quality 100, insider 100
    assert abs(cards["B"]["score"] - 75.0) < 1e-9
    assert cards["C"]["rank_pct"] is None and cards["B"]["rank_pct"] == 100.0


def test_gate_order():
    card = {"coverage": 1.0, "rank_pct": 90.0, "score": 70.0}
    assert scorecard.gate(card, 3.0, CFG)[0] == "eligible"
    assert scorecard.gate(card, None, CFG)[0] == "eligible"
    assert scorecard.gate(card, 1.2, CFG) == ("watch", "Altman Z 1.20 below 1.8")
    assert scorecard.gate({**card, "rank_pct": 79.9}, 3.0, CFG)[0] == "pass"
    assert scorecard.gate({**card, "coverage": 0.65, "rank_pct": None}, 3.0, CFG)[0] == "could not tell"


def test_gate_accepts_coverage_exactly_at_the_floor():
    card = {"coverage": 0.30 + 0.20 + 0.20, "rank_pct": 95.0, "score": 80.0}
    assert scorecard.gate(card, None, CFG)[0] == "eligible"


if __name__ == "__main__":
    raise SystemExit(testkit.run(globals()))
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd bobs/investor && python3 harness/test_scorecard.py`
Expected: `ModuleNotFoundError: No module named 'scorecard'`.

- [ ] **Step 3: Write `scorecard.py`**

```python
#!/usr/bin/env python3
"""The deterministic scorecard: percentile ranks, a weighted score over what is known, a gate.

Why percentiles: revenue growth, a surprise ratio and a Piotroski score live on different scales;
ranking each within the day's universe makes them comparable without hand-tuned scaling.

Why coverage is separate from score: a company with only two of five groups known must not look
strong or weak because of what is missing. The score averages the groups that have data; coverage
says how much of the weight that was, and a card under the floor is "could not tell".

Gate order (the spec's): coverage floor, then the Altman distress veto, then the top-20% rank.

Usage (library; screen.py calls it):
    cards = scorecard.build(raw, cfg["weights"], cfg["min_coverage"])
"""
from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import scoutpath  # noqa: E402,F401
import scoutcfg  # noqa: E402

GROUP_SIGNALS = {"growth": ("ttm_growth", "acceleration"), "surprise": ("eps_sue", "revenue_sue"),
                 "momentum": ("momentum_12_1", "high_proximity"), "quality": ("piotroski",),
                 "insider": ("insider",)}
assert tuple(GROUP_SIGNALS) == scoutcfg.GROUPS, "scorecard groups must match [scout.weights]"
SIGNALS = [s for names in GROUP_SIGNALS.values() for s in names]


def percentile_ranks(values: dict[str, float | None]) -> dict[str, float | None]:
    present = sorted((v, k) for k, v in values.items() if v is not None)
    out: dict[str, float | None] = {k: None for k in values}
    n = len(present)
    i = 0
    while i < n:
        j = i
        while j + 1 < n and present[j + 1][0] == present[i][0]:
            j += 1
        pct = 50.0 if n == 1 else (i + j) / 2 / (n - 1) * 100
        for k in range(i, j + 1):
            out[present[k][1]] = pct
        i = j + 1
    return out


def build(raw: dict[str, dict[str, dict]], weights: dict[str, float], min_coverage: float) -> dict[str, dict]:
    pcts = {s: percentile_ranks({t: raw[t].get(s, {}).get("value") for t in raw}) for s in SIGNALS}
    cards = {}
    for ticker, signals in raw.items():
        sig = {s: {"value": signals.get(s, {}).get("value"), "pct": pcts[s][ticker],
                   "evidence": signals.get(s, {}).get("evidence", [])} for s in SIGNALS}
        groups = {}
        for group, names in GROUP_SIGNALS.items():
            known = [sig[s]["pct"] for s in names if sig[s]["pct"] is not None]
            groups[group] = sum(known) / len(known) if known else None
        have = {g: w for g, w in weights.items() if groups[g] is not None}
        coverage = round(sum(have.values()), 9)
        score = sum(groups[g] * w for g, w in have.items()) / coverage if coverage > 0 else None
        cards[ticker] = {"score": score, "coverage": coverage, "groups": groups, "signals": sig}
    ranks = percentile_ranks({t: c["score"] for t, c in cards.items() if c["coverage"] >= min_coverage})
    for ticker, card in cards.items():
        card["rank_pct"] = ranks.get(ticker)
    return cards


def gate(card: dict, altman: float | None, cfg: dict) -> tuple[str, str]:
    if round(card["coverage"], 9) < cfg["min_coverage"]:
        return "could not tell", f"coverage {card['coverage']:.0%} below {cfg['min_coverage']:.0%}"
    if altman is not None and altman < cfg["altman_veto"]:
        return "watch", f"Altman Z {altman:.2f} below {cfg['altman_veto']}"
    if card["rank_pct"] is None or card["rank_pct"] < cfg["buy_percentile"]:
        return "pass", f"score rank {card['rank_pct'] or 0:.0f} below {cfg['buy_percentile']}"
    return "eligible", f"score rank {card['rank_pct']:.0f}, coverage {card['coverage']:.0%}"
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd bobs/investor && python3 harness/test_scorecard.py`
Expected: `5 of 5 passed`.

- [ ] **Step 5: Break-proof the coverage rule**

With the editor, in `build` change `have = {g: w for g, w in weights.items() if groups[g] is not None}` to `have = dict(weights)` and `groups[g] * w` to `(groups[g] or 0) * w`. Run: `test_missing_groups_shrink_coverage_not_score` must FAIL. Revert both; `5 of 5 passed`.

- [ ] **Step 6: Commit**

```bash
git add bobs/investor/harness/scorecard.py bobs/investor/harness/test_scorecard.py
git commit -F <msg file: "Add scorecard: percentile ranks, coverage-weighted score, verdict gate" + Co-Authored-By line>
```

---

### Task 10: `screen.py`: universe, nightly screen, shortlist, diff

**Files:**
- Create: `bobs/investor/harness/screen.py`, `bobs/investor/harness/test_screen.py`

**Interfaces:**
- Consumes: everything from Tasks 1 to 9: `scoutcfg.load/state_dir`, `edgar.read_json/write_json/sec_dir/sec_to_alpaca`, `xbrl.load_frames/company_series/annual_facts/latest_shares`, `prices.load/sessions/prices_dir/BENCHMARKS`, `marketclock.last_session/is_stale`, `signals.*`, `insider.load_store/signal_for`, `scorecard.build/gate`, `quality.piotroski_f_score/altman_z_score`.
- Produces:
  - `screen_dir() -> Path` (`<state>/screen`), `latest(folder=None) -> dict` (the last screen document, `{}` if none).
  - `candidates(tickers) -> list[dict]` (one ticker per CIK, first in SEC order, plus `"symbol"`), `price_view(bars) -> dict | None`, `passes_floors(view, market_cap, cfg) -> bool`, `raw_signals(series, current, previous, closes, insider_value, evidence) -> dict`.
  - `run(now_utc) -> tuple[int, str]` writes `<state>/screen/<session>.json` and `latest.json`:
    `{"session", "generated_at", "scorecard_version", "candidates", "unpriced", "stale_prices", "scored", "shortlist": [ticker], "previous_shortlist": [ticker], "new_entrants": [ticker], "cards": {ticker: card + {"name", "cik", "symbol", "close", "market_cap", "altman", "shares_evidence", "gate", "reason"}}}`
  - `card_text(ticker, doc) -> str`. CLI: `--run`, `--card TICKER`.

- [ ] **Step 1: Write the failing tests**

Create `bobs/investor/harness/test_screen.py`:

```python
#!/usr/bin/env python3
"""screen: a small fabricated market, end to end, including the days it must refuse to screen.

Run:  python3 harness/test_screen.py
"""
from __future__ import annotations

import datetime as dt
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import scoutpath  # noqa: E402,F401
import edgar  # noqa: E402
import screen  # noqa: E402
import testkit  # noqa: E402

NOW = dt.datetime(2026, 10, 13, 5, 30, tzinfo=dt.timezone.utc)
STOCKS = {"AAA": (1, 1e8, 0.01), "BBB": (2, 1e8, 0.03), "CCC": (3, 1e8, 0.06), "DDD": (4, 1e3, 0.02)}
LOWER_FLOOR = {"min_coverage = 0.70": "min_coverage = 0.60"}


def _sessions(n: int = 300) -> list[str]:
    day, out = dt.date(2026, 10, 12), []
    while len(out) < n:
        if day.weekday() < 5:
            out.append(day.isoformat())
        day -= dt.timedelta(days=1)
    return out[::-1]


def _put(path: pathlib.Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj))


def _world(bob: pathlib.Path) -> pathlib.Path:
    state = bob / "data" / "state"
    sec, px = state / "sec", state / "prices"
    days = _sessions()
    _put(px / "_calendar.json", {"fetched": "2026-10-13", "evidence": "ev/cal",
                                 "sessions": [{"date": d, "close": "16:00"} for d in days]})
    for symbol in ("SPY", "QQQ"):
        _put(px / f"{symbol}.json", {"bars": [[d, 100.0, 1e6] for d in days], "evidence": [f"ev/{symbol}"]})
    _put(sec / "tickers.json", {"fetched": "2026-10-13", "evidence": "ev/tickers", "rows": [
        {"cik": cik, "name": f"{t} INC", "ticker": t, "exchange": "Nasdaq"} for t, (cik, _, _) in STOCKS.items()]
        + [{"cik": 1, "name": "AAA INC", "ticker": "AAA-WT", "exchange": "Nasdaq"}]})
    quarters = edgar.recent_quarters(dt.date(2026, 10, 13), 13)
    for i, (y, q) in enumerate(quarters):
        end = edgar.quarter_end(y, q)
        py, pq = (y - 1, 4) if q == 1 else (y, q - 1)
        start = (edgar.quarter_end(py, pq) + dt.timedelta(days=1)).isoformat()
        # flat, then +g and +3g in the last two quarters: TTM growth = g, acceleration = 2g, both
        # strictly ordered by g, so the ranking below never depends on floating-point ties
        rev = [{"cik": cik, "val": 100 * (1 + g) if i == 11 else 100 * (1 + 3 * g) if i == 12 else 100.0,
                "start": start, "end": end.isoformat()} for cik, _, g in STOCKS.values()]
        eps = [{"cik": cik, "val": 1 + i * g + (i % 3) * 0.05, "start": start, "end": end.isoformat()}
               for cik, _, g in STOCKS.values()]
        _put(sec / "frames" / f"us-gaap.Revenues.USD.CY{y}Q{q}.json", {"evidence": f"ev/rev{i}", "rows": rev})
        _put(sec / "frames" / f"us-gaap.EarningsPerShareDiluted.USD-per-shares.CY{y}Q{q}.json",
             {"evidence": f"ev/eps{i}", "rows": eps})
    _put(sec / "frames" / "dei.EntityCommonStockSharesOutstanding.shares.CY2026Q2I.json",
         {"evidence": "ev/shares", "rows": [{"cik": cik, "val": sh, "end": "2026-06-30"} for cik, sh, _ in STOCKS.values()]})
    for t, (_, _, g) in STOCKS.items():
        _put(px / f"{t}.json", {"bars": [[d, 10 * (1 + g / 20) ** k, 1e6] for k, d in enumerate(days)],
                                "evidence": [f"ev/px-{t}"]})
    return state


def _drop_last_bar(state: pathlib.Path, symbol: str) -> None:
    path = state / "prices" / f"{symbol}.json"
    doc = json.loads(path.read_text())
    doc["bars"] = doc["bars"][:-1]
    path.write_text(json.dumps(doc))


def test_candidates_one_ticker_per_cik():
    rows = [{"cik": 1, "ticker": "BRK-B", "name": "B", "exchange": "NYSE"},
            {"cik": 1, "ticker": "BRK-A", "name": "B", "exchange": "NYSE"}]
    assert [(c["ticker"], c["symbol"]) for c in screen.candidates(rows)] == [("BRK-B", "BRK.B")]


def test_run_scores_ranks_and_applies_floors():
    state = _world(testkit.temp_bob(LOWER_FLOOR))
    code, message = screen.run(NOW)
    assert code == 0 and message.startswith("OK"), message
    doc = screen.latest()
    assert doc["session"] == "2026-10-12" and doc["shortlist"] == ["CCC", "BBB", "AAA"]
    assert "DDD" not in doc["cards"] and doc["new_entrants"] == ["CCC", "BBB", "AAA"]
    assert doc["cards"]["CCC"]["gate"] == "eligible" and doc["cards"]["AAA"]["gate"] == "pass"
    assert (state / "screen" / "2026-10-12.json").is_file()


def test_low_coverage_is_could_not_tell():
    _world(testkit.temp_bob())
    code, _ = screen.run(NOW)
    doc = screen.latest()
    assert code == 0 and doc["shortlist"] == []
    assert {c["gate"] for c in doc["cards"].values()} == {"could not tell"}


def test_stale_benchmark_fails_and_writes_nothing():
    state = _world(testkit.temp_bob(LOWER_FLOOR))
    _drop_last_bar(state, "SPY")
    code, message = screen.run(NOW)
    assert code == 1 and "SPY" in message and not (state / "screen" / "latest.json").exists()


def test_stale_prices_over_the_limit_fail_but_unpriced_names_do_not():
    state = _world(testkit.temp_bob(LOWER_FLOOR))
    (state / "prices" / "DDD.json").unlink()
    code, _ = screen.run(NOW)
    assert code == 0 and screen.latest()["unpriced"] == 1
    _drop_last_bar(state, "AAA")
    code, message = screen.run(NOW)
    assert code == 1 and "no current price" in message


def test_rerun_same_session_keeps_new_entrants():
    _world(testkit.temp_bob(LOWER_FLOOR))
    screen.run(NOW)
    screen.run(NOW)
    assert screen.latest()["new_entrants"] == ["CCC", "BBB", "AAA"]


def test_card_text_names_gate_and_evidence():
    _world(testkit.temp_bob(LOWER_FLOOR))
    screen.run(NOW)
    text = screen.card_text("CCC", screen.latest())
    assert "eligible" in text and "ev/px-CCC" in text and "ev/rev12" in text


if __name__ == "__main__":
    raise SystemExit(testkit.run(globals()))
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd bobs/investor && python3 harness/test_screen.py`
Expected: `ModuleNotFoundError: No module named 'screen'`.

- [ ] **Step 3: Write `screen.py`**

```python
#!/usr/bin/env python3
"""The numbers lane: score every listed company that passes the floors, keep the top of the list.

Refuses to screen rather than screen badly: no calendar, a stale SPY or QQQ, or too many companies
whose price stopped updating, and it writes nothing and exits 1. A call made on a partial or stale
market picture would sit in the ledger for a year.

Companies Alpaca never priced (warrants, odd share classes) are counted as "unpriced" and reported,
not failed on: they were never part of the picture. A price that was there and stopped is "stale"
and counts toward the failure limit.

Re-running the same session keeps the new entrants measured against the previous session, so a
resumed loop still sees which names to research.

Usage:
    python3 harness/screen.py --run           # nightly; prints OK ... or FAIL: ...
    python3 harness/screen.py --card NVDA     # the scorecard for one ticker, to paste in a thesis
"""
from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import scoutpath  # noqa: E402,F401
import edgar  # noqa: E402
import fetch  # noqa: E402
import insider  # noqa: E402
import marketclock  # noqa: E402
import prices  # noqa: E402
import scorecard  # noqa: E402
import scoutcfg  # noqa: E402
import signals  # noqa: E402
import xbrl  # noqa: E402
from fundamentals import quality  # noqa: E402

PIOTROSKI_MIN_TESTS = 6
VOLUME_DAYS = 20


def screen_dir() -> pathlib.Path:
    return scoutcfg.state_dir() / "screen"


def latest(folder: pathlib.Path | None = None) -> dict:
    return edgar.read_json((folder or screen_dir()) / "latest.json", {})


def candidates(tickers: list[dict]) -> list[dict]:
    seen, out = set(), []
    for row in tickers:
        if row["cik"] not in seen:
            seen.add(row["cik"])
            out.append({**row, "symbol": edgar.sec_to_alpaca(row["ticker"])})
    return out


def price_view(bars: list) -> dict | None:
    if not bars:
        return None
    tail = bars[-VOLUME_DAYS:]
    return {"last": bars[-1][0], "close": bars[-1][1], "closes": [b[1] for b in bars],
            "dollar_volume": sum(b[1] * b[2] for b in tail) / len(tail)}


def passes_floors(view: dict, market_cap: float | None, cfg: dict) -> bool:
    return (market_cap is not None and market_cap >= cfg["min_market_cap"]
            and view["close"] >= cfg["min_price"] and view["dollar_volume"] >= cfg["min_dollar_volume"])


def _ev(rows: list[dict]) -> list[str]:
    return list(dict.fromkeys(e for r in rows for e in r["evidence"]))


def raw_signals(series: dict, current, previous, closes: list[float], insider_value: float | None,
                evidence: dict) -> dict:
    rev, eps = series["revenue_q"], series["eps_q"]
    pio = quality.piotroski_f_score(current, previous) if current and previous else None
    pio_value = pio["score"] / pio["max_possible"] if pio and pio["max_possible"] >= PIOTROSKI_MIN_TESTS else None
    return {
        "ttm_growth": {"value": signals.ttm_growth(rev), "evidence": _ev(rev[-8:])},
        "acceleration": {"value": signals.acceleration(rev), "evidence": _ev(rev[-6:])},
        "eps_sue": {"value": signals.sue(eps), "evidence": _ev(eps[-9:])},
        "revenue_sue": {"value": signals.sue(rev), "evidence": _ev(rev[-9:])},
        "momentum_12_1": {"value": signals.momentum_12_1(closes), "evidence": evidence["prices"]},
        "high_proximity": {"value": signals.high_proximity(closes), "evidence": evidence["prices"]},
        "piotroski": {"value": pio_value, "evidence": evidence["annual"]},
        "insider": {"value": insider_value, "evidence": evidence["insider"]},
    }


def _benchmark_problem(sessions: list[dict], now_utc: dt.datetime, session: str) -> str | None:
    for symbol in prices.BENCHMARKS:
        bars = prices.load(symbol)["bars"]
        last = bars[-1][0] if bars else None
        if marketclock.is_stale(last, sessions, now_utc):
            return f"FAIL: {symbol} last bar {last}, expected {session}; no screen today"
    return None


def run(now_utc: dt.datetime) -> tuple[int, str]:
    cfg = scoutcfg.load()
    sec, out = edgar.sec_dir(), screen_dir()
    sessions = prices.sessions()
    session = marketclock.last_session(sessions, now_utc)
    if session is None:
        return 1, "FAIL: no market calendar; run prices.py --refresh"
    problem = _benchmark_problem(sessions, now_utc, session)
    if problem:
        return 1, problem
    tickers = edgar.read_json(sec / "tickers.json", {}).get("rows", [])
    frames = xbrl.load_frames(sec)
    if not tickers or not frames:
        return 1, "FAIL: no SEC tickers or frames; run edgar.py --sync"
    store = insider.load_store(sec)
    today = dt.date.fromisoformat(session)
    pool = candidates(tickers)
    raw, info, unpriced, stale = {}, {}, 0, 0
    for c in pool:
        stored = prices.load(c["symbol"])
        view = price_view(stored["bars"])
        if view is None:
            unpriced += 1
            continue
        if view["last"] < session:
            stale += 1
            continue
        shares, shares_ev = xbrl.latest_shares(frames, c["cik"])
        cap = shares * view["close"] if shares else None
        if not passes_floors(view, cap, cfg):
            continue
        current, previous, annual_ev = xbrl.annual_facts(frames, c["cik"])
        altman = quality.altman_z_score(dataclasses.replace(current, market_cap=cap)) if current else None
        insider_value, insider_ev = insider.signal_for(c["cik"], cap, store, today)
        raw[c["ticker"]] = raw_signals(xbrl.company_series(frames, c["cik"]), current, previous,
                                       view["closes"], insider_value,
                                       {"prices": stored["evidence"], "annual": annual_ev, "insider": insider_ev})
        info[c["ticker"]] = {"name": c["name"], "cik": c["cik"], "symbol": c["symbol"], "close": view["close"],
                             "market_cap": cap, "altman": altman, "shares_evidence": shares_ev}
    priced = len(pool) - unpriced
    if priced == 0 or stale / priced > cfg["max_missing_price_share"]:
        return 1, (f"FAIL: {stale} of {priced} priced companies have no current price "
                   f"(limit {cfg['max_missing_price_share']:.0%}); no screen today")
    cards = scorecard.build(raw, cfg["weights"], cfg["min_coverage"])
    for ticker, card in cards.items():
        card.update(info[ticker])
        card["gate"], card["reason"] = scorecard.gate(card, info[ticker]["altman"], cfg)
    ranked = sorted((t for t, c in cards.items() if c["rank_pct"] is not None), key=lambda t: -cards[t]["score"])
    shortlist = ranked[:cfg["top_n"]]
    before = latest(out)
    base = before.get("previous_shortlist", []) if before.get("session") == session else before.get("shortlist", [])
    doc = {"session": session, "generated_at": now_utc.isoformat(timespec="seconds"),
           "scorecard_version": cfg["scorecard_version"], "candidates": len(pool), "unpriced": unpriced,
           "stale_prices": stale, "scored": len(cards), "shortlist": shortlist, "previous_shortlist": base,
           "new_entrants": [t for t in shortlist if t not in base], "cards": cards}
    edgar.write_json(out / f"{session}.json", doc)
    edgar.write_json(out / "latest.json", doc)
    eligible = sum(c["gate"] == "eligible" for c in cards.values())
    return 0, (f"OK session={session} scored={len(cards)} eligible={eligible} shortlist={len(shortlist)} "
               f"new={doc['new_entrants']} stale={stale} unpriced={unpriced}")


def card_text(ticker: str, doc: dict) -> str:
    card = doc.get("cards", {}).get(ticker)
    if card is None:
        return f"{ticker}: not scored in session {doc.get('session')}"
    fmt = lambda v: "could not tell" if v is None else f"{v:.4g}"  # noqa: E731
    lines = [f"{ticker} ({card['name']}) session {doc['session']}, scorecard v{doc['scorecard_version']}",
             f"gate: {card['gate']} ({card['reason']})",
             f"score {fmt(card['score'])}, rank {fmt(card['rank_pct'])}, coverage {card['coverage']:.0%}, "
             f"Altman Z {fmt(card['altman'])}, market cap {fmt(card['market_cap'])} [ev:{card['shares_evidence']}]"]
    for group, names in scorecard.GROUP_SIGNALS.items():
        lines.append(f"{group}: {fmt(card['groups'][group])}")
        for name in names:
            s = card["signals"][name]
            lines.append(f"  {name}: value {fmt(s['value'])}, pct {fmt(s['pct'])} "
                         + " ".join(f"[ev:{e}]" for e in s["evidence"]))
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--run", action="store_true")
    ap.add_argument("--card")
    a = ap.parse_args(argv)
    if a.run:
        code, message = run(fetch.utcnow())
        print(message)
        return code
    if a.card:
        print(card_text(a.card.upper(), latest()))
        return 0
    ap.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
```

Evidence paths in cards are as stored by `fetch.save_read` (absolute). `thesis_lint` (Task 11) accepts a citation either relative to the evidence dir or absolute under it.

- [ ] **Step 4: Run to verify it passes**

Run: `cd bobs/investor && python3 harness/test_screen.py`
Expected: `7 of 7 passed`.

- [ ] **Step 5: Break-proof the stale-benchmark refusal**

With the editor, change `if problem:` to `if False:`. Run: `test_stale_benchmark_fails_and_writes_nothing` must FAIL. Revert; `7 of 7 passed`.

- [ ] **Step 6: Commit**

```bash
git add bobs/investor/harness/screen.py bobs/investor/harness/test_screen.py
git commit -F <msg file: "Add screen: universe floors, nightly scorecard run, shortlist and entrants" + Co-Authored-By line>
```

---

## Milestone C: Ledger and guards

### Task 11: `thesis_lint.py` and the thesis template

**Files:**
- Create: `bobs/investor/templates/thesis.md`, `bobs/investor/harness/thesis_lint.py`, `bobs/investor/harness/test_thesis_lint.py`

**Interfaces:**
- Consumes: `scoutcfg.evidence_dir()`, `scoutcfg.bob_dir()`.
- Produces: `VERDICTS = ("buy-worthy", "watch", "pass", "could not tell")`, `LANES = ("numbers", "story")`, `CITE` (regex `\[ev:([^\]]+)\]`), `header(text) -> dict` (keys `verdict`, `lane`, `date`), `sections(text) -> dict[str, list[str]]`, `resolve(cite: str, evidence_dir: Path) -> Path | None`, `lint(text, evidence_dir) -> list[str]`. CLI: `python3 harness/thesis_lint.py theses/NVDA.md` prints `OK` or `FAIL: ...` lines.

- [ ] **Step 1: Write the template**

Create `bobs/investor/templates/thesis.md`:

```markdown
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
```

- [ ] **Step 2: Write the failing tests**

Create `bobs/investor/harness/test_thesis_lint.py`:

```python
#!/usr/bin/env python3
"""thesis_lint: a thesis ships only with both cases, cited numbers and citations that resolve.

Run:  python3 harness/test_thesis_lint.py
"""
from __future__ import annotations

import pathlib
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import scoutpath  # noqa: E402,F401
import testkit  # noqa: E402
import thesis_lint  # noqa: E402

CITE = "[ev:2026-10-13/sec-frame-x]"


def _evidence() -> pathlib.Path:
    ev = pathlib.Path(tempfile.mkdtemp())
    (ev / "2026-10-13" / "sec-frame-x").mkdir(parents=True)
    (ev / "2026-10-13" / "sec-frame-x" / "meta.json").write_text("{}")
    return ev


def thesis(verdict: str = "buy-worthy", lane: str = "numbers", bull: str = f"- Revenue grew 94% {CITE}",
           bear: str = f"- Gross margin fell 2 points {CITE}", drop: str = "") -> str:
    parts = {"Bull case": bull, "Bear case": bear,
             "Reverse DCF": f"- Price implies 31% a year for 10 years {CITE}",
             "What would change my mind": f"- Two quarters under 20% growth {CITE}"}
    body = "".join(f"## {name}\n\n{text}\n\n" for name, text in parts.items() if name != drop)
    return f"# NVDA: NVIDIA\n\nVerdict: {verdict}\nLane: {lane}\nDate: 2026-10-13\n\n## Scorecard\n\nscore 91\n\n{body}"


def test_valid_thesis_passes():
    assert thesis_lint.lint(thesis(), _evidence()) == []


def test_uncited_number_refused():
    errors = thesis_lint.lint(thesis(bull=f"- Revenue grew 94%\n- Margins up {CITE}"), _evidence())
    assert errors == ["Bull case: uncited number: - Revenue grew 94%"], errors


def test_a_year_counts_as_a_number():
    assert thesis_lint.lint(thesis(bear="- In 2025 the CEO left"), _evidence())


def test_unresolved_citation_refused():
    errors = thesis_lint.lint(thesis(bear="- Debt rose 3x [ev:2026-10-13/made-up]"), _evidence())
    assert errors == ["evidence not found: 2026-10-13/made-up"], errors


def test_missing_bear_case_refused():
    assert "missing section: ## Bear case" in thesis_lint.lint(thesis(drop="Bear case"), _evidence())


def test_bull_without_a_cited_claim_refused():
    assert "Bull case: no cited claim" in thesis_lint.lint(thesis(bull="- Great company"), _evidence())


def test_placeholder_verdict_and_lane_refused():
    errors = thesis_lint.lint(thesis(verdict="buy-worthy | watch", lane="numbers | story"), _evidence())
    assert any(e.startswith("Verdict:") for e in errors) and any(e.startswith("Lane:") for e in errors)


def test_absolute_citation_inside_evidence_dir_resolves():
    ev = _evidence()
    absolute = (ev / "2026-10-13" / "sec-frame-x").resolve()
    assert thesis_lint.resolve(str(absolute), ev) == absolute
    assert thesis_lint.resolve(str(pathlib.Path(tempfile.mkdtemp())), ev) is None


def test_header_reads_verdict_lane_date():
    assert thesis_lint.header(thesis("watch", "story")) == {"verdict": "watch", "lane": "story", "date": "2026-10-13"}


if __name__ == "__main__":
    raise SystemExit(testkit.run(globals()))
```

- [ ] **Step 3: Run to verify it fails**

Run: `cd bobs/investor && python3 harness/test_thesis_lint.py`
Expected: `ModuleNotFoundError: No module named 'thesis_lint'`.

- [ ] **Step 4: Write `thesis_lint.py`**

```python
#!/usr/bin/env python3
"""Refuse a thesis that is not finished: missing a case, an uncited number, a citation to nothing.

What it checks: the Verdict, Lane and Date lines; the four claim sections exist; Bull and Bear each
carry at least one cited claim; every line in a claim section that contains a digit carries a
[ev:...] citation; every citation names a saved evidence folder (one with meta.json) inside this
Bob's evidence dir.

What it cannot check: that the cited evidence says what the line claims. That is core's known
limit ("no machine claim verification"); the citation makes it checkable by a person.

Usage:
    python3 harness/thesis_lint.py theses/NVDA.md
"""
from __future__ import annotations

import datetime as dt
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import scoutpath  # noqa: E402,F401
import scoutcfg  # noqa: E402

VERDICTS = ("buy-worthy", "watch", "pass", "could not tell")
LANES = ("numbers", "story")
CITE = re.compile(r"\[ev:([^\]]+)\]")
HEADER = re.compile(r"^(Verdict|Lane|Date):[ \t]*(.*?)[ \t]*$", re.M)
CLAIM_SECTIONS = ("Bull case", "Bear case", "Reverse DCF", "What would change my mind")
MUST_CITE = ("Bull case", "Bear case")


def header(text: str) -> dict:
    return {key.lower(): value for key, value in HEADER.findall(text)}


def sections(text: str) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    current = None
    for line in text.splitlines():
        if line.startswith("## "):
            current = line[3:].strip()
            out[current] = []
        elif current is not None:
            out[current].append(line)
    return out


def resolve(cite: str, evidence_dir: pathlib.Path) -> pathlib.Path | None:
    path = pathlib.Path(cite.strip())
    target = path if path.is_absolute() else evidence_dir / path
    try:
        target.resolve().relative_to(evidence_dir.resolve())
    except ValueError:
        return None
    return target if (target / "meta.json").is_file() else None


def lint(text: str, evidence_dir: pathlib.Path) -> list[str]:
    errors = []
    head = header(text)
    if head.get("verdict") not in VERDICTS:
        errors.append(f"Verdict: one of {', '.join(VERDICTS)}")
    if head.get("lane") not in LANES:
        errors.append(f"Lane: one of {', '.join(LANES)}")
    try:
        dt.date.fromisoformat(head.get("date", ""))
    except ValueError:
        errors.append("Date: YYYY-MM-DD")
    found = sections(text)
    for name in CLAIM_SECTIONS:
        if name not in found:
            errors.append(f"missing section: ## {name}")
            continue
        for line in (raw.strip() for raw in found[name]):
            if line and re.search(r"\d", CITE.sub("", line)) and not CITE.search(line):
                errors.append(f"{name}: uncited number: {line[:80]}")
    errors += [f"{name}: no cited claim" for name in MUST_CITE
               if name in found and not any(CITE.search(line) for line in found[name])]
    errors += [f"evidence not found: {cite}" for cite in CITE.findall(text) if resolve(cite, evidence_dir) is None]
    return errors


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print(__doc__)
        return 2
    path = pathlib.Path(argv[0])
    if not path.is_absolute():
        path = scoutcfg.bob_dir() / path
    errors = lint(path.read_text(), scoutcfg.evidence_dir())
    for err in errors:
        print(f"FAIL: {err}")
    if not errors:
        print("OK")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
```

- [ ] **Step 5: Run to verify it passes**

Run: `cd bobs/investor && python3 harness/test_thesis_lint.py`
Expected: `9 of 9 passed`.

- [ ] **Step 6: Break-proof the citation rule**

With the editor, change `and not CITE.search(line)` to `and False`. Run: `test_uncited_number_refused` must FAIL. Revert; `9 of 9 passed`.

- [ ] **Step 7: Commit**

```bash
git add bobs/investor/templates/thesis.md bobs/investor/harness/thesis_lint.py bobs/investor/harness/test_thesis_lint.py
git commit -F <msg file: "Add thesis template and thesis_lint: sections, cited numbers, resolving evidence" + Co-Authored-By line>
```

---

### Task 12: `calls.py`: the verdict and call ledger

**Files:**
- Create: `bobs/investor/harness/calls.py`, `bobs/investor/harness/test_calls.py`

**Interfaces:**
- Consumes: `screen.latest()`, `prices.sessions()`, `marketclock.last_session`, `thesis_lint.lint/header/resolve/VERDICTS/LANES`, `scoutcfg.state_dir/evidence_dir/bob_dir`, core `oscompat.lock/unlock`.
- Produces:
  - `class Refused(Exception)`; `HOLD_DAYS = 365`.
  - `ledger_path() -> Path` (`<state>/calls.jsonl`), `read(path=None) -> tuple[list[dict], int]` (rows, torn line count), `append(rows: list[dict], path=None) -> None`.
  - `open_call(rows, ticker, today: dt.date) -> dict | None`.
  - `record_verdict(ticker, verdict, lane, source: str | None, now: dt.datetime) -> list[dict]` (rows appended; raises `Refused`).
  - `correct(call_id, note, now) -> dict`.
  - Row kinds: `verdict` `{kind, ticker, verdict, lane, session, at, source}`; `call` `{kind, id, ticker, symbol, lane, session, at, scorecard_version, score, coverage, rank_pct, thesis, thesis_sha256, source}`; `withdrawn` `{kind, call_id, ticker, session, at, reason}`; `correction` `{kind, call_id, at, note}`.
  - CLI: `calls.py verdict TICKER --verdict V --lane L [--source EV]`, `calls.py correct CALL_ID --note TEXT`, `calls.py list`.

- [ ] **Step 1: Write the failing tests**

Create `bobs/investor/harness/test_calls.py`:

```python
#!/usr/bin/env python3
"""calls: every refusal the spec names, each next to the case it must allow.

Run:  python3 harness/test_calls.py
"""
from __future__ import annotations

import datetime as dt
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import scoutpath  # noqa: E402,F401
import calls  # noqa: E402
import testkit  # noqa: E402
from test_thesis_lint import thesis  # noqa: E402

NOW = dt.datetime(2026, 10, 13, 5, 30, tzinfo=dt.timezone.utc)
LATER = dt.datetime(2026, 10, 14, 5, 30, tzinfo=dt.timezone.utc)


def _put(path: pathlib.Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(obj if isinstance(obj, str) else json.dumps(obj))


def _screen(bob: pathlib.Path, session: str, gate: str = "eligible") -> None:
    state = bob / "data" / "state"
    _put(state / "prices" / "_calendar.json", {"sessions": [
        {"date": "2026-10-12", "close": "16:00"}, {"date": "2026-10-13", "close": "16:00"}]})
    _put(state / "screen" / "latest.json", {"session": session, "scorecard_version": 1, "cards": {
        "NVDA": {"symbol": "NVDA", "gate": gate, "reason": "test", "score": 91.0, "coverage": 1.0, "rank_pct": 95.0}}})


def _bob(gate: str = "eligible", verdict: str = "buy-worthy", lane: str = "numbers") -> pathlib.Path:
    bob = testkit.temp_bob()
    _put(bob / "data" / "evidence" / "2026-10-13" / "sec-frame-x" / "meta.json", "{}")
    _screen(bob, "2026-10-12", gate)
    _put(bob / "theses" / "NVDA.md", thesis(verdict, lane))
    return bob


def _refused(fn) -> str:
    try:
        fn()
    except calls.Refused as e:
        return str(e)
    raise AssertionError("not refused")


def test_valid_buy_worthy_records_verdict_and_call():
    _bob()
    rows = calls.record_verdict("NVDA", "buy-worthy", "numbers", None, NOW)
    assert [r["kind"] for r in rows] == ["verdict", "call"]
    assert rows[1]["id"] == "NVDA-2026-10-12-1" and rows[1]["session"] == "2026-10-12" and rows[1]["thesis_sha256"]
    assert calls.read()[0] == rows


def test_refused_when_gate_is_not_eligible():
    _bob(gate="pass")
    assert "gate is pass" in _refused(lambda: calls.record_verdict("NVDA", "buy-worthy", "numbers", None, NOW))
    assert calls.read() == ([], 0)


def test_watch_is_allowed_whatever_the_gate():
    _bob(gate="pass", verdict="watch")
    assert [r["kind"] for r in calls.record_verdict("NVDA", "watch", "numbers", None, NOW)] == ["verdict"]


def test_refused_without_thesis():
    bob = _bob()
    (bob / "theses" / "NVDA.md").unlink()
    assert "no thesis" in _refused(lambda: calls.record_verdict("NVDA", "buy-worthy", "numbers", None, NOW))


def test_refused_when_thesis_fails_lint():
    bob = _bob()
    _put(bob / "theses" / "NVDA.md", thesis(bull="- Revenue grew 94%"))
    assert "uncited number" in _refused(lambda: calls.record_verdict("NVDA", "buy-worthy", "numbers", None, NOW))


def test_refused_when_thesis_verdict_differs():
    _bob(verdict="watch")
    assert "thesis says watch" in _refused(lambda: calls.record_verdict("NVDA", "buy-worthy", "numbers", None, NOW))


def test_refused_when_screen_is_stale():
    _bob()
    message = _refused(lambda: calls.record_verdict("NVDA", "buy-worthy", "numbers", None, LATER))
    assert "screen is for 2026-10-12" in message and "2026-10-13" in message


def test_same_verdict_same_day_refused():
    _bob()
    calls.record_verdict("NVDA", "buy-worthy", "numbers", None, NOW)
    assert "already recorded" in _refused(lambda: calls.record_verdict("NVDA", "buy-worthy", "numbers", None, NOW))
    assert len(calls.read()[0]) == 2


def test_open_call_blocks_a_second_call():
    bob = _bob()
    calls.record_verdict("NVDA", "buy-worthy", "numbers", None, NOW)
    _screen(bob, "2026-10-13")
    assert "already called" in _refused(lambda: calls.record_verdict("NVDA", "buy-worthy", "numbers", None, LATER))


def test_lower_verdict_withdraws_the_open_call():
    bob = _bob()
    calls.record_verdict("NVDA", "buy-worthy", "numbers", None, NOW)
    _screen(bob, "2026-10-13")
    _put(bob / "theses" / "NVDA.md", thesis("watch"))
    rows = calls.record_verdict("NVDA", "watch", "numbers", None, LATER)
    assert [r["kind"] for r in rows] == ["verdict", "withdrawn"] and rows[1]["call_id"] == "NVDA-2026-10-12-1"
    assert calls.open_call(calls.read()[0], "NVDA", dt.date(2026, 10, 14)) is None


def test_story_lane_needs_a_resolving_source():
    _bob(lane="story")
    assert "source" in _refused(lambda: calls.record_verdict("NVDA", "buy-worthy", "story", None, NOW))
    assert "source" in _refused(lambda: calls.record_verdict("NVDA", "buy-worthy", "story", "2026-10-13/nope", NOW))
    rows = calls.record_verdict("NVDA", "buy-worthy", "story", "2026-10-13/sec-frame-x", NOW)
    assert rows[1]["lane"] == "story" and rows[1]["source"] == "2026-10-13/sec-frame-x"


def test_torn_line_is_skipped_and_counted():
    _bob()
    calls.record_verdict("NVDA", "buy-worthy", "numbers", None, NOW)
    with calls.ledger_path().open("a") as fh:
        fh.write('{"kind": "verdict", "tick\n')
    rows, torn = calls.read()
    assert len(rows) == 2 and torn == 1


def test_correction_needs_an_existing_call():
    _bob()
    assert "no call" in _refused(lambda: calls.correct("NVDA-2026-10-12-1", "typo", NOW))
    calls.record_verdict("NVDA", "buy-worthy", "numbers", None, NOW)
    assert calls.correct("NVDA-2026-10-12-1", "thesis link moved", NOW)["kind"] == "correction"


if __name__ == "__main__":
    raise SystemExit(testkit.run(globals()))
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd bobs/investor && python3 harness/test_calls.py`
Expected: `ModuleNotFoundError: No module named 'calls'`.

- [ ] **Step 3: Write `calls.py`**

```python
#!/usr/bin/env python3
"""The prediction ledger: every thesis verdict, every call, every withdrawal, append-only.

A call is the Scout's claim that a stock will beat SPY and QQQ over 12 months. It is recorded only
when everything that makes it checkable is in place, and refused otherwise:
- the screen is from the last completed session (no calls on a stale picture);
- the ticker was scored that session and passed the gate (top 20%, coverage, no distress veto);
- theses/<TICKER>.md exists, passes thesis_lint, and states the same verdict and lane;
- a story-lane call names the saved evidence that started it;
- the ticker has no open call (one call per name per 12 months: no double counting);
- the same verdict was not already recorded this session (a resumed loop must not repeat itself).

A verdict that drops below buy-worthy withdraws the open call, and the call is still scored:
otherwise the Scout could quietly drop its losers. Nothing is ever rewritten; a correction is a
new line pointing at the old one. The entry price is not stored here: ledger_score.py takes the
next session's close from the adjusted series, so dividends and splits never skew a result.

Usage:
    python3 harness/calls.py verdict NVDA --verdict buy-worthy --lane numbers
    python3 harness/calls.py verdict ACME --verdict watch --lane story --source 2026-10-13/sec-index-...
    python3 harness/calls.py correct NVDA-2026-10-12-1 --note "thesis moved to theses/NVDA.md"
    python3 harness/calls.py list
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import scoutpath  # noqa: E402,F401
import fetch  # noqa: E402
import marketclock  # noqa: E402
import oscompat  # noqa: E402
import prices  # noqa: E402
import scoutcfg  # noqa: E402
import screen  # noqa: E402
import thesis_lint  # noqa: E402

HOLD_DAYS = 365


class Refused(Exception):
    pass


def ledger_path() -> pathlib.Path:
    return scoutcfg.state_dir() / "calls.jsonl"


def read(path: pathlib.Path | None = None) -> tuple[list[dict], int]:
    path = path or ledger_path()
    try:
        lines = path.read_text().splitlines()
    except FileNotFoundError:
        return [], 0
    rows, torn = [], 0
    for line in lines:
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except ValueError:
            torn += 1
    return rows, torn


def append(rows: list[dict], path: pathlib.Path | None = None) -> None:
    path = path or ledger_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as fh:
        oscompat.lock(fh)
        try:
            fh.write("".join(json.dumps(r, sort_keys=True) + "\n" for r in rows))
            fh.flush()
        finally:
            oscompat.unlock(fh)


def open_call(rows: list[dict], ticker: str, today: dt.date) -> dict | None:
    withdrawn = {r["call_id"] for r in rows if r.get("kind") == "withdrawn"}
    for row in reversed(rows):
        if (row.get("kind") == "call" and row["ticker"] == ticker and row["id"] not in withdrawn
                and (today - dt.date.fromisoformat(row["session"])).days < HOLD_DAYS):
            return row
    return None


def _check_thesis(ticker: str, verdict: str, lane: str) -> pathlib.Path:
    path = scoutcfg.bob_dir() / "theses" / f"{ticker}.md"
    if not path.is_file():
        raise Refused(f"no thesis at theses/{ticker}.md")
    text = path.read_text()
    errors = thesis_lint.lint(text, scoutcfg.evidence_dir())
    if errors:
        raise Refused("thesis_lint: " + "; ".join(errors))
    head = thesis_lint.header(text)
    if head.get("verdict") != verdict or head.get("lane") != lane:
        raise Refused(f"thesis says {head.get('verdict')} / {head.get('lane')}, not {verdict} / {lane}")
    return path


def record_verdict(ticker: str, verdict: str, lane: str, source: str | None, now: dt.datetime) -> list[dict]:
    ticker = ticker.upper()
    if verdict not in thesis_lint.VERDICTS or lane not in thesis_lint.LANES:
        raise Refused(f"verdict one of {thesis_lint.VERDICTS}, lane one of {thesis_lint.LANES}")
    doc = screen.latest()
    session = doc.get("session")
    expected = marketclock.last_session(prices.sessions(), now)
    if session is None or session != expected:
        raise Refused(f"screen is for {session}, last completed session is {expected}; run the nightly screen first")
    card = doc.get("cards", {}).get(ticker)
    if card is None:
        raise Refused(f"{ticker} was not scored in session {session} (outside the universe floors?)")
    thesis_path = _check_thesis(ticker, verdict, lane)
    if lane == "story" and (not source or thesis_lint.resolve(source, scoutcfg.evidence_dir()) is None):
        raise Refused("story lane needs --source naming saved evidence that exists")
    rows, _ = read()
    if any(r.get("kind") == "verdict" and r["ticker"] == ticker and r["verdict"] == verdict
           and r["session"] == session for r in rows):
        raise Refused(f"{ticker} {verdict} already recorded for session {session}")
    at = now.isoformat(timespec="seconds")
    new = [{"kind": "verdict", "ticker": ticker, "verdict": verdict, "lane": lane, "session": session,
            "at": at, "source": source}]
    current = open_call(rows, ticker, dt.date.fromisoformat(session))
    if verdict == "buy-worthy":
        if card["gate"] != "eligible":
            raise Refused(f"gate is {card['gate']}: {card['reason']}")
        if current is not None:
            raise Refused(f"already called on {current['session']} ({current['id']}); one open call per name")
        number = sum(r.get("kind") == "call" and r["ticker"] == ticker for r in rows) + 1
        new.append({"kind": "call", "id": f"{ticker}-{session}-{number}", "ticker": ticker,
                    "symbol": card["symbol"], "lane": lane, "session": session, "at": at,
                    "scorecard_version": doc["scorecard_version"], "score": card["score"],
                    "coverage": card["coverage"], "rank_pct": card["rank_pct"],
                    "thesis": f"theses/{ticker}.md",
                    "thesis_sha256": hashlib.sha256(thesis_path.read_bytes()).hexdigest(), "source": source})
    elif current is not None:
        new.append({"kind": "withdrawn", "call_id": current["id"], "ticker": ticker, "session": session,
                    "at": at, "reason": f"verdict now {verdict}"})
    append(new)
    return new


def correct(call_id: str, note: str, now: dt.datetime) -> dict:
    rows, _ = read()
    if not any(r.get("kind") == "call" and r["id"] == call_id for r in rows):
        raise Refused(f"no call with id {call_id}")
    row = {"kind": "correction", "call_id": call_id, "at": now.isoformat(timespec="seconds"), "note": note}
    append([row])
    return row


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="command", required=True)
    v = sub.add_parser("verdict")
    v.add_argument("ticker")
    v.add_argument("--verdict", required=True)
    v.add_argument("--lane", required=True)
    v.add_argument("--source")
    c = sub.add_parser("correct")
    c.add_argument("call_id")
    c.add_argument("--note", required=True)
    sub.add_parser("list")
    a = ap.parse_args(argv)
    try:
        if a.command == "verdict":
            rows = record_verdict(a.ticker, a.verdict, a.lane, a.source, fetch.utcnow())
            print("OK recorded " + ", ".join(r.get("id") or r["kind"] for r in rows))
            return 0
        if a.command == "correct":
            print(f"OK {correct(a.call_id, a.note, fetch.utcnow())}")
            return 0
    except Refused as e:
        print(f"REFUSED: {e}")
        return 1
    rows, torn = read()
    withdrawn = {r["call_id"] for r in rows if r.get("kind") == "withdrawn"}
    for r in (r for r in rows if r.get("kind") == "call"):
        print(f"{r['id']:28} {r['lane']:8} score {r['score']:.0f} {'withdrawn' if r['id'] in withdrawn else 'open'}")
    if torn:
        print(f"WARN: {torn} torn line(s) skipped in {ledger_path()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd bobs/investor && python3 harness/test_calls.py`
Expected: `13 of 13 passed`.

- [ ] **Step 5: Break-proof the gate and the open-call rule**

With the editor, delete the two lines `if card["gate"] != "eligible":` / `raise Refused(...)`. Run: `test_refused_when_gate_is_not_eligible` must FAIL. Revert. Then change `if current is not None:` (inside the buy-worthy branch) to `if False:`. Run: `test_open_call_blocks_a_second_call` must FAIL. Revert; `13 of 13 passed`.

- [ ] **Step 6: Commit**

```bash
git add bobs/investor/harness/calls.py bobs/investor/harness/test_calls.py
git commit -F <msg file: "Add calls: append-only verdict and call ledger with refusals" + Co-Authored-By line>
```

---

### Task 13: `ledger_score.py`: excess returns and the basket curve

**Files:**
- Create: `bobs/investor/harness/ledger_score.py`, `bobs/investor/harness/test_ledger_score.py`

**Interfaces:**
- Consumes: `calls.read()`, `prices.load/sessions/BENCHMARKS`, `marketclock.last_session`, `edgar.read_json/write_json/sec_dir/sec_to_alpaca`, `scoutcfg.state_dir()`.
- Produces:
  - `HORIZONS = (3, 6, 12)`, `MIN_SCORED = 20`.
  - `add_months(day, n) -> dt.date`, `entry(bars, session) -> tuple[str, float] | None` (first close after the call's session), `close_on_or_before(bars, day: str) -> tuple[str, float] | None`.
  - `score_call(call, series, listed: set[str], last_session: str, withdrawn: set[str]) -> dict` with `{"id", "ticker", "symbol", "lane", "session", "score", "withdrawn", "entry_date", "entry_close", "delisted", "horizons": {"3"|"6"|"12": {"status": "pending" | "no price" | "scored", "due", "price_date"?, "return"?, "excess_spy"?, "excess_qqq"?}}}`.
  - `summarize(rows) -> {"3"|"6"|"12": {"n", "avg_excess_spy", "avg_excess_qqq", "beat_spy", "interim": bool}}`.
  - `basket(rows, series) -> {"dates": [...], "scout": [...], "spy": [...], "qqq": [...]}` (all start at 100 on the first entry date).
  - `results_path() -> Path` (`<state>/ledger_results.json`), `run(now_utc) -> dict` writes `{"generated_at", "session", "n_calls", "torn_lines", "min_scored": 20, "calls": [...], "summary": {...}, "by_lane": {"numbers": {...}, "story": {...}}, "basket": {...}}`. CLI `--run`.

- [ ] **Step 1: Write the failing tests**

Create `bobs/investor/harness/test_ledger_score.py`:

```python
#!/usr/bin/env python3
"""ledger_score: next-session entry, fixed horizons, delistings scored, gaps never back-filled.

Run:  python3 harness/test_ledger_score.py
"""
from __future__ import annotations

import datetime as dt
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import scoutpath  # noqa: E402,F401
import ledger_score as ls  # noqa: E402
import testkit  # noqa: E402

CALL = {"kind": "call", "id": "PICK-2026-01-01-1", "ticker": "PICK", "symbol": "PICK", "lane": "numbers",
        "session": "2026-01-01", "score": 90.0}


def _weekdays(first: str, last: str) -> list[str]:
    day, end, out = dt.date.fromisoformat(first), dt.date.fromisoformat(last), []
    while day <= end:
        if day.weekday() < 5:
            out.append(day.isoformat())
        day += dt.timedelta(days=1)
    return out


def _series(pick_until: str = "2026-07-31", pick_after_april: float = 110.0) -> dict:
    days = _weekdays("2026-01-01", "2026-07-31")
    step = lambda before, after: [[d, before if d < "2026-04-01" else after, 1] for d in days]  # noqa: E731
    pick = [b for b in step(100.0, pick_after_april) if b[0] <= pick_until]
    return {"PICK": pick, "SPY": step(100.0, 104.0), "QQQ": step(100.0, 105.0)}


def test_add_months_clamps_month_end():
    assert ls.add_months(dt.date(2026, 1, 31), 1) == dt.date(2026, 2, 28)
    assert ls.add_months(dt.date(2026, 1, 31), 12) == dt.date(2027, 1, 31)


def test_entry_is_the_next_session_close():
    assert ls.entry([["2026-01-01", 99.0, 1], ["2026-01-02", 100.0, 1]], "2026-01-01") == ("2026-01-02", 100.0)


def test_excess_returns_hand_worked():
    row = ls.score_call(CALL, _series(), {"PICK"}, "2026-07-31", set())
    three = row["horizons"]["3"]
    # pick +10%, SPY +4%, QQQ +5%
    assert three["status"] == "scored" and abs(three["excess_spy"] - 0.06) < 1e-12 and abs(three["excess_qqq"] - 0.05) < 1e-12
    assert row["horizons"]["6"]["status"] == "scored" and row["horizons"]["12"]["status"] == "pending"
    assert row["entry_date"] == "2026-01-02" and row["entry_close"] == 100.0


def test_withdrawn_calls_are_still_scored():
    row = ls.score_call(CALL, _series(), {"PICK"}, "2026-07-31", {CALL["id"]})
    assert row["withdrawn"] and row["horizons"]["3"]["status"] == "scored"


def test_delisted_is_scored_at_last_price():
    series = _series(pick_until="2026-03-02")
    for b in series["PICK"]:
        b[1] = 50.0 if b[0] >= "2026-02-02" else 100.0
    row = ls.score_call(CALL, series, set(), "2026-07-31", set())
    three = row["horizons"]["3"]
    assert row["delisted"] and three["status"] == "scored" and abs(three["excess_spy"] - (-0.54)) < 1e-12


def test_listed_but_missing_price_is_never_back_filled():
    row = ls.score_call(CALL, _series(pick_until="2026-03-02"), {"PICK"}, "2026-07-31", set())
    assert not row["delisted"] and row["horizons"]["3"]["status"] == "no price"


def test_summary_and_interim_labels():
    rows = [{"horizons": {"3": {"status": "scored", "excess_spy": 0.10, "excess_qqq": 0.0}}},
            {"horizons": {"3": {"status": "scored", "excess_spy": -0.02, "excess_qqq": -0.04}}},
            {"horizons": {"3": {"status": "pending"}}}]
    s = ls.summarize(rows)
    assert s["3"]["n"] == 2 and abs(s["3"]["avg_excess_spy"] - 0.04) < 1e-12 and s["3"]["beat_spy"] == 0.5
    assert s["3"]["interim"] and not s["12"]["interim"] and s["12"]["n"] == 0 and s["12"]["avg_excess_spy"] is None


def test_basket_follows_a_single_pick():
    series = _series()
    row = ls.score_call(CALL, series, {"PICK"}, "2026-07-31", set())
    curve = ls.basket([row], series)
    assert curve["dates"][0] == "2026-01-02" and curve["scout"][0] == 100.0
    assert abs(curve["scout"][-1] - 110.0) < 1e-9 and abs(curve["spy"][-1] - 104.0) < 1e-9


def test_run_writes_results_from_the_ledger():
    bob = testkit.temp_bob()
    state = bob / "data" / "state"
    for symbol, bars in _series().items():
        (state / "prices").mkdir(parents=True, exist_ok=True)
        (state / "prices" / f"{symbol}.json").write_text(json.dumps({"bars": bars, "evidence": []}))
    (state / "prices" / "_calendar.json").write_text(json.dumps({"sessions": [{"date": "2026-07-31", "close": "16:00"}]}))
    (state / "calls.jsonl").write_text(json.dumps(CALL) + "\n")
    doc = ls.run(dt.datetime(2026, 8, 1, 12, tzinfo=dt.timezone.utc))
    assert doc["n_calls"] == 1 and doc["summary"]["3"]["n"] == 1 and doc["session"] == "2026-07-31"
    assert json.loads(ls.results_path().read_text())["by_lane"]["numbers"]["3"]["n"] == 1


if __name__ == "__main__":
    raise SystemExit(testkit.run(globals()))
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd bobs/investor && python3 harness/test_ledger_score.py`
Expected: `ModuleNotFoundError: No module named 'ledger_score'`.

- [ ] **Step 3: Write `ledger_score.py`**

```python
#!/usr/bin/env python3
"""Score every call against SPY and QQQ at 3, 6 and 12 months; build the Scout basket curve.

The rules that keep the record honest:
- Entry is the close of the session after the call, never a price the Scout had already seen.
- Returns come from the current adjusted series for both the pick and the benchmark, so dividends
  and splits never make a pick look better or worse than it was.
- Every call is scored at fixed horizons, withdrawn or not.
- A delisted name (gone from SEC's list and no bars for a week) is scored at its last price.
- A listed name with no price near a horizon stays "no price"; an old price is never used instead.
- 3 and 6 months are labelled interim; 12 months is the verdict. Below 20 scored calls the board
  and digest say the result is noise.

The basket is equal weight across calls held at the time (entry to 12 months), rebalanced daily,
starting at 100 on the first entry date; SPY and QQQ are rebased to the same date.

Usage:
    python3 harness/ledger_score.py --run
"""
from __future__ import annotations

import argparse
import calendar
import datetime as dt
import pathlib
import statistics
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import scoutpath  # noqa: E402,F401
import calls  # noqa: E402
import edgar  # noqa: E402
import fetch  # noqa: E402
import marketclock  # noqa: E402
import prices  # noqa: E402
import scoutcfg  # noqa: E402

HORIZONS = (3, 6, 12)
MIN_SCORED = 20
STALE_PRICE_DAYS = 7
DELISTED_SESSIONS = 5


def _d(text: str) -> dt.date:
    return dt.date.fromisoformat(text)


def add_months(day: dt.date, n: int) -> dt.date:
    month0 = day.month - 1 + n
    year, month = day.year + month0 // 12, month0 % 12 + 1
    return dt.date(year, month, min(day.day, calendar.monthrange(year, month)[1]))


def entry(bars: list, session: str) -> tuple[str, float] | None:
    return next(((b[0], b[1]) for b in bars if b[0] > session), None)


def close_on_or_before(bars: list, day: str) -> tuple[str, float] | None:
    best = None
    for b in bars:
        if b[0] > day:
            break
        best = (b[0], b[1])
    return best


def score_call(call: dict, series: dict, listed: set[str], last_session: str, withdrawn: set[str]) -> dict:
    bars, spy, qqq = series.get(call["symbol"], []), series["SPY"], series["QQQ"]
    row = {k: call[k] for k in ("id", "ticker", "symbol", "lane", "session", "score")}
    row.update({"withdrawn": call["id"] in withdrawn, "entry_date": None, "entry_close": None,
                "delisted": False, "horizons": {str(h): {"status": "pending"} for h in HORIZONS}})
    start = entry(bars, call["session"])
    if start is None:
        return row
    spy0, qqq0 = close_on_or_before(spy, start[0]), close_on_or_before(qqq, start[0])
    if spy0 is None or qqq0 is None:
        return row
    row["entry_date"], row["entry_close"] = start
    row["delisted"] = (call["symbol"] not in listed and len(spy) > DELISTED_SESSIONS
                       and bars[-1][0] < spy[-DELISTED_SESSIONS - 1][0])
    for h in HORIZONS:
        due = add_months(_d(start[0]), h).isoformat()
        if due > last_session:
            row["horizons"][str(h)] = {"status": "pending", "due": due}
            continue
        pick = close_on_or_before(bars, due)
        if pick is None or (not row["delisted"] and (_d(due) - _d(pick[0])).days > STALE_PRICE_DAYS):
            row["horizons"][str(h)] = {"status": "no price", "due": due}
            continue
        ret = pick[1] / start[1] - 1
        spy_ret = close_on_or_before(spy, due)[1] / spy0[1] - 1
        qqq_ret = close_on_or_before(qqq, due)[1] / qqq0[1] - 1
        row["horizons"][str(h)] = {"status": "scored", "due": due, "price_date": pick[0], "return": ret,
                                   "excess_spy": ret - spy_ret, "excess_qqq": ret - qqq_ret}
    return row


def summarize(rows: list[dict]) -> dict:
    out = {}
    for h in HORIZONS:
        scored = [r["horizons"][str(h)] for r in rows if r["horizons"].get(str(h), {}).get("status") == "scored"]
        n = len(scored)
        out[str(h)] = {"n": n, "interim": h != 12,
                       "avg_excess_spy": statistics.fmean(s["excess_spy"] for s in scored) if n else None,
                       "avg_excess_qqq": statistics.fmean(s["excess_qqq"] for s in scored) if n else None,
                       "beat_spy": sum(s["excess_spy"] > 0 for s in scored) / n if n else None}
    return out


def basket(rows: list[dict], series: dict) -> dict:
    live = [r for r in rows if r["entry_date"]]
    if not live:
        return {"dates": [], "scout": [], "spy": [], "qqq": []}
    first = min(r["entry_date"] for r in live)
    dates = [b[0] for b in series["SPY"] if b[0] >= first]
    closes = {r["id"]: {b[0]: b[1] for b in series.get(r["symbol"], [])} for r in live}
    ends = {r["id"]: add_months(_d(r["entry_date"]), 12).isoformat() for r in live}
    scout = [100.0]
    for prev, day in zip(dates, dates[1:]):
        moves = [closes[r["id"]][day] / closes[r["id"]][prev] - 1 for r in live
                 if r["entry_date"] <= prev and day <= ends[r["id"]]
                 and day in closes[r["id"]] and prev in closes[r["id"]]]
        scout.append(scout[-1] * (1 + (sum(moves) / len(moves) if moves else 0.0)))
    rebased = {}
    for symbol in prices.BENCHMARKS:
        by_date = {b[0]: b[1] for b in series[symbol]}
        rebased[symbol] = [by_date[d] / by_date[dates[0]] * 100 for d in dates]
    return {"dates": dates, "scout": scout, "spy": rebased["SPY"], "qqq": rebased["QQQ"]}


def results_path() -> pathlib.Path:
    return scoutcfg.state_dir() / "ledger_results.json"


def run(now_utc: dt.datetime) -> dict:
    rows, torn = calls.read()
    made = [r for r in rows if r.get("kind") == "call"]
    withdrawn = {r["call_id"] for r in rows if r.get("kind") == "withdrawn"}
    symbols = {c["symbol"] for c in made} | set(prices.BENCHMARKS)
    series = {s: prices.load(s)["bars"] for s in symbols}
    listed = {edgar.sec_to_alpaca(t["ticker"])
              for t in edgar.read_json(edgar.sec_dir() / "tickers.json", {}).get("rows", [])}
    session = marketclock.last_session(prices.sessions(), now_utc) or ""
    scored = [score_call(c, series, listed, session, withdrawn) for c in made]
    doc = {"generated_at": now_utc.isoformat(timespec="seconds"), "session": session, "n_calls": len(made),
           "torn_lines": torn, "min_scored": MIN_SCORED, "calls": scored, "summary": summarize(scored),
           "by_lane": {lane: summarize([r for r in scored if r["lane"] == lane]) for lane in ("numbers", "story")},
           "basket": basket(scored, series)}
    edgar.write_json(results_path(), doc)
    return doc


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--run", action="store_true", required=True)
    ap.parse_args(argv)
    doc = run(fetch.utcnow())
    twelve = doc["summary"]["12"]
    print(f"OK calls={doc['n_calls']} scored_12m={twelve['n']} torn={doc['torn_lines']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd bobs/investor && python3 harness/test_ledger_score.py`
Expected: `9 of 9 passed`.

- [ ] **Step 5: Break-proof the no-back-fill rule**

With the editor, change `(_d(due) - _d(pick[0])).days > STALE_PRICE_DAYS` to `(_d(due) - _d(pick[0])).days > 9999`. Run: `test_listed_but_missing_price_is_never_back_filled` must FAIL. Revert; `9 of 9 passed`.

- [ ] **Step 6: Commit**

```bash
git add bobs/investor/harness/ledger_score.py bobs/investor/harness/test_ledger_score.py
git commit -F <msg file: "Add ledger_score: 3/6/12-month excess vs SPY and QQQ, basket curve" + Co-Authored-By line>
```

---

### Task 14: Hooks: `protect_gate` and `order_gate`

**Files:**
- Create: `bobs/investor/harness/hooks/protect_gate.py`, `bobs/investor/harness/hooks/order_gate.py`, `bobs/investor/harness/hooks/test_hooks.py`
- Modify: `bobs/investor/.claude/settings.json` (written by `new_bob.py` in Task 1)

**Interfaces:**
- Consumes: `scoutcfg.state_dir/evidence_dir/bob_dir`, Claude Code's PreToolUse JSON on stdin (`tool_name`, `tool_input`).
- Produces: each gate prints a deny decision `{"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny", "permissionDecisionReason": ...}}` or nothing; exit code 0 always. `protect_gate.decide(payload, cwd) -> str | None`, `order_gate.decide(payload) -> str | None`. Both fail closed: unreadable input is denied.

- [ ] **Step 1: Write the failing tests**

Create `bobs/investor/harness/hooks/test_hooks.py`:

```python
#!/usr/bin/env python3
"""The Bob's own gates, driven the way Claude Code drives them; every refusal paired with an allow.

Run:  python3 harness/hooks/test_hooks.py
"""
from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import scoutpath  # noqa: E402,F401
import testkit  # noqa: E402


def run_gate(script: str, payload, bob: pathlib.Path | None = None) -> dict | None:
    bob = bob or testkit.temp_bob()
    data = payload if isinstance(payload, str) else json.dumps(payload)
    out = subprocess.run([sys.executable, str(HERE / script)], input=data, env=dict(os.environ), cwd=bob,
                         capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, f"{script} exited {out.returncode}: {out.stderr}"
    return json.loads(out.stdout) if out.stdout.strip() else None


def denied(decision) -> bool:
    return bool(decision) and decision["hookSpecificOutput"]["permissionDecision"] == "deny"


def call(tool: str, **tool_input) -> dict:
    return {"tool_name": tool, "tool_input": tool_input}


# ---- protect_gate -----------------------------------------------------------------------------

def test_edit_on_ledger_denied():
    assert denied(run_gate("protect_gate.py", call("Edit", file_path="data/state/calls.jsonl")))


def test_edit_on_thesis_allowed():
    assert not denied(run_gate("protect_gate.py", call("Edit", file_path="theses/NVDA.md")))


def test_write_on_manifest_denied():
    assert denied(run_gate("protect_gate.py", call("Write", file_path="bob.toml", content="x")))


def test_edit_on_own_hooks_denied():
    assert denied(run_gate("protect_gate.py", call("Edit", file_path="harness/hooks/protect_gate.py")))


def test_edit_on_evidence_by_absolute_path_denied():
    bob = testkit.temp_bob()
    target = bob / "data" / "evidence" / "2026-10-13" / "x" / "raw.txt"
    assert denied(run_gate("protect_gate.py", call("Write", file_path=str(target), content="x"), bob))


def test_shell_append_to_ledger_denied():
    assert denied(run_gate("protect_gate.py", call("Bash", command="echo '{}' >> data/state/calls.jsonl")))


def test_shell_read_of_ledger_allowed():
    assert not denied(run_gate("protect_gate.py", call("Bash", command="cat data/state/calls.jsonl")))


def test_shell_cli_that_writes_state_allowed():
    assert not denied(run_gate("protect_gate.py",
                               call("Bash", command="python3 harness/calls.py verdict NVDA --verdict watch --lane numbers")))


def test_powershell_remove_evidence_denied():
    assert denied(run_gate("protect_gate.py", call("PowerShell", command="Remove-Item data\\evidence\\2026-10-13 -Recurse")))


def test_protect_gate_fails_closed():
    assert denied(run_gate("protect_gate.py", "not json"))


# ---- order_gate -------------------------------------------------------------------------------

def test_order_endpoint_denied():
    assert denied(run_gate("order_gate.py", call("Bash", command=(
        "curl -X POST https://paper-api.alpaca.markets/v2/orders -d '{\"symbol\":\"NVDA\"}'"))))


def test_live_host_denied():
    assert denied(run_gate("order_gate.py", call("WebFetch", url="https://api.alpaca.markets/v2/account", prompt="x")))


def test_alpaca_mcp_tool_denied():
    assert denied(run_gate("order_gate.py", call("mcp__alpaca__place_stock_order", symbol="NVDA")))


def test_data_and_calendar_reads_allowed():
    assert not denied(run_gate("order_gate.py", call("Bash", command=(
        "curl 'https://data.alpaca.markets/v2/stocks/bars?symbols=SPY&timeframe=1Day'"))))
    assert not denied(run_gate("order_gate.py", call("WebFetch", url="https://paper-api.alpaca.markets/v2/calendar", prompt="x")))


def test_order_gate_fails_closed():
    assert denied(run_gate("order_gate.py", "{broken"))


if __name__ == "__main__":
    raise SystemExit(testkit.run(globals()))
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd bobs/investor && python3 harness/hooks/test_hooks.py`
Expected: every test FAILs (`... exited 2: can't open file ... protect_gate.py`).

- [ ] **Step 3: Write `protect_gate.py`**

```python
#!/usr/bin/env python3
"""PreToolUse gate: the Scout never edits its own record, evidence, settings or guards.

The ledger, the saved evidence, the manifest (which holds the scorecard weights) and these hooks
change only through their CLIs or through a person. An agent that could rewrite them could make
its track record look better, change what "buy-worthy" means, or switch this gate off: exactly
the prompt-only limits that sank the trading agents this Bob was designed against.

Refuses: Edit/Write/MultiEdit/NotebookEdit on any file under the state dir, the evidence dir,
harness/hooks/, bob.toml or .claude/settings.json; a Bash or PowerShell command that names one of
those paths and also writes (redirect, tee, sed -i, rm, mv, cp, Set-Content, Remove-Item, ...).
Reading them is allowed, and so is running the Bob's own CLIs.

Fails CLOSED: this gate protects state, so input it cannot read is refused.
"""
from __future__ import annotations

import json
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import scoutpath  # noqa: E402,F401
import scoutcfg  # noqa: E402

FILE_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}
SHELL_TOOLS = {"Bash", "PowerShell"}
WRITES = re.compile(r"(?<![0-9&])>|\btee\b|\bsed\b[^|;&]*\s-i|\brm\b|\bmv\b|\bcp\b|\btruncate\b|\bdd\b|"
                    r"\bln\b|Set-Content|Add-Content|Out-File|Remove-Item|Move-Item|Copy-Item|Rename-Item|"
                    r"Clear-Content|\bpython3?\s+-c\b|\bgit\s+(checkout|restore|reset|clean)\b", re.I)


def protected() -> list[pathlib.Path]:
    bob = scoutcfg.bob_dir()
    return [scoutcfg.state_dir(), scoutcfg.evidence_dir(), bob / "harness" / "hooks", bob / "bob.toml",
            bob / ".claude" / "settings.json"]


def _inside(path: pathlib.Path, roots: list[pathlib.Path]) -> bool:
    resolved = path.resolve()
    return any(resolved == root.resolve() or resolved.is_relative_to(root.resolve()) for root in roots)


def decide(payload: dict, cwd: pathlib.Path) -> str | None:
    tool = payload.get("tool_name", "")
    data = payload.get("tool_input") or {}
    roots = protected()
    if tool in FILE_TOOLS:
        target = data.get("file_path") or data.get("notebook_path")
        if target and _inside(cwd / target, roots):
            return (f"{target} is protected (ledger, evidence, manifest, settings or hooks). Use the "
                    "Bob's CLIs (calls.py, evidence.save) or ask the person who directs this Bob.")
        return None
    if tool in SHELL_TOOLS:
        command = str(data.get("command", "")).replace("\\", "/")
        bob = scoutcfg.bob_dir().resolve()
        names = {r.resolve().relative_to(bob).as_posix() for r in roots if r.resolve().is_relative_to(bob)}
        named = sorted(n for n in names if n in command)
        if named and WRITES.search(command):
            return (f"this command writes near protected paths ({', '.join(named)}). Only the Bob's CLIs "
                    "change the ledger and evidence; manifest, settings and hooks change by a person.")
    return None


def _deny(reason: str) -> None:
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                             "permissionDecisionReason": reason}}))


def main() -> int:
    try:
        payload = json.load(sys.stdin)
        reason = decide(payload, pathlib.Path.cwd())
    except Exception as exc:  # noqa: BLE001 - fail closed on anything this gate cannot judge
        reason = f"protect_gate could not judge this call ({type(exc).__name__}); refusing, as state gates do"
    if reason:
        _deny(reason)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Write `order_gate.py`**

```python
#!/usr/bin/env python3
"""PreToolUse gate: no Alpaca order, position or account change, and no live trading host, ever.

This Bob is read-only. prices.py already refuses any URL outside the data host and the calendar,
but the agent also has a shell, WebFetch and possibly MCP servers; this gate closes those doors.
It refuses any Alpaca MCP tool, any call input naming an orders, positions, account-configuration
or watchlists endpoint, and any mention of the live host api.alpaca.markets. Market data
(data.alpaca.markets) and the calendar stay allowed.

When a Trader Bob is built it gets its own manifest, credentials and its own order gate; nothing
here is loosened for it.

Fails CLOSED: input it cannot read is refused.
"""
from __future__ import annotations

import json
import re
import sys

ORDER_PATHS = re.compile(r"alpaca\.markets/v\d+/(orders|positions|account/configurations|watchlists)", re.I)
LIVE_HOST = re.compile(r"(?<![\w.-])api\.alpaca\.markets", re.I)
REASON = ("This Bob is read-only and never trades: Alpaca order, position and account endpoints, the "
          "live trading host and Alpaca tools are refused. Market data and the calendar are allowed.")


def decide(payload: dict) -> str | None:
    tool = str(payload.get("tool_name", ""))
    if tool.startswith("mcp__") and "alpaca" in tool.lower():
        return REASON
    text = json.dumps(payload.get("tool_input") or {})
    if ORDER_PATHS.search(text) or LIVE_HOST.search(text):
        return REASON
    return None


def main() -> int:
    try:
        reason = decide(json.load(sys.stdin))
    except Exception as exc:  # noqa: BLE001 - fail closed
        reason = f"order_gate could not judge this call ({type(exc).__name__}); refusing"
    if reason:
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                                 "permissionDecisionReason": reason}}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 5: Run to verify it passes**

Run: `cd bobs/investor && python3 harness/hooks/test_hooks.py`
Expected: `15 of 15 passed`.

- [ ] **Step 6: Break-proof both gates**

With the editor, in `protect_gate.decide` change `if named and WRITES.search(command):` to `if False:`. Run: `test_shell_append_to_ledger_denied` and `test_powershell_remove_evidence_denied` must FAIL. Revert. In `order_gate.decide` change `if ORDER_PATHS.search(text) or LIVE_HOST.search(text):` to `if LIVE_HOST.search(text):`. Run: `test_order_endpoint_denied` must FAIL. Revert; `15 of 15 passed`.

- [ ] **Step 7: Register the gates**

Read `bobs/investor/.claude/settings.json` (written by `new_bob.py`). Add two entries to the `PreToolUse` list, before the `"*"` skill-gate entry, so it becomes:

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Bash",
        "hooks": [{"type": "command", "command": "cd \"$CLAUDE_PROJECT_DIR\" && python3 ../../harness/hooks/commit_gate.py"}]
      },
      {
        "matcher": "Edit|Write|MultiEdit|NotebookEdit|Bash|PowerShell",
        "hooks": [{"type": "command", "command": "cd \"$CLAUDE_PROJECT_DIR\" && python3 harness/hooks/protect_gate.py"}]
      },
      {
        "matcher": "Bash|PowerShell|WebFetch|mcp__.*",
        "hooks": [{"type": "command", "command": "cd \"$CLAUDE_PROJECT_DIR\" && python3 harness/hooks/order_gate.py"}]
      },
      {
        "matcher": "*",
        "hooks": [{"type": "command", "command": "cd \"$CLAUDE_PROJECT_DIR\" && python3 ../../harness/hooks/skill_gate.py"}]
      }
    ]
  }
}
```

Keep the two core entries exactly as `new_bob.py` wrote them (their commands may differ in spacing from the above); only the two middle entries are new. Validate: `python3 -c "import json; json.load(open('bobs/investor/.claude/settings.json'))"` prints nothing.

- [ ] **Step 8: Run the whole Bob suite and commit**

Run: `bobs/investor/selftest.sh`
Expected: every line `ok`.

```bash
git add bobs/investor/harness/hooks bobs/investor/.claude/settings.json
git commit -F <msg file: "Add protect_gate and order_gate hooks and register them" + Co-Authored-By line>
```

---

## Milestone D: Output and go-live

### Task 15: `digest.py`: the weekly digest

**Files:**
- Create: `bobs/investor/harness/digest.py`, `bobs/investor/harness/test_digest.py`

**Interfaces:**
- Consumes: `calls.read()`, `ledger_score.results_path/MIN_SCORED/HORIZONS`, `screen.screen_dir()`, `edgar.read_json/write_json`, `scoutcfg.bob_dir()`.
- Produces: `RANK = {"pass": 0, "could not tell": 1, "watch": 2, "buy-worthy": 3}`, `build(today: dt.date) -> tuple[str, dict]` (Markdown, data), `write(today) -> pathlib.Path` (writes `digests/<date>.md` and `digests/<date>.json` in the Bob folder, returns the `.md` path). Data keys: `week_ending, new_calls, changes, withdrawn, matured, entrants, summary, by_lane, scored_12m, min_scored, noise, torn_lines`. CLI `--write`.

- [ ] **Step 1: Write the failing tests**

Create `bobs/investor/harness/test_digest.py`:

```python
#!/usr/bin/env python3
"""digest: built from the ledger and computed results only, with the noise banner and interim labels.

Run:  python3 harness/test_digest.py
"""
from __future__ import annotations

import datetime as dt
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import scoutpath  # noqa: E402,F401
import digest  # noqa: E402
import testkit  # noqa: E402

TODAY = dt.date(2026, 10, 17)


def _world(scored_12m: int = 0) -> pathlib.Path:
    bob = testkit.temp_bob()
    state = bob / "data" / "state"
    (state / "screen").mkdir(parents=True)
    rows = [
        {"kind": "verdict", "ticker": "ACME", "verdict": "watch", "lane": "story", "session": "2026-10-05", "at": "2026-10-06T05:00:00+00:00", "source": "s"},
        {"kind": "verdict", "ticker": "ACME", "verdict": "buy-worthy", "lane": "story", "session": "2026-10-14", "at": "2026-10-15T05:00:00+00:00", "source": "s"},
        {"kind": "call", "id": "ACME-2026-10-14-1", "ticker": "ACME", "symbol": "ACME", "lane": "story", "session": "2026-10-14",
         "at": "2026-10-15T05:00:00+00:00", "score": 88.0},
        {"kind": "verdict", "ticker": "OLD", "verdict": "pass", "lane": "numbers", "session": "2026-10-15", "at": "2026-10-16T05:00:00+00:00", "source": None},
        {"kind": "withdrawn", "call_id": "OLD-2026-01-05-1", "ticker": "OLD", "session": "2026-10-15", "at": "2026-10-16T05:00:00+00:00", "reason": "verdict now pass"},
    ]
    (state / "calls.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    summary = {"3": {"n": 2, "avg_excess_spy": 0.04, "avg_excess_qqq": 0.01, "beat_spy": 0.5, "interim": True},
               "6": {"n": 0, "avg_excess_spy": None, "avg_excess_qqq": None, "beat_spy": None, "interim": True},
               "12": {"n": scored_12m, "avg_excess_spy": 0.1 if scored_12m else None, "avg_excess_qqq": 0.05 if scored_12m else None,
                      "beat_spy": 0.6 if scored_12m else None, "interim": False}}
    results = {"summary": summary, "by_lane": {"numbers": summary, "story": summary}, "calls": [
        {"id": "OLD-2026-01-05-1", "ticker": "OLD", "lane": "numbers", "horizons": {
            "3": {"status": "scored", "due": "2026-10-15", "excess_spy": 0.06, "excess_qqq": 0.05},
            "12": {"status": "pending", "due": "2027-01-06"}}}]}
    (state / "ledger_results.json").write_text(json.dumps(results))
    (state / "screen" / "2026-10-14.json").write_text(json.dumps({"session": "2026-10-14", "new_entrants": ["ACME", "ZED"]}))
    (state / "screen" / "2026-10-01.json").write_text(json.dumps({"session": "2026-10-01", "new_entrants": ["OLDNEWS"]}))
    return bob


def test_lists_calls_changes_withdrawals_and_entrants():
    _world()
    text, data = digest.build(TODAY)
    assert [c["id"] for c in data["new_calls"]] == ["ACME-2026-10-14-1"]
    assert data["changes"] == [{"ticker": "ACME", "from": "watch", "to": "buy-worthy", "direction": "up"}]
    assert [w["call_id"] for w in data["withdrawn"]] == ["OLD-2026-01-05-1"]
    assert data["entrants"] == ["ACME", "ZED"]
    assert "ACME: watch -> buy-worthy (up)" in text


def test_matured_horizon_is_labelled_interim():
    _world()
    text, data = digest.build(TODAY)
    assert data["matured"] == [{"id": "OLD-2026-01-05-1", "ticker": "OLD", "months": 3, "excess_spy": 0.06, "excess_qqq": 0.05}]
    assert "OLD 3 months (interim): +6.0% vs SPY, +5.0% vs QQQ" in text


def test_noise_banner_below_twenty_scored_calls():
    _world(scored_12m=3)
    text, data = digest.build(TODAY)
    assert data["noise"] and "Too few scored calls to judge" in text
    _world(scored_12m=20)
    text, data = digest.build(TODAY)
    assert not data["noise"] and "Too few scored calls" not in text


def test_summary_numbers_come_from_results():
    _world()
    text, _ = digest.build(TODAY)
    assert "| 3 months (interim) | 2 | +4.0% | +1.0% | 50% |" in text
    assert "| 12 months | 0 | n/a | n/a | n/a |" in text


def test_write_creates_markdown_and_json():
    bob = _world()
    path = digest.write(TODAY)
    assert path == bob / "digests" / "2026-10-17.md" and (bob / "digests" / "2026-10-17.json").is_file()


if __name__ == "__main__":
    raise SystemExit(testkit.run(globals()))
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd bobs/investor && python3 harness/test_digest.py`
Expected: `ModuleNotFoundError: No module named 'digest'`.

- [ ] **Step 3: Write `digest.py`**

```python
#!/usr/bin/env python3
"""The weekly digest: what the Scout called, changed and learned, in Markdown plus JSON.

Every number comes from the ledger and ledger_score's results, never from the agent's prose, so
the digest cannot round a loss into a win. The noise banner and the interim labels are written by
code for the same reason. The JSON copy is what a future sender (Telegram) reads; the Markdown is
what a person reads today.

Usage:
    python3 harness/digest.py --write      # digests/<today>.md and .json in the Bob folder
"""
from __future__ import annotations

import argparse
import datetime as dt
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import scoutpath  # noqa: E402,F401
import calls  # noqa: E402
import edgar  # noqa: E402
import ledger_score  # noqa: E402
import scoutcfg  # noqa: E402
import screen  # noqa: E402

WINDOW_DAYS = 7
RANK = {"pass": 0, "could not tell": 1, "watch": 2, "buy-worthy": 3}


def _pct(value) -> str:
    return "n/a" if value is None else f"{value:+.1%}"


def _share(value) -> str:
    return "n/a" if value is None else f"{value:.0%}"


def _changes(rows: list[dict], since: str) -> list[dict]:
    out, last = [], {}
    for row in (r for r in rows if r.get("kind") == "verdict"):
        before = last.get(row["ticker"])
        if row["at"][:10] > since and before and before != row["verdict"]:
            out.append({"ticker": row["ticker"], "from": before, "to": row["verdict"],
                        "direction": "up" if RANK[row["verdict"]] > RANK[before] else "down"})
        last[row["ticker"]] = row["verdict"]
    return out


def _matured(results: dict, since: str, until: str) -> list[dict]:
    return [{"id": c["id"], "ticker": c["ticker"], "months": int(h), "excess_spy": s["excess_spy"],
             "excess_qqq": s["excess_qqq"]}
            for c in results.get("calls", []) for h, s in c["horizons"].items()
            if s.get("status") == "scored" and since < s["due"] <= until]


def _entrants(since: str) -> list[str]:
    seen: dict[str, None] = {}
    for path in sorted(screen.screen_dir().glob("????-??-??.json")):
        if path.stem > since:
            seen.update(dict.fromkeys(edgar.read_json(path, {}).get("new_entrants", [])))
    return list(seen)


def _table(summary: dict) -> list[str]:
    lines = ["| horizon | calls scored | avg vs SPY | avg vs QQQ | beat SPY |", "|---|---|---|---|---|"]
    for h in ledger_score.HORIZONS:
        s = summary.get(str(h), {})
        label = f"{h} months" + (" (interim)" if s.get("interim", h != 12) else "")
        lines.append(f"| {label} | {s.get('n', 0)} | {_pct(s.get('avg_excess_spy'))} | "
                     f"{_pct(s.get('avg_excess_qqq'))} | {_share(s.get('beat_spy'))} |")
    return lines


def build(today: dt.date) -> tuple[str, dict]:
    rows, torn = calls.read()
    since = (today - dt.timedelta(days=WINDOW_DAYS)).isoformat()
    recent = [r for r in rows if r["at"][:10] > since]
    results = edgar.read_json(ledger_score.results_path(), {})
    summary = results.get("summary", {})
    scored_12m = summary.get("12", {}).get("n", 0)
    data = {"week_ending": today.isoformat(),
            "new_calls": [{k: r.get(k) for k in ("id", "ticker", "lane", "score")} for r in recent if r["kind"] == "call"],
            "changes": _changes(rows, since),
            "withdrawn": [{k: r[k] for k in ("call_id", "ticker", "reason")} for r in recent if r["kind"] == "withdrawn"],
            "matured": _matured(results, since, today.isoformat()), "entrants": _entrants(since),
            "summary": summary, "by_lane": results.get("by_lane", {}), "scored_12m": scored_12m,
            "min_scored": ledger_score.MIN_SCORED, "noise": scored_12m < ledger_score.MIN_SCORED, "torn_lines": torn}
    lines = [f"# Investor Scout digest, week ending {today.isoformat()}", ""]
    if data["noise"]:
        lines += [f"> Too few scored calls to judge: {scored_12m} of {ledger_score.MIN_SCORED} needed for a "
                  "12-month verdict. Treat every number below as noise.", ""]
    lines += ["## New buy-worthy calls", ""]
    lines += [f"- {c['ticker']} ({c['lane']} lane, score {c['score']:.0f}): theses/{c['ticker']}.md"
              for c in data["new_calls"]] or ["- none"]
    lines += ["", "## Verdict changes", ""]
    lines += [f"- {c['ticker']}: {c['from']} -> {c['to']} ({c['direction']})" for c in data["changes"]] or ["- none"]
    lines += ["", "## Withdrawn calls (still scored)", ""]
    lines += [f"- {w['call_id']}: {w['reason']}" for w in data["withdrawn"]] or ["- none"]
    lines += ["", "## Calls reaching 3, 6 or 12 months", ""]
    lines += [f"- {m['ticker']} {m['months']} months{' (interim)' if m['months'] != 12 else ''}: "
              f"{_pct(m['excess_spy'])} vs SPY, {_pct(m['excess_qqq'])} vs QQQ" for m in data["matured"]] or ["- none"]
    lines += ["", "## Scout vs SPY vs QQQ (all calls)", ""] + _table(summary)
    for lane in ("numbers", "story"):
        lines += ["", f"## {lane.capitalize()} lane", ""] + _table(data["by_lane"].get(lane, {}))
    lines += ["", "## New names on the shortlist this week", "", ", ".join(data["entrants"]) or "none"]
    if torn:
        lines += ["", f"> {torn} torn ledger line(s) were skipped; see calls.py list."]
    return "\n".join(lines) + "\n", data


def write(today: dt.date) -> pathlib.Path:
    text, data = build(today)
    folder = scoutcfg.bob_dir() / "digests"
    folder.mkdir(exist_ok=True)
    path = folder / f"{today.isoformat()}.md"
    path.write_text(text)
    edgar.write_json(folder / f"{today.isoformat()}.json", data)
    return path


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--write", action="store_true", required=True)
    ap.parse_args(argv)
    print(f"OK {write(dt.date.today())}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd bobs/investor && python3 harness/test_digest.py`
Expected: `5 of 5 passed`.

- [ ] **Step 5: Break-proof the noise banner**

With the editor, change `"noise": scored_12m < ledger_score.MIN_SCORED` to `"noise": False`. Run: `test_noise_banner_below_twenty_scored_calls` must FAIL. Revert; `5 of 5 passed`.

- [ ] **Step 6: Commit**

```bash
git add bobs/investor/harness/digest.py bobs/investor/harness/test_digest.py
git commit -F <msg file: "Add digest: weekly Markdown and JSON from ledger and results" + Co-Authored-By line>
```

---

### Task 16: `scout_board.py`: the board panel

**Files:**
- Create: `bobs/investor/harness/scout_board.py`, `bobs/investor/harness/test_scout_board.py`

**Interfaces:**
- Consumes: core `board_shell.BaseHandler`, `board_shell.Hub`, `board_shell.Watcher`; `screen.latest()`, `ledger_score.results_path/MIN_SCORED`, `thesis_lint.header`, `prices.load/sessions`, `marketclock.is_stale`, `scoutcfg.bob_dir/state_dir`, `edgar.read_json`, `fetch.utcnow`.
- Produces: `class Watched` (`fingerprint() -> tuple`), `class Model(title)` with `ev`, `_lock`, `snapshot`, `rebuild() -> dict` (keys `title, version, session, stale, shortlist, theses, calls, summary, by_lane, basket, scored_12m, min_scored`), `class Handler(board_shell.BaseHandler)` adding `GET /thesis/<TICKER>` (text/plain), `PAGE: bytes`, `serve(model, host, port, ...) -> ThreadingHTTPServer`, `main(argv) -> int`. Run with `python3 harness/scout_board.py` (port and title from `bob.toml`).

- [ ] **Step 1: Write the failing tests**

Create `bobs/investor/harness/test_scout_board.py`:

```python
#!/usr/bin/env python3
"""scout_board: the model reads only computed files, the page and routes answer, paths cannot escape.

Run:  python3 harness/test_scout_board.py
"""
from __future__ import annotations

import json
import pathlib
import sys
import threading
import time
import urllib.error
import urllib.request

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import scoutpath  # noqa: E402,F401
import scout_board  # noqa: E402
import testkit  # noqa: E402
from test_thesis_lint import thesis  # noqa: E402


def _world() -> pathlib.Path:
    bob = testkit.temp_bob()
    state = bob / "data" / "state"
    (state / "screen").mkdir(parents=True)
    (state / "screen" / "latest.json").write_text(json.dumps({"session": "2026-10-12", "shortlist": ["NVDA"], "cards": {
        "NVDA": {"name": "NVIDIA CORP", "score": 91.0, "coverage": 1.0, "gate": "eligible", "reason": "r"}}}))
    (state / "ledger_results.json").write_text(json.dumps({"summary": {"12": {"n": 0}}, "by_lane": {}, "calls": [],
                                                           "basket": {"dates": [], "scout": [], "spy": [], "qqq": []}}))
    (bob / "theses" / "NVDA.md").write_text(thesis("watch"))
    return bob


def test_rebuild_reads_screen_results_and_theses():
    _world()
    snap = scout_board.Model("Investor Scout").rebuild()
    assert snap["session"] == "2026-10-12" and snap["shortlist"][0]["ticker"] == "NVDA"
    assert snap["shortlist"][0]["verdict"] == "watch" and snap["theses"] == [
        {"ticker": "NVDA", "verdict": "watch", "lane": "numbers", "date": "2026-10-13"}]
    assert snap["scored_12m"] == 0 and snap["min_scored"] == 20 and snap["stale"] is True


def test_fingerprint_changes_when_a_thesis_changes():
    bob = _world()
    model = scout_board.Model("t")
    before = model.ev.fingerprint()
    time.sleep(0.05)
    (bob / "theses" / "NVDA.md").write_text(thesis("pass"))
    assert model.ev.fingerprint() != before


def _get(port: int, path: str) -> tuple[int, str]:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=5) as resp:
            return resp.status, resp.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, ""


def test_routes():
    _world()
    model = scout_board.Model("Investor Scout")
    server = scout_board.serve(model, "127.0.0.1", 0, quiet=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    port = server.server_address[1]
    try:
        status, page = _get(port, "/")
        assert status == 200 and "Scout vs S&amp;P 500 vs Nasdaq-100" in page
        status, body = _get(port, "/api/state")
        assert status == 200 and json.loads(body)["shortlist"][0]["ticker"] == "NVDA"
        status, body = _get(port, "/thesis/NVDA")
        assert status == 200 and body.startswith("# NVDA")
        assert _get(port, "/thesis/..%2Fbob.toml")[0] == 404
        assert _get(port, "/thesis/MSFT")[0] == 404
    finally:
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    raise SystemExit(testkit.run(globals()))
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd bobs/investor && python3 harness/test_scout_board.py`
Expected: `ModuleNotFoundError: No module named 'scout_board'`.

- [ ] **Step 3: Write `scout_board.py`**

```python
#!/usr/bin/env python3
"""The Investor Scout board: shortlist, theses, the call table and Scout vs S&P 500 vs Nasdaq-100.

Built on core's board_shell (plumbing, /api/state, /events). Every number on the page comes from
screen/latest.json and ledger_results.json, which code computed; the page only draws them. The
chart is inline SVG drawn by the page's own script, no libraries. "Too few calls to judge" shows
below 20 scored 12-month calls, interim horizons are labelled, stale prices are flagged.

Usage:
    python3 harness/scout_board.py               # port and title from bob.toml
    python3 harness/scout_board.py --port 8791 --quiet
"""
from __future__ import annotations

import argparse
import pathlib
import re
import sys
import threading
from http.server import ThreadingHTTPServer

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import scoutpath  # noqa: E402,F401
import bobcfg  # noqa: E402
import board_shell  # noqa: E402
import edgar  # noqa: E402
import fetch  # noqa: E402
import ledger_score  # noqa: E402
import marketclock  # noqa: E402
import prices  # noqa: E402
import scoutcfg  # noqa: E402
import screen  # noqa: E402
import thesis_lint  # noqa: E402

TICKER = re.compile(r"^[A-Z][A-Z0-9.\-]{0,9}$")


class Watched:
    """Fixed files plus every thesis, so editing a thesis refreshes the page like a new screen does."""

    def __init__(self, files: list[pathlib.Path], theses: pathlib.Path):
        self.files, self.theses = files, theses

    def fingerprint(self) -> tuple:
        out = []
        for path in [*self.files, *sorted(self.theses.glob("*.md"))]:
            try:
                st = path.stat()
                out.append((str(path), st.st_mtime_ns, st.st_size))
            except OSError:
                out.append((str(path), None, None))
        return tuple(out)


class Model:
    def __init__(self, title: str):
        self.title = title
        state, self.theses = scoutcfg.state_dir(), scoutcfg.bob_dir() / "theses"
        self.ev = Watched([state / "screen" / "latest.json", ledger_score.results_path(), state / "calls.jsonl"],
                          self.theses)
        self._lock = threading.Lock()
        self.snapshot: dict | None = None
        self._builds = 0

    def _theses(self) -> dict[str, dict]:
        out = {}
        for path in sorted(self.theses.glob("*.md")):
            head = thesis_lint.header(path.read_text())
            out[path.stem] = {"ticker": path.stem, "verdict": head.get("verdict"), "lane": head.get("lane"),
                              "date": head.get("date")}
        return out

    def rebuild(self) -> dict:
        self._builds += 1
        doc = screen.latest()
        results = edgar.read_json(ledger_score.results_path(), {})
        theses = self._theses()
        spy = prices.load("SPY")["bars"]
        stale = marketclock.is_stale(spy[-1][0] if spy else None, prices.sessions(), fetch.utcnow())
        cards = doc.get("cards", {})
        shortlist = [{"ticker": t, "name": cards[t].get("name"), "score": cards[t].get("score"),
                      "coverage": cards[t].get("coverage"), "gate": cards[t].get("gate"),
                      "reason": cards[t].get("reason"), "verdict": theses.get(t, {}).get("verdict")}
                     for t in doc.get("shortlist", []) if t in cards]
        summary = results.get("summary", {})
        return {"title": self.title, "version": self._builds, "session": doc.get("session"), "stale": stale,
                "shortlist": shortlist, "theses": list(theses.values()), "calls": results.get("calls", []),
                "summary": summary, "by_lane": results.get("by_lane", {}),
                "basket": results.get("basket", {"dates": [], "scout": [], "spy": [], "qqq": []}),
                "scored_12m": summary.get("12", {}).get("n", 0), "min_scored": ledger_score.MIN_SCORED}


class Handler(board_shell.BaseHandler):
    def panel_route(self, path: str, raw_path: str) -> bool:
        if not path.startswith("/thesis/"):
            return False
        ticker = path[len("/thesis/"):]
        target = scoutcfg.bob_dir() / "theses" / f"{ticker}.md"
        if not TICKER.match(ticker) or not target.is_file():
            self._reject(404, "no such thesis")
            return True
        self._send(200, target.read_bytes(), "text/plain; charset=utf-8")
        return True


PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>Investor Scout</title>
<style>
:root{--bg:#0f141b;--fg:#d7dde6;--muted:#7d8a9c;--line:#2a3442;--up:#5fb37a;--down:#e07a5f;--scout:#e3b341;--spy:#6ea8fe;--qqq:#b392f0}
body{font:14px/1.5 system-ui,sans-serif;margin:16px;max-width:1100px;background:var(--bg);color:var(--fg)}
h1{font-size:20px}h2{font-size:15px;margin-top:28px;border-bottom:1px solid var(--line);padding-bottom:4px}
table{border-collapse:collapse;width:100%;font-size:13px}td,th{padding:4px 6px;border-bottom:1px solid var(--line);text-align:left}
.muted{color:var(--muted)}.up{color:var(--up)}.down{color:var(--down)}.banner{border:1px solid var(--down);padding:6px 10px;margin:8px 0}
.wrap{overflow-x:auto}button{background:none;color:var(--fg);border:1px solid var(--line);padding:2px 8px;cursor:pointer}
button.on{border-color:var(--scout)}a{color:var(--spy)}
</style></head><body>
<h1 id="title">Investor Scout</h1>
<div id="banners"></div>
<h2>Scout vs S&amp;P 500 vs Nasdaq-100</h2>
<div><button data-m="3">3 months</button> <button data-m="6">6 months</button> <button data-m="12">12 months</button> <button data-m="0" class="on">all</button>
 <span class="muted" id="legend"></span></div>
<div class="wrap"><svg id="chart" width="100%" height="260" viewBox="0 0 1000 260" preserveAspectRatio="none"></svg></div>
<div class="wrap"><table id="summary"></table></div>
<h2>Calls</h2><div class="wrap"><table id="calls"></table></div>
<h2>Shortlist <span class="muted" id="session"></span></h2><div class="wrap"><table id="shortlist"></table></div>
<h2>Theses</h2><div class="wrap"><table id="theses"></table></div>
<script>
var S = null, months = 0;
function pct(v){ return v === null || v === undefined ? "n/a" : (v >= 0 ? "+" : "") + (v * 100).toFixed(1) + "%"; }
function cls(v){ return v === null || v === undefined ? "muted" : (v >= 0 ? "up" : "down"); }
function el(tag, text, klass){ var e = document.createElement(tag); if (text !== undefined) e.textContent = text; if (klass) e.className = klass; return e; }
function table(id, head, rows){
  var t = document.getElementById(id); t.textContent = "";
  var tr = el("tr"); head.forEach(function(h){ tr.appendChild(el("th", h)); }); t.appendChild(tr);
  if (!rows.length){ var r = el("tr"); var td = el("td", "none", "muted"); td.colSpan = head.length; r.appendChild(td); t.appendChild(r); return; }
  rows.forEach(function(cells){ var r = el("tr"); cells.forEach(function(c){ r.appendChild(c instanceof Node ? c : el("td", c)); }); t.appendChild(r); });
}
function cell(v){ return el("td", pct(v), cls(v)); }
function link(t){ var td = el("td"); var a = el("a", t); a.href = "/thesis/" + encodeURIComponent(t); td.appendChild(a); return td; }
function chart(){
  var b = S.basket, svg = document.getElementById("chart"); svg.textContent = "";
  if (!b.dates.length){ document.getElementById("legend").textContent = "no calls yet"; return; }
  var start = 0;
  if (months){ var cut = new Date(b.dates[b.dates.length - 1]); cut.setMonth(cut.getMonth() - months);
    var iso = cut.toISOString().slice(0, 10); while (start < b.dates.length - 1 && b.dates[start] < iso) start++; }
  var series = [["scout", b.scout], ["spy", b.spy], ["qqq", b.qqq]].map(function(s){
    var base = s[1][start]; return [s[0], s[1].slice(start).map(function(v){ return v / base * 100; })]; });
  var all = [].concat.apply([], series.map(function(s){ return s[1]; }));
  var lo = Math.min.apply(null, all), hi = Math.max.apply(null, all), n = series[0][1].length;
  if (hi === lo) hi = lo + 1;
  series.forEach(function(s){
    var pts = s[1].map(function(v, i){ return (n > 1 ? i / (n - 1) * 1000 : 0).toFixed(1) + "," + (250 - (v - lo) / (hi - lo) * 240).toFixed(1); });
    var p = document.createElementNS("http://www.w3.org/2000/svg", "polyline");
    p.setAttribute("points", pts.join(" ")); p.setAttribute("fill", "none"); p.setAttribute("stroke-width", "2");
    p.setAttribute("stroke", "var(--" + s[0] + ")"); p.setAttribute("vector-effect", "non-scaling-stroke"); svg.appendChild(p); });
  var last = series.map(function(s){ return s[0].toUpperCase() + " " + s[1][n - 1].toFixed(1); });
  document.getElementById("legend").textContent = "from " + b.dates[start] + ", rebased to 100: " + last.join("  ") + "  (n = " + S.calls.length + " calls)";
}
function show(s){
  S = s; document.getElementById("title").textContent = s.title;
  var banners = document.getElementById("banners"); banners.textContent = "";
  if (s.scored_12m < s.min_scored) banners.appendChild(el("div", "Too few calls to judge: " + s.scored_12m + " of " + s.min_scored + " scored at 12 months. Every result below is noise.", "banner"));
  if (s.stale) banners.appendChild(el("div", "Prices are stale: the last SPY bar is older than the last completed session.", "banner"));
  document.getElementById("session").textContent = s.session ? "session " + s.session : "no screen yet";
  table("summary", ["horizon", "scored", "avg vs SPY", "avg vs QQQ", "beat SPY"], ["3", "6", "12"].map(function(h){
    var x = s.summary[h] || {}; return [h + " months" + (h === "12" ? "" : " (interim)"), String(x.n || 0), cell(x.avg_excess_spy), cell(x.avg_excess_qqq),
      x.beat_spy === null || x.beat_spy === undefined ? "n/a" : (x.beat_spy * 100).toFixed(0) + "%"]; }));
  table("calls", ["ticker", "lane", "called", "entry", "3m vs SPY", "3m vs QQQ", "6m vs SPY", "6m vs QQQ", "12m vs SPY", "12m vs QQQ", "status"],
    s.calls.map(function(c){
      var cells = [link(c.ticker), c.lane, c.session, c.entry_date ? c.entry_date + " @ " + c.entry_close.toFixed(2) : "pending"];
      ["3", "6", "12"].forEach(function(h){ var x = c.horizons[h] || {};
        if (x.status === "scored"){ cells.push(cell(x.excess_spy)); cells.push(cell(x.excess_qqq)); }
        else { cells.push(el("td", x.status || "pending", "muted")); cells.push(el("td", "", "muted")); } });
      cells.push((c.withdrawn ? "withdrawn" : "open") + (c.delisted ? ", delisted" : "")); return cells; }));
  table("shortlist", ["ticker", "name", "score", "coverage", "gate", "thesis verdict"], s.shortlist.map(function(r){
    return [link(r.ticker), r.name || "", r.score === null ? "could not tell" : r.score.toFixed(0), (r.coverage * 100).toFixed(0) + "%", r.gate + " (" + r.reason + ")", r.verdict || "no thesis"]; }));
  table("theses", ["ticker", "verdict", "lane", "date"], s.theses.map(function(t){ return [link(t.ticker), t.verdict || "?", t.lane || "?", t.date || "?"]; }));
  chart();
}
document.querySelectorAll("button[data-m]").forEach(function(b){ b.onclick = function(){
  months = +b.dataset.m; document.querySelectorAll("button[data-m]").forEach(function(x){ x.className = x === b ? "on" : ""; }); if (S) chart(); }; });
fetch("/api/state").then(function(r){ return r.json(); }).then(show);
var es = new EventSource("/events");
es.addEventListener("state", function(e){ show(JSON.parse(e.data)); });
</script></body></html>
""".encode("utf-8")


def serve(model: Model, host: str, port: int, *, heartbeat: float = 15.0, quiet: bool = False) -> ThreadingHTTPServer:
    model.snapshot = model.rebuild()
    Handler.model, Handler.hub, Handler.heartbeat, Handler.quiet, Handler.page = (
        model, board_shell.Hub(), heartbeat, quiet, PAGE)
    server = ThreadingHTTPServer((host, port), Handler)
    server.daemon_threads = True
    return server


def main(argv: list[str]) -> int:
    cfg = bobcfg.config()
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--port", type=int, default=bobcfg.get(cfg, "board.port"))
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--interval", type=float, default=2.0)
    ap.add_argument("--heartbeat", type=float, default=15.0)
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args(argv)
    model = Model(bobcfg.get(cfg, "board.title"))
    server = serve(model, a.host, a.port, heartbeat=a.heartbeat, quiet=a.quiet)
    board_shell.Watcher(model, Handler.hub, a.interval).start()
    print(f"{model.title} board :: http://{a.host}:{a.port}/", file=sys.stderr, flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
```

The `/thesis/` route decodes the path first (`BaseHandler` unquotes it), so `..%2Fbob.toml` arrives as `../bob.toml` and fails the `TICKER` pattern: 404.

- [ ] **Step 4: Run to verify it passes**

Run: `cd bobs/investor && python3 harness/test_scout_board.py`
Expected: `3 of 3 passed`.

- [ ] **Step 5: Break-proof the path check**

With the editor, change `if not TICKER.match(ticker) or not target.is_file():` to `if not target.is_file():`. Run: the `/thesis/..%2Fbob.toml` assertion in `test_routes` must FAIL (it returns 200 with the manifest). Revert; `3 of 3 passed`.

- [ ] **Step 6: Look at it in a browser**

Run `cd bobs/investor && python3 harness/scout_board.py` and open `http://127.0.0.1:8791/`. With no data it must show the "Too few calls" and "Prices are stale" banners, "no calls yet", and "none" rows, with no console errors. Check at a 375px-wide window: tables scroll sideways inside their box, the page itself does not. Stop the server by PID (`Get-Process python` / `Stop-Process -Id <pid>`).

- [ ] **Step 7: Commit**

```bash
git add bobs/investor/harness/scout_board.py bobs/investor/harness/test_scout_board.py
git commit -F <msg file: "Add scout_board: shortlist, theses, calls and Scout vs SPY vs QQQ chart" + Co-Authored-By line>
```

---

### Task 17: `edgar.py`: filing reader and 8-K list for the loops

The loops need two more SEC reads: the text of a filing (10-Q, 10-K, 8-K, press release) saved as evidence before Claude reads it, and the list of recent 8-Ks for the story lane.

**Files:**
- Modify: `bobs/investor/harness/edgar.py`, `bobs/investor/harness/test_edgar.py`

**Interfaces:**
- Produces: `SEC_HOSTS = ("https://www.sec.gov/", "https://data.sec.gov/")`, `Client.document(url) -> Path` (refuses non-SEC URLs with `fetch.FetchError`), `eight_ks(client, days, ciks: dict[int, str]) -> list[dict]` (rows from the daily index with form `8-K` or `8-K/A` for universe CIKs, plus `ticker` and `url`, one per filing). CLI: `edgar.py --read URL` prints `OK <evidence path>`; `edgar.py --eight-k --days 3` prints one line per filing: `TICKER  YYYY-MM-DD  FORM  URL`.

- [ ] **Step 1: Add the failing tests to `test_edgar.py`** (above the `if __name__` block)

```python
def test_document_refuses_non_sec_urls():
    called = []
    client = edgar.Client("Investor Scout a@b.test", getter=lambda u, h: called.append(u) or "x",
                          limiter=edgar.fetch.Limiter(1000))
    try:
        client.document("https://example.com/filing.htm")
    except edgar.fetch.FetchError as e:
        assert "refused" in str(e) and called == []
    else:
        raise AssertionError("a non-SEC URL was read")


def test_document_saves_sec_reads():
    testkit.temp_bob()
    client = edgar.Client("Investor Scout a@b.test", getter=lambda u, h: "<html>10-Q</html>",
                          limiter=edgar.fetch.Limiter(1000))
    where = client.document("https://www.sec.gov/Archives/edgar/data/1045810/x.htm")
    assert (where / "raw.txt").read_text() == "<html>10-Q</html>"


def test_eight_ks_lists_universe_filings_once():
    day = dt.date(2026, 10, 12)
    index = INDEX.replace("10-K        ACME WIDGETS INC  ", "8-K         ACME WIDGETS INC  ")
    client = FakeClient({day: edgar.parse_daily_index(index)})
    rows = edgar.eight_ks(client, [day, day], {1234567: "ACME"})
    assert [(r["ticker"], r["form"], r["date"]) for r in rows] == [("ACME", "8-K", "2026-10-12")]
    assert rows[0]["url"] == "https://www.sec.gov/Archives/edgar/data/1234567/0001234567-26-000123.txt"
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd bobs/investor && python3 harness/test_edgar.py`
Expected: the three new tests FAIL (`AttributeError: 'Client' object has no attribute 'document'`, `module 'edgar' has no attribute 'eight_ks'`); the other 14 PASS.

- [ ] **Step 3: Implement**

In `edgar.py`, after `ARCHIVE_URL`, add:

```python
SEC_HOSTS = ("https://www.sec.gov/", "https://data.sec.gov/")
```

Add this method to `Client` (after `form4`):

```python
    def document(self, url: str) -> pathlib.Path:
        """Any SEC document, saved as evidence before anyone reads it. Other hosts are refused:
        a thesis cites SEC filings, and other web reads go through evidence.py by hand."""
        if not url.startswith(SEC_HOSTS):
            raise fetch.FetchError(url, None, "refused: --read only fetches sec.gov documents")
        return self._read("sec-doc", url)[1]
```

Add after `sync_form4`'s helper `_refresh_owner`:

```python
def eight_ks(client, days: list[dt.date], ciks: dict[int, str]) -> list[dict]:
    found: dict[str, dict] = {}
    for day in sorted(set(days)):
        rows, _ = client.daily_index(day)
        for r in rows or []:
            if r["form"] in ("8-K", "8-K/A") and r["cik"] in ciks:
                found.setdefault(r["path"], {**r, "ticker": ciks[r["cik"]], "url": ARCHIVE_URL.format(path=r["path"])})
    return list(found.values())
```

In `main`, add arguments and handling (before `a = ap.parse_args(argv)` add the arguments; inside the `try`, before `if a.sync:`, add the branches):

```python
    ap.add_argument("--read", metavar="URL")
    ap.add_argument("--eight-k", action="store_true")
```

```python
        if a.read:
            print(f"OK {client.document(a.read)}")
            return 0
        if a.eight_k:
            rows = read_json(sec / "tickers.json", {}).get("rows", [])
            for r in eight_ks(client, _days_back(today, a.days), {t["cik"]: t["ticker"] for t in rows}):
                print(f"{r['ticker']:8} {r['date']}  {r['form']:6} {r['url']}")
            return 0
```

`--eight-k` uses `--days` (default 90); the loop passes `--days 3`. Update the module docstring's `Usage:` with the two new commands.

- [ ] **Step 4: Run to verify it passes**

Run: `cd bobs/investor && python3 harness/test_edgar.py`
Expected: `17 of 17 passed`.

- [ ] **Step 5: Break-proof the host check**

With the editor, change `if not url.startswith(SEC_HOSTS):` to `if False:`. Run: `test_document_refuses_non_sec_urls` must FAIL. Revert; `17 of 17 passed`.

- [ ] **Step 6: Commit**

```bash
git add bobs/investor/harness/edgar.py bobs/investor/harness/test_edgar.py
git commit -F <msg file: "edgar: SEC filing reader and 8-K list for the loops" + Co-Authored-By line>
```

---

### Task 18: Steering: loops, `CLAUDE.md` domain rules, lessons, README

**Files:**
- Create: `bobs/investor/loops/nightly-screen.txt`, `loops/story-lane.txt`, `loops/weekly-digest.txt`, `skills/stale-price.md`, `skills/refused-is-an-answer.md`, `README.md`, `harness/test_loops.py`
- Modify: `bobs/investor/CLAUDE.md` (scaffolded), `skills/INDEX.md` (regenerated)

**Interfaces:**
- Consumes: every CLI from Tasks 1 to 17 and core's `loop_passes.py`, `evidence.py`, `skill_index.py`.
- Produces: loop files whose names match `[loops.schedules]`; `test_loops.py` proving each loop registers, records first, and only calls CLIs and flags that exist.

- [ ] **Step 1: Write the failing test**

Create `bobs/investor/harness/test_loops.py`:

```python
#!/usr/bin/env python3
"""Loops are code too: each must register and record first, and call only CLIs and flags that exist.

A loop that names a renamed script fails at 07:30 with nobody watching; this test fails now instead.

Run:  python3 harness/test_loops.py
"""
from __future__ import annotations

import pathlib
import re
import sys
import tomllib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import scoutpath  # noqa: E402,F401
import testkit  # noqa: E402

BOB, CORE = scoutpath.BOB, scoutpath.CORE
OWN = tomllib.loads(testkit.MANIFEST.read_text())["loops"]["own"]
CALL = re.compile(r"python3 ((?:\.\./\.\./)?harness/[\w/]+\.py)((?: --[\w-]+| [\w<>.-]+)*)")


def _loops() -> dict[str, str]:
    return {pathlib.PurePosixPath(rel).stem: (BOB / rel).read_text() for rel in OWN}


def test_every_own_loop_exists():
    assert all((BOB / rel).is_file() for rel in OWN), OWN


def test_each_loop_registers_then_records_first():
    for name, text in _loops().items():
        lines = [l for l in text.splitlines() if l.strip()]
        assert lines[1].startswith("FIRST: CronList"), name
        assert lines[2].endswith(f"loop_passes.py --record {name}"), name


def test_loops_call_only_existing_clis_and_flags():
    for name, text in _loops().items():
        for script, args in CALL.findall(text):
            path = (CORE / script[len("../../"):]) if script.startswith("../../") else BOB / script
            assert path.is_file(), f"{name}: {script} does not exist"
            source = path.read_text()
            for flag in re.findall(r"--[\w-]+", args):
                assert f'"{flag}"' in source, f"{name}: {script} has no {flag}"


if __name__ == "__main__":
    raise SystemExit(testkit.run(globals()))
```

Run: `cd bobs/investor && python3 harness/test_loops.py`
Expected: FAIL (`FileNotFoundError` on `loops/nightly-screen.txt`).

- [ ] **Step 2: Write `loops/nightly-screen.txt`**

```
Nightly screen for Investor Scout. Schedule: 30 7 * * 2-6 (after the US close and SEC's overnight index).

FIRST: CronList. If this job is absent, re-register it from this file and say so.
SECOND, before any work: python3 ../../harness/loop_passes.py --record nightly-screen

Scripts do the numbers; you do the reading. Stop at the first FAIL, report it, never work around it.

1. python3 harness/prices.py --refresh
2. python3 harness/edgar.py --sync
3. python3 harness/screen.py --run
   A FAIL here means no screen today: no theses and no verdicts. Go to step 4, then 6.
4. python3 harness/ledger_score.py --run
5. Only if step 3 printed OK: for at most 5 names from its new= list, in that order:
   a. python3 harness/screen.py --card TICKER
   b. Find the latest 10-Q or 10-K and the earnings release (8-K, exhibit 99.1) on SEC, and read each with
      python3 harness/edgar.py --read URL
      It prints the evidence folder; read raw.txt there. Never read a filing any other way.
   c. Copy templates/thesis.md to theses/TICKER.md (or update the one there). Fill every section. Every line
      with a number ends with [ev:PATH] for the folder it came from. Paste the card unchanged under Scorecard.
      Reverse DCF: python3 harness/fundamentals/valuation.py --reverse --target MARKETCAP --fcf CASHFLOW
   d. python3 harness/thesis_lint.py theses/TICKER.md until it prints OK.
   e. python3 harness/calls.py verdict TICKER --verdict VERDICT --lane numbers
      REFUSED is the answer, not an obstacle. Write the reason under the thesis's Sources; never edit files
      to get past it.
6. One row in docs/TRACKER.md: steps run, OK or FAIL lines, theses written, verdicts recorded or refused.
```

- [ ] **Step 3: Write `loops/story-lane.txt`**

```
Story lane for Investor Scout. Schedule: 0 12 * * 1,3,5.

FIRST: CronList. If this job is absent, re-register it from this file and say so.
SECOND, before any work: python3 ../../harness/loop_passes.py --record story-lane

The story lane catches what the numbers cannot show yet (a new contract, a new product line). It is
measured separately, and switched off if it adds noise. At most 3 proposals a week in total.

1. python3 harness/calls.py list
   Count this week's story-lane verdicts in data/state/calls.jsonl (lane "story", at since Monday).
   If 3 or more: say so and stop.
2. python3 harness/edgar.py --eight-k --days 3
   Open the ones that look material with python3 harness/edgar.py --read URL and read raw.txt:
   item 1.01 (material agreement), 2.02 (results), 7.01 (press release).
3. Web search is allowed here only. Save every result you use before relying on it:
   write it to a file outside the Bob folder, then
   python3 ../../harness/evidence.py save --id TICKER-news-YYYYMMDD --query "SEARCH TERMS" --from FILE
4. For each proposal (up to the weekly limit):
   a. python3 harness/screen.py --card TICKER   (not scored means outside the universe: drop it)
   b. Write theses/TICKER.md from templates/thesis.md with Lane: story; the source goes under Sources.
   c. python3 harness/thesis_lint.py theses/TICKER.md until OK.
   d. python3 harness/calls.py verdict TICKER --verdict VERDICT --lane story --source EVIDENCE_PATH
      A story name that fails the scorecard gate is "watch", never "buy-worthy".
5. One row in docs/TRACKER.md: 8-Ks reviewed, proposals made, verdicts recorded or refused.
```

- [ ] **Step 4: Write `loops/weekly-digest.txt`**

```
Weekly digest for Investor Scout. Schedule: 0 9 * * 6.

FIRST: CronList. If this job is absent, re-register it from this file and say so.
SECOND, before any work: python3 ../../harness/loop_passes.py --record weekly-digest

1. python3 harness/ledger_score.py --run
2. python3 harness/digest.py --write
   It prints the path of this week's digest. Read it.
3. Reply in the terminal with the digest's path and three lines: new calls, verdict changes, and the
   12-month line with its n. Add commentary only around the digest's numbers; never restate them
   differently or round them.
4. If a lesson emerges (a thesis that went wrong for a reason you could have seen), write it under
   skills/ in the lessons format and run python3 ../../harness/skill_index.py --write.
```

- [ ] **Step 5: Run the loop test**

Run: `cd bobs/investor && python3 harness/test_loops.py`
Expected: `3 of 3 passed`. If a flag check fails, the loop names a flag the script lacks: fix the loop text, not the test.

- [ ] **Step 6: Edit `CLAUDE.md`**

In the scaffolded `bobs/investor/CLAUDE.md`, change the board line under "Where things live" to:

```
- Board: port 8791, `python3 harness/scout_board.py`.
```

and replace the placeholder text under `## Domain rules` with:

```markdown
- **Read-only.** This Bob never trades: no orders, previews, drafts or position sizes. `order_gate`
  refuses Alpaca order traffic; do not look for another way. Calls are research with a track record,
  not instructions to the person.
- **Sources.** SEC through `harness/edgar.py` (`--read URL` for any filing) and Alpaca market data
  through `harness/prices.py`. Web search only in the story lane, each result saved with
  `../../harness/evidence.py save` before use.
- **Evidence or it did not happen.** Every number in a thesis, digest or answer ends with
  `[ev:PATH]`. Quote figures; do not paraphrase them. A price carries its session date; a stale
  price is called stale.
- **Verdicts.** buy-worthy, watch, pass, could not tell. Missing data is "could not tell", never a
  guess and never zero.
- **Scripts compute, you read.** Never work out a score, a return or an excess by hand.
  `screen.py` and `ledger_score.py` compute; the board and digest show their output.
- **REFUSED is an answer.** When `calls.py` or `thesis_lint.py` refuses, record why. Never edit
  state, evidence, `bob.toml`, `.claude/settings.json` or `harness/hooks/` (`protect_gate` refuses
  it anyway).
- **Weights and thresholds** live in `[scout]` in `bob.toml`. Propose a change as a lesson citing
  ledger evidence; only the person changes them.
```

- [ ] **Step 7: Seed two lessons**

Create `bobs/investor/skills/stale-price.md`:

```
+++
summary = "Prices come from prices.py with their session date; a quote from a web page has no session and is not evidence"
tells = [
  { tool = "WebFetch", pattern = 'finance\.yahoo|google\.com/finance|marketwatch|investing\.com' },
]
+++

# A price without its session

What happened: seeded from the design, before any failure. Trading agents in published tests quoted
prices with no timestamp, then compared them with stale data.
What generalises: every price this Bob uses is a bar from prices.py, cited with the evidence folder
and the session date; anything behind the last completed session is called stale.
The tell: fetching a finance web page is the moment a price without a session would enter a thesis.
```

Create `bobs/investor/skills/refused-is-an-answer.md`:

```
+++
summary = "calls.py REFUSED is the result: write down why; never rework a thesis only to get past the gate"
tells = [
  { tool = "Bash", pattern = 'calls\.py verdict' },
]
+++

# REFUSED is an answer

What happened: seeded from the design. OpenProphet's agent could raise its own limits; a gate that
an agent routes around is no gate.
What generalises: a refusal says the call is not checkable yet (stale screen, gate not passed,
uncited number, open call). Record the reason; change the thesis only when the evidence changes.
The tell: every verdict command is the moment a refusal can arrive.
```

Run from `bobs/investor`: `python3 ../../harness/skill_index.py --write` then `python3 ../../harness/skill_index.py --check`.
Expected: `--check` reports both lessons valid and the index current.

- [ ] **Step 8: Write `README.md`**

Create `bobs/investor/README.md`:

````markdown
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
````

- [ ] **Step 9: Validate and commit**

Run from `bobs/investor`: `python3 ../../harness/bobcfg.py` (expected `OK ...`), `./selftest.sh` (every line `ok`), and from `bob_core`: `./selftest.sh` (every line `ok`).

```bash
git add bobs/investor/loops bobs/investor/skills bobs/investor/CLAUDE.md bobs/investor/README.md bobs/investor/harness/test_loops.py
git commit -F <msg file: "Add Scout loops, domain rules, seed lessons and README" + Co-Authored-By line>
```

---

### Task 19: Go live with the person

This task needs the person: their Alpaca keys, their Claude login for the Bob, and their machine's scheduler. Every result goes into `bobs/investor/docs/TRACKER.md` with its evidence path.

- [ ] **Step 1: Keys.** The person signs up for an Alpaca paper account and sets the keys as user environment variables in their own terminal (do not type the values in this session): `setx ALPACA_PAPER_KEY_ID <key>` and `setx ALPACA_PAPER_SECRET_KEY <secret>`, then opens a new terminal.

- [ ] **Step 2: Live smoke checks.** From `bobs/investor`:
  - `python3 harness/edgar.py --selftest-live` prints `OK tickers=... frame ... rows=...` with non-zero counts.
  - `python3 harness/prices.py --selftest-live` prints `OK SPY bars=...`. Confirm the bars are dividend-adjusted: SPY's first close in the output must be lower than SPY's unadjusted close for that date on any chart. If equal, stop: the ledger's total-return comparison depends on it.

- [ ] **Step 3: First fill.** Run in order, recording counts and durations: `python3 harness/edgar.py --sync`; `python3 harness/prices.py --refresh` (first run fetches full history for every symbol); `python3 harness/edgar.py --backfill-form4 --days 90` (about an hour at 8 requests a second).

- [ ] **Step 4: Golden check on a real company.** `python3 harness/xbrl.py --cik 1045810` prints NVIDIA's quarterly revenue series. Open NVIDIA's latest 10-Q with `python3 harness/edgar.py --read <URL of the 10-Q>` and compare the latest quarter's revenue figure. They must match exactly; record both with their evidence paths.

- [ ] **Step 4b: Checks the offline tests cannot make** (from the whole-branch review):
  - Multi-class names: `python3 harness/screen.py --card BRK-B` and `--card GOOGL` are scored (shares from the diluted fallback); the screen's OK line shows how many names have `no_shares`.
  - A full-universe price refresh: record its duration, `failed=` count and peak memory; any symbol Alpaca rejects must show in the WARN line, not kill the run.
  - Interrupt `edgar.py --backfill-form4 --days 90` once (Ctrl+C after a few minutes), rerun it, and confirm it resumes from `form4/coverage.json` instead of starting over.
  - The share of the real universe that is stale inside the floors (screen OK line `stale=`), against the 5% limit.
  - `python3 harness/insider.py --cik <a company with a known insider buy>` after the backfill: the buy is classified (look in `data/state/sec/form4/<date>.json` for `"class"`).

- [ ] **Step 5: First screen.** `python3 harness/screen.py --run` prints `OK session=...`. Look at `--card` for the top three names with the person; any card with a clearly wrong number is a bug to fix before going further.

- [ ] **Step 6: First launch.** `cd bobs/investor && ./launch.sh`, `/login`, ask the Bob to list its loaded rule files (`CORE.md` among them) and to register its loops. Run the nightly-screen loop once by asking for it. Done when at least one thesis passes `thesis_lint` and at least one verdict is in `data/state/calls.jsonl` (spec "done when" 2 and 3).

- [ ] **Step 7: Board and digest.** `python3 harness/scout_board.py`: the page shows the shortlist, theses, the chart with n and the noise banner, and the calls table (done when 4). `python3 harness/digest.py --write` writes `digests/<date>.md` (done when 5).

- [ ] **Step 8: Gates seen live.** In the Bob's session ask it to (a) append a line to `data/state/calls.jsonl` with the shell, (b) fetch `https://api.alpaca.markets/v2/account`, (c) record a verdict (the `refused-is-an-answer` lesson must show). Each of (a) and (b) must be denied with the gate's reason, and (c) must show the lesson. Record each (done when 6).

- [ ] **Step 9: Supervisor on Windows.** Put the session id and name in `supervisor/sessions.json` (`claude agents --json` lists them). Register the task with the command `new_bob.py` printed in Task 1 (`schtasks /Create /TN com.george.bob-investor /SC MINUTE /MO 20 /TR ...`). `python3 ../../harness/watchdog_check.py` must print `WATCHDOG: PASS`, and after the next tick the agent log shows a resume (done when 7; core's first live Windows supervisor check: record the result in core's tracker too).

- [ ] **Step 10: Close out.** Both `selftest.sh` runs pass. Add an entry to `bobs/investor/docs/RELEASE-NOTES.md` ("v1 live: nightly screen, theses, ledger, board, digest; what the person should expect"). Commit. Push only when the person asks.

