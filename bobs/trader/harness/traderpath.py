"""Put this Bob's harness and core's harness on sys.path, and pin bobcfg to this Bob's manifest.

Core's bobcfg looks for bob.toml above core's own folder, then above the working directory. This
Bob lives inside core (bobs/trader), so a tool started from the repo root would find no manifest,
and one started from the Scout's folder would find the Scout's: a Trader reading the Scout's
manifest would run with no [trader] section at all. Importing this module first pins BOB_MANIFEST
to this Bob's own, unless it is already set (tests set it to a temp copy).

Usage (first lines of every module in harness/):
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
    import traderpath  # noqa: E402,F401
"""
from __future__ import annotations

import os
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
BOB = HERE.parent
CORE = BOB.parents[1]

for _path in (str(CORE / "harness"), str(HERE)):
    if _path not in sys.path:
        sys.path.insert(0, _path)
os.environ.setdefault("BOB_MANIFEST", str(BOB / "bob.toml"))
