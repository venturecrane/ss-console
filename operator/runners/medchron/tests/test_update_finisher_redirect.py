"""A duplicate the finisher cannot drop without losing content becomes the
edit that loses nothing; an omitted separate encounter may be written as its
own entry (2026-10-14 parity runs: one matter's round 1 left 26 declines on such
encounters and 8 refused drops; another held 5 refused drops). Synthetic text."""

from __future__ import annotations

import update_testkit as K
from medchron import prompts
from medchron.update import finisher as FN, review_repair as RR

D1 = "12/08/2025\nExample Hospital | Surgery\nRight hernia repair with mesh. (Exhibit 2 - p. 4)"
RICH = "12/08/2025\nExample Hospital | Surgery\nRight hernia repair with mesh. (Exhibit 11 - p. 9)\n\nPost-op BP 132/84 and lidocaine 1% 10 mL. (Exhibit 11 - p. 10)"
THIN = "12/08/2025\nExample Hospital | Surgery\nPost-op BP 132/84. (Exhibit 11 - p. 10)"


def _drop(anchor: str, survivor: str) -> RR.Finding:
    f = RR.Finding("MUST", "4 duplicate", anchor, 11, 9, "x", "drop_entry", "", "")
    f.survivor, f.fid = survivor, "F1"
    return f


def test_a_drop_onto_a_delivered_visit_that_lacks_content_becomes_a_fold_of_only_that_content():
    e = _drop("N1", "D1")
    FN.precheck([e], [RR.Entry("N1", RICH)], [D1], None, keep={11}, headings=set())
    assert e.action == "append_to_delivered" and e.survivor == "D1" and e.outcome == ""
    assert "132/84" in e.corrected and "hernia repair" not in e.corrected


def test_a_drop_onto_a_poorer_added_entry_keeps_the_richer_one():
    e = _drop("N1", "N2")
    FN.precheck([e], [RR.Entry("N1", RICH), RR.Entry("N2", THIN)], [], None, keep={11}, headings=set())
    assert (e.action, e.anchor, e.survivor, e.outcome) == ("drop_entry", "N2", "N1", "")


def test_a_drop_whose_survivor_has_its_own_content_is_still_refused():
    other = "12/08/2025\nExample Hospital | Surgery\nTemperature 99.1 F. (Exhibit 11 - p. 11)"
    e = _drop("N1", "N2")
    FN.precheck([e], [RR.Entry("N1", THIN), RR.Entry("N2", other)], [], None, keep={11}, headings=set())
    assert e.action == "drop_entry" and e.anchor == "N1" and "does not carry" in e.outcome


def test_a_time_stamp_or_a_bare_year_does_not_block_a_duplicate_drop_but_a_date_or_dose_does():
    kept = "12/08/2025\nExample Hospital | Portal message\nPatient reports BP 127/81 at home. (Exhibit 11 - p. 70)"
    stamped = kept.replace("at home.", "at home, sent 10:32 in 2026.")
    e = _drop("N1", "N2")
    FN.precheck([e], [RR.Entry("N1", stamped), RR.Entry("N2", kept)], [], None, keep={11}, headings=set())
    assert e.outcome == "" and e.anchor == "N1"
    dosed = kept.replace("at home.", "at home; lisinopril 10 mg on 12/09/2025.")
    e = _drop("N1", "N2")
    FN.precheck([e], [RR.Entry("N1", dosed), RR.Entry("N2", kept)], [], None, keep={11}, headings=set())
    assert (e.anchor, e.survivor, e.outcome) == ("N2", "N1", "")  # the dose keeps N1; the poorer copy goes


def test_the_finisher_is_told_to_write_an_omitted_separate_encounter_as_its_own_entry():
    text = prompts.load("update-finish-system", K.cfg())
    assert "NO entry records" in text and "Never `decline` an omitted separate encounter" in text


def test_a_drop_into_an_entry_of_another_date_is_refused_and_never_redirected():
    """A heading dated by a pathology order's signature date was dropped into
    another visit; the body comparison passed and the date was lost (proof 10
    failed on a real run). The survivor's date line must state the same date."""
    signed = "12/09/2025 (order signature date)\nExample Hospital | Surgery\nPost-op BP 132/84. (Exhibit 11 - p. 10)"
    e = _drop("N1", "N2")
    FN.precheck([e], [RR.Entry("N1", signed), RR.Entry("N2", RICH)], [], None, keep={11}, headings=set())
    assert (e.anchor, e.survivor) == ("N1", "N2") and "dated 12/09/2025" in e.outcome
    e = _drop("N1", "D1")
    FN.precheck([e], [RR.Entry("N1", signed)], [D1], None, keep={11}, headings=set())
    assert e.action == "drop_entry" and "dated 12/09/2025" in e.outcome  # never turned into a fold
