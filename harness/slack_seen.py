#!/usr/bin/env python3
"""Write a Slack timestamp into the watch state only when a saved read contains it.

The mention seen-file suppresses a message permanently, and a cursor skips everything before it.
An agent writing them by hand will sometimes compute a timestamp from a displayed clock time and
invent the fractional digits. A ts in the seen-file that Slack never issued is indistinguishable
from a handled mention, and a cursor set past a real message hides it.

So the writer takes the READ, not the author's word for it:

    python3 harness/slack_seen.py mark   --read <file> <ts>...
    python3 harness/slack_seen.py cursor --read <file> --channel <id> [--new] <ts>

`<file>` is the saved output of a Slack read - the search result the mention sweep already writes
to disk for large results, or a channel or thread read redirected to a file. Every ts must appear
in it, or nothing is written.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys

#: A Slack ts as Slack returns it: ten digits, a dot, six digits. Nothing else is a ts.
TS = re.compile(r"\b\d{10}\.\d{6}\b")
#: `Message_ts:` in a search result, `Message TS:` in a channel or thread read.
TS_FIELD = re.compile(r"Message[_ ]TS:\s*(\d{10}\.\d{6})", re.I)
#: The search result's own count of blocks, which a truncated file contradicts.
HEADER_COUNT = re.compile(r"##\s*Messages\s*\((\d+)\s*results?\)", re.I)
BLOCK = re.compile(r"^###\s*Result\s+\d+\s+of\s+\d+", re.M)

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import bobcfg  # noqa: E402

SEEN = bobcfg.state_home() / "slack-mention-seen.txt"
CURSORS = bobcfg.state_home() / "slack-watch-dm-cursors.json"


class NotInRead(Exception):
    """A timestamp that the saved read does not contain."""


class ReadMismatch(Exception):
    """The read holds fewer result blocks than its own header claims."""


def timestamps(read_text: str) -> set[str]:
    """Every message ts the read contains.

    A search result states how many messages it found; a file holding fewer blocks than that is a
    truncation, and a truncation is exactly how a ts goes missing from the population being checked
    against. Counted before anything is returned.
    """
    header = HEADER_COUNT.search(read_text)
    if header:
        blocks = len(BLOCK.findall(read_text))
        if blocks and blocks < int(header.group(1)):
            raise ReadMismatch(f"the read header claims {header.group(1)} results and the file "
                               f"holds {blocks} blocks: it is truncated")
    return set(TS_FIELD.findall(read_text))


def _check(read_text: str, wanted: list[str]) -> None:
    held = timestamps(read_text)
    missing = [t for t in wanted if t not in held]
    if missing:
        raise NotInRead("the saved read does not contain " + ", ".join(missing) +
                        f"; it holds {len(held)} timestamp(s). A ts is copied from a read, "
                        "never computed from a clock time.")


def mark(seen_path: pathlib.Path, wanted: list[str], read_text: str) -> list[str]:
    """Append the timestamps to the seen-file. Writes all of them or none."""
    _check(read_text, wanted)
    existing = seen_path.read_text().splitlines() if seen_path.exists() else []
    added = [t for t in wanted if t not in existing]
    if added:
        seen_path.write_text("\n".join(existing + added) + "\n")
    return added


def set_cursor(cursors_path: pathlib.Path, channel: str, ts: str, read_text: str,
               new: bool = False) -> None:
    """Move one channel's cursor. Refuses a ts the read does not hold, and a channel the file does
    not know unless `new` says it was just enumerated (a DM list or channel list read). A missing
    file is an empty one: the first cursor creates it."""
    _check(read_text, [ts])
    try:
        data = json.loads(cursors_path.read_text())
    except FileNotFoundError:
        data = {}
    if channel not in data:
        if not new:
            raise KeyError(f"{channel} is not in {cursors_path.name}; enumerate the DMs or channels "
                           "first, then pass --new for one that list showed. Never invent a surface")
        if not re.fullmatch(r"[CDG][A-Z0-9]{8,}", channel):
            raise KeyError(f"{channel} is not a Slack channel id")
        data[channel] = ts
    entry = data[channel]
    if isinstance(entry, dict):
        entry["cursor"] = ts
    else:
        data[channel] = ts
    cursors_path.parent.mkdir(parents=True, exist_ok=True)
    cursors_path.write_text(json.dumps(data, indent=2) + "\n")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="command", required=True)
    m = sub.add_parser("mark"); m.add_argument("ts", nargs="+")
    m.add_argument("--read", required=True); m.add_argument("--seen", default=str(SEEN))
    c = sub.add_parser("cursor"); c.add_argument("ts")
    c.add_argument("--read", required=True); c.add_argument("--channel", required=True)
    c.add_argument("--cursors", default=str(CURSORS))
    c.add_argument("--new", action="store_true",
                   help="the channel is not in the file yet; it was just enumerated")
    args = ap.parse_args(argv)
    read_text = pathlib.Path(args.read).read_text(errors="replace")
    try:
        if args.command == "mark":
            added = mark(pathlib.Path(args.seen), args.ts, read_text)
            print(f"marked {len(added)} of {len(args.ts)} (the rest were already there)")
        else:
            set_cursor(pathlib.Path(args.cursors), args.channel, args.ts, read_text, args.new)
            print(f"{args.channel} cursor -> {args.ts}")
    except (NotInRead, ReadMismatch, KeyError) as exc:
        print(f"REFUSED: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
