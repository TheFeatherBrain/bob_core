#!/usr/bin/env python3
"""fetch: 403 is never retried, transient failures are, and every read lands as unique evidence.

Run:  python3 harness/test_fetch.py
"""
from __future__ import annotations

import datetime as dt
import gzip
import io
import pathlib
import socket
import sys
import tempfile
import urllib.error

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import scoutpath  # noqa: E402,F401
import evidence  # noqa: E402
import fetch  # noqa: E402
import testkit  # noqa: E402

NOW = dt.datetime(2026, 10, 13, 6, 30, tzinfo=dt.timezone.utc)


class _Resp:
    def __init__(self, body: str):
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self) -> bytes:
        return self.body.encode()


def _opener(outcomes: list):
    calls = []

    def opener(request, timeout):
        calls.append(request.full_url)
        item = outcomes.pop(0)
        if isinstance(item, int):
            raise urllib.error.HTTPError(request.full_url, item, "status", {}, None)
        if isinstance(item, tuple):
            code, headers, body = item
            raise urllib.error.HTTPError(request.full_url, code, "status", headers, io.BytesIO(body))
        if isinstance(item, BaseException):
            raise item
        return _Resp(item)
    return opener, calls


def _get(outcomes: list):
    opener, calls = _opener(outcomes)
    sleeps: list[float] = []
    try:
        return fetch.get("https://x.test/a", {}, opener=opener, sleep=sleeps.append), calls, sleeps
    except fetch.FetchError as e:
        return e, calls, sleeps


def test_403_is_not_retried():
    result, calls, _ = _get([403, "ok"])
    assert isinstance(result, fetch.Forbidden) and len(calls) == 1


#: What SEC's Archives answer for a file that does not exist (a weekend daily index): a 403 from
#: the storage behind it, gzip-compressed even when identity was asked for.
MISSING = (b'<?xml version="1.0" encoding="UTF-8"?>\n<Error><Code>AccessDenied</Code>'
           b"<Message>Access Denied</Message><RequestId>YB82M3N6MFZX6TJ0</RequestId></Error>")
#: What SEC's edge answers when it refuses the request itself: the same status, an HTML page.
EDGE = b"<HTML><HEAD>\n<TITLE>Access Denied</TITLE>\n</HEAD><BODY>\n<H1>Access Denied</H1>"


def test_storage_403_for_a_missing_file_is_not_found_and_not_retried():
    for headers, body in (({"Content-Type": "application/xml", "Content-Encoding": "gzip"}, gzip.compress(MISSING)),
                          ({"Content-Type": "application/xml"}, MISSING)):
        result, calls, _ = _get([(403, headers, body), "ok"])
        assert isinstance(result, fetch.NotFound) and result.status == 403 and len(calls) == 1, result


def test_edge_or_unreadable_403_still_stops_the_run():
    for headers, body in (({"Content-Type": "text/html"}, EDGE),
                          ({"Content-Type": "application/xml", "Content-Encoding": "gzip"}, b"not gzip"),
                          ({"Content-Type": "text/html"}, MISSING)):
        result, calls, _ = _get([(403, headers, body), "ok"])
        assert isinstance(result, fetch.Forbidden) and len(calls) == 1, (headers, result)


def test_5xx_is_retried_with_backoff():
    result, calls, sleeps = _get([503, 502, "ok"])
    assert result == "ok" and len(calls) == 3 and sleeps == [1.0, 2.0]


def test_gives_up_after_three_retries():
    result, calls, _ = _get([500, 500, 500, 500, "never"])
    assert isinstance(result, fetch.FetchError) and result.status == 500 and len(calls) == 4


def test_timeout_is_retried():
    result, calls, _ = _get([socket.timeout("slow"), "ok"])
    assert result == "ok" and len(calls) == 2


def test_404_is_not_found_and_not_retried():
    result, calls, _ = _get([404, "ok"])
    assert isinstance(result, fetch.NotFound) and len(calls) == 1


def test_other_4xx_fails_without_retry():
    result, calls, _ = _get([400, "ok"])
    assert isinstance(result, fetch.FetchError) and result.status == 400 and len(calls) == 1


def test_limiter_spaces_requests_evenly():
    now = [0.0]
    sleeps: list[float] = []

    def sleep(s):
        sleeps.append(round(s, 6))
        now[0] += s
    limiter = fetch.Limiter(8, clock=lambda: now[0], sleep=sleep)
    for _ in range(3):
        limiter.wait()
    assert sleeps == [0.125, 0.125]


def test_evidence_id_is_valid_for_core():
    ask = fetch.evidence_id("sec-frame-us-gaap.RevenueFromContractWithCustomerExcludingAssessedTax", "https://u", NOW)
    assert evidence.ID.match(ask) and len(ask) <= 64, ask


def test_save_read_same_url_twice_same_day():
    base = pathlib.Path(tempfile.mkdtemp())
    first = fetch.save_read("sec-index", "https://u", "a", NOW, base=base, now=NOW)
    second = fetch.save_read("sec-index", "https://u", "b", NOW, base=base, now=NOW)
    assert first != second and (second / "raw.txt").read_text() == "b"


def test_read_saves_empty_evidence_on_404():
    testkit.temp_bob()

    def getter(url, headers):
        raise fetch.NotFound(url, 404, "404 not found")
    try:
        fetch.read("sec-index", "https://u/form.idx", {}, getter=getter)
    except fetch.NotFound as e:
        assert (e.evidence / "query.txt").read_text().startswith("GET https://u/form.idx")
        assert (e.evidence / "raw.txt").read_text() == ""
    else:
        raise AssertionError("NotFound was swallowed")


def test_read_returns_body_and_evidence():
    testkit.temp_bob()
    raw, where = fetch.read("alpaca-bars", "https://d/bars", {}, getter=lambda u, h: '{"bars":{}}')
    assert raw == '{"bars":{}}' and (where / "meta.json").is_file()


if __name__ == "__main__":
    raise SystemExit(testkit.run(globals()))
