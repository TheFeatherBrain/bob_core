#!/usr/bin/env python3
"""Stop new tips: create state/HALT with a reason. Watching, updates and settling continue.

Why there is no way to remove it here: HALT exists for the moments a person must look before tips
resume (the reviewed wallet below its floor, a fault). Only the person deletes the file.

Usage:
    python3 harness/halt.py --reason TEXT    # OK halted: TEXT (or OK already halted: <reason>)
"""
from __future__ import annotations

import argparse
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import tipsterpath  # noqa: E402,F401
import tips  # noqa: E402


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Create HALT. Only a person removes it.")
    parser.add_argument("--reason", required=True)
    args = parser.parse_args(argv)
    if not args.reason.strip():
        print("FAIL: a reason is required")
        return 1
    if tips.halt(args.reason.strip()):
        print(f"OK halted: {args.reason.strip()}")
    else:
        print(f"OK already halted: {tips.halted()}")
    return 0


if __name__ == "__main__":
    import oscompat
    oscompat.utf8_stdio()
    raise SystemExit(main(sys.argv[1:]))
