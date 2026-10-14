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
