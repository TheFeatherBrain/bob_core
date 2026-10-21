#!/usr/bin/env python3
"""The Tipster board: mode and HALT, upcoming tips with Claude's decision, both wallets, profit, ROI
and CLV with n per track and market, settled tips, the odds budget and the calibration result.

Built on core's board_shell (plumbing, /api/state, /events). Every number comes from the ledger
replay, the budget ledger or the calibration file, which code computed; the page only draws them.
Why the banners: CLV needs about 50 tips to mean anything and profit about 500 (spec 1); a number
shown without its n reads as a verdict it is not. Why void tips are left out of profit and ROI:
the stake came back, so they say nothing either way. A CLV of "could not tell" is counted, never
averaged as zero.

Usage:
    python3 harness/tipster_board.py               # port and title from bob.toml
    python3 harness/tipster_board.py --port 8793 --quiet
"""
from __future__ import annotations

import argparse
import datetime as dt
import pathlib
import sys
import threading
from http.server import ThreadingHTTPServer

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import tipsterpath  # noqa: E402,F401
import bobcfg  # noqa: E402
import board_shell  # noqa: E402
import budget  # noqa: E402
import calibrate  # noqa: E402
import notify  # noqa: E402
import tips  # noqa: E402
import tipstercfg  # noqa: E402

CLV_MIN = 50
PROFIT_MIN = 500
SETTLED_ROWS = 50


def summarize(settled: list[dict]) -> dict:
    counted = [t for t in settled if t["result"] != "void"]
    staked = sum(t["stake"] for t in counted)
    profit = round(sum(t["pnl"] for t in counted), 2)
    clvs = [t["clv"] for t in counted if t.get("clv") is not None]
    return {"n": len(counted), "void": len(settled) - len(counted), "staked": staked, "profit": profit,
            "roi": profit / staked if staked else None, "clv_n": len(clvs), "clv_unknown": len(counted) - len(clvs),
            "clv_mean": sum(clvs) / len(clvs) if clvs else None,
            "clv_positive_share": sum(1 for c in clvs if c > 0) / len(clvs) if clvs else None}


def scores(settled: list[dict]) -> dict:
    out = {"all": summarize(settled)}
    for market in tipstercfg.MARKETS:
        out[market] = summarize([t for t in settled if t["market"] == market])
    return out


def banners(summary: dict) -> list[str]:
    out = []
    if summary["clv_n"] < CLV_MIN:
        out.append("too early to judge CLV")
    if summary["n"] < PROFIT_MIN:
        out.append("profit is mostly luck at this size")
    return out


def _wallet(state: dict, start: float) -> dict:
    points, wallet = [], start
    for t in sorted(state["settled"], key=lambda t: t["at_settled"]):
        wallet = round(wallet + t["pnl"], 2)
        points.append({"at": t["at_settled"], "wallet": wallet})
    return {"now": round(state["wallet"], 2), "start": start, "points": points}


def replayed(track: str) -> dict:
    """tips.replay with each settled tip's settlement time kept apart from its tip time."""
    state = tips.replay(track)
    settled_at = {r["tip"]: r["at"] for r in tips.rows()[0] if r.get("kind") == "settled"}
    state["settled"] = [dict(t, at_settled=settled_at.get(t["id"], "")) for t in state["settled"]]
    return state


def _upcoming(states: dict) -> list[dict]:
    lines = tips.rows()[0]
    vetoes = {(r["candidate"].get("game"), r["candidate"].get("family")): r["reason"] for r in lines
              if r.get("kind") == "refused" and r.get("track") == "reviewed"}
    reviewed = {(t["game"], t["family"]): t for t in states["reviewed"]["open"]}
    out = []
    for t in states["model"]["open"] + [t for k, t in reviewed.items()
                                        if k not in {(m["game"], m["family"]) for m in states["model"]["open"]}]:
        key = (t["game"], t["family"])
        r = reviewed.get(key)
        latest = (r or {}).get("updates") or []
        out.append({"game": t["game"], "family": t["family"], "start_utc": t.get("start_utc"), "market": t["market"],
                    "side": t["side"], "line": t["line"], "book": notify.book((r or t)["book"]),
                    "odds": (r or t)["odds"], "edge": (r or t)["edge"],
                    "model_stake": t["stake"] if t["track"] == "model" else None,
                    "stake": r["stake"] if r else None,
                    "decision": r["review"]["decision"] if r else vetoes.get(key, "pending"),
                    "reason": r["review"]["reason"] if r else None,
                    "update": latest[-1]["status"] if latest else None} | _described(r or t))
    return sorted(out, key=lambda u: (u["start_utc"] or "", u["game"], u["family"]))


def _described(t: dict) -> dict:
    """Teams, local kick-off and the bet in words, worded as in the Telegram message."""
    return {"match": notify.match(t), "kickoff": notify.kickoff(t) if t.get("start_utc") else None,
            "round": t.get("round"), "pick": notify.pick(t), "terms": notify.terms(t)}


def state(now: dt.datetime | None = None, title: str = "Tipster") -> dict:
    now = now or dt.datetime.now(dt.timezone.utc)
    cfg = tipstercfg.load()
    states = {track: replayed(track) for track in tips.TRACKS}
    result = calibrate.report() or {}
    all_scores = {track: scores(states[track]["settled"]) for track in tips.TRACKS}
    settled = sorted((dict(t, track=track) for track in tips.TRACKS for t in states[track]["settled"]),
                     key=lambda t: t["at_settled"], reverse=True)[:SETTLED_ROWS]
    return {"title": title, "mode": cfg["mode"], "halt": tips.halted(), "torn": states["reviewed"]["torn"],
            "upcoming": _upcoming(states), "scores": all_scores,
            "banners": {track: banners(all_scores[track]["all"]) for track in tips.TRACKS},
            "wallets": {track: _wallet(states[track], cfg["wallet_start"]) for track in tips.TRACKS},
            "settled": [{k: t.get(k) for k in ("track", "game", "market", "side", "line", "odds", "stake",
                                               "result", "pnl", "clv", "clv_note", "at_settled")}
                        | {"book": notify.book(t["book"])} | _described(t) for t in settled],
            "budget": budget.status(now),
            "calibration": {m: result.get(m) for m in tipstercfg.MARKETS} | {"n": result.get("n"),
                                                                              "seasons": result.get("seasons")}}


class Watched:
    def __init__(self, files: list[pathlib.Path]):
        self.files = files

    def fingerprint(self) -> tuple:
        out = []
        for path in self.files:
            try:
                st = path.stat()
                out.append((str(path), st.st_mtime_ns, st.st_size))
            except OSError:
                out.append((str(path), None, None))
        return tuple(out)


class Model:
    def __init__(self, title: str):
        self.title = title
        state_dir = tipstercfg.state_dir()
        self.ev = Watched([tips.ledger_path(), tips.halt_path(), budget.ledger_path(),
                           state_dir / "calibration.json", bobcfg.bob_root() / "bob.toml"])
        self._lock = threading.Lock()
        self.snapshot: dict | None = None
        self._builds = 0

    def rebuild(self) -> dict:
        self._builds += 1
        return dict(state(title=self.title), version=self._builds)


class Handler(board_shell.BaseHandler):
    pass


PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>Tipster</title>
<style>
:root{--bg:#0f141b;--fg:#d7dde6;--muted:#7d8a9c;--line:#2a3442;--up:#5fb37a;--down:#e07a5f;--model:#7d8a9c}
body{font:14px/1.5 system-ui,sans-serif;margin:16px;max-width:1100px;background:var(--bg);color:var(--fg)}
h1{font-size:20px}h2{font-size:15px;margin-top:28px;border-bottom:1px solid var(--line);padding-bottom:4px}
table{border-collapse:collapse;width:100%;font-size:13px}td,th{padding:4px 6px;border-bottom:1px solid var(--line);text-align:left}
.muted{color:var(--muted)}.banner{border:1px solid var(--down);padding:6px 10px;margin:8px 0}.wrap{overflow-x:auto}
svg{width:100%;height:160px;background:#121922}
</style></head><body>
<h1 id="title">Tipster</h1>
<div id="banners"></div>
<h2>Next games and tips</h2><div class="wrap"><table id="upcoming"></table></div>
<h2>Wallets (simulated EUR) <span class="muted">reviewed: solid, model: dashed</span></h2><svg id="wallets" viewBox="0 0 1000 160" preserveAspectRatio="none"></svg>
<p id="walletnow" class="muted"></p>
<h2>Scores</h2><div id="scorebanners"></div><div class="wrap"><table id="scores"></table></div>
<h2>Settled tips</h2><div class="wrap"><table id="settled"></table></div>
<h2>Odds budget and calibration</h2><div class="wrap"><table id="system"></table></div>
<script>
function el(tag, text, klass){ var e = document.createElement(tag); if (text !== undefined) e.textContent = text; if (klass) e.className = klass; return e; }
function pct(x){ return x === null || x === undefined ? "n/a" : (x * 100).toFixed(1) + "%"; }
function num(x, d){ return x === null || x === undefined ? "n/a" : Number(x).toFixed(d); }
function bet(t){ return t.terms ? t.pick + ": wins if " + t.terms : t.pick; }
function table(id, head, rows){
  var t = document.getElementById(id); t.textContent = "";
  var tr = el("tr"); head.forEach(function(h){ tr.appendChild(el("th", h)); }); t.appendChild(tr);
  if (!rows.length){ var r = el("tr"); var td = el("td", "none", "muted"); td.colSpan = head.length; r.appendChild(td); t.appendChild(r); return; }
  rows.forEach(function(cells){ var r = el("tr"); cells.forEach(function(c){ r.appendChild(el("td", c === null || c === undefined ? "n/a" : String(c))); }); t.appendChild(r); });
}
function line(points, start, lo, hi, dash, colour){
  var all = [{wallet: start}].concat(points), n = Math.max(all.length - 1, 1);
  var d = all.map(function(p, i){ return (i * 1000 / n).toFixed(1) + "," + (150 - (p.wallet - lo) / (hi - lo || 1) * 140).toFixed(1); }).join(" ");
  var pl = document.createElementNS("http://www.w3.org/2000/svg", "polyline");
  pl.setAttribute("points", d); pl.setAttribute("fill", "none"); pl.setAttribute("stroke", colour); pl.setAttribute("stroke-width", "2");
  if (dash) pl.setAttribute("stroke-dasharray", "6 4");
  return pl;
}
function show(s){
  document.getElementById("title").textContent = s.title + " (mode " + s.mode + ")";
  var b = document.getElementById("banners"); b.textContent = "";
  if (s.mode === "off") b.appendChild(el("div", "Mode is off: nothing reads or sends. A person changes the mode in bob.toml.", "banner"));
  if (s.halt) b.appendChild(el("div", "HALT: " + s.halt + ". No new tips; watching and settling continue. A person deletes state/HALT to resume.", "banner"));
  if (s.torn) b.appendChild(el("div", s.torn + " torn ledger line(s) skipped.", "banner"));
  table("upcoming", ["starts (Athens)", "match", "bet", "odds", "edge", "model stake", "your stake", "Claude's call", "latest update"],
    s.upcoming.map(function(u){ return [u.kickoff, u.match + (u.round === null ? "" : " (round " + u.round + ")"), bet(u),
      num(u.odds, 2) + " on " + u.book, pct(u.edge), u.model_stake, u.stake === null ? "none" : "EUR " + u.stake,
      u.reason ? u.decision + ": " + u.reason : u.decision, u.update]; }));
  var w = s.wallets, svg = document.getElementById("wallets"); svg.textContent = "";
  var vals = [w.reviewed.start].concat(w.reviewed.points.map(function(p){ return p.wallet; }), w.model.points.map(function(p){ return p.wallet; }));
  var lo = Math.min.apply(null, vals) - 10, hi = Math.max.apply(null, vals) + 10;
  svg.appendChild(line(w.model.points, w.model.start, lo, hi, true, "var(--model)"));
  svg.appendChild(line(w.reviewed.points, w.reviewed.start, lo, hi, false, "var(--up)"));
  document.getElementById("walletnow").textContent = "reviewed EUR " + num(w.reviewed.now, 2) + ", model EUR " + num(w.model.now, 2);
  var sb = document.getElementById("scorebanners"); sb.textContent = "";
  ["reviewed", "model"].forEach(function(t){ s.banners[t].forEach(function(x){ sb.appendChild(el("div", t + ": " + x, "banner")); }); });
  var rows = [];
  ["reviewed", "model"].forEach(function(t){ ["all", "moneyline", "handicap", "total"].forEach(function(m){
    var x = s.scores[t][m]; rows.push([t, m, x.n, num(x.profit, 2), pct(x.roi), x.clv_mean === null ? "n/a" : num(x.clv_mean, 3) + " (n=" + x.clv_n + ")",
      pct(x.clv_positive_share), x.clv_unknown, x.void]); }); });
  table("scores", ["track", "market", "n", "profit EUR", "ROI", "mean CLV", "CLV > 0", "CLV could not tell", "void"], rows);
  table("settled", ["track", "played (Athens)", "match", "bet", "odds", "stake", "result", "profit", "CLV"], s.settled.map(function(t){
    return [t.track, t.kickoff, t.match, bet(t), num(t.odds, 2) + " on " + t.book, t.stake, t.result,
      num(t.pnl, 2), t.clv === null ? t.clv_note : num(t.clv, 3) + " (" + t.clv_note + ")"]; }));
  var c = s.calibration, sys = [["odds requests " + s.budget.month, s.budget.used + " used of " + s.budget.allowance + ", " + s.budget.remaining + " left"]];
  ["moneyline", "handicap", "total"].forEach(function(m){ var r = c[m]; sys.push(["calibration " + m, r ? (r.pass ? "PASS" : "FAIL: " + r.why.join("; ")) : "not run: no tips"]); });
  sys.push(["calibration games", c.n === null ? "n/a" : c.n + " (" + (c.seasons || []).join(", ") + ")"]);
  table("system", ["what", "state"], sys);
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
