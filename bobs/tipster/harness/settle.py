#!/usr/bin/env python3
"""Settle tips from the official result, and score each against the fair closing price (CLV).

Why the official feed decides: it is the record both bookmakers settle by. Why the void rule is
"not played within `void_after_hours` of the start time recorded on the tip" (stake returned):
bookmakers void a postponed game after a short window, so a tip on it is never settled against a
replay days later as if nothing happened. Why CLV uses the sharp book with its margin removed
(power method): its closing price is the best public estimate of the true probability; when it has
no price, the consensus of the other books in the snapshot stands in, and the tip says so. Why a
closing line on a different number is moved to the tip's line through the model's normal: the
alternative, comparing a -5.5 price with a -7.5 one, measures the line move, not the price.
No snapshot means "could not tell", never zero.

Usage:
    python3 harness/settle.py --run     # SETTLED lines, then OK settled=N open=M
"""
from __future__ import annotations

import argparse
import datetime as dt
import pathlib
import statistics
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import tipsterpath  # noqa: E402,F401
import calibrate  # noqa: E402
import fetch  # noqa: E402
import pricing  # noqa: E402
import stats  # noqa: E402
import store  # noqa: E402
import tips  # noqa: E402
import tipstercfg  # noqa: E402

NORMAL = statistics.NormalDist()
OTHER = {"home": "away", "away": "home", "over": "under", "under": "over"}


def result(tip: dict, game: dict) -> str:
    margin = game["home_pts"] - game["away_pts"]
    if tip["market"] == "total":
        diff = (game["home_pts"] + game["away_pts"] - tip["line"]) * (1 if tip["side"] == "over" else -1)
    else:
        diff = (margin if tip["side"] == "home" else -margin) + (tip["line"] or 0)
    return "win" if diff > 0 else "push" if diff == 0 else "loss"


def pnl(tip: dict, outcome: str) -> float:
    return round({"win": tip["stake"] * (tip["odds"] - 1), "loss": -tip["stake"]}.get(outcome, 0.0), 2)


def _moved(tip: dict, p: float, closing_line: float, params: dict) -> float:
    """P(win) at the tip's line, given fair P(win) = p at `closing_line` (both from the side's view)."""
    if tip["market"] == "handicap":
        sigma = params["sigma_margin"]
        mean = -closing_line + sigma * NORMAL.inv_cdf(p)
        return NORMAL.cdf((mean + tip["line"]) / sigma)
    sigma = params["sigma_total"]
    if tip["side"] == "over":
        mean = closing_line + sigma * NORMAL.inv_cdf(p)
        return 1 - NORMAL.cdf((tip["line"] - mean) / sigma)
    mean = closing_line - sigma * NORMAL.inv_cdf(p)
    return NORMAL.cdf((tip["line"] - mean) / sigma)


def _fair(tip: dict, book_prices: dict | None, params: dict) -> tuple[float, bool] | None:
    prices = (book_prices or {}).get(tip["market"])
    side, other = tip["side"], OTHER[tip["side"]]
    if not prices or not all(isinstance(prices.get(s), (int, float)) and prices[s] > 1 for s in (side, other)):
        return None
    p = pricing.fair_power([prices[side], prices[other]])[0]
    if tip["market"] == "moneyline":
        return p, False
    line = pricing.side_line(tip["market"], side, prices.get("line"))
    if line is None:
        return None
    return (p, False) if line == tip["line"] else (_moved(tip, p, line, params), True)


def clv(tip: dict, closing_books: dict | None, cfg: dict, params: dict) -> tuple[float | None, str]:
    if not closing_books:
        return None, "could not tell"
    sharp = _fair(tip, closing_books.get(cfg["sharp_book"]), params)
    if sharp:
        p, moved = sharp
        note = cfg["sharp_book"] + ("; line moved" if moved else "")
    else:
        others = [f for b, v in closing_books.items() if b != cfg["sharp_book"] for f in [_fair(tip, v, params)] if f]
        if not others:
            return None, "could not tell"
        p = sum(f[0] for f in others) / len(others)
        note = "consensus" + ("; line moved" if any(f[1] for f in others) else "")
    return tip["odds"] * p - 1, note


def _closing(game_key: str) -> dict | None:
    snaps = [r for r in tips.rows()[0] if r.get("kind") == "closing" and r.get("game") == game_key]
    return None if not snaps or snaps[-1].get("missed") else snaps[-1].get("books")


def run(now: dt.datetime | None = None, *, refresh: bool = True, getter=fetch.get) -> tuple[int, list[str]]:
    now = now or dt.datetime.now(dt.timezone.utc)
    cfg = tipstercfg.load()
    if not tipstercfg.is_on():
        return 0, ["OK mode off"]
    params = store.read_json(calibrate.params_path(), None)
    open_tips = [t for track in tips.TRACKS for t in tips.replay(track)["open"]]
    games: dict[str, dict] = {}
    for season in sorted({t["season"] for t in open_tips}):
        games.update({g["key"]: g for g in stats.games(season, refresh=refresh, getter=getter, now=now)})
    out, still_open = [], 0
    for tip in open_tips:
        game = games.get(tip["game"])
        if game and game["played"]:
            outcome = result(tip, game)
            value, note = clv(tip, _closing(tip["game"]), cfg, params) if params else (None, "could not tell")
        elif now - stats.parse_utc(tip["start_utc"]) >= dt.timedelta(hours=cfg["void_after_hours"]):
            outcome, value, note = "void", None, "could not tell"
        else:
            still_open += 1
            continue
        tips.append({"kind": "settled", "tip": tip["id"], "at": now.isoformat(), "result": outcome,
                     "pnl": pnl(tip, outcome), "clv": value, "clv_note": note,
                     "score": [game["home_pts"], game["away_pts"]] if game and game["played"] else None})
        shown = note if value is None else f"{value:.3f} ({note})"
        out.append(f"SETTLED {tip['id']} {outcome} pnl={pnl(tip, outcome):.2f} clv={shown}")
    tips.replay("reviewed")
    out.append(f"OK settled={len(out)} open={still_open}")
    return 0, out


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Settle finished or void tips.")
    parser.add_argument("--run", action="store_true", required=True)
    parser.parse_args(argv)
    try:
        code, lines = run()
    except (stats.StatsError, fetch.FetchError) as e:
        code, lines = 1, [f"FAIL: {type(e).__name__}: {e}"]
    print("\n".join(lines))
    return code


if __name__ == "__main__":
    import oscompat
    oscompat.utf8_stdio()
    raise SystemExit(main(sys.argv[1:]))
