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
