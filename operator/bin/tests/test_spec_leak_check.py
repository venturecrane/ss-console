"""The compiler that makes "we copied nothing" checkable (ss ADR 0083).

Ordered by what an unrelated refactor is most likely to break silently:

1. A NAME-SWAPPED copy is still a copy. This is the whole reason masking exists
   and the cheapest evasion a distiller makes under budget pressure. If this
   test goes green after someone "simplifies" normalization, the guarantee is
   gone and nothing else here notices.
2. The report never echoes the matched text. A refusal that prints the prose
   makes the audit trail the largest copy of the thing it protects.
3. A characterization passes where a quotation fails — the check must not be so
   blunt that writing ABOUT the corpus is impossible.
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
    spec = importlib.util.spec_from_file_location("_slc", _BIN / "spec_leak_check.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["_slc"] = module
    spec.loader.exec_module(module)
    return module


slc = _load()

CORPUS = {
    "01": (
        "Your driver ran a red light on Willow Street and hit Marisol Duarte in the "
        "driver's door while she was two thirds of the way through the intersection. "
        "We represent Ms. Duarte. This is her demand. She is thirty four."
    ),
    "02": (
        "Your store's own video shows a bottle of cooking oil break in aisle 7 at "
        "5:41 p.m. Thirty four minutes. That is the whole case."
    ),
}


# --------------------------------------------------------------- the evasion


def test_a_name_swapped_sentence_is_still_a_copy():
    """THE test. Swap the names and numbers; it is still their sentence.

    An exact comparison passes this. Masking is what makes it fail, and if this
    ever goes green the containment check has become decorative.
    """
    swapped = (
        "Your driver ran a red light on Marlow Street and hit Serena Okafor in the "
        "driver's door while she was two thirds of the way through the intersection."
    )
    report = slc.check(swapped, CORPUS)
    assert not report.clean
    assert any(f.kind == "containment" for f in report.findings)


def test_an_exact_copy_is_a_copy():
    report = slc.check(CORPUS["01"], CORPUS)
    assert not report.clean


def test_a_reordered_paraphrase_is_caught_by_the_second_pass():
    """Reordering breaks every 8-run; the Jaccard pass is what sees it."""
    para = (
        "Marisol Duarte was hit in the driver's door by your driver, who ran a red "
        "light on Willow Street, when she was two thirds through the intersection."
    )
    report = slc.check(para, CORPUS)
    assert not report.clean


# ------------------------------------------------------------- the leak rule


def test_the_report_never_echoes_the_matched_text(capsys, tmp_path):
    """A privacy control's audit trail must not become the largest copy.

    Note the production `_overlap_findings` DOES emit the matched run — correct
    there (the other side is the firm's own record) and wrong here.
    """
    spec = tmp_path / "spec.md"
    spec.write_text(CORPUS["01"])
    doc = tmp_path / "c.md"
    doc.write_text(CORPUS["01"])

    rc = slc.main(["--spec", str(spec), "--corpus", str(doc)])
    assert rc == 2

    out = capsys.readouterr()
    combined = out.out + out.err
    for planted in ("Marisol", "Duarte", "Willow", "red light", "intersection"):
        assert planted not in combined, f"refusal echoed {planted!r}"


def test_findings_carry_no_field_that_could_hold_text():
    """Structural, not incidental: there is nowhere for prose to live."""
    f = slc.Finding(kind="containment", corpus_doc="01", spec_offset=3, spec_line=1, tokens=9)
    assert set(vars(f)) == {"kind", "corpus_doc", "spec_offset", "spec_line", "tokens", "detail"}


# ------------------------------------------------------- writing ABOUT it


def test_a_characterization_passes():
    """The check must leave room for the thing it exists to encourage."""
    spec = (
        "Open on the operative fact the reader can verify. Representation belongs in "
        "the second or third sentence, never the first. Attribute evidence to the "
        "reader's own possession. State the claimant's age bare at the hinge between "
        "injury and damages, in its own paragraph."
    )
    report = slc.check(spec, CORPUS)
    assert report.clean, [f.detail for f in report.findings]


def test_a_short_shared_phrase_does_not_trip_it():
    report = slc.check("The firm addresses the reader in the second person.", CORPUS)
    assert report.clean


# ----------------------------------------------------------- identifiers


def test_a_paraphrase_naming_a_real_party_is_caught():
    """Containment cannot see this; the identifier scan is why it exists."""
    spec = "Injuries are described plainly before the clinical term is used."
    report = slc.check(spec + " Consider Duarte.", CORPUS, proper_nouns=["Duarte"])
    assert not report.clean
    assert any(f.kind == "identifier" for f in report.findings)


@pytest.mark.parametrize(
    "text",
    ["Call (916) 786-7787.", "Claim ABC1234567 refers.", "sk-abcdefghijklmnopqrstuvwx is here."],
)
def test_identifier_shapes_are_refused(text):
    assert not slc.check(text, CORPUS).clean


# --------------------------------------------------------- the real artifacts


@pytest.mark.usefixtures("seed_corpus")
def test_the_repos_own_production_rule_spec_leaks():
    """The empirical case for a compiler over an instruction.

    `drafting-voice-spec.md` was authored by an agent told to characterize
    rather than copy, and it embedded verbatim shapes anyway. If this ever comes
    back clean, either the file was fixed (good — say so here) or masking broke.
    """
    corpus = {p.name: p.read_text() for p in sorted(_SEED.glob("*.md")) if p.name[0].isdigit()}
    report = slc.check((_SEED / "drafting-voice-spec.md").read_text(), corpus)
    assert not report.clean
    assert any(f.kind == "containment" for f in report.findings)


@pytest.mark.usefixtures("seed_corpus")
def test_the_exemplar_profile_leaks_far_more_than_the_rule_spec():
    """The ratio should track how much each artifact actually copied."""
    corpus = {p.name: p.read_text() for p in sorted(_SEED.glob("*.md")) if p.name[0].isdigit()}
    rules = slc.check((_SEED / "drafting-voice-spec.md").read_text(), corpus)
    exemplars = slc.check((_SEED / "voice-profile.md").read_text(), corpus)
    assert len(exemplars.findings) > len(rules.findings) * 3


# ------------------------------------------------------------------ contract


def test_the_threshold_is_imported_not_restated():
    """One constant answers both directions of the same question."""
    gate = slc._GATE
    assert slc.NGRAM == gate._HELD_OUT_NGRAM == 8


def test_every_attestation_carries_the_sweep():
    report = slc.check("Open on the operative fact.", CORPUS)
    assert set(report.sweep) == set(range(4, 13))
    assert "sweep" in report.to_json()
