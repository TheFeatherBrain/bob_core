#!/usr/bin/env python3
"""Send plain text to one person on Telegram, and nothing else.

Why plain text: with a parse mode, one stray `_` or `*` in a team name makes Telegram refuse the
whole message, and a tip that never arrives looks the same as no tip. Why the token never appears
in an error: the bot token sits in the request URL, and urllib's errors quote the URL, so an error
message printed into a transcript or saved as evidence would leak it. Errors are rebuilt from the
HTTP status and Telegram's own description instead. Why a sent log with a hash, not the text: a
Bob must be able to show that a message went out and which one, without a second copy of what may
be private.

The manifest names the environment variables, never their values:

    [telegram]
    token_env = "TELEGRAM_BOT_TOKEN"
    chat_id_env = "TELEGRAM_CHAT_ID"

Usage:
    python3 harness/telegram.py send --file PATH   # OK message_id=N, or FAIL: ...
    python3 harness/telegram.py --selftest         # prove lint and redaction still refuse
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import pathlib
import re
import sys
import time
import urllib.error
import urllib.request

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import bobcfg  # noqa: E402
import oscompat  # noqa: E402

MAX_LEN = 4096
TOKEN_SHAPE = re.compile(r"\d{8,10}:[A-Za-z0-9_-]{35}")
RETRY_STATUS = {429, 500, 502, 503, 504}


class TelegramError(Exception):
    pass


def lint(text: str) -> list[str]:
    errors = []
    if not text.strip():
        errors.append("empty: Telegram refuses an empty message")
    if len(text) > MAX_LEN:
        errors.append(f"too long: {len(text)} characters, Telegram's limit is {MAX_LEN}")
    if TOKEN_SHAPE.search(text):
        errors.append("holds a bot token shape: never send a credential")
    return errors


def error_from_body(status: int, body: str) -> TelegramError:
    try:
        description = json.loads(body).get("description")
    except (ValueError, AttributeError):
        description = None
    return TelegramError(f"HTTP {status}: {description}" if description else f"HTTP {status}")


def _retry_after(body: str) -> float:
    try:
        return float(json.loads(body)["parameters"]["retry_after"])
    except (ValueError, KeyError, TypeError):
        return 1.0


def _post(token: str, payload: dict, *, opener=urllib.request.urlopen, sleep=time.sleep) -> int:
    request = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendMessage", data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST")
    for attempt in (1, 2):
        try:
            with opener(request, timeout=30) as resp:
                return int(json.loads(resp.read().decode("utf-8"))["result"]["message_id"])
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace") if e.fp else ""
            if e.code not in RETRY_STATUS or attempt == 2:
                raise error_from_body(e.code, body) from None
            sleep(_retry_after(body))
        except (urllib.error.URLError, OSError) as e:
            raise TelegramError(f"network: {type(e).__name__}") from None
        except (ValueError, KeyError, TypeError):
            raise TelegramError("unreadable reply from Telegram") from None
    raise TelegramError("not sent")


def _names() -> tuple[str, str]:
    section = bobcfg.config().get("telegram")
    if not section:
        raise TelegramError("no [telegram] section in the manifest")
    return section["token_env"], section["chat_id_env"]


def sent_log() -> pathlib.Path:
    return bobcfg.state_home() / "telegram-sent.jsonl"


def _log(message_id: int, text: str) -> None:
    path = sent_log()
    path.parent.mkdir(parents=True, exist_ok=True)
    row = {"at": dt.datetime.now(dt.timezone.utc).isoformat(), "message_id": message_id,
           "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest()}
    with path.open("a", encoding="utf-8") as fh:
        oscompat.lock(fh)
        try:
            fh.write(json.dumps(row, sort_keys=True) + "\n")
        finally:
            oscompat.unlock(fh)


def send(text: str, *, environ=os.environ, poster=_post) -> int:
    problems = lint(text)
    if problems:
        raise TelegramError("; ".join(problems))
    token_env, chat_env = _names()
    token, chat_id = environ.get(token_env), environ.get(chat_env)
    if not token:
        raise TelegramError(f"{token_env} not set")
    if not chat_id:
        raise TelegramError(f"{chat_env} not set")
    payload = {"chat_id": chat_id, "text": text, "disable_web_page_preview": True}
    try:
        message_id = poster(token, payload)
    except TelegramError as e:
        raise TelegramError(str(e).replace(token, "<token>")) from None
    except Exception as e:  # noqa: BLE001 - any failure must lose the token before it is shown
        raise TelegramError(f"{type(e).__name__}: {e}".replace(token, "<token>")) from None
    _log(message_id, text)
    return message_id


def selftest() -> int:
    token = "123456789:" + "A" * 35

    def leaky(t, p):
        raise OSError(f"https://api.telegram.org/bot{t}/sendMessage")
    try:
        token_env, chat_env = _names()
        send("hello", environ={token_env: token, chat_env: "1"}, poster=leaky)
        leaked = "not raised"
    except TelegramError as e:
        leaked = str(e)
    checks = {
        "empty refused": bool(lint("")),
        "over limit refused": bool(lint("a" * (MAX_LEN + 1))),
        "token refused": bool(lint(f"x {token}")),
        "plain allowed": lint("TIP t-1") == [],
        "error has no token": token not in leaked and "<token>" in leaked,
    }
    failed = [name for name, ok in checks.items() if not ok]
    for name in failed:
        print(f"FAIL: {name}")
    if failed:
        return 1
    print("OK")
    return 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Send plain text to the person on Telegram.")
    parser.add_argument("--selftest", action="store_true")
    sub = parser.add_subparsers(dest="cmd")
    send_cmd = sub.add_parser("send")
    send_cmd.add_argument("--file", required=True)
    args = parser.parse_args(argv)
    if args.selftest:
        return selftest()
    if args.cmd != "send":
        parser.print_usage()
        return 2
    try:
        text = pathlib.Path(args.file).read_text(encoding="utf-8")
        message_id = send(text)
    except (OSError, TelegramError, bobcfg.ManifestError) as e:
        print(f"FAIL: {e}")
        return 1
    print(f"OK message_id={message_id}")
    return 0


if __name__ == "__main__":
    oscompat.utf8_stdio()
    raise SystemExit(main(sys.argv[1:]))
