#!/usr/bin/env python3
"""What has been added to the release notes since the last time they were posted.

Release notes are written with each change, and posted to a channel in batches (for example
twice a day) so the channel is not flooded.

Batching needs a marker, and the marker is the thing a reader sees rather than a timestamp. A
clock-based cut-off ("everything from the last 12 hours") silently drops an entry written 12 hours
and one minute ago, and silently repeats one when a post runs late. So the state is the HEADING of
the newest entry already posted, and the digest is every entry above it. An entry is posted exactly
once, whenever the post actually happens.

The notes file is `[paths] release_notes` in the manifest.

    python3 harness/release_digest.py            # the unposted entries, newest first
    python3 harness/release_digest.py --count    # how many, for a loop that must not post zero
    python3 harness/release_digest.py --mark     # record the newest entry as posted
    python3 harness/release_digest.py --selftest # prove the cut can still be wrong
"""

from __future__ import annotations

import json
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import bobcfg  # noqa: E402

NOTES = bobcfg.path(bobcfg.config(), "release_notes")
#: Beside the session, not in the Bob's folder: it records what a channel has seen, which is a
#: property of this agent's posting history and not of the files - a fresh copy has posted nothing.
STATE = bobcfg.state_home() / "release-posted.json"

#: An entry starts at a level-2 heading. The file's own preamble sits above the first one and is
#: never part of a digest.
HEADING = "## "


def entries(text: str) -> list[tuple[str, str]]:
    """Every entry as `(heading, body)`, newest first, preamble excluded.

    Splitting on the heading marker rather than on `---` because the separator is cosmetic and an
    author will forget it; the heading is what the entry is.
    """
    out: list[tuple[str, str]] = []
    heading: str | None = None
    lines: list[str] = []

    def close(name: str) -> None:
        # **The trailing `---` belongs to the file, not to the entry.** Left on, every posted
        # entry ends in a horizontal rule that renders as a stray `---` in Slack. Only a TRAILING
        # run is dropped; a rule inside a body is the author's and stays.
        body = lines[:]
        while body and body[-1].strip() in ("", "---"):
            body.pop()
        out.append((name, "\n".join(body).strip()))

    for line in text.splitlines():
        if line.startswith(HEADING):
            if heading is not None:
                close(heading)
            heading, lines = line[len(HEADING):].strip(), []
        elif heading is not None:
            lines.append(line)
    if heading is not None:
        close(heading)
    return out


def posted() -> str | None:
    """The heading last marked as posted, or None when nothing ever has been."""
    try:
        return json.loads(STATE.read_text()).get("heading")
    except (OSError, ValueError):
        return None


def unposted(text: str, marker: str | None) -> list[tuple[str, str]]:
    """Entries above `marker`, newest first.

    **A marker that is not in the file yields EVERYTHING, not nothing.** The file was rewritten, or
    the state came from another checkout. Posting a duplicate is recoverable; dropping every entry
    silently is the failure this module exists to prevent, and it is the one that looks like
    success.
    """
    found = entries(text)
    if marker is None:
        return found
    for i, (heading, _) in enumerate(found):
        if heading == marker:
            return found[:i]
    return found


def render(items: list[tuple[str, str]]) -> str:
    return "\n\n".join(f"{HEADING}{heading}\n\n{body}" for heading, body in items)


def mark(text: str) -> str | None:
    """Record the newest entry as posted. Returns the heading, or None on an empty file."""
    found = entries(text)
    if not found:
        return None
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps({"heading": found[0][0]}, indent=1) + "\n")
    return found[0][0]


def selftest() -> int:
    """Prove the cut can be wrong, because a digest that silently posts nothing looks like calm."""
    sample = (
        "# Release notes\n\nPreamble that is not an entry.\n\n---\n\n"
        "## third\n\nC body\n\n---\n\n"
        "## second\n\nB body\n\n---\n\n"
        "## first\n\nA body\n"
    )
    failures = []

    def check(label, ok):
        print(f"  {'ok  ' if ok else 'FAIL'}  {label}")
        if not ok:
            failures.append(label)

    found = entries(sample)
    check("the preamble is not an entry", [h for h, _ in found] == ["third", "second", "first"])
    check("a body is carried with its heading", found[0][1] == "C body")
    check("no marker yields every entry", len(unposted(sample, None)) == 3)
    check("a marker yields only what sits above it",
          [h for h, _ in unposted(sample, "second")] == ["third"])
    check("the newest marker yields nothing", unposted(sample, "third") == [])
    check("the oldest marker yields the other two",
          [h for h, _ in unposted(sample, "first")] == ["third", "second"])
    # The case that matters: an unknown marker must not read as "all caught up".
    check("an unknown marker yields EVERYTHING, not nothing",
          len(unposted(sample, "a heading that was never written")) == 3)
    check("an empty file yields nothing and does not raise", unposted("", None) == [])
    check("a file with a preamble and no entry yields nothing",
          unposted("# Notes\n\njust prose\n", None) == [])
    check("render round-trips a heading and its body",
          render(unposted(sample, "second")) == "## third\n\nC body")

    if failures:
        print(f"SELFTEST FAILED: {len(failures)} check(s)")
        return 1
    print("SELFTEST OK")
    return 0


def main() -> int:
    args = sys.argv[1:]
    if "--selftest" in args:
        return selftest()
    text = NOTES.read_text(encoding="utf-8") if NOTES.exists() else ""
    if "--mark" in args:
        heading = mark(text)
        print(f"marked as posted: {heading!r}" if heading else "nothing to mark: no entries")
        return 0
    items = unposted(text, posted())
    if "--count" in args:
        print(len(items))
        return 0
    if not items:
        print("nothing unposted")
        return 0
    print(render(items))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
