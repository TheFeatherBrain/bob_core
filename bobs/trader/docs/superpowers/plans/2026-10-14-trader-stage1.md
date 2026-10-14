# Trader Bob Stage 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build `bobs/trader`, a Bob that acts on Investor Scout v2 calls through a broker-neutral core with a simulator, every guard from the spec, and `mode = "off"` as the committed default.

**Architecture:** A sibling Bob scaffolded by `automation/new_bob.py`. A daily pipeline (`trade.py --run`) collects broker activity into an append-only ledger, reconciles the replayed ledger against the broker, reads the Scout's calls and market conditions as data, lets strategies propose orders, then applies risk flags, sizing and hard caps before submitting (simulate, paper) or queueing for the person (live). Hooks keep the agent away from the ledger, the manifest, broker adapters and the person-only CLIs.

**Tech Stack:** Python 3.11+, standard library only; plain-assert test scripts run through `harness/testkit.py`; Git Bash on Windows.

**Spec:** `bobs/trader/docs/superpowers/specs/2026-10-14-trader-stage1-design.md`

## Global Constraints

- Stdlib only. No new dependencies.
- Every `read_text` / `write_text` / `open` / `subprocess(text=True)` passes `encoding="utf-8"` (`harness/test_utf8_io.py`, Task 1, refuses otherwise).
- Every network read goes through `fetch.read` so it is saved as evidence before use.
- Module shape: docstring with the *why* and a `Usage:` block, `from __future__ import annotations`, `sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))` then `import traderpath  # noqa: E402,F401` (modules in `broker/` and `strategies/` use `.parent.parent`), `main(argv) -> int`, `if __name__ == "__main__":` starting with `oscompat.utf8_stdio()`.
- Output convention: `OK ...` or `FAIL: ...`; exit 1 on failure. Refusals inside a run are ledger lines, not crashes.
- No em-dashes (U+2014) anywhere.
- Missing data is `None` / "could not tell", never a default, a zero or a carried-over value. In sizing, missing or stale market conditions count as bad (the cautious side).
- The Trader never imports from `bobs/investor`; it reads the Scout's files as data. Never edit anything under `bobs/investor`.
- Tests come in refuse/allow pairs. After each new check passes, break the guarded line once with the editor (never `sed -i`), confirm the named test fails, restore it.
- Never overwrite files from the shell. Copying a file to a new path with `cp` is fine; edits use the editor.
- Work on `main`; commit after each task and `git push` after each commit. End every commit message with `Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>`.
- Commands run from `bobs/trader/` unless stated. One suite: `python3 harness/test_<name>.py`. All: `./selftest.sh`.
- `bob.toml` keeps `mode = "off"` in every commit.

## Review Focus

1. A Scout call refused one day (HALT, daily loss limit, stale price) must be proposed again on a later run while the call is open (Task 8 `test_refused_buy_is_proposed_again_later`).
2. A held symbol that splits must not trip its stop: the stop compares split-adjusted closes with the split-adjusted cost (Task 10 end-to-end, BBB splits 2:1 and stays held).
3. A run that dies after the simulator filled but before the ledger recorded it must not lose the fill: the next run collects it by `activity_id` (Task 10 `test_collect_is_idempotent_by_activity_id`).
4. A mismatch must be recorded once, not once per run, while it waits for the person (Task 10 `test_mismatch_stops_trading_and_is_recorded_once`).
5. A buy candidate whose price is stale (a halted stock) must be refused with a reason, not block the sells of the whole run (Task 10 `test_stale_candidate_is_refused_not_fatal`). This narrows spec section 4 step 4, which lists candidates among the run-stopping inputs: only SPY and held symbols stop the run, so exits keep working when one candidate is halted.

---

### Task 1: Scaffold the Bob, config, test kit, shared helpers

**Files:**
- Create (through `new_bob.py`): `bobs/trader/bob.toml`, `CLAUDE.md`, `launch.sh`, `.claude/settings.json`, `docs/TRACKER.md`, `docs/RELEASE-NOTES.md`, `skills/`, `loops/health-checks.txt`, `loops/lessons-review.txt`, `supervisor/`
- Create: `harness/traderpath.py`, `harness/store.py`, `harness/tradercfg.py`, `harness/testkit.py`, `harness/fetch.py` (copy), `harness/marketclock.py` (copy), `harness/broker/__init__.py`, `harness/strategies/__init__.py`, `selftest.sh`
- Test: `harness/test_tradercfg.py`, `harness/test_store.py`, `harness/test_fetch.py` (copy), `harness/test_marketclock.py`, `harness/test_utf8_io.py`
- Modify: `bob_core/fleet.toml` (appended by `new_bob.py`), `bob_core/.claude/CLAUDE.md`

**Interfaces:**
- Produces:
  - `traderpath.HERE`, `traderpath.BOB`, `traderpath.CORE` (`pathlib.Path`)
  - `store.read_json(path, default)`, `store.write_json(path, obj) -> None` (atomic), `store.read_jsonl(path) -> tuple[list[dict], int]` (rows, torn), `store.append_jsonl(path, rows: list[dict]) -> None` (locked)
  - `tradercfg.MODES`, `tradercfg.FLAGS`, `tradercfg.CAPS`, `tradercfg.BAR`, `tradercfg.validate(trader) -> list[str]`, `tradercfg.load() -> dict`, `tradercfg.state_dir()`, `tradercfg.evidence_dir()`, `tradercfg.bob_dir()`, `tradercfg.scout() -> {"state_dir": Path, "buy_percentile": float | None}`, `tradercfg.fingerprint(cfg, keys) -> str`
  - `testkit.MANIFEST`, `testkit.temp_bob(replace: dict[str, str] | None = None) -> Path` (returns `<tmp>/trader`, with a minimal Scout at `<tmp>/investor`), `testkit.scout_state(bob) -> Path`, `testkit.put(path, obj) -> None`, `testkit.run(namespace) -> int`
  - `fetch` and `marketclock`: identical API to the Scout's copies.

- [ ] **Step 1: Move the spec and plan aside so `new_bob.py` gets an empty folder**

From `bob_core/` in Git Bash:

```bash
ASIDE=$(mktemp -d) && echo "$ASIDE" && mv bobs/trader/docs "$ASIDE/" && ls -A bobs/trader
```

Expected: the temp path, then nothing listed.

- [ ] **Step 2: Write the manifest**

Create `$ASIDE/trader.toml`:

```toml
# Trader: acts on Investor Scout calls through a broker-neutral core. mode = "off" until a person
# changes it. Every per-Bob value lives here. Credential tables carry names only, never values.

[bob]
slug = "trader"
name = "Trader"
purpose = "Act on the Investor Scout's buy-worthy calls with fixed sizing, hard caps and exit rules set at entry; simulate first, paper next, live only with a person's approval of each order"
owner = "George"

[board]
port = 8792
title = "Trader"
panels = ["trader_board"]

[loops]
templates = ["health-checks", "lessons-review"]
own = ["loops/daily-trade.txt", "loops/weekly-review.txt"]

[loops.schedules]
"daily-trade" = "30 8 * * 2-6"
"weekly-review" = "0 10 * * 6"
"health-checks" = "19 * * * *"
"lessons-review" = "29 9 * * 1"

[paths]
data_root = "data"
evidence_dir = "evidence"
state_dir = "state"
tracker = "docs/TRACKER.md"
release_notes = "docs/RELEASE-NOTES.md"

[runtime]
launchd_label = "com.george.bob-trader"
agent_log = "C:/Users/georg/AppData/Local/Temp/bob-trader-agent.log"
supervisor_tmp_prefix = "C:/Users/georg/AppData/Local/Temp/bob-trader-supervisor"
sessions_file = "supervisor/sessions.json"
wake_prompt = "supervisor/wake-prompt.md"
claude_bin = "C:/Users/georg/.local/bin/claude.exe"

[credentials]
ALPACA_DATA = { access = "read-only", why = "daily bars and the market calendar; never orders" }
BROKER_PAPER = { access = "read-write", why = "paper orders (adapter not built in stage 1)" }
BROKER_LIVE = { access = "read-write", why = "live orders after the person approves each one (adapter not built in stage 1)" }

[trader]
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
take_profit = 0
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

The `[runtime]` values are the Scout's with `investor` replaced by `trader`; check them against `bobs/investor/bob.toml` before Step 3.

- [ ] **Step 3: Scaffold and put the docs back**

```bash
python3 automation/new_bob.py "$ASIDE/trader.toml" --out bobs/trader
mv "$ASIDE/docs/superpowers" bobs/trader/docs/
ls bobs/trader bobs/trader/docs
```

Expected: `ok: Trader scaffolded at ...`, then `CLAUDE.md bob.toml docs launch.sh loops skills supervisor` and `RELEASE-NOTES.md TRACKER.md superpowers`. Run `grep -n "trader" fleet.toml` and check the new entry (slug, port 8792, absolute path).

- [ ] **Step 4: Path pin and package markers**

Create `bobs/trader/harness/traderpath.py`:

```python
"""Put this Bob's harness and core's harness on sys.path, and pin bobcfg to this Bob's manifest.

Core's bobcfg looks for bob.toml above core's own folder, then above the working directory. This
Bob lives inside core (bobs/trader), so a tool started from the repo root would find no manifest,
and one started from the Scout's folder would find the Scout's: a Trader reading the Scout's
manifest would run with no [trader] section at all. Importing this module first pins BOB_MANIFEST
to this Bob's own, unless it is already set (tests set it to a temp copy).

Usage (first lines of every module in harness/):
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
    import traderpath  # noqa: E402,F401
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

Create `bobs/trader/harness/broker/__init__.py` and `bobs/trader/harness/strategies/__init__.py`, each containing one line:

```python
"""Package marker; see the modules beside it."""
```

- [ ] **Step 5: Copy fetch and marketclock from the Scout**

From `bobs/trader/`:

```bash
cp ../investor/harness/fetch.py harness/fetch.py
cp ../investor/harness/marketclock.py harness/marketclock.py
cp ../investor/harness/test_fetch.py harness/test_fetch.py
```

Then, with the editor, in each of the three copied files replace every `scoutpath` with `traderpath` (fetch.py: one import line; marketclock.py: the import inside `if __name__ == "__main__":`; test_fetch.py: one import line). Add one line to the end of each module docstring (fetch.py, marketclock.py): `Copied from bobs/investor/harness; the Trader never imports from another Bob.`

- [ ] **Step 6: Write the failing tests**

Create `bobs/trader/harness/test_store.py`:

```python
#!/usr/bin/env python3
"""store: JSON files replaced atomically, JSON Lines appended under a lock, torn lines counted.

Run:  python3 harness/test_store.py
"""
from __future__ import annotations

import pathlib
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import traderpath  # noqa: E402,F401
import store  # noqa: E402
import testkit  # noqa: E402


def test_json_round_trip_and_default():
    folder = pathlib.Path(tempfile.mkdtemp())
    store.write_json(folder / "a" / "b.json", {"x": 1})
    assert store.read_json(folder / "a" / "b.json", None) == {"x": 1}
    assert store.read_json(folder / "missing.json", {"d": 1}) == {"d": 1}
    (folder / "torn.json").write_text("{torn", encoding="utf-8")
    assert store.read_json(folder / "torn.json", []) == []


def test_jsonl_appends_and_counts_torn_lines():
    path = pathlib.Path(tempfile.mkdtemp()) / "l.jsonl"
    assert store.read_jsonl(path) == ([], 0)
    store.append_jsonl(path, [{"a": 1}, {"a": 2}])
    with path.open("a", encoding="utf-8") as fh:
        fh.write('{"a": \n')
    store.append_jsonl(path, [{"a": 3}])
    assert store.read_jsonl(path) == ([{"a": 1}, {"a": 2}, {"a": 3}], 1)


if __name__ == "__main__":
    raise SystemExit(testkit.run(globals()))
```

Create `bobs/trader/harness/test_tradercfg.py`:

```python
#!/usr/bin/env python3
"""[trader] must be checked before any module trusts it: a typo in a cap changes every order.

Run:  python3 harness/test_tradercfg.py
"""
from __future__ import annotations

import pathlib
import sys
import tomllib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import traderpath  # noqa: E402,F401
import bobcfg  # noqa: E402
import testkit  # noqa: E402
import tradercfg  # noqa: E402


def _trader() -> dict:
    return dict(tomllib.loads(testkit.MANIFEST.read_text(encoding="utf-8"))["trader"])


def test_real_manifest_passes_core_and_trader_checks():
    cfg = tomllib.loads(testkit.MANIFEST.read_text(encoding="utf-8"))
    assert bobcfg.validate(cfg) == [], bobcfg.validate(cfg)
    assert tradercfg.validate(cfg["trader"]) == [], tradercfg.validate(cfg["trader"])


def test_committed_mode_is_off():
    assert _trader()["mode"] == "off"


def test_unknown_mode_refused():
    t = _trader()
    t["mode"] = "yolo"
    assert any("trader.mode" in e for e in tradercfg.validate(t))


def test_caps_that_cannot_all_hold_refused():
    t = _trader()
    t["base_weight"] = 0.2
    assert any("base_weight" in e for e in tradercfg.validate(t))
    t = _trader()
    t["min_cash"] = 0.3
    assert any("max_invested + min_cash" in e for e in tradercfg.validate(t))
    t = _trader()
    t["min_cash"] = 0.2
    assert tradercfg.validate(t) == []


def test_fractions_counts_and_flags_checked():
    for key, bad in (("max_position", 0), ("max_position", 1.5), ("max_orders_per_day", 0),
                     ("stop_loss", None), ("block_flags", ["moon"]), ("budgets", {"scout_calls": 0.6, "x": 0.6})):
        t = _trader()
        t[key] = bad
        assert any(f"trader.{key}" in e for e in tradercfg.validate(t)), (key, bad)


def test_scout_paths_come_from_the_scout_manifest():
    bob = testkit.temp_bob()
    scout = tradercfg.scout()
    assert scout["state_dir"] == testkit.scout_state(bob).resolve() and scout["buy_percentile"] == 80


def test_missing_scout_manifest_is_a_manifest_error():
    bob = testkit.temp_bob()
    (bob.parent / "investor" / "bob.toml").unlink()
    try:
        tradercfg.scout()
    except bobcfg.ManifestError as e:
        assert "scout_manifest" in e.errors[0]
    else:
        raise AssertionError("not refused")


def test_fingerprint_changes_with_its_keys_only():
    cfg = _trader()
    base = tradercfg.fingerprint(cfg, tradercfg.BAR)
    assert tradercfg.fingerprint({**cfg, "max_position": 0.09}, tradercfg.BAR) == base
    assert tradercfg.fingerprint({**cfg, "min_paper_days": 200}, tradercfg.BAR) != base


if __name__ == "__main__":
    raise SystemExit(testkit.run(globals()))
```

Create `bobs/trader/harness/test_marketclock.py`:

```python
#!/usr/bin/env python3
"""marketclock (copied from the Scout): DST edges, the last session waits for close plus an hour.

Run:  python3 harness/test_marketclock.py
"""
from __future__ import annotations

import datetime as dt
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import traderpath  # noqa: E402,F401
import marketclock  # noqa: E402
import testkit  # noqa: E402


def _utc(*args) -> dt.datetime:
    return dt.datetime(*args, tzinfo=dt.timezone.utc)


def test_et_offset_dst_edges():
    hours = lambda t: marketclock.et_offset(t).total_seconds() / 3600  # noqa: E731
    assert hours(_utc(2026, 3, 8, 6, 59)) == -5 and hours(_utc(2026, 3, 8, 7, 0)) == -4
    assert hours(_utc(2026, 11, 1, 5, 59)) == -4 and hours(_utc(2026, 11, 1, 6, 0)) == -5


def test_last_session_waits_for_close_plus_an_hour():
    sessions = [{"date": "2026-10-09", "close": "16:00"}, {"date": "2026-10-12", "close": "16:00"}]
    assert marketclock.last_session(sessions, _utc(2026, 10, 12, 21, 0)) == "2026-10-12"
    assert marketclock.last_session(sessions, _utc(2026, 10, 12, 20, 30)) == "2026-10-09"


if __name__ == "__main__":
    raise SystemExit(testkit.run(globals()))
```

Create `bobs/trader/harness/test_utf8_io.py`:

```python
#!/usr/bin/env python3
"""The Bob holds itself to core's encoding rule: every text read, write and pipe says UTF-8.

Run:  python3 harness/test_utf8_io.py
"""
from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import traderpath  # noqa: E402,F401
import test_text_encoding  # noqa: E402
import testkit  # noqa: E402


def test_bob_has_no_bare_text_io():
    harness = traderpath.HERE
    files = test_text_encoding.modules(harness, harness / "hooks", harness / "broker", harness / "strategies")
    found = test_text_encoding.findings(files, traderpath.BOB)
    assert found == [], "\n".join(found)


if __name__ == "__main__":
    raise SystemExit(testkit.run(globals()))
```

- [ ] **Step 7: Run the tests to verify they fail**

Run: `python3 harness/test_store.py`
Expected: `ModuleNotFoundError: No module named 'testkit'` (or `store`).

- [ ] **Step 8: Write the implementation**

Create `bobs/trader/harness/store.py`:

```python
#!/usr/bin/env python3
"""Small file helpers every Trader module shares: JSON replaced atomically, JSON Lines appended.

Why atomic JSON: a crash mid-write must leave the old copy, not half a file the next run cannot
read. Why a lock on appends: the daily run and a person running approve.py can write the ledger at
the same moment. Why torn lines are counted, not fatal: a ledger must stay readable after a crash,
and the count makes the damage visible (reconcile then refuses to trade).

Usage (library):
    rows, torn = store.read_jsonl(path)
    store.append_jsonl(path, [row])
"""
from __future__ import annotations

import json
import os
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import traderpath  # noqa: E402,F401
import oscompat  # noqa: E402


def read_json(path: pathlib.Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def write_json(path: pathlib.Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, default=str, sort_keys=True), encoding="utf-8")
    os.replace(tmp, path)


def read_jsonl(path: pathlib.Path) -> tuple[list[dict], int]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
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


def append_jsonl(path: pathlib.Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        oscompat.lock(fh)
        try:
            fh.write("".join(json.dumps(r, sort_keys=True, default=str) + "\n" for r in rows))
            fh.flush()
        finally:
            oscompat.unlock(fh)
```

Create `bobs/trader/harness/tradercfg.py`:

```python
#!/usr/bin/env python3
"""Read and check this Bob's [trader] section: mode, caps, costs, exit rules and the go-live bar.

Core's bobcfg validates the sections every Bob has and ignores [trader]. Without this check a typo
in a cap would silently change every order, and caps that cannot all hold (a base weight above the
position cap) would refuse every buy with a reason nobody expects.

Why the Scout's paths come from the Scout's manifest: the Trader reads the Scout's ledger as data,
and a hard-coded path would go quietly blind the day the Scout's data root moves.

Usage:
    python3 harness/tradercfg.py      # OK, or FAIL: <error> per problem in [trader]
"""
from __future__ import annotations

import json
import pathlib
import sys
import tomllib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import traderpath  # noqa: E402,F401
import bobcfg  # noqa: E402

MODES = ("off", "simulate", "paper", "live")
FLAGS = ("vix", "insider", "liquidity")
FRACTIONS = ("base_weight", "max_position", "max_invested", "min_cash", "daily_loss_limit",
             "max_adv_share", "stop_loss", "throttle_floor")
NON_NEGATIVE = ("commission_per_order", "slippage_bps", "take_profit", "min_order_value", "vix_flag",
                "hy_spread_flag")
COUNTS = ("max_orders_per_day", "max_hold_days", "min_paper_days", "min_closed_trades")
CAPS = ("base_weight", "max_position", "max_invested", "min_cash", "daily_loss_limit", "max_orders_per_day",
        "min_order_value", "max_adv_share", "block_flags", "budgets", "stop_loss", "take_profit",
        "max_hold_days", "vix_flag", "hy_spread_flag", "throttle_floor")
BAR = ("min_paper_days", "min_closed_trades", "commission_per_order", "slippage_bps")


def _num(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def validate(trader) -> list[str]:
    if not isinstance(trader, dict):
        return ["trader: required table"]
    errors: list[str] = []
    if trader.get("mode") not in MODES:
        errors.append(f"trader.mode: one of {list(MODES)}")
    if not (isinstance(trader.get("scout_manifest"), str) and trader["scout_manifest"]):
        errors.append("trader.scout_manifest: path to the Scout's bob.toml, relative to this Bob")
    if not (_num(trader.get("sim_starting_cash")) and trader["sim_starting_cash"] > 0):
        errors.append("trader.sim_starting_cash: a number above 0")
    errors += [f"trader.{k}: a number above 0 and at most 1" for k in FRACTIONS
               if not (_num(trader.get(k)) and 0 < trader[k] <= 1)]
    errors += [f"trader.{k}: a number of at least 0" for k in NON_NEGATIVE
               if not (_num(trader.get(k)) and trader[k] >= 0)]
    errors += [f"trader.{k}: an integer of at least 1" for k in COUNTS
               if not (isinstance(trader.get(k), int) and not isinstance(trader[k], bool) and trader[k] >= 1)]
    if not errors and trader["base_weight"] > trader["max_position"]:
        errors.append("trader.base_weight: at most max_position, or every buy is refused")
    if not errors and trader["max_invested"] + trader["min_cash"] > 1 + 1e-9:
        errors.append("trader.max_invested + min_cash: at most 1")
    flags = trader.get("block_flags")
    if not (isinstance(flags, list) and set(flags) <= set(FLAGS)):
        errors.append(f"trader.block_flags: a list drawn from {list(FLAGS)}")
    if not isinstance(trader.get("override_promotion"), str):
        errors.append('trader.override_promotion: a string ("" for none)')
    budgets = trader.get("budgets")
    if not (isinstance(budgets, dict) and budgets and all(_num(v) and 0 < v <= 1 for v in budgets.values())):
        errors.append("trader.budgets: one or more strategies, each a share above 0 and at most 1")
    elif sum(budgets.values()) > 1 + 1e-9:
        errors.append("trader.budgets: shares summing to at most 1")
    return errors


def load() -> dict:
    trader = bobcfg.config().get("trader")
    errors = validate(trader)
    if errors:
        raise bobcfg.ManifestError(errors)
    return trader


def state_dir() -> pathlib.Path:
    return bobcfg.path(bobcfg.config(), "state_dir")


def evidence_dir() -> pathlib.Path:
    return bobcfg.path(bobcfg.config(), "evidence_dir")


def bob_dir() -> pathlib.Path:
    return bobcfg.bob_root()


def scout() -> dict:
    manifest = (bob_dir() / load()["scout_manifest"]).resolve()
    try:
        cfg = tomllib.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as e:
        raise bobcfg.ManifestError([f"trader.scout_manifest: cannot read {manifest} ({type(e).__name__})"]) from None
    paths = cfg.get("paths", {})
    root = manifest.parent / paths.get("data_root", "")
    return {"state_dir": (root / paths.get("state_dir", "state")).resolve(),
            "buy_percentile": cfg.get("scout", {}).get("buy_percentile")}


def fingerprint(cfg: dict, keys) -> str:
    return json.dumps({k: cfg.get(k) for k in keys}, sort_keys=True)


def main(argv: list[str]) -> int:
    try:
        load()
        scout()
    except bobcfg.ManifestError as e:
        for err in e.errors:
            print(f"FAIL: {err}")
        return 1
    print("OK")
    return 0


if __name__ == "__main__":
    import oscompat
    oscompat.utf8_stdio()
    raise SystemExit(main(sys.argv[1:]))
```

Create `bobs/trader/harness/testkit.py`:

```python
"""Shared test helpers: a throwaway Trader beside a minimal Scout, and core's plain-assert runner.

Every test runs against a temp copy of this Bob's real manifest, placed at <tmp>/trader with a
minimal Scout manifest at <tmp>/investor, so scout_manifest = "../investor/bob.toml" resolves the
same way it does in the repo and no test can write into either real data folder.
"""
from __future__ import annotations

import json
import os
import pathlib
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import traderpath  # noqa: E402,F401
import bobcfg  # noqa: E402

MANIFEST = traderpath.BOB / "bob.toml"
SCOUT = '[paths]\ndata_root = "data"\nstate_dir = "state"\n\n[scout]\nbuy_percentile = 80\n'


def temp_bob(replace: dict[str, str] | None = None) -> pathlib.Path:
    root = pathlib.Path(tempfile.mkdtemp()).resolve()
    bob, scout = root / "trader", root / "investor"
    bob.mkdir()
    scout.mkdir()
    text = MANIFEST.read_text(encoding="utf-8")
    for old, new in (replace or {}).items():
        assert old in text, f"{old!r} is not in the manifest"
        text = text.replace(old, new)
    (bob / "bob.toml").write_text(text, encoding="utf-8")
    (scout / "bob.toml").write_text(SCOUT, encoding="utf-8")
    os.environ["BOB_MANIFEST"] = str(bob / "bob.toml")
    os.environ["CLAUDE_CONFIG_DIR"] = str(bob / "config")
    bobcfg.config.cache_clear()
    return bob


def scout_state(bob: pathlib.Path) -> pathlib.Path:
    return bob.parent / "investor" / "data" / "state"


def put(path: pathlib.Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(obj if isinstance(obj, str) else json.dumps(obj), encoding="utf-8")


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

Create `bobs/trader/selftest.sh`:

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
for f in harness/*.py harness/hooks/*.py harness/broker/*.py harness/strategies/*.py; do
  case "$f" in */test_*) continue ;; esac
  [ -e "$f" ] && grep -qE "add_argument\(['\"]--selftest['\"]" "$f" && run python3 "$f" --selftest
done
rm -rf "$CLAUDE_CONFIG_DIR"
exit $failed
```

Run `chmod +x selftest.sh`.

In `bob_core/.claude/CLAUDE.md`, change the `bobs/` line in `## Project Structure` to:

```
bobs/              Bobs kept inside this repo for now (bobs/investor, bobs/trader); each has its own selftest.sh. Core never imports from bobs/, and a Bob never imports from another Bob
```

- [ ] **Step 9: Run the tests to verify they pass**

Run: `./selftest.sh`
Expected: `ok` for `test_fetch.py`, `test_marketclock.py`, `test_store.py`, `test_tradercfg.py`, `test_utf8_io.py`. Then `python3 harness/tradercfg.py`: `OK`.

- [ ] **Step 10: Break it once, then restore**

In `tradercfg.validate`, delete the `base_weight > max_position` check: `test_caps_that_cannot_all_hold_refused` must FAIL. Restore. In `store.read_jsonl`, change `torn += 1` to `pass`: `test_jsonl_appends_and_counts_torn_lines` must FAIL. Restore. Re-run `./selftest.sh`: all `ok`.

- [ ] **Step 11: Commit**

From `bob_core/`:

```bash
git add fleet.toml .claude/CLAUDE.md bobs/trader
git commit -m "Trader: scaffold the Bob, [trader] config, test kit, shared helpers

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
git push
```

---

### Task 2: The ledger (`ledger.py`)

**Files:**
- Create: `bobs/trader/harness/ledger.py`
- Test: `bobs/trader/harness/test_ledger.py`

**Interfaces:**
- Consumes: `store.read_jsonl`, `store.append_jsonl`, `tradercfg.state_dir()`.
- Produces:
  - `ledger.KINDS`, `ledger.EPS = 1e-6`
  - `ledger.Lot` (dataclass: `symbol: str, qty: float, price: float, opened: str, order_id: str, strategy: str | None, exit_rules: dict, call_id: str | None`)
  - `ledger.Book` (dataclass: `cash: float, lots: list[Lot], closed: list[dict]`; methods `positions() -> dict[str, float]`, `lots_of(symbol) -> list[Lot]`)
  - `ledger.line(kind: str, now: dt.datetime, session: str | None, **fields) -> dict` (adds `id`, `kind`, `at`, `session`)
  - `ledger.fill_amount(side: str, qty: float, price: float, commission: float) -> float` (signed cash change, rounded to cents; the simulator uses the same function)
  - `ledger.path()`, `ledger.read(path=None) -> tuple[list[dict], int]`, `ledger.append(rows, path=None)`
  - `ledger.replay(rows) -> Book`
  - `ledger.unresolved_mismatches(rows) -> list[dict]`
  - `ledger.Refused` (exception), `ledger.confirm(expected: str, stdin=sys.stdin, ask=input) -> None` (raises `Refused` unless stdin is a terminal and the typed text equals `expected`)
  - `ledger.correct(note, now, clears=None, cash_delta=0.0, share_deltas=None, stdin=sys.stdin, ask=input) -> dict`
  - CLI: `book`, `summary --days N`, `correct --note ... [--clears ID] [--cash-delta X] [--shares SYM=QTY@PRICE ...]`

- [ ] **Step 1: Write the failing tests**

Create `bobs/trader/harness/test_ledger.py`:

```python
#!/usr/bin/env python3
"""ledger: replay rebuilds lots oldest-first, splits and dividends apply, corrections are person-only.

Run:  python3 harness/test_ledger.py
"""
from __future__ import annotations

import datetime as dt
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import traderpath  # noqa: E402,F401
import ledger  # noqa: E402
import testkit  # noqa: E402

NOW = dt.datetime(2026, 10, 14, 6, 30, tzinfo=dt.timezone.utc)


class Tty:
    def __init__(self, tty: bool):
        self.tty = tty

    def isatty(self) -> bool:
        return self.tty


def _buy(order_id: str, symbol: str, qty: float, price: float, session: str) -> list[dict]:
    return [ledger.line("proposed", NOW, session, order_id=order_id, strategy="scout_calls", symbol=symbol,
                        side="buy", call_id=f"{symbol}-call", exit_rules={"stop_loss": 0.2}),
            ledger.line("filled", NOW, session, order_id=order_id, symbol=symbol, side="buy", qty=qty,
                        price=price, commission=1.0)]


def _sell(order_id: str, symbol: str, qty: float, price: float, session: str) -> dict:
    return ledger.line("filled", NOW, session, order_id=order_id, symbol=symbol, side="sell", qty=qty,
                       price=price, commission=1.0)


def test_append_and_read_round_trip_with_torn_line_counted():
    testkit.temp_bob()
    ledger.append([ledger.line("cash", NOW, "2026-10-13", amount=100.0)])
    with ledger.path().open("a", encoding="utf-8") as fh:
        fh.write("{torn\n")
    rows, torn = ledger.read()
    assert torn == 1 and rows[0]["kind"] == "cash" and rows[0]["id"].startswith("cash-2026-10-13-")


def test_sells_consume_the_oldest_lot_first():
    rows = ([ledger.line("cash", NOW, "2026-01-01", amount=10000.0)]
            + _buy("b1", "AAA", 10, 100.0, "2026-01-02") + _buy("b2", "AAA", 10, 110.0, "2026-01-05")
            + [_sell("s1", "AAA", 15, 120.0, "2026-01-09")])
    book = ledger.replay(rows)
    assert book.positions() == {"AAA": 5.0} and book.lots_of("AAA")[0].price == 110.0
    assert [(c["qty"], c["buy_price"], c["full"]) for c in book.closed] == [(10, 100.0, True), (5, 110.0, False)]
    assert abs(book.cash - (10000 - 1001 - 1101 + 1799)) < 1e-9
    assert book.lots_of("AAA")[0].strategy == "scout_calls" and book.lots_of("AAA")[0].call_id == "AAA-call"


def test_split_multiplies_shares_and_divides_cost():
    rows = _buy("b1", "BBB", 10, 100.0, "2026-01-02") + [ledger.line("split", NOW, "2026-02-02", symbol="BBB", ratio=2.0)]
    lot = ledger.replay(rows).lots_of("BBB")[0]
    assert (lot.qty, lot.price) == (20.0, 50.0)


def test_dividend_and_cash_lines_add_cash():
    rows = [ledger.line("cash", NOW, "s", amount=500.0), ledger.line("dividend", NOW, "s", symbol="X", amount=12.34)]
    assert ledger.replay(rows).cash == 512.34


def test_fill_amount_is_rounded_to_cents_and_signed():
    assert ledger.fill_amount("buy", 3, 33.3333, 1.0) == -101.0
    assert ledger.fill_amount("sell", 3, 33.3333, 1.0) == 99.0


def test_correction_adjusts_and_clears_a_mismatch():
    mismatch = ledger.line("mismatch", NOW, "s", details=["cash off"])
    fix = ledger.line("correction", NOW, "s", clears=mismatch["id"], cash_delta=-5.0,
                      share_deltas={"ZZZ": [3, 10.0]}, note="missed fill")
    assert ledger.unresolved_mismatches([mismatch]) == [mismatch]
    assert ledger.unresolved_mismatches([mismatch, fix]) == []
    book = ledger.replay([ledger.line("cash", NOW, "s", amount=100.0), fix])
    assert book.cash == 95.0 and book.positions() == {"ZZZ": 3.0} and book.lots_of("ZZZ")[0].strategy is None


def test_correct_refuses_without_a_terminal():
    testkit.temp_bob()
    try:
        ledger.correct("x", NOW, cash_delta=1.0, stdin=Tty(False), ask=lambda p: "CORRECT")
    except ledger.Refused as e:
        assert "terminal" in str(e)
    else:
        raise AssertionError("not refused")
    assert ledger.read() == ([], 0)


def test_correct_needs_the_typed_word_and_then_appends():
    testkit.temp_bob()
    try:
        ledger.correct("x", NOW, cash_delta=1.0, stdin=Tty(True), ask=lambda p: "yes")
    except ledger.Refused:
        pass
    else:
        raise AssertionError("not refused")
    row = ledger.correct("x", NOW, cash_delta=1.0, stdin=Tty(True), ask=lambda p: "CORRECT")
    assert ledger.read()[0] == [row] and row["kind"] == "correction" and row["cash_delta"] == 1.0


if __name__ == "__main__":
    raise SystemExit(testkit.run(globals()))
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 harness/test_ledger.py`
Expected: `ModuleNotFoundError: No module named 'ledger'`.

- [ ] **Step 3: Write the implementation**

Create `bobs/trader/harness/ledger.py`:

```python
#!/usr/bin/env python3
"""The Trader's ledger: every proposal, refusal, order, fill, dividend, split, cash movement,
mismatch and correction, append-only. Lots, positions, cash and realized gains are rebuilt by
replaying it, never stored anywhere else.

Why replay: one source of truth means one thing to check against the broker. A positions file that
drifted from the order history would make reconcile compare two copies of the same mistake.
Why append-only: a duplicate is recoverable, an overwrite is not. A torn line is skipped and
counted; the replay then disagrees with the broker and reconcile stops trading, so nothing is lost
quietly.
Why oldest lot first: one fixed rule, so realized gains are the same however often they are
recomputed.
Why corrections are the person's: the agent may never make its own books agree with the broker.
correct needs a terminal and a typed confirmation, and order_gate refuses it to the agent.

Usage:
    python3 harness/ledger.py book                       # cash, positions and open lots
    python3 harness/ledger.py summary --days 7           # fills and refusals in the last N days
    python3 harness/ledger.py correct --note "..." [--clears MISMATCH_ID] [--cash-delta X] [--shares SYM=QTY@PRICE ...]
"""
from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
import pathlib
import sys
import uuid

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import traderpath  # noqa: E402,F401
import store  # noqa: E402
import tradercfg  # noqa: E402

KINDS = ("proposed", "refused", "queued", "approved", "submitted", "filled", "expired", "dividend", "split",
         "cash", "mismatch", "correction")
EPS = 1e-6
CONFIRM_WORD = "CORRECT"


class Refused(Exception):
    pass


@dataclasses.dataclass
class Lot:
    symbol: str
    qty: float
    price: float
    opened: str
    order_id: str
    strategy: str | None
    exit_rules: dict
    call_id: str | None


@dataclasses.dataclass
class Book:
    cash: float = 0.0
    lots: list[Lot] = dataclasses.field(default_factory=list)
    closed: list[dict] = dataclasses.field(default_factory=list)

    def positions(self) -> dict[str, float]:
        out: dict[str, float] = {}
        for lot in self.lots:
            out[lot.symbol] = round(out.get(lot.symbol, 0.0) + lot.qty, 6)
        return {s: q for s, q in out.items() if q > EPS}

    def lots_of(self, symbol: str) -> list[Lot]:
        return [lot for lot in self.lots if lot.symbol == symbol]


def line(kind: str, now: dt.datetime, session: str | None, **fields) -> dict:
    return {"id": f"{kind}-{session or 'none'}-{uuid.uuid4().hex[:8]}", "kind": kind,
            "at": now.isoformat(timespec="seconds"), "session": session, **fields}


def fill_amount(side: str, qty: float, price: float, commission: float) -> float:
    gross = round(qty * price, 2)
    return round(-(gross + commission), 2) if side == "buy" else round(gross - commission, 2)


def path() -> pathlib.Path:
    return tradercfg.state_dir() / "ledger.jsonl"


def read(where: pathlib.Path | None = None) -> tuple[list[dict], int]:
    return store.read_jsonl(where or path())


def append(rows: list[dict], where: pathlib.Path | None = None) -> None:
    store.append_jsonl(where or path(), rows)


def _consume(book: Book, symbol: str, qty: float, price: float, session: str) -> None:
    left = qty
    for lot in book.lots_of(symbol):
        if left <= EPS:
            break
        take = min(lot.qty, left)
        lot.qty = round(lot.qty - take, 6)
        left = round(left - take, 6)
        book.closed.append({"symbol": symbol, "qty": take, "buy_price": lot.price, "sell_price": price,
                            "opened": lot.opened, "closed": session, "pnl": round(take * (price - lot.price), 2),
                            "strategy": lot.strategy, "order_id": lot.order_id, "full": lot.qty <= EPS})
    book.lots = [lot for lot in book.lots if lot.qty > EPS]


def replay(rows: list[dict]) -> Book:
    book = Book()
    proposals = {r["order_id"]: r for r in rows if r.get("kind") == "proposed"}
    for r in rows:
        kind = r.get("kind")
        if kind in ("cash", "dividend"):
            book.cash = round(book.cash + r["amount"], 2)
        elif kind == "filled":
            book.cash = round(book.cash + fill_amount(r["side"], r["qty"], r["price"], r["commission"]), 2)
            if r["side"] == "buy":
                origin = proposals.get(r["order_id"], {})
                book.lots.append(Lot(r["symbol"], float(r["qty"]), float(r["price"]), r["session"], r["order_id"],
                                     origin.get("strategy"), origin.get("exit_rules") or {}, origin.get("call_id")))
            else:
                _consume(book, r["symbol"], float(r["qty"]), float(r["price"]), r["session"])
        elif kind == "split":
            for lot in book.lots_of(r["symbol"]):
                lot.qty = round(lot.qty * r["ratio"], 6)
                lot.price = lot.price / r["ratio"]
        elif kind == "correction":
            book.cash = round(book.cash + r.get("cash_delta", 0.0), 2)
            for symbol, (qty, price) in (r.get("share_deltas") or {}).items():
                if qty > 0:
                    book.lots.append(Lot(symbol, float(qty), float(price), r["session"] or "", r["id"], None, {}, None))
                else:
                    _consume(book, symbol, -float(qty), float(price), r["session"] or "")
    return book


def unresolved_mismatches(rows: list[dict]) -> list[dict]:
    cleared = {r.get("clears") for r in rows if r.get("kind") == "correction"}
    return [r for r in rows if r.get("kind") == "mismatch" and r["id"] not in cleared]


def confirm(expected: str, stdin=sys.stdin, ask=input) -> None:
    if not stdin.isatty():
        raise Refused("needs a person at a terminal; the agent may not run this (order_gate refuses it too)")
    if ask(f"Type {expected} to confirm: ").strip() != expected:
        raise Refused("confirmation did not match; nothing written")


def correct(note: str, now: dt.datetime, clears: str | None = None, cash_delta: float = 0.0,
            share_deltas: dict | None = None, stdin=sys.stdin, ask=input) -> dict:
    confirm(clears or CONFIRM_WORD, stdin, ask)
    row = line("correction", now, now.date().isoformat(), note=note, clears=clears, cash_delta=cash_delta,
               share_deltas=share_deltas or {})
    append([row])
    return row


def summary(rows: list[dict], since: str) -> list[str]:
    out = []
    for r in rows:
        if (r.get("session") or "") < since:
            continue
        if r.get("kind") == "filled":
            out.append(f"{r['session']} filled {r['side']} {r['qty']:g} {r['symbol']} @ {r['price']:.2f}")
        elif r.get("kind") == "refused":
            out.append(f"{r['session']} refused {r['order_id']}: {r['reason']}")
        elif r.get("kind") in ("mismatch", "correction", "expired"):
            out.append(f"{r['session']} {r['kind']} {r['id']}")
    return out or ["nothing in this window"]


def _shares(items: list[str]) -> dict:
    out = {}
    for item in items:
        symbol, _, rest = item.partition("=")
        qty, _, price = rest.partition("@")
        out[symbol.upper()] = [float(qty), float(price)]
    return out


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="command", required=True)
    sub.add_parser("book")
    s = sub.add_parser("summary")
    s.add_argument("--days", type=int, default=7)
    c = sub.add_parser("correct")
    c.add_argument("--note", required=True)
    c.add_argument("--clears")
    c.add_argument("--cash-delta", type=float, default=0.0)
    c.add_argument("--shares", nargs="*", default=[])
    a = ap.parse_args(argv)
    now = dt.datetime.now(dt.timezone.utc)
    if a.command == "correct":
        try:
            print(f"OK {correct(a.note, now, a.clears, a.cash_delta, _shares(a.shares))['id']}")
        except Refused as e:
            print(f"REFUSED: {e}")
            return 1
        return 0
    rows, torn = read()
    if a.command == "summary":
        print("\n".join(summary(rows, (now.date() - dt.timedelta(days=a.days)).isoformat())))
    else:
        book = replay(rows)
        print(f"cash {book.cash:.2f}")
        for lot in book.lots:
            print(f"{lot.symbol:8} {lot.qty:g} @ {lot.price:.4f} opened {lot.opened} ({lot.strategy}, {lot.order_id})")
    if torn:
        print(f"WARN: {torn} torn line(s) skipped in {path()}")
    return 0


if __name__ == "__main__":
    import oscompat
    oscompat.utf8_stdio()
    raise SystemExit(main(sys.argv[1:]))
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 harness/test_ledger.py`
Expected: `8 of 8 passed`.

- [ ] **Step 5: Break it once, then restore**

In `_consume`, iterate `reversed(book.lots_of(symbol))`: `test_sells_consume_the_oldest_lot_first` must FAIL. Restore. In `confirm`, delete the `isatty` check: `test_correct_refuses_without_a_terminal` must FAIL. Restore. Re-run: `8 of 8 passed`.

- [ ] **Step 6: Commit**

```bash
git add harness/ledger.py harness/test_ledger.py
git commit -m "Trader: append-only ledger replayed into lots, oldest first

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
git push
```

---

### Task 3: Daily bars (`bars.py`)

**Files:**
- Create: `bobs/trader/harness/bars.py`
- Test: `bobs/trader/harness/test_bars.py`

**Interfaces:**
- Consumes: `fetch.read`, `fetch.get`, `fetch.FetchError`, `marketclock.to_et`, `store.read_json`, `store.write_json`, `tradercfg.state_dir()`.
- Produces:
  - `bars.BENCHMARK = "SPY"`, `bars.ADV_DAYS = 20`
  - `bars.credentials(env=os.environ) -> dict` (raises `fetch.FetchError` when keys are missing)
  - `bars.Client(headers, *, getter=fetch.get)` with `.bars(symbols, start, end, adjustment) -> tuple[dict[str, list[list]], list[Path]]` and `.calendar(start, end) -> tuple[list[dict], Path]`
  - storage per symbol `state/bars/<SYMBOL>.json`: `{"raw": [[date, open, high, low, close, volume], ...], "split": [[date, close], ...], "all": [[date, close], ...] (SPY only), "evidence": [...]}`; calendar `state/bars/_calendar.json`: `{"sessions": [{"date", "close"}], ...}`
  - `bars.load(symbol, folder=None) -> dict`, `bars.sessions(folder=None) -> list[dict]`
  - `bars.fresh(symbol, session) -> bool`, `bars.close_on(symbol, session, key="raw") -> float | None`, `bars.adv(symbol) -> float | None`
  - `bars.refresh(client, symbols, today, folder) -> dict` (`{"symbols": n, "failed": [...]}`)
  - CLI `--refresh`

- [ ] **Step 1: Write the failing tests**

Create `bobs/trader/harness/test_bars.py`:

```python
#!/usr/bin/env python3
"""bars: raw OHLCV for fills, split-adjusted closes for stops, dividend-adjusted SPY for the bar.

Run:  python3 harness/test_bars.py
"""
from __future__ import annotations

import datetime as dt
import json
import pathlib
import sys
import urllib.parse

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import traderpath  # noqa: E402,F401
import bars  # noqa: E402
import fetch  # noqa: E402
import testkit  # noqa: E402

TODAY = dt.date(2026, 10, 14)


def _days(n: int) -> list[str]:
    day, out = TODAY, []
    while len(out) < n:
        day -= dt.timedelta(days=1)
        if day.weekday() < 5:
            out.append(day.isoformat())
    return out[::-1]


class Market:
    """A fake Alpaca data host: closes per symbol and adjustment, and a log of what was asked."""

    def __init__(self, closes: dict[str, dict[str, dict[str, float]]]):
        self.closes, self.asked = closes, []

    def get(self, url: str, headers: dict) -> str:
        q = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query))
        if "calendar" in url:
            return json.dumps([{"date": d, "open": "09:30", "close": "16:00"} for d in _days(30)])
        self.asked.append((q["adjustment"], q["start"], q["symbols"]))
        out = {}
        for symbol in q["symbols"].split(","):
            series = self.closes.get(symbol, {}).get(q["adjustment"], {})
            out[symbol] = [{"t": f"{d}T04:00:00Z", "o": c, "h": c, "l": c, "c": c, "v": 1000}
                           for d, c in sorted(series.items()) if q["start"] <= d <= q["end"]]
        return json.dumps({"bars": out, "next_page_token": None})


def _flat(price: float, n: int = 300) -> dict[str, float]:
    return {d: price for d in _days(n)}


def test_only_the_data_host_and_calendar_are_read():
    testkit.temp_bob()
    client = bars.Client({}, getter=Market({}).get)
    try:
        client._read("x", "https://paper-api.alpaca.markets/v2/orders")
    except fetch.FetchError as e:
        assert "refused" in str(e)
    else:
        raise AssertionError("not refused")


def test_missing_keys_refused():
    try:
        bars.credentials({})
    except fetch.FetchError as e:
        assert "ALPACA_PAPER_KEY_ID" in str(e)
    else:
        raise AssertionError("not refused")


def test_refresh_stores_raw_split_and_spy_all():
    bob = testkit.temp_bob()
    market = Market({"AAA": {"raw": _flat(100), "split": _flat(50)},
                     "SPY": {"raw": _flat(400), "split": _flat(400), "all": _flat(390)}})
    folder = bob / "data" / "state" / "bars"
    summary = bars.refresh(bars.Client({}, getter=market.get), ["AAA", "SPY"], TODAY, folder)
    assert summary == {"symbols": 2, "failed": []}
    aaa = bars.load("AAA", folder)
    assert aaa["raw"][-1] == [_days(1)[0], 100.0, 100.0, 100.0, 100.0, 1000] and aaa["split"][-1][1] == 50.0
    assert aaa["all"] == [] and bars.load("SPY", folder)["all"][-1][1] == 390.0
    assert aaa["evidence"] and pathlib.Path(aaa["evidence"][0]).exists()


def test_second_refresh_reads_only_the_recent_window():
    bob = testkit.temp_bob()
    market = Market({"AAA": {"raw": _flat(100), "split": _flat(100)}})
    folder = bob / "data" / "state" / "bars"
    client = bars.Client({}, getter=market.get)
    bars.refresh(client, ["AAA"], TODAY, folder)
    market.asked.clear()
    bars.refresh(client, ["AAA"], TODAY, folder)
    starts = {start for _, start, _ in market.asked}
    assert starts == {(TODAY - dt.timedelta(days=bars.RECENT_DAYS)).isoformat()}, market.asked


def test_a_split_re_adjusts_history_and_triggers_a_full_split_refetch():
    bob = testkit.temp_bob()
    market = Market({"BBB": {"raw": _flat(100), "split": _flat(100)}})
    folder = bob / "data" / "state" / "bars"
    client = bars.Client({}, getter=market.get)
    bars.refresh(client, ["BBB"], TODAY, folder)
    market.closes["BBB"]["split"] = _flat(50)
    market.asked.clear()
    bars.refresh(client, ["BBB"], TODAY, folder)
    full = [a for a in market.asked if a[1] == (TODAY - dt.timedelta(days=bars.HISTORY_DAYS)).isoformat()]
    assert [a[0] for a in full] == ["split"] and bars.load("BBB", folder)["split"][0][1] == 50.0


def test_accessors_fresh_close_and_adv():
    bob = testkit.temp_bob()
    folder = bob / "data" / "state" / "bars"
    rows = [[d, 10.0, 10.0, 10.0, 10.0, 100] for d in _days(25)]
    testkit.put(folder / "AAA.json", {"raw": rows, "split": [[d, 5.0] for d in _days(25)], "all": [], "evidence": []})
    last = _days(1)[0]
    assert bars.fresh("AAA", last) and not bars.fresh("AAA", TODAY.isoformat()) and not bars.fresh("NONE", last)
    assert bars.close_on("AAA", last) == 10.0 and bars.close_on("AAA", last, "split") == 5.0
    assert bars.close_on("AAA", "1999-01-01") is None and bars.adv("AAA") == 1000.0
    testkit.put(folder / "NEW.json", {"raw": rows[:19], "split": [], "all": [], "evidence": []})
    assert bars.adv("NEW") is None


if __name__ == "__main__":
    raise SystemExit(testkit.run(globals()))
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 harness/test_bars.py`
Expected: `ModuleNotFoundError: No module named 'bars'`.

- [ ] **Step 3: Write the implementation**

Create `bobs/trader/harness/bars.py`:

```python
#!/usr/bin/env python3
"""Daily bars and the market calendar from Alpaca market data, for the few symbols the Trader needs.

Three series, because each answers a different question:
- raw open/high/low/close/volume: what a fill would actually have cost (the simulator fills at the
  next raw open) and what the position is worth today;
- split-adjusted closes: what a stop compares against, so a 2-for-1 split is not a 50% drop;
- dividend-adjusted closes for SPY only: SPY's total return for the go-live comparison.

Read-only by construction: the client refuses any URL that is not the market-data host or the
calendar. Keys come from the environment (ALPACA_PAPER_KEY_ID, ALPACA_PAPER_SECRET_KEY), the same
read-only data keys the Scout uses.

Why a short nightly window: re-reading 400 days every night would grow evidence for nothing. The
window is compared with the stored overlap; if a split or dividend re-adjusted history, that
series alone is re-read in full.

Usage:
    python3 harness/bars.py --refresh     # calendar, then SPY, held symbols and open Scout calls
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import pathlib
import statistics
import sys
import urllib.parse

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import traderpath  # noqa: E402,F401
import fetch  # noqa: E402
import marketclock  # noqa: E402
import store  # noqa: E402
import tradercfg  # noqa: E402

DATA_HOST = "https://data.alpaca.markets/"
BARS_URL = DATA_HOST + "v2/stocks/bars"
CALENDAR_URL = "https://paper-api.alpaca.markets/v2/calendar"
ALLOWED = (DATA_HOST, CALENDAR_URL + "?")
BENCHMARK = "SPY"
CHUNK = 200
RECENT_DAYS = 10
HISTORY_DAYS = 400
TOLERANCE = 1e-4
KEEP_EVIDENCE = 10
ADV_DAYS = 20
SERIES = (("raw", "raw"), ("split", "split"))


def credentials(env=os.environ) -> dict:
    key, secret = env.get("ALPACA_PAPER_KEY_ID"), env.get("ALPACA_PAPER_SECRET_KEY")
    if not (key and secret):
        raise fetch.FetchError("", None, "ALPACA_PAPER_KEY_ID / ALPACA_PAPER_SECRET_KEY not set")
    return {"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret}


def _bar_date(stamp: str) -> str:
    utc = dt.datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    return marketclock.to_et(utc).date().isoformat()


def _row(bar: dict, adjustment: str) -> list:
    day = _bar_date(bar["t"])
    if adjustment == "raw":
        return [day, float(bar["o"]), float(bar["h"]), float(bar["l"]), float(bar["c"]), int(bar["v"])]
    return [day, float(bar["c"])]


class Client:
    def __init__(self, headers: dict, *, getter=fetch.get):
        self.headers, self.getter = headers, getter

    def _read(self, prefix: str, url: str) -> tuple[str, pathlib.Path]:
        if not url.startswith(ALLOWED):
            raise fetch.FetchError(url, None, "refused: bars reads only the Alpaca data host and calendar")
        return fetch.read(prefix, url, self.headers, getter=self.getter)

    def bars(self, symbols: list[str], start: dt.date, end: dt.date,
             adjustment: str) -> tuple[dict[str, list[list]], list[pathlib.Path]]:
        out: dict[str, list[list]] = {}
        evidence: list[pathlib.Path] = []
        for i in range(0, len(symbols), CHUNK):
            token = None
            while True:
                query = {"symbols": ",".join(symbols[i:i + CHUNK]), "timeframe": "1Day",
                         "start": start.isoformat(), "end": end.isoformat(), "adjustment": adjustment,
                         "feed": "sip", "limit": 10000}
                if token:
                    query["page_token"] = token
                raw, where = self._read(f"alpaca-bars-{adjustment}", f"{BARS_URL}?{urllib.parse.urlencode(query)}")
                evidence.append(where)
                doc = json.loads(raw)
                for symbol, rows in (doc.get("bars") or {}).items():
                    out.setdefault(symbol, []).extend(_row(r, adjustment) for r in rows)
                token = doc.get("next_page_token")
                if not token:
                    break
        return out, evidence

    def calendar(self, start: dt.date, end: dt.date) -> tuple[list[dict], pathlib.Path]:
        query = urllib.parse.urlencode({"start": start.isoformat(), "end": end.isoformat()})
        raw, where = self._read("alpaca-calendar", f"{CALENDAR_URL}?{query}")
        return [{"date": s["date"], "close": s["close"][:5]} for s in json.loads(raw)], where


def bars_dir() -> pathlib.Path:
    return tradercfg.state_dir() / "bars"


def load(symbol: str, folder: pathlib.Path | None = None) -> dict:
    doc = store.read_json((folder or bars_dir()) / f"{symbol}.json", {})
    return {"raw": doc.get("raw", []), "split": doc.get("split", []), "all": doc.get("all", []),
            "evidence": doc.get("evidence", [])}


def sessions(folder: pathlib.Path | None = None) -> list[dict]:
    return store.read_json((folder or bars_dir()) / "_calendar.json", {}).get("sessions", [])


def _close(row: list) -> float:
    return row[4] if len(row) == 6 else row[1]


def merge(old: list, new: list) -> tuple[list, bool]:
    by_date = {r[0]: r for r in old}
    changed = any(r[0] in by_date and abs(_close(by_date[r[0]]) - _close(r)) > TOLERANCE * max(abs(_close(r)), 1e-9)
                  for r in new)
    by_date.update({r[0]: r for r in new})
    return [by_date[d] for d in sorted(by_date)], changed


def _save(folder: pathlib.Path, symbol: str, doc: dict) -> None:
    doc["evidence"] = [str(e) for e in doc["evidence"]][-KEEP_EVIDENCE:]
    store.write_json(folder / f"{symbol}.json", doc)


def _update(client, symbols: list[str], adjustment: str, key: str, today: dt.date, folder: pathlib.Path,
            failed: list[str]) -> None:
    window = today - dt.timedelta(days=RECENT_DAYS)
    try:
        recent, recent_ev = client.bars(symbols, window, today, adjustment)
    except fetch.FetchError:
        failed.extend(symbols)
        return
    full = []
    for symbol in symbols:
        doc = load(symbol, folder)
        rows = recent.get(symbol, [])
        merged, changed = merge(doc[key], rows)
        if not doc[key] or doc[key][-1][0] < window.isoformat() or changed:
            full.append(symbol)
            continue
        doc[key] = merged
        doc["evidence"] += recent_ev
        _save(folder, symbol, doc)
    if not full:
        return
    try:
        history, history_ev = client.bars(full, today - dt.timedelta(days=HISTORY_DAYS), today, adjustment)
    except fetch.FetchError:
        failed.extend(full)
        return
    for symbol in full:
        doc = load(symbol, folder)
        doc[key] = history.get(symbol, [])
        doc["evidence"] += history_ev
        _save(folder, symbol, doc)


def refresh(client, symbols: list[str], today: dt.date, folder: pathlib.Path) -> dict:
    failed: list[str] = []
    for adjustment, key in SERIES:
        _update(client, symbols, adjustment, key, today, folder, failed)
    if BENCHMARK in symbols:
        _update(client, [BENCHMARK], "all", "all", today, folder, failed)
    return {"symbols": len(symbols), "failed": sorted(set(failed))}


def fresh(symbol: str, session: str) -> bool:
    raw = load(symbol)["raw"]
    return bool(raw) and raw[-1][0] == session


def close_on(symbol: str, session: str, key: str = "raw") -> float | None:
    return next((_close(r) for r in load(symbol)[key] if r[0] == session), None)


def adv(symbol: str) -> float | None:
    raw = load(symbol)["raw"][-ADV_DAYS:]
    if len(raw) < ADV_DAYS:
        return None
    return statistics.fmean(r[4] * r[5] for r in raw)


def needed_symbols() -> list[str]:
    import ledger
    import scout_feed
    held = set(ledger.replay(ledger.read()[0]).positions())
    feed = scout_feed.load()
    calls = {c["symbol"] for c in scout_feed.open_calls(feed, dt.date.today())}
    return sorted({BENCHMARK} | held | calls)


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--refresh", action="store_true", required=True)
    ap.parse_args(argv)
    today = fetch.utcnow().date()
    try:
        client = Client(credentials())
        folder = bars_dir()
        cal, where = client.calendar(today - dt.timedelta(days=30), today + dt.timedelta(days=10))
        store.write_json(folder / "_calendar.json", {"fetched": today.isoformat(), "evidence": str(where), "sessions": cal})
        symbols = needed_symbols()
        summary = refresh(client, symbols, today, folder)
    except fetch.FetchError as e:
        print(f"FAIL: {e}")
        return 1
    if summary["failed"] and len(summary["failed"]) == len(symbols):
        print(f"FAIL: every bars request failed ({len(symbols)} symbols)")
        return 1
    print(f"OK symbols={summary['symbols']} failed={summary['failed']}")
    return 0


if __name__ == "__main__":
    import oscompat
    oscompat.utf8_stdio()
    raise SystemExit(main(sys.argv[1:]))
```

`needed_symbols` imports `ledger` and `scout_feed` inside the function: `scout_feed` arrives in Task 4, and `bars` must import cleanly before it exists.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 harness/test_bars.py`
Expected: `6 of 6 passed`.

- [ ] **Step 5: Break it once, then restore**

In `_update`, remove `or changed` from the full-refetch condition: `test_a_split_re_adjusts_history_and_triggers_a_full_split_refetch` must FAIL. Restore. Re-run: `6 of 6 passed`.

- [ ] **Step 6: Commit**

```bash
git add harness/bars.py harness/test_bars.py
git commit -m "Trader: bars.py reads raw, split-adjusted and SPY total-return series

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
git push
```

---

### Task 4: Reading the Scout (`scout_feed.py`)

**Files:**
- Create: `bobs/trader/harness/scout_feed.py`
- Test: `bobs/trader/harness/test_scout_feed.py`

**Interfaces:**
- Consumes: `store.read_json`, `tradercfg.scout()`.
- Produces:
  - `scout_feed.FeedError`
  - `scout_feed.load(state: Path | None = None) -> dict` with keys `calls` (v2 call rows: `kind == "call"` with a `card` dict), `withdrawn` (set of call ids), `skipped_v1` (int), `torn` (int), `macro` (the Scout's `macro.json`, `{}` when absent)
  - `scout_feed.open_calls(feed, today: dt.date, max_days: int = 365) -> list[dict]`
  - `scout_feed.macro_value(macro: dict, series: str) -> float | None` (None when missing, unread or stale)

- [ ] **Step 1: Write the failing tests**

Create `bobs/trader/harness/test_scout_feed.py`:

```python
#!/usr/bin/env python3
"""scout_feed: v2 calls only, withdrawals honoured, torn lines counted, stale macro is unknown.

Run:  python3 harness/test_scout_feed.py
"""
from __future__ import annotations

import datetime as dt
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import traderpath  # noqa: E402,F401
import scout_feed  # noqa: E402
import testkit  # noqa: E402

CARD = {"rank_pct": 95.0, "groups": {"insider": None}}


def _call(cid: str, session: str, card: dict | None = CARD) -> dict:
    row = {"kind": "call", "id": cid, "ticker": cid[:3], "symbol": cid[:3], "session": session}
    return {**row, "card": card} if card is not None else row


def test_reads_v2_calls_and_counts_v1_and_torn():
    bob = testkit.temp_bob()
    rows = [_call("AAA-1", "2026-10-01"), _call("OLD-1", "2026-09-01", card=None),
            {"kind": "verdict", "ticker": "AAA"}, {"kind": "withdrawn", "call_id": "AAA-1"}]
    testkit.put(testkit.scout_state(bob) / "calls.jsonl", "".join(json.dumps(r) + "\n" for r in rows) + "{torn\n")
    feed = scout_feed.load()
    assert [c["id"] for c in feed["calls"]] == ["AAA-1"] and feed["skipped_v1"] == 1 and feed["torn"] == 1
    assert feed["withdrawn"] == {"AAA-1"} and feed["macro"] == {}


def test_missing_ledger_is_empty_not_an_error():
    testkit.temp_bob()
    feed = scout_feed.load()
    assert feed["calls"] == [] and feed["torn"] == 0


def test_open_calls_drop_withdrawn_and_expired():
    feed = {"calls": [_call("AAA-1", "2026-10-01"), _call("BBB-1", "2025-10-01"), _call("CCC-1", "2026-10-05")],
            "withdrawn": {"CCC-1"}}
    assert [c["id"] for c in scout_feed.open_calls(feed, dt.date(2026, 10, 14))] == ["AAA-1"]


def test_macro_value_is_none_when_missing_unread_or_stale():
    macro = {"series": {"VIXCLS": {"value": 18.0, "stale": False}, "NFCI": {"value": -0.4, "stale": True},
                        "T10Y2Y": {"value": None, "stale": None}}}
    assert scout_feed.macro_value(macro, "VIXCLS") == 18.0
    assert scout_feed.macro_value(macro, "NFCI") is None and scout_feed.macro_value(macro, "T10Y2Y") is None
    assert scout_feed.macro_value(macro, "ICSA") is None and scout_feed.macro_value({}, "VIXCLS") is None


if __name__ == "__main__":
    raise SystemExit(testkit.run(globals()))
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 harness/test_scout_feed.py`
Expected: `ModuleNotFoundError: No module named 'scout_feed'`.

- [ ] **Step 3: Write the implementation**

Create `bobs/trader/harness/scout_feed.py`:

```python
#!/usr/bin/env python3
"""The Scout's calls and market conditions, read as data. Never written.

Why v2 calls only: the Trader sizes by the call's rank and checks its factor groups, and only calls
made under scorecard v2 carry their full card. A v1 call is counted and skipped, never guessed at.
Why a stale market series is None: a value that stopped updating looks exactly like a current one;
sizing treats None as the cautious case.

Usage (library; trade.py and bars.py call it):
    feed = scout_feed.load()
    calls = scout_feed.open_calls(feed, today)
"""
from __future__ import annotations

import datetime as dt
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import traderpath  # noqa: E402,F401
import store  # noqa: E402
import tradercfg  # noqa: E402

HOLD_DAYS = 365


class FeedError(Exception):
    pass


def load(state: pathlib.Path | None = None) -> dict:
    state = state or tradercfg.scout()["state_dir"]
    path = state / "calls.jsonl"
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        lines = []
    except OSError as e:
        raise FeedError(f"cannot read the Scout's ledger {path}: {e}") from None
    rows, torn = [], 0
    for text in lines:
        if not text.strip():
            continue
        try:
            rows.append(json.loads(text))
        except ValueError:
            torn += 1
    calls = [r for r in rows if r.get("kind") == "call"]
    v2 = [c for c in calls if isinstance(c.get("card"), dict)]
    return {"calls": v2, "withdrawn": {r["call_id"] for r in rows if r.get("kind") == "withdrawn"},
            "skipped_v1": len(calls) - len(v2), "torn": torn, "macro": store.read_json(state / "macro.json", {})}


def open_calls(feed: dict, today: dt.date, max_days: int = HOLD_DAYS) -> list[dict]:
    return [c for c in feed["calls"] if c["id"] not in feed["withdrawn"]
            and (today - dt.date.fromisoformat(c["session"])).days < max_days]


def macro_value(macro: dict, series: str) -> float | None:
    row = (macro.get("series") or {}).get(series) or {}
    if row.get("value") is None or row.get("stale") is not False:
        return None
    return float(row["value"])
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 harness/test_scout_feed.py`
Expected: `4 of 4 passed`.

- [ ] **Step 5: Break it once, then restore**

In `macro_value`, change `row.get("stale") is not False` to `row.get("stale") is True`: `test_macro_value_is_none_when_missing_unread_or_stale` must FAIL (a series with `stale: None`, never confirmed fresh, would be read as fresh). Restore. In `load`, drop the `isinstance(c.get("card"), dict)` filter (`v2 = calls`): `test_reads_v2_calls_and_counts_v1_and_torn` must FAIL. Restore. Re-run: `4 of 4 passed`.

- [ ] **Step 6: Commit**

```bash
git add harness/scout_feed.py harness/test_scout_feed.py
git commit -m "Trader: scout_feed reads the Scout's v2 calls and market conditions as data

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
git push
```

---

### Task 5: Broker interface and simulator (`broker/base.py`, `broker/sim.py`)

**Files:**
- Create: `bobs/trader/harness/broker/base.py`, `bobs/trader/harness/broker/sim.py`
- Test: `bobs/trader/harness/test_sim.py`

**Interfaces:**
- Consumes: `bars.load`, `ledger.fill_amount`, `ledger.EPS`, `store.read_json`, `store.write_json`, `tradercfg.load()`, `tradercfg.state_dir()`.
- Produces:
  - `broker.base.AdapterError`, `broker.base.Broker` (`MODE: str`; `__init__(mode)` refuses another mode; `account() -> {"cash": float}`, `positions() -> dict[str, float]`, `submit(order: dict) -> None`, `activities() -> list[dict]`)
  - `broker.base.adapter_for(mode: str) -> Broker` (only `"simulate"` in stage 1; anything else raises `AdapterError`)
  - `broker.sim.Simulator(mode, folder=None, cfg=None)`, `MODE = "simulate"`, `EXPIRE_SESSIONS = 5`
  - order dict: `{"order_id", "symbol", "side": "buy"|"sell", "qty", "session"}`
  - activity dicts (ledger-shaped, each with a stable `activity_id`): `{"activity_id", "kind": "filled", "order_id", "symbol", "side", "qty", "price", "commission", "session"}`, `{"activity_id", "kind": "expired", "order_id", "reason", "session"}`, `{"activity_id", "kind": "split", "symbol", "ratio", "session"}`

- [ ] **Step 1: Write the failing tests**

Create `bobs/trader/harness/test_sim.py`:

```python
#!/usr/bin/env python3
"""broker: one mode per adapter; the simulator fills at the next raw open with costs, sees splits.

Run:  python3 harness/test_sim.py
"""
from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import traderpath  # noqa: E402,F401
import testkit  # noqa: E402
from broker import base, sim  # noqa: E402

DAYS = ["2026-01-05", "2026-01-06", "2026-01-07", "2026-01-08", "2026-01-09", "2026-01-12", "2026-01-13"]
CFG = {"sim_starting_cash": 10000, "slippage_bps": 10, "commission_per_order": 1.0}


def _bars(bob: pathlib.Path, symbol: str, raw: list[float], split: list[float] | None = None) -> None:
    days = DAYS[:len(raw)]
    testkit.put(bob / "data" / "state" / "bars" / f"{symbol}.json", {
        "raw": [[d, c, c, c, c, 1000] for d, c in zip(days, raw)],
        "split": [[d, c] for d, c in zip(days, split or raw)], "all": [], "evidence": []})


def _sim(mode: str = "simulate") -> sim.Simulator:
    return sim.Simulator(mode, cfg=CFG)


def test_each_adapter_serves_one_mode():
    testkit.temp_bob()
    try:
        _sim("paper")
    except base.AdapterError as e:
        assert "simulate" in str(e)
    else:
        raise AssertionError("not refused")
    for mode in ("paper", "live"):
        try:
            base.adapter_for(mode)
        except base.AdapterError:
            pass
        else:
            raise AssertionError(f"{mode} must have no adapter in stage 1")


def test_buy_fills_at_next_open_with_slippage_and_commission():
    bob = testkit.temp_bob()
    _bars(bob, "SPY", [400.0] * 3)
    _bars(bob, "AAA", [100.0, 102.0, 104.0])
    broker = _sim()
    broker.submit({"order_id": "o1", "symbol": "AAA", "side": "buy", "qty": 10, "session": DAYS[0]})
    fill = broker.activities()[0]
    assert fill["kind"] == "filled" and fill["session"] == DAYS[1] and abs(fill["price"] - 102.102) < 1e-9
    assert broker.positions() == {"AAA": 10} and broker.account() == {"cash": round(10000 - 1021.02 - 1.0, 2)}
    assert sim.Simulator("simulate", cfg=CFG).activities() == broker.activities()


def test_order_waits_for_a_bar_then_expires():
    bob = testkit.temp_bob()
    _bars(bob, "SPY", [400.0] * 3)
    _bars(bob, "HALT", [50.0])
    broker = _sim()
    broker.submit({"order_id": "o1", "symbol": "HALT", "side": "buy", "qty": 1, "session": DAYS[0]})
    assert broker.activities() == []
    _bars(bob, "SPY", [400.0] * 7)
    assert broker.activities()[0]["kind"] == "expired" and broker.positions() == {}


def test_insufficient_cash_expires_the_buy():
    bob = testkit.temp_bob()
    _bars(bob, "SPY", [400.0] * 2)
    _bars(bob, "AAA", [100.0, 100.0])
    broker = _sim()
    broker.submit({"order_id": "o1", "symbol": "AAA", "side": "buy", "qty": 1000, "session": DAYS[0]})
    act = broker.activities()[0]
    assert act["kind"] == "expired" and "cash" in act["reason"] and broker.account()["cash"] == 10000


def test_split_is_seen_from_the_adjusted_series():
    bob = testkit.temp_bob()
    _bars(bob, "SPY", [400.0] * 2)
    _bars(bob, "BBB", [100.0, 100.0])
    broker = _sim()
    broker.submit({"order_id": "o1", "symbol": "BBB", "side": "buy", "qty": 10, "session": DAYS[0]})
    broker.activities()
    _bars(bob, "SPY", [400.0] * 4)
    _bars(bob, "BBB", [100.0, 100.0, 50.0, 50.0], split=[50.0, 50.0, 50.0, 50.0])
    split = [a for a in broker.activities() if a["kind"] == "split"]
    assert [(a["ratio"], a["session"]) for a in split] == [(2.0, DAYS[2])] and broker.positions() == {"BBB": 20.0}


def test_duplicate_client_order_id_refused():
    bob = testkit.temp_bob()
    _bars(bob, "SPY", [400.0])
    broker = _sim()
    order = {"order_id": "o1", "symbol": "AAA", "side": "buy", "qty": 1, "session": DAYS[0]}
    broker.submit(order)
    try:
        broker.submit(order)
    except base.AdapterError as e:
        assert "duplicate" in str(e)
    else:
        raise AssertionError("not refused")


def test_sell_never_sells_more_than_held():
    bob = testkit.temp_bob()
    _bars(bob, "SPY", [400.0] * 3)
    _bars(bob, "AAA", [100.0] * 3)
    broker = _sim()
    broker.submit({"order_id": "b", "symbol": "AAA", "side": "buy", "qty": 5, "session": DAYS[0]})
    broker.activities()
    broker.submit({"order_id": "s", "symbol": "AAA", "side": "sell", "qty": 9, "session": DAYS[1]})
    sell = broker.activities()[-1]
    assert sell["qty"] == 5 and broker.positions() == {}


if __name__ == "__main__":
    raise SystemExit(testkit.run(globals()))
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 harness/test_sim.py`
Expected: `ImportError: cannot import name 'base' from 'broker'`.

- [ ] **Step 3: Write the implementation**

Create `bobs/trader/harness/broker/base.py`:

```python
#!/usr/bin/env python3
"""The one interface every broker adapter implements; trade.py and approve.py talk only to it.

Why an interface: the broker is decided later, and the guards (caps, reconcile, the approval queue)
must not change when it is. Why one mode per adapter: a paper key must never end up placing live
orders, so each adapter names the single mode it serves and refuses any other before it touches
the network.

Usage (library):
    broker = base.adapter_for(cfg["mode"])
"""
from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import traderpath  # noqa: E402,F401


class AdapterError(Exception):
    pass


class Broker:
    MODE = ""

    def __init__(self, mode: str):
        if mode != self.MODE:
            raise AdapterError(f"{type(self).__name__} serves mode {self.MODE!r}, not {mode!r}")

    def account(self) -> dict:
        raise NotImplementedError

    def positions(self) -> dict[str, float]:
        raise NotImplementedError

    def submit(self, order: dict) -> None:
        raise NotImplementedError

    def activities(self) -> list[dict]:
        raise NotImplementedError


def adapter_for(mode: str) -> Broker:
    if mode == "simulate":
        from broker import sim
        return sim.Simulator(mode)
    raise AdapterError(f"no broker adapter for mode {mode!r} yet; stage 1 builds only the simulator")
```

Create `bobs/trader/harness/broker/sim.py`:

```python
#!/usr/bin/env python3
"""The simulator: a broker that fills market orders at the next session's raw open, with costs.

Why next open: the Trader decides after the close, so the earliest honest fill is the next open.
Slippage moves the price against the order; commission is charged per order. A buy the cash cannot
cover, or an order with no bar for EXPIRE_SESSIONS sessions (a halted stock), expires with its
reason. Splits are found where the raw and split-adjusted closes stop moving together, and reported
as activities, as a real broker would. Dividends are not credited: it understates returns, which
is the conservative side.

Every activity keeps its id forever, so trade.py can collect them again after a crash without
recording one twice.

Usage (library; through broker.base.adapter_for("simulate")).
"""
from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import traderpath  # noqa: E402,F401
import bars  # noqa: E402
import ledger  # noqa: E402
import store  # noqa: E402
import tradercfg  # noqa: E402
from broker import base  # noqa: E402

EXPIRE_SESSIONS = 5
SPLIT_TOLERANCE = 1e-3


class Simulator(base.Broker):
    MODE = "simulate"

    def __init__(self, mode: str, folder: pathlib.Path | None = None, cfg: dict | None = None):
        super().__init__(mode)
        self.cfg = cfg or tradercfg.load()
        self.path = (folder or tradercfg.state_dir() / "sim") / "broker.json"
        self.state = store.read_json(self.path, None) or {
            "cash": float(self.cfg["sim_starting_cash"]), "positions": {}, "open": [], "activities": [], "seen": {}}

    def _save(self) -> None:
        store.write_json(self.path, self.state)

    def _activity(self, kind: str, session: str, **fields) -> None:
        self.state["activities"].append({"activity_id": f"sim-{len(self.state['activities']) + 1}", "kind": kind,
                                         "session": session, **fields})

    def account(self) -> dict:
        self._settle()
        return {"cash": round(self.state["cash"], 2)}

    def positions(self) -> dict[str, float]:
        self._settle()
        return {s: q for s, q in self.state["positions"].items() if q > ledger.EPS}

    def activities(self) -> list[dict]:
        self._settle()
        return list(self.state["activities"])

    def submit(self, order: dict) -> None:
        known = {o["order_id"] for o in self.state["open"]} | {a.get("order_id") for a in self.state["activities"]}
        if order["order_id"] in known:
            raise base.AdapterError(f"duplicate client order id {order['order_id']}")
        self.state["open"].append(dict(order))
        self._save()

    def _settle(self) -> None:
        self._splits()
        spy = bars.load(bars.BENCHMARK)["raw"]
        waiting = []
        for order in self.state["open"]:
            later = [r for r in bars.load(order["symbol"])["raw"] if r[0] > order["session"]]
            if later:
                self._fill(order, later[0])
                continue
            passed = [r[0] for r in spy if r[0] > order["session"]]
            if len(passed) >= EXPIRE_SESSIONS:
                self._activity("expired", passed[-1], order_id=order["order_id"],
                               reason=f"no {order['symbol']} bar in {EXPIRE_SESSIONS} sessions")
                continue
            waiting.append(order)
        self.state["open"] = waiting
        self._save()

    def _fill(self, order: dict, bar: list) -> None:
        day, open_price = bar[0], bar[1]
        slip = self.cfg["slippage_bps"] / 10_000
        commission = float(self.cfg["commission_per_order"])
        held = self.state["positions"].get(order["symbol"], 0.0)
        side = order["side"]
        price = round(open_price * (1 + slip if side == "buy" else 1 - slip), 4)
        qty = order["qty"] if side == "buy" else min(order["qty"], held)
        if side == "sell" and qty <= ledger.EPS:
            self._activity("expired", day, order_id=order["order_id"], reason="nothing to sell")
            return
        amount = ledger.fill_amount(side, qty, price, commission)
        if self.state["cash"] + amount < 0:
            self._activity("expired", day, order_id=order["order_id"], reason="insufficient cash at the open")
            return
        self.state["cash"] = round(self.state["cash"] + amount, 2)
        self.state["positions"][order["symbol"]] = round(held + (qty if side == "buy" else -qty), 6)
        self.state["seen"].setdefault(order["symbol"], day)
        self._activity("filled", day, order_id=order["order_id"], symbol=order["symbol"], side=side, qty=qty,
                       price=price, commission=commission)

    def _splits(self) -> None:
        """Compare raw and split-adjusted closes day by day from the last day seen. The adjusted
        series is rewritten after each split, so factors are always recomputed from the current
        file, never from a stored number."""
        for symbol, since in list(self.state["seen"].items()):
            if self.state["positions"].get(symbol, 0.0) <= ledger.EPS:
                del self.state["seen"][symbol]
                continue
            stored = bars.load(symbol)
            adjusted = {r[0]: r[1] for r in stored["split"]}
            raw = [(r[0], r[4]) for r in stored["raw"] if r[0] >= since and r[0] in adjusted and adjusted[r[0]]]
            for (d0, c0), (d1, c1) in zip(raw, raw[1:]):
                ratio = (c0 / adjusted[d0]) / (c1 / adjusted[d1])
                if abs(ratio - 1) > SPLIT_TOLERANCE:
                    ratio = round(ratio, 6)
                    self.state["positions"][symbol] = round(self.state["positions"][symbol] * ratio, 6)
                    self._activity("split", d1, symbol=symbol, ratio=ratio)
            if raw:
                self.state["seen"][symbol] = raw[-1][0]
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 harness/test_sim.py`
Expected: `7 of 7 passed`.

- [ ] **Step 5: Break it once, then restore**

In `Broker.__init__`, delete the mode check: `test_each_adapter_serves_one_mode` must FAIL. Restore. In `_splits`, compute the factor from a stored number instead (replace `adjusted[d0]` with `c0` so the ratio is always 1): `test_split_is_seen_from_the_adjusted_series` must FAIL. Restore. Re-run: `7 of 7 passed`.

- [ ] **Step 6: Commit**

```bash
git add harness/broker harness/test_sim.py
git commit -m "Trader: broker interface and a simulator that fills at the next open

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
git push
```

---

### Task 6: Reconcile (`reconcile.py`)

**Files:**
- Create: `bobs/trader/harness/reconcile.py`
- Test: `bobs/trader/harness/test_reconcile.py`

**Interfaces:**
- Consumes: `ledger.replay`, `ledger.unresolved_mismatches`, `ledger.EPS`, `ledger.Book`, a `Broker`.
- Produces:
  - `reconcile.differences(book, broker_positions: dict, broker_cash: float) -> list[str]`
  - `reconcile.check(rows: list[dict], broker) -> list[str]` (differences plus one entry per unresolved mismatch; empty means clean)

- [ ] **Step 1: Write the failing tests**

Create `bobs/trader/harness/test_reconcile.py`:

```python
#!/usr/bin/env python3
"""reconcile: shares exactly, cash to the cent; a torn ledger line is a mismatch, not a crash.

Run:  python3 harness/test_reconcile.py
"""
from __future__ import annotations

import datetime as dt
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import traderpath  # noqa: E402,F401
import ledger  # noqa: E402
import reconcile  # noqa: E402
import testkit  # noqa: E402

NOW = dt.datetime(2026, 10, 14, 6, 30, tzinfo=dt.timezone.utc)


class Broker:
    def __init__(self, positions: dict, cash: float):
        self._positions, self._cash = positions, cash

    def positions(self) -> dict:
        return self._positions

    def account(self) -> dict:
        return {"cash": self._cash}


ROWS = [ledger.line("cash", NOW, "s", amount=1000.0),
        ledger.line("filled", NOW, "s", order_id="o", symbol="AAA", side="buy", qty=3, price=100.0, commission=1.0)]


def test_exact_match_is_clean():
    assert reconcile.check(ROWS, Broker({"AAA": 3.0}, 699.0)) == []


def test_one_share_or_one_cent_off_is_a_mismatch():
    assert reconcile.check(ROWS, Broker({"AAA": 4.0}, 699.0)) == ["AAA: ledger 3 shares, broker 4"]
    assert reconcile.check(ROWS, Broker({"AAA": 3.0}, 699.01)) == ["cash: ledger 699.00, broker 699.01"]
    assert reconcile.check(ROWS, Broker({"AAA": 3.0, "ZZZ": 1.0}, 699.0)) == ["ZZZ: ledger 0 shares, broker 1"]


def test_a_lost_line_shows_as_a_mismatch():
    assert reconcile.check(ROWS[:1], Broker({"AAA": 3.0}, 699.0)) == [
        "AAA: ledger 0 shares, broker 3", "cash: ledger 1000.00, broker 699.00"]


def test_unresolved_mismatch_blocks_until_corrected():
    mismatch = ledger.line("mismatch", NOW, "s", details=["x"])
    assert reconcile.check(ROWS + [mismatch], Broker({"AAA": 3.0}, 699.0)) == [f"unresolved mismatch {mismatch['id']}"]
    fix = ledger.line("correction", NOW, "s", clears=mismatch["id"], cash_delta=0.0, share_deltas={})
    assert reconcile.check(ROWS + [mismatch, fix], Broker({"AAA": 3.0}, 699.0)) == []


if __name__ == "__main__":
    raise SystemExit(testkit.run(globals()))
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 harness/test_reconcile.py`
Expected: `ModuleNotFoundError: No module named 'reconcile'`.

- [ ] **Step 3: Write the implementation**

Create `bobs/trader/harness/reconcile.py`:

```python
#!/usr/bin/env python3
"""The replayed ledger against the broker: shares exactly, cash to the cent.

Why exact: a share or a cent off means a fill, a dividend or a split the books did not record, and
every later order would be sized on numbers that are not real. Why an unresolved mismatch also
blocks: a person must look at each one and record a correction; the agent may never make the books
agree by itself. Fails closed: any difference stops trading.

Usage (library; trade.py and approve.py call it):
    problems = reconcile.check(rows, broker)
"""
from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import traderpath  # noqa: E402,F401
import ledger  # noqa: E402

CENT = 0.005


def differences(book: ledger.Book, broker_positions: dict, broker_cash: float) -> list[str]:
    mine = book.positions()
    out = []
    for symbol in sorted(set(mine) | set(broker_positions)):
        a, b = mine.get(symbol, 0.0), float(broker_positions.get(symbol, 0.0))
        if abs(a - b) > ledger.EPS:
            out.append(f"{symbol}: ledger {a:g} shares, broker {b:g}")
    if abs(book.cash - broker_cash) >= CENT:
        out.append(f"cash: ledger {book.cash:.2f}, broker {broker_cash:.2f}")
    return out


def check(rows: list[dict], broker) -> list[str]:
    found = differences(ledger.replay(rows), broker.positions(), broker.account()["cash"])
    return found + [f"unresolved mismatch {m['id']}" for m in ledger.unresolved_mismatches(rows)]
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 harness/test_reconcile.py`
Expected: `4 of 4 passed`.

- [ ] **Step 5: Break it once, then restore**

Change `>= CENT` to `>= 0.02`: `test_one_share_or_one_cent_off_is_a_mismatch` must FAIL. Restore. Re-run: `4 of 4 passed`.

- [ ] **Step 6: Commit**

```bash
git add harness/reconcile.py harness/test_reconcile.py
git commit -m "Trader: reconcile the replayed ledger against the broker, exactly

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
git push
```

---

### Task 7: Pre-trade checks (`sizing.py`, `risk_flags.py`, `caps.py`)

**Files:**
- Create: `bobs/trader/harness/sizing.py`, `bobs/trader/harness/risk_flags.py`, `bobs/trader/harness/caps.py`
- Test: `bobs/trader/harness/test_pretrade.py`

**Interfaces:**
- Consumes: `scout_feed.macro_value`, `ledger.Book`.
- Produces:
  - `sizing.bad_signals(macro, cfg) -> list[str]` (subset of `["vix", "curve", "credit", "conditions"]`), `sizing.throttle(macro, cfg) -> float`, `sizing.conviction(rank_pct, buy_percentile) -> float`, `sizing.size(equity, price, rank_pct, macro, cfg, buy_percentile) -> dict` (`qty: int, value: float, target_value, conviction, throttle, bad_signals`)
  - `risk_flags.flags(card, macro, value, adv, cfg) -> list[str]`, `risk_flags.blocking(found, cfg) -> list[str]`
  - `caps.Exposure` (dataclass: `equity, cash, values: dict[str, float], orders: int, by_strategy: dict[str, float]`; property `invested`), `caps.exposure(book, closes: dict[str, float], equity: float) -> Exposure`, `caps.check_buy(exp, symbol, value, strategy, cfg) -> str | None`, `caps.accept(exp, symbol, value, strategy, cfg) -> None`, `caps.count_sell(exp) -> None`

- [ ] **Step 1: Write the failing tests**

Create `bobs/trader/harness/test_pretrade.py`:

```python
#!/usr/bin/env python3
"""sizing, risk flags and caps: cautious when blind, each cap refuses one cent over and allows at it.

Run:  python3 harness/test_pretrade.py
"""
from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import traderpath  # noqa: E402,F401
import caps  # noqa: E402
import risk_flags  # noqa: E402
import sizing  # noqa: E402
import testkit  # noqa: E402

CFG = {"base_weight": 0.05, "max_position": 0.10, "max_invested": 0.80, "min_cash": 0.20, "max_orders_per_day": 10,
       "commission_per_order": 1.0, "budgets": {"scout_calls": 1.0}, "vix_flag": 25, "hy_spread_flag": 5.0,
       "throttle_floor": 0.25, "max_adv_share": 0.01, "block_flags": ["liquidity"]}


def _series(**values) -> dict:
    return {"series": {k: {"value": v, "stale": False} for k, v in values.items()}}


CALM = _series(VIXCLS=15.0, T10Y2Y=0.5, BAMLH0A0HYM2=3.0, NFCI=-0.5)


def test_conviction_runs_from_half_at_the_buy_line_to_one_at_the_top():
    assert sizing.conviction(80.0, 80) == 0.5 and sizing.conviction(100.0, 80) == 1.0
    assert sizing.conviction(90.0, 80) == 0.75 and sizing.conviction(None, 80) == 0.5


def test_throttle_counts_bad_signals_and_missing_ones_count_as_bad():
    assert sizing.throttle(CALM, CFG) == 1.0
    stressed = _series(VIXCLS=30.0, T10Y2Y=-0.2, BAMLH0A0HYM2=3.0, NFCI=-0.5)
    assert sizing.bad_signals(stressed, CFG) == ["vix", "curve"] and sizing.throttle(stressed, CFG) == 0.5
    assert sizing.throttle({}, CFG) == 0.25


def test_size_rounds_down_to_whole_shares():
    s = sizing.size(100_000, 101.0, 100.0, CALM, CFG, 80)
    assert s["target_value"] == 5000.0 and s["qty"] == 49 and s["value"] == 4949.0


def test_flags_and_which_ones_block():
    card = {"groups": {"insider": 40.0}}
    found = risk_flags.flags(card, _series(VIXCLS=30.0), 2000.0, 100_000.0, CFG)
    assert found == ["vix", "insider", "liquidity"] and risk_flags.blocking(found, CFG) == ["liquidity"]
    assert risk_flags.flags({"groups": {"insider": None}}, CALM, 500.0, 100_000.0, CFG) == []
    assert risk_flags.flags({}, CALM, 500.0, None, CFG) == ["liquidity"]


def _exp(**over) -> caps.Exposure:
    base = {"equity": 100_000.0, "cash": 100_000.0, "values": {}, "orders": 0, "by_strategy": {}}
    return caps.Exposure(**{**base, **over})


def test_position_cap_refuses_one_cent_over_and_allows_at_it():
    assert caps.check_buy(_exp(), "AAA", 10_000.00, "scout_calls", CFG) is None
    assert "max_position" in caps.check_buy(_exp(), "AAA", 10_000.01, "scout_calls", CFG)


def test_invested_cash_orders_and_budget_caps():
    full = _exp(cash=20_000.0, values={"X": 75_000.0})
    assert "max_invested" in caps.check_buy(full, "AAA", 5_001.0, "scout_calls", CFG)
    poor = _exp(cash=24_000.0, values={"X": 70_000.0})
    assert caps.check_buy(poor, "AAA", 3_999.0, "scout_calls", CFG) is None
    assert "min_cash" in caps.check_buy(poor, "AAA", 3_999.02, "scout_calls", CFG)
    assert "orders today" in caps.check_buy(_exp(orders=10), "AAA", 100.0, "scout_calls", CFG)
    assert "budget" in caps.check_buy(_exp(), "AAA", 100.0, "other", CFG)


def test_orders_accepted_earlier_in_the_run_count():
    exp = _exp()
    caps.accept(exp, "AAA", 6_000.0, "scout_calls", CFG)
    assert "max_position" in caps.check_buy(exp, "AAA", 4_000.01, "scout_calls", CFG)
    assert exp.orders == 1 and exp.cash == 100_000.0 - 6_001.0
    caps.count_sell(exp)
    assert exp.orders == 2


if __name__ == "__main__":
    raise SystemExit(testkit.run(globals()))
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 harness/test_pretrade.py`
Expected: `ModuleNotFoundError: No module named 'caps'`.

- [ ] **Step 3: Write the implementation**

Create `bobs/trader/harness/sizing.py`:

```python
#!/usr/bin/env python3
"""How much to buy: equity x base weight x conviction x market-conditions throttle, in whole shares.

Why conviction from the call's rank: a name at the top of the Scout's ranking earns a fuller
position than one just over its buy line, and the rank is computed by code, not judged.
Why the throttle: when VIX is high, the yield curve is inverted, credit spreads are wide or
financial conditions are tight, the same pick deserves less money. A series that is missing or
stale counts as bad: not knowing is the cautious case, never the calm one.

Usage (library; trade.py calls it):
    s = sizing.size(equity, price, card["rank_pct"], macro, cfg, buy_percentile)
"""
from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import traderpath  # noqa: E402,F401
import scout_feed  # noqa: E402

STEP = 0.25


def bad_signals(macro: dict, cfg: dict) -> list[str]:
    vix = scout_feed.macro_value(macro, "VIXCLS")
    curve = scout_feed.macro_value(macro, "T10Y2Y")
    credit = scout_feed.macro_value(macro, "BAMLH0A0HYM2")
    conditions = scout_feed.macro_value(macro, "NFCI")
    checks = (("vix", vix is None or vix > cfg["vix_flag"]), ("curve", curve is None or curve < 0),
              ("credit", credit is None or credit > cfg["hy_spread_flag"]),
              ("conditions", conditions is None or conditions > 0))
    return [name for name, bad in checks if bad]


def throttle(macro: dict, cfg: dict) -> float:
    return max(cfg["throttle_floor"], 1 - STEP * len(bad_signals(macro, cfg)))


def conviction(rank_pct: float | None, buy_percentile: float | None) -> float:
    if rank_pct is None or buy_percentile is None or buy_percentile >= 100:
        return 0.5
    return min(1.0, max(0.5, 0.5 + 0.5 * (rank_pct - buy_percentile) / (100 - buy_percentile)))


def size(equity: float, price: float, rank_pct: float | None, macro: dict, cfg: dict,
         buy_percentile: float | None) -> dict:
    c, t = conviction(rank_pct, buy_percentile), throttle(macro, cfg)
    target = equity * cfg["base_weight"] * c * t
    qty = int(target // price) if price > 0 else 0
    return {"qty": qty, "value": round(qty * price, 2), "target_value": round(target, 2), "conviction": c,
            "throttle": t, "bad_signals": bad_signals(macro, cfg)}
```

Create `bobs/trader/harness/risk_flags.py`:

```python
#!/usr/bin/env python3
"""Warnings on one proposed buy; only the flags named in block_flags refuse it.

Why most flags only warn: VIX and insider selling are context a person reads later next to the
result; refusing on them would turn a soft signal into a hard rule nobody tested. Liquidity blocks
by default: an order that is a large share of a day's trading moves the price against itself.
Two flags from the original comparison are left out because they could never fire here: earnings
inside the holding period (no source of future earnings dates) and weak factor agreement (every
v2 buy-worthy call already passed the Scout's agreement gate).

Usage (library; trade.py calls it):
    found = risk_flags.flags(card, macro, value, adv, cfg)
"""
from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import traderpath  # noqa: E402,F401
import scout_feed  # noqa: E402


def flags(card: dict, macro: dict, value: float, adv: float | None, cfg: dict) -> list[str]:
    out = []
    vix = scout_feed.macro_value(macro, "VIXCLS")
    if vix is None or vix > cfg["vix_flag"]:
        out.append("vix")
    insider = (card.get("groups") or {}).get("insider")
    if insider is not None and insider < 50:
        out.append("insider")
    if adv is None or value > cfg["max_adv_share"] * adv:
        out.append("liquidity")
    return out


def blocking(found: list[str], cfg: dict) -> list[str]:
    return [f for f in found if f in cfg["block_flags"]]
```

Create `bobs/trader/harness/caps.py`:

```python
#!/usr/bin/env python3
"""Hard limits on every buy: per position, total invested, cash floor, orders per day, strategy budget.

Why orders earlier in the same run count: ten buys that each pass alone can together breach the
invested cap; the exposure is updated after each accepted order. Why sells are exempt (they still
count toward orders per day): an exit must never wait on a cap. Each refusal names the figures,
so the ledger says exactly which limit and by how much. Fails closed: a cap that cannot be checked
refuses.

Usage (library; trade.py and approve.py call it):
    exp = caps.exposure(book, closes, equity)
    reason = caps.check_buy(exp, symbol, value, strategy, cfg)
"""
from __future__ import annotations

import dataclasses
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import traderpath  # noqa: E402,F401
import ledger  # noqa: E402

CENT = 0.005


@dataclasses.dataclass
class Exposure:
    equity: float
    cash: float
    values: dict[str, float]
    orders: int
    by_strategy: dict[str, float]

    @property
    def invested(self) -> float:
        return sum(self.values.values())


def exposure(book: ledger.Book, closes: dict[str, float], equity: float) -> Exposure:
    values: dict[str, float] = {}
    by_strategy: dict[str, float] = {}
    for lot in book.lots:
        value = lot.qty * closes[lot.symbol]
        values[lot.symbol] = values.get(lot.symbol, 0.0) + value
        by_strategy[lot.strategy or "none"] = by_strategy.get(lot.strategy or "none", 0.0) + value
    return Exposure(equity, book.cash, values, 0, by_strategy)


def check_buy(exp: Exposure, symbol: str, value: float, strategy: str, cfg: dict) -> str | None:
    if exp.orders + 1 > cfg["max_orders_per_day"]:
        return f"orders today {exp.orders + 1} above max_orders_per_day {cfg['max_orders_per_day']}"
    position = exp.values.get(symbol, 0.0) + value
    if position > cfg["max_position"] * exp.equity + CENT:
        return f"position {position:.2f} above max_position {cfg['max_position']:.0%} of equity {exp.equity:.2f}"
    if exp.invested + value > cfg["max_invested"] * exp.equity + CENT:
        return f"invested {exp.invested + value:.2f} above max_invested {cfg['max_invested']:.0%} of equity {exp.equity:.2f}"
    cash_after = exp.cash - value - cfg["commission_per_order"]
    if cash_after < cfg["min_cash"] * exp.equity - CENT:
        return f"cash after {cash_after:.2f} below min_cash {cfg['min_cash']:.0%} of equity {exp.equity:.2f}"
    budget = cfg["budgets"].get(strategy, 0.0) * exp.equity
    if exp.by_strategy.get(strategy, 0.0) + value > budget + CENT:
        return f"strategy {strategy} at {exp.by_strategy.get(strategy, 0.0) + value:.2f} above its budget {budget:.2f}"
    return None


def accept(exp: Exposure, symbol: str, value: float, strategy: str, cfg: dict) -> None:
    exp.values[symbol] = exp.values.get(symbol, 0.0) + value
    exp.by_strategy[strategy] = exp.by_strategy.get(strategy, 0.0) + value
    exp.cash -= value + cfg["commission_per_order"]
    exp.orders += 1


def count_sell(exp: Exposure) -> None:
    exp.orders += 1
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 harness/test_pretrade.py`
Expected: `7 of 7 passed`.

- [ ] **Step 5: Break it once, then restore**

In `bad_signals`, change `vix is None or vix > ...` to `vix is not None and vix > ...`: `test_throttle_counts_bad_signals_and_missing_ones_count_as_bad` must FAIL. Restore. In `check_buy`, change `+ CENT` in the position check to `+ 1.0`: `test_position_cap_refuses_one_cent_over_and_allows_at_it` must FAIL. Restore. Re-run: `7 of 7 passed`.

- [ ] **Step 6: Commit**

```bash
git add harness/sizing.py harness/risk_flags.py harness/caps.py harness/test_pretrade.py
git commit -m "Trader: sizing with a market-conditions throttle, risk flags, hard caps

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
git push
```

---

### Task 8: Strategies (`strategies/base.py`, `strategies/scout_calls.py`)

**Files:**
- Create: `bobs/trader/harness/strategies/base.py`, `bobs/trader/harness/strategies/scout_calls.py`
- Test: `bobs/trader/harness/test_scout_calls.py`

**Interfaces:**
- Consumes: `ledger.Book`, `scout_feed.open_calls`.
- Produces:
  - `strategies.base.Context` (dataclass: `session: str, today: dt.date, book: ledger.Book, feed: dict, rows: list[dict], cfg: dict, closes: dict[str, float]` (split-adjusted close per held symbol), `since: str` (first session the Trader ran in a non-off mode))
  - `strategies.base.order_id(session, strategy, side, symbol) -> str` (`f"{session}-{strategy}-{side}-{symbol}"`)
  - `strategies.scout_calls.NAME = "scout_calls"`, `exit_rules(cfg) -> dict`, `propose(ctx) -> list[dict]`
  - proposal dict: `{"order_id", "strategy", "symbol", "side", "qty" (sells: held qty; buys: None), "reason", "call_id"}` plus, for buys, `"card"` and `"exit_rules"`

- [ ] **Step 1: Write the failing tests**

Create `bobs/trader/harness/test_scout_calls.py`:

```python
#!/usr/bin/env python3
"""scout_calls: buys new open calls once, sells by the rules fixed at entry, sells first.

Run:  python3 harness/test_scout_calls.py
"""
from __future__ import annotations

import datetime as dt
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import traderpath  # noqa: E402,F401
import ledger  # noqa: E402
import testkit  # noqa: E402
from strategies import base, scout_calls  # noqa: E402

NOW = dt.datetime(2026, 10, 14, 6, 30, tzinfo=dt.timezone.utc)
CFG = {"stop_loss": 0.20, "take_profit": 0, "max_hold_days": 365}
RULES = {"stop_loss": 0.20, "take_profit": 0, "max_hold_days": 365}


def _call(symbol: str, session: str) -> dict:
    return {"kind": "call", "id": f"{symbol}-{session}-1", "symbol": symbol, "session": session,
            "card": {"rank_pct": 95.0}}


def _held(symbol: str, price: float, opened: str, rules: dict = RULES) -> list[dict]:
    order = f"{opened}-scout_calls-buy-{symbol}"
    return [ledger.line("proposed", NOW, opened, order_id=order, strategy="scout_calls", symbol=symbol, side="buy",
                        call_id=f"{symbol}-call", exit_rules=rules, decision="accepted"),
            ledger.line("filled", NOW, opened, order_id=order, symbol=symbol, side="buy", qty=10, price=price,
                        commission=1.0)]


def _ctx(rows: list[dict], calls: list[dict], closes: dict, session: str = "2026-10-13",
         withdrawn: set | None = None, since: str = "2026-10-01") -> base.Context:
    return base.Context(session=session, today=dt.date.fromisoformat(session), book=ledger.replay(rows),
                        feed={"calls": calls, "withdrawn": withdrawn or set()}, rows=rows, cfg=CFG,
                        closes=closes, since=since)


def test_new_call_is_bought_with_its_card_and_exit_rules():
    out = scout_calls.propose(_ctx([], [_call("AAA", "2026-10-13")], {}))
    assert [(p["side"], p["symbol"], p["order_id"]) for p in out] == [("buy", "AAA", "2026-10-13-scout_calls-buy-AAA")]
    assert out[0]["exit_rules"] == RULES and out[0]["card"]["rank_pct"] == 95.0 and out[0]["qty"] is None


def test_calls_before_the_trader_started_and_held_names_are_skipped():
    rows = _held("BBB", 100.0, "2026-10-05")
    calls = [_call("OLD", "2026-09-30"), _call("BBB", "2026-10-02")]
    assert scout_calls.propose(_ctx(rows, calls, {"BBB": 100.0})) == []


def test_refused_buy_is_proposed_again_later():
    refused = [ledger.line("proposed", NOW, "2026-10-12", order_id="2026-10-12-scout_calls-buy-AAA", strategy="scout_calls",
                           symbol="AAA", side="buy", call_id="AAA-2026-10-12-1", decision="refused")]
    out = scout_calls.propose(_ctx(refused, [_call("AAA", "2026-10-12")], {}))
    assert [p["order_id"] for p in out] == ["2026-10-13-scout_calls-buy-AAA"]
    accepted = [{**refused[0], "decision": "accepted"}]
    assert scout_calls.propose(_ctx(accepted, [_call("AAA", "2026-10-12")], {})) == []


def test_stop_on_the_close_sells_and_one_cent_above_holds():
    rows = _held("AAA", 100.0, "2026-10-01")
    assert scout_calls.propose(_ctx(rows, [], {"AAA": 80.01})) == []
    out = scout_calls.propose(_ctx(rows, [], {"AAA": 80.0}))
    assert out[0]["side"] == "sell" and out[0]["qty"] == 10 and out[0]["reason"].startswith("stop")


def test_target_time_and_withdrawal_sell():
    with_target = _held("AAA", 100.0, "2026-10-01", {**RULES, "take_profit": 0.5})
    assert scout_calls.propose(_ctx(with_target, [], {"AAA": 150.0}))[0]["reason"].startswith("target")
    old = _held("AAA", 100.0, "2025-10-13")
    assert scout_calls.propose(_ctx(old, [], {"AAA": 100.0}))[0]["reason"].startswith("held")
    fresh = _held("AAA", 100.0, "2026-10-01")
    out = scout_calls.propose(_ctx(fresh, [], {"AAA": 100.0}, withdrawn={"AAA-call"}))
    assert out[0]["reason"] == "the Scout withdrew the call"


def test_sells_come_before_buys():
    rows = _held("AAA", 100.0, "2026-10-01")
    out = scout_calls.propose(_ctx(rows, [_call("CCC", "2026-10-13")], {"AAA": 70.0}))
    assert [p["side"] for p in out] == ["sell", "buy"]


if __name__ == "__main__":
    raise SystemExit(testkit.run(globals()))
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 harness/test_scout_calls.py`
Expected: `ImportError: cannot import name 'base' from 'strategies'`.

- [ ] **Step 3: Write the implementation**

Create `bobs/trader/harness/strategies/base.py`:

```python
#!/usr/bin/env python3
"""What every strategy gets and returns. A strategy proposes; the core decides.

Why proposals only: sizing, flags, caps, reconcile and the approval queue live in the core, so a
new strategy (stage 2) cannot skip a guard by forgetting it. The order id is built from session,
strategy, side and symbol: a re-run of the same session produces the same ids, and trade.py drops
any id the ledger already has.

Usage (library):
    ctx = base.Context(...); proposals = scout_calls.propose(ctx)
"""
from __future__ import annotations

import dataclasses
import datetime as dt
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import traderpath  # noqa: E402,F401
import ledger  # noqa: E402


@dataclasses.dataclass
class Context:
    session: str
    today: dt.date
    book: ledger.Book
    feed: dict
    rows: list[dict]
    cfg: dict
    closes: dict[str, float]
    since: str


def order_id(session: str, strategy: str, side: str, symbol: str) -> str:
    return f"{session}-{strategy}-{side}-{symbol}"
```

Create `bobs/trader/harness/strategies/scout_calls.py`:

```python
#!/usr/bin/env python3
"""Stage 1 strategy: buy the Scout's open v2 calls, sell by the exit rules fixed at entry.

Why calls since the Trader started: switching the Trader on must not buy a year of old calls at
once. Why a refused buy is proposed again: a refusal for HALT, a daily loss or a stale price is
about that day, not about the call; only an accepted proposal ends the call's entry.
Exit rules are copied onto the buy when it is proposed, so changing them in bob.toml later never
moves the stop of a position already held. Order: the Scout withdrew the call, the stop on the
split-adjusted close, the target, the holding limit.

Usage (library; trade.py calls propose(ctx)).
"""
from __future__ import annotations

import datetime as dt
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import traderpath  # noqa: E402,F401
import scout_feed  # noqa: E402
from strategies.base import Context, order_id  # noqa: E402

NAME = "scout_calls"
TOLERANCE = 1e-9


def exit_rules(cfg: dict) -> dict:
    return {"stop_loss": cfg["stop_loss"], "take_profit": cfg["take_profit"], "max_hold_days": cfg["max_hold_days"]}


def _exit_reason(close: float | None, entry: float, opened: str, today: dt.date, rules: dict,
                 withdrawn: bool) -> str | None:
    if withdrawn:
        return "the Scout withdrew the call"
    stop = entry * (1 - rules["stop_loss"])
    if close is not None and close <= stop + TOLERANCE:
        return f"stop: close {close:.2f} at or below {stop:.2f}"
    target = entry * (1 + rules["take_profit"])
    if rules.get("take_profit") and close is not None and close >= target - TOLERANCE:
        return f"target: close {close:.2f} at or above {target:.2f}"
    if (today - dt.date.fromisoformat(opened)).days >= rules["max_hold_days"]:
        return f"held {rules['max_hold_days']} days"
    return None


def sells(ctx: Context) -> list[dict]:
    out = []
    for symbol in sorted({lot.symbol for lot in ctx.book.lots if lot.strategy == NAME}):
        lots = [lot for lot in ctx.book.lots_of(symbol) if lot.strategy == NAME]
        qty = round(sum(lot.qty for lot in lots), 6)
        entry = sum(lot.qty * lot.price for lot in lots) / qty
        first = lots[0]
        reason = _exit_reason(ctx.closes.get(symbol), entry, first.opened, ctx.today, first.exit_rules,
                              first.call_id in ctx.feed["withdrawn"])
        if reason:
            out.append({"order_id": order_id(ctx.session, NAME, "sell", symbol), "strategy": NAME, "symbol": symbol,
                        "side": "sell", "qty": qty, "reason": reason, "call_id": first.call_id})
    return out


def buys(ctx: Context) -> list[dict]:
    held = set(ctx.book.positions())
    entered = {r.get("call_id") for r in ctx.rows if r.get("kind") == "proposed" and r.get("side") == "buy"
               and r.get("decision") == "accepted"}
    out = []
    for call in scout_feed.open_calls(ctx.feed, ctx.today):
        if call["symbol"] in held or call["id"] in entered or call["session"] < ctx.since:
            continue
        out.append({"order_id": order_id(ctx.session, NAME, "buy", call["symbol"]), "strategy": NAME,
                    "symbol": call["symbol"], "side": "buy", "qty": None, "reason": f"Scout call {call['id']}",
                    "call_id": call["id"], "card": call["card"], "exit_rules": exit_rules(ctx.cfg)})
    return out


def propose(ctx: Context) -> list[dict]:
    return sells(ctx) + buys(ctx)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 harness/test_scout_calls.py`
Expected: `6 of 6 passed`.

- [ ] **Step 5: Break it once, then restore**

In `buys`, remove `and r.get("decision") == "accepted"`: `test_refused_buy_is_proposed_again_later` must FAIL. Restore. In `_exit_reason`, change `close <= stop + TOLERANCE` to `close < stop`: `test_stop_on_the_close_sells_and_one_cent_above_holds` must FAIL. Restore. Re-run: `6 of 6 passed`.

- [ ] **Step 6: Commit**

```bash
git add harness/strategies harness/test_scout_calls.py
git commit -m "Trader: scout_calls strategy, exit rules fixed at entry

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
git push
```

---

### Task 9: Mode history and the go-live report (`promotion.py`)

**Files:**
- Create: `bobs/trader/harness/promotion.py`
- Test: `bobs/trader/harness/test_promotion.py`

**Interfaces:**
- Consumes: `store.*`, `ledger.read`, `ledger.replay`, `ledger.unresolved_mismatches`, `bars.load`, `tradercfg.load()`, `tradercfg.fingerprint`, `tradercfg.CAPS`, `tradercfg.BAR`, `tradercfg.state_dir()`.
- Produces:
  - `promotion.history_path()`, `promotion.read_history() -> list[dict]`
  - `promotion.record_run(now, session, cfg, result: str, equity: float | None) -> None` (appends `{at, session, mode, caps, bar, result, equity}`)
  - `promotion.first_active_session(history) -> str | None`, `promotion.last_ok_equity(history) -> float | None`
  - `promotion.report(now, cfg=None, rows=None, history=None) -> dict` (`{"at", "passed": bool, "criteria": [{"name", "status": "PASS"|"FAIL"|"could not tell", "detail"}], "window": {...}}`)
  - `promotion.write(report) -> Path` (`state/promotion/<date>.json`), `promotion.latest_passing(now, days=30) -> bool`
  - CLI `--report`

- [ ] **Step 1: Write the failing tests**

Create `bobs/trader/harness/test_promotion.py`:

```python
#!/usr/bin/env python3
"""promotion: every criterion PASS or the report fails; changing the bar restarts the clock.

Run:  python3 harness/test_promotion.py
"""
from __future__ import annotations

import datetime as dt
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import traderpath  # noqa: E402,F401
import ledger  # noqa: E402
import promotion  # noqa: E402
import testkit  # noqa: E402
import tradercfg  # noqa: E402

NOW = dt.datetime(2026, 10, 14, 6, 30, tzinfo=dt.timezone.utc)
START = dt.date(2026, 3, 1)


def _days(n: int) -> list[str]:
    return [(START + dt.timedelta(days=i)).isoformat() for i in range(n)]


def _history(cfg: dict, days: list[str], end_equity: float = 110_000.0, bar: str | None = None) -> list[dict]:
    fp = bar or tradercfg.fingerprint(cfg, tradercfg.BAR)
    n = len(days)
    return [{"at": d, "session": d, "mode": "paper", "caps": "c", "bar": fp, "result": "OK",
             "equity": 100_000.0 + (end_equity - 100_000.0) * i / (n - 1)} for i, d in enumerate(days)]


def _trades(days: list[str], n: int = 20) -> list[dict]:
    rows = []
    for i in range(n):
        day = days[1 + i]
        oid = f"{day}-scout_calls-buy-S{i}"
        rows += [ledger.line("proposed", NOW, day, order_id=oid, strategy="scout_calls", symbol=f"S{i}", side="buy",
                             caps="ok", decision="accepted"),
                 ledger.line("submitted", NOW, day, order_id=oid),
                 ledger.line("filled", NOW, day, order_id=oid, symbol=f"S{i}", side="buy", qty=1, price=100.0, commission=1.0),
                 ledger.line("filled", NOW, days[-2], order_id=f"sell-{i}", symbol=f"S{i}", side="sell", qty=1,
                             price=101.0, commission=1.0)]
    return rows


def _spy(bob: pathlib.Path, days: list[str], end: float = 420.0) -> None:
    n = len(days)
    testkit.put(bob / "data" / "state" / "bars" / "SPY.json",
                {"raw": [], "split": [], "evidence": [], "all": [[d, 400.0 + (end - 400.0) * i / (n - 1)] for i, d in enumerate(days)]})


def _status(report: dict) -> dict:
    return {c["name"]: c["status"] for c in report["criteria"]}


def test_a_clean_record_passes_every_criterion():
    bob = testkit.temp_bob()
    cfg, days = tradercfg.load(), _days(200)
    _spy(bob, days)
    r = promotion.report(NOW, cfg, _trades(days), _history(cfg, days))
    assert r["passed"] and set(_status(r).values()) == {"PASS"}, r


def test_too_short_and_too_few_trades_fail():
    bob = testkit.temp_bob()
    cfg, days = tradercfg.load(), _days(150)
    _spy(bob, days)
    r = promotion.report(NOW, cfg, _trades(days, 19), _history(cfg, days))
    assert _status(r)["time"] == "FAIL" and _status(r)["trades"] == "FAIL" and not r["passed"]


def test_losing_to_spy_or_a_deeper_drawdown_fails():
    bob = testkit.temp_bob()
    cfg, days = tradercfg.load(), _days(200)
    _spy(bob, days, end=460.0)
    assert _status(promotion.report(NOW, cfg, _trades(days), _history(cfg, days)))["return"] == "FAIL"
    _spy(bob, days)
    history = _history(cfg, days)
    history[100]["equity"] = 80_000.0
    assert _status(promotion.report(NOW, cfg, _trades(days), history))["drawdown"] == "FAIL"


def test_a_submitted_order_without_a_passing_cap_check_fails_the_guards():
    bob = testkit.temp_bob()
    cfg, days = tradercfg.load(), _days(200)
    _spy(bob, days)
    rows = _trades(days) + [ledger.line("submitted", NOW, days[5], order_id="sneaky")]
    assert _status(promotion.report(NOW, cfg, rows, _history(cfg, days)))["guards"] == "FAIL"
    rows = _trades(days) + [ledger.line("mismatch", NOW, days[5], details=["x"])]
    assert _status(promotion.report(NOW, cfg, rows, _history(cfg, days)))["guards"] == "FAIL"


def test_missing_spy_is_could_not_tell_and_does_not_pass():
    testkit.temp_bob()
    cfg, days = tradercfg.load(), _days(200)
    r = promotion.report(NOW, cfg, _trades(days), _history(cfg, days))
    assert _status(r)["return"] == "could not tell" and not r["passed"]


def test_changing_the_bar_restarts_the_clock():
    bob = testkit.temp_bob()
    cfg, days = tradercfg.load(), _days(200)
    _spy(bob, days)
    history = _history(cfg, days[:100], bar="old bar") + _history(cfg, days[100:])
    assert _status(promotion.report(NOW, cfg, _trades(days[100:]), history))["time"] == "FAIL"


def test_latest_passing_looks_back_thirty_days():
    testkit.temp_bob()
    promotion.write({"at": "2026-09-10T06:00:00+00:00", "passed": True, "criteria": []})
    assert not promotion.latest_passing(NOW)
    promotion.write({"at": "2026-09-20T06:00:00+00:00", "passed": True, "criteria": []})
    assert promotion.latest_passing(NOW)


def test_record_run_and_history_helpers():
    testkit.temp_bob()
    cfg = tradercfg.load()
    promotion.record_run(NOW, None, cfg, "OK", None)
    promotion.record_run(NOW, "2026-10-12", {**cfg, "mode": "simulate"}, "OK", 100_000.0)
    promotion.record_run(NOW, "2026-10-13", {**cfg, "mode": "simulate"}, "FAIL", None)
    history = promotion.read_history()
    assert [h["mode"] for h in history] == ["off", "simulate", "simulate"]
    assert promotion.first_active_session(history) == "2026-10-12" and promotion.last_ok_equity(history) == 100_000.0


if __name__ == "__main__":
    raise SystemExit(testkit.run(globals()))
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 harness/test_promotion.py`
Expected: `ModuleNotFoundError: No module named 'promotion'`.

- [ ] **Step 3: Write the implementation**

Create `bobs/trader/harness/promotion.py`:

```python
#!/usr/bin/env python3
"""The run history and the go-live report: is the paper record good enough to consider live?

The bar was agreed before paper started and lives in bob.toml: at least min_paper_days in paper and
min_closed_trades closed trades; return after a cost estimate above SPY's total return over the same
dates; maximum drawdown no worse than SPY's; zero submitted orders without a passing cap check and
zero mismatches a person has not cleared. Every criterion is PASS, FAIL or could not tell; the
report passes only when all are PASS. Changing the bar restarts the paper clock, so the bar cannot
be moved to fit the record. The report never changes the mode; a person does.

Why costs again on top of paper fills: paper fills are usually better than real ones.

Usage:
    python3 harness/promotion.py --report     # writes state/promotion/<date>.json, prints each criterion
"""
from __future__ import annotations

import argparse
import datetime as dt
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import traderpath  # noqa: E402,F401
import bars  # noqa: E402
import fetch  # noqa: E402
import ledger  # noqa: E402
import store  # noqa: E402
import tradercfg  # noqa: E402

LOOKBACK_DAYS = 30


def history_path() -> pathlib.Path:
    return tradercfg.state_dir() / "mode_history.jsonl"


def read_history() -> list[dict]:
    return store.read_jsonl(history_path())[0]


def record_run(now: dt.datetime, session: str | None, cfg: dict, result: str, equity: float | None) -> None:
    store.append_jsonl(history_path(), [{
        "at": now.isoformat(timespec="seconds"), "session": session, "mode": cfg["mode"],
        "caps": tradercfg.fingerprint(cfg, tradercfg.CAPS), "bar": tradercfg.fingerprint(cfg, tradercfg.BAR),
        "result": result, "equity": equity}])


def first_active_session(history: list[dict]) -> str | None:
    return next((h["session"] for h in history if h["mode"] != "off" and h.get("session")), None)


def last_ok_equity(history: list[dict]) -> float | None:
    return next((h["equity"] for h in reversed(history) if h["result"] == "OK" and h.get("equity") is not None), None)


def _window(history: list[dict], bar: str) -> list[dict]:
    start = next((i for i, h in enumerate(history) if h["mode"] == "paper" and h["bar"] == bar), None)
    if start is None:
        return []
    by_session = {h["session"]: h for h in history[start:]
                  if h["mode"] == "paper" and h["bar"] == bar and h["result"] == "OK" and h.get("equity") is not None}
    return [by_session[s] for s in sorted(by_session)]


def _drawdown(values: list[float]) -> float:
    peak, worst = values[0], 0.0
    for v in values:
        peak = max(peak, v)
        worst = max(worst, (peak - v) / peak)
    return worst


def _crit(name: str, ok: bool | None, detail: str) -> dict:
    return {"name": name, "status": "could not tell" if ok is None else "PASS" if ok else "FAIL", "detail": detail}


def report(now: dt.datetime, cfg: dict | None = None, rows: list[dict] | None = None,
           history: list[dict] | None = None) -> dict:
    cfg = cfg or tradercfg.load()
    rows = ledger.read()[0] if rows is None else rows
    history = read_history() if history is None else history
    window = _window(history, tradercfg.fingerprint(cfg, tradercfg.BAR))
    criteria: list[dict] = []
    if len(window) < 2:
        criteria = [_crit(n, None, "no paper runs under the current bar") for n in ("time", "trades", "return", "drawdown")]
    else:
        first, last = window[0]["session"], window[-1]["session"]
        days = (dt.date.fromisoformat(last) - dt.date.fromisoformat(first)).days
        criteria.append(_crit("time", days >= cfg["min_paper_days"], f"{days} days in paper, {cfg['min_paper_days']} needed"))
        closed = [c for c in ledger.replay(rows).closed if c["full"] and first <= c["closed"] <= last]
        criteria.append(_crit("trades", len(closed) >= cfg["min_closed_trades"],
                              f"{len(closed)} closed trades, {cfg['min_closed_trades']} needed"))
        extra: dict[str, float] = {}
        for r in rows:
            if r.get("kind") == "filled" and first <= r["session"] <= last:
                extra[r["session"]] = extra.get(r["session"], 0.0) + cfg["commission_per_order"] + \
                    r["qty"] * r["price"] * cfg["slippage_bps"] / 10_000
        curve = [h["equity"] - sum(v for s, v in extra.items() if s <= h["session"]) for h in window]
        spy = {d: c for d, c in bars.load(bars.BENCHMARK)["all"]}
        spy_curve = [spy.get(h["session"]) for h in window]
        if None in spy_curve:
            criteria += [_crit("return", None, "no SPY total-return close for every paper session"),
                         _crit("drawdown", None, "no SPY total-return close for every paper session")]
        else:
            mine, theirs = curve[-1] / curve[0] - 1, spy_curve[-1] / spy_curve[0] - 1
            criteria.append(_crit("return", mine > theirs, f"after costs {mine:+.2%} vs SPY {theirs:+.2%}"))
            dd_mine, dd_spy = _drawdown(curve), _drawdown(spy_curve)
            criteria.append(_crit("drawdown", dd_mine <= dd_spy, f"max drawdown {dd_mine:.2%} vs SPY {dd_spy:.2%}"))
    checked = {r["order_id"] for r in rows if r.get("kind") == "proposed" and r.get("caps") in ("ok", "exempt")}
    breaches = [r["order_id"] for r in rows if r.get("kind") == "submitted" and r["order_id"] not in checked]
    open_ = ledger.unresolved_mismatches(rows)
    criteria.append(_crit("guards", not breaches and not open_,
                          f"{len(breaches)} orders without a cap check, {len(open_)} uncleared mismatches"))
    return {"at": now.isoformat(timespec="seconds"), "passed": all(c["status"] == "PASS" for c in criteria),
            "criteria": criteria,
            "window": {"first": window[0]["session"] if window else None, "last": window[-1]["session"] if window else None}}


def folder() -> pathlib.Path:
    return tradercfg.state_dir() / "promotion"


def write(doc: dict) -> pathlib.Path:
    path = folder() / f"{doc['at'][:10]}.json"
    store.write_json(path, doc)
    return path


def latest_passing(now: dt.datetime, days: int = LOOKBACK_DAYS) -> bool:
    cutoff = (now.date() - dt.timedelta(days=days)).isoformat()
    return any(p.stem >= cutoff and store.read_json(p, {}).get("passed") is True for p in folder().glob("*.json"))


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--report", action="store_true", required=True)
    ap.parse_args(argv)
    doc = report(fetch.utcnow())
    print(f"OK report {write(doc)} passed={'yes' if doc['passed'] else 'no'}")
    for c in doc["criteria"]:
        print(f"  {c['name']}: {c['status']} ({c['detail']})")
    return 0


if __name__ == "__main__":
    import oscompat
    oscompat.utf8_stdio()
    raise SystemExit(main(sys.argv[1:]))
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 harness/test_promotion.py`
Expected: `8 of 8 passed`.

- [ ] **Step 5: Break it once, then restore**

In `_window`, drop `and h["bar"] == bar` from the `by_session` filter: `test_changing_the_bar_restarts_the_clock` must FAIL. Restore. In `report`, change `all(c["status"] == "PASS" ...)` to `not any(c["status"] == "FAIL" ...)`: `test_missing_spy_is_could_not_tell_and_does_not_pass` must FAIL. Restore. Re-run: `8 of 8 passed`.

- [ ] **Step 6: Commit**

```bash
git add harness/promotion.py harness/test_promotion.py
git commit -m "Trader: run history and the go-live report against the bar in bob.toml

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
git push
```

---

### Task 10: The daily pipeline (`trade.py`) and the brake (`halt.py`)

**Files:**
- Create: `bobs/trader/harness/trade.py`, `bobs/trader/harness/halt.py`
- Test: `bobs/trader/harness/test_trade.py`

**Interfaces:**
- Consumes: everything from Tasks 2 to 9.
- Produces:
  - `trade.run(now: dt.datetime, adapter=broker.base.adapter_for) -> tuple[int, str]`
  - `trade.halt_path() -> Path` (`state/HALT`)
  - `trade.equity_of(book, closes) -> float`
  - ledger `proposed` lines carry `order_id, strategy, symbol, side, call_id, reason, decision ("accepted"|"refused"), caps ("ok"|"exempt"|"refused"|None), qty, price, sizing, flags, card, macro, exit_rules`
  - `halt.halt(reason: str, now) -> Path`; CLI `halt.py --reason TEXT`

- [ ] **Step 1: Write the failing tests**

Create `bobs/trader/harness/test_trade.py`:

```python
#!/usr/bin/env python3
"""trade: off means off; the books always match the simulator; exits run under every brake.

Run:  python3 harness/test_trade.py
"""
from __future__ import annotations

import datetime as dt
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import traderpath  # noqa: E402,F401
import ledger  # noqa: E402
import promotion  # noqa: E402
import testkit  # noqa: E402
import trade  # noqa: E402
from broker import sim  # noqa: E402

SIMULATE = {'mode = "off"': 'mode = "simulate"'}
LIVE = {'mode = "off"': 'mode = "live"'}
CALM = {"series": {"VIXCLS": {"value": 15.0, "stale": False}, "T10Y2Y": {"value": 0.5, "stale": False},
                   "BAMLH0A0HYM2": {"value": 3.0, "stale": False}, "NFCI": {"value": -0.5, "stale": False}}}


def _sessions(n: int = 70) -> list[str]:
    day, out = dt.date(2026, 1, 5), []
    while len(out) < n:
        if day.weekday() < 5:
            out.append(day.isoformat())
        day += dt.timedelta(days=1)
    return out


DAYS = _sessions()


def _now(k: int) -> dt.datetime:
    return dt.datetime.combine(dt.date.fromisoformat(DAYS[k]) + dt.timedelta(days=1), dt.time(6, 30), dt.timezone.utc)


def _price(symbol: str, k: int) -> tuple[float, float]:
    """(raw close, split-adjusted close as known on day k is computed by the caller)."""
    if symbol == "AAA":
        return (100.0 if k < 40 else 70.0), 1.0
    if symbol == "BBB":
        return (100.0 if k < 45 else 50.0), (2.0 if k < 45 else 1.0)
    if symbol == "SPY":
        return 400.0, 1.0
    return 100.0, 1.0


def _write_bars(bob: pathlib.Path, upto: int, symbols=("SPY", "AAA", "BBB", "CCC")) -> None:
    folder = bob / "data" / "state" / "bars"
    testkit.put(folder / "_calendar.json", {"sessions": [{"date": d, "close": "16:00"} for d in DAYS]})
    for symbol in symbols:
        raw, split = [], []
        for k in range(upto + 1):
            close, factor = _price(symbol, k)
            raw.append([DAYS[k], close, close, close, close, 1_000_000])
            split_happened = symbol == "BBB" and upto >= 45
            split.append([DAYS[k], close / factor if split_happened else close])
        testkit.put(folder / f"{symbol}.json", {"raw": raw, "split": split, "all": [[d, c] for d, *_, c, _ in raw],
                                                "evidence": []})


def _scout(bob: pathlib.Path, rows: list[dict], macro: dict | None = CALM) -> None:
    state = testkit.scout_state(bob)
    testkit.put(state / "calls.jsonl", "".join(json.dumps(r) + "\n" for r in rows))
    if macro is not None:
        testkit.put(state / "macro.json", macro)


def _call(symbol: str, k: int) -> dict:
    return {"kind": "call", "id": f"{symbol}-{DAYS[k]}-1", "ticker": symbol, "symbol": symbol, "session": DAYS[k],
            "card": {"rank_pct": 95.0, "groups": {"insider": None}}}


def _never(mode):
    raise AssertionError("mode off must not touch a broker")


def test_mode_off_touches_nothing():
    bob = testkit.temp_bob()
    assert trade.run(_now(30), adapter=_never) == (0, "OK mode=off")
    assert not ledger.path().exists() and not (bob / "data" / "state" / "sim").exists()
    assert promotion.read_history()[-1]["mode"] == "off"


def test_live_needs_a_passing_report_or_an_override():
    testkit.temp_bob(LIVE)
    code, message = trade.run(_now(30), adapter=_never)
    assert code == 1 and "go-live report" in message
    testkit.temp_bob({**LIVE, 'override_promotion = ""': 'override_promotion = "person accepts the risk"'})
    code, message = trade.run(_now(30))
    assert code == 1 and "no broker adapter" in message


def test_sixty_sessions_end_to_end_books_match_the_simulator_every_day():
    bob = testkit.temp_bob(SIMULATE)
    calls: list[dict] = []
    for k in range(5, 66):
        if k == 25:
            calls.append(_call("AAA", 25))
        if k == 30:
            calls.append(_call("BBB", 30))
        if k == 35:
            calls.append(_call("CCC", 35))
        if k == 50:
            calls.append({"kind": "withdrawn", "call_id": f"CCC-{DAYS[35]}-1"})
        _write_bars(bob, k)
        _scout(bob, calls)
        code, message = trade.run(_now(k))
        assert code == 0, (k, message)
        broker = sim.Simulator("simulate")
        book = ledger.replay(ledger.read()[0])
        assert book.positions() == broker.positions() and abs(book.cash - broker.account()["cash"]) < 0.005, k
    rows = ledger.read()[0]
    sells = {r["symbol"]: r["reason"] for r in rows if r["kind"] == "proposed" and r["side"] == "sell"}
    assert sells["AAA"].startswith("stop") and sells["CCC"] == "the Scout withdrew the call", sells
    assert "BBB" not in sells and ledger.replay(rows).positions()["BBB"] > 0
    assert any(r["kind"] == "split" and r["symbol"] == "BBB" for r in rows)


def _ready(k: int = 30, calls: list[dict] | None = None, macro: dict | None = CALM) -> pathlib.Path:
    bob = testkit.temp_bob(SIMULATE)
    _write_bars(bob, k)
    _scout(bob, calls if calls is not None else [], macro)
    return bob


def test_rerun_of_the_same_session_places_nothing_new():
    _ready(calls=[_call("AAA", 30)])
    assert "placed=1" in trade.run(_now(30))[1]
    assert "placed=0 refused=0" in trade.run(_now(30))[1]


def test_collect_is_idempotent_by_activity_id():
    bob = _ready(calls=[_call("AAA", 30)])
    trade.run(_now(30))
    _write_bars(bob, 31)
    trade.run(_now(31))
    trade.run(_now(31))
    fills = [r for r in ledger.read()[0] if r["kind"] == "filled"]
    assert len(fills) == 1


def test_halt_refuses_buys_but_sells_still_run():
    bob = _ready(k=39, calls=[_call("AAA", 30)])
    for k in range(30, 40):
        _write_bars(bob, k)
        trade.run(_now(k))
    trade.halt_path().write_text("2026-03-01 person: stop buying\n", encoding="utf-8")
    _scout(bob, [_call("AAA", 30), _call("CCC", 40)])
    _write_bars(bob, 40)
    code, message = trade.run(_now(40))
    rows = ledger.read()[0]
    today = [r for r in rows if r["kind"] == "proposed" and r["session"] == DAYS[40]]
    assert code == 0 and {(r["side"], r["decision"]) for r in today} == {("sell", "accepted"), ("buy", "refused")}
    assert any(r["kind"] == "refused" and r["reason"].startswith("HALT") for r in rows)


def test_daily_loss_limit_refuses_buys():
    _ready(calls=[_call("AAA", 30)])
    promotion.record_run(_now(29), DAYS[29], {"mode": "simulate", **_cfg()}, "OK", 200_000.0)
    trade.run(_now(30))
    refused = [r for r in ledger.read()[0] if r["kind"] == "refused"]
    assert refused and "down more than" in refused[0]["reason"]


def _cfg() -> dict:
    import tradercfg
    return {k: v for k, v in tradercfg.load().items() if k != "mode"}


def test_stale_held_symbol_fails_the_run():
    bob = _ready(calls=[_call("AAA", 30)])
    trade.run(_now(30))
    _write_bars(bob, 31)
    trade.run(_now(31))
    _write_bars(bob, 32, symbols=("SPY", "BBB", "CCC"))
    code, message = trade.run(_now(32))
    assert code == 1 and "AAA" in message


def test_stale_candidate_is_refused_not_fatal():
    bob = _ready(calls=[_call("AAA", 30)])
    _write_bars(bob, 29, symbols=("AAA",))
    code, message = trade.run(_now(30))
    refused = [r for r in ledger.read()[0] if r["kind"] == "refused"]
    assert code == 0 and refused and "no 2026" in refused[0]["reason"]


def test_mismatch_stops_trading_and_is_recorded_once():
    bob = _ready(calls=[_call("AAA", 30)])
    trade.run(_now(30))
    state = bob / "data" / "state" / "sim" / "broker.json"
    doc = json.loads(state.read_text(encoding="utf-8"))
    doc["cash"] += 10.0
    state.write_text(json.dumps(doc), encoding="utf-8")
    assert trade.run(_now(30))[0] == 1
    assert trade.run(_now(30))[0] == 1
    assert len([r for r in ledger.read()[0] if r["kind"] == "mismatch"]) == 1


def test_missing_market_conditions_size_at_the_floor():
    _ready(calls=[_call("AAA", 30)], macro=None)
    trade.run(_now(30))
    proposed = [r for r in ledger.read()[0] if r["kind"] == "proposed"][0]
    assert proposed["sizing"]["throttle"] == 0.25 and "vix" in proposed["flags"]


def test_orders_per_day_cap_refuses_the_extra_buy():
    bob = testkit.temp_bob({**SIMULATE, "max_orders_per_day = 10": "max_orders_per_day = 1"})
    _write_bars(bob, 30)
    _scout(bob, [_call("AAA", 30), _call("BBB", 30)])
    code, message = trade.run(_now(30))
    assert code == 0 and "placed=1 refused=1" in message
    refused = [r for r in ledger.read()[0] if r["kind"] == "refused"]
    assert "max_orders_per_day" in refused[0]["reason"]


if __name__ == "__main__":
    raise SystemExit(testkit.run(globals()))
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 harness/test_trade.py`
Expected: `ModuleNotFoundError: No module named 'trade'`.

- [ ] **Step 3: Write the implementation**

Create `bobs/trader/harness/trade.py`:

```python
#!/usr/bin/env python3
"""The Trader's daily run: collect, reconcile, check inputs, propose, flag, size, cap, act, record.

Why this order: fills first, so the books include last night's orders before they are compared;
reconcile before anything new, because an order placed on books that disagree with the broker
compounds the error; sells before buys, so exits never wait on cash or caps. Every decision is a
ledger line, refusals included: a refused order is an answer, not something to work around.

Stops on: mode off (no broker or network call at all); live without a passing go-live report or an
override; a missing calendar; any reconcile difference; an unreadable Scout ledger; a stale bar
for SPY or for a held symbol. A stale bar for a buy candidate refuses that buy only, so one halted
stock never blocks the exits of the whole book. HALT and the daily loss limit stop buys, not sells.

Usage:
    python3 harness/trade.py --run      # OK session=... placed=N refused=M queued=K, or FAIL: ...
"""
from __future__ import annotations

import argparse
import datetime as dt
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import traderpath  # noqa: E402,F401
import bars  # noqa: E402
import caps  # noqa: E402
import fetch  # noqa: E402
import ledger  # noqa: E402
import marketclock  # noqa: E402
import promotion  # noqa: E402
import reconcile  # noqa: E402
import risk_flags  # noqa: E402
import scout_feed  # noqa: E402
import sizing  # noqa: E402
import tradercfg  # noqa: E402
from broker import base as broker_base  # noqa: E402
from strategies import base as strategy_base, scout_calls  # noqa: E402

STRATEGIES = (scout_calls,)


def halt_path() -> pathlib.Path:
    return tradercfg.state_dir() / "HALT"


def equity_of(book: ledger.Book, closes: dict[str, float]) -> float:
    return round(book.cash + sum(q * closes[s] for s, q in book.positions().items()), 2)


def run(now: dt.datetime, adapter=broker_base.adapter_for) -> tuple[int, str]:
    cfg = tradercfg.load()
    if cfg["mode"] == "off":
        promotion.record_run(now, None, cfg, "OK", None)
        return 0, "OK mode=off"
    history = promotion.read_history()
    code, message, session, equity = _run(now, cfg, adapter, history)
    promotion.record_run(now, session, cfg, "OK" if code == 0 else "FAIL", equity)
    return code, message


def _collect(now: dt.datetime, session: str, broker, cfg: dict) -> None:
    rows, _ = ledger.read()
    new = []
    if cfg["mode"] == "simulate" and not any(r.get("kind") == "cash" for r in rows):
        new.append(ledger.line("cash", now, session, amount=float(cfg["sim_starting_cash"]),
                               note="simulator starting cash"))
    seen = {r.get("activity_id") for r in rows}
    for a in broker.activities():
        if a["activity_id"] not in seen:
            new.append(ledger.line(a["kind"], now, a["session"], **{k: v for k, v in a.items() if k not in ("kind", "session")}))
    if new:
        ledger.append(new)


def _no_buys(equity: float, history: list[dict], cfg: dict) -> str | None:
    if halt_path().exists():
        return "HALT: " + (halt_path().read_text(encoding="utf-8").strip().splitlines() or ["no reason given"])[-1]
    prev = promotion.last_ok_equity(history)
    if prev and equity < prev * (1 - cfg["daily_loss_limit"]):
        return f"equity {equity:.2f} down more than {cfg['daily_loss_limit']:.0%} since the last run ({prev:.2f})"
    return None


def _buy(p: dict, cfg: dict, exp: caps.Exposure, equity: float, blocked: str | None, buy_pct, macro: dict,
         session: str) -> tuple[dict, str | None]:
    info = {"card": p["card"], "exit_rules": p["exit_rules"], "macro": macro, "caps": None, "qty": None,
            "price": None, "sizing": None, "flags": []}
    if blocked:
        return info, blocked
    if not bars.fresh(p["symbol"], session):
        return info, f"no {session} bar for {p['symbol']}"
    price = bars.close_on(p["symbol"], session)
    sized = sizing.size(equity, price, p["card"].get("rank_pct"), macro, cfg, buy_pct)
    found = risk_flags.flags(p["card"], macro, sized["value"], bars.adv(p["symbol"]), cfg)
    info.update(qty=sized["qty"], price=price, sizing=sized, flags=found)
    if sized["qty"] < 1 or sized["value"] < cfg["min_order_value"]:
        return info, f"order value {sized['value']:.2f} below min_order_value {cfg['min_order_value']}"
    stop = risk_flags.blocking(found, cfg)
    if stop:
        return info, f"blocked by flags {stop}"
    breach = caps.check_buy(exp, p["symbol"], sized["value"], p["strategy"], cfg)
    if breach:
        info["caps"] = "refused"
        return info, breach
    caps.accept(exp, p["symbol"], sized["value"], p["strategy"], cfg)
    info["caps"] = "ok"
    return info, None


def _act(now: dt.datetime, session: str, p: dict, info: dict, why: str | None, mode: str, broker) -> str:
    proposed = ledger.line("proposed", now, session, order_id=p["order_id"], strategy=p["strategy"], symbol=p["symbol"],
                           side=p["side"], call_id=p.get("call_id"), reason=p["reason"],
                           decision="refused" if why else "accepted", **info)
    if why:
        ledger.append([proposed, ledger.line("refused", now, session, order_id=p["order_id"], reason=why)])
        return "refused"
    if mode == "live":
        ledger.append([proposed, ledger.line("queued", now, session, order_id=p["order_id"])])
        return "queued"
    ledger.append([proposed])
    broker.submit({"order_id": p["order_id"], "symbol": p["symbol"], "side": p["side"], "qty": info["qty"],
                   "session": session})
    ledger.append([ledger.line("submitted", now, session, order_id=p["order_id"])])
    return "placed"


def _run(now: dt.datetime, cfg: dict, adapter, history: list[dict]) -> tuple[int, str, str | None, float | None]:
    mode, warn = cfg["mode"], ""
    if mode == "live" and not cfg["override_promotion"] and not promotion.latest_passing(now):
        return 1, ("FAIL: live needs a passing go-live report from the last 30 days (promotion.py --report) "
                   "or override_promotion in bob.toml"), None, None
    if mode == "live" and cfg["override_promotion"]:
        warn = f" WARN override_promotion: {cfg['override_promotion']}"
    try:
        broker = adapter(mode)
    except broker_base.AdapterError as e:
        return 1, f"FAIL: {e}", None, None
    session = marketclock.last_session(bars.sessions(), now)
    if session is None:
        return 1, "FAIL: no market calendar; run bars.py --refresh", None, None
    _collect(now, session, broker, cfg)
    rows, _ = ledger.read()
    problems = reconcile.check(rows, broker)
    if problems:
        if not ledger.unresolved_mismatches(rows):
            ledger.append([ledger.line("mismatch", now, session, details=problems)])
        return 1, "FAIL: reconcile " + "; ".join(problems) + " (a person clears it with ledger.py correct)", session, None
    try:
        feed = scout_feed.load()
    except scout_feed.FeedError as e:
        return 1, f"FAIL: {e}", session, None
    book = ledger.replay(rows)
    held = sorted(book.positions())
    stale = [s for s in [bars.BENCHMARK, *held] if not bars.fresh(s, session)]
    if stale:
        return 1, f"FAIL: no {session} bar for {', '.join(stale)}; run bars.py --refresh", session, None
    closes = {s: bars.close_on(s, session) for s in held}
    equity = equity_of(book, closes)
    blocked = _no_buys(equity, history, cfg)
    ctx = strategy_base.Context(session=session, today=dt.date.fromisoformat(session), book=book, feed=feed, rows=rows,
                                cfg=cfg, closes={s: bars.close_on(s, session, "split") for s in held},
                                since=promotion.first_active_session(history) or session)
    done = {r.get("order_id") for r in rows}
    proposals = [p for strategy in STRATEGIES for p in strategy.propose(ctx) if p["order_id"] not in done]
    exp = caps.exposure(book, closes, equity)
    buy_pct = tradercfg.scout()["buy_percentile"]
    counts = {"placed": 0, "refused": 0, "queued": 0}
    for p in sorted(proposals, key=lambda p: p["side"] != "sell"):
        if p["side"] == "sell":
            caps.count_sell(exp)
            info, why = {"caps": "exempt", "qty": p["qty"], "price": closes.get(p["symbol"])}, None
        else:
            info, why = _buy(p, cfg, exp, equity, blocked, buy_pct, feed["macro"], session)
        counts[_act(now, session, p, info, why, mode, broker)] += 1
    return 0, (f"OK session={session} placed={counts['placed']} refused={counts['refused']} "
               f"queued={counts['queued']}{warn}"), session, equity


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--run", action="store_true", required=True)
    ap.parse_args(argv)
    code, message = run(fetch.utcnow())
    print(message)
    return code


if __name__ == "__main__":
    import oscompat
    oscompat.utf8_stdio()
    raise SystemExit(main(sys.argv[1:]))
```

Create `bobs/trader/harness/halt.py`:

```python
#!/usr/bin/env python3
"""Pull the brake: create state/HALT so no new buys are placed; sells by the exit rules still run.

Why the agent may halt but never resume: stopping is always safe, starting again is a decision.
This CLI only appends to HALT; protect_gate refuses any edit or removal of files under state/, so
only a person deletes it.

Usage:
    python3 harness/halt.py --reason "why"
"""
from __future__ import annotations

import argparse
import datetime as dt
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import traderpath  # noqa: E402,F401
import fetch  # noqa: E402
import trade  # noqa: E402


def halt(reason: str, now: dt.datetime) -> pathlib.Path:
    path = trade.halt_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(f"{now.isoformat(timespec='seconds')} {reason.strip()}\n")
    return path


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--reason", required=True)
    a = ap.parse_args(argv)
    print(f"OK HALT at {halt(a.reason, fetch.utcnow())}; a person deletes it to resume buying")
    return 0


if __name__ == "__main__":
    import oscompat
    oscompat.utf8_stdio()
    raise SystemExit(main(sys.argv[1:]))
```

Append to `test_trade.py`, before the `__main__` block:

```python
def test_halt_cli_appends_with_reason():
    import halt
    testkit.temp_bob()
    halt.halt("first", _now(1))
    halt.halt("second", _now(2))
    assert trade.halt_path().read_text(encoding="utf-8").splitlines()[-1].endswith("second")
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 harness/test_trade.py`
Expected: `13 of 13 passed`. If the end-to-end test fails, read the `(k, message)` it prints: the session and the FAIL line say which step broke.

- [ ] **Step 5: Break it once, then restore**

1. In `_collect`, drop the `if a["activity_id"] not in seen` filter: `test_collect_is_idempotent_by_activity_id` must FAIL. Restore.
2. In `_run`, remove the `if not ledger.unresolved_mismatches(rows):` guard (always append): `test_mismatch_stops_trading_and_is_recorded_once` must FAIL. Restore.
3. In `_run`, move `_no_buys` so that `blocked` also refuses sells (pass `blocked` as `why` for sells): `test_halt_refuses_buys_but_sells_still_run` must FAIL. Restore.
4. In the `run` function's mode-off branch, call `adapter("off")` before returning: `test_mode_off_touches_nothing` must FAIL. Restore.

Re-run: `13 of 13 passed`.

- [ ] **Step 6: Commit**

```bash
git add harness/trade.py harness/halt.py harness/test_trade.py
git commit -m "Trader: the daily pipeline and the HALT brake

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
git push
```

---

### Task 11: Live approval (`approve.py`)

**Files:**
- Create: `bobs/trader/harness/approve.py`
- Test: `bobs/trader/harness/test_approve.py`

**Interfaces:**
- Consumes: `ledger.*` (including `confirm`, `Refused`), `reconcile.check`, `bars.*`, `caps.*`, `marketclock.last_session`, `trade.equity_of`, `broker.base`.
- Produces: `approve.approve(order_id, now, adapter=broker.base.adapter_for, stdin=sys.stdin, ask=input) -> list[dict]` (the `approved` and `submitted` lines); CLI `approve.py ORDER_ID`.

- [ ] **Step 1: Write the failing tests**

Create `bobs/trader/harness/test_approve.py`:

```python
#!/usr/bin/env python3
"""approve: a person at a terminal, the id typed back, and the books, prices and caps checked again.

Run:  python3 harness/test_approve.py
"""
from __future__ import annotations

import datetime as dt
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import traderpath  # noqa: E402,F401
import approve  # noqa: E402
import ledger  # noqa: E402
import testkit  # noqa: E402
from broker import base  # noqa: E402

LIVE = {'mode = "off"': 'mode = "live"'}
NOW = dt.datetime(2026, 1, 9, 6, 30, tzinfo=dt.timezone.utc)
DAYS = ["2026-01-05", "2026-01-06", "2026-01-07", "2026-01-08"]
OID = "2026-01-08-scout_calls-buy-AAA"


class Tty:
    def __init__(self, tty: bool):
        self.tty = tty

    def isatty(self) -> bool:
        return self.tty


class FakeLive(base.Broker):
    MODE = "live"
    submitted: list = []

    def account(self) -> dict:
        return {"cash": 100_000.0}

    def positions(self) -> dict:
        return {}

    def submit(self, order: dict) -> None:
        FakeLive.submitted.append(order)

    def activities(self) -> list:
        return []


def _world(replace: dict | None = None, qty: int = 40) -> pathlib.Path:
    bob = testkit.temp_bob({**LIVE, **(replace or {})})
    folder = bob / "data" / "state" / "bars"
    testkit.put(folder / "_calendar.json", {"sessions": [{"date": d, "close": "16:00"} for d in DAYS]})
    for symbol, price in (("SPY", 400.0), ("AAA", 100.0)):
        testkit.put(folder / f"{symbol}.json", {"raw": [[d, price, price, price, price, 10**6] for d in DAYS],
                                                "split": [], "all": [], "evidence": []})
    ledger.append([ledger.line("cash", NOW, DAYS[0], amount=100_000.0),
                   ledger.line("proposed", NOW, DAYS[-1], order_id=OID, strategy="scout_calls", symbol="AAA",
                               side="buy", qty=qty, price=100.0, decision="accepted", caps="ok"),
                   ledger.line("queued", NOW, DAYS[-1], order_id=OID)])
    FakeLive.submitted = []
    return bob


def _approve(stdin=Tty(True), typed: str = OID):
    return approve.approve(OID, NOW, adapter=FakeLive, stdin=stdin, ask=lambda prompt: typed)


def _refused(fn) -> str:
    try:
        fn()
    except ledger.Refused as e:
        return str(e)
    raise AssertionError("not refused")


def test_person_at_a_terminal_approves_and_it_is_submitted():
    _world()
    rows = _approve()
    assert [r["kind"] for r in rows] == ["approved", "submitted"] and FakeLive.submitted[0]["qty"] == 40


def test_refused_without_a_terminal_or_with_the_wrong_id():
    _world()
    assert "terminal" in _refused(lambda: _approve(stdin=Tty(False)))
    assert "did not match" in _refused(lambda: _approve(typed="nope"))
    assert FakeLive.submitted == []


def test_caps_are_checked_again_at_approval_time():
    _world(qty=200)
    assert "max_position" in _refused(_approve)


def test_only_queued_orders_and_only_once():
    _world()
    _approve()
    assert "already" in _refused(_approve)


def test_only_in_live_mode():
    _world()
    testkit.temp_bob()
    assert "live" in _refused(_approve)


if __name__ == "__main__":
    raise SystemExit(testkit.run(globals()))
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 harness/test_approve.py`
Expected: `ModuleNotFoundError: No module named 'approve'`.

- [ ] **Step 3: Write the implementation**

Create `bobs/trader/harness/approve.py`:

```python
#!/usr/bin/env python3
"""A person approves one queued live order by id; the books, prices and caps are checked again first.

Why again: time has passed since the order was queued; prices, cash and other fills may have moved.
Why a terminal and a typed-back id: approval is the person's act. The agent has no terminal, and
order_gate refuses any agent tool call that names this script.

Usage (the person, in their own shell or with ! in Claude Code):
    python3 harness/approve.py ORDER_ID
"""
from __future__ import annotations

import argparse
import datetime as dt
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import traderpath  # noqa: E402,F401
import bars  # noqa: E402
import caps  # noqa: E402
import fetch  # noqa: E402
import ledger  # noqa: E402
import marketclock  # noqa: E402
import reconcile  # noqa: E402
import trade  # noqa: E402
import tradercfg  # noqa: E402
from broker import base as broker_base  # noqa: E402

Refused = ledger.Refused


def approve(order_id: str, now: dt.datetime, adapter=broker_base.adapter_for, stdin=sys.stdin, ask=input) -> list[dict]:
    cfg = tradercfg.load()
    if cfg["mode"] != "live":
        raise Refused(f"approve is for live mode; mode is {cfg['mode']}")
    ledger.confirm(order_id, stdin, ask)
    rows, _ = ledger.read()
    lines = [r for r in rows if r.get("order_id") == order_id]
    if not any(r["kind"] == "queued" for r in lines):
        raise Refused(f"no queued order {order_id}")
    if any(r["kind"] in ("approved", "submitted", "expired") for r in lines):
        raise Refused(f"{order_id} was already approved, submitted or expired")
    proposal = next(r for r in lines if r["kind"] == "proposed")
    try:
        broker = adapter("live")
    except broker_base.AdapterError as e:
        raise Refused(str(e)) from None
    problems = reconcile.check(rows, broker)
    if problems:
        raise Refused("reconcile: " + "; ".join(problems))
    session = marketclock.last_session(bars.sessions(), now)
    book = ledger.replay(rows)
    stale = [s for s in [bars.BENCHMARK, proposal["symbol"], *book.positions()] if session is None or not bars.fresh(s, session)]
    if stale:
        raise Refused(f"no current bar for {', '.join(stale)}")
    closes = {s: bars.close_on(s, session) for s in book.positions()}
    if proposal["side"] == "buy":
        exp = caps.exposure(book, closes, trade.equity_of(book, closes))
        value = round(proposal["qty"] * bars.close_on(proposal["symbol"], session), 2)
        breach = caps.check_buy(exp, proposal["symbol"], value, proposal["strategy"], cfg)
        if breach:
            raise Refused(breach)
    approved = ledger.line("approved", now, session, order_id=order_id)
    ledger.append([approved])
    broker.submit({"order_id": order_id, "symbol": proposal["symbol"], "side": proposal["side"],
                   "qty": proposal["qty"], "session": session})
    submitted = ledger.line("submitted", now, session, order_id=order_id)
    ledger.append([submitted])
    return [approved, submitted]


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("order_id")
    a = ap.parse_args(argv)
    try:
        approve(a.order_id, fetch.utcnow())
    except Refused as e:
        print(f"REFUSED: {e}")
        return 1
    print(f"OK submitted {a.order_id}")
    return 0


if __name__ == "__main__":
    import oscompat
    oscompat.utf8_stdio()
    raise SystemExit(main(sys.argv[1:]))
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 harness/test_approve.py`
Expected: `5 of 5 passed`.

- [ ] **Step 5: Break it once, then restore**

Delete the `caps.check_buy` block in `approve`: `test_caps_are_checked_again_at_approval_time` must FAIL. Restore. Re-run: `5 of 5 passed`.

- [ ] **Step 6: Commit**

```bash
git add harness/approve.py harness/test_approve.py
git commit -m "Trader: approve.py, a person-only approval that re-checks books, prices and caps

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
git push
```

---

### Task 12: Hooks (`protect_gate`, `order_gate`) and their registration

**Files:**
- Create: `bobs/trader/harness/hooks/protect_gate.py` (adapted copy), `bobs/trader/harness/hooks/order_gate.py`
- Modify: `bobs/trader/.claude/settings.json`
- Test: `bobs/trader/harness/hooks/test_hooks.py`

**Interfaces:**
- Consumes: `traderpath`, `tradercfg.state_dir/evidence_dir/bob_dir`, `bobcfg.claude_home`.
- Produces: two PreToolUse gates printing a `deny` decision JSON or nothing; both fail closed.

- [ ] **Step 1: Write the failing tests**

Create `bobs/trader/harness/hooks/test_hooks.py`:

```python
#!/usr/bin/env python3
"""The Trader's gates, driven the way Claude Code drives them; every refusal paired with an allow.

Run:  python3 harness/hooks/test_hooks.py
"""
from __future__ import annotations

import json
import os
import pathlib
import re
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import traderpath  # noqa: E402,F401
import testkit  # noqa: E402


def run_gate(script: str, payload, bob: pathlib.Path | None = None) -> dict | None:
    bob = bob or testkit.temp_bob()
    data = payload if isinstance(payload, str) else json.dumps(payload)
    out = subprocess.run([sys.executable, str(HERE / script)], input=data, env=dict(os.environ), cwd=bob,
                         capture_output=True, text=True, encoding="utf-8", timeout=30)
    assert out.returncode == 0, f"{script} exited {out.returncode}: {out.stderr}"
    return json.loads(out.stdout) if out.stdout.strip() else None


def denied(decision) -> bool:
    return bool(decision) and decision["hookSpecificOutput"]["permissionDecision"] == "deny"


def call(tool: str, **tool_input) -> dict:
    return {"tool_name": tool, "tool_input": tool_input}


def test_protect_denies_ledger_manifest_hooks_and_halt_removal():
    for payload in (call("Edit", file_path="data/state/ledger.jsonl"), call("Write", file_path="bob.toml", content="x"),
                    call("Edit", file_path="harness/hooks/order_gate.py"),
                    call("Bash", command="rm data/state/HALT"), call("Bash", command="echo x >> data/state/ledger.jsonl"),
                    call("Edit", file_path="data/state/sim/broker.json")):
        assert denied(run_gate("protect_gate.py", payload)), payload


def test_protect_allows_reads_clis_and_notes():
    for payload in (call("Bash", command="cat data/state/ledger.jsonl"), call("Bash", command="python3 harness/trade.py --run"),
                    call("Bash", command="python3 harness/halt.py --reason 'vix spike'"),
                    call("Write", file_path="docs/TRACKER.md", content="x"), call("Write", file_path="skills/x.md", content="x")):
        assert not denied(run_gate("protect_gate.py", payload)), payload


def test_order_gate_denies_broker_traffic_adapters_and_person_only_clis():
    for payload in (call("Bash", command="curl -X POST https://paper-api.alpaca.markets/v2/orders"),
                    call("WebFetch", url="https://api.alpaca.markets/v2/account", prompt="x"),
                    call("mcp__alpaca__place_stock_order", symbol="AAA"),
                    call("Bash", command="python3 harness/broker/sim.py"),
                    call("Bash", command="python3 -c 'from broker import sim; sim.Simulator(\"simulate\").submit({})'"),
                    call("Write", file_path="tmp.py", content="from broker import sim\n"),
                    call("Bash", command="python3 harness/approve.py 2026-01-08-scout_calls-buy-AAA"),
                    call("Bash", command="python3 harness/ledger.py correct --note x --cash-delta 5")):
        assert denied(run_gate("order_gate.py", payload)), payload


def test_order_gate_allows_the_daily_run_halt_and_reads():
    for payload in (call("Bash", command="python3 harness/trade.py --run"),
                    call("Bash", command="python3 harness/halt.py --reason x"),
                    call("Bash", command="python3 harness/ledger.py summary --days 7"),
                    call("Bash", command="curl 'https://data.alpaca.markets/v2/stocks/bars?symbols=SPY'"),
                    call("Edit", file_path="docs/TRACKER.md", old_string="a", new_string="b")):
        assert not denied(run_gate("order_gate.py", payload)), payload


def test_notes_may_name_person_only_clis_and_adapters():
    note = "person: please run approve.py 2026-01-08-scout_calls-buy-AAA; see harness/broker/sim.py"
    for payload in (call("Write", file_path="docs/TRACKER.md", content=note),
                    call("Edit", file_path="skills/lesson.md", old_string="a", new_string=note)):
        assert not denied(run_gate("order_gate.py", payload)), payload


def test_both_gates_fail_closed():
    assert denied(run_gate("protect_gate.py", "not json"))
    assert denied(run_gate("order_gate.py", "{broken"))


def test_gates_import_only_stdlib_at_module_level():
    import ast
    for script in ("protect_gate.py", "order_gate.py"):
        tree = ast.parse((HERE / script).read_text(encoding="utf-8"))
        names = {a.name.split(".")[0] for n in tree.body if isinstance(n, (ast.Import, ast.ImportFrom)) for a in n.names}
        names |= {n.module.split(".")[0] for n in tree.body if isinstance(n, ast.ImportFrom) and n.module}
        assert names <= {"__future__", "annotations", "json", "os", "pathlib", "re", "sys"}, (script, names)


def test_settings_register_both_gates_for_files_and_shells():
    settings = json.loads((traderpath.BOB / ".claude" / "settings.json").read_text(encoding="utf-8"))
    for script in ("protect_gate.py", "order_gate.py"):
        matchers = [e["matcher"] for e in settings["hooks"]["PreToolUse"] if any(script in h["command"] for h in e["hooks"])]
        assert matchers and all(re.fullmatch(matchers[0], t) for t in ("Write", "Edit", "Bash", "PowerShell")), (script, matchers)


if __name__ == "__main__":
    raise SystemExit(testkit.run(globals()))
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 harness/hooks/test_hooks.py`
Expected: every test FAILS (`can't open file ... protect_gate.py`).

- [ ] **Step 3: Write the implementation**

Copy the Scout's gate, then edit it:

```bash
cp ../investor/harness/hooks/protect_gate.py harness/hooks/protect_gate.py
```

With the editor, in `harness/hooks/protect_gate.py`:

1. Replace the module docstring's first line with `"""PreToolUse gate: the Trader never edits its own ledger, state, evidence, settings, code or guards.` and its second paragraph's first sentence with: `The ledger, the simulator's state, HALT, the saved evidence, the manifest (which holds the mode, the caps and the go-live bar), the Bob's code, core's code and the Claude settings change only through their CLIs or through a person. An agent that could rewrite them could make its books agree with the broker by itself, loosen a cap, switch modes, release the HALT brake or switch this gate off.` Keep the rest of the docstring.
2. Change `LEAVES = (...)` to:

```python
LEAVES = ("ledger.jsonl", "mode_history.jsonl", "halt", "broker.json", "bob.toml", "settings.json",
          "settings.local.json", "state/", "evidence/")
```

3. Replace `_modules` and `protected` with:

```python
def _modules():
    """The Bob's own modules, loaded inside main's try so a broken import is refused, not allowed."""
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
    import traderpath  # noqa: F401
    import bobcfg
    import tradercfg
    return traderpath, bobcfg, tradercfg


def protected() -> list[pathlib.Path]:
    traderpath, bobcfg, tradercfg = _modules()
    bob, home = tradercfg.bob_dir(), bobcfg.claude_home()
    return [tradercfg.state_dir(), tradercfg.evidence_dir(), bob / "harness", bob / ".claude", bob / "bob.toml",
            traderpath.CORE / "harness", home / "settings.json", home / "settings.local.json"]
```

4. In `decide`, change the two user-facing reasons to name the Trader's CLIs: `"Use the Bob's CLIs (trade.py, halt.py) or ask the person who directs this Bob."` and `"Only the Bob's CLIs change the ledger and state; manifest, settings and code change by a person."` (in both shell messages).

Create `bobs/trader/harness/hooks/order_gate.py`:

```python
#!/usr/bin/env python3
"""PreToolUse gate: orders reach a broker only through trade.py, and approvals are the person's.

trade.py and approve.py are the only doors to a broker: they run reconcile, the input checks and
the caps first. This gate closes the others the agent has: a shell, WebFetch, file writes (a script
holding an orders URL or importing an adapter could be written, then run) and MCP servers. It also
refuses any tool call naming approve.py or `ledger.py correct`: approving a live order and
correcting the books are the person's acts, run from the person's own terminal.

Refuses: any broker-looking MCP tool; any call input naming an Alpaca orders, positions,
account-configuration or watchlists endpoint, or the live host api.alpaca.markets; a shell command
or a code file (.py .sh .ps1 .cmd .bat) naming harness/broker/ or importing the broker package; a
shell command naming approve.py or `ledger.py correct`.
Allows: trade.py --run, halt.py, ledger.py book/summary, market-data reads, and notes (the tracker,
lessons) that mention approve.py or the adapters: writing "the person should run approve.py" is
exactly what the loops ask for.

Fails CLOSED: input it cannot read is refused. Only the standard library is imported.
"""
from __future__ import annotations

import json
import pathlib
import re
import sys

ORDER_PATHS = re.compile(r"alpaca\.markets/v\d+/(orders|positions|account/configurations|watchlists)", re.I)
LIVE_HOST = re.compile(r"(?<![\w.-])api\.alpaca\.markets", re.I)
BROKER_MCP = re.compile(r"alpaca|broker|ibkr|interactive.?brokers|tastytrade|schwab|robinhood|etrade|trade|order", re.I)
ADAPTER = re.compile(r"harness[/\\]+broker[/\\]|\b(import|from)\s+broker\b|-m\s+broker\b", re.I)
PERSON_ONLY = re.compile(r"\bapprove\.py\b|\bledger\.py\b[^|;&]*\bcorrect\b", re.I)
SHELLS = {"Bash", "PowerShell"}
CODE_FILE = re.compile(r"\.(py|sh|ps1|cmd|bat)$", re.I)


def decide(payload: dict) -> str | None:
    tool = str(payload.get("tool_name", ""))
    if tool.startswith("mcp__") and BROKER_MCP.search(tool):
        return "broker tools are refused: orders go only through harness/trade.py --run"
    data = payload.get("tool_input") or {}
    text = json.dumps(data)
    if ORDER_PATHS.search(text) or LIVE_HOST.search(text):
        return "broker order, position and account endpoints and the live host are refused: orders go only through trade.py"
    if tool in SHELLS and PERSON_ONLY.search(text):
        return ("approve.py and ledger.py correct are the person's: they run them from their own terminal "
                "(or with ! in Claude Code). Record what needs approving or correcting in the tracker.")
    code = tool in SHELLS or bool(CODE_FILE.search(str(data.get("file_path") or "")))
    if code and ADAPTER.search(text):
        return "broker adapters run only inside trade.py and approve.py, never directly"
    return None


def main() -> int:
    try:
        sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
        import traderpath  # noqa: F401
        import oscompat
        oscompat.utf8_stdio()
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

In `bobs/trader/.claude/settings.json`, insert these two entries into `hooks.PreToolUse` after the `commit_gate.py` entry and before the `"matcher": "*"` (skill_gate) entry:

```json
      {
        "matcher": "Edit|Write|MultiEdit|NotebookEdit|Bash|PowerShell",
        "hooks": [
          {
            "type": "command",
            "command": "cd \"$CLAUDE_PROJECT_DIR\" && python3 harness/hooks/protect_gate.py"
          }
        ]
      },
      {
        "matcher": "Bash|PowerShell|WebFetch|Edit|Write|MultiEdit|NotebookEdit|mcp__.*",
        "hooks": [
          {
            "type": "command",
            "command": "cd \"$CLAUDE_PROJECT_DIR\" && python3 harness/hooks/order_gate.py"
          }
        ]
      },
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 harness/hooks/test_hooks.py`
Expected: `8 of 8 passed`.

- [ ] **Step 5: Break it once, then restore**

In `order_gate.py`, delete the `PERSON_ONLY` check: `test_order_gate_denies_broker_traffic_adapters_and_person_only_clis` must FAIL. Restore. In `protect_gate.py`, remove `"halt"` from `LEAVES` and `tradercfg.state_dir()` from `protected()`: `test_protect_denies_ledger_manifest_hooks_and_halt_removal` must FAIL. Restore. Change `if tool in SHELLS and PERSON_ONLY...` to `if PERSON_ONLY...`: `test_notes_may_name_person_only_clis_and_adapters` must FAIL. Restore. Re-run: `8 of 8 passed`.

- [ ] **Step 6: Commit**

```bash
git add harness/hooks .claude/settings.json
git commit -m "Trader: protect_gate and order_gate, registered for files and shells

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
git push
```

---

### Task 13: Board (`trader_board.py`)

**Files:**
- Create: `bobs/trader/harness/trader_board.py`
- Test: `bobs/trader/harness/test_trader_board.py`

**Interfaces:**
- Consumes: `board_shell.BaseHandler`, `board_shell.Hub`, `board_shell.Watcher`, `bobcfg`, `ledger`, `bars`, `promotion`, `trade.halt_path`, `tradercfg`.
- Produces: `trader_board.Model(title)` with `rebuild() -> dict` (`title, version, mode, halt, override, positions, queue, refusals, equity, promotion`), `trader_board.PAGE` (bytes), `trader_board.serve(model, host, port, ...)`.

- [ ] **Step 1: Write the failing tests**

Create `bobs/trader/harness/test_trader_board.py`:

```python
#!/usr/bin/env python3
"""trader_board: mode, HALT, positions with their stops, the queue, refusals and the go-live report.

Run:  python3 harness/test_trader_board.py
"""
from __future__ import annotations

import datetime as dt
import json
import pathlib
import sys
import threading
import urllib.request

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import traderpath  # noqa: E402,F401
import ledger  # noqa: E402
import testkit  # noqa: E402
import trade  # noqa: E402
import trader_board  # noqa: E402

NOW = dt.datetime(2026, 1, 9, 6, 30, tzinfo=dt.timezone.utc)


def test_empty_world_shows_mode_off_and_nothing_else():
    testkit.temp_bob()
    s = trader_board.Model("Trader").rebuild()
    assert s["mode"] == "off" and s["halt"] is None and s["positions"] == [] and s["queue"] == []


def test_positions_queue_refusals_and_halt():
    bob = testkit.temp_bob()
    testkit.put(bob / "data" / "state" / "bars" / "AAA.json", {"raw": [["2026-01-08", 90, 90, 90, 90, 1]],
                                                              "split": [["2026-01-08", 90.0]], "all": [], "evidence": []})
    ledger.append([
        ledger.line("cash", NOW, "2026-01-05", amount=1000.0),
        ledger.line("proposed", NOW, "2026-01-05", order_id="b", strategy="scout_calls", symbol="AAA", side="buy",
                    exit_rules={"stop_loss": 0.2, "take_profit": 0, "max_hold_days": 365}, decision="accepted"),
        ledger.line("filled", NOW, "2026-01-06", order_id="b", symbol="AAA", side="buy", qty=2, price=100.0, commission=1.0),
        ledger.line("queued", NOW, "2026-01-08", order_id="q"),
        ledger.line("refused", NOW, "2026-01-08", order_id="r", reason="max_position")])
    trade.halt_path().write_text("2026-01-08 vix spike\n", encoding="utf-8")
    s = trader_board.Model("Trader").rebuild()
    assert s["halt"] == "2026-01-08 vix spike"
    pos = s["positions"][0]
    assert (pos["symbol"], pos["qty"], pos["stop"], pos["close"]) == ("AAA", 2.0, 80.0, 90.0)
    assert [q["order_id"] for q in s["queue"]] == ["q"] and s["refusals"][0]["reason"] == "max_position"


def test_routes_serve_the_page_and_state():
    testkit.temp_bob()
    server = trader_board.serve(trader_board.Model("Trader"), "127.0.0.1", 0, quiet=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        assert b"Trader" in urllib.request.urlopen(base + "/").read()
        assert json.loads(urllib.request.urlopen(base + "/api/state").read())["mode"] == "off"
    finally:
        server.shutdown()


if __name__ == "__main__":
    raise SystemExit(testkit.run(globals()))
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 harness/test_trader_board.py`
Expected: `ModuleNotFoundError: No module named 'trader_board'`.

- [ ] **Step 3: Write the implementation**

Create `bobs/trader/harness/trader_board.py`:

```python
#!/usr/bin/env python3
"""The Trader board: mode and HALT, positions with their stops, the live queue, today's refusals,
equity next to SPY and the latest go-live report.

Built on core's board_shell (plumbing, /api/state, /events). Every number comes from the ledger,
the run history, the bars and the promotion reports, which code computed; the page only draws them.

Usage:
    python3 harness/trader_board.py               # port and title from bob.toml
    python3 harness/trader_board.py --port 8792 --quiet
"""
from __future__ import annotations

import argparse
import pathlib
import sys
import threading
from http.server import ThreadingHTTPServer

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import traderpath  # noqa: E402,F401
import bars  # noqa: E402
import bobcfg  # noqa: E402
import board_shell  # noqa: E402
import ledger  # noqa: E402
import promotion  # noqa: E402
import store  # noqa: E402
import trade  # noqa: E402
import tradercfg  # noqa: E402

EQUITY_ROWS = 30


class Watched:
    def __init__(self, files: list[pathlib.Path], folder: pathlib.Path):
        self.files, self.folder = files, folder

    def fingerprint(self) -> tuple:
        out = []
        for path in [*self.files, *sorted(self.folder.glob("*.json"))]:
            try:
                st = path.stat()
                out.append((str(path), st.st_mtime_ns, st.st_size))
            except OSError:
                out.append((str(path), None, None))
        return tuple(out)


class Model:
    def __init__(self, title: str):
        self.title = title
        self.ev = Watched([ledger.path(), promotion.history_path(), trade.halt_path(), tradercfg.bob_dir() / "bob.toml"],
                          promotion.folder())
        self._lock = threading.Lock()
        self.snapshot: dict | None = None
        self._builds = 0

    def rebuild(self) -> dict:
        self._builds += 1
        cfg = tradercfg.load()
        rows, torn = ledger.read()
        book = ledger.replay(rows)
        positions = []
        for symbol, qty in sorted(book.positions().items()):
            lots = book.lots_of(symbol)
            entry = sum(lot.qty * lot.price for lot in lots) / qty
            rules = lots[0].exit_rules
            stop = round(entry * (1 - rules.get("stop_loss", 0)), 4) if rules else None
            last = bars.load(symbol)["split"]
            close = last[-1][1] if last else None
            positions.append({"symbol": symbol, "qty": qty, "entry": round(entry, 4), "close": close, "stop": stop,
                              "to_stop": None if close is None or stop is None else round(close / stop - 1, 4),
                              "opened": lots[0].opened, "strategy": lots[0].strategy})
        done = {r["order_id"] for r in rows if r.get("kind") in ("approved", "submitted", "expired")}
        queue = [r for r in rows if r.get("kind") == "queued" and r["order_id"] not in done]
        last_session = max((r.get("session") or "" for r in rows if r.get("kind") == "refused"), default=None)
        refusals = [r for r in rows if r.get("kind") == "refused" and r.get("session") == last_session]
        spy = dict(bars.load(bars.BENCHMARK)["all"])
        runs = [h for h in promotion.read_history() if h.get("equity") is not None][-EQUITY_ROWS:]
        equity = [{"session": h["session"], "equity": h["equity"], "spy": spy.get(h["session"])} for h in runs]
        reports = sorted(promotion.folder().glob("*.json"))
        halt = trade.halt_path().read_text(encoding="utf-8").strip().splitlines()[-1] if trade.halt_path().exists() else None
        return {"title": self.title, "version": self._builds, "mode": cfg["mode"], "halt": halt,
                "override": cfg["override_promotion"], "torn": torn, "cash": book.cash, "positions": positions,
                "queue": queue, "refusals": refusals, "equity": equity,
                "promotion": store.read_json(reports[-1], None) if reports else None}


class Handler(board_shell.BaseHandler):
    pass


PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>Trader</title>
<style>
:root{--bg:#0f141b;--fg:#d7dde6;--muted:#7d8a9c;--line:#2a3442;--up:#5fb37a;--down:#e07a5f}
body{font:14px/1.5 system-ui,sans-serif;margin:16px;max-width:1100px;background:var(--bg);color:var(--fg)}
h1{font-size:20px}h2{font-size:15px;margin-top:28px;border-bottom:1px solid var(--line);padding-bottom:4px}
table{border-collapse:collapse;width:100%;font-size:13px}td,th{padding:4px 6px;border-bottom:1px solid var(--line);text-align:left}
.muted{color:var(--muted)}.banner{border:1px solid var(--down);padding:6px 10px;margin:8px 0}.wrap{overflow-x:auto}
</style></head><body>
<h1 id="title">Trader</h1>
<div id="banners"></div>
<h2>Positions <span class="muted" id="cash"></span></h2><div class="wrap"><table id="positions"></table></div>
<h2>Waiting for your approval</h2><div class="wrap"><table id="queue"></table></div>
<h2>Refused on the last run</h2><div class="wrap"><table id="refusals"></table></div>
<h2>Equity and SPY</h2><div class="wrap"><table id="equity"></table></div>
<h2>Go-live report</h2><div class="wrap"><table id="promotion"></table></div>
<script>
function el(tag, text, klass){ var e = document.createElement(tag); if (text !== undefined) e.textContent = text; if (klass) e.className = klass; return e; }
function table(id, head, rows){
  var t = document.getElementById(id); t.textContent = "";
  var tr = el("tr"); head.forEach(function(h){ tr.appendChild(el("th", h)); }); t.appendChild(tr);
  if (!rows.length){ var r = el("tr"); var td = el("td", "none", "muted"); td.colSpan = head.length; r.appendChild(td); t.appendChild(r); return; }
  rows.forEach(function(cells){ var r = el("tr"); cells.forEach(function(c){ r.appendChild(el("td", c === null || c === undefined ? "n/a" : String(c))); }); t.appendChild(r); });
}
function show(s){
  document.getElementById("title").textContent = s.title + " (mode " + s.mode + ")";
  var b = document.getElementById("banners"); b.textContent = "";
  if (s.mode === "off") b.appendChild(el("div", "Mode is off: nothing runs. A person changes the mode in bob.toml.", "banner"));
  if (s.halt) b.appendChild(el("div", "HALT: " + s.halt + ". No new buys; sells still run. A person deletes state/HALT to resume.", "banner"));
  if (s.override) b.appendChild(el("div", "override_promotion is set: " + s.override, "banner"));
  if (s.torn) b.appendChild(el("div", s.torn + " torn ledger line(s); reconcile will refuse to trade.", "banner"));
  document.getElementById("cash").textContent = "cash " + s.cash.toFixed(2);
  table("positions", ["symbol", "shares", "entry", "close", "stop", "to stop", "opened", "strategy"], s.positions.map(function(p){
    return [p.symbol, p.qty, p.entry, p.close, p.stop, p.to_stop === null ? null : (p.to_stop * 100).toFixed(1) + "%", p.opened, p.strategy]; }));
  table("queue", ["order", "session"], s.queue.map(function(q){ return [q.order_id, q.session]; }));
  table("refusals", ["order", "reason"], s.refusals.map(function(r){ return [r.order_id, r.reason]; }));
  table("equity", ["session", "equity", "SPY total return close"], s.equity.slice().reverse().map(function(e){ return [e.session, e.equity.toFixed(2), e.spy]; }));
  var p = s.promotion;
  table("promotion", ["criterion", "status", "detail"], p ? p.criteria.map(function(c){ return [c.name, c.status, c.detail]; }) : []);
}
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
    import oscompat
    oscompat.utf8_stdio()
    raise SystemExit(main(sys.argv[1:]))
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 harness/test_trader_board.py`
Expected: `3 of 3 passed`.

- [ ] **Step 5: Look at the page**

Start `python3 harness/trader_board.py` in the background and note its PID. Before looking, write down what must show: the title "Trader (mode off)", the "Mode is off" banner, and five sections each reading "none". Open `http://127.0.0.1:8792/`, confirm, then stop the board by its PID.

- [ ] **Step 6: Commit**

```bash
git add harness/trader_board.py harness/test_trader_board.py
git commit -m "Trader: board with mode, HALT, positions, queue, refusals and the go-live report

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
git push
```

---

### Task 14: Loops, domain rules, README, release notes

**Files:**
- Create: `bobs/trader/loops/daily-trade.txt`, `bobs/trader/loops/weekly-review.txt`, `bobs/trader/README.md`, `bobs/trader/harness/test_loops.py`
- Modify: `bobs/trader/CLAUDE.md` (`## Domain rules`), `bobs/trader/docs/RELEASE-NOTES.md`, `bobs/trader/docs/TRACKER.md`

- [ ] **Step 1: Write the failing loop test**

Create `bobs/trader/harness/test_loops.py`:

```python
#!/usr/bin/env python3
"""Loops are code too: each must register and record first, and call only CLIs and flags that exist.

Run:  python3 harness/test_loops.py
"""
from __future__ import annotations

import pathlib
import re
import sys
import tomllib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import traderpath  # noqa: E402,F401
import testkit  # noqa: E402

BOB, CORE = traderpath.BOB, traderpath.CORE
OWN = tomllib.loads(testkit.MANIFEST.read_text(encoding="utf-8"))["loops"]["own"]
CALL = re.compile(r"python3 ((?:\.\./\.\./)?harness/[\w/]+\.py)((?: --[\w-]+| [\w<>.-]+)*)")


def _loops() -> dict[str, str]:
    return {pathlib.PurePosixPath(rel).stem: (BOB / rel).read_text(encoding="utf-8") for rel in OWN}


def test_every_own_loop_exists():
    assert all((BOB / rel).is_file() for rel in OWN), OWN


def test_each_loop_registers_then_records_first():
    for name, text in _loops().items():
        lines = [line for line in text.splitlines() if line.strip()]
        assert lines[1].startswith("FIRST: CronList"), name
        assert lines[2].endswith(f"loop_passes.py --record {name}"), name


def test_loops_call_only_existing_clis_and_flags():
    for name, text in _loops().items():
        for script, args in CALL.findall(text):
            path = (CORE / script[len("../../"):]) if script.startswith("../../") else BOB / script
            assert path.is_file(), f"{name}: {script} does not exist"
            source = path.read_text(encoding="utf-8")
            for flag in re.findall(r"--[\w-]+", args):
                assert f'"{flag}"' in source, f"{name}: {script} has no {flag}"


def test_no_loop_names_a_person_only_cli():
    for name, text in _loops().items():
        assert "approve.py" not in text and "ledger.py correct" not in text, name


if __name__ == "__main__":
    raise SystemExit(testkit.run(globals()))
```

Run: `python3 harness/test_loops.py`
Expected: `test_every_own_loop_exists` FAILS (the loop files do not exist yet).

- [ ] **Step 2: Write the loops**

Create `bobs/trader/loops/daily-trade.txt`:

```
Daily trade for Trader. Schedule: 30 8 * * 2-6 (after the Scout's nightly screen).

FIRST: CronList. If this job is absent, re-register it from this file and say so.
SECOND, before any work: python3 ../../harness/loop_passes.py --record daily-trade

Scripts decide; you read and record. Stop at the first FAIL, report it, never work around it.

1. python3 harness/bars.py --refresh
2. python3 harness/trade.py --run
   OK mode=off: nothing else to do; go to step 4.
   FAIL: reconcile ...: the books disagree with the broker. Do not try to fix it. Write the exact line in the
   tracker and tell the person; they clear it with ledger.py correct from their own terminal.
   Any other FAIL: record it; no orders were placed.
3. python3 harness/ledger.py summary --days 1
   Read the refusals. A refusal is an answer: never change anything to get past one.
   If something looks dangerous (a burst of refusals, a position far below its stop, a mismatch), pull the brake:
   python3 harness/halt.py --reason "what you saw"
   You may halt; you may never resume. Only the person deletes state/HALT.
4. One row in docs/TRACKER.md: steps run, the OK or FAIL lines, placed / refused / queued counts.
```

Create `bobs/trader/loops/weekly-review.txt`:

```
Weekly review for Trader. Schedule: 0 10 * * 6.

FIRST: CronList. If this job is absent, re-register it from this file and say so.
SECOND, before any work: python3 ../../harness/loop_passes.py --record weekly-review

1. python3 harness/promotion.py --report
   Read every criterion. The report never changes the mode; only the person does, in bob.toml.
2. python3 harness/ledger.py summary --days 7
3. Reply in the terminal with: the mode, placed / refused counts for the week, any mismatch or HALT, and the
   go-live report line by line exactly as printed. Never restate a number differently.
4. If a lesson emerges (a refusal that keeps repeating for the same reason, a stop hit right after entry),
   write it under skills/ in the lessons format and run python3 ../../harness/skill_index.py --write.
   A rule change (a cap, a stop, a flag) is a proposal in the tracker for the person, never an edit.
```

Run: `python3 harness/test_loops.py`
Expected: `4 of 4 passed`.

- [ ] **Step 3: Domain rules**

In `bobs/trader/CLAUDE.md`, replace the two template lines under `## Domain rules` with:

```
- **The mode is the person's.** `bob.toml` sets `mode`: off, simulate, paper or live. Never change it,
  never propose working around it. `protect_gate` refuses edits to `bob.toml` anyway.
- **Orders go only through `trade.py --run`.** No broker endpoint, no adapter, no MCP broker tool
  directly; `order_gate` refuses them. In live mode orders wait for the person's `approve.py`.
- **Never approve and never correct.** `approve.py` and `ledger.py correct` are the person's. When
  something needs approving or correcting, write it in the tracker and say so.
- **REFUSED and FAIL are answers.** A refused order, a reconcile mismatch or a stale price is
  recorded, not worked around. Never edit the ledger, `state/` or the hooks.
- **You may halt; you may never resume.** `halt.py --reason ...` stops new buys. Only the person
  deletes `state/HALT`.
- **Sources.** The Scout's calls and market conditions through `scout_feed.py` (read-only), prices
  through `bars.py`. Every number you report comes from a CLI's output; never compute one by hand.
- **Caps, exit rules and the go-live bar** live in `[trader]`. Propose a change as a lesson citing
  the ledger; only the person changes them.
```

- [ ] **Step 4: README, release notes, tracker**

Create `bobs/trader/README.md`:

```
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
```

In `docs/RELEASE-NOTES.md`, add above `## Scaffold`:

```
## Stage 1: core, simulator, Scout-calls strategy (mode off)

- Built: ledger, bars, Scout feed, simulator, reconcile, sizing, risk flags, caps, the scout_calls
  strategy, the daily run, HALT, live approval, the go-live report, both gates, the board, loops.
- What it means for you: nothing trades. `mode = "off"` is committed; switching to `simulate` is
  your change in bob.toml. Paper and live need adapters that are not built yet.
```

In `docs/TRACKER.md`, add a row:

```
| 2026-10-14 | Stage 1 built per docs/superpowers/plans/2026-10-14-trader-stage1.md | selftest and the simulate week in the next rows |
```

Run: `./selftest.sh`
Expected: every line `ok`.

- [ ] **Step 5: Commit**

```bash
git add loops CLAUDE.md README.md docs/RELEASE-NOTES.md docs/TRACKER.md harness/test_loops.py
git commit -m "Trader: loops, domain rules, README, release notes

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
git push
```

---

### Task 15: A simulated week, then back to off (spec section 11)

**Files:**
- Modify: `bobs/trader/docs/TRACKER.md` (one row per day and per check)
- `bob.toml` changes only locally and only by the person; never committed with a mode other than `off`.

- [ ] **Step 1: Every suite**

From `bob_core/`: `./selftest.sh`; from `bobs/investor/`: `./selftest.sh`; from `bobs/trader/`: `./selftest.sh`.
Expected: every line `ok` in all three.

- [ ] **Step 2: The person switches to simulate**

Ask the person to change `mode = "off"` to `mode = "simulate"` in `bobs/trader/bob.toml` themselves (protect_gate refuses the agent). Do not commit this change. Confirm the Alpaca data keys are in the environment; if not, record "blocked: Alpaca keys not in environment" and stop here, reporting the blocker.

- [ ] **Step 3: One week of daily runs**

On each of five consecutive trading days, after the Scout's nightly screen, from `bobs/trader/`:

```bash
python3 harness/bars.py --refresh
python3 harness/trade.py --run
python3 harness/ledger.py summary --days 1
```

Expected each day: `OK session=... placed=N refused=M queued=0`. Record the three outputs in the tracker. A `FAIL: reconcile` line stops the week: record it and ask the person to investigate. If the Scout made no v2 calls during the week, record that the week shows reconcile and the run working but no fills, and ask the person whether to extend it.

- [ ] **Step 4: The board shows it**

Start the board, open `http://127.0.0.1:8792/`; before looking, write down what must show: mode simulate, the week's positions with stops, and the last run's refusals. Confirm, stop the board by its PID.

- [ ] **Step 5: Back to off and record**

Ask the person to set `mode = "off"` again. Run `git diff bob.toml`: it must show no change. Then:

```bash
git add docs/TRACKER.md
git commit -m "Trader: record the stage 1 simulated week

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
git push
```
