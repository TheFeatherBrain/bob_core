#!/usr/bin/env python3
"""The thread-poll ledger: which thread roots this watch pass actually looked at.

A channel read does not return thread replies, so a reply under an old thread is invisible to a
watch that only reads channels and cursors. And an absent entry in a seen-list reads exactly like
a surface with nothing on it: absent and never-looked-at are the same bytes. So the poll itself is
recorded, and a root that is due and has not been polled recently is something a pass can print.

**Due is a window, not the whole file.** Roots accumulate; polling all of them every pass is what
makes a pass skip them, and a root nobody has posted under for two days is not where the next reply
lands.

**Every root prints with its channel.** A thread read takes a channel and a ts. A roots line is
`<ts>` or `<ts> <channel_id>`; a bare line takes its file's default channel from the manifest's
`[slack.roots]`, and a file with no default prints `channel-unknown` rather than a guess.
`<ts> unreadable` marks a root no channel can read: it is not polled, and stderr counts it.

The roots files live in the Bob's state home: every file named in `[slack.roots]`, plus
`slack-watch-threads-dm.txt`, which has no default channel because several DMs write to it.

Run:  python3 harness/slack_threads.py --due     # `<ts> <channel>` per root to poll this pass
      python3 harness/slack_threads.py --stale   # the ones overdue, exit 1 if any
      python3 harness/slack_threads.py --record TS --reply TS --read FILE

`--reply` is what the poll saw at the bottom of the thread, and a thread with no replies passes its
own root ts. Recording a poll without it keeps the root overdue on purpose: a poll that did not say
what it saw cannot show the thread has not grown since.
"""

from __future__ import annotations

import argparse
import json
import pathlib
from pathlib import Path
import re
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import bobcfg  # noqa: E402

#: A root nobody has posted under in two days is not where the next reply lands.
DEFAULT_WINDOW_HOURS = 48.0

#: A watch that runs every five to ten minutes has missed several passes by this age.
DEFAULT_MAX_AGE_S = 900.0

#: The channel a bare line lives in, by roots-file name, from the manifest's [slack.roots].
DEFAULT_CHANNEL = dict(bobcfg.get(bobcfg.config(), "slack").get("roots") or {})

#: The DM roots file. No default channel: more than one DM can write to it, so a default would be
#: a guess, and a thread read in the wrong channel returns thread_not_found.
DM_ROOTS = "slack-watch-threads-dm.txt"

STATE = bobcfg.state_home() / "slack-watch-polled.json"
ROOTS = tuple(bobcfg.state_home() / name for name in (*DEFAULT_CHANNEL, DM_ROOTS))

#: Printed where a channel belongs when no line gives the root one.
UNKNOWN_CHANNEL = "channel-unknown"

#: A second column of this word marks a root no channel can read. Listing it as pollable is a
#: read that must fail; dropping it from the file loses the record that it was ever tracked.
UNREADABLE = "unreadable"

#: Public channels start C, private groups G, DMs D.
CHANNEL_ID = re.compile(r"[CDG][A-Z0-9]+")


def _require_channel(value: str, what: str) -> str:
    """A Slack channel id, or raise: a channel column that addresses nothing is a root no poll can
    reach, recorded as if it were tracked."""
    text = str(value)
    if not CHANNEL_ID.fullmatch(text):
        raise ValueError(f"{what} is not a Slack channel id: {text!r}")
    return text


def root_lines(text: str, path: str = "",
               default: str | None = None) -> list[tuple[str, str | None]]:
    """Every `(ts, channel)` line of one roots file, in file order, duplicates kept.

    A line is `<ts>`, `<ts> <channel_id>` or `<ts> unreadable`; a bare line takes `default`, and
    None is channel-unknown. This is the one parser for these files; `slack_roots` reads them too.

    A line that is not a timestamp raises rather than being skipped: silently dropping a root is
    the defect this module exists for.
    """
    out: list[tuple[str, str | None]] = []
    for lineno, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line:
            continue
        fields = line.split()
        if len(fields) > 2:
            raise ValueError(f"{path}:{lineno} is not `<ts> [channel]`: {line!r}")
        try:
            float(fields[0])
        except ValueError:
            raise ValueError(f"{path}:{lineno} is not a Slack timestamp: {line!r}") from None
        if len(fields) == 1:
            out.append((fields[0], default))
        elif fields[1] == UNREADABLE:
            out.append((fields[0], UNREADABLE))
        else:
            out.append((fields[0], _require_channel(fields[1], f"{path}:{lineno} channel column")))
    return out


def channels(paths) -> dict[str, str | None]:
    """Every root in every file, in first-seen order, mapped to its channel; None is unknown.

    One root under two channels raises: only one of the two reads can find the thread, and nothing
    here can say which. A bare line with no default claims nothing, so it contradicts nothing.
    """
    where: dict[str, str | None] = {}
    for raw_path in paths:
        path = pathlib.Path(raw_path)
        if not path.is_file():
            continue
        for ts, channel in root_lines(path.read_text(encoding="utf-8"), str(path), DEFAULT_CHANNEL.get(path.name)):
            known = where.get(ts)
            if known is None:
                where[ts] = channel
            elif channel is not None and channel != known:
                raise ValueError(f"{path}: root {ts} is in {known} and {channel}")
    return where


def _require_ts(value: str, what: str) -> str:
    """A Slack timestamp, or raise. The same bar `root_lines` sets, for values a caller hands in:
    a malformed key written to the ledger matches no root and fails silently."""
    text = "" if value is None else str(value)
    try:
        float(text)
    except ValueError:
        raise ValueError(f"{what} is not a Slack timestamp: {text!r}") from None
    if text.strip() != text or not text.strip():
        raise ValueError(f"{what} is not a Slack timestamp: {text!r}")
    return text


def _in_window(roots, now: float, window_hours: float) -> list[str]:
    return [root for root in roots if now - float(root) <= window_hours * 3600]


def _pollable(where: dict, window) -> list[str]:
    """The in-window roots a thread read can reach: every one not marked unreadable."""
    return [root for root in window if where[root] != UNREADABLE]


def due(paths, now: float, window_hours: float = DEFAULT_WINDOW_HOURS) -> list[str]:
    """The roots worth polling on this pass."""
    where = channels(paths)
    return _pollable(where, _in_window(where, now, window_hours))


def _stale_rows(where: dict, rows, state: dict, now: float, max_age_s: float) -> list[str]:
    """The one definition of overdue; `stale` and `--stale` both call it.

    A channel-unknown root is overdue whatever the ledger says: an entry for it cannot show which
    channel was read, and a read in the wrong one finds nothing and looks the same.
    """
    overdue = []
    for root in rows:
        entry = state.get(root) or {}
        last = entry.get("polled_at")
        if last is None or now - last > max_age_s:
            overdue.append(root)
        elif not entry.get("newest_reply"):
            overdue.append(root)
        elif where.get(root) is None:
            overdue.append(root)
    return overdue


def stale(paths, state: dict, now: float,
          window_hours: float = DEFAULT_WINDOW_HOURS,
          max_age_s: float = DEFAULT_MAX_AGE_S) -> list[str]:
    """Due roots this ledger cannot show a recent COMPLETE poll for. Never polled counts.

    Two ways to be overdue. An old poll is obvious. A recent poll that did not record what it saw
    is not: it clears the age test while saying nothing about whether the thread has grown, so a
    mention landing as a late reply in an already-answered thread would stay hidden.
    """
    return _stale_rows(channels(paths), due(paths, now, window_hours), state, now, max_age_s)


def record(state: dict, root: str, polled_at: float, newest_reply: str | None = None) -> dict:
    """Note that `root` was polled, and what the poll saw at the bottom of the thread.

    A poll that does not say what it saw CLEARS the previous one's answer rather than leaving it
    standing: a fresh `polled_at` beside an older `newest_reply` asserts a thread state no single
    poll ever saw.
    """
    root = _require_ts(root, "root")
    if newest_reply is not None:
        newest_reply = _require_ts(newest_reply, "newest_reply")
    state = dict(state)
    entry = dict(state.get(root) or {})
    entry["polled_at"] = polled_at
    if newest_reply is None:
        entry.pop("newest_reply", None)
    else:
        entry["newest_reply"] = newest_reply
    state[root] = entry
    return state


def load(path) -> dict:
    """The ledger, or empty. A torn write must not take the watch down."""
    path = pathlib.Path(path)
    if not path.is_file():
        return {}
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


def save(path, state: dict) -> None:
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _channel_text(channel: str | None) -> str:
    return channel or UNKNOWN_CHANNEL


def _say_unaddressable(where: dict, window, rows) -> None:
    """Name on stderr every due root the poll cannot read, so neither kind is dropped silently."""
    unknown = [root for root in rows if where[root] is None]
    if unknown:
        print(f"{len(unknown)} due root(s) are {UNKNOWN_CHANNEL}: their file has no default "
              f"channel. Write each line as `<ts> <channel_id>`.", file=sys.stderr)
    unreadable = [root for root in window if where[root] == UNREADABLE]
    if unreadable:
        print(f"{len(unreadable)} due root(s) marked {UNREADABLE}, not polled: "
              f"{' '.join(unreadable)}", file=sys.stderr)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--due", action="store_true")
    parser.add_argument("--stale", action="store_true")
    parser.add_argument("--record", metavar="TS")
    parser.add_argument("--reply", metavar="TS")
    parser.add_argument("--read", metavar="FILE",
                        help="the saved thread read this poll made; it must contain the root and "
                             "the reply ts, or the poll is refused")
    parser.add_argument("--window-hours", type=float, default=DEFAULT_WINDOW_HOURS)
    parser.add_argument("--max-age", type=float, default=DEFAULT_MAX_AGE_S)
    args = parser.parse_args(argv)
    now = time.time()

    if args.record:
        # A poll is an OBSERVATION. Both values come from the caller, so without the saved read
        # the ledger would record that a read happened whether or not one did. The read is checked
        # by the same parser the seen-file uses, so both agree what a read is.
        _require_ts(args.record, "root")
        if args.reply is not None:
            _require_ts(args.reply, "newest_reply")
        if not args.read:
            print("REFUSED: --record needs --read <file>, the saved thread read. A poll that "
                  "cannot show its read records a claim, not an observation.")
            return 1
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import slack_seen
        try:
            held = slack_seen.timestamps(Path(args.read).read_text(encoding="utf-8", errors="replace"))
        except (OSError, slack_seen.ReadMismatch) as exc:
            print(f"REFUSED: {exc}")
            return 1
        missing = [ts for ts in (args.record, args.reply) if ts and ts not in held]
        if missing:
            print("REFUSED: the saved read does not contain " + ", ".join(missing) +
                  f"; it holds {len(held)} timestamp(s). Poll the thread, save the read, then "
                  "record what it showed.")
            return 1
        save(STATE, record(load(STATE), args.record, now, args.reply))
        print(f"polled {args.record}" + (f", newest reply {args.reply}" if args.reply else ""))
        return 0

    # One read of the roots files per run: a file rewritten between two reads would print a root
    # as channel-unknown that no single snapshot left without one.
    where = channels(ROOTS)
    window = _in_window(where, now, args.window_hours)
    rows = _pollable(where, window)
    if args.due:
        for root in rows:
            print(f"{root} {_channel_text(where[root])}")
        _say_unaddressable(where, window, rows)
        return 0

    overdue = _stale_rows(where, rows, load(STATE), now, args.max_age)
    for root in overdue:
        print(f"  unpolled or overdue: {root} {_channel_text(where[root])}")
    _say_unaddressable(where, window, rows)
    print(f"\n{len(overdue)} of {len(rows)} due root(s) overdue "
          f"(window {args.window_hours}h, max age {args.max_age}s)")
    return 1 if overdue else 0


if __name__ == "__main__":
    import oscompat
    oscompat.utf8_stdio()
    sys.exit(main())
