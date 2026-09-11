"""The half of a voice spec that must be computed rather than asserted.

Ordered by which failure ships a FALSE RULE rather than a broken build, because
a false rule is invisible: it reads exactly like a true one and a drafter obeys
it.

1. Frontmatter contamination. A fixture's own metadata describing the fixture,
   counted as the firm's prose, inverts a hyphenation rule.
2. Possessive vs contraction. `\\w+'s` matches "your driver's door". Measured on
   the real corpus the naive pattern reports 71 and the precise one reports 1.
3. The digit invariant. Without it an agent types a number into prose and it
   reads exactly like a computed one, which is the whole defect.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_BIN = Path(__file__).resolve().parents[1]
_SEED = _BIN.parents[1] / "operator" / "customers" / "pilot-smokeball" / "seed" / "voice"


def _load():
    spec = importlib.util.spec_from_file_location("_vp", _BIN / "voice_profile.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["_vp"] = module
    spec.loader.exec_module(module)
    return module


vp = _load()


# ------------------------------------------------------- zone segmentation


def test_frontmatter_is_not_the_firms_prose():
    """The documented contamination case, in miniature.

    The note field describes the FIXTURE using hyphenated compounds. Counting it
    as authored prose makes a firm that avoids them look like one that uses them.
    """
    doc = (
        "---\n"
        "sample_id: 12\n"
        "note: Authored as a voice-derivation sample to reach the five-sample floor.\n"
        "---\n\n"
        "Their own logs put him at nineteen hours in a twenty-four hour window.\n"
    )
    prose, zones = vp.segment(doc, "12")
    assert "voice-derivation" not in prose
    assert "five-sample" not in prose
    assert "twenty-four" in prose  # the real body hit survives
    assert zones.discarded["frontmatter"] > 0


def test_institutional_form_is_discarded_and_reported():
    doc = (
        "**BRANNOCK & FERREIRA LLP**\n"
        "3400 E. Broadway, Suite 610\n"
        "Long Beach, California 90803\n"
        "(562) 555-0170\n\n"
        "July 14, 2026\n\n"
        "RE: Duarte v. Brennan\n\n"
        "Dear Reyna,\n\n"
        "Your driver ran a red light.\n\n"
        "Very truly yours,\n"
    )
    prose, zones = vp.segment(doc, "x")
    assert "Your driver ran a red light." in prose
    for gone in ("3400 E. Broadway", "555-0170", "July 14, 2026", "RE:", "Dear Reyna", "Very truly yours"):
        assert gone not in prose, f"{gone!r} survived into prose"
    # Discarding is reviewable, not silent.
    assert zones.discarded_line_numbers


def test_a_sentence_mentioning_a_date_is_not_a_date_line():
    prose, _ = vp.segment("The demand expires on July 14, 2026 and we file after that.\n", "x")
    assert "July 14, 2026" in prose


# ------------------------------------------------ the probe that was wrong


def test_a_possessive_is_not_a_contraction():
    """THE test. `\\w+'s` catches every possessive in professional prose.

    If this goes green after someone "simplifies" the pattern back to a suffix
    match, the contraction measurement inverts and nothing else here notices.
    """
    corpus = {"a": "Your driver's door absorbed the impact. The carrier's file shows it."}
    data = vp.profile(corpus)
    assert data["measurements"]["absence.contraction"]["value"] == 0


def test_a_real_contraction_is_counted():
    corpus = {"a": "It's on the record. They don't dispute it."}
    assert data_value(vp.profile(corpus), "absence.contraction") == 2


def data_value(data, key):
    return data["measurements"][key]["value"]


# ------------------------------------------------------ support and floor


def test_every_measurement_renders_with_its_support():
    """No bare numbers. Zero-across-eleven and zero-across-one differ."""
    data = vp.profile({"a": "Plain prose here.", "b": "More plain prose here."})
    for key, m in data["measurements"].items():
        assert "[n=" in m["rendered"], f"{key} rendered without support"


def test_thin_support_is_flagged_below_floor():
    data = vp.profile({"a": "One document only, so nothing here is evidence of a habit."})
    flagged = [m for m in data["measurements"].values() if m["below_floor"]]
    assert flagged, "a single-document corpus supports no rule"


def test_counterexamples_name_their_documents():
    corpus = {"clean": "Plain prose.", "dirty": "This one has a semicolon; right here."}
    m = vp.profile(corpus)["measurements"]["absence.semicolon"]
    assert m["counterexample_docs"] == ["dirty"]
    assert m["support_docs"] == 1


# ---------------------------------------------------- the digit invariant


def test_a_typed_number_in_a_card_is_refused():
    card = "# Card\n\nThe firm keeps sentences under 12 words.\n"
    assert vp.card_digit_violations(card)


def test_an_interpolated_number_is_allowed():
    card = "# Card\n\nMean sentence length is {{profile.sentence.mean_words}}.\n"
    assert not vp.card_digit_violations(card)


def test_fenced_examples_and_comments_are_exempt():
    card = (
        "<!-- provenance: derived 2026-08-01 -->\n\n"
        "# Card\n\n"
        '```json\n{"support_docs": 11}\n```\n\n'
        "Prose with no digits.\n"
    )
    assert not vp.card_digit_violations(card)


# ------------------------------------------------------- the real corpus


@pytest.mark.skipif(not _SEED.exists(), reason="rehearsal corpus not present")
def test_the_real_corpus_reproduces_the_documented_hyphen_finding():
    """Zone segmentation halves the apparent violations, as documented.

    A naive grep over 12/13 returns 4 body hits AND 4 frontmatter hits. Only the
    body hits are the firm's.
    """
    corpus = {p.name: p.read_text() for p in sorted(_SEED.glob("*.md")) if p.name[0].isdigit()}
    m = vp.profile(corpus)["measurements"]["absence.hyphenated_compound_numeral"]
    assert m["value"] == 4
    assert len(m["counterexample_docs"]) == 2


@pytest.mark.skipif(not _SEED.exists(), reason="rehearsal corpus not present")
def test_the_real_corpus_carries_exactly_one_contraction():
    """Independently reproduced: one contraction, inside quoted testimony.

    A separate bake-off arm measured the same thing without seeing this code.
    Two independent measurements agreeing is the point; the naive pattern
    reported 71 and would have inverted the rule.
    """
    corpus = {p.name: p.read_text() for p in sorted(_SEED.glob("*.md")) if p.name[0].isdigit()}
    m = vp.profile(corpus)["measurements"]["absence.contraction"]
    assert m["value"] == 1
    assert m["counterexample_docs"] == ["08-mediation-brief-facts-nakashima.md"]


@pytest.mark.skipif(not _SEED.exists(), reason="rehearsal corpus not present")
def test_the_shipped_fixture_would_fail_the_digit_invariant_today():
    """Honest: the winning bake-off card predates the invariant and carries
    typed numbers. It is installed as the VOICE fixture, which the invariant
    does not yet gate; wiring the two together is the compiler's job, and this
    test records the gap rather than letting it look closed.
    """
    card = (_SEED / "spec" / "work_product.voice.md").read_text()
    assert vp.card_digit_violations(card), (
        "the fixture now passes the digit invariant — if that is because it was "
        "regenerated through the compiler, delete this test and say so"
    )
