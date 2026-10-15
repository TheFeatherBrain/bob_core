#!/usr/bin/env python3
"""HTTP GET for this Bob's readers, and the one place a read becomes evidence.

Why one module: SEC answers 403 when it blocks an IP, and retrying a 403 is how a short block
becomes a long one, so 403 is never retried; timeouts and 5xx are, a few times. Every reader must
also save what it read before using it, including an empty 404 ("not found" is evidence only with
its query and time on file). Keeping both here means no reader can forget either.

Usage (from a reader):
    raw, where = fetch.read("sec-frame", url, headers, limiter=limiter)

Copied from bobs/investor/harness; the Trader never imports from another Bob.
"""
from __future__ import annotations

import datetime as dt
import gzip
import hashlib
import pathlib
import re
import socket
import sys
import time
import urllib.error
import urllib.request

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import traderpath  # noqa: E402,F401
import evidence  # noqa: E402

RETRY_STATUS = {429, 500, 502, 503, 504}
ID_CHARS = re.compile(r"[^a-z0-9._-]+")


class FetchError(Exception):
    def __init__(self, url: str, status: int | None, why: str):
        super().__init__(f"{why}: {url}")
        self.url = url
        self.status = status
        self.evidence: pathlib.Path | None = None


class Forbidden(FetchError):
    pass


class NotFound(FetchError):
    pass


class Limiter:
    """At most `per_second` requests per second, evenly spaced. SEC allows 10/s per IP; a steady
    8/s stays under it with room for clock jitter."""

    def __init__(self, per_second: float, clock=time.monotonic, sleep=time.sleep):
        self.gap = 1.0 / per_second
        self.clock = clock
        self.sleep = sleep
        self.next_at: float | None = None

    def wait(self) -> None:
        now = self.clock()
        if self.next_at is not None and now < self.next_at:
            self.sleep(self.next_at - now)
            now = self.next_at
        self.next_at = now + self.gap


def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _missing_object(e: urllib.error.HTTPError) -> bool:
    """True for the 403 SEC's Archives give a file that does not exist (a weekend or holiday daily
    index, a wrong path): an S3 `<Code>AccessDenied</Code>` XML body, gzip-compressed even when
    identity was asked for. Treating it as a block stopped every sync at the first weekend. A block
    comes from SEC's edge as an HTML page, so any other 403, or one whose body cannot be read, is
    still Forbidden."""
    if "xml" not in (e.headers.get("Content-Type") or ""):
        return False
    try:
        body = e.read()
        if e.headers.get("Content-Encoding") == "gzip":
            body = gzip.decompress(body)
    except (OSError, EOFError):
        return False
    return b"<Code>AccessDenied</Code>" in body


def get(url: str, headers: dict, *, retries: int = 3, backoff: float = 1.0, timeout: float = 60,
        opener=urllib.request.urlopen, sleep=time.sleep) -> str:
    attempt = 0
    while True:
        try:
            with opener(urllib.request.Request(url, headers=headers), timeout=timeout) as resp:
                return resp.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as e:
            if e.code == 403 and _missing_object(e):
                raise NotFound(url, 403, "no such file (403 AccessDenied from storage)") from None
            if e.code == 403:
                raise Forbidden(url, 403, "403 forbidden (check User-Agent / rate); not retried") from None
            if e.code == 404:
                raise NotFound(url, 404, "404 not found") from None
            if e.code not in RETRY_STATUS or attempt >= retries:
                raise FetchError(url, e.code, f"HTTP {e.code}") from None
        except (urllib.error.URLError, socket.timeout, TimeoutError, ConnectionError) as e:
            if attempt >= retries:
                raise FetchError(url, None, f"{type(e).__name__}: {e}") from None
        attempt += 1
        sleep(backoff * 2 ** (attempt - 1))


def evidence_id(prefix: str, url: str, now: dt.datetime) -> str:
    """Readable prefix, time and a hash of the URL: unique per read, at most 64 characters."""
    stem = ID_CHARS.sub("-", prefix.lower()).strip("-.")[:40]
    return f"{stem}-{now:%H%M%S}-{hashlib.sha1(url.encode()).hexdigest()[:8]}"


def save_read(prefix: str, url: str, raw: str, started: dt.datetime, extra: dict | None = None,
              base: pathlib.Path | None = None, now: dt.datetime | None = None) -> pathlib.Path:
    """Save one read. A re-run in the same second gets a -2, -3 suffix rather than an error: a
    loop resumed after a crash must not die on its own earlier evidence."""
    now = now or utcnow()
    ask = evidence_id(prefix, url, now)
    meta = {"source": prefix.split("-")[0], "url": url, **(extra or {})}
    for n in range(1, 100):
        try:
            return evidence.save(ask if n == 1 else f"{ask}-{n}", raw, query=f"GET {url}",
                                 window=(started.isoformat(), now.isoformat()), extra=meta,
                                 base=base, now=now)
        except FileExistsError:
            continue
    raise FileExistsError(f"{ask}: 99 reads of the same URL in one second")


def read(prefix: str, url: str, headers: dict, *, limiter: Limiter | None = None, getter=get,
         base: pathlib.Path | None = None) -> tuple[str, pathlib.Path]:
    if limiter is not None:
        limiter.wait()
    started = utcnow()
    try:
        raw = getter(url, headers)
    except NotFound as e:
        e.evidence = save_read(prefix, url, "", started, {"status": e.status}, base)
        raise
    return raw, save_read(prefix, url, raw, started, None, base)
