#!/usr/bin/env python3
"""The Bob holds itself to core's encoding rule: every text read, write and pipe says UTF-8.

Theses, filings and company names carry characters outside Windows' default code page (cp1252);
a bare read_text() or print crashes on them. Core's checker, run over this Bob's own harness.

Run:  python3 harness/test_utf8_io.py
"""
from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import scoutpath  # noqa: E402,F401
import test_text_encoding  # noqa: E402
import testkit  # noqa: E402


def test_bob_has_no_bare_text_io():
    harness = scoutpath.HERE
    files = test_text_encoding.modules(harness, harness / "hooks", harness / "fundamentals")
    found = test_text_encoding.findings(files, scoutpath.BOB)
    assert found == [], "\n".join(found)


if __name__ == "__main__":
    raise SystemExit(testkit.run(globals()))
