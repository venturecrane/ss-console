"""Running a spec's rules against the writing they were derived from.

The failure this prevents is specific and was observed: a candidate card stated
a threshold as a hard gate, called it "the load-bearing number", and three of
the firm's own letters violated it. Nothing anywhere would have noticed, because
nothing ran the rules against the corpus.

Ordered by which failure ships a bad gate rather than a broken build:

1. An unknown rule kind must DEMOTE, never silently pass. A rule this module
   cannot evaluate that reads as evaluated-and-approved is the exact shape of
   every false-confidence defect in this subsystem.
2. A block rule the corpus violates must demote AND name the documents, because
   the documents are the information the firm needs to resolve it.
3. Unlabeled corpora gate on everything. The permissive reading — only labeled
   documents gate, so an unlabeled corpus gates on nothing — would turn the
   whole check off by omission.
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


def _load():
    spec = importlib.util.spec_from_file_location("_sst", _BIN / "spec_selftest.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["_sst"] = module
    spec.loader.exec_module(module)
    return module


st = _load()

CORPUS = {
    "a": "Plain prose. No punctuation tricks here.",
    "b": "This one has a semicolon; right here in the body.",
}


def _rule(**over):
    base = {"id": "S2", "kind": "absence", "tier": "block", "pattern": ";"}
    base.update(over)
    return base


# --------------------------------------------------- silent-pass is the enemy


def test_an_unknown_rule_kind_demotes_rather_than_passing():
    """A rule nobody evaluated must not read as one that held."""
    report = st.selftest([_rule(kind="vibes", tier="block")], CORPUS)
    r = report["results"][0]
    assert r["tier_effective"] == st.WARN
    assert "not evaluated" in r["detail"]


# ------------------------------------------------------------ the mechanism


def test_a_rule_the_corpus_violates_demotes_and_names_the_document():
    report = st.selftest([_rule()], CORPUS)
    r = report["results"][0]
    assert r["demoted"]
    assert r["tier_effective"] == st.WARN
    assert r["failed_exemplary_docs"] == ["b"]


def test_a_rule_the_corpus_honors_stays_block():
    report = st.selftest([_rule(id="S1", pattern="—")], CORPUS)
    assert report["results"][0]["tier_effective"] == st.BLOCK
    assert report["rules_demoted"] == 0


def test_one_violating_document_is_enough():
    """100%, not 90%. At a dozen documents a percentage tolerates exactly the
    falsifying one, and the falsifying one carries the information."""
    corpus = {f"d{i}": "Plain prose." for i in range(20)}
    corpus["d20"] = "One semicolon; here."
    assert st.selftest([_rule()], corpus)["rules_demoted"] == 1


def test_a_warn_rule_is_not_demoted_further():
    report = st.selftest([_rule(tier="warn")], CORPUS)
    assert report["results"][0]["tier_effective"] == st.WARN
    assert not report["results"][0]["demoted"]


# ----------------------------------------------------------------- labelling


def test_an_unlabelled_corpus_gates_on_every_document():
    """The stricter reading, and the safe default.

    The permissive one — only labelled documents gate — turns the check off for
    any corpus nobody has labelled yet, which is every corpus at the start.
    """
    assert st.selftest([_rule()], CORPUS, exemplary=None)["rules_demoted"] == 1


def test_labels_restrict_which_documents_gate():
    """A document the firm did not call exemplary is reported, not gating."""
    report = st.selftest([_rule()], CORPUS, exemplary={"a"})
    r = report["results"][0]
    assert not r["demoted"]
    assert r["failed_docs"] == ["b"]  # still reported
    assert r["failed_exemplary_docs"] == []


# --------------------------------------------------------- customer-facing


def test_demotions_render_for_the_customer():
    """ADR 0083 requires approval; this is what makes approval informed."""
    text = st.render_demotions(st.selftest([_rule()], CORPUS))
    assert "S2" in text and "b" in text
    assert "your own writing" in text


# ------------------------------------------------------------ real corpus


@pytest.mark.usefixtures("seed_corpus")
def test_the_hyphen_rule_demotes_on_the_two_late_documents():
    """Reproduces the documented finding, from the rules side.

    Documents 12 and 13 were added after the original eleven and are the only
    two that break the open-numeral habit. Whether that is newer house style or
    associate drift is not knowable from the corpus, which is exactly why the
    rule demotes to warn and the documents get named rather than the rule being
    dropped or kept.
    """
    corpus = {p.name: p.read_text() for p in sorted(_SEED.glob("*.md")) if p.name[0].isdigit()}
    rule = _rule(
        id="S6",
        ignore_case=True,
        pattern=r"\b(?:twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety)-(?:one|two|three|four|five|six|seven|eight|nine)\b",
    )
    r = st.selftest([rule], corpus)["results"][0]
    assert r["demoted"]
    assert r["failed_exemplary_docs"] == [
        "12-client-status-duarte.md",
        "13-client-status-tolliver.md",
    ]


@pytest.mark.usefixtures("seed_corpus")
def test_a_threshold_rules_verdict_depends_on_the_tokenizer():
    """WHY THE TOKENIZER SHIPS WITH THE CARD, demonstrated rather than argued.

    The bake-off's splitter put three documents under a 15% short-sentence
    threshold, at 12.8 / 12.1 / 10.3. This module's splitter puts every document
    over it, the lowest at 15.9. Neither is wrong; they segment sentences
    differently, and roughly a factor of two separates them on the same files.

    So the SAME rule blocks under one tokenizer and passes under another. A
    threshold without the code that computed it is not a gate, it is a number
    someone remembers. This test pins the local verdict so a change to the
    splitter shows up here as a rule silently flipping, which is the only way
    anyone would notice.
    """
    corpus = {p.name: p.read_text() for p in sorted(_SEED.glob("*.md")) if p.name[0].isdigit()}
    rule = {"id": "R3", "kind": "min_pct_short_sentences", "tier": "block", "at_most_words": 5, "threshold": 15}
    r = st.selftest([rule], corpus)["results"][0]
    assert not r["demoted"], "the splitter changed; re-derive every threshold rule"
    assert len(r["passed_docs"]) == 13
