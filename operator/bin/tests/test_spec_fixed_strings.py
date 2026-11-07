"""The layer that lets a no-verbatim spec still represent a law firm.

Ordered by which failure is worst:

1. The exemption becoming a BYPASS. If an unapproved corpus sentence passes
   once approvals are loaded, the whole guarantee is void and nothing else here
   matters.
2. The detector self-approving. Proposal and permission are different powers
   and the split is the safety property.
3. Missing the thing it was written to find. The first cut of this module found
   ZERO candidates on a corpus whose signature close recurs in four documents,
   because sentence granularity cut the two-sentence close in half.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

from bin.tests.guard_needs import require_path

_BIN = Path(__file__).resolve().parents[1]
_SEED = _BIN.parents[1] / "operator" / "customers" / "pilot-smokeball" / "seed" / "voice"


@pytest.fixture
def seed_corpus() -> None:
    """The committed rehearsal corpus these checks run against; absent fails, never skips."""
    require_path(_SEED)


def _load(name: str, mod: str):
    spec = importlib.util.spec_from_file_location(name, _BIN / mod)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


fs = _load("_fs", "spec_fixed_strings.py")
slc = _load("_slc_fx", "spec_leak_check.py")

CORPUS = {
    "a": "We would rather resolve this. We are prepared not to.\n\n**What happened**\n\nYour driver ran a red light on Willow Street and hit Marisol Duarte squarely.",
    "b": "We would rather resolve this. We are prepared not to.\n\n**What happened**\n\nA bottle broke in aisle seven.",
    "c": "We would rather resolve this. We are prepared not to.\n\n**What happened**\n\nThe dog had bitten before.",
}


# ------------------------------------------------ the exemption is not a bypass


def test_an_unapproved_corpus_sentence_is_still_refused_with_approvals_loaded():
    """THE test. If this goes green the guarantee is gone and nothing else matters."""
    approved = ["We would rather resolve this. We are prepared not to."]
    spec = (
        "Close with: We would rather resolve this. We are prepared not to.\n\n"
        "Your driver ran a red light on Willow Street and hit Marisol Duarte squarely."
    )
    assert not slc.check(spec, CORPUS, approved=approved).clean


def test_an_approved_string_passes_where_it_would_otherwise_refuse():
    spec = "Close adjuster letters with: We would rather resolve this. We are prepared not to."
    assert not slc.check(spec, CORPUS).clean
    assert slc.check(spec, CORPUS, approved=["We would rather resolve this. We are prepared not to."]).clean


def test_the_exemption_budget_is_recorded():
    """An attestation that names its budget is checkable; one that does not is a promise."""
    spec = "Close with: We would rather resolve this. We are prepared not to."
    r = slc.check(spec, CORPUS, approved=["We would rather resolve this. We are prepared not to."])
    assert r.approved_used == 1
    assert r.approved_tokens > 0
    assert "approved_used" in r.to_json()


def test_approval_does_not_exempt_an_identifier():
    """One approval must not carry two permissions.

    The firm saying "keep our closing line" says nothing about whether someone
    approved a string with a claimant's name in it, so the identifier scan runs
    on the ORIGINAL spec rather than the masked one.
    """
    spec = "Standard close: We would rather resolve this. We are prepared not to. Also Duarte."
    r = slc.check(
        spec,
        CORPUS,
        proper_nouns=["Duarte"],
        approved=["We would rather resolve this. We are prepared not to. Also Duarte."],
    )
    assert not r.clean
    assert any(f.kind == "identifier" for f in r.findings)


# ------------------------------------------------- proposal is not permission


def test_the_detector_approves_nothing():
    found, _ = fs.candidates(CORPUS)
    assert found, "expected candidates"
    # There is no field, method, or side effect by which detection grants
    # permission. The approved list is loaded from a file this module only reads.
    assert not hasattr(found[0], "approved")
    assert fs.approved_strings(None) == []


# ------------------------------------------- finding what it was written for


def test_the_two_sentence_close_is_found_as_one_block():
    """Sentence granularity halved this and found nothing. Boilerplate is a block."""
    found, _ = fs.candidates(CORPUS)
    blocks = [c for c in found if c.category == "block"]
    assert any("prepared not to" in c.text for c in blocks)


def test_a_section_label_is_found_despite_being_two_tokens():
    """The category a single length floor excludes by an order of magnitude."""
    found, _ = fs.candidates(CORPUS)
    assert any(c.category == "label" and "What happened" in c.text for c in found)


def test_a_short_prose_line_is_not_a_label():
    """The firm's most distinctive VOICE move must never be frozen as a string.

    A bare pivot sentence is short and recurs in shape, not in text — freezing
    one would convert a construction the drafter should re-derive into a literal
    it pastes. Only heading-SHAPED lines qualify.
    """
    corpus = {k: f"He cannot kneel.\n\n{v}" for k, v in CORPUS.items()}
    found, _ = fs.candidates(corpus)
    assert not any(c.category == "label" and "kneel" in c.text for c in found)


# ------------------------------------------------------------- disqualifiers


@pytest.mark.parametrize(
    "text,reason",
    [
        ("The claim number is ABC1234567 in every letter here", "carries a digit"),
        ("Our standard demand is $50,000 for these matters", "carries a currency figure"),
        ("This letter follows our January practice for all clients", "carries a date"),
        ("We represent Ms. Duarte in this matter as always", "carries a proper noun"),
    ],
)
def test_matter_content_is_dropped_not_proposed(text, reason):
    corpus = {k: text for k in ("a", "b", "c")}
    found, dropped = fs.candidates(corpus)
    assert not found
    assert any(r == reason for _, r in dropped)


def test_a_dropped_candidate_never_echoes_its_text():
    """Same rule as the leak report: describe the shape, never the span."""
    corpus = {k: "We represent Ms. Duarte in this matter as always" for k in ("a", "b", "c")}
    _, dropped = fs.candidates(corpus)
    assert dropped
    for shape, _ in dropped:
        assert "Duarte" not in shape


# ------------------------------------------------------------- real corpus


@pytest.mark.usefixtures("seed_corpus")
def test_the_real_corpus_yields_the_signature_close_and_its_headers():
    corpus = {p.name: p.read_text() for p in sorted(_SEED.glob("*.md")) if p.name[0].isdigit()}
    found, _ = fs.candidates(corpus)
    blocks = [c for c in found if c.category == "block"]
    labels = [c for c in found if c.category == "label"]
    assert len(blocks) == 1 and blocks[0].doc_count == 4
    assert {"**What happened**", "**The injuries**", "**The demand**"} <= {c.text for c in labels}


@pytest.mark.usefixtures("seed_corpus")
def test_the_signature_blocks_are_dropped_not_proposed():
    """They recur in four documents and are exactly what must not be proposed."""
    corpus = {p.name: p.read_text() for p in sorted(_SEED.glob("*.md")) if p.name[0].isdigit()}
    _, dropped = fs.candidates(corpus)
    assert len(dropped) >= 2
