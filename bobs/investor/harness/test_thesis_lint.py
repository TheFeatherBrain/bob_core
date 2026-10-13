#!/usr/bin/env python3
"""thesis_lint: a thesis ships only with both cases, cited numbers and citations that resolve.

Run:  python3 harness/test_thesis_lint.py
"""
from __future__ import annotations

import pathlib
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import scoutpath  # noqa: E402,F401
import testkit  # noqa: E402
import thesis_lint  # noqa: E402

CITE = "[ev:2026-10-13/sec-frame-x]"


def _evidence() -> pathlib.Path:
    ev = pathlib.Path(tempfile.mkdtemp())
    (ev / "2026-10-13" / "sec-frame-x").mkdir(parents=True)
    (ev / "2026-10-13" / "sec-frame-x" / "meta.json").write_text("{}")
    return ev


def thesis(verdict: str = "buy-worthy", lane: str = "numbers", bull: str = f"- Revenue grew 94% {CITE}",
           bear: str = f"- Gross margin fell 2 points {CITE}", drop: str = "") -> str:
    parts = {"Bull case": bull, "Bear case": bear,
             "Reverse DCF": f"- Price implies 31% a year for 10 years {CITE}",
             "What would change my mind": f"- Two quarters under 20% growth {CITE}"}
    body = "".join(f"## {name}\n\n{text}\n\n" for name, text in parts.items() if name != drop)
    return f"# NVDA: NVIDIA\n\nVerdict: {verdict}\nLane: {lane}\nDate: 2026-10-13\n\n## Scorecard\n\nscore 91\n\n{body}"


def test_valid_thesis_passes():
    assert thesis_lint.lint(thesis(), _evidence()) == []


def test_uncited_number_refused():
    errors = thesis_lint.lint(thesis(bull=f"- Revenue grew 94%\n- Margins up {CITE}"), _evidence())
    assert errors == ["Bull case: uncited number: - Revenue grew 94%"], errors


def test_a_year_counts_as_a_number():
    assert thesis_lint.lint(thesis(bear="- In 2025 the CEO left"), _evidence())


def test_unresolved_citation_refused():
    errors = thesis_lint.lint(thesis(bear="- Debt rose 3x [ev:2026-10-13/made-up]"), _evidence())
    assert errors == ["evidence not found: 2026-10-13/made-up"], errors


def test_missing_bear_case_refused():
    assert "missing section: ## Bear case" in thesis_lint.lint(thesis(drop="Bear case"), _evidence())


def test_bull_without_a_cited_claim_refused():
    assert "Bull case: no cited claim" in thesis_lint.lint(thesis(bull="- Great company"), _evidence())


def test_placeholder_verdict_and_lane_refused():
    errors = thesis_lint.lint(thesis(verdict="buy-worthy | watch", lane="numbers | story"), _evidence())
    assert any(e.startswith("Verdict:") for e in errors) and any(e.startswith("Lane:") for e in errors)


def test_absolute_citation_inside_evidence_dir_resolves():
    ev = _evidence()
    absolute = (ev / "2026-10-13" / "sec-frame-x").resolve()
    assert thesis_lint.resolve(str(absolute), ev) == absolute
    assert thesis_lint.resolve(str(pathlib.Path(tempfile.mkdtemp())), ev) is None


def test_cli_reads_a_utf8_thesis_on_any_platform():
    bob = testkit.temp_bob()
    ev = bob / "data" / "evidence" / "2026-10-13" / "sec-frame-x"
    ev.mkdir(parents=True)
    (ev / "meta.json").write_text("{}")
    (bob / "theses" / "NVDA.md").write_text(thesis(bull=f"- Łódź plant ships 94% more {CITE}"), encoding="utf-8")
    assert thesis_lint.main(["theses/NVDA.md"]) == 0


def test_header_reads_verdict_lane_date():
    assert thesis_lint.header(thesis("watch", "story")) == {"verdict": "watch", "lane": "story", "date": "2026-10-13"}


if __name__ == "__main__":
    raise SystemExit(testkit.run(globals()))
