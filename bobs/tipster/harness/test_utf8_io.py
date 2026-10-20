#!/usr/bin/env python3
"""The Bob holds itself to core's encoding rule: every text read, write and pipe says UTF-8.

Run:  python3 harness/test_utf8_io.py
"""
from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import tipsterpath  # noqa: E402,F401
import test_text_encoding  # noqa: E402
import testkit  # noqa: E402


def test_bob_has_no_bare_text_io():
    harness = tipsterpath.HERE
    files = test_text_encoding.modules(harness, harness / "hooks")
    found = test_text_encoding.findings(files, tipsterpath.BOB)
    assert found == [], "\n".join(found)


if __name__ == "__main__":
    raise SystemExit(testkit.run(globals()))
