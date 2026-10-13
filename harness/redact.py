#!/usr/bin/env python3
"""Replace credential material in text with named markers, before anything is saved as evidence.

A Bob that saves raw responses, log lines or API output as evidence can save a token with them.
This removes credential-shaped values and leaves a marker that names what was removed, for example
`<redacted:bearer>`. A value that disappears without a marker would let a reader believe the source
never had it.

It covers credentials only: tokens, keys, cookies, authorization headers. It does not detect names,
addresses, account ids or other personal data; a Bob that handles those needs its own rules.

Usage:
    python3 harness/redact.py < raw.txt > safe.txt
    python3 harness/redact.py raw.txt
    python3 harness/redact.py --selftest

From Python:
    import redact
    safe = redact.redact_text(raw)
    safe, counts = redact.redact_counted(raw)       # counts per rule, for the evidence record
    headers = redact.redact_headers({"Authorization": "Bearer ..."})
"""
from __future__ import annotations

import pathlib
import re
import sys

#: Header names whose VALUES are never kept.
SENSITIVE_HEADERS = {
    "cookie", "set-cookie", "authorization", "proxy-authorization", "x-api-key", "api-key",
    "x-auth-token", "x-csrf-token", "x-xsrf-token",
}

#: (pattern, label). Ordered so a JWT is matched before its base64-looking segments are caught by
#: the generic opaque-token rule, which runs last.
BODY_PATTERNS = [
    (re.compile(r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"), "jwt"),
    (re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{16,}", re.I), "bearer"),
    (re.compile(r"\bBasic\s+[A-Za-z0-9+/=]{16,}"), "basic-auth"),
    (re.compile(r"\bsk-(?:ant-)?[A-Za-z0-9_-]{16,}"), "api-key"),
    (re.compile(r"\bxox[abposr]-[A-Za-z0-9-]{8,}"), "slack-token"),
    (re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})"), "github-token"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "aws-access-key"),
    (re.compile(r"(?i)\b(?:password|passwd|secret|api_key|apikey|token)=[^&\s\"';]+"), "secret-param"),
    (re.compile(r"\b[A-Za-z0-9_-]{60,}\b"), "opaque-token"),
]


def redact_counted(s: str, keep: set[str] | frozenset[str] = frozenset()) -> tuple[str, dict[str, int]]:
    """(redacted text, {label: count}). Values in `keep` are never redacted: a Bob's own synthetic
    test markers, for example, which the generic opaque-token rule would otherwise swallow."""
    protected: dict[str, str] = {}
    for i, value in enumerate(sorted(keep, key=len, reverse=True)):
        if value and value in s:
            key = f"\x00KEEP{i}\x00"
            protected[key] = value
            s = s.replace(value, key)
    counts: dict[str, int] = {}
    for pattern, label in BODY_PATTERNS:
        s, hits = pattern.subn(f"<redacted:{label}>", s)
        if hits:
            counts[label] = counts.get(label, 0) + hits
    for key, value in protected.items():
        s = s.replace(key, value)
    return s, counts


def redact_text(s: str, keep: set[str] | frozenset[str] = frozenset()) -> str:
    """Replace credential material with named markers. Never drops silently."""
    return redact_counted(s, keep)[0]


def redact_headers(headers: dict) -> dict:
    """Headers with sensitive values replaced by a marker and every other value redacted as text."""
    out = {}
    for k, v in (headers or {}).items():
        out[k] = f"<redacted:{k.lower()}>" if k.lower() in SENSITIVE_HEADERS else redact_text(str(v))
    return out


#: (label, input, a substring the output must contain, a substring it must not).
CASES = [
    ("jwt", "t=eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U",
     "<redacted:jwt>", "eyJhbGci"),
    ("bearer", "Authorization: Bearer abcdefghijklmnop1234", "<redacted:bearer>", "abcdefghijklmnop1234"),
    ("api key", "key sk-ant-abcdefghijklmnopqrstu", "<redacted:api-key>", "sk-ant-abc"),
    ("slack token", "xoxb-1234567890-abcdefgh", "<redacted:slack-token>", "xoxb-123"),
    ("github token", "ghp_abcdefghijklmnopqrstuvwxyz0123", "<redacted:github-token>", "ghp_abc"),
    ("aws key", "AKIAABCDEFGHIJKLMNOP", "<redacted:aws-access-key>", "AKIAABCD"),
    ("query secret", "GET /x?user=a&password=hunter2&y=1", "<redacted:secret-param>", "hunter2"),
    ("plain text untouched", "The meeting is at 10:00 in room 4.", "room 4", "redacted"),
    ("short ids untouched", "order 12345 shipped", "order 12345", "redacted"),
]


def selftest() -> int:
    bad = 0
    for label, text, must, must_not in CASES:
        out = redact_text(text)
        ok = must in out and must_not not in out
        print(f"  {'ok  ' if ok else 'FAIL'}  {label}" + ("" if ok else f": {out!r}"))
        bad += not ok
    kept = "MARKER_" + "x" * 70
    ok = redact_text(f"value {kept}", keep={kept}) == f"value {kept}"
    print(f"  {'ok  ' if ok else 'FAIL'}  a kept value survives the opaque-token rule")
    bad += not ok
    ok = redact_headers({"Cookie": "a=b", "Accept": "text/html"}) == {
        "Cookie": "<redacted:cookie>", "Accept": "text/html"}
    print(f"  {'ok  ' if ok else 'FAIL'}  sensitive headers are replaced, others kept")
    bad += not ok
    print("SELFTEST OK" if not bad else f"SELFTEST FAILED: {bad} check(s)")
    return 1 if bad else 0


def main(argv: list[str]) -> int:
    if "--selftest" in argv:
        return selftest()
    paths = [a for a in argv if not a.startswith("-")]
    text = pathlib.Path(paths[0]).read_text(errors="replace") if paths else sys.stdin.read()
    out, counts = redact_counted(text)
    sys.stdout.write(out)
    if counts:
        print("redacted: " + ", ".join(f"{k} {v}" for k, v in sorted(counts.items())), file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
