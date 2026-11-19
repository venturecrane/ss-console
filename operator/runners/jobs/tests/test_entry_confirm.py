"""What an entry-read finding would write, the second reader's verify of it,
and the code that decides (``medchron/update/entry_confirm.py``, round 4):
a finding becomes the WHOLE labelled line it would write (or a new
paragraph), the verify is shown the line, the proposed sentence and the
pages as pictures, and code applies only a line found wrong with a proposed
sentence found supported on a page the verify was shown. Every value
invented."""

from __future__ import annotations

import json
from dataclasses import replace
from typing import Any


from jobs.medchron.update import entry_confirm as EC, entry_read as ER
from test_entry_read import E1, E2, HEADINGS, _img, doc, on_line, reply, units

KEEP = {1, 2, 3}
YES = EC.Verify(True, True, 1, 4, "the page records 78")


def found(u: ER.Unit, **kw: Any) -> ER.ReadFinding:
    got = ER.parse(reply(on_line(u, **kw)), u)
    assert got.findings, got.dropped
    return got.findings[0]


def proposal(u: ER.Unit, **kw: Any) -> EC.Proposal:
    p = EC.propose(found(u, **kw), u)
    assert isinstance(p, EC.Proposal), p
    return p


# ---- C: the whole labelled line ------------------------------------------------------
def test_a_value_finding_proposes_the_whole_line_with_its_citation_byte_for_byte() -> None:
    n1 = units()[1]
    p = proposal(n1)
    assert p.edit.kind == "swap" and p.edit.line == n1.lines["S1"]
    assert p.edit.new == "Blood pressure was 128/84 and pulse was 78 at triage. (Exhibit 1 - p. 4)"


def test_a_quote_not_once_in_its_line_is_recorded() -> None:
    n1 = units()[1]
    f = found(n1, quote="a")  # "a" occurs more than once in S1
    assert EC.propose(f, n1) == EC.QUOTE_NOT_ONCE
    f2 = found(n1, quote="pulse was 72", replacement="pulse was 78 (Exhibit 1 - p. 3)")  # carries a citation
    assert EC.propose(f2, n1) == EC.QUOTE_NOT_ONCE


def test_a_pointer_swaps_the_lines_one_citation_and_a_multi_page_one_is_recorded() -> None:
    n1 = units()[1]
    f = found(n1, **{"class": "pointer", "page": 3, "quote": "pulse was 72", "replacement": "(Exhibit 1 - p. 3)"})
    assert EC.propose(f, n1).edit.new.endswith("at triage. (Exhibit 1 - p. 3)")
    multi = E1.replace("(Exhibit 1 - p. 4)", "(Exhibit 1 - p. 3-4)")
    m1 = units(doc([multi, E2]))[1]
    fm = found(m1, **{"class": "pointer", "page": 5, "quote": "pulse was 72"})
    assert EC.propose(fm, m1) == EC.POINTER_MULTI


def test_record_classes_and_the_prior_history_are_recorded_without_a_verify() -> None:
    us = units()
    for klass, quote in (("date", "01/05/2025"), ("provider", "Example Clinic"), ("another_person", "pulse")):
        assert EC.propose(found(us[1], **{"class": klass, "quote": quote}), us[1]) == EC.RECORD_ONLY_CLASS
    pre = found(us[0], page=1, quote="knee sprain", replacement="ankle sprain")
    assert EC.propose(pre, us[0]) == EC.PRIOR_HISTORY


# ---- D: adds ------------------------------------------------------------------------------
ADD = "Lumbar flexion was limited to 40 degrees. (Exhibit 1 - p. 4)"


def _om(u: ER.Unit, **kw: Any) -> ER.ReadFinding:
    return found(u, **{"class": "omitted", "action": "add", "replacement": ADD, **kw})


def test_an_add_proposes_a_new_paragraph_after_its_line() -> None:
    n1 = units()[1]
    p = EC.propose(_om(n1), n1)
    assert p.edit.kind == "add" and p.edit.new == ADD and p.edit.line == n1.lines["S1"]


def test_an_add_on_the_header_with_a_code_or_on_an_uncited_page_is_recorded() -> None:
    n1 = units()[1]
    assert EC.propose(_om(n1, line="D", quote="01/05/2025"), n1) == EC.ADD_ON_HEADER
    code = _om(n1, replacement="Lumbar strain M54.5 was assessed. (Exhibit 1 - p. 4)")
    assert EC.propose(code, n1) == EC.CODES_IN_INDEX
    uncited = _om(n1, page=3, replacement="Lumbar flexion was limited. (Exhibit 1 - p. 3)")
    assert EC.propose(uncited, n1) == EC.ADD_CITATION
    assert not EC.CODE.search("Temperature was 100.4 at triage.") and not EC.CODE.search("Fracture at T12 seen.")


# ---- F: a one-page sub-read cannot judge a line citing more ------------------------------
def test_a_one_page_read_of_a_line_citing_more_pages_is_partial_view() -> None:
    multi = E1.replace("(Exhibit 1 - p. 4)", "(Exhibit 1 - p. 3-4)")
    n1 = units(doc([multi, E2]))[1]
    sub = replace(n1, pages=[(1, 4)], cited=[(1, 4)], sub=True)
    assert EC.propose(found(sub), sub) == EC.PARTIAL_VIEW
    assert isinstance(EC.propose(found(n1), n1), EC.Proposal)


# ---- A: the verify request -------------------------------------------------------------
def test_the_verify_shows_the_line_the_proposed_sentence_and_the_pages_as_pictures(firm_config_path) -> None:
    from jobs.medchron import config as config_mod

    n1 = units()[1]
    p = proposal(n1)
    system, content = EC.verify_request(p, config_mod.load(str(firm_config_path)), "CLIENT", _img)
    asked = " ".join(b.get("text", "") for b in content)
    assert "ENTRY VERIFY" in system and "{{" not in system
    assert n1.lines["S1"] in asked and p.edit.new in asked and "the page records 78" in asked
    assert sum(1 for b in content if b.get("type") == "image") == len(EC.verify_pages(p)) == 3
    assert "PAGE TEXT" not in asked  # never page text: the second reader looks at the page itself


def test_the_verify_shows_at_most_eight_pages_nearest_the_finding() -> None:
    sents = " ".join(f"Finding {i} was recorded. (Exhibit 3 - p. {i})" for i in range(2, 13))
    md = doc([f"03/01/2025\nExample Clinic | All Other Information\n\nAll Other Information\n{sents}"], pre="")
    u = ER.plan(md, KEEP, HEADINGS, lambda ex: 50)[0]
    p = proposal(u, exhibit=3, page=7, quote="Finding 7 was", replacement="Finding 8 was")
    assert EC.verify_pages(p) == [(3, 6), (3, 7), (3, 8)]


# ---- B: decide -------------------------------------------------------------------------
def test_only_a_wrong_line_with_a_supported_sentence_on_a_shown_page_applies() -> None:
    p = proposal(units()[1])
    assert EC.decide(p, YES) == ("apply", p.edit)
    assert EC.decide(p, EC.Verify(False, True, 1, 4)) == ("record", EC.NOT_WRONG)
    assert EC.decide(p, EC.Verify(True, False, 1, 4)) == ("record", EC.NOT_SUPPORTED)
    assert EC.decide(p, EC.Verify(True, True, 2, 1)) == ("record", EC.NOT_SUPPORTED)  # a page it was not shown
    assert EC.decide(p, None) == ("record", EC.VERIFY_UNPARSED)


def test_a_verify_reply_parses_only_with_both_flags() -> None:
    ok = json.dumps({"original_wrong": True, "corrected_supported": False, "page": {"exhibit": 1, "page": 4},
                     "reason": "the page records 76"})  # fmt: skip
    assert EC.parse_verify(ok) == EC.Verify(True, False, 1, 4, "the page records 76")
    assert EC.parse_verify('{"original_wrong": "yes", "corrected_supported": true}') is None
    assert EC.parse_verify("the line is wrong") is None
