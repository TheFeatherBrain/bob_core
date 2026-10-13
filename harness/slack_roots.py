#!/usr/bin/env python3
"""Compute which thread roots a channel's history carries that the tracked-root files do not.

Acquiring roots by habit (after answering a mention inside a thread, append that thread's root)
misses three routes by which a root gains replies: the Bob's own top-level post that others reply
under, someone else's post that others reply under, and the Bob's reply under a root that never
mentioned it. An instruction to remember is not a mechanism, so the root set is computed from what
Slack prints rather than accumulated by hand.

Slack's channel read prints `Thread: N replies` under the message that owns the thread. That line
is the observable; this module reads it and nothing else.
"""
from __future__ import annotations

import argparse
import pathlib
import re
import sys

from slack_threads import DEFAULT_CHANNEL, DM_ROOTS, ROOTS, _require_channel, root_lines

#: `Message TS: 1790114844.390799` - the line Slack prints for every message in a channel read.
MESSAGE_TS = re.compile(r"^Message TS: (\d+\.\d+)\s*$", re.M)

#: `Thread: 4 replies (latest: ...)` - printed only under a message that owns a thread. Slack says
#: `1 replies`, so the plural is not optional and matching `reply` as well would match nothing.
THREAD_MARKER = re.compile(r"^Thread: (\d+) repl(?:y|ies)", re.M)

#: `Channel: #general (C0123456789)` - the header a channel read opens with.
CHANNEL_HEADER = re.compile(r"^Channel: .*\((\S+)\)\s*$", re.M)


def reply_bearing(history: str) -> list[tuple[str, int]]:
    """Every (root_ts, reply_count) the history prints a thread marker for, in document order.

    The marker belongs to the message ABOVE it. Binding it to the next one instead attributes the
    replies to a root that has none and misses the root that has them - an off-by-one that reports
    a plausible list and is wrong in both directions at once."""
    out: list[tuple[str, int]] = []
    current: str | None = None
    for line in history.splitlines():
        message = MESSAGE_TS.match(line)
        if message:
            current = message.group(1)
            continue
        marker = THREAD_MARKER.match(line)
        if marker and current is not None:
            out.append((current, int(marker.group(1))))
    return out


def history_channel(history: str, path: str = "") -> str:
    """The one channel a saved read came from, or raise.

    An appended root is written with this channel unless it is the tracked file's default. A
    history with no header, or with headers naming two channels, would file the root under a guess,
    and a thread read in the wrong channel returns thread_not_found for a root the file says it
    tracks."""
    found = {_require_channel(ch, f"{path} header channel")
             for ch in CHANNEL_HEADER.findall(history)}
    if len(found) != 1:
        raise ValueError(f"{path} names {len(found)} channel(s) in its header, need exactly 1: "
                         f"{sorted(found)}")
    return found.pop()


def append_line(root: str, channel: str, tracked_name: str) -> str:
    """`<ts>` when `channel` is the tracked file's default, `<ts> <channel>` otherwise."""
    return root if DEFAULT_CHANNEL.get(tracked_name) == channel else f"{root} {channel}"


def tracked_for(channel: str) -> pathlib.Path:
    """The roots file a channel's threads are tracked in: the file whose [slack.roots] default is
    that channel, else the DM file, where every line carries its own channel."""
    by_name = {path.name: path for path in ROOTS}
    for name, default in DEFAULT_CHANNEL.items():
        if default == channel:
            return by_name[name]
    return by_name[DM_ROOTS]


def tracked_roots(text: str, path: str = "") -> set[str]:
    """The roots a tracked-root file holds, blanks and stray whitespace tolerated.

    These files are hand-edited. A set that keeps a trailing space or the empty string reports an
    already-tracked root as missing, and the append then writes a second copy of it. The same holds
    for a second column: a line `<ts> <channel_id>` or `<ts> unreadable` tracks `<ts>`, and the file
    is read by the thread poll's own parser so the two cannot disagree about what a line means."""
    return {ts for ts, _channel in root_lines(text, path)}


def missing(history: str, tracked: str, path: str = "") -> list[str]:
    """Reply-bearing roots the tracked file does not hold, in document order, no duplicates.

    Two overlapping channel reads saved to one file print the same message twice, so a list that
    keeps document order without deduplicating appends the same root twice."""
    have = tracked_roots(tracked, path)
    seen: set[str] = set()
    out: list[str] = []
    for root, _count in reply_bearing(history):
        if root in have or root in seen:
            continue
        seen.add(root)
        out.append(root)
    return out


def top_level(history: str) -> set[str]:
    """Every ts the history prints as a `Message TS:` line.

    A channel read returns top-level messages only - thread replies are not in it. That absence is
    what makes this set a membership test for rootness rather than merely a list of messages."""
    return set(MESSAGE_TS.findall(history))


def tracked_roots_in_order(text: str, path: str = "") -> list[str]:
    """The tracked file's roots in file order, duplicates kept.

    `tracked_roots` returns a set because membership is all a gap check needs. An audit reports in
    the order a person reads the file, so it needs the sequence, and the dedup belongs at the point
    of reporting rather than here."""
    return [ts for ts, _channel in root_lines(text, path)]


def not_a_root(history: str, tracked: str, path: str = "") -> list[str]:
    """Tracked entries that the history proves are not roots, in file order, no duplicates.

    A tracked ts inside the history's window and absent from its top-level set is a reply. Slack's
    thread read takes a root ts; given a reply ts it returns that one message and no replies, so the
    poll records the root as clean forever while replies under the true parent stay unread.

    The window is the floor. A tracked ts older than the oldest message in the read is unjudgeable -
    the read simply did not reach that far - and reporting it would flag every root below the window
    at once. Comparison is numeric: Slack timestamps order lexicographically only while their integer
    parts are the same width.

    There is no ceiling, deliberately: a channel read taken NOW reaches the channel's top, so a
    tracked ts newer than its newest message is not a top-level message. That holds only for a live
    read. Audit against a history saved earlier than an entry was appended and the entry is reported
    although nothing is wrong with it.
    """
    tops = top_level(history)
    if not tops:
        return []
    floor = min(float(ts) for ts in tops)
    out: list[str] = []
    seen: set[str] = set()
    for ts in tracked_roots_in_order(tracked, path):
        if ts in tops or ts in seen or float(ts) < floor:
            continue
        seen.add(ts)
        out.append(ts)
    return out


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--history", required=True, metavar="FILE",
                        help="a saved Slack channel read")
    parser.add_argument("--tracked", metavar="FILE",
                        help="the tracked-root file for that surface (default: the file the "
                             "history's channel belongs to, from [slack.roots])")
    parser.add_argument("--append", action="store_true",
                        help="append what is missing to the tracked file instead of printing it")
    parser.add_argument("--audit", action="store_true",
                        help="report tracked entries the history proves are replies, not roots")
    args = parser.parse_args(argv)

    history = pathlib.Path(args.history).read_text()
    tracked_path = (pathlib.Path(args.tracked) if args.tracked
                    else tracked_for(history_channel(history, args.history)))
    if not tracked_path.exists():
        tracked_path.parent.mkdir(parents=True, exist_ok=True)
        tracked_path.write_text("")
    if args.audit:
        wrong = not_a_root(history, tracked_path.read_text(), str(tracked_path))
        for root in wrong:
            print(root)
        print(f"{len(wrong)} tracked entr(ies) are replies, not roots", file=sys.stderr)
        return 1 if wrong else 0
    gaps = missing(history, tracked_path.read_text(), str(tracked_path))
    total = len(reply_bearing(history))
    if args.append and gaps:
        channel = history_channel(history, args.history)
        with tracked_path.open("a") as fh:
            for root in gaps:
                fh.write(append_line(root, channel, tracked_path.name) + "\n")
    for root in gaps:
        print(root)
    print(f"{len(gaps)} of {total} reply-bearing root(s) untracked", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
