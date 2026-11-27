"""A proof of service counts only when it was executed (``served_papers.executed_proof``).

D8 (2026-11-26, a $0 census replay of real response jobs): a firm keeps its own
response drafts on a template that carries a blank proof of service, "PROOF OF
SERVICE ... I served the foregoing document" with every "____ BY MAIL." line
blank and no signature. The finder read that heading as service made and
dropped two served sets as already answered, so the attorney who asked for
three responses got one. Every name, number and date here is invented.
"""

from __future__ import annotations

import json

import pytest
from test_drafting_served_find import CAPTION, CLIENT, DEF, THREE, Fake, matter, run, three_docs

from jobs.drafting import served, served_find, served_papers

BODY = (
    CAPTION + "PLAINTIFF GAMMA EXAMPLE'S RESPONSES TO SPECIAL INTERROGATORIES, SET ONE\n"
    f"PROPOUNDING PARTY: Defendant {DEF}\nRESPONDING PARTY: Plaintiff GAMMA EXAMPLE\nSET NO.: ONE\n"
    "RESPONSE TO SPECIAL INTERROGATORY NO. 1: Gamma Example.\n"
    "Dated: November 17, 2026\nKAPPA EXAMPLE LAW\nBy: ______________\nAttorneys for Plaintiff GAMMA EXAMPLE\n"
)
HEADING = (
    "PROOF OF SERVICE\nSTATE OF CALIFORNIA, COUNTY OF ORANGE\n"
    "I am employed in the County of Orange. On November 17, 2026, I served the foregoing document described as\n"
    "PLAINTIFF'S RESPONSES TO SPECIAL INTERROGATORIES, SET ONE on the interested parties in this action.\n"
)
METHODS = ["BY MAIL.", "BY FAX.", "BY PERSONAL SERVICE.", "BY OVERNIGHT MAIL.", "BY ELECTRONIC MAIL."]


def proof(marked: str | None = None, signature: str = "") -> str:
    """The template's proof of service, every method line blank but ``marked``."""
    lines = [(f"X  {m}" if m == marked else f"____ {m} I caused such document to be delivered.") for m in METHODS]
    tail = "I declare under penalty of perjury that the foregoing is true and correct.\n"
    return HEADING + "\n".join(lines) + "\n" + tail + "______________________\n" + signature


def test_a_blank_template_proof_of_service_is_not_service():
    text = BODY + proof()
    assert not served_papers.executed_proof(text)
    assert not served_papers.was_served("Resp SROGs.docx", text)  # the dated signature block does not override it


@pytest.mark.parametrize("method", METHODS)
def test_a_marked_service_method_is_service(method):
    assert served_papers.was_served("Resp SROGs.docx", BODY + proof(marked=method))


@pytest.mark.parametrize(
    "line",
    ["[X] BY ELECTRONIC MAIL. I caused it to be sent.", "[ x ] By Electronic Mail.", "☒ BY MAIL.", "BY FAX."],
)
def test_other_marks_of_a_chosen_method_are_service(line):
    assert served_papers.executed_proof(HEADING + line + "\n")


@pytest.mark.parametrize("line", ["[ ] BY ELECTRONIC MAIL.", "☐ BY MAIL.", "__ BY FAX.", "/s/ ________"])
def test_an_unchosen_box_or_a_blank_signature_is_not(line):
    assert not served_papers.executed_proof(HEADING + line + "\n")


def test_an_s_signature_with_every_box_blank_is_service():
    assert served_papers.was_served("Resp SROGs.docx", BODY + proof(signature="/s/ Jane Example\n"))


def test_a_typed_declarant_under_executed_on_a_date_is_service():
    signed = proof().replace("I declare", "Executed on November 17, 2026, at Irvine, California.\nI declare")
    assert served_papers.was_served("Resp SROGs.docx", BODY + signed + "Jane Example\n")
    assert not served_papers.was_served("Resp SROGs.docx", BODY + signed)  # a date and no name: unsigned


def test_a_served_by_statement_and_the_dated_signature_route_still_serve():
    assert served_papers.was_served("r.pdf", "PROOF OF SERVICE\nServed by email on October 30, 2026.\n")
    assert served_papers.was_served("r.pdf", BODY.replace("By: ____", "By: /s/ Jane Example ____"))
    assert not served_papers.was_served("DRAFT r.docx", BODY + proof(marked="BY MAIL."))  # a draft by name never is


DATED = "RESPONSE TO SPECIAL INTERROGATORY NO. 1: Gamma Example.\nDated: November 17, 2026\nKAPPA EXAMPLE LLP\n"


@pytest.mark.parametrize(
    "block, served_",
    [
        ("By: ________________________________\n", False),  # the firm's draft: a blank signature line
        ("By: ________________________________\nAttorneys for Plaintiff GAMMA EXAMPLE\n", False),
        ("By: /s/ ____________\n", False),
        ("By: /s/ Jane Example\n", True),
        ("By: Jane Example\nAttorneys for Plaintiff GAMMA EXAMPLE\n", True),
        ("By: ________________________________\nJANE EXAMPLE, Esq.\nAttorneys for Plaintiff\n", True),
        ("By: ________________________________\n\nJane Example\n", True),
    ],
)
def test_the_dated_route_needs_a_real_signature_not_a_blank_by_line(block, served_):
    """D8 addendum (2026-11-26): with no proof of service, the firm's draft still
    read as served through "Dated: ... / <FIRM> LLP / By: ______"."""
    assert served_papers.was_served("Resp SROGs.docx", DATED + block) is served_


def test_our_response_draft_with_a_blank_template_proof_keeps_its_served_set_to_draft(tmp_path):
    docs = [*three_docs(), {"id": "r1", "name": "Resp SROGs.docx", "ext": ".docx", "text": BODY + proof(),
                            "created": "2026-11-17T09:00:00"}]  # fmt: skip
    data, jd = matter(tmp_path, docs)
    run(data, jd, Fake(THREE))
    got = [s.set_type for s in served.read(jd, [CLIENT])]
    assert got == ["special_interrogatories", "requests_for_production", "form_interrogatories"]
    row = next(r for r in json.loads((jd / served_find.REPORT).read_text())["candidates"] if r["id"] == "r1")
    assert not row["kept"] and "not served" in row["why"]


def test_our_draft_with_a_blank_by_line_and_no_proof_keeps_its_served_set_to_draft(tmp_path):
    docs = [*three_docs(), {"id": "r1", "name": "Resp SROGs.docx", "ext": ".docx", "text": BODY,
                            "created": "2026-11-17T09:00:00"}]  # fmt: skip
    data, jd = matter(tmp_path, docs)
    run(data, jd, Fake(THREE))
    assert len(served.read(jd, [CLIENT])) == 3


def test_the_same_draft_with_its_proof_executed_answers_the_set(tmp_path):
    text = BODY + proof(marked="BY ELECTRONIC MAIL.", signature="Jane Example\n")
    docs = [*three_docs(), {"id": "r1", "name": "Resp SROGs.docx", "ext": ".docx", "text": text,
                            "created": "2026-11-17T09:00:00"}]  # fmt: skip
    data, jd = matter(tmp_path, docs)
    run(data, jd, Fake(THREE))
    assert [s.set_type for s in served.read(jd, [CLIENT])] == ["requests_for_production", "form_interrogatories"]
