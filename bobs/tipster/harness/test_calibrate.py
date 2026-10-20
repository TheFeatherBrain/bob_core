#!/usr/bin/env python3
"""calibrate: fit on past seasons, predict game by game from earlier games only, and gate each market.

Synthetic seasons have known answers: a home advantage, a double-round effect, team strengths and
noise drawn with a fixed seed. random.Random(seed) is used here only, never in calibrate.py.

Run:  python3 harness/test_calibrate.py
"""
from __future__ import annotations

import datetime as dt
import math
import pathlib
import random
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import tipsterpath  # noqa: E402,F401
import calibrate  # noqa: E402
import testkit  # noqa: E402

PARAMS = {"home_adv": 3.0, "k": 6, "sigma_margin": 12.0, "sigma_total": 16.0, "double_adj": 0.0,
          "fitted_on": ["E2020"]}


def _pairs(n_teams: int) -> list[list[tuple[int, int]]]:
    """Single round robin by the circle method: n-1 rounds of n/2 pairs."""
    teams = list(range(n_teams))
    rounds = []
    for _ in range(n_teams - 1):
        rounds.append([(teams[i], teams[-1 - i]) for i in range(n_teams // 2)])
        teams = [teams[0], teams[-1]] + teams[1:-1]
    return rounds


def _round_days(year: int, n_rounds: int) -> list[tuple[int, ...]]:
    """Day offsets per round, per pair index. Weeks alternate: a double week puts its first round on
    Monday (even pairs) or Tuesday (odd pairs) and its second on Friday, so only Tuesday's teams are
    in a second game within 3 days; a single week plays on Thursday."""
    days, week = [], 0
    while len(days) < n_rounds:
        start = 7 * week
        if week % 2 == 0:
            days += [(start, start + 1), (start + 4,)]
        else:
            days.append((start + 3,))
        week += 1
    return days[:n_rounds]


def season(year: int, rng: random.Random, *, home_adv=4.0, double=0.0, sigma=12.0, n_teams=12,
           strengths=None) -> dict:
    """A double round robin with double weeks (see _round_days). `double` is the margin change for a
    team in its second game of a double week."""
    name = f"E{year}"
    strengths = strengths or [(rng.gauss(0, 4), rng.gauss(0, 4)) for _ in range(n_teams)]
    slot = list(range(n_teams))
    rng.shuffle(slot)
    first = [[(slot[a], slot[b]) for a, b in r] for r in _pairs(n_teams)]
    rounds = first + [[(b, a) for a, b in r] for r in first]
    day0 = dt.datetime(year, 10, 1, 18, tzinfo=dt.timezone.utc)
    games, lines, last_day = [], [], {}
    code = 0
    for rnd, (pairs, offsets) in enumerate(zip(rounds, _round_days(year, len(rounds))), start=1):
        played = []
        for i, (h, a) in enumerate(pairs):
            when = day0 + dt.timedelta(days=offsets[i % len(offsets)])
            played += [(h, when), (a, when)]
            code += 1
            tired = [t in last_day and (when - last_day[t]).days <= 3 for t in (h, a)]
            (oh, dh), (oa, da) = strengths[h], strengths[a]
            margin_true = (oh - da) - (oa - dh) + home_adv + double * (int(tired[0]) - int(tired[1]))
            total_true = 160 + (oh - da) + (oa - dh)
            margin = round(rng.gauss(margin_true, sigma)) or rng.choice((1, -1))
            total = round(rng.gauss(total_true, 16))
            if (total + margin) % 2:
                total += 1
            ph, pa = (total + margin) // 2, (total - margin) // 2
            key = f"{name}-{code}"
            start = when.strftime("%Y-%m-%dT%H:%M:%SZ")
            games.append({"key": key, "season": name, "code": code, "round": rnd, "phase": "RS",
                          "start_utc": start, "home": f"T{h:02d}", "away": f"T{a:02d}", "home_pts": ph,
                          "away_pts": pa, "played": True, "neutral": False})
            for team, opp, pts, opp_pts, home in ((h, a, ph, pa, True), (a, h, pa, ph, False)):
                lines.append({"game": key, "start_utc": start, "team": f"T{team:02d}", "opp": f"T{opp:02d}",
                              "home": home, "neutral": False, "pts": pts, "fga": 72, "fta": 0, "oreb": 0,
                              "tov": 0, "minutes": 200, "opp_pts": opp_pts})
        last_day.update(played)
    return {"games": games, "lines": lines}


def _data(seed: int, years, **kwargs) -> dict:
    rng = random.Random(seed)
    strengths = [(rng.gauss(0, 4), rng.gauss(0, 4)) for _ in range(12)]
    return {f"E{y}": season(y, rng, strengths=strengths, **kwargs) for y in years}


def _rows(seed: int, n: int, sigma_true: float, sigma_model: float = 12.0) -> list[dict]:
    """Rows whose outcomes are drawn around the expectation with spread `sigma_true`; the model
    claims `sigma_model` (smaller is overconfident: probabilities pushed toward 0 and 1)."""
    rng = random.Random(seed)
    rows = []
    for i in range(n):
        margin, total = rng.gauss(3, 8), rng.gauss(160, 8)
        actual_m = round(rng.gauss(margin, sigma_true)) or rng.choice((1, -1))
        rows.append({"key": f"g{i}", "exp": {"margin": margin, "total": total},
                     "actual_margin": actual_m, "actual_total": round(rng.gauss(total, 16)),
                     "base_margin": 3.0, "base_total": 160.0, "neutral": False,
                     "sigma_margin": sigma_model, "sigma_total": 16.0})
    return rows


def test_snapshot_matches_model_predict():
    import model
    import ratings
    data = _data(9, [2019, 2020])
    last = ratings.Season(data["E2019"]["lines"])
    snaps = calibrate.snapshots(data["E2020"], last)
    for snap in (snaps[0], snaps[40], snaps[-1]):
        g = snap["game"]
        lines_before = [x for x in data["E2020"]["lines"] if x["start_utc"] < g["start_utc"]]
        direct = model.predict(g, lines_before, last, PARAMS, snap["double"])
        assert calibrate.expect_from(snap, PARAMS) == direct, g["key"]


def test_second_of_double():
    # Week 1: round 1 on Monday or Tuesday, round 2 on Friday. Week 2: round 3 on Thursday.
    games = _data(1, [2020])["E2020"]["games"]
    tuesday = {t for g in games if g["round"] == 1 and g["start_utc"].startswith("2020-10-02")
               for t in (g["home"], g["away"])}
    assert len(tuesday) == 6
    flags = calibrate.double_flags(games)
    for g in games:
        if g["round"] == 2:
            expected = (g["home"] in tuesday, g["away"] in tuesday)
            assert flags[g["key"]] == expected, (g, expected)
            assert calibrate.second_of_double(g["home"], g, games) is expected[0]
        elif g["round"] in (1, 3):
            assert flags[g["key"]] == (False, False), g
    assert any(flags[g["key"]][0] != flags[g["key"]][1] for g in games), "no one-sided rest in the schedule"


def test_fit_recovers_home_advantage():
    data = _data(2, range(2015, 2020), sigma=3.0)  # 660 games: standard error 0.12
    params = calibrate.fit(list(data), [], data)
    assert abs(params["home_adv"] - 4.0) < 0.3, params
    assert 0 <= params["k"] <= 20 and params["sigma_margin"] > 0 and params["sigma_total"] > 0
    assert params["fitted_on"] == list(data)


def test_no_fans_seasons_excluded_from_home_adv():
    rng = random.Random(3)
    strengths = [(rng.gauss(0, 4), rng.gauss(0, 4)) for _ in range(12)]
    data = {f"E{y}": season(y, rng, strengths=strengths, sigma=6.0, home_adv=0.0 if y in (2019, 2020) else 4.0)
            for y in range(2016, 2022)}
    kept = calibrate.fit(list(data), ["E2019", "E2020"], data)
    mixed = calibrate.fit(list(data), [], data)
    assert abs(kept["home_adv"] - 4.0) < 0.35 and mixed["home_adv"] < kept["home_adv"] - 1, (kept, mixed)


def test_double_adj_kept_only_if_it_helps():
    # About 14 games a season have one-sided rest, so the effect needs low noise to be seen.
    seasons = [f"E{y}" for y in range(2013, 2021)]
    tired = calibrate.fit(seasons, [], _data(4, range(2013, 2021), double=-3.0, sigma=6.0))
    rested = calibrate.fit(seasons, [], _data(4, range(2013, 2021), double=0.0, sigma=6.0))
    assert -4.5 < tired["double_adj"] < -1.5, tired
    assert rested["double_adj"] == 0, rested


def test_walk_forward_uses_only_earlier_games():
    data = _data(5, range(2018, 2021))
    rows = calibrate.walk_forward("E2020", ["E2018", "E2019"], data)
    changed = {k: dict(v) for k, v in data.items()}
    season = changed["E2020"]
    last = season["games"][-1]
    season["games"] = season["games"][:-1] + [dict(last, home_pts=last["home_pts"] + 60)]
    season["lines"] = [dict(x, pts=x["pts"] + 60) if x["game"] == last["key"] and x["home"] else
                       dict(x, opp_pts=x["opp_pts"] + 60) if x["game"] == last["key"] else x
                       for x in season["lines"]]
    again = calibrate.walk_forward("E2020", ["E2018", "E2019"], changed)
    assert [r["exp"] for r in rows] == [r["exp"] for r in again]
    assert rows[-1]["actual_margin"] + 60 == again[-1]["actual_margin"]
    assert all(r["key"].startswith("E2020-") for r in rows) and len(rows) == len(data["E2020"]["games"])


def test_calibrated_model_passes():
    rows = _rows(6, 3 * 330, sigma_true=12.0)
    result = calibrate.gate(rows)
    for market in ("moneyline", "handicap", "total"):
        assert result[market]["pass"], (market, result[market]["why"])
    assert result["n"] == len(rows)


def test_overconfident_model_fails_bands():
    rows = _rows(7, 3 * 330, sigma_true=12.0, sigma_model=6.0)
    result = calibrate.gate(rows)
    assert not result["moneyline"]["pass"] and not result["handicap"]["pass"]
    assert any("band" in why for why in result["handicap"]["why"]), result["handicap"]["why"]


def test_band_under_50_not_judged():
    off = {"lo": 0.1, "n": 49, "games": 49, "p": 0.15, "obs": 0.40}
    bulk = {"lo": 0.5, "n": 2000, "games": 2000, "p": 0.5, "obs": 0.5}
    assert calibrate.judge([off, bulk]) == []
    assert any("band" in why for why in calibrate.judge([dict(off, n=50, games=50), bulk]))


def test_band_interval_uses_games_and_all_judged_bands():
    # 2 judged bands: z = 2.24 (95% over both). 400 games: half-width 2.24 x 0.025 = 0.056.
    ok = {"lo": 0.5, "n": 3600, "games": 400, "p": 0.5, "obs": 0.55}
    other = {"lo": 0.2, "n": 3600, "games": 400, "p": 0.25, "obs": 0.25}
    assert not [w for w in calibrate.judge([ok, other]) if "band" in w]
    assert [w for w in calibrate.judge([dict(ok, obs=0.56), other]) if "band" in w]


def test_ece_limit():
    # Noise-corrected: a band's error counts above sqrt(2/pi) x its binomial standard deviation.
    # n = 100,000 at p = 0.5: sd 0.00158, noise term 0.00126.
    noise = math.sqrt(2 / math.pi) * math.sqrt(0.25 / 100_000)
    band = {"lo": 0.5, "n": 100_000, "games": 100_000, "p": 0.5}
    over = calibrate.judge([dict(band, obs=0.5 + 0.031 + noise)])
    assert any("calibration error 0.031" in why for why in over), over
    assert not [w for w in calibrate.judge([dict(band, obs=0.5 + 0.029 + noise)]) if "calibration error" in w]


def test_bands_group_by_tenths():
    bands = calibrate.bands([(0.05, 0, "a"), (0.15, 1, "a"), (0.19, 0, "b"), (0.12, 1, "b"), (1.0, 1, "c")])
    assert [(b["lo"], b["n"], b["games"]) for b in bands] == [(0.0, 1, 1), (0.1, 3, 2), (0.9, 1, 1)]
    assert math.isclose(bands[1]["p"], 0.46 / 3) and math.isclose(bands[1]["obs"], 2 / 3)


def test_failed_market_recorded_not_raised():
    testkit.temp_bob()
    rows = _rows(8, 600, sigma_true=12.0)
    for r in rows:
        r["exp"]["total"] += 30
    result = calibrate.gate(rows)
    assert result["moneyline"]["pass"] and not result["total"]["pass"]
    assert any("RMSE" in why for why in result["total"]["why"])
    calibrate.save(result, PARAMS)
    assert calibrate.passed_markets() == ["moneyline", "handicap"]


def test_no_calibration_file_passes_nothing():
    testkit.temp_bob()
    assert calibrate.passed_markets() == []


if __name__ == "__main__":
    raise SystemExit(testkit.run(globals()))
