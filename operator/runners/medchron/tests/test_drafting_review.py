"""The 2026-10-12 incident, as falsifiers: each defect the delivered discovery
set carried, and the stage that now stops each one before an attorney sees it.

The delivered file combined three sets in one document with one proof of
service, carried a section 2030.050 declaration over a set of exactly 35
special interrogatories with no prior set served (a matter note about the
other side's discovery read as a prior set), ignored the request's pleading
paper, used defined terms in lower case, and filed notes that contradicted the
document. Every name and sentence below is invented (Example / Exampletown).
"""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

from drafting_testkit import ScriptedClient, make_inputs, make_job, seat_with, standard_docs
from medchron_testkit import PRICING, make_pdf
from medchron.drafting import firm as firm_mod, pleading, render, review, sets
from medchron.drafting.run import DraftingRun

CHECKER = Path(__file__).resolve().parents[3] / "templates" / "drafting" / "drafting_gate_check.py"
REQUEST = (
    "Please draft plaintiff's RFAs set one, RFPs set one and special interrogatories set one to the defendant, "
    "and put the requests into the caption so that they are on pleading paper."
)
KINDS = {
    "Requests for Admission": ("REQUEST FOR ADMISSION", "REQUESTS FOR ADMISSION", "Admit that YOU owned the PREMISES."),
    "Requests for Production": (
        "REQUEST FOR PRODUCTION",
        "REQUESTS FOR PRODUCTION",
        "All DOCUMENTS showing the PREMISES.",
    ),
    "Special Interrogatories": (
        "SPECIAL INTERROGATORY",
        "SPECIAL INTERROGATORIES",
        "IDENTIFY each PERSON who inspected the premises before the incident.",
    ),
}


@pytest.fixture(autouse=True)
def _checker(monkeypatch):
    monkeypatch.setenv("SMD_DRAFTING_GATE_CHECK", str(CHECKER))


@pytest.fixture
def pricing(tmp_path: Path) -> Path:
    p = tmp_path / "pricing.json"
    p.write_text(json.dumps(PRICING), encoding="utf-8")
    return p


def one_set(label: str, n: int = 3) -> str:
    item, title, text = KINDS[label]
    items = "\n\n".join(f"{item} NO. {i}\n\n{text}" for i in range(1, n + 1))
    return (
        "| | |\n| --- | --- |\n| GAMMA EXAMPLE, Plaintiff, v. DELTA EXAMPLE, Defendant. | Case No. CV-0001 |\n"
        f"| | **PLAINTIFF GAMMA EXAMPLE'S {title} TO DEFENDANT DELTA EXAMPLE, SET ONE** |\n\n"
        "| | |\n| --- | --- |\n| PROPOUNDING PARTY: | GAMMA EXAMPLE |\n| RESPONDING PARTY: | DELTA EXAMPLE |\n"
        "| SET NO.: | ONE |\n\n"
        '# DEFINITIONS\n\n1. "INCIDENT" means the fall (ER record 1-15-26, p. 1).\n\n2. "PREMISES" means the store.\n\n'
        '3. "YOU" means DELTA EXAMPLE.\n\n4. "PERSON" means a natural person.\n\n'
        '5. "DOCUMENTS" means writings under Evidence Code section 250.\n\n'
        f"# {title}\n\n{items}\n\nDated: {{{{ATTORNEY: date}}}}\n\n=== ATTORNEY NOTES ===\n\n## REQUEST BASIS\n\nNone.\n"
    )


#: The other side's discovery, mentioned in a matter note: the incident's prior "set".
MEMO_DIGEST = (
    "## MEDICAL | ER record 1-15-26 | 01/15/2026 | (/Medical)\nFall (FILE: ER record 1-15-26, p. 1)\n\n"
    "## DISCOVERY-SETS\nSprogs uploaded | not readable | Gamma Example -> Delta Example | special interrogatories | "
    "not readable | undated | FILE: Note re discovery 3-1-26, p. 1\n\n## FILES-SEEN\n- ER record 1-15-26 digested\n"
)


class Client(ScriptedClient):
    def __init__(self, *a, digest: str = MEMO_DIGEST, **kw) -> None:
        super().__init__(*a, **kw)
        self.digest = digest

    def _answer(self, params):
        if "DIGEST-PROMPT" in json.dumps(params.get("system")):
            return self.digest
        return super()._answer(params)

    def count(self, marker: str) -> int:
        return sum(1 for c in self.calls if marker in json.dumps(c.get("system")))


def docs_with_note():
    return [
        *standard_docs(),
        (
            "d9",
            "Note re discovery 3-1-26.pdf",
            make_pdf(["DRAFTED FORM ROGS, RFAS AND SPROGS UPLOADED TO THE TOOL."]),
            "f-plead",
        ),
    ]


def _run(tmp_path, pricing, client, request=REQUEST, inputs_over=None, seat=None):
    inputs = tmp_path / "inputs"
    if not inputs.is_dir():
        make_inputs(inputs, **(inputs_over or {}))
    jd = make_job(tmp_path / "job", cls="discovery_set")
    doc = json.loads((jd / "job.json").read_text())
    doc["request_text"] = request
    (jd / "job.json").write_text(json.dumps(doc))
    seat = seat or seat_with(docs_with_note())
    r = DraftingRun(
        jd,
        inputs_dir=str(inputs),
        pricing=str(pricing),
        seat_factory=lambda: seat,
        client=client,
        log=lambda _m: None,
        readback_pause=0.0,
    )
    return r, r.run(), seat


def _docx(path: Path):
    import docx

    return docx.Document(io.BytesIO(path.read_bytes()))


# ---- the request's words -----------------------------------------------------------------


def test_the_request_names_three_sets_in_its_own_order():
    assert [u.key for u in sets.variants(REQUEST)] == [
        "requests_for_admission",
        "requests_for_production",
        "special_interrogatories",
    ]
    assert [u.key for u in sets.variants("Draft SROGs set two")] == ["special_interrogatories"]
    assert sets.variants("Draft a memo on liability") == []


@pytest.mark.parametrize(
    "text, wants",
    [
        (REQUEST, True),
        ("Please prepare them on pleading paper.", True),
        ("Draft special interrogatories.", False),
        ("draft it as a plain Word .docx - not line-numbered pleading paper", False),
    ],
)
def test_a_pleading_request_is_read_from_the_words(text, wants):
    assert pleading.wants_pleading(text) is wants


# ---- end to end: the incident's request -----------------------------------------------------


def test_three_sets_become_three_documents_each_with_one_proof_of_service_on_pleading_paper(tmp_path, pricing):
    client = Client(drafts={label: one_set(label, 35 if label.startswith("Special") else 4) for label in KINDS})
    r, v, seat = _run(tmp_path, pricing, client)
    assert v.outcome == "delivered", (v.stage, v.reason)
    roles = [f["role"] for f in v.files]
    assert roles == ["draft", "draft", "draft", "attorney_notes"]
    from medchron.demand import deliver
    from medchron.drafting import format_check

    spec = pleading.spec_for(r.firm.data["format"])
    for f in v.files[:3]:
        path = deliver.out_dir(r.data) / f["name"]
        d = _docx(path)
        text = format_check.doc_text(d)
        assert text.upper().count("PROOF OF SERVICE") == 1, f["name"]
        assert len(sets.kinds_in(text)) == 1, f["name"]
        # 35 special interrogatories and a matter note about the other side's discovery: no declaration
        assert "DECLARATION" not in text.upper(), f["name"]
        assert pleading.check(d, path.read_bytes(), spec) == [], f["name"]
    # the composes each drafted ONE set, told so by the job
    assert client.count("COMPOSE-PROMPT") >= 3
    # defined terms in capitals: "premises" and "incident" in the interrogatories were lower case
    sr = next(f for f in v.files if "Special Interrogatories" in f["name"])
    text = format_check.doc_text(_docx(deliver.out_dir(r.data) / sr["name"]))
    assert "the PREMISES before the INCIDENT" in text
    notes = (r.data / "notes-final.md").read_text(encoding="utf-8")
    assert "is a note about discovery, not a served set, so paragraph 4 does not count it" in notes
    assert "## Request basis, record gaps and your decisions" in notes
    assert "Left for the attorney by the auditor" not in notes
    assert json.loads((r.data / "review.json").read_text())["must_fix"] == 0


def test_a_review_must_fix_holds_the_job_for_smd_and_files_nothing(tmp_path, pricing):
    bad = "| MUST-FIX | 1 statements | The declaration swears the set exceeds 35; it has 35. | Document 3, para 3 |\nVERDICT: 1 MUST-FIX, 0 SHOULD-FIX, 0 NOTE"
    client = Client(drafts={label: one_set(label) for label in KINDS}, review=bad)
    r, v, seat = _run(tmp_path, pricing, client)
    assert v.outcome == "failed" and v.reason.startswith("review_held: ") and v.stage == "review"
    assert seat.created == [] and seat.sent == []
    paid = client.count("INDEPENDENT PRE-DELIVERY REVIEW")
    _r, v2, _ = _run(tmp_path, pricing, client, seat=seat)  # a resume holds again, paying nothing twice
    assert v2.outcome == "failed" and v2.reason.startswith("review_held: ")
    assert client.count("INDEPENDENT PRE-DELIVERY REVIEW") == paid
    assert seat.created == []


def test_the_audit_and_the_review_ask_for_high_effort(tmp_path, pricing):
    """claude-opus-5-5 defaults to medium effort; the drafter's audit (the gate
    that passed the false declaration) and the review state high explicitly."""
    client = Client(draft=one_set("Special Interrogatories"))
    _run(tmp_path, pricing, client, request="Please draft special interrogatories, set one.")
    for marker in ("AUDIT-PROMPT", "INDEPENDENT PRE-DELIVERY REVIEW"):
        calls = [c for c in client.calls if marker in json.dumps(c.get("system"))]
        assert calls and all((c.get("output_config") or {}).get("effort") == "high" for c in calls), marker


# ---- the review of the corrected 2026-10-12 run: assembly and wording defects -------------


def _clean_paper() -> str:
    return one_set("Requests for Admission").split("=== ATTORNEY NOTES")[0] + "\n# PROOF OF SERVICE\n\nServed.\n"


def test_a_second_document_that_landed_in_the_notes_is_must_fix():
    """(a) Documents 2 and 3 were split into the notes file after the first
    notes marker, with no proof of service."""
    leaked = (
        "## REQUEST BASIS\n\nNone.\n\n=== DOCUMENT 2 OF 3: REQUESTS FOR PRODUCTION ===\n\n"
        "REQUEST FOR PRODUCTION NO. 1\n\nAll DOCUMENTS.\n\nREQUEST FOR PRODUCTION NO. 2\n\nAll photos.\n"
    )
    found = review.code_findings("discovery_set", [_paper(_clean_paper())], "draft RFAs", "", {}, True, notes_md=leaked)
    assert any(f.grade == review.MUST and f.check == "5 assembly" for f in found), found


def test_three_requested_sets_with_one_document_is_must_fix():
    found = review.code_findings("discovery_set", [_paper(_clean_paper())], REQUEST, "", {}, True)
    missing = [f for f in found if f.check == "2 the sets asked for"]
    assert {f.finding.split("asks for ")[1].split(";")[0] for f in missing} == {
        "Requests for Production",
        "Special Interrogatories",
    }


def test_the_systems_vocabulary_in_the_notes_is_must_fix():
    """(b) "Under the job rule, code counts the interrogatories"."""
    found = review.code_findings(
        "discovery_set",
        [_paper(_clean_paper())],
        "draft RFAs",
        "",
        {},
        True,
        notes_md="Under the job rule, code counts the interrogatories.",
    )
    assert any(f.check == "9 notes wording" and f.grade == review.MUST for f in found)


def test_a_marker_standing_as_a_request_is_must_fix():
    """(d) A bare NOT IN RECORD marker left as a numbered request."""
    text = _clean_paper().replace(
        "REQUEST FOR ADMISSION NO. 2\n\nAdmit that YOU owned the PREMISES.",
        "REQUEST FOR ADMISSION NO. 2\n\n{{NOT IN RECORD: the date, not in the file}}",
    )
    found = review.code_findings("discovery_set", [_paper(text)], "draft RFAs", "", {}, True)
    assert any("is a marker, not a request" in f.finding and f.grade == review.MUST for f in found), found


def test_a_backticked_slot_renders_without_its_backticks(tmp_path):
    """(d) Stray backticks around a {{FILL}} slot."""
    from drafting_testkit import firm_data
    from medchron.drafting import format_check

    md = "# PROOF OF SERVICE\n\nOn `{{FILL: date of service | at service}}`, I served it.\n"
    path, _ = render.render(md, "memo", tmp_path / "x.docx", firm_data()["format"])
    text = format_check.doc_text(_docx(path))
    assert "`" not in text and "{{FILL: date of service | at service}}" in text


def test_the_notes_prompt_and_the_review_prompt_carry_the_three_wording_rules():
    """(b), (c), (e): the model-written notes never use the system's
    vocabulary, adopt a fact the request states, and run each request in the
    direction its words run; the review grades each of those MUST-FIX."""
    for phrase in ("never mention the job", "adopt it as stated", "direction its words run"):
        assert phrase in review.NOTES_PROMPT
    for phrase in ("on either reading", "opposite direction", "CONSISTENCY ACROSS THE DOCUMENTS"):
        assert phrase in review.REVIEW_PROMPT


def test_a_must_fix_without_a_citation_is_graded_should_fix():
    rows = review._rows("| MUST-FIX | 2 request | Something seems off. |  |\nVERDICT: 0 MUST-FIX")
    assert [f.grade for f in rows] == [review.SHOULD]


def test_a_review_that_never_finishes_fails_without_filing(tmp_path, pricing):
    client = Client(drafts={label: one_set(label) for label in KINDS}, review="| NOTE | x | y | z |")
    r, v, seat = _run(tmp_path, pricing, client)
    assert v.outcome == "failed" and "review did not complete" in v.reason
    assert seat.created == []


def test_two_kinds_of_set_in_one_document_is_held(tmp_path, pricing):
    combined = one_set("Special Interrogatories").split("Dated:")[0] + one_set("Requests for Admission")
    client = Client(draft=combined)
    r, v, seat = _run(tmp_path, pricing, client, request="Please draft special interrogatories, set one.")
    assert v.outcome == "failed" and v.reason.startswith("review_held: ")
    assert "kinds of set in one file" in v.reason
    assert seat.created == []


def test_a_pleading_request_the_firm_cannot_honor_is_held(tmp_path, pricing):
    from drafting_testkit import firm_data

    fmt = dict(firm_data()["format"])
    fmt.pop("pleading")
    client = Client(drafts={label: one_set(label) for label in KINDS})
    r, v, seat = _run(tmp_path, pricing, client, inputs_over={"format": fmt})
    assert v.outcome == "failed" and "pleading paper" in v.reason
    assert seat.created == []


def test_a_request_without_pleading_words_stays_on_plain_paper(tmp_path, pricing):
    from medchron.demand import deliver

    client = Client(draft=one_set("Special Interrogatories"))
    r, v, _ = _run(tmp_path, pricing, client, request="Please draft special interrogatories, set one.")
    assert v.outcome == "delivered", (v.stage, v.reason)
    assert [f["role"] for f in v.files] == ["draft", "attorney_notes"]
    d = _docx(deliver.out_dir(r.data) / v.files[0]["name"])
    assert "Pleading" not in "".join(p.text for p in d.sections[0].header.paragraphs)
    assert pleading.check(d, (deliver.out_dir(r.data) / v.files[0]["name"]).read_bytes(), pleading.Spec()) != []


# ---- the code checks, directly ----------------------------------------------------------------


def _paper(text: str, paper: str = "plain") -> review.Paper:
    return review.Paper("", "Discovery Set", text, paper)


def test_a_declaration_over_exactly_35_is_must_fix():
    class F:
        def attachment(self, k):
            from drafting_testkit import DECL, POS

            return {"decl_2030_050": DECL, "pos": POS}[k]

    body = one_set("Special Interrogatories", 35).split("=== ATTORNEY NOTES")[0]
    decl = render._fill_decl(F().attachment("decl_2030_050"), render.decl_decision(body, ""), body)
    planted = body + "\n\n" + decl + "\n\n" + F().attachment("pos")
    found = review.code_findings("discovery_set", [_paper(planted)], "draft special interrogatories", "", {}, True)
    assert any(f.grade == review.MUST and "2030.050" in f.check for f in found), found


def test_lower_case_defined_terms_are_capitalized_outside_quotations():
    doc = (
        '# DEFINITIONS\n\n1. "INCIDENT LOCATION" means the aisle.\n\n2. "INCIDENT" means the fall.\n\n'
        "# SPECIAL INTERROGATORIES\n\nSPECIAL INTERROGATORY NO. 1\n\nDescribe the incident location on the day of the "
        'incident, and quote the sign that read "incident area".\n\nDated: x\n\nAttorneys for the incident\n'
    )
    out, changes = sets.enforce_caps(doc)
    assert "Describe the INCIDENT LOCATION on the day of the INCIDENT" in out
    assert '"incident area"' in out and "Attorneys for the incident" in out
    assert len(changes) == 2 and sets.lowercase_uses(out) == []
    assert sets.lowercase_uses(doc) == ["INCIDENT LOCATION", "INCIDENT"]


def test_a_capitalized_term_never_defined_is_noted():
    doc = (
        '# DEFINITIONS\n\n1. "SURVEILLANCE VIDEO" means the store video.\n\n# SPECIAL INTERROGATORIES\n\n'
        "SPECIAL INTERROGATORY NO. 1\n\nIDENTIFY the custodian of the SURVEILLANCE FOOTAGE.\n\nDated: x\n"
    )
    assert sets.undefined_caps(doc) == ["SURVEILLANCE FOOTAGE"]


def test_the_firm_pleading_block_is_validated():
    from drafting_testkit import firm_data

    data = {**firm_data(), "inputs": {}}
    data["format"]["pleading"] = {"lines": 28, "line_pitch_pt": 24, "margins_in": [0.5, 0.5, 2.0, 1.5], "x": 1}
    problems = firm_mod.validate(data)
    assert any("unknown key" in p for p in problems)
    data["format"]["pleading"].pop("x")
    assert any("do not fit" in p for p in firm_mod.validate(data))
