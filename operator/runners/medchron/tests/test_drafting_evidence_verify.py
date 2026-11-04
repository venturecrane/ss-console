"""Evidence verify: code checks every proposed fact against the page it names.

Synthetic record only (``evidence_testkit``)."""

from __future__ import annotations

import random
from pathlib import Path

from evidence_testkit import BILL, CHART, DEPO, candidates, corpus

from medchron.drafting.evidence import verify
from medchron.drafting.evidence.shapes import Candidate, DocPages, Ledger


def _by_claim(ledger: Ledger) -> dict[str, object]:
    return {f.claim: f for f in ledger}


def test_an_excerpt_on_the_wrong_page_is_relocated_and_one_nowhere_in_the_file_is_rejected():
    led = _by_claim(verify.verify(candidates(), corpus()))
    swollen = led["Knee still swollen"]
    assert swollen.status == "relocated" and swollen.page == 2  # the model cited p. 1
    invented = led["No prior knee injury"]
    assert invented.status == "rejected" and not invented.usable


def test_today_the_gate_passes_a_quote_with_the_wrong_pin_and_verify_does_not():
    """The gate tests that a quote exists somewhere; it never tests the page
    (taxonomy: 10 of 23 wrong items were pins). Verify moves the pin."""
    chk = verify.checker()
    text = "\n".join(f"[p.{p}]\n{t}" for p, t in corpus()[CHART].pages.items())
    draft = chk.Draft(
        Path("d.md"),
        f'The chart says "the patient was doing well, but her knee remained swollen" (FILE: {CHART}, p. 1).',
    )
    findings, hits = chk.gate_quote_contiguity(draft, [chk.SourceDoc(Path(CHART), text)])
    assert findings == [] and len(hits) == 1  # today: passes with p. 1
    led = _by_claim(verify.verify(candidates(), corpus()))
    assert led["Knee still swollen"].page == 2


def test_the_excerpt_is_the_pages_characters_not_the_models():
    """The model typed a semicolon; the page prints a comma (taxonomy item 16)."""
    f = _by_claim(verify.verify(candidates(), corpus()))["Knee still swollen"]
    assert "doing well, but her knee" in f.excerpt and ";" not in f.excerpt
    assert "taken from the page" in f.note


def test_the_excerpt_widens_to_the_sentence_so_its_qualifier_comes_with_it():
    corp = {CHART: corpus()[CHART]}
    c = Candidate(CHART, 2, "fact", "the patient was doing well", "Doing well")
    [f] = list(verify.verify([c], corp))
    assert f.excerpt == "Assessment: the patient was doing well, but her knee remained swollen after therapy."


def test_a_split_cents_box_reads_at_its_printed_amount_never_100x():
    f = _by_claim(verify.verify(candidates(), corpus()))["Surgery charges"]
    assert (f.kind, f.value, f.unit, f.role) == ("figure", "8300.00", "usd", "billed")


def test_a_figure_or_date_the_excerpt_does_not_print_is_not_a_figure():
    corp = {BILL: corpus()[BILL]}
    cs = [
        Candidate(BILL, 2, "figure", "Total charges 8300 00", "Charges", role="billed", value="$9,999.00"),
        Candidate(BILL, 2, "date", "Date of service 02/01/2026", "DOS", value="02/02/2026"),
    ]
    got = {f.claim: f for f in verify.verify(cs, corp)}
    assert got["Charges"].kind == "fact" and got["Charges"].value is None
    assert got["DOS"].kind == "fact" and got["DOS"].value is None


def test_dates_parse_numeric_and_month_name_forms():
    assert verify.parse_date("02/01/2026").isoformat() == "2026-02-01"
    assert verify.parse_date("February 1, 2026").isoformat() == "2026-02-01"
    assert verify.parse_date("Feb. 1, 2026").isoformat() == "2026-02-01"
    assert verify.parse_date("sometime in spring") is None


def test_testimony_page_and_line_are_located_by_the_gate_checkers_parser():
    f = _by_claim(verify.verify(candidates(), corpus()))["Neck stiff that night"]
    assert (f.kind, f.tpage, f.lines) == ("testimony", 12, (4, 5))
    assert f.excerpt.startswith("Not right away") and "A." not in f.excerpt and " 5 " not in f.excerpt


def test_testimony_the_model_pinned_wrong_is_corrected_and_unlocatable_testimony_is_cited_by_file_page():
    corp = corpus()
    c = Candidate(DEPO, 1, "testimony", "To work, at the bakery on Main.", "Going to work", tpage=40, lines=(1, 1))
    [f] = list(verify.verify([c], {DEPO: corp[DEPO]}))
    assert (f.tpage, f.lines) == (12, (2, 2)) and "located at 12:2" in f.note
    flat = DocPages("Notes.pdf", {1: "She said the stairs were wet and dark that morning."})
    c2 = Candidate("Notes.pdf", 1, "testimony", "the stairs were wet and dark", "Wet stairs", tpage=3, lines=(4, 5))
    [g] = list(verify.verify([c2], {"Notes.pdf": flat}))
    assert g.kind == "quote" and g.tpage is None and g.lines is None


def test_ids_are_stable_whatever_order_the_model_answered_in():
    cs = candidates()
    a = verify.verify(cs, corpus())
    random.Random(7).shuffle(cs)
    b = verify.verify(cs, corpus())
    assert [(f.id, f.claim) for f in a] == [(f.id, f.claim) for f in b]
    assert all(f.id.startswith("F") for f in a) and a.rejected()[0].id == f"F{len(a):04d}"


def test_duplicates_collapse_and_the_ledger_round_trips():
    cs = candidates() + candidates()[:3]
    led = verify.verify(cs, corpus())
    assert len(led) == len(candidates())
    assert Ledger.from_jsonl(led.to_jsonl()) == led
    s = verify.stats(led)
    assert s["rejected"] == 1 and s["relocated"] == 1


def test_a_short_excerpt_is_never_relocated():
    corp = {CHART: corpus()[CHART]}
    [f] = list(verify.verify([Candidate(CHART, 1, "fact", "knee remained", "Knee")], corp))
    assert f.status == "rejected"


def test_an_unknown_file_is_rejected_and_a_file_named_by_its_stem_resolves():
    corp = corpus()
    got = list(verify.verify([Candidate("No such file.pdf", 1, "fact", "anything at all here", "x")], corp))
    assert got[0].status == "rejected"
    got = list(
        verify.verify(
            [Candidate("Exampletown Clinic chart", 1, "date", "Visit of 01/20/2026", "v", value="01/20/2026")], corp
        )
    )
    assert got[0].file == CHART and got[0].status == "ok"
