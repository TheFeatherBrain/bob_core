#!/usr/bin/env python3
"""Refuse Slack text that mrkdwn will render wrong, or silently eat.

Two groups of checks:

* Formatting. Code fences that eat lines, and Markdown habits (`**bold**`, `## `,
  `- ` bullets, `[label](url)`) that Slack shows as literal punctuation. Each
  check names what it costs, because a linter whose findings look like style
  gets switched off.
* Register. A word budget for prose, a sentence-length limit, a hedge list and
  an emphasis cap. These are policy, so a Bob can override them in its manifest:

      [slack.lint]
      word_budget = 300
      sentence_words = 30
      max_emphasis = 1
      hedges = ["it seems", "needless to say"]

Code blocks are exempt from every register check.

    python3 harness/slack_lint.py --selftest      # prove each check can fire
    python3 harness/slack_lint.py < draft.txt     # lint a draft
"""
import pathlib
import re
import sys

FENCE = "```"


def fence_walk(lines):
    """Yield (n, line, was_in, count) per line, tracking fenced state.

    ONE parity implementation, used by the absorption check and by the markdown
    suppression, so the two cannot disagree.

    Parity counts fence OCCURRENCES, not lines that start with one. A close
    sharing its line (`B```) still closes; a one-line block (```x```) opens and
    closes in the same breath and must leave parity untouched.
    """
    in_block = False
    for n, line in enumerate(lines, 1):
        count = line.count(FENCE)
        was_in = in_block
        if count:
            in_block = (in_block + count) % 2 == 1
        yield n, line, was_in, count


def fenced_line_numbers(lines) -> set:
    """1-based line numbers Slack renders inside a code block.

    A line is inside if the block was already open when it started, or if it
    carries a fence at all - content sharing a fence line renders inside the
    block, which is the whole reason check 2 exists.

    An UNCLOSED fence leaves every following line inside, which is what Slack
    does and why check 1 flags the odd count separately. Suppressing markdown
    findings for the tail of a message with a stray fence is correct: the
    asterisks really are literal there. The stray fence is the finding.
    """
    return {n for n, _, was_in, count in fence_walk(lines) if was_in or count}


def lint(text: str) -> list[str]:
    """Findings, worst first. Empty means the text is safe to send."""
    out = []
    lines = text.split("\n")

    # 1. Fences pair up. A stray one opens a block that never closes and
    #    swallows the whole rest of the message.
    fences = sum(line.count(FENCE) for line in lines)
    if fences % 2:
        out.append(f"odd fence count ({fences}): one block never closes")

    # 2. Content sharing the opening fence line can go missing from the FIRST
    #    fenced block in a message. The fix - start the content on the next
    #    line - costs one newline.
    seen_fence = False
    for n, line in enumerate(lines, 1):
        stripped = line.lstrip()
        if not stripped.startswith(FENCE):
            continue
        rest = stripped[len(FENCE):].strip()
        if rest and not seen_fence:
            out.append(f"line {n}: content shares the message's first opening fence; it can be dropped: {rest[:60]!r}")
        seen_fence = True

    # 3. Text on the line right after a closing fence that stands alone is
    #    absorbed into the block. Parity counts fence OCCURRENCES, as check 1
    #    does, so a close sharing its line still closes.
    for n, line, was_in, count in fence_walk(lines):
        if not count:
            continue
        alone = line.strip() == FENCE
        if was_in and alone and n < len(lines) and lines[n].strip():
            out.append(f"line {n + 1}: follows a standalone closing fence with no blank line; it is absorbed into the block")

    # 4. Markdown habits that survive into Slack as visible punctuation.
    #    Skipped inside a fenced block, where they are data, not habits.
    fenced = fenced_line_numbers(lines)
    for pattern, cost in (
        (r"\*\*[^*\n]+\*\*", "renders the asterisks literally; Slack bold is *one* asterisk"),
        (r"~~[^~\n]+~~", "renders the tildes literally; Slack strike is ~one~ tilde"),
        (r"\[[^\]\n]+\]\([^)\n]+\)", "renders literally; Slack links are <url|label>"),
        (r"(?m)^#{1,6}\s", "renders literally; Slack has no headings, use bold on its own line"),
        (r"(?m)^[-*]\s", "renders literally; Slack bullets are a literal •"),
    ):
        for m in re.finditer(pattern, text):
            n = text.count("\n", 0, m.start()) + 1
            if n in fenced:
                continue
            out.append(f"line {n}: {m.group(0)[:30]!r} {cost}")
    out.extend(register_findings(text, lines))
    return out


#: Register defaults. A Bob overrides any of them in `[slack.lint]` of its manifest.
DEFAULTS = {
    "word_budget": 300,       # refuse above this many words of prose
    "sentence_words": 30,     # refuse a sentence longer than this
    "max_emphasis": 1,        # emphasis is not evidence
    "hedges": [
        "worth noting", "it is worth saying", "i would argue", "to be clear",
        "worth knowing", "i would expect", "it seems", "needless to say",
        "it goes without saying",
    ],
}


def register_rules() -> dict:
    """DEFAULTS, overridden by the manifest's `[slack.lint]` when a manifest can be read.

    Never fails: a linter that cannot find its manifest still lints with the defaults.
    """
    rules = dict(DEFAULTS)
    try:
        sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
        import bobcfg
        rules.update((bobcfg.config().get("slack") or {}).get("lint") or {})
    except Exception:  # noqa: BLE001
        pass
    return rules


def prose_lines(lines, fenced: set) -> list[str]:
    """Everything Slack renders as prose - fenced blocks excluded, they carry the measurements."""
    return [line for n, line in enumerate(lines, 1) if n not in fenced]


def register_findings(text: str, lines) -> list[str]:
    """The register rules, as measurements rather than as reminders. None of them can judge
    whether a sentence is filler; each measures a proxy stated as a number."""
    rules = register_rules()
    budget, limit, cap = rules["word_budget"], rules["sentence_words"], rules["max_emphasis"]
    fenced = fenced_line_numbers(lines)
    prose = " ".join(prose_lines(lines, fenced))
    out = []

    words = len(prose.split())
    if words > budget:
        out.append(f"{words} words of prose, ceiling {budget}: cut {words - budget}. Delete any "
                   f"sentence that does not change what the reader does next")

    for sentence in re.split(r"(?<=[.!?])\s+", prose):
        count = len(sentence.split())
        if count > limit:
            out.append(f"{count}-word sentence, limit {limit}: "
                       f"{sentence.strip()[:70]!r}")

    lowered = prose.lower()
    for hedge in rules["hedges"]:
        if hedge in lowered:
            out.append(f"hedge {hedge!r}: a hedge is a stated uncertainty with a number on it, "
                       f"or it is noise")

    emphasis = len(re.findall(r"(?<!\w)[*_][^*_\n]+[*_](?!\w)", prose))
    if emphasis > cap:
        out.append(f"{emphasis} emphases, limit {cap}: emphasis is not evidence")

    return out


#: (label, text, the substring the finding must contain). A case per check, plus
#: a clean twin per check - a linter that only ever fires is as useless as one
#: that never does, and only the pair shows it discriminates.
CASES = [
    ("odd fence", "a\n```\nb", "odd fence count"),
    ("even fences", "a\n```\nb\n```\n\nc", None),
    ("content on the first opening fence", "```AAA\nBBB\n```\n\nx", "it can be dropped"),
    # Only the first block's opening fence is affected.
    ("content on a later opening fence", "```\nA\n```\n\n```BBB\nC\n```\n\nx", None),
    ("fence alone on its line", "```\nAAA\nBBB\n```\n\nx", None),
    ("text hard after a standalone close", "```\nAAA\n```\ntail", "absorbed into the block"),
    ("text after a shared close", "```\nAAA\nBBB```\ntail", None),
    # A close carrying trailing content is not standalone.
    ("a close carrying trailing content", "```\nAAA\n```tail\nmore", None),
    # A shared close must not invert parity for the rest of the message.
    ("a shared close does not invert parity", "```\nA\nB```\n\n```\nC\n```\ntail", "line 8"),
    # Two fences on one line open and close at once and leave parity untouched.
    ("a one-line block does not invert parity", "```one```\n\n```\nA\n```\ntail", "line 6"),
    # Trailing spaces after a close are invisible; it still counts as standalone.
    ("a close with trailing spaces is standalone", "```\nAAA\n```   \ntail", "line 4"),
    ("blank line after closing fence", "```\nAAA\n```\n\ntail", None),
    # Markdown inside a block is data; outside a block it still fires.
    ("markdown inside a block is data", "```\n- row\n## col\n**lit**\n```\n\nEnd.", None),
    ("markdown outside a block still fires",
     "```\n- row\n```\n\n## Real heading", "no headings"),
    # An unclosed fence puts the tail inside the block; the stray fence is the finding.
    ("unclosed fence suppresses the tail, not itself",
     "text\n\n```\n- inside\n**inside**", "odd fence count"),
    ("markdown bold", "this is **bold** here", "asterisks literally"),
    ("slack bold", "this is *bold* here", None),
    ("markdown strike", "this is ~~gone~~ here", "tildes literally"),
    ("markdown link", "see [the board](http://x) now", "<url|label>"),
    ("slack link", "see <http://x|the board> now", None),
    ("markdown heading", "## Heading\ntext", "no headings"),
    ("markdown bullet", "- item one\n- item two", "bullets are a"),
    ("slack bullet", "• item one\n• item two", None),
]


def selftest() -> int:
    bad = 0
    width = max(len(n) for n, _, _ in CASES)
    for name, text, needle in CASES:
        found = lint(text)
        if needle is None:
            ok = not found
            detail = "" if ok else f"   <- fired on clean text: {found[0][:70]}"
        else:
            ok = any(needle in f for f in found)
            detail = "" if ok else f"   <- expected {needle!r}, got {found or '<silent>'}"
        print(f"  {'PASS' if ok else 'FAIL'}  {name.ljust(width)}{detail}")
        bad += not ok
    print(f"\n{len(CASES) - bad} passed, {bad} failed")
    return 1 if bad else 0


def read_message(argv: list[str]) -> str:
    """The draft to lint: a named file, or stdin when no file is named."""
    paths = [a for a in argv if not a.startswith("-")]
    if not paths:
        return sys.stdin.read()
    path = pathlib.Path(paths[0])
    if not path.exists():
        raise SystemExit(f"slack_lint: no such draft: {path}")
    return path.read_text(errors="replace")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(selftest())
    findings = lint(read_message(sys.argv[1:]))
    for f in findings:
        print(f"  {f}")
    print(f"\n{len(findings)} finding(s)")
    sys.exit(1 if findings else 0)
