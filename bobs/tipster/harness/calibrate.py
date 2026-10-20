#!/usr/bin/env python3
"""Fit the model on past seasons and gate each market on how its predictions held, game by game.

Why a gate before any tip: a model that is confidently wrong finds "edges" everywhere, and its
tips would lose money while looking principled. Each market (winner, handicap, total) must beat a
naive baseline AND be calibrated: in every 10%-wide probability band with at least 50 predictions
the observed share lies inside the 95% binomial interval, and the expected calibration error is at
most 3 points. A fixed "within 5 points" would fail a well-calibrated model by chance in small
bands. Both checks allow for sampling noise (see judge): the plain versions fail a calibrated model
on three seasons of games more often than not. A market that fails is recorded and not tipped;
failing is a finding, not a fault.

Why walk-forward: each game is predicted from games that started before it (same-time games
cannot see each other) with parameters fitted on earlier seasons only. Anything else leaks the
future into the score.

Why the double-round adjustment is kept only if it helps out of sample: a fitted effect always
improves the fit it came from. It is estimated on earlier seasons and kept only when it lowers the
next season's margin RMSE by at least DOUBLE_MIN_GAIN points summed over the walk.

Usage:
    python3 harness/calibrate.py --run      # PASS/FAIL per market with reasons, then OK
    python3 harness/calibrate.py --report   # the saved result
"""
from __future__ import annotations

import argparse
import itertools
import math
import pathlib
import statistics
import sys
from collections import defaultdict

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import tipsterpath  # noqa: E402,F401
import model  # noqa: E402
import ratings  # noqa: E402
import stats  # noqa: E402
import store  # noqa: E402
import tipstercfg  # noqa: E402

K_GRID = range(0, 21)
ECE_MAX = 0.03
NOISE = math.sqrt(2 / math.pi)
MIN_BAND = 50
MARGIN_LINES = range(-12, 13, 3)
TOTAL_LINES = range(145, 179, 3)
DOUBLE_DAYS = 3
DOUBLE_MIN_GAIN = 0.01


def _start(game: dict):
    return stats.parse_utc(game["start_utc"])


def second_of_double(team: str, game: dict, games: list[dict]) -> bool:
    start = _start(game)
    before = [_start(g) for g in games if team in (g["home"], g["away"]) and _start(g) < start]
    return bool(before) and (start.date() - max(before).date()).days <= DOUBLE_DAYS


def double_flags(games: list[dict]) -> dict[str, tuple[bool, bool]]:
    """Per game key: (home team in its second game of a double week, away team likewise)."""
    last: dict[str, object] = {}
    flags = {}
    for start, group in itertools.groupby(sorted(games, key=_start), key=_start):
        group = list(group)
        for g in group:
            flags[g["key"]] = tuple(t in last and (start.date() - last[t]).days <= DOUBLE_DAYS
                                    for t in (g["home"], g["away"]))
        for g in group:
            last[g["home"]] = last[g["away"]] = start.date()
    return flags


def snapshots(season_data: dict, last: ratings.Season | None) -> list[dict]:
    """What was known before each played game: current and prior ratings, leagues, double flags."""
    flags = double_flags(season_data["games"])
    lines_by_game: dict[str, list[dict]] = defaultdict(list)
    for line in season_data["lines"]:
        lines_by_game[line["game"]].append(line)
    played = sorted((g for g in season_data["games"] if g["played"]), key=lambda g: (_start(g), g["code"]))
    current = ratings.Season([])
    last_league = last.league() if last else None
    out = []
    for _, group in itertools.groupby(played, key=_start):
        group = list(group)
        league_cur = current.league()
        for g in group:
            out.append({"game": g, "double": flags[g["key"]],
                        "home_cur": current.team(g["home"]), "away_cur": current.team(g["away"]),
                        "home_prior": last.team(g["home"]) if last else None,
                        "away_prior": last.team(g["away"]) if last else None,
                        "league_shrink": ratings.league_target(current, last), "league_last": last_league,
                        "league_cur": league_cur or last_league})
        current.extend([x for g in group for x in lines_by_game[g["key"]]])
    return out


def expect_from(snap: dict, params: dict) -> dict | None:
    """The same answer model.predict gives, from a snapshot instead of a scan of every earlier line."""
    if snap["league_shrink"] is None:
        return None
    target, last = snap["league_shrink"], snap["league_last"]
    home = ratings.shrunk(snap["home_cur"], snap["home_prior"], target, params["k"], last)
    away = ratings.shrunk(snap["away_cur"], snap["away_prior"], target, params["k"], last)
    k_total = params.get("k_total", params["k"])
    points = (ratings.shrunk_points(snap["home_cur"], snap["home_prior"], target, k_total, last),
              ratings.shrunk_points(snap["away_cur"], snap["away_prior"], target, k_total, last))
    return model.expect(home, away, snap["league_cur"], params, snap["game"]["neutral"], snap["double"], points)


def base_total(snap: dict) -> float | None:
    """Season-to-date points for and against of both teams; last season's for a team with no games
    yet; the league's points per game for a club with neither."""
    league = snap["league_shrink"]
    if league is None:
        return None

    def per_game(cur, prior):
        team = cur or prior
        return (team["ppg"], team["oppg"]) if team else (league["ppg"], league["ppg"])
    h_for, h_against = per_game(snap["home_cur"], snap["home_prior"])
    a_for, a_against = per_game(snap["away_cur"], snap["away_prior"])
    return (h_for + a_against + a_for + h_against) / 2


def _margin(g: dict) -> int:
    return g["home_pts"] - g["away_pts"]


def _rms(values: list[float]) -> float:
    return math.sqrt(sum(v * v for v in values) / len(values))


def _previous(season: str) -> str:
    return f"E{int(season[1:]) - 1}"


def _season_snaps(season: str, data: dict) -> list[dict]:
    prev = data.get(_previous(season))
    return snapshots(data[season], ratings.Season(prev["lines"]) if prev else None)


def _residual_rows(snaps: list[dict], params: dict) -> list[dict]:
    rows = []
    for snap in snaps:
        exp = expect_from(snap, dict(params, double_adj=0.0))
        if exp is not None:
            g = snap["game"]
            rows.append({"r": _margin(g) - exp["margin"], "x": int(snap["double"][0]) - int(snap["double"][1]),
                         "rt": g["home_pts"] + g["away_pts"] - exp["total"]})
    return rows


def _adj(rows: list[dict]) -> float:
    xx = sum(r["x"] * r["x"] for r in rows)
    return sum(r["r"] * r["x"] for r in rows) / xx if xx else 0.0


def _double_adj(by_season: list[list[dict]]) -> float:
    without = with_adj = 0.0
    for i in range(1, len(by_season)):
        est = _adj([r for rows in by_season[:i] for r in rows])
        without += sum(r["r"] ** 2 for r in by_season[i])
        with_adj += sum((r["r"] - est * r["x"]) ** 2 for r in by_season[i])
    n = sum(len(rows) for rows in by_season[1:])
    if not n or math.sqrt(without / n) - math.sqrt(with_adj / n) < DOUBLE_MIN_GAIN:
        return 0.0
    return _adj([r for rows in by_season for r in rows])


def fit(seasons: list[str], no_fans: list[str], data: dict) -> dict:
    home = [_margin(g) for s in seasons if s not in no_fans for g in data[s]["games"]
            if g["played"] and not g["neutral"]]
    params = {"home_adv": sum(home) / len(home), "k": 0, "k_total": 0, "double_adj": 0.0, "fitted_on": list(seasons)}
    snaps = {s: _season_snaps(s, data) for s in seasons}
    best = None
    for k in K_GRID:
        res = [r["r"] for s in seasons for r in _residual_rows(snaps[s], dict(params, k=k))]
        if best is None or _rms(res) < best[0]:
            best = (_rms(res), k)
    params["k"] = best[1]
    best_total = None
    for k in K_GRID:
        res = [r["rt"] for s in seasons for r in _residual_rows(snaps[s], dict(params, k_total=k))]
        if best_total is None or _rms(res) < best_total[0]:
            best_total = (_rms(res), k)
    params["k_total"] = best_total[1]
    by_season = [_residual_rows(snaps[s], params) for s in seasons]
    params["double_adj"] = _double_adj(by_season)
    rows = [r for rows in by_season for r in rows]
    params["sigma_margin"] = _rms([r["r"] - params["double_adj"] * r["x"] for r in rows])
    params["sigma_total"] = _rms([r["rt"] for r in rows])
    return params


def walk_forward(eval_season: str, fit_seasons: list[str], data: dict, no_fans=()) -> list[dict]:
    params = fit(fit_seasons, list(no_fans), data)
    rows = []
    for snap in _season_snaps(eval_season, data):
        exp, base = expect_from(snap, params), base_total(snap)
        if exp is None or base is None:
            continue
        g = snap["game"]
        rows.append({"key": g["key"], "season": eval_season, "neutral": g["neutral"],
                     "exp": {"margin": exp["margin"], "total": exp["total"]},
                     "p_home_win": model.p_home_win(exp, params),
                     "actual_margin": _margin(g), "actual_total": g["home_pts"] + g["away_pts"],
                     "base_margin": 0.0 if g["neutral"] else params["home_adv"], "base_total": base,
                     "sigma_margin": params["sigma_margin"], "sigma_total": params["sigma_total"]})
    return rows


def bands(triples: list[tuple[float, int, str]]) -> list[dict]:
    """(probability, outcome, game key) grouped into 10%-wide bands. `games` counts distinct games:
    the handicap and total checks pool several lines per game, and those outcomes move together."""
    grouped: dict[int, list[tuple[float, int, str]]] = defaultdict(list)
    for p, y, key in triples:
        grouped[min(int(p * 10 + 1e-9), 9)].append((p, y, key))
    return [{"lo": i / 10, "n": len(g), "games": len({k for _, _, k in g}),
             "p": sum(p for p, _, _ in g) / len(g), "obs": sum(y for _, y, _ in g) / len(g)}
            for i, g in sorted(grouped.items())]


def _sd(b: dict) -> float:
    return math.sqrt(b["p"] * (1 - b["p"]) / b["games"])


def judge(band_list: list[dict]) -> list[str]:
    """Each band of at least MIN_BAND games must hold its observed share inside a 95% interval that
    covers all judged bands together (Bonferroni): ten bands each at 95% fail a calibrated model
    about 40% of the time. The calibration error counts each band's error above the deviation
    sampling alone gives (sqrt(2/pi) x its standard deviation): with three seasons of games the raw
    error of a calibrated model is about 0.033, above the limit by noise alone."""
    why = []
    judged = [b for b in band_list if b["games"] >= MIN_BAND]
    z = statistics.NormalDist().inv_cdf(1 - 0.05 / (2 * len(judged))) if judged else 0.0
    for b in judged:
        half = z * _sd(b)
        if abs(b["obs"] - b["p"]) > half:
            why.append(f"band {b['lo']:.1f}-{b['lo'] + 0.1:.1f}: observed {b['obs']:.3f} outside "
                       f"{b['p']:.3f} +/- {half:.3f} (games={b['games']})")
    total = sum(b["n"] for b in band_list)
    ece = sum(b["n"] * max(0.0, abs(b["obs"] - b["p"]) - NOISE * _sd(b)) for b in band_list) / total if total else 0.0
    if ece > ECE_MAX:
        why.append(f"calibration error {ece:.3f} above {ECE_MAX} (beyond sampling noise)")
    return why


def _sig(row: dict) -> dict:
    return {"sigma_margin": row["sigma_margin"], "sigma_total": row["sigma_total"]}


def _p_margin_over(row: dict, x: int) -> float:
    if x == 0:
        return model.p_home_win(row["exp"], _sig(row))
    return model.p_side(row["exp"], _sig(row), "home", -x)[0]


def _rmse_why(name: str, model_err: list[float], base_err: list[float]) -> list[str]:
    m, b = _rms(model_err), _rms(base_err)
    return [] if m < b else [f"{name} RMSE {m:.3f} not below baseline {b:.3f}"]


def gate(rows: list[dict]) -> dict:
    win = [int(r["actual_margin"] > 0) for r in rows]
    p_model = [model.p_home_win(r["exp"], _sig(r)) for r in rows]
    p_base = [model.p_home_win({"margin": r["base_margin"]}, _sig(r)) for r in rows]
    brier = sum((p - y) ** 2 for p, y in zip(p_model, win)) / len(rows)
    brier_base = sum((p - y) ** 2 for p, y in zip(p_base, win)) / len(rows)
    moneyline = ([] if brier < brier_base else [f"Brier {brier:.4f} not below baseline {brier_base:.4f}"])
    moneyline += judge(bands([(p, y, r["key"]) for p, y, r in zip(p_model, win, rows)]))
    handicap = _rmse_why("margin", [r["actual_margin"] - r["exp"]["margin"] for r in rows],
                         [r["actual_margin"] - r["base_margin"] for r in rows])
    handicap += judge(bands([(_p_margin_over(r, x), int(r["actual_margin"] > x), r["key"])
                             for r in rows for x in MARGIN_LINES]))
    total = _rmse_why("total", [r["actual_total"] - r["exp"]["total"] for r in rows],
                      [r["actual_total"] - r["base_total"] for r in rows])
    total += judge(bands([(model.p_total(r["exp"], _sig(r), "over", y)[0], int(r["actual_total"] > y), r["key"])
                          for r in rows for y in TOTAL_LINES]))
    return {"moneyline": {"pass": not moneyline, "why": moneyline},
            "handicap": {"pass": not handicap, "why": handicap},
            "total": {"pass": not total, "why": total}, "n": len(rows)}


def _calibration_path() -> pathlib.Path:
    return tipstercfg.state_dir() / "calibration.json"


def params_path() -> pathlib.Path:
    return tipstercfg.state_dir() / "model_params.json"


def save(result: dict, params: dict) -> None:
    store.write_json(_calibration_path(), dict(result, params=params))
    store.write_json(params_path(), params)


def report() -> dict | None:
    return store.read_json(_calibration_path(), None)


def passed_markets() -> list[str]:
    result = report()
    if not result:
        return []
    allowed = tipstercfg.load()["markets"]
    return [m for m in tipstercfg.MARKETS if m in allowed and result.get(m, {}).get("pass") is True]


def _seasons(first: str, last: str) -> list[str]:
    return [f"E{y}" for y in range(int(first[1:]), int(last[1:]) + 1)]


def load_data(seasons: list[str]) -> dict:
    data = {}
    for season in seasons:
        games = stats.games(season)
        data[season] = {"games": games, "lines": stats.season_lines_from(games)}
    return data


def run() -> dict:
    cfg = tipstercfg.load()
    fit_seasons = _seasons(cfg["fit_from"], max(cfg["calibration_seasons"]))
    data = load_data(fit_seasons)
    rows = []
    for season in cfg["calibration_seasons"]:
        rows += walk_forward(season, [s for s in fit_seasons if s < season], data, cfg["no_fans_seasons"])
    result = dict(gate(rows), seasons=list(cfg["calibration_seasons"]))
    params = fit(fit_seasons, cfg["no_fans_seasons"], data)
    save(result, params)
    return dict(result, params=params)


def _print(result: dict) -> None:
    for market in tipstercfg.MARKETS:
        part = result[market]
        print(f"{market}: {'PASS' if part['pass'] else 'FAIL'}" + ("" if part["pass"] else f" ({'; '.join(part['why'])})"))
    p = result["params"]
    print(f"OK n={result['n']} seasons={','.join(result['seasons'])} home_adv={p['home_adv']:.2f} k={p['k']} "
          f"double_adj={p['double_adj']:.2f} sigma_margin={p['sigma_margin']:.2f} sigma_total={p['sigma_total']:.2f}")


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Fit and gate the model on past seasons.")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--run", action="store_true")
    group.add_argument("--report", action="store_true")
    args = parser.parse_args(argv)
    try:
        result = run() if args.run else report()
    except (stats.StatsError, OSError) as e:
        print(f"FAIL: {type(e).__name__}: {e}")
        return 1
    if result is None:
        print("FAIL: no calibration result yet; run --run")
        return 1
    _print(result)
    return 0


if __name__ == "__main__":
    import oscompat
    oscompat.utf8_stdio()
    raise SystemExit(main(sys.argv[1:]))
