#!/usr/bin/env python3
"""Save what a Bob read, the way CORE.md says evidence is kept, and return the path to cite.

One call per read. It writes `<evidence_dir>/<UTC date>/<id>/`:

    raw.txt      what came back, with credentials replaced by redact.py's named markers
    query.txt    the request, query or command that produced it (when given)
    meta.json    id, saved_at (UTC), the time window the read covered, byte count, the sha256 of
                 raw.txt, redaction counts per rule, and any extra fields the caller passes

An answer cites the directory. A saved read is never overwritten: the same id on the same day is
refused, so a citation always points at what was there when it was made. "Not found" is evidence
only with a query and a window on file, which is why an empty read still writes all three.

From a Bob's own tool:
    sys.path.insert(0, "<path to bob_core>/harness"); import evidence
    where = evidence.save("nvda-quote", raw_text, query="GET /quote?symbol=NVDA",
                          window=("2026-10-13T09:30:00-04:00", "2026-10-13T16:00:00-04:00"),
                          extra={"source": "exchange API", "capped": False})

From the shell:
    python3 harness/evidence.py save --id nvda-quote --query "GET /quote?symbol=NVDA" < raw.txt
    python3 harness/evidence.py list [--date 2026-10-13]
    python3 harness/evidence.py --selftest
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import pathlib
import re
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import redact  # noqa: E402

ID = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")


def root() -> pathlib.Path:
    """The Bob's evidence directory, `[paths] evidence_dir` under data_root."""
    import bobcfg
    return bobcfg.path(bobcfg.config(), "evidence_dir")


def _utc(text: str) -> str:
    """An ISO instant in UTC. A naive time is refused: a window whose zone is a guess is not one."""
    when = dt.datetime.fromisoformat(str(text).replace("Z", "+00:00"))
    if when.tzinfo is None:
        raise ValueError(f"{text!r} has no timezone; give an offset or Z")
    return when.astimezone(dt.timezone.utc).isoformat(timespec="seconds")


def save(ask_id: str, raw: str, query: str | None = None,
         window: tuple[str, str] | None = None, extra: dict | None = None,
         keep: set[str] | frozenset[str] = frozenset(),
         base: pathlib.Path | None = None, now: dt.datetime | None = None) -> pathlib.Path:
    """Write one read as evidence and return its directory. Raises on a bad id, a naive window,
    or a directory that already exists."""
    if not ID.match(ask_id or ""):
        raise ValueError(f"id {ask_id!r}: lowercase letters, digits, . _ -, up to 64, e.g. nvda-quote")
    now = (now or dt.datetime.now(dt.timezone.utc)).astimezone(dt.timezone.utc)
    where = (base or root()) / now.strftime("%Y-%m-%d") / ask_id
    if where.exists():
        raise FileExistsError(f"{where} exists; evidence is never overwritten, use a new id")
    span = None
    if window is not None:
        start, end = _utc(window[0]), _utc(window[1])
        if start > end:
            raise ValueError(f"window starts after it ends: {start} > {end}")
        span = {"start": start, "end": end}
    safe, counts = redact.redact_counted(raw, keep)
    where.mkdir(parents=True)
    (where / "raw.txt").write_text(safe, encoding="utf-8")
    if query is not None:
        (where / "query.txt").write_text(redact.redact_text(query, keep) + "\n", encoding="utf-8")
    meta = {
        "id": ask_id,
        "saved_at": now.isoformat(timespec="seconds"),
        "window": span,
        "bytes": len(safe.encode()),
        "sha256": hashlib.sha256(safe.encode()).hexdigest(),
        "redacted": counts,
        **(extra or {}),
    }
    (where / "meta.json").write_text(json.dumps(meta, indent=2, default=str) + "\n", encoding="utf-8")
    return where


def entries(base: pathlib.Path | None = None, date: str | None = None) -> list[dict]:
    """Every saved read's meta.json, oldest first, optionally for one UTC date."""
    base = base or root()
    pattern = f"{date}/*/meta.json" if date else "*/*/meta.json"
    out = []
    for meta in sorted(base.glob(pattern)):
        try:
            out.append({**json.loads(meta.read_text(encoding="utf-8")), "path": str(meta.parent)})
        except (OSError, ValueError):
            out.append({"path": str(meta.parent), "error": "unreadable meta.json"})
    return out


def selftest() -> int:
    import tempfile
    bad = 0

    def check(label: str, ok: bool) -> None:
        nonlocal bad
        print(f"  {'ok  ' if ok else 'FAIL'}  {label}")
        bad += not ok

    base = pathlib.Path(tempfile.mkdtemp())
    when = dt.datetime(2026, 10, 13, 14, 0, tzinfo=dt.timezone.utc)
    where = save("quote-1", "price 101.5 token=abc123secret", query="GET /q?key=sk-ant-abcdefghijklmnopqrstu",
                 window=("2026-10-13T09:30:00-04:00", "2026-10-13T10:00:00-04:00"),
                 extra={"source": "test"}, base=base, now=when)
    meta = json.loads((where / "meta.json").read_text(encoding="utf-8"))
    check("written under <UTC date>/<id>", where == base / "2026-10-13" / "quote-1")
    check("credentials are redacted in the raw read", "abc123secret" not in (where / "raw.txt").read_text(encoding="utf-8"))
    check("and in the query", "sk-ant" not in (where / "query.txt").read_text(encoding="utf-8"))
    check("redaction is counted, not silent", meta["redacted"] == {"secret-param": 1})
    check("the window is stored in UTC", meta["window"] == {"start": "2026-10-13T13:30:00+00:00",
                                                           "end": "2026-10-13T14:00:00+00:00"})
    check("the hash is of what was saved",
          meta["sha256"] == hashlib.sha256((where / "raw.txt").read_bytes()).hexdigest())
    check("extra fields are kept", meta["source"] == "test")

    def refused(fn) -> bool:
        try:
            fn()
        except (ValueError, FileExistsError):
            return True
        return False

    check("the same id twice is refused", refused(lambda: save("quote-1", "x", base=base, now=when)))
    check("a naive window is refused",
          refused(lambda: save("quote-2", "x", window=("2026-10-13T09:30", "2026-10-13T10:00"), base=base, now=when)))
    check("a backwards window is refused",
          refused(lambda: save("quote-3", "x", window=("2026-10-13T11:00Z", "2026-10-13T10:00Z"), base=base, now=when)))
    check("a bad id is refused", refused(lambda: save("../escape", "x", base=base, now=when)))
    empty = save("empty-read", "", query="q", window=("2026-10-13T00:00Z", "2026-10-13T01:00Z"), base=base, now=when)
    check("an empty read still writes all three files",
          all((empty / f).is_file() for f in ("raw.txt", "query.txt", "meta.json")))
    check("list returns every read", [e["id"] for e in entries(base)] == ["empty-read", "quote-1"])
    try:
        text = save("non-ascii", "→ Łódź", base=base, now=when)
        kept = (text / "raw.txt").read_bytes() == "→ Łódź".encode("utf-8")
    except UnicodeError:
        kept = False
    check("non-ASCII text is saved as UTF-8 on every platform", kept)
    print("SELFTEST OK" if not bad else f"SELFTEST FAILED: {bad} check(s)")
    return 1 if bad else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--selftest", action="store_true")
    sub = ap.add_subparsers(dest="command")
    s = sub.add_parser("save", help="save stdin (or --from FILE) as one read")
    s.add_argument("--id", required=True)
    s.add_argument("--query")
    s.add_argument("--from", dest="source", help="read the raw content from this file, not stdin")
    s.add_argument("--window-start")
    s.add_argument("--window-end")
    s.add_argument("--meta", action="append", default=[], metavar="KEY=VALUE")
    ls = sub.add_parser("list", help="every saved read")
    ls.add_argument("--date", help="one UTC date, YYYY-MM-DD")
    args = ap.parse_args(argv)

    if args.selftest:
        return selftest()
    if args.command == "save":
        if bool(args.window_start) != bool(args.window_end):
            ap.error("give both --window-start and --window-end, or neither")
        raw = pathlib.Path(args.source).read_text(encoding="utf-8", errors="replace") if args.source else sys.stdin.read()
        extra = dict(item.split("=", 1) for item in args.meta if "=" in item)
        window = (args.window_start, args.window_end) if args.window_start else None
        try:
            where = save(args.id, raw, args.query, window, extra)
        except (ValueError, FileExistsError) as exc:
            print(f"REFUSED: {exc}")
            return 1
        print(where)
        return 0
    if args.command == "list":
        for e in entries(date=args.date):
            window = e.get("window") or {}
            print(f"{e.get('saved_at', '?'):26} {e.get('id', '?'):24} {e.get('bytes', '?'):>8} B  "
                  f"{window.get('start', '-')} .. {window.get('end', '-')}  {e['path']}")
        return 0
    ap.print_help()
    return 2


if __name__ == "__main__":
    import oscompat
    oscompat.utf8_stdio()
    raise SystemExit(main())
