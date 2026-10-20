#!/usr/bin/env python3
"""telegram: plain text only, never a token in text or errors, every send logged by hash.

Run:  BOB_MANIFEST=$PWD/bob.example.toml python3 harness/test_telegram.py
"""
from __future__ import annotations

import hashlib
import json
import os
import pathlib
import subprocess
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import bobcfg  # noqa: E402
import telegram  # noqa: E402

TOKEN = "123456789:AAH" + "x" * 32
ENV = {"TELEGRAM_BOT_TOKEN": TOKEN, "TELEGRAM_CHAT_ID": "4242"}


def _config_dir() -> pathlib.Path:
    path = pathlib.Path(tempfile.mkdtemp()).resolve()
    os.environ["CLAUDE_CONFIG_DIR"] = str(path)
    bobcfg.config.cache_clear()
    return path


def test_lint_refuses_empty():
    assert telegram.lint("") and telegram.lint("   \n")


def test_lint_refuses_over_4096():
    assert telegram.lint("a" * (telegram.MAX_LEN + 1))
    assert telegram.lint("a" * telegram.MAX_LEN) == []


def test_lint_refuses_token_shaped_text():
    assert any("token" in e for e in telegram.lint(f"key is {TOKEN} ok"))


def test_lint_allows_plain_text():
    assert telegram.lint("TIP t-1 (round 8)\nOlympiacos vs Real Madrid\nStake EUR 34") == []


def test_send_posts_plain_text_without_parse_mode():
    _config_dir()
    seen = []

    def poster(token, payload):
        seen.append((token, payload))
        return 77
    assert telegram.send("hello", environ=ENV, poster=poster) == 77
    token, payload = seen[0]
    assert token == TOKEN and payload["chat_id"] == "4242" and payload["text"] == "hello"
    assert "parse_mode" not in payload and payload["disable_web_page_preview"] is True


def test_send_refuses_text_that_fails_lint():
    _config_dir()
    try:
        telegram.send("", environ=ENV, poster=lambda t, p: 1)
    except telegram.TelegramError as e:
        assert "empty" in str(e)
    else:
        raise AssertionError("not refused")


def test_error_never_contains_token():
    _config_dir()

    def poster(token, payload):
        raise OSError(f"failed https://api.telegram.org/bot{token}/sendMessage")
    try:
        telegram.send("hello", environ=ENV, poster=poster)
    except telegram.TelegramError as e:
        assert TOKEN not in str(e) and "AAH" not in str(e), str(e)
    else:
        raise AssertionError("not raised")


def test_http_error_is_rebuilt_from_status_and_description():
    err = telegram.error_from_body(400, '{"ok": false, "description": "Bad Request: chat not found"}')
    assert str(err) == "HTTP 400: Bad Request: chat not found"
    assert str(telegram.error_from_body(502, "<html>")) == "HTTP 502"


def test_post_retries_once_on_429_honouring_retry_after():
    import io
    import urllib.error
    calls, sleeps = [], []

    class Resp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def opener(request, timeout):
        calls.append(request.full_url)
        if len(calls) == 1:
            body = b'{"ok": false, "description": "Too Many Requests", "parameters": {"retry_after": 3}}'
            raise urllib.error.HTTPError(request.full_url, 429, "x", {}, io.BytesIO(body))
        return Resp(b'{"ok": true, "result": {"message_id": 5}}')
    assert telegram._post(TOKEN, {"text": "x"}, opener=opener, sleep=sleeps.append) == 5
    assert len(calls) == 2 and sleeps == [3.0]
    calls.clear()

    def always_502(request, timeout):
        calls.append(1)
        raise urllib.error.HTTPError(request.full_url, 502, "x", {}, io.BytesIO(b""))
    try:
        telegram._post(TOKEN, {"text": "x"}, opener=always_502, sleep=sleeps.append)
    except telegram.TelegramError as e:
        assert str(e) == "HTTP 502" and len(calls) == 2
    else:
        raise AssertionError("not raised")


def test_missing_env_is_fail_not_crash():
    env = {k: v for k, v in os.environ.items() if not k.startswith("TELEGRAM_")}
    env["BOB_MANIFEST"] = str(HERE.parent / "bob.example.toml")
    env["CLAUDE_CONFIG_DIR"] = tempfile.mkdtemp()
    text = pathlib.Path(tempfile.mkdtemp()) / "m.txt"
    text.write_text("hello", encoding="utf-8")
    out = subprocess.run([sys.executable, str(HERE / "telegram.py"), "send", "--file", str(text)],
                         env=env, capture_output=True, text=True, encoding="utf-8")
    assert out.returncode == 1 and out.stdout.strip() == "FAIL: TELEGRAM_BOT_TOKEN not set", (out.stdout, out.stderr)


def test_send_is_logged_with_hash():
    config = _config_dir()
    telegram.send("hello", environ=ENV, poster=lambda t, p: 9)
    rows = [json.loads(line) for line in (config / "telegram-sent.jsonl").read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 1 and rows[0]["message_id"] == 9 and rows[0]["at"].endswith("+00:00")
    assert rows[0]["sha256"] == hashlib.sha256(b"hello").hexdigest() and "text" not in rows[0]


def main() -> int:
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"PASS {name}")
        except Exception as e:  # noqa: BLE001 - a crash is a failure, reported by name
            failed += 1
            print(f"FAIL {name}: {type(e).__name__}: {e}")
    print(f"{len(tests) - failed} of {len(tests)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
