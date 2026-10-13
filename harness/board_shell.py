#!/usr/bin/env python3
"""The board every Bob gets: an HTTP shell, a live stream, and a default page.

A Bob whose manifest declares no panels (`board.panels = []`) is served the default page: the
health of its loops and the release notes not yet posted. A Bob with a panel module
(`board.panels = ["my_panel"]`) hands the whole board to that module's `main`, which builds its
own model and page on `BaseHandler`. The shell owns the plumbing both share: response helpers,
`/api/state`, `/api/health`, `/events`, and the 404 for anything nobody claims.

Usage:
    python3 harness/board_shell.py                # port and title from bob.toml
    python3 harness/board_shell.py --port 8790 --quiet
"""
from __future__ import annotations

import argparse
import importlib
import json
import queue
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import bobcfg  # noqa: E402
import loop_passes  # noqa: E402
import release_digest  # noqa: E402
import skill_index  # noqa: E402


# ---- live stream --------------------------------------------------------------------------------


class Hub:
    """Fan-out for SSE subscribers. One bounded queue per connection; slow readers get dropped."""

    def __init__(self, backlog: int = 16):
        self._lock = threading.Lock()
        self._subs: set[queue.Queue] = set()
        self._backlog = backlog

    def subscribe(self) -> queue.Queue:
        q: queue.Queue = queue.Queue(maxsize=self._backlog)
        with self._lock:
            self._subs.add(q)
        return q

    def unsubscribe(self, q: queue.Queue) -> None:
        with self._lock:
            self._subs.discard(q)

    def count(self) -> int:
        with self._lock:
            return len(self._subs)

    def publish(self, frame: str) -> None:
        with self._lock:
            subs = list(self._subs)
        for q in subs:
            try:
                q.put_nowait(frame)
            except queue.Full:
                # A client too slow to keep up would otherwise pin the whole snapshot in memory.
                # Dropping it is safe: EventSource reconnects and gets a fresh snapshot on open.
                self.unsubscribe(q)


def sse(event: str, data) -> str:
    payload = data if isinstance(data, str) else json.dumps(data, default=str)
    body = "".join(f"data: {line}\n" for line in payload.split("\n"))
    return f"event: {event}\n{body}\n"


class Watcher(threading.Thread):
    """Stat the evidence tree on a timer; rebuild and publish only when it actually changed.

    The model needs four things: `ev.fingerprint()`, `rebuild()`, `_lock` and `snapshot`.
    """

    daemon = True

    def __init__(self, model, hub: Hub, interval: float):
        super().__init__(name="evidence-watcher")
        self.model = model
        self.hub = hub
        self.interval = interval
        self.ticks = 0
        self.pushes = 0

    def run(self) -> None:
        last = None
        while True:
            try:
                self.ticks += 1
                current = self.model.ev.fingerprint()
                if current != last:
                    last = current
                    snapshot = self.model.rebuild()
                    with self.model._lock:
                        self.model.snapshot = snapshot
                    self.pushes += 1
                    self.hub.publish(sse("state", snapshot))
            except Exception as exc:                        # a watcher that dies is a dead page
                self.hub.publish(sse("warn", {"error": f"{type(exc).__name__}: {exc}"}))
            time.sleep(self.interval)


# ---- http ---------------------------------------------------------------------------------------


class BaseHandler(BaseHTTPRequestHandler):
    """Shell routes first, then the panel's `panel_route`, then 404."""

    protocol_version = "HTTP/1.1"
    server_version = "Bob/1.0"
    #: The comment line that opens every event stream.
    stream_banner = b": bob - stream open\n\n"
    #: The page served at `/`.
    page = b""

    model: object
    hub: Hub
    heartbeat: float
    quiet: bool

    # -- plumbing ----------------------------------------------------------------------------

    def log_message(self, fmt, *args):                      # noqa: D401 - quieter by default
        if not self.quiet:
            sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    def _send(self, code: int, body: bytes, ctype: str, cache: str = "no-store", extra=()):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", cache)
        self.send_header("X-Content-Type-Options", "nosniff")
        for key, value in extra:
            self.send_header(key, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, code: int, payload) -> None:
        self._send(code, json.dumps(payload, default=str).encode("utf-8"),
                   "application/json; charset=utf-8")

    def _reject(self, code: int, reason: str) -> None:
        self._json(code, {"error": reason})

    # -- routes ------------------------------------------------------------------------------

    def do_HEAD(self):                                      # noqa: N802 - stdlib naming
        self.do_GET()

    def do_GET(self):                                       # noqa: N802 - stdlib naming
        raw_path = urlsplit(self.path).path
        path = unquote(raw_path)

        if path in ("/", "/index.html"):
            return self._send(200, self.page, "text/html; charset=utf-8")
        if path == "/api/state":
            return self._json(200, self.current())
        if path == "/api/health":
            return self._json(200, {
                "ok": True,
                "version": self.current().get("version"),
                "subscribers": self.hub.count(),
                **self.health_extra(),
            })
        if path == "/events":
            return self.serve_events()
        if self.panel_route(path, raw_path):
            return None
        return self._reject(404, "no such route")

    def panel_route(self, path: str, raw_path: str) -> bool:
        """A panel's own routes. Return True when the request was answered."""
        return False

    def health_extra(self) -> dict:
        """Fields a panel appends to `/api/health`."""
        return {}

    def current(self) -> dict:
        with self.model._lock:
            snapshot = self.model.snapshot
        if snapshot:
            return snapshot
        snapshot = self.model.rebuild()
        with self.model._lock:
            self.model.snapshot = snapshot
        return snapshot

    def serve_events(self) -> None:
        q = self.hub.subscribe()
        try:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache, no-transform")
            self.send_header("X-Accel-Buffering", "no")
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(self.stream_banner)
            self._emit(sse("state", self.current()))
            while True:
                try:
                    frame = q.get(timeout=self.heartbeat)
                except queue.Empty:
                    # Both forms, on purpose: the comment keeps intermediaries and raw readers
                    # happy, and the named event is the one EventSource can actually observe -
                    # which is what lets the page tell "quiet" from "frozen".
                    beat = time.time()
                    self._emit(f": keep-alive {beat:.0f}\n\n"
                               + sse("ping", {"t": beat, "subs": self.hub.count()}))
                    continue
                self._emit(frame)
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            self.hub.unsubscribe(q)

    def _emit(self, text: str) -> None:
        self.wfile.write(text.encode("utf-8"))
        self.wfile.flush()


# ---- default board ------------------------------------------------------------------------------


class _Files:
    """The files the default page is built from; the watcher rebuilds when any of them changes."""

    def __init__(self, paths):
        self.paths = list(paths)

    def fingerprint(self) -> tuple:
        out = []
        for p in self.paths:
            try:
                st = p.stat()
                out.append((str(p), st.st_mtime_ns, st.st_size))
            except OSError:
                out.append((str(p), None, None))
        return tuple(out)


class DefaultModel:
    """Loop health, unposted release notes and lesson matches, read through the core modules."""

    def __init__(self, cfg: dict):
        self.title = bobcfg.get(cfg, "board.title")
        self.ev = _Files([release_digest.NOTES, loop_passes.LEDGER, skill_index.fires_path(),
                          skill_index.skills_dir() / "INDEX.md"])
        self._lock = threading.Lock()
        self.snapshot: dict | None = None
        self._builds = 0

    def rebuild(self) -> dict:
        self._builds += 1
        missed = loop_passes.audit()
        try:
            notes = release_digest.NOTES.read_text()
        except OSError:
            notes = ""
        return {
            "title": self.title,
            "version": self._builds,
            "loops": [{"loop": loop, "missed": len(rows)} for loop, rows in sorted(missed.items())],
            "release_notes": [heading for heading, _ in
                              release_digest.unposted(notes, release_digest.posted())],
            "lessons": skill_index.stats(skill_index.load()[0], skill_index.fires()),
        }


DEFAULT_PAGE = b"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>Board</title>
<style>
body{font:14px/1.5 system-ui,sans-serif;margin:24px;max-width:960px;background:#0f141b;color:#d7dde6}
h1{font-size:20px}h2{font-size:15px;margin-top:28px;border-bottom:1px solid #2a3442;padding-bottom:4px}
li{margin:2px 0}.muted{color:#7d8a9c}.bad{color:#e07a5f}
</style></head><body>
<h1 id="title">Board</h1>
<h2>Loop health</h2><ul id="loops"><li class="muted">loading</li></ul>
<h2>Release notes</h2><ul id="notes"><li class="muted">loading</li></ul>
<h2>Lessons (matches in 30 days)</h2><ul id="lessons"><li class="muted">loading</li></ul>
<script>
function fill(id, items, render){
  var ul = document.getElementById(id); ul.textContent = "";
  if (!items.length){ var li = document.createElement("li"); li.className = "muted";
    li.textContent = "none"; ul.appendChild(li); return; }
  items.forEach(function(x){ var li = document.createElement("li"); render(li, x); ul.appendChild(li); });
}
function show(s){
  document.title = s.title; document.getElementById("title").textContent = s.title;
  fill("loops", s.loops, function(li, l){
    li.textContent = l.loop + ": " + l.missed + " missed in 24 h";
    if (l.missed) li.className = "bad"; });
  fill("notes", s.release_notes, function(li, n){ li.textContent = n; });
  fill("lessons", s.lessons || [], function(li, k){
    li.textContent = k.skill + ": " + k.matches + (k.flags.length ? "  " + k.flags.join(" ") : "");
    if (k.flags.indexOf("PROMOTE?") >= 0) li.className = "bad"; });
}
fetch("/api/state").then(function(r){ return r.json(); }).then(show);
var es = new EventSource("/events");
es.addEventListener("state", function(e){ show(JSON.parse(e.data)); });
</script></body></html>
"""


def main(argv=None) -> int:
    cfg = bobcfg.config()
    panels = bobcfg.get(cfg, "board.panels")
    if len(panels) > 1:
        print(f"board.panels names {len(panels)} modules; this shell serves one panel module",
              file=sys.stderr)
        return 2
    if panels:
        return importlib.import_module(panels[0]).main(argv)

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--port", type=int, default=bobcfg.get(cfg, "board.port"))
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--interval", type=float, default=1.0,
                        help="seconds between file stat sweeps (default: 1.0)")
    parser.add_argument("--heartbeat", type=float, default=15.0,
                        help="seconds between SSE heartbeats (default: 15.0)")
    parser.add_argument("--quiet", action="store_true", help="suppress per-request logging")
    args = parser.parse_args(argv)

    model = DefaultModel(cfg)
    model.snapshot = model.rebuild()
    hub = Hub()
    Watcher(model, hub, args.interval).start()

    BaseHandler.model = model
    BaseHandler.hub = hub
    BaseHandler.heartbeat = args.heartbeat
    BaseHandler.quiet = args.quiet
    BaseHandler.page = DEFAULT_PAGE

    server = ThreadingHTTPServer((args.host, args.port), BaseHandler)
    server.daemon_threads = True
    print(f"{model.title} board :: http://{args.host}:{args.port}/", file=sys.stderr, flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
