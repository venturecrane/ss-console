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


def reply(*items: dict[str, Any], complete: bool = True) -> str:
    return json.dumps({"findings": list(items), "complete": complete})


def item(**kw: Any) -> dict[str, Any]:
    base = {"class": "value", "exhibit": 1, "page": 4, "quote": "pulse was 72", "action": "replace",
            "replacement": "pulse was 78", "problem": "the page records 78"}  # fmt: skip
    return {**base, **kw}


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


def test_an_entry_citing_45_pages_is_split() -> None:
    sents = "\n\n".join(f"Finding number {i} was recorded at the visit. (Exhibit 3 - p. {i})" for i in range(1, 46))
    big = f"03/01/2025\nExample Clinic | All Other Information\n\nAll Other Information\n{sents}"
    us = [u for u in units(doc([E1, big], pre="")) if u.anchor == "N2"]
    assert [(u.part, u.parts) for u in us] == [(1, 2), (2, 2)]
    assert [u.key for u in us] == ["N2-1", "N2-2"]
    assert max(p for _, p in us[0].pages) == 41 and min(p for _, p in us[1].pages) == 40
    assert all(u.text == us[0].text for u in us)  # the whole entry rides with every split


def test_the_request_carries_the_entry_whole_the_gaps_and_each_page_by_the_one_rule(firm_config_path) -> None:
    from jobs.medchron import config as config_mod

    u = units()[1]
    u.gaps.append("The writer's own index for this visit lists codes M54.5 that the entry does not state.")

    def modes(ex: int, pg: int) -> PM.Mode:
        return PM.Mode(PM.TEXT, "Vitals: BP 128/84, pulse 72.", "native") if pg == 4 else PM.Mode(PM.IMAGE, None, "x")

    system, content = ER.request(u, config_mod.load(str(firm_config_path)), "CLIENT: Alpha Example", _img, modes)
    texts = [b.get("text", "") for b in content]
    assert "{{" not in system and "ENTRY READ" in system
    assert any(t.startswith("ENTRY N1:\n01/05/2025\nExample Clinic") for t in texts)
    assert any("codes M54.5" in t for t in texts)
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
