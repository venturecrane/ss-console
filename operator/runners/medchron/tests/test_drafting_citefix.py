"""Every final-pass marker is balanced for the gate; refused deposition
citations are moved to their question (drafting/citefix.py). Fictional."""

from __future__ import annotations

import importlib.util
from pathlib import Path

from medchron.drafting import citefix, settle

from test_drafting_settle import DRAFT, IX, VIII, XI, _audit


def _gate_check():
    p = Path(__file__).resolve().parents[3] / "templates" / "drafting" / "drafting_gate_check.py"
    spec = importlib.util.spec_from_file_location("dgc", p)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_every_marker_kind_is_balanced_for_the_gate():
    au = _audit(
        {
            VIII: [
                "- Umbrella $3 Million disclosed by each defendant | INVENTED | no cite in digest",
                "- Defendant has stated no reservation of rights | INVENTED | no cite",
            ],
            IX: ["- Umbrella limits of $3 Million disclosed in verified discovery | SUPPORTED | Form Interrog. 4.1"],
            XI: ["- looks forward to the mediator's assistance | INVENTED | no cite"],
        }
    )
    md, _ = settle.settle(DRAFT, au)
    assert "disagrees with itself" in md and "removed by the final audit" in md and "could not place" in md
    _spans, unclosed = _gate_check().marker_spans(md)
    assert unclosed == []


def test_a_citation_that_skips_its_question_is_moved_to_it():
    doc = 'Of recreation he said: "I don\'t run anymore." (Example Dep. 66:14-19.) Next.'
    r = [
        '[2b] cited range 66:14 to 66:19 excludes the question this answer answered, at 66:13: "I don\'t run anymore." x'
    ]
    out, log = citefix.repair(doc, r)
    assert "(Example Dep. 66:13-19.)" in out and log


def test_a_question_on_the_previous_page_spans_both_pages():
    doc = 'He said "I can no longer lift my son" (Dep. 66:1-5).'
    r = [
        '[2b] cited range 66:1 to 66:5 excludes the question this answer answered, at 65:25: "I can no longer lift my son"; '
    ]
    out, _ = citefix.repair(doc, r)
    assert "(Dep. 65:25-66:5)" in out


def test_a_citation_the_repair_cannot_find_is_left_alone():
    doc = "No quote here (Dep. 12:1-4)."
    r = ['[2b] cited range 66:14 to 66:19 excludes the question this answer answered, at 66:13: "absent words here" ']
    assert citefix.repair(doc, r) == (doc, [])
