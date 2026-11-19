"""The full chronology's entry read (``medchron/update/entry_read.py``): the
planner shows each entry WHOLE (date and provider lines included) with the
pages it cites and one either side, the reply is parsed field by field, and a
finding the unit cannot ground is dropped and counted. Every value invented."""

from __future__ import annotations

import json
from typing import Any

import pytest

from jobs.medchron import page_mode as PM
from jobs.medchron.update import entry_read as ER, review_repair as RR

HEADINGS = {"Medical Diagnoses", "All Other Information"}
E1 = (
    "01/05/2025\nExample Clinic | Medical Diagnoses\n\nMedical Diagnoses\n"
    "Blood pressure was 128/84 and pulse was 72 at triage. (Exhibit 1 - p. 4)\n\n"
    "All Other Information\nTherapy twice weekly for four weeks was planned. (Exhibit 1 - p. 5)"
)
E2 = (
    "02/10/2025\nSample Imaging | All Other Information\n\nAll Other Information\n"
    "Lumbar radiographs showed mild degenerative change at L4-L5. (Exhibit 2 - p. 2)"
)
PRE = "Prior Medical History\nA prior knee sprain was reported in an earlier year. (Exhibit 1 - p. 1)"


def doc(entries: list[str], pre: str = PRE) -> str:
    return (
        "Alpha Example - Medical Chronology\n\n## Treatment Timeline\n\n## Diagnostic Highlights\n\n"
        "## Medical Chronology\n\n" + (pre + "\n\n" if pre else "") + "\n\n".join(entries) + "\n\n## Exhibit List\n"
    )


def npages(ex: int) -> int:
    return {1: 5, 2: 3}.get(ex, 50)


def units(md: str | None = None) -> list[ER.Unit]:
    return ER.plan(md or doc([E1, E2]), {1, 2, 3}, HEADINGS, npages)


#: A verdict for every label an invented entry here can carry, all ``ok``.
ALL_OK = {"D": "ok", "P": "ok", **{f"S{i}": "ok" for i in range(1, 60)}}


def reply(*items: dict[str, Any], complete: bool = True, lines: dict[str, str] | None = None) -> str:
    return json.dumps({"lines": ALL_OK if lines is None else lines, "findings": list(items), "complete": complete})


def item(**kw: Any) -> dict[str, Any]:
    base = {"line": "S1", "class": "value", "exhibit": 1, "page": 4, "quote": "pulse was 72", "action": "replace",
            "replacement": "pulse was 78", "problem": "the page records 78"}  # fmt: skip
    return {**base, **kw}


def on_line(u: ER.Unit, **kw: Any) -> dict[str, Any]:
    """An item whose line is the unit's line holding its quote (unless named)."""
    it = item(**kw)
    if "line" not in kw:
        it["line"] = next((lb for lb, t in u.lines.items() if it["quote"] in t), "S1")
    return it


# ---- plan --------------------------------------------------------------------------
def test_one_unit_per_entry_with_its_header_lines_and_a_record_only_prior_history() -> None:
    us = units()
    assert [u.anchor for u in us] == [ER.PRE, "N1", "N2"]
    assert us[0].record_only and not us[1].record_only
    assert us[1].text.startswith("01/05/2025\nExample Clinic | Medical Diagnoses")
    assert "02/10/2025\nSample Imaging" in us[2].text


def test_neighbour_pages_stay_inside_the_exhibit() -> None:
    n1, n2 = units()[1:]
    assert n1.pages == [(1, 3), (1, 4), (1, 5)]  # p. 6 is past Exhibit 1's five pages
    assert n2.pages == [(2, 1), (2, 2), (2, 3)]
    assert units()[0].pages == [(1, 1), (1, 2)]  # no page 0


def test_an_entry_showing_more_than_24_pages_is_split() -> None:
    sents = "\n\n".join(f"Finding number {i} was recorded at the visit. (Exhibit 3 - p. {i})" for i in range(1, 46))
    big = f"03/01/2025\nExample Clinic | All Other Information\n\nAll Other Information\n{sents}"
    us = [u for u in units(doc([E1, big], pre="")) if u.anchor == "N2"]
    assert [(u.part, u.parts) for u in us] == [(1, 2), (2, 2)]
    assert [u.key for u in us] == ["N2-1", "N2-2"]
    assert max(p for _, p in us[0].pages) == 24 and min(p for _, p in us[1].pages) == 23
    assert all(len(u.pages) <= ER.MAX_SHOWN == 24 for u in us)
    assert all(u.text == us[0].text for u in us)  # the whole entry rides with every split
    # 22 cited pages show 23: one unit, as before the cap.
    small = "\n\n".join(f"Finding number {i} was recorded at the visit. (Exhibit 3 - p. {i})" for i in range(1, 23))
    one = [u for u in units(doc([E1, big.replace(sents, small)], pre="")) if u.anchor == "N2"]
    assert [(u.part, u.parts, len(u.pages)) for u in one] == [(1, 1, 23)]


def test_the_request_carries_the_entry_whole_and_each_page_by_the_one_rule(firm_config_path) -> None:
    from jobs.medchron import config as config_mod

    u = units()[1]

    def modes(ex: int, pg: int) -> PM.Mode:
        return PM.Mode(PM.TEXT, "Vitals: BP 128/84, pulse 72.", "native") if pg == 4 else PM.Mode(PM.IMAGE, None, "x")

    system, content = ER.request(u, config_mod.load(str(firm_config_path)), "CLIENT: Alpha Example", _img, modes)
    texts = [b.get("text", "") for b in content]
    assert "{{" not in system and "ENTRY READ" in system
    assert (
        "ENTRY N1:\n[D] 01/05/2025\n[P] Example Clinic | Medical Diagnoses\n\nMedical Diagnoses\n"
        "[S1] Blood pressure was 128/84 and pulse was 72 at triage. (Exhibit 1 - p. 4)\n\nAll Other Information\n"
        "[S2] Therapy twice weekly for four weeks was planned. (Exhibit 1 - p. 5)"
    ) in texts
    assert not any("INDEX" in t for t in texts)  # no index-gap line, ever (round 3)
    assert sum(1 for b in content if b.get("type") == "image") == 2  # pp. 3 and 5
    assert any(t.startswith("PAGE TEXT") and "pulse 72" in t for t in texts)  # p. 4 as text


def _img(ex: int, pg: int) -> dict[str, Any]:
    return {"type": "image", "page": f"{ex}:{pg}"}


# ---- parse -------------------------------------------------------------------------
def test_a_good_finding_parses() -> None:
    got = ER.parse(reply(item()), units()[1])
    assert len(got.findings) == 1 and not got.dropped
    f = got.findings[0]
    assert (f.klass, f.anchor, f.exhibit, f.page, f.quote) == ("value", "N1", 1, 4, "pulse was 72")


def test_unknown_class_unshown_page_and_unquoted_text_are_dropped_and_counted() -> None:
    bad = [item(**{"class": "style"}), item(page=9), item(exhibit=2, page=2), item(quote="pulse was 99"), "x"]
    got = ER.parse(reply(item(), *bad), units()[1])
    assert len(got.findings) == 1
    assert dict(got.dropped) == {"class": 1, "page": 2, "quote": 1, "not_object": 1}


def test_a_thirteenth_finding_is_counted_over_cap() -> None:
    got = ER.parse(reply(*[item() for _ in range(13)]), units()[1])
    assert len(got.findings) == ER.MAX_FINDINGS and got.over_cap == 1


@pytest.mark.parametrize("text", ["not json at all", reply(item(), complete=False), '{"complete": true}'])
def test_an_incomplete_reply_raises_the_review_s_own_signal(text: str) -> None:
    with pytest.raises(RR.ReplyUnparsed):
        ER.parse(text, units()[1])


def test_a_prior_history_finding_is_marked_record_only() -> None:
    got = ER.parse(reply(item(page=1, quote="knee sprain", replacement="ankle sprain")), units()[0])
    assert got.findings[0].record_only


# ---- tripwire ----------------------------------------------------------------------
def test_sentences_skip_date_provider_heading_and_bare_citation_lines() -> None:
    from jobs.medchron.audit import claims as CL

    assert ER.count_sentences(CL.body_of(doc([E1, E2]))) == 4  # pre, two in N1, one in N2
    assert ER.count_sentences("\n\n01/05/2025\nExample Clinic | X\n\nX\n(Exhibit 1 - p. 2)\n") == 0


def test_over_share_fires_past_eight_per_hundred() -> None:
    assert not ER.over_share(8, 100)
    assert ER.over_share(9, 100)
    assert ER.over_share(1, 0)


# ---- every line accounted for (round 3, 2026-11-18) ----------------------------------
def test_a_reply_that_skips_one_line_is_unparsed() -> None:
    n1 = units()[1]
    assert n1.labels == ("D", "P", "S1", "S2")
    skip = {"D": "ok", "P": "ok", "S1": "ok"}  # S2 never judged
    with pytest.raises(RR.ReplyUnparsed):
        ER.parse(reply(lines=skip), n1)
    with pytest.raises(RR.ReplyUnparsed):
        ER.parse(json.dumps({"findings": [], "complete": True}), n1)  # no lines at all
    with pytest.raises(RR.ReplyUnparsed):
        ER.parse(reply(lines={**skip, "S2": "fine"}), n1)  # not one of the three verdicts
    assert ER.parse(reply(lines={**skip, "S2": "unreadable"}), n1).lines["S2"] == "unreadable"


def test_a_quote_from_another_line_is_dropped() -> None:
    n1 = units()[1]
    got = ER.parse(reply(item(line="S2")), n1)  # "pulse was 72" is on S1, not S2
    assert got.findings == [] and got.dropped == {"quote": 1}
    assert ER.parse(reply(item(line="S9")), n1).dropped == {"line": 1}


def test_findings_on_an_unreadable_line_or_page_are_dropped_and_a_bare_problem_is_counted() -> None:
    n1 = units()[1]
    lines = {"D": "ok", "P": "problem", "S1": "unreadable", "S2": "ok"}
    got = ER.parse(reply(item(), lines=lines), n1)
    assert got.findings == [] and got.dropped == {"unreadable": 1} and got.problem_without_finding == 1
    obj = json.loads(reply(item(), lines={**lines, "S1": "problem"}))
    obj["unreadable_pages"] = [{"exhibit": 1, "page": 4}]
    assert ER.parse(json.dumps(obj), n1).dropped == {"unreadable": 1}


def test_the_fifth_omitted_finding_is_counted_over_cap() -> None:
    n1 = units()[1]
    om = item(**{"class": "omitted", "action": "add", "replacement": "A plan was made. (Exhibit 1 - p. 4)"})
    got = ER.parse(reply(*[om] * 5), n1)
    assert len(got.findings) == 4 and got.omitted_over_cap == 1 and got.over_cap == 0


def test_labels_and_the_tripwire_count_agree_on_a_heading_a_bare_citation_and_two_sentences_on_a_line() -> None:
    from jobs.medchron.audit import claims as CL

    e = (
        "03/03/2025\nExample Clinic | All Other Information\n\nAll Other Information\n"
        "Gait was steady at the visit. (Exhibit 1 - p. 2) Home exercise was reviewed again. (Exhibit 1 - p. 3)\n"
        "(Exhibit 1 - p. 3)\n\nMedical Diagnoses\nA lumbar strain was assessed by the examiner. (Exhibit 1 - p. 4)"
    )
    md = doc([e])
    us = ER.plan(md, {1}, HEADINGS, npages)
    shown = ER.EL.numbered(e)[0].split("\n")
    assert "(Exhibit 1 - p. 3)" in shown and "Medical Diagnoses" in shown  # shown, unlabelled
    s_labels = [lb for u in us for lb in u.labels if lb.startswith("S")]
    assert len(s_labels) == ER.count_sentences(CL.body_of(md)) == 4  # PRE's one, and three in the entry
    n1 = next(u for u in us if u.anchor == "N1")
    assert n1.lines["S1"] == "Gait was steady at the visit. (Exhibit 1 - p. 2)"
    assert n1.lines["S2"] == "Home exercise was reviewed again. (Exhibit 1 - p. 3)"


def test_the_prior_history_unit_has_only_sentence_labels() -> None:
    pre = units()[0]
    assert pre.anchor == ER.PRE and pre.labels == ("S1",)
    assert pre.lines["S1"] == "A prior knee sprain was reported in an earlier year. (Exhibit 1 - p. 1)"


# ---- round 4 ------------------------------------------------------------------------
def test_a_short_line_carrying_a_citation_is_a_sentence_never_a_heading() -> None:
    e = (
        "03/03/2025\nExample Clinic | All Other Information\n\nAll Other Information\n"
        "BP 128/84, pulse 72 (Exhibit 1 - p. 2)\n(Exhibit 1 - p. 3)"
    )
    _shown, lines = ER.EL.numbered(e)
    assert lines["S1"] == "BP 128/84, pulse 72 (Exhibit 1 - p. 2)" and "S2" not in lines  # the bare citation is not


def test_line_keys_written_with_brackets_are_the_same_labels() -> None:
    n1 = units()[1]
    got = ER.parse(reply(lines={"[D]": "ok", "[P]": "ok", "[S1]": "ok", " S2 ": "problem"}), n1)
    assert got.lines == {"D": "ok", "P": "ok", "S1": "ok", "S2": "problem"}
