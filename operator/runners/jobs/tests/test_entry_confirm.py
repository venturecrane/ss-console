"""The blind confirm and the code that decides (``medchron/update/entry_confirm.py``):
a correction applies only when the page's text layer carries it or a blind
read that never saw it agrees; a pointer only when the blind read names the
page; an added sentence only on YES, on a cited page, stated nowhere else;
date, provider and prior-history findings are recorded; nothing is removed.
Every applied edit goes through ``review_repair.apply`` unchanged. Every
value invented."""

from __future__ import annotations

import json
from typing import Any

from jobs.medchron.audit import claims as CL
from jobs.medchron.update import entry_confirm as EC, entry_read as ER, review_repair as RR
from test_entry_read import E1, E2, HEADINGS, _img, doc, item, reply, units

KEEP = {1, 2, 3}
PAGE4 = "Triage vitals. Blood pressure 128/84. Pulse 78. Lumbar flexion limited to 40 degrees."


def found(u: ER.Unit, **kw: Any) -> ER.ReadFinding:
    got = ER.parse(reply(item(**kw)), u)
    assert got.findings, got.dropped
    return got.findings[0]


def confirm(**kw: Any) -> EC.Confirm:
    return EC.parse_confirm(json.dumps(kw))


def applied(md: str, d: EC.Decision, us: list[ER.Unit]) -> str:
    assert d[0] == "apply", d
    edits = EC.to_repair_findings([d])
    out, _ = RR.apply(md, edits, KEEP, HEADINGS, shown=EC.shown(us))
    assert edits[0].outcome == "applied", edits[0].outcome
    assert ER.count_sentences(CL.body_of(out)) >= ER.count_sentences(CL.body_of(md))  # nothing removed
    return out


# ---- value -------------------------------------------------------------------------
def test_a_wrong_value_is_corrected_when_the_page_text_carries_it() -> None:
    md, us = doc([E1, E2]), units()
    d = EC.decide(found(us[1]), None, PAGE4, us[1])
    out = applied(md, d, us)
    assert "pulse was 78 at triage. (Exhibit 1 - p. 4)" in out and "pulse was 72" not in out


def test_a_wrong_value_is_not_applied_when_the_page_text_lacks_it() -> None:
    us = units()
    d = EC.decide(found(us[1]), None, "Triage vitals. Blood pressure 128/84. Pulse 72.", us[1])
    assert d[0] == "record"


def test_a_change_that_adds_nothing_checkable_is_recorded() -> None:
    us = units()
    f = found(us[1], quote="Blood pressure was", replacement="No blood pressure was")
    assert EC.decide(f, None, PAGE4, us[1]) == ("record", "the change adds nothing the page text can be checked for")


def test_on_a_picture_page_only_a_matching_blind_value_applies() -> None:
    md, us = doc([E1, E2]), units()
    f = found(us[1])
    applied(md, EC.decide(f, confirm(answer="STATED", exhibit=1, page=4, value="78"), None, us[1]), us)
    assert EC.decide(f, confirm(answer="STATED", exhibit=1, page=4, value="76"), None, us[1])[0] == "record"
    assert EC.decide(f, confirm(answer="STATED", exhibit=1, page=3, value="78"), None, us[1])[0] == "record"
    assert EC.decide(f, confirm(answer="CANNOT_READ"), None, us[1])[0] == "record"
    assert EC.decide(f, None, None, us[1])[0] == "record"
    assert EC.decide(f, EC.parse_confirm("the pulse is 78"), None, us[1])[0] == "record"


def test_a_text_page_still_records_when_the_blind_read_says_not_stated() -> None:
    us = units()
    assert EC.decide(found(us[1]), confirm(answer="NOT_STATED"), PAGE4, us[1])[0] == "record"


# ---- pointer -----------------------------------------------------------------------
def test_a_pointer_applies_only_when_the_blind_read_names_that_page() -> None:
    md, us = doc([E1, E2]), units()
    f = found(us[1], **{"class": "pointer", "page": 4, "quote": "Therapy twice weekly for four weeks was planned."})
    out = applied(md, EC.decide(f, confirm(answer="STATED", exhibit=1, page=4), None, us[1]), us)
    assert "Therapy twice weekly for four weeks was planned. (Exhibit 1 - p. 4)" in out
    assert EC.decide(f, confirm(answer="STATED", exhibit=1, page=5), None, us[1])[0] == "record"
    assert EC.decide(f, confirm(answer="NOT_STATED"), None, us[1])[0] == "record"


# ---- omitted -----------------------------------------------------------------------
ADD = "Lumbar flexion was limited to 40 degrees. (Exhibit 1 - p. 4)"


def _omitted(u: ER.Unit, **kw: Any) -> ER.ReadFinding:
    args = {"class": "omitted", "action": "add", "quote": "Medical Diagnoses", "replacement": ADD}
    return found(u, **{**args, **kw})


def test_an_added_sentence_applies_on_yes_on_a_cited_page_stated_nowhere_else() -> None:
    md, us = doc([E1, E2]), units()
    others = [u.entry for u in us if u.entry]
    d = EC.decide(_omitted(us[1]), confirm(answer="YES", exhibit=1, page=4), None, us[1], others)
    out = applied(md, d, us)
    assert ADD in out and ER.count_sentences(CL.body_of(out)) == ER.count_sentences(CL.body_of(md)) + 1
    assert EC.decide(_omitted(us[1]), confirm(answer="NO"), None, us[1], others)[0] == "record"


def test_an_added_sentence_on_an_uncited_neighbour_page_is_recorded() -> None:
    us = units()
    f = _omitted(us[1], page=3, replacement="Lumbar flexion was limited to 40 degrees. (Exhibit 1 - p. 3)")
    assert EC.decide(f, confirm(answer="YES", exhibit=1, page=3), None, us[1]) == (
        "record",
        "the page is not one the entry already cites",
    )


def test_an_added_sentence_another_entry_carries_is_recorded() -> None:
    e3 = (
        "01/06/2025\nExample Clinic | All Other Information\n\nAll Other Information\n"
        "Flexion of the lumbar spine reached 40 degrees on recheck. (Exhibit 1 - p. 4)"
    )
    us = ER.plan(doc([E1, e3, E2]), KEEP, HEADINGS, lambda ex: 5)
    n1 = next(u for u in us if u.anchor == "N1")
    d = EC.decide(_omitted(n1), confirm(answer="YES", exhibit=1, page=4), None, n1, [u.entry for u in us if u.entry])
    assert d == ("record", "another entry already carries that page and those values")


# ---- recorded classes --------------------------------------------------------------
def test_date_provider_and_prior_history_findings_are_recorded_never_applied() -> None:
    us = units()
    yes = confirm(answer="STATED", exhibit=1, page=4, value="01/06/2025")
    for klass, quote in (("date", "01/05/2025"), ("provider", "Example Clinic"), ("another_person", "pulse")):
        f = found(us[1], **{"class": klass, "quote": quote, "replacement": "01/06/2025"})
        assert EC.decide(f, yes, PAGE4, us[1])[0] == "record"
    pre = found(us[0], page=1, quote="knee sprain", replacement="ankle sprain")
    assert EC.decide(pre, None, "An ankle sprain was reported.", us[0])[0] == "record"


# ---- the blind request ---------------------------------------------------------------
def test_the_confirm_never_sees_the_replacement_or_the_entry(firm_config_path) -> None:
    from jobs.medchron import config as config_mod

    cfg, us = config_mod.load(str(firm_config_path)), units()
    f = found(us[1], quote="pulse was 72 at triage", replacement="pulse was 78 at triage")
    system, content = EC.confirm_request(f, us[1], cfg, "CLIENT: Alpha Example", _img)
    asked = " ".join(b.get("text", "") for b in content)
    assert "ENTRY CONFIRM" in system and "{{" not in system
    assert "78" not in asked and "72" not in asked and EC.BLANK in asked and "Example Clinic" not in asked
    assert sum(1 for b in content if b.get("type") == "image") == len(us[1].pages)
    add = EC.confirm_request(_omitted(us[1]), us[1], cfg, "CLIENT", _img)[1]
    shown = " ".join(b.get("text", "") for b in add)
    assert "limited to 40 degrees" in shown and "(Exhibit 1 - p. 4)" not in shown  # the page is not hinted


def test_the_summary_is_counts_only() -> None:
    us = units()
    parsed = [ER.parse(reply(item(), item(page=9)), us[1])]
    ds = [EC.decide(parsed[0].findings[0], None, PAGE4, us[1])]
    s = EC.summary(us, parsed, ds)
    assert s["units"] == 3 and s["applied"] == 1 and s["dropped"] == {"page": 1}
    assert s["findings_by_class"] == {"value": 1} and s["confirmed"] == 1
    assert "pulse" not in json.dumps(s) and "72" not in json.dumps(s)
