#!/usr/bin/env python3
"""fetch: 403 is never retried, transient failures are, and every read lands as unique evidence.

Run:  python3 harness/test_fetch.py
"""
from __future__ import annotations

import datetime as dt
import pathlib
import socket
import sys
import tempfile
import urllib.error

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import traderpath  # noqa: E402,F401
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
