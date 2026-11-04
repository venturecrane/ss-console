"""A deposition quote against a transcript's text layer (2026-11-03).

A reporter's text layer carries a line number and middle-dot spacers on every
line. A correctly quoted multi-line Q&A ("Q. ...? A. ...") is contiguous once
those are read as typesetting, so the gate passes it with no repair; a repair
of a genuine near-quote rebuilds the record's words with their Q./A. labels and
never splices a line number or a spacer into the brief. Both lanes that repair
quotes (demand, drafting) call the same ``quotefix.repair``.

Every word is invented.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from medchron.demand import quotefix
from smokeball_connector.record_check import run_record_check

CHECKER = Path(__file__).resolve().parents[3] / "templates" / "drafting" / "drafting_gate_check.py"


@pytest.fixture(autouse=True)
def _checker(monkeypatch):
    monkeypatch.setenv("SMD_DRAFTING_GATE_CHECK", str(CHECKER))


DEP = "\n".join(
    [
        "Page 50",
        "14· ·Q.· · ·Where were you headed?",
        "15· ·A.· · ·Home.",
        "16· ·Q.· · ·Did you see the other car before it reached the",
        "17· ·intersection; the white one?",
        "18· ·A.· · ·No.",
        "19· ·Q.· · ·Never?",
        "20· ·Q.· · ·So alpha bravo charlie delta on echo foxtrot golf hotel",
        "21· ·india; juliet?",
        "22· ·A.· · ·Kilo lima mike november, oscar.",
        '23· ·Q.· · ·And he said "papa quebec romeo sierra tango uniform victor whiskey" to you?',
        "24· ·A.· · ·Yes, xray yankee.",
        "25· ·Q.· · ·Then what happened after",
        "Page 51",
        "1· · · ·the stop at whiskey xray?",
        "2· ·A.· · ·Yankee zulu.",
    ]
)

REPRO = "Q. So alpha bravo charlie delta on echo foxtrot golf hotel india; juliet? A. Kilo lima mike november, oscar."
TYPESET = ("·", " 21 ", " 22 ")


def _gate(md: str, src: str = DEP):
    return run_record_check(md, [("Xxxx deposition", src)], held_out_names=set(), unextractable=[], vision_sources=[])


def _md(quote: str, cite: str = "50:20-22") -> str:
    return f'He testified: "{quote}" (Xxxx Dep. {cite}.)\n'


def _quoted(md: str) -> str:
    return md.split('"', 1)[1].rsplit('"', 1)[0]


def _repair(quote: str, src: str = DEP, cite: str = "50:20-22") -> tuple[str, list[str]]:
    v = _gate(_md(quote, cite), src)
    assert not v.passed and quotefix.quote_findings(v.refusals), v.refusals
    return quotefix.repair(_md(quote, cite), v.refusals, [src])


@pytest.mark.parametrize(
    "quote, cite",
    [
        (REPRO, "50:20-22"),
        ("Q. Did you see the other car before it reached the intersection; the white one? A. No.", "50:16-18"),
        ("Q. Then what happened after the stop at whiskey xray? A. Yankee zulu.", "50:25-51:2"),  # a page break
        ("So alpha bravo charlie delta on echo foxtrot golf hotel india; juliet?", "50:20-21"),
    ],
)
def test_a_correctly_quoted_multi_line_answer_passes_with_no_repair(quote, cite):
    v = _gate(_md(quote, cite))
    assert v.passed, v.refusals


@pytest.mark.parametrize(
    "quote",
    [
        REPRO.replace("november", "december"),  # one word changed
        REPRO.replace("A. Kilo", "A. Yes. Kilo"),  # a word added
        REPRO.replace(" foxtrot", ""),  # a word dropped
    ],
)
def test_a_genuinely_altered_quote_still_refuses(quote):
    v = _gate(_md(quote))
    assert not v.passed and quotefix.quote_findings(v.refusals)


SPACE_GUTTER = "\n".join(
    [
        "Page 50",
        "20   Q.   So alpha bravo charlie delta on echo foxtrot golf hotel",
        "21        india; juliet?",
        "22   A.   Kilo lima mike november, oscar.",
    ]
)


@pytest.mark.parametrize(
    "garbled, src",
    [
        (
            "· ·So alpha bravo charlie delta on echo foxtrot golf hotel 21· ·india; juliet? "
            "22· ·A.· · ·Kilo lima mike november, oscar.",
            DEP,
        ),
        ("golf hotel 21 india; juliet? 22 A. Kilo lima mike november, oscar.", SPACE_GUTTER),
    ],
)
def test_a_quote_carrying_transcript_typesetting_refuses_though_the_raw_text_has_it(garbled, src):
    """The 2026-11-03 output passed the gate because it was verbatim in the raw
    text layer. Typesetting in a quotation is a garble, never the record's words."""
    assert garbled in " ".join(" ".join(src.split()).split())  # it IS in the raw text
    assert not _gate(_md(garbled), src).passed


def test_the_exact_failing_repro_is_repaired_to_clean_labeled_words():
    """The near-quote the 2026-11-03 brief carried: repaired from the gutter
    view, the replacement keeps its labels and its first word and has no line
    number, no spacer, and nothing of the line before."""
    out, log = _repair(REPRO.replace("november", "december"))
    assert _quoted(out) == REPRO
    assert not any(t in _quoted(out) for t in TYPESET)
    assert log and log[0].startswith("quote normalized to source")
    assert _gate(out).passed


def test_a_changed_first_word_is_substituted_never_the_prior_lines_tail():
    out, _ = _repair("Q. Then alpha bravo charlie delta on echo foxtrot golf hotel india; juliet?")
    assert _quoted(out) == "Q. So alpha bravo charlie delta on echo foxtrot golf hotel india; juliet?"
    assert "Never" not in out


def test_a_dropped_label_is_restored_from_the_record():
    out, _ = _repair(
        "So alpha bravo charlie delta on echo foxtrot golf hotel india; juliet? Kilo lima mike november, oscar."
    )
    assert (
        _quoted(out)
        == "So alpha bravo charlie delta on echo foxtrot golf hotel india; juliet? A. Kilo lima mike november, oscar."
    )
    assert _gate(out).passed


def test_a_nested_quotation_in_the_record_is_kept_whole_or_not_at_all():
    """The record's own nested quotation keeps both of its marks; a mark whose
    partner the region boundary cut away does not ride into the brief."""
    quote = "And he said papa quebec romeo sierra tango uniform victor whiskey to you? A. Yes, xray yankeee."
    out, _ = _repair(quote, cite="50:23-24")
    assert (
        _quoted(out)
        == "And he said 'papa quebec romeo sierra tango uniform victor whiskey' to you? A. Yes, xray yankee."
    )
    assert _gate(out).passed
    out, _ = _repair("papa quebec romeo sierra tangoo uniform victor", cite="50:23")
    assert _quoted(out) == "papa quebec romeo sierra tango uniform victor"
    assert _gate(out).passed


def test_a_space_gutter_transcript_repairs_without_line_numbers():
    """The other observed shape: a plain two-space gutter, where the old repair
    lost the first word and label and kept "17 A." in the brief."""
    src = "\n".join(
        [
            "Page 50",
            "15   Q.   Where were you headed?",
            "16   Q.   Word alpha bravo charlie; delta?",
            "17   A.   Echo.",
            "18   Q.   Foxtrot golf hotel india?",
            "19   A.   No.",
            "20   Q.   Juliet?",
        ]
    )
    quote = "Q. Word alpha bravo charlie; delta? A. Echo. Q. Foxtrot golf hotel indigo?"
    out, _ = _repair(quote, src, cite="50:16-18")
    assert _quoted(out) == "Q. Word alpha bravo charlie; delta? A. Echo. Q. Foxtrot golf hotel india?"
    assert _gate(out, src).passed


def test_a_region_that_would_carry_typesetting_is_left_for_the_gate():
    """A gutter the view cannot read (one space after the number) leaves "17
    A." in the record's words. That is never spliced into the brief: the quote
    stays as written, the log says why, and the gate refuses, failing the job."""
    src = "\n".join(["Page 50", "16 Q. Word alpha bravo charlie; delta?", "17 A. Echo foxtrot golf.", "18 Q. Hotel?"])
    quote = "Q. Word alpha bravo charlie; delta? A. Echo foxtrot gulf."
    out, log = _repair(quote, src, cite="50:16-17")
    assert out == _md(quote, "50:16-17")
    assert log and log[-1].startswith("quote not repaired, the record's region carries transcript typesetting")
    assert not _gate(out, src).passed


#: Single-digit lines carry a LEADING spacer ("·9· ·Q."); lines 10+ do not
#: (2026-11-04). Before the gutter read it, lines 1-9 kept their numbers in the
#: view and the question on them was paired with the previous page's.
LEADING_DOT = "\n".join(
    [
        "Page 49",
        "24· ·Q.· · ·Uniform victor whiskey xray?",
        "25· ·A.· · ·Yankee.",
        "Page 50",
        "·7· ·Q.· · ·Juliet kilo?",
        "·8· ·A.· · ·Lima.",
        "·9· ·Q.· · ·And when you reached the corner of mike and",
        "10· ·november, which way did the oscar papa turn",
        "11· ·before the quebec romeo?",
        "12· ·A.· · ·Sierra tango uniform victor, toward the whiskey.",
        "13· ·Q.· · ·Xray?",
    ]
)
LD_QA = (
    "Q. And when you reached the corner of mike and november, which way did the oscar papa turn "
    "before the quebec romeo? A. Sierra tango uniform victor, toward the whiskey."
)


@pytest.mark.parametrize(
    "quote, cite",
    [
        ("Sierra tango uniform victor, toward the whiskey.", "50:9-12"),  # answer, question in range
        (LD_QA, "50:9-12"),
        ("Q. Juliet kilo? A. Lima. Q. And when you reached the corner of mike and november", "50:7-10"),
    ],
)
def test_a_question_on_a_leading_dot_line_is_read_and_paired(quote, cite):
    v = _gate(_md(quote, cite), LEADING_DOT)
    assert v.passed, v.refusals


def test_a_cite_leaving_out_a_leading_dot_question_still_refuses():
    """The pairing check can still fail on lines 1-9: the question at 50:9 is
    outside 50:10-12, and the refusal names it, not page 49's question."""
    v = _gate(_md("Sierra tango uniform victor, toward the whiskey.", "50:10-12"), LEADING_DOT)
    assert not v.passed
    assert any("50:9" in r for r in v.refusals), v.refusals
    assert not any("49:24" in r for r in v.refusals), v.refusals


def test_a_leading_dot_near_quote_repairs_without_line_numbers():
    out, _ = _repair(LD_QA.replace("quebec", "kebec"), LEADING_DOT, cite="50:9-12")
    assert _quoted(out) == LD_QA
    assert not any(t in _quoted(out) for t in ("·", " 9 ", " 10 ", " 11 ", " 12 "))
    assert _gate(out, LEADING_DOT).passed
