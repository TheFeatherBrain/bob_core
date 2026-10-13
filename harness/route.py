#!/usr/bin/env python3
"""Decide which Bob answers a Slack message when several Bobs share one Slack account.

One Bob watches Slack: the front door. Before it answers a mention or a DM, it asks this module
whether the message belongs to another Bob. The rule is deterministic, so the same message always
routes the same way:

1. After the @-mention, a message that starts with a Bob's prefix (for example `tutor:`) goes to
   that Bob. Case does not matter.
2. Otherwise, if whole-word keywords from exactly one Bob appear, it goes to that Bob.
3. Anything else, including keywords from two Bobs, stays with the front door.

Each Bob declares its rule in bob.toml:

    [routing]
    prefixes = ["tutor:"]
    keywords = ["grammar", "vocabulary"]

Usage:
    python3 harness/route.py --text "<message>" --fleet <fleet.toml>
    python3 harness/route.py --text "<message>" <bob.toml> [<bob.toml> ...]
    prints the slug of the Bob that should answer, or `front`.

`--fleet` reads every Bob new_bob.py registered: each `[[bob]]` entry's `path` holds a bob.toml.
"""
from __future__ import annotations

import argparse
import pathlib
import re
import sys
import tomllib

MENTION = re.compile(r"^\s*(<@[A-Z0-9]+(\|[^>]*)?>\s*)+")


def _body(text: str) -> str:
    return MENTION.sub("", text).strip().lower()


def route(text: str, bobs: list[dict]) -> str | None:
    """bobs: [{"slug", "prefixes", "keywords"}]. Returns the slug that should answer, or None for
    the front door."""
    body = _body(text)
    for bob in bobs:
        if any(body.startswith(p.lower()) for p in bob.get("prefixes", [])):
            return bob["slug"]
    hits = {bob["slug"] for bob in bobs
            for k in bob.get("keywords", [])
            if re.search(rf"(?<!\w){re.escape(k.lower())}(?!\w)", body)}
    return hits.pop() if len(hits) == 1 else None


def from_manifest(path: str) -> dict:
    with open(path, "rb") as fh:
        cfg = tomllib.load(fh)
    rules = cfg.get("routing") or {}
    return {"slug": cfg["bob"]["slug"], "prefixes": list(rules.get("prefixes", [])),
            "keywords": list(rules.get("keywords", []))}


def fleet_manifests(fleet_path: str) -> list[str]:
    """The bob.toml of every Bob in a fleet file that records where it lives."""
    with open(fleet_path, "rb") as fh:
        fleet = tomllib.load(fh)
    return [str(pathlib.Path(bob["path"]) / "bob.toml") for bob in fleet.get("bob", [])
            if bob.get("path")]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--text", required=True)
    ap.add_argument("--fleet", help="a fleet.toml whose entries carry each Bob's path")
    ap.add_argument("manifests", nargs="*")
    args = ap.parse_args(argv)
    manifests = list(args.manifests) + (fleet_manifests(args.fleet) if args.fleet else [])
    if not manifests:
        ap.error("name the Bobs: --fleet <fleet.toml> or one or more bob.toml paths")
    print(route(args.text, [from_manifest(p) for p in manifests]) or "front")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
