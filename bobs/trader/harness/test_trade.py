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


def _two_held(bob: pathlib.Path) -> list[dict]:
    calls = [_call("AAA", 30), _call("CCC", 30)]
    _scout(bob, calls)
    trade.run(_now(30))
    _write_bars(bob, 31)
    trade.run(_now(31))
    assert set(ledger.replay(ledger.read()[0]).positions()) == {"AAA", "CCC"}
    return calls


def test_stale_held_symbol_blocks_buys_but_other_exits_still_run():
    bob = _ready()
    calls = _two_held(bob)
    _scout(bob, calls + [{"kind": "withdrawn", "call_id": f"CCC-{DAYS[30]}-1"}, _call("BBB", 32)])
    _write_bars(bob, 32, symbols=("SPY", "BBB", "CCC"))
    code, message = trade.run(_now(32))
    today = {(r["symbol"], r["side"]): r for r in ledger.read()[0] if r["kind"] == "proposed" and r["session"] == DAYS[32]}
    assert code == 0 and "WARN" in message and "AAA" in message, message
    assert today[("CCC", "sell")]["decision"] == "accepted" and today[("BBB", "buy")]["decision"] == "refused"
    assert ("AAA", "sell") not in today


def test_held_symbol_without_a_split_adjusted_close_is_stale_not_silently_unstopped():
    bob = _ready()
    _two_held(bob)
    _write_bars(bob, 32)
    path = bob / "data" / "state" / "bars" / "AAA.json"
    doc = json.loads(path.read_text(encoding="utf-8"))
    doc["split"] = doc["split"][:-1]
    path.write_text(json.dumps(doc), encoding="utf-8")
    code, message = trade.run(_now(32))
    assert code == 0 and "WARN" in message and "AAA" in message, message


def test_held_symbol_never_priced_fails_the_run():
    bob = _ready()
    _two_held(bob)
    _write_bars(bob, 32, symbols=("SPY", "CCC"))
    (bob / "data" / "state" / "bars" / "AAA.json").write_text("{}", encoding="utf-8")
    code, message = trade.run(_now(32))
    assert code == 1 and "AAA" in message, message


def test_stale_spy_fails_the_run():
    bob = _ready()
    _two_held(bob)
    _write_bars(bob, 32, symbols=("AAA", "CCC"))
    code, message = trade.run(_now(32))
    assert code == 1 and "SPY" in message, message


def test_torn_ledger_line_stops_trading_until_a_correction_clears_it():
    bob = _ready(calls=[_call("AAA", 30)])
    trade.run(_now(30))
    with ledger.path().open("a", encoding="utf-8") as fh:
        fh.write("{torn\n")
    code, message = trade.run(_now(30))
    assert code == 1 and "torn" in message, message
    assert trade.run(_now(30))[0] == 1
    mismatches = [r for r in ledger.read()[0] if r["kind"] == "mismatch"]
    assert len(mismatches) == 1
    ledger.append([ledger.line("correction", _now(30), DAYS[30], clears=mismatches[0]["id"], cash_delta=0.0,
                               share_deltas={}, note="torn line checked")])
    assert trade.run(_now(30))[0] == 0


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


def test_orders_per_day_counts_orders_from_an_earlier_run_of_the_same_session():
    bob = testkit.temp_bob({**SIMULATE, "max_orders_per_day = 10": "max_orders_per_day = 1"})
    _write_bars(bob, 30)
    _scout(bob, [_call("AAA", 30)])
    assert "placed=1" in trade.run(_now(30))[1]
    _scout(bob, [_call("AAA", 30), _call("BBB", 30)])
    code, message = trade.run(_now(30))
    assert code == 0 and "placed=0 refused=1" in message, message


def test_sells_still_run_after_the_daily_loss_limit():
    bob = _ready(calls=[_call("AAA", 30)])
    trade.run(_now(30))
    _write_bars(bob, 31)
    trade.run(_now(31))
    promotion.record_run(_now(31), DAYS[31], {"mode": "simulate", **_cfg()}, "OK", 500_000.0)
    _scout(bob, [_call("AAA", 30), {"kind": "withdrawn", "call_id": f"AAA-{DAYS[30]}-1"}, _call("CCC", 32)])
    _write_bars(bob, 32)
    trade.run(_now(32))
    today = {r["side"]: r for r in ledger.read()[0] if r["kind"] == "proposed" and r["session"] == DAYS[32]}
    assert today["sell"]["decision"] == "accepted" and today["buy"]["decision"] == "refused"


def test_a_fill_recorded_by_the_broker_survives_a_crash_while_collecting():
    bob = _ready(calls=[_call("AAA", 30)])
    trade.run(_now(30))
    _write_bars(bob, 31)
    real = ledger.append

    def crash_on_fill(rows, where=None):
        if any(r["kind"] == "filled" for r in rows):
            raise RuntimeError("crash while recording the fill")
        real(rows, where)
    ledger.append = crash_on_fill
    try:
        trade.run(_now(31))
    except RuntimeError:
        pass
    finally:
        ledger.append = real
    assert trade.run(_now(31))[0] == 0
    assert trade.run(_now(31))[0] == 0
    assert len([r for r in ledger.read()[0] if r["kind"] == "filled"]) == 1


def test_missing_scout_buy_percentile_refuses_buys_with_the_reason():
    bob = _ready(calls=[_call("AAA", 30)])
    (bob.parent / "investor" / "bob.toml").write_text('[paths]\ndata_root = "data"\nstate_dir = "state"\n',
                                                      encoding="utf-8")
    code, message = trade.run(_now(30))
    refused = [r for r in ledger.read()[0] if r["kind"] == "refused"]
    assert code == 0 and refused and "buy_percentile" in refused[0]["reason"], (message, refused)


def test_torn_scout_lines_are_shown_in_the_run_output():
    bob = _ready(calls=[_call("AAA", 30)])
    path = testkit.scout_state(bob) / "calls.jsonl"
    path.write_text(path.read_text(encoding="utf-8") + "{torn\n", encoding="utf-8")
    code, message = trade.run(_now(30))
    assert code == 0 and "WARN scout_torn=1" in message, message


class RefusingSim(sim.Simulator):
    def submit(self, order: dict) -> None:
        raise sim.base.AdapterError("market closed for this symbol")


class CrashingSim(sim.Simulator):
    def submit(self, order: dict) -> None:
        raise RuntimeError("power cut")


def test_broker_refusal_on_submit_is_a_refused_line_not_a_crash():
    _ready(calls=[_call("AAA", 30)])
    code, message = trade.run(_now(30), adapter=RefusingSim)
    refused = [r for r in ledger.read()[0] if r["kind"] == "refused"]
    assert code == 0 and "placed=0 refused=1" in message and "market closed" in refused[0]["reason"], message


def test_buy_lost_to_a_crash_before_submit_is_proposed_again_next_session():
    bob = _ready(k=29)
    assert trade.run(_now(29))[0] == 0
    _write_bars(bob, 30)
    _scout(bob, [_call("AAA", 30)])
    try:
        trade.run(_now(30), adapter=CrashingSim)
    except RuntimeError:
        pass
    _write_bars(bob, 31)
    code, message = trade.run(_now(31))
    assert code == 0 and "placed=1" in message, message


class FakePaper(sim.base.Broker):
    MODE = "paper"
    orders: list = []

    def account(self) -> dict:
        return {"cash": 50_000.0}

    def positions(self) -> dict:
        return {}

    def submit(self, order: dict) -> None:
        FakePaper.orders.append(order)

    def activities(self) -> list:
        return []


def test_switching_to_paper_starts_a_fresh_ledger_from_the_broker():
    import bobcfg
    bob = _ready(calls=[_call("AAA", 30)])
    assert "placed=1" in trade.run(_now(30))[1]
    manifest = bob / "bob.toml"
    manifest.write_text(manifest.read_text(encoding="utf-8").replace('mode = "simulate"', 'mode = "paper"'),
                        encoding="utf-8")
    bobcfg.config.cache_clear()
    _scout(bob, [_call("AAA", 30), _call("CCC", 31)])
    _write_bars(bob, 31)
    FakePaper.orders = []
    code, message = trade.run(_now(31), adapter=FakePaper)
    assert code == 0 and "placed=1 refused=0" in message, message
    assert [o["symbol"] for o in FakePaper.orders] == ["CCC"]
    assert ledger.path().name == "ledger-paper.jsonl" and ledger.replay(ledger.read()[0]).cash == 50_000.0


def test_halt_cli_appends_with_reason():
    import halt
    testkit.temp_bob()
    halt.halt("first", _now(1))
    halt.halt("second", _now(2))
    assert trade.halt_path().read_text(encoding="utf-8").splitlines()[-1].endswith("second")


if __name__ == "__main__":
    raise SystemExit(testkit.run(globals()))
