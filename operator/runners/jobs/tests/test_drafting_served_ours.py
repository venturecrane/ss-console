"""Our own papers are never served sets (``provenance.whose``, 2026-11-24).

The replay: a response job failed ``served_other_set`` naming three files, a
served "DIS RFP1", an Operator response draft ("Responses to <party> RFP Set
One DRAFT <date> by A&P Operator Agent") and a corrected response
("<name>_Resp_RFP_CORRECTED_<date>"). Each carried the party lines and no title
in capitals. Every name, party, number and date here is invented.
"""

from __future__ import annotations

import json

import pytest
from test_drafting_served_find import CAPTION, CLIENT, DEF, END, Fake, _delivered, matter, run, stop_of
from test_drafting_served_find import three_docs, values, THREE

from jobs.drafting import provenance, served, served_earlier, served_find

PARTY_LINES = f"PROPOUNDING PARTY: Defendant {DEF}\nRESPONDING PARTY: Plaintiff GAMMA EXAMPLE\nSET NO.: ONE\n"
SERVED_TITLE = "Defendant Delta Example Corporation's Request for Production of Documents, Set One\n"
DRAFT = "Responses to Delta RFP Set One DRAFT 2026-09-23 by A&P Operator Agent.docx"
CORRECTED = "Gamma_Resp_RFP_CORRECTED_9-24.docx"


def _labelled(n: int) -> str:
    """Requests labelled in mixed case: nothing on the first pages in the capitals a title list reads."""
    return "\n".join(f"Request for Production No. {i}:\nAll example writings {i}.\n" for i in range(1, n + 1))


def _served_rfp(title: str = SERVED_TITLE) -> dict:
    """The served set: its title in mixed case (or none at all), labelled requests."""
    return {"id": "p1", "name": "DIS RFP1.pdf", "pages": [CAPTION + title + PARTY_LINES, _labelled(4), END]}


def _our_paper(rid: str, name: str, title: str, tail: str) -> dict:
    body = PARTY_LINES + "Response No. 1:\nGamma Example will produce.\n" + tail + title
    return {"id": rid, "name": name, "ext": ".docx", "text": body, "created": "2026-09-24T09:00:00"}


def _the_mix(served_title: str = SERVED_TITLE) -> list[dict]:
    stamped = "Dated: September 23, 2026\nBy: /s/ Alpha Example\n"  # a dated block never makes a draft served
    return [
        _served_rfp(served_title),
        _our_paper("w1", DRAFT, "Plaintiff Gamma Example's Responses to Request for Production, Set One", stamped),
        _our_paper("w2", CORRECTED, "", ""),
    ]


RFP = {"Request for Production of Documents": values("requests_for_production", 4)}


@pytest.mark.parametrize("served_title", [SERVED_TITLE, ""], ids=["mixed-case title", "no title, its name"])
def test_the_replayed_mix_finds_one_served_rfp_and_leaves_our_two_responses_out(tmp_path, served_title):
    data, jd = matter(tmp_path, _the_mix(served_title))
    fake = Fake(RFP if served_title else {"Request for Production No. 1": values("requests_for_production", 4)})
    run(data, jd, fake)
    got = served.read(jd, [CLIENT])
    assert [(s.set_type, s.set_number, s.source) for s in got] == [("requests_for_production", 1, "p1")]
    assert len(fake.seen) == 1  # only the served set was read
    report = json.loads((jd / served_find.REPORT).read_text())
    rows = {r["id"]: r for r in report["candidates"]}
    assert report["stop"] is None and not rows["w1"]["kept"] and not rows["w2"]["kept"]
    assert "a draft by its name" in rows["w1"]["why"] and "not served" in rows["w2"]["why"]
    notes = served_earlier.attorney_lines(jd / served_find.REPORT)
    assert len(notes) == 1 and DRAFT in notes[0] and CORRECTED in notes[0]
    assert "Requests for Production, Set One" in notes[0]


def test_whose_reads_each_replayed_file():
    head = CAPTION + PARTY_LINES
    assert provenance.whose("DIS RFP1.pdf", "/Discovery", head) == provenance.Whose(
        provenance.THEIRS, "served", "requests_for_production", "served on our client"
    )
    draft = provenance.whose(DRAFT, "/Discovery", head)
    assert (draft.side, draft.what, draft.kind) == (provenance.OURS, "draft", "requests_for_production")
    fixed = provenance.whose(CORRECTED, "/Discovery", head)
    assert (fixed.side, fixed.what, fixed.kind) == (provenance.OURS, "response", "requests_for_production")
    assert provenance.whose("Def SROGs.pdf", "/Discovery/Smith (Operator A1B2C3)").what == "filed"
    assert provenance.whose("x.docx", "", "", delivered=["X.docx"]).what == "filed"
    theirs = provenance.whose("Def resp.pdf", "", f"RESPONDING PARTY: Defendant {DEF}\n", clients=[CLIENT])
    assert (theirs.side, theirs.what) == (provenance.THEIRS, "response")


def test_instruction_prose_in_a_served_set_is_not_a_response_title():
    """A served set tells the other side how to answer: that is not our response."""
    prose = CAPTION + PARTY_LINES + "Your responses to these requests for production must be served in 30 days.\n"
    assert provenance.whose("Def demand.pdf", "/Discovery", prose).side == provenance.THEIRS


def test_a_leftover_of_another_kind_beside_served_sets_is_noted_not_refused(tmp_path):
    other = {"id": "o1", "name": "Notice of inspection.pdf",
             "pages": [CAPTION + "NOTICE OF INSPECTION OF LAND, SET ONE\n" + PARTY_LINES, "1. Premises.\n", END]}  # fmt: skip
    data, jd = matter(tmp_path, [*three_docs(), other])
    run(data, jd, Fake(THREE))
    assert len(served.read(jd, [CLIENT])) == 3
    row = next(r for r in json.loads((jd / served_find.REPORT).read_text())["candidates"] if r["id"] == "o1")
    assert not row["kept"] and "left out" in row["why"]
    notes = served_earlier.attorney_lines(jd / served_find.REPORT)
    assert notes == [f'Not answered by these drafts: "{other["name"]}" reads as a discovery paper of a kind other '
                     "than interrogatories, requests for production or requests for admission."]  # fmt: skip


def test_a_served_set_of_another_kind_alone_still_refuses(tmp_path):
    """served_other_set stays for real served papers this lane does not draft."""
    pages = [CAPTION + "Notice of Inspection of Land, Set One\n" + PARTY_LINES, "1. Example premises.\n", END]
    data, jd = matter(tmp_path, [{"id": "o1", "name": "Def notice.pdf", "pages": pages}])
    fake = Fake({})
    assert stop_of(lambda: run(data, jd, fake)) == "served_other_set" and fake.seen == []
    assert served_earlier.attorney_lines(jd / served_find.REPORT) == []


def test_an_earlier_paper_an_operator_job_delivered_routes_to_the_prior_draft_path(tmp_path):
    """A1: the delivered draft of one set is named, not drafted again; the other sets go on."""
    name = "Gamma Responses to Delta SROGs Set One DRAFT.docx"
    paper = _our_paper("w1", name, "Plaintiff Gamma Example's Responses to Special Interrogatories, Set One", "")
    data, jd = matter(tmp_path, [*three_docs(), paper])
    _delivered(jd, "special_interrogatories", name)
    run(data, jd, Fake(THREE))
    si = next(s for s in served.read(jd, [CLIENT]) if s.set_type == "special_interrogatories")
    assert (si.prior_draft, si.prior_job) == (name, "01PRIORJOB")
    assert served_earlier.attorney_lines(jd / served_find.REPORT) == []


def test_name_kind_reads_abbreviations_through_underscores_and_digits():
    assert provenance.name_kind("DIS RFP1.pdf") == "requests_for_production"
    assert provenance.name_kind("Gamma_Resp_RFP_CORRECTED_9-24.docx") == "requests_for_production"
    assert provenance.name_kind("Def SROGs Set 2.pdf") == "special_interrogatories"
    assert provenance.name_kind("Def RFA2.pdf") == "requests_for_admission"
    assert provenance.name_kind("Def FROGs.pdf") == "form_interrogatories"
    assert provenance.name_kind("Letter to counsel.pdf") is None


#: Our firm, as its authored caption block and its roster print it (invented).
OUR_BLOCK = [{"email": "kappa@ourfirm.example", "block": ["Kappa Example, Esq. (SBN 777001)", "OUR EXAMPLE LAW"]}]


class _Roster:
    status = "ok"
    attorneys = (type("P", (), {"name": "Lambda Example", "bar_number": "777002"})(),)


def test_our_firm_reads_the_authored_blocks_and_the_roster():
    firm = provenance.our_firm(OUR_BLOCK, _Roster())
    assert firm.names == ("Lambda Example", "Kappa Example") and firm.bars == ("777002", "777001")
    assert provenance.our_firm(OUR_BLOCK, type("R", (), {"status": "unusable"})()).bars == ("777001",)


@pytest.mark.parametrize("block", ["KAPPA EXAMPLE (SBN 777001)\n", "Mu Example (SBN 777002)\n", "LAMBDA EXAMPLE\n"])
def test_a_paper_whose_attorney_block_names_our_firm_is_ours_not_served(tmp_path, block):
    """No response title and no tell-tale name: our firm's attorney block alone marks it ours."""
    ours = {"id": "w1", "name": "Gamma RFP set.pdf", "created": "2026-09-24T09:00:00",
            "pages": [block + "Attorneys for Plaintiff GAMMA EXAMPLE\nSUPERIOR COURT OF THE STATE OF CALIFORNIA\n"
                      + PARTY_LINES + "Response No. 1:\nGamma Example will produce.\n"]}  # fmt: skip
    data, jd = matter(tmp_path, [_served_rfp(), ours])
    firm = provenance.our_firm(OUR_BLOCK, _Roster())
    served_find.find(data, jd, "Please draft responses to the discovery.", Fake(RFP).reader(), clients=[CLIENT],
                     firm=firm)  # fmt: skip
    assert [s.source for s in served.read(jd, [CLIENT])] == ["p1"]
    w = provenance.whose(ours["name"], "/Discovery", ours["pages"][0], firm=firm)
    assert (w.side, w.what) == (provenance.OURS, "attorney")


def test_another_firms_attorney_block_stays_a_served_paper():
    """The other side's block (its own bar number) never reads as ours; nor a number inside a longer one."""
    firm = provenance.our_firm(OUR_BLOCK)
    head = "NU EXAMPLE (SBN 7770019)\nSUPERIOR COURT\n" + PARTY_LINES
    assert provenance.whose("DIS RFP1.pdf", "/Discovery", head, firm=firm).side == provenance.THEIRS
