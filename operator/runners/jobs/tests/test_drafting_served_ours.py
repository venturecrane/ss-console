"""Our own papers are never served sets, and a real served set is never dropped (``provenance.whose``).

The replay (2026-11-24): a response job failed ``served_other_set`` naming a
served "DIS RFP1", an Operator response draft ("Responses to <party> RFP Set One
DRAFT <date> by A&P Operator Agent") and a corrected response
("<name>_Resp_RFP_CORRECTED_<date>"). The #4226 review: a paper is ours only on
STRONG evidence (our filing records, the Operator's stamp, a response title with
our client responding); name words and attorney names are supporting only, so
a renamed or quoted served set stays served. Every name, party, number and date
here is invented.
"""

from __future__ import annotations

import json

import pytest
from test_drafting_served_find import CAPTION, CLIENT, DEF, END, THREE, Fake, _delivered, matter, run, stop_of
from test_drafting_served_find import bare, response, srog, three_docs, values

from jobs.drafting import provenance, served, served_earlier, served_find, served_page, served_papers
from jobs.drafting.served_docs import Doc

PARTY_LINES = f"PROPOUNDING PARTY: Defendant {DEF}\nRESPONDING PARTY: Plaintiff GAMMA EXAMPLE\nSET NO.: ONE\n"
SERVED_TITLE = "Defendant Delta Example Corporation's Request for Production of Documents, Set One\n"
DRAFT = "Responses to Delta RFP Set One DRAFT 2026-09-23 by A&P Operator Agent.docx"
CORRECTED = "Gamma_Resp_RFP_CORRECTED_9-24.docx"
OUR_TITLE = "Plaintiff Gamma Example's Responses to Request for Production, Set One"
EV = provenance.Evidence((CLIENT,))
RFP_READ = {"Request for Production No. 1": values("requests_for_production", 4)}
HEAD = CAPTION + SERVED_TITLE + PARTY_LINES


def _labelled(n: int) -> str:
    """Requests labelled in mixed case: nothing on the first pages in the capitals a title list reads."""
    return "\n".join(f"Request for Production No. {i}:\nAll example writings {i}.\n" for i in range(1, n + 1))


def _served_rfp(title: str = SERVED_TITLE) -> dict:
    """The served set: its title in mixed case (or none at all), labelled requests."""
    return {"id": "p1", "name": "DIS RFP1.pdf", "pages": [CAPTION + title + PARTY_LINES, _labelled(4), END]}


def _our_paper(rid: str, name: str, title: str, tail: str) -> dict:
    body = PARTY_LINES + "Response No. 1:\nGamma Example will produce.\n" + tail + title
    return {"id": rid, "name": name, "ext": ".docx", "text": body, "created": "2026-09-24T09:00:00"}


def _the_mix(served_title: str = SERVED_TITLE, corrected_title: str = OUR_TITLE) -> list[dict]:
    stamped = "Dated: September 23, 2026\nBy: /s/ Alpha Example\n"  # a dated block never makes a draft served
    return [
        _served_rfp(served_title),
        _our_paper("w1", DRAFT, "", stamped),  # the Operator's stamp alone: no title at all
        _our_paper("w2", CORRECTED, corrected_title, ""),
    ]


def _doc(name: str, text: str, folder: str = "/Discovery", ext: str = ".pdf") -> Doc:
    return Doc(id="x", name=name, folder=folder, pages={1: text}, listed=None, sha=None, ext=ext)


def _report(jd) -> dict:
    return json.loads((jd / served_find.REPORT).read_text())


# ---- the replay -------------------------------------------------------------------------------


@pytest.mark.parametrize("served_title", [SERVED_TITLE, ""], ids=["mixed-case title", "no title, its name"])
def test_the_replayed_mix_finds_one_served_rfp_and_leaves_our_two_responses_out(tmp_path, served_title):
    """The corrected response carries its title with our client responding: strong, so ours."""
    data, jd = matter(tmp_path, _the_mix(served_title))
    fake = Fake(RFP_READ)
    run(data, jd, fake)
    got = served.read(jd, [CLIENT])
    assert [(s.set_type, s.set_number, s.source) for s in got] == [("requests_for_production", 1, "p1")]
    assert len(fake.seen) == 1  # only the served set was read
    rows = {r["id"]: r for r in _report(jd)["candidates"]}
    assert not rows["w1"]["kept"] and not rows["w2"]["kept"]
    assert "a draft by its name" in rows["w1"]["why"] and "not served" in rows["w2"]["why"]
    notes = served_earlier.attorney_lines(jd / served_find.REPORT)
    assert len(notes) == 1 and DRAFT in notes[0] and CORRECTED in notes[0]
    assert "Requests for Production, Set One" in notes[0]


def test_a_corrected_response_with_only_its_name_holds_the_job(tmp_path):
    """Weak evidence only ("Resp" in a name, no title, no stamp, no record): never dropped as ours.
    It reads as a served RFP candidate, and code's checks of the read fail on it (here its values
    are not on the paper), so the job holds for SMD before drafting: a hold, never a guess."""
    data, jd = matter(tmp_path, _the_mix(corrected_title=""))
    fake = Fake({**RFP_READ, "Gamma Example will produce": values("requests_for_production", 1)})
    assert stop_of(lambda: run(data, jd, fake)) in ("served_ambiguous", "served_unnumbered")
    assert CORRECTED in _report(jd)["reason"] and not (jd / served.FILE).exists()


def test_whose_reads_each_replayed_file():
    w = provenance.whose("DIS RFP1.pdf", "/Discovery", CAPTION + PARTY_LINES, ev=EV)
    assert (w.side, w.what, w.kind) == (provenance.THEIRS, "served", "requests_for_production")
    draft = provenance.whose(DRAFT, "/Discovery", PARTY_LINES, ev=EV)
    assert (draft.side, draft.what, draft.kind) == (provenance.OURS, "stamped", "requests_for_production")
    titled = provenance.whose(CORRECTED, "/Discovery", PARTY_LINES + OUR_TITLE + "\n", ev=EV)
    assert (titled.side, titled.what) == (provenance.OURS, "response")
    bare = provenance.whose(CORRECTED, "/Discovery", PARTY_LINES, ev=EV)
    assert (bare.side, bare.clues) == (provenance.THEIRS, ("a response by its name",))


def test_a_response_title_with_the_other_side_responding_is_not_ours():
    theirs = f"PROPOUNDING PARTY: Plaintiff GAMMA EXAMPLE\nRESPONDING PARTY: Defendant {DEF}\n"
    assert provenance.whose("x.docx", "", theirs + OUR_TITLE + "\n", ev=EV).side == provenance.THEIRS
    assert provenance.whose("x.docx", "", PARTY_LINES + OUR_TITLE + "\n").side == provenance.THEIRS  # no clients


def test_our_filing_records_make_a_paper_ours():
    assert provenance.whose("Def SROGs.pdf", "/Discovery/Smith (Operator A1B2C3)").what == "filed"
    assert provenance.whose("x.docx", "", "", ev=provenance.Evidence(delivered=("X.docx",))).what == "filed"
    proven = provenance.Evidence(proven=(("f-9", "aa"),))
    assert provenance.whose("y.pdf", "", PARTY_LINES, ev=proven, doc_id="f-9", sha="aa").what == "filed"
    assert provenance.whose("y.pdf", "", PARTY_LINES, ev=proven, doc_id="f-8", sha="aa").side == provenance.THEIRS
    # B7: the id alone is not ours; a paper that later took that id has other bytes.
    assert provenance.whose("y.pdf", "", PARTY_LINES, ev=proven, doc_id="f-9", sha="bb").side == provenance.THEIRS


def test_proven_ids_read_the_sibling_jobs_delivery_proof(tmp_path):
    jd = tmp_path / "J1"
    jd.mkdir()
    (jd / "job.json").write_text(json.dumps({"matter_id": "m-1"}))
    for job, matter_id in (("J0", "m-1"), ("J9", "m-2")):
        (tmp_path / job).mkdir()
        (tmp_path / job / "job.json").write_text(json.dumps({"matter_id": matter_id}))
        proof = {"proof": {"files": [{"file_id": f"f-{job}", "size": 1, "sha256": "0" * 64}]}}
        (tmp_path / job / "verdict.json").write_text(json.dumps(proof))
    assert served_earlier.proven_ids(jd) == (("f-J0", "0" * 64),)


# ---- B1, B2: weak names and instruction prose never drop a real served set ---------------------


@pytest.mark.parametrize(
    "name",
    [
        "Resp due 12-1 RFP1.pdf",
        "RFP1 - response due 12-1.pdf",
        "Answers to RFP1 due.pdf",
        "RFP re Draft Lease.pdf",
        "Def RFP1 (Operator Agent copy).pdf",
        "RFP1 resp.pdf",
        "Responsive docs RFP1.pdf",
        "RFP Set One - Responses Due 10.1.pdf",
    ],
)
def test_response_words_in_a_name_never_make_a_served_set_ours(name):
    assert served_page.classify(_doc(name, HEAD + _labelled(3)), EV) == ("served", "requests_for_production")


@pytest.mark.parametrize(
    "prose",
    [
        "Plaintiff is required to serve written responses to Request for Production, Set One, within 30 days.\n",
        "You must serve your responses to these Requests for Production, Set One within thirty days.\n",
        "Responses to these Requests for Production are due within thirty days.\n",
    ],
)
@pytest.mark.parametrize("ext", [".pdf", ".docx"])
def test_instruction_prose_is_never_a_response_title(prose, ext):
    d = _doc("DIS RFP1" + ext, HEAD + prose + _labelled(3), ext=ext)
    assert served_page.classify(d, EV) == ("served", "requests_for_production")


@pytest.mark.parametrize("sub", ["renamed", "copy_beside"])
def test_a_renamed_served_set_is_read_and_a_renamed_copy_never_answers_its_original(tmp_path, sub):
    pages = [HEAD, _labelled(3), END]
    docs = [{"id": "p2", "name": "Resp due 12-1 RFP1.pdf", "pages": pages}]
    docs = docs if sub == "renamed" else [{"id": "p1", "name": "DIS RFP1.pdf", "pages": pages}, *docs]
    data, jd = matter(tmp_path, docs)
    run(data, jd, Fake({"Request for Production No. 1": values("requests_for_production", 3)}))
    assert [(s.set_type, s.set_number) for s in served.read(jd, [CLIENT])] == [("requests_for_production", 1)]


def test_a_served_paper_with_the_other_sides_proof_of_service_is_never_dropped_on_weak_evidence():
    """The structural guard: a response name, a draft name and our attorney quoted on it are not enough."""
    firm = provenance.OurFirm(("Kappa Example",), ("777001",))
    text = "Kappa Example (SBN 777001)\nAttorneys for Plaintiff GAMMA EXAMPLE\n" + HEAD + _labelled(3) + END
    d = _doc("Resp to Def RFP1 DRAFT.pdf", text)
    ev = provenance.Evidence((CLIENT,), firm)
    assert served_papers.was_served("Def RFP1.pdf", d.text)
    assert served_page.classify(d, ev) == ("served", "requests_for_production")
    w = provenance.whose(d.name, d.folder, d.first, ev=ev)
    assert w.side == provenance.THEIRS and len(w.clues) == 3


def test_the_other_sides_response_to_our_discovery_is_dropped_by_its_asking_party(tmp_path):
    theirs = f"PROPOUNDING PARTY: Plaintiff GAMMA EXAMPLE\nRESPONDING PARTY: Defendant {DEF}\nSET NO.: ONE\n"
    title = "Defendant's Responses to Plaintiff's Request for Production, Set One\n"
    paper = {"id": "r1", "name": "Delta production.pdf", "pages": [CAPTION + title + theirs, "Response No. 1.\n", END]}
    data, jd = matter(tmp_path, [{"id": "s1", "name": "Def SROG1.pdf", "pages": srog(5)}, paper])
    run(data, jd, Fake({"SPECIAL": values("special_interrogatories", 5)}))
    assert [s.source for s in served.read(jd, [CLIENT])] == ["s1"]
    assert "propounded by our client" in next(r for r in _report(jd)["candidates"] if r["id"] == "r1")["why"]


# ---- B3: our firm's attorney block is supporting evidence only ----------------------------------


#: Our firm, as its authored caption block and its roster print it (invented).
OUR_BLOCK = [{"email": "kappa@ourfirm.example", "block": ["Kappa Example, Esq. (SBN 777001)", "OUR EXAMPLE LAW"]}]
ATTY = "\nAttorneys for Plaintiff GAMMA EXAMPLE\nSUPERIOR COURT\n"


class _Roster:
    status = "ok"
    attorneys = (type("P", (), {"name": "Lambda Example", "bar_number": "777002"})(),)


def test_our_firm_reads_the_authored_blocks_and_the_roster():
    firm = provenance.our_firm(OUR_BLOCK, _Roster())
    assert firm.names == ("Lambda Example", "Kappa Example") and firm.bars == ("777002", "777001")
    assert provenance.our_firm(OUR_BLOCK, type("R", (), {"status": "unusable"})()).bars == ("777001",)
    assert provenance.our_firm([], None) == provenance.our_firm(None) == provenance.our_firm([{"block": []}])


@pytest.mark.parametrize(
    "text, ext, found",
    [
        ("KAPPA EXAMPLE (SBN 777001)" + ATTY, ".pdf", "777001"),
        ("Mu Example (SBN 777002)" + ATTY, ".pdf", "777002"),
        ("LAMBDA EXAMPLE" + ATTY, ".pdf", "Lambda Example"),
        ("KAPPA EXAMPLE (SBN 777001)" + ATTY, ".docx", ""),  # a Word file's text order is not a block
        ("TO PLAINTIFF AND HER ATTORNEYS OF RECORD, Kappa Example:" + ATTY, ".pdf", ""),
        ("Case No. CV2026-777001" + ATTY, ".pdf", ""),
        ("NU EXAMPLE (SBN 7770019)" + ATTY, ".pdf", ""),
        ("KAPPA EXAMPLES LLP" + ATTY, ".pdf", ""),
        ("Kappa Example (SBN 777001)\nSUPERIOR COURT\n", ".pdf", ""),  # no "Attorneys for": no block read
    ],
)
def test_the_attorney_check_reads_only_a_pdfs_attorney_block(text, ext, found):
    assert provenance.our_attorney(text, ext, provenance.our_firm(OUR_BLOCK, _Roster())) == found


def test_our_attorney_alone_never_makes_a_paper_ours():
    d = _doc("Gamma RFP set.pdf", "KAPPA EXAMPLE (SBN 777001)" + ATTY + PARTY_LINES)
    ev = provenance.Evidence((CLIENT,), provenance.our_firm(OUR_BLOCK))
    w = provenance.whose(d.name, d.folder, d.first, ev=ev)
    assert w.side == provenance.THEIRS and w.clues == ("its attorney block names our firm's 777001",)
    assert served_page.classify(d, ev) == ("served", "requests_for_production")


# ---- B4, B5: draft names, scans --------------------------------------------------------------


@pytest.mark.parametrize(
    "name, draft",
    [
        ("DRAFT - Responses to SROGs.docx", True),
        ("Responses to SROGs DRAFT.docx", True),
        ("Responses to RFP Set One DRAFT 2026-09-23.docx", True),
        ("Responses to RFP DRAFT v2.docx", True),
        ("Responses to RFP Set One by A&P Operator Agent.docx", True),
        ("RFP re Draft Lease.pdf", False),
        ("Draftsman notes RFP1.pdf", False),
    ],
)
def test_a_draft_name_is_draft_as_its_own_token_first_last_or_before_a_date(name, draft):
    assert served_papers.draft_name(name) is draft


def test_ours_written_is_no_wider_than_before():
    assert not served_papers.ours_written("Responses to SROGs DRAFT.docx", "/Discovery")
    assert served_papers.ours_written("DRAFT Responses.docx", "/Discovery")


def test_a_no_text_scan_named_as_a_response_stays_a_scan():
    assert served_page.classify(_doc("Resp due RFP1 scan.pdf", ""), EV) == ("scan", None)


# ---- the other papers beside the served sets ---------------------------------------------------

UNREAD = "the Operator could not read what kind of paper this is; it was not answered."


def test_a_leftover_of_another_kind_beside_served_sets_holds_and_keeps_its_note(tmp_path):
    other = {"id": "o1", "name": "Defendant Discovery Set 2.pdf",
             "pages": [CAPTION + "Defendant's Notice of Deposition and Demand\n" + PARTY_LINES, "1. Example.\n", END]}  # fmt: skip
    data, jd = matter(tmp_path, [*three_docs(), other])
    assert stop_of(lambda: run(data, jd, Fake(THREE))) == "served_other_set"
    note = f'"{other["name"]}": {UNREAD}'
    assert _report(jd)["attorney_notes"] == [note] == served_earlier.attorney_lines(jd / served_find.REPORT)


def test_a_paper_of_ours_on_strong_evidence_of_no_readable_kind_proceeds_with_a_note(tmp_path):
    ours = {"id": "o1", "name": "Notice by A&P Operator Agent.pdf",
            "pages": [CAPTION + "Notice to Counsel\n" + PARTY_LINES, "1. Example.\n"]}  # fmt: skip
    data, jd = matter(tmp_path, [*three_docs(), ours])
    run(data, jd, Fake(THREE))
    assert len(served.read(jd, [CLIENT])) == 3
    assert served_earlier.attorney_lines(jd / served_find.REPORT) == [f'"{ours["name"]}": {UNREAD}']


def test_a_served_set_of_another_kind_alone_still_refuses(tmp_path):
    """served_other_set stays for real served papers this lane does not draft."""
    pages = [CAPTION + "Notice of Inspection of Land, Set One\n" + PARTY_LINES, "1. Example premises.\n", END]
    data, jd = matter(tmp_path, [{"id": "o1", "name": "Def notice.pdf", "pages": pages}])
    fake = Fake({})
    assert stop_of(lambda: run(data, jd, fake)) == "served_other_set" and fake.seen == []


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


def test_planned_reads_take_the_firm_as_the_seat_does(tmp_path):
    data, _jd = matter(tmp_path, _the_mix())
    reads = served_find.planned_reads(data, "Draft responses.", [CLIENT], provenance.our_firm(OUR_BLOCK))
    assert len(reads) == 1 and "Request for Production No. 1" in reads[0]


def test_name_kind_reads_abbreviations_through_underscores_and_digits():
    assert provenance.name_kind("DIS RFP1.pdf") == "requests_for_production"
    assert provenance.name_kind("Gamma_Resp_RFP_CORRECTED_9-24.docx") == "requests_for_production"
    assert provenance.name_kind("Def SROGs Set 2.pdf") == "special_interrogatories"
    assert provenance.name_kind("Def RFA2.pdf") == "requests_for_admission"
    assert provenance.name_kind("Def FROGs.pdf") == "form_interrogatories"
    assert provenance.name_kind("Letter to counsel.pdf") is None


# ---- the backstop: every paper with both party lines is accounted for, or the job holds ----------

HUNT_READ = {
    "Request for Production No. 1": values("requests_for_production", 3),
    "SPECIAL": values("special_interrogatories", 5),
}
SROG = {"id": "s1", "name": "Def SROG1.pdf", "pages": srog(5)}
SERVED = [HEAD, _labelled(3), END]


def _sibling(root, *, proof=(), delivered=None, plan_set_number=1) -> None:
    """An earlier Operator job on the same matter: its delivery proof and its delivered drafts."""
    sib = root / "job0"
    (sib / "data").mkdir(parents=True)
    (sib / "job.json").write_text(json.dumps({"matter_id": "M1"}))
    if proof:
        files = [{"file_id": i, "size": 1, "sha256": sha} for i, sha in proof]
        (sib / "verdict.json").write_text(json.dumps({"proof": {"files": files}}))
    if delivered:
        plan = {"set_type": "requests_for_production", "set_number": plan_set_number,
                "propounding_party": f"Defendant {DEF}"}  # fmt: skip
        (sib / "data" / served.PLAN).write_text(json.dumps({"sets": [plan]}))
        done = {"status": "filed", "drafted_by": "operator", "document": delivered}
        (sib / "data" / served.OUTCOMES).write_text(json.dumps({"sets": [done]}))


def _hunt(tmp_path, docs, **sib):
    data, jd = matter(tmp_path, docs)
    (jd / "job.json").write_text(json.dumps({"matter_id": "M1"}))
    if sib:
        _sibling(tmp_path, **sib)
    return data, jd


def _held(jd, name: str) -> None:
    rep = _report(jd)
    assert rep["stop"] == "served_other_set" and name in rep["reason"]
    assert f'"{name}": {UNREAD}' in rep["attorney_notes"]


@pytest.mark.parametrize(
    "title",
    ["Plaintiff's Responses to Request for Production, Set One\n",
     "PLAINTIFF'S RESPONSES TO REQUEST FOR PRODUCTION, SET ONE\n"],
    ids=["mixed case", "capitals"],
)  # fmt: skip
def test_b6_a_mistitled_served_set_with_a_proof_of_service_beside_an_srog_holds(tmp_path, title):
    """Titled as a response, our client responding, a proof of service: it answers no set read here."""
    paper = {"id": "p1", "name": "DIS RFP1.pdf", "pages": [CAPTION + title + PARTY_LINES, _labelled(3), END]}
    data, jd = _hunt(tmp_path, [SROG, paper])
    assert stop_of(lambda: run(data, jd, Fake(HUNT_READ))) == "served_other_set"
    _held(jd, "DIS RFP1.pdf")


def test_b7_a_proven_file_id_later_holding_their_served_set_is_read_as_served(tmp_path):
    """The delivery proof names the id with OUR bytes; the paper there now has other bytes."""
    data, jd = _hunt(tmp_path, [SROG, {"id": "p1", "name": "DIS RFP1.pdf", "pages": SERVED}], proof=[("p1", "aa")])
    run(data, jd, Fake(HUNT_READ))
    assert [s.source for s in served.read(jd, [CLIENT])] == ["s1", "p1"]


def test_b7_backstop_a_proven_id_and_sha_on_a_paper_with_a_proof_of_service_still_holds(tmp_path):
    """Even our own filing record does not let a paper carrying a proof of service and answering nothing vanish."""
    data, jd = _hunt(tmp_path, [SROG, {"id": "p1", "name": "DIS RFP1.pdf", "pages": SERVED}], proof=[("p1", "p1")])
    assert stop_of(lambda: run(data, jd, Fake(HUNT_READ))) == "served_other_set"
    _held(jd, "DIS RFP1.pdf")


def test_b8_a_delivered_name_colliding_with_a_served_set_holds(tmp_path):
    data, jd = _hunt(tmp_path, [SROG, {"id": "p1", "name": "DIS RFP1.pdf", "pages": SERVED}], delivered="DIS RFP1.pdf")
    assert stop_of(lambda: run(data, jd, Fake(HUNT_READ))) == "served_other_set"
    _held(jd, "DIS RFP1.pdf")


def test_a_stamped_name_on_a_paper_with_a_proof_of_service_holds(tmp_path):
    name = "DIS RFP1 forwarded by A&P Operator Agent.pdf"
    data, jd = _hunt(tmp_path, [SROG, {"id": "p1", "name": name, "pages": SERVED}])
    assert stop_of(lambda: run(data, jd, Fake(HUNT_READ))) == "served_other_set"
    _held(jd, name)


def test_a_stamp_in_the_text_never_makes_a_served_set_ours(tmp_path):
    pages = [HEAD + "Prepared by A&P Operator Agent\n", _labelled(3), END]
    data, jd = _hunt(tmp_path, [SROG, {"id": "p1", "name": "DIS RFP1.pdf", "pages": pages}])
    run(data, jd, Fake(HUNT_READ))
    assert [s.source for s in served.read(jd, [CLIENT])] == ["s1", "p1"]


def test_a_draft_named_copy_folds_into_its_set_and_the_set_is_read(tmp_path):
    docs = [SROG, {"id": "p0", "name": "DIS RFP1 DRAFT 2026-11-01.pdf", "pages": SERVED},
            {"id": "p1", "name": "DIS RFP1.pdf", "pages": SERVED}]  # fmt: skip
    data, jd = _hunt(tmp_path, docs)
    run(data, jd, Fake(HUNT_READ))
    got = served.read(jd, [CLIENT])
    assert [s.set_type for s in got] == ["special_interrogatories", "requests_for_production"]


def test_a_mistitled_set_alone_still_stops(tmp_path):
    mis = [CAPTION + "Plaintiff's Responses to Request for Production, Set One\n" + PARTY_LINES, _labelled(3), END]
    data, jd = _hunt(tmp_path, [{"id": "p1", "name": "DIS RFP1.pdf", "pages": mis}])
    assert stop_of(lambda: run(data, jd, Fake(HUNT_READ))) == "served_none"


# ---- the ledger: a sibling job's records never crash the finder ---------------------------------


@pytest.mark.parametrize(
    "files",
    [
        {"verdict.json": "{not json", "data/served-plan.json": "{", "data/served-outcomes.json": "["},
        {"verdict.json": json.dumps([{"proof": {"files": ["x", 3]}}])},
        {"verdict.json": json.dumps({"proof": {"files": "abc"}})},
        {"verdict.json": json.dumps({"proof": None})},
        {"data/served-plan.json": json.dumps({"sets": [{"set_type": "x", "set_number": "One"}]}),
         "data/served-outcomes.json": json.dumps({"sets": [{"status": "filed", "drafted_by": "operator",
                                                            "document": "d"}]})},
        {"job.json": "[1]"},
    ],
    ids=["corrupt", "proof-shape", "proof-str", "proof-null", "set-number-word", "job-json-list"],
)  # fmt: skip
def test_a_malformed_sibling_record_is_skipped_never_a_crash(tmp_path, files):
    data, jd = _hunt(tmp_path, [SROG])
    sib = tmp_path / "job0"
    (sib / "data").mkdir(parents=True)
    (sib / "job.json").write_text(json.dumps({"matter_id": "M1"}))
    for rel, body in files.items():
        (sib / rel).write_text(body)
    run(data, jd, Fake(HUNT_READ))
    assert [s.source for s in served.read(jd, [CLIENT])] == ["s1"]


def test_delivered_drafts_skip_a_set_number_that_is_not_a_whole_number(tmp_path):
    jd = tmp_path / "J1"
    jd.mkdir()
    (jd / "job.json").write_text(json.dumps({"matter_id": "M1"}))
    _sibling(tmp_path, delivered="a.docx", plan_set_number="One")
    assert served_earlier.delivered_drafts(jd) == []
    (tmp_path / "job0" / "data" / served.PLAN).write_text(json.dumps({"sets": [
        {"set_type": "requests_for_production", "set_number": 1, "propounding_party": "P"}]}))  # fmt: skip
    assert [d.document for d in served_earlier.delivered_drafts(jd)] == ["a.docx"]


# ---- replay parity: the $0 replay classifies as the seat does --------------------------------------


def test_the_replay_and_the_seat_classify_the_same_paper_the_same_way(tmp_path):
    """The seat's finder reads its own job dir's siblings; the replay works in another dir and
    passes the saved job's (``ledger_dir``). The paper's only strong evidence is that ledger."""
    name = "Gamma Responses to Delta RFP Set One.docx"
    paper = _our_paper("w1", name, "", "")  # no title, no stamp: ours only by the delivered-drafts ledger
    data, jd = _hunt(tmp_path, [SROG, _served_rfp(), paper], delivered=name)
    reads = {**HUNT_READ, **RFP_READ, "Gamma Example will produce": values("requests_for_production", 1)}
    run(data, jd, Fake(reads))
    work = tmp_path / "replay-work"
    work.mkdir()
    served_find.find(data, work, "Draft responses.", Fake(reads).reader(), clients=[CLIENT], ledger_dir=jd)
    seat, replay = _report(jd), _report(work)
    assert seat["candidates"] == replay["candidates"] and seat["attorney_notes"] == replay["attorney_notes"]
    assert served.read(jd, [CLIENT]) == served.read(work, [CLIENT])
    assert [s.source for s in served.read(jd, [CLIENT])] == ["s1", "p1"]
    assert served_find.planned_reads(data, "x", [CLIENT], None, jd) != served_find.planned_reads(data, "x", [CLIENT])
    # The falsifier: a replay without the saved job's ledger reads the paper as a served set.
    blind = tmp_path / "blind"
    blind.mkdir()
    with pytest.raises(served_find.Stop):
        served_find.find(data, blind, "Draft responses.", Fake(reads).reader(), clients=[CLIENT])


# ---- the final review (5ea80cd1c): narrowing, near names, and the deliberate costs ------------------


RESP_SROG = {"id": "r1", "name": "Resp SROGs.pdf", "pages": response("SPECIAL INTERROGATORIES"),
             "created": "2026-10-30T09:00:00"}  # fmt: skip
RFP_ONLY = "Please draft responses to the request for production on matter 100001."


def _outcome(data, jd, request="Please draft responses to the discovery on matter 100001.") -> str:
    try:
        run(data, jd, Fake(THREE), request=request)
        return "ok"
    except served_find.Stop as exc:
        return exc.code


def test_our_served_response_to_a_kind_the_request_does_not_name_never_holds_the_job(tmp_path):
    """The regression: their SROG, RFP and form sets, our served SROG response, a request for the RFP only."""
    data, jd = matter(tmp_path, [*three_docs(), RESP_SROG])
    assert _outcome(data, jd, RFP_ONLY) == "ok"
    assert [(s.set_type, s.source) for s in served.read(jd, [CLIENT])] == [("requests_for_production", "p1")]


def test_our_served_response_to_a_set_read_here_answers_it(tmp_path):
    data, jd = matter(tmp_path, [*three_docs(), RESP_SROG])
    assert _outcome(data, jd) == "ok"
    assert [s.set_type for s in served.read(jd, [CLIENT])] == ["requests_for_production", "form_interrogatories"]


@pytest.mark.parametrize("sub", ["set not in the file", "same day as its set"])
def test_our_served_response_that_answers_no_set_read_here_holds(tmp_path, sub):
    """Deliberate: a served response of ours that answers nothing this finder read is held, never dropped."""
    if sub == "set not in the file":
        docs = [d for d in three_docs() if d["id"] not in ("s1", "s1b")] + [RESP_SROG]
    else:
        docs = [*three_docs(), dict(RESP_SROG, created="2026-10-01T00:00:00")]
    data, jd = matter(tmp_path, docs)
    assert _outcome(data, jd) == "served_other_set"
    _held(jd, "Resp SROGs.pdf")


def test_known_cost_a_served_response_to_an_earlier_set_that_is_a_no_text_scan_holds_the_job(tmp_path):
    """KNOWN COST, deliberate: the earlier SROG set is a no-text scan, so code reads no set for our served
    SROG response to answer, and the job holds rather than guess. SMD names the set by hand (served.json)."""
    scan = {"id": "s9", "name": "Def SROGs Set One scan.pdf", "pages": [""]}
    docs = [d for d in three_docs() if d["id"] not in ("s1", "s1b")] + [scan, RESP_SROG]
    data, jd = matter(tmp_path, docs)
    assert _outcome(data, jd) == "served_other_set"
    _held(jd, "Resp SROGs.pdf")
    assert next(r for r in _report(jd)["candidates"] if r.get("id") == "s9")["kind"] == "scan"


GARBLED = [CAPTION + "DEFENDANT'S REQUESTS F0R PR0DUCTI0N, SET ONE\n" + PARTY_LINES, bare("X", 5), END]


@pytest.mark.parametrize("name", ["Def discovery.pdf", "Def RFA and RFP Set One.pdf"], ids=["no kind", "name kind"])
def test_a_garbled_set_beside_other_sets_is_never_narrowed_away(tmp_path, name):
    """Narrowing settles only a set whose kind its title gives; a garbled one, or one typed by its name, holds."""
    data, jd = matter(tmp_path, [*three_docs(), {"id": "g1", "name": name, "pages": GARBLED}])
    assert _outcome(data, jd, RFP_ONLY) == "served_other_set"
    assert name in _report(jd)["reason"]


def test_a_set_titled_in_capitals_of_a_kind_not_asked_for_is_narrowed_out(tmp_path):
    data, jd = matter(tmp_path, three_docs())
    assert _outcome(data, jd, RFP_ONLY) == "ok"
    assert [s.source for s in served.read(jd, [CLIENT])] == ["p1"]


@pytest.mark.parametrize(
    "asking, ours",
    [
        ("Plaintiff GAMMA EXAMPLE", True),
        ("Plaintiff and Cross-Complainant Gamma Example", True),
        ("Defendant GAMMA EXAMPLE TRUST", False),
        ("GAMMA EXAMPLE JR.", False),
        ("GAMMA EXAMPLE, INC.", False),
    ],
)
def test_one_client_is_an_exact_name_once_normalised(asking, ours):
    assert provenance.one_client(asking, [CLIENT]) is ours
    assert provenance.one_client("Gamma Example", [CLIENT, "GAMMA EXAMPLE"]) is False  # twice: not one client


def test_a_set_asked_by_a_near_name_of_our_client_holds(tmp_path):
    pages = [p.replace("REQUESTING PARTY: Defendant " + DEF, "REQUESTING PARTY: Defendant GAMMA EXAMPLE TRUST")
             .replace("DELTA EXAMPLE CORPORATION'S SPECIAL", "GAMMA EXAMPLE TRUST'S SPECIAL") for p in srog(5)]  # fmt: skip
    data, jd = matter(tmp_path, [*three_docs(), {"id": "t1", "name": "Trust SROGs Set One.pdf", "pages": pages}])
    assert _outcome(data, jd) == "served_other_set"
    _held(jd, "Trust SROGs Set One.pdf")
