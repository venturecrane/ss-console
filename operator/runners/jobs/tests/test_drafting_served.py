"""Release 3: discovery to the other side, and responses to the sets SMD names.

The falsifiers, each from the 2026-11-19 requests (every value invented):

1. "Draft discovery to send to the defense" ran 32 minutes, paid, then failed
   ``party_unknown``. Now "the defense" is every defendant on the caption, the
   plan is made just after preflight, and its stops end the job before the
   estimate and the transcription.
2. "The defense's expert" and "defense counsel" never mean every defendant.
3. A request naming no kind of set takes the firm's default sets (a firm
   input, never a constant), else fails ``request_incomplete`` before spend.
4. A response job reads only SMD's ``served.json``: none yet is
   ``served_unknown`` before spend, with the frozen rules for SMD's page; a set
   type outside the closed list is ``served_other_set``; anything malformed is
   ``served_invalid``.
5. One document per set the Operator drafts, each composed with that set's
   verbatim requests; the delivered job lists every served set as filed or
   being prepared by us.
6. The responses' numbering is checked in code before filing: a gap, a double
   response or a count off the served set's holds the job.
7. What worked still works: a set request naming its parties and sets plans as
   before, and a frozen plan resumes without planning again.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from drafting_testkit import CAPTION, make_inputs, make_job, seat_with, standard_docs
from jobs.drafting import outcome, parties, review as review_mod, served, sets
from jobs.drafting.run import DraftingRun
from test_drafting_review import Client

DELTA, CITY = "DELTA EXAMPLE CORPORATION", "CITY OF EXAMPLETOWN"
DEFENSE = "Please draft discovery to send to the defense on this one.\n\nThanks"
DEFAULTS = {"sets": ["special_interrogatories", "requests_for_production", "requests_for_admission"]}
SROG, RFP, RFA = "Special Interrogatories", "Requests for Production", "Requests for Admission"


@pytest.fixture(autouse=True)
def _checker(monkeypatch):
    from test_drafting_review import CHECKER

    monkeypatch.setenv("SMD_DRAFTING_GATE_CHECK", str(CHECKER))


@pytest.fixture
def pricing(tmp_path):
    from jobs_testkit import PRICING

    p = tmp_path / "pricing.json"
    p.write_text(json.dumps(PRICING), encoding="utf-8")
    return p


class _Estimated(Exception):
    """Raised at the estimate: the test reads what was settled before it."""


def _record(*others: str) -> dict:
    return {
        "case_number": "CV-0001",
        "court": "Superior Court",
        "county": "Exampletown",
        "parties": [{"side": "client", "name": "Gamma Example"}, *({"side": "other", "name": o} for o in others)],
        "attorney_email": "",
    }


def _runner(tmp_path, pricing, request, cls, client, seat, defaults=None):
    # The firm input, drafting-firm.yaml ``discovery_defaults``, loaded and validated by firm.py.
    make_inputs(tmp_path / "inputs", **({sets.DEFAULTS_KEY: defaults} if defaults is not None else {}))
    jd = make_job(tmp_path / "job", cls=cls)
    doc = json.loads((jd / "job.json").read_text())
    (jd / "job.json").write_text(json.dumps({**doc, "request_text": request}))
    r = DraftingRun(
        jd,
        inputs_dir=str(tmp_path / "inputs"),
        pricing=str(pricing),
        seat_factory=lambda: seat,
        client=client,
        log=lambda _m: None,
        readback_pause=0.0,
    )
    return r


def _stop_at_finish(monkeypatch, seen):
    """Stop a propounded-set job where it plans, after its transcription and
    its caption stage (``finish``), as on origin/main: the test reads what was
    settled and in which order."""

    def at_finish(self, digest):
        self.units()  # the plan, made here as on main
        seen["units"] = self._state().get("units")
        seen["state"] = list(self._state())
        raise _Estimated()

    monkeypatch.setattr(DraftingRun, "_finish", at_finish)


def _stop_at_estimate(monkeypatch, seen):
    """Stop the job just after its plan, before the transcription: the test
    reads what was settled (the estimate has run before it, at no cost)."""
    plan = DraftingRun._plan_before_spend

    def after_plan(self):
        plan(self)
        seen["units"] = self._state().get("units")
        seen["state"] = sorted(self._state())
        raise _Estimated()

    monkeypatch.setattr(DraftingRun, "_plan_before_spend", after_plan)


def _two_defendants():
    cap = json.dumps({**CAPTION, "defendants": [DELTA, CITY]})
    return Client(caption=[cap]), seat_with(
        standard_docs(), record=_record("Delta Example Corporation", "City of Exampletown")
    )


def _calls_other_than_the_caption_read(client) -> list[str]:
    return [
        json.dumps(c.get("system"))[:60] for c in client.calls if "COPY THE CAPTION" not in json.dumps(c.get("system"))
    ]


# ---- 1. discovery to the defense: the plan before any spend --------------------------------


def test_discovery_to_the_defense_plans_three_sets_to_each_defendant_at_the_caption_stage(
    tmp_path, pricing, monkeypatch
):
    """The 11-19 request, at the caption stage where main plans: "the defense"
    is every defendant on the court's caption."""
    seen: dict = {}
    _stop_at_finish(monkeypatch, seen)
    client, seat = _two_defendants()
    r = _runner(tmp_path, pricing, DEFENSE, "discovery_set", client, seat, defaults=DEFAULTS)
    r.run()
    plan = seen["units"]
    assert [(p["label"], p["party"]) for p in plan] == [(k, p) for p in (DELTA, CITY) for k in (SROG, RFP, RFA)]


def test_a_propounded_set_reads_its_caption_after_the_transcription_when_a_paper_may_still_gain_text(
    tmp_path, pricing, monkeypatch
):
    """FALSIFIER (final review, 11-19): an early caption read froze the wrong
    paper's caption and wrote corrections from it. A later complaint with no
    text yet (a scan) could outrank the paper visible now, so the caption is
    read once, after the transcription, and nothing plans before it."""
    from drafting_testkit import make_pdf

    seen: dict = {}
    _stop_at_finish(monkeypatch, seen)
    client, _seat = _two_defendants()
    scan = ("d4", "First Amended Complaint 3-1-26.pdf", make_pdf([""]), "f-plead")
    seat = seat_with([*standard_docs(), scan], record=_record("Delta Example Corporation", "City of Exampletown"))
    r = _runner(tmp_path, pricing, DEFENSE, "discovery_set", client, seat, defaults=DEFAULTS)
    r.run()
    order = seen["state"]
    assert order.index("transcribe") < order.index("caption") and "plan" not in order
    assert not (r.data / "caption-plan.json").exists()
    assert client.count("COPY THE CAPTION") == 1


def test_a_propounded_set_plans_before_the_transcription_when_its_caption_paper_is_settled(
    tmp_path, pricing, monkeypatch
):
    """B0: the complaint already carries its text and nothing could outrank it,
    so the caption is read once, before the file, and the plan follows it."""
    seen: dict = {}
    _stop_at_finish(monkeypatch, seen)
    client, seat = _two_defendants()
    r = _runner(tmp_path, pricing, DEFENSE, "discovery_set", client, seat, defaults=DEFAULTS)
    r.run()
    order = seen["state"]
    assert order.index("caption") < order.index("plan") < order.index("transcribe")
    assert client.count("COPY THE CAPTION") == 1 and len(seen["units"]) == 6


def test_a_resumed_job_reads_the_caption_once_and_reuses_its_plan(tmp_path, pricing, monkeypatch):
    seen: dict = {}
    _stop_at_finish(monkeypatch, seen)
    client, seat = _two_defendants()
    r = _runner(tmp_path, pricing, DEFENSE, "discovery_set", client, seat, defaults=DEFAULTS)
    r.run()
    first = seen["units"]
    again = DraftingRun(r.job.job_dir, inputs_dir=str(tmp_path / "inputs"), pricing=str(pricing),
                        seat_factory=lambda: seat, client=client, log=lambda _m: None)  # fmt: skip
    again.run()  # a resume: the plan on disk is reused, the caption not read again
    assert seen["units"] == first and len(first) == 6
    assert client.count("COPY THE CAPTION") == 1


# ---- 2. "the defense" and its look-alikes ---------------------------------------------------


@pytest.mark.parametrize(
    "text",
    ["Please draft discovery to send to the defense.", "Serve these on the defence", "propound to the defense today"],
)
def test_the_defense_means_every_defendant(text):
    assert parties._DEFENSE.search(text)
    assert parties.resolve(text, [DELTA, CITY]).planned == [DELTA, CITY]


@pytest.mark.parametrize(
    "text",
    [
        "Draft RFAs to Delta Example Corporation on the defense of immunity",
        "Draft SROGs; focus on the defense of comparative fault",
        "Draft RFAs to the defense firm",
        "Draft RFPs, the defense has refused to produce",
        "Draft RFAs like the ones in the defense in the other case",
    ],
)
def test_the_defense_outside_a_sending_phrase_or_beside_a_named_party_never_widens(text):
    """FALSIFIER (review, 11-19): each of these planned BOTH defendants."""
    try:
        got = parties.resolve(text, [DELTA, CITY]).planned
    except parties.PartyUnknown:
        return  # names none of several: stops for SMD, never every defendant
    assert got != [DELTA, CITY], got


NESTED = ["ALDO BRENT QUILL EXAMPLE", "QUILL EXAMPLE", "DOES 1-25"]


def test_a_defendant_whose_name_sits_inside_another_is_two_parties():
    """FALSIFIER (the 11-19 replay): one defendant's whole name inside the
    other's was read as one party, so three sets drafted to nobody."""
    got = parties.resolve("Please draft discovery to send to the defense.", NESTED)
    assert got.planned == ["ALDO BRENT QUILL EXAMPLE", "QUILL EXAMPLE"]
    found = [sets.Unit(k, label) for k, label, _pat in sets.VARIANTS]
    assert len(parties.plan_units(found, got.planned)) == 6


def test_an_aka_or_dba_pair_is_one_party():
    for pair in (["ZETA EXAMPLE aka ZETTA EXAMPLE", "ZETTA EXAMPLE", "CITY OF EXAMPLETOWN"],
                 ["OMEGA EXAMPLE dba OMEGA PLUMBING", "OMEGA PLUMBING", "CITY OF EXAMPLETOWN"]):  # fmt: skip
        got = parties.resolve("Please draft discovery to send to the defense.", pair)
        assert len(got.planned) == 2 and got.planned[1] == "CITY OF EXAMPLETOWN", (pair, got.planned)


def test_the_backstop_never_plans_fewer_parties_than_the_caption_names(monkeypatch):
    """A forced mismatch (a de-duplication that drops one) stops for SMD; it never drafts the wrong scope."""
    monkeypatch.setattr(parties, "_whole", lambda values: [values[0]])
    with pytest.raises(parties.PartyUnknown, match="the caption names 2, the plan found 1"):
        parties.resolve("Please draft discovery to send to the defense.", NESTED)


def test_the_defense_when_our_client_is_a_defendant_stops_for_smd(tmp_path):
    """FALSIFIER: our client defends, so "the defense" is our own side; it must
    never select the plaintiffs."""
    data = tmp_path / "data"
    data.mkdir()
    cap = {
        "fields": {"plaintiffs": [{"value": "ZETA EXAMPLE"}], "defendants": [{"value": "GAMMA EXAMPLE"}]},
        "parties": [{"side": "client", "name": "Gamma Example"}, {"side": "other", "name": "Zeta Example"}],
    }
    (data / "caption.json").write_text(json.dumps(cap))
    (data / "facts.json").write_text(json.dumps({"client_names": ["Gamma Example"]}))
    with pytest.raises(parties.PartyUnknown, match="our client is a defendant"):
        parties.served_on(data, tmp_path, "Please draft discovery to send to the defense")


@pytest.mark.parametrize(
    "text",
    [
        "Please answer the defense's expert disclosures with special interrogatories to Delta Example Corporation",
        "Send the RFAs to the defense counsel for Delta Example Corporation",
        "Draft SROGs to Delta Example Corporation; the defense attorneys asked for them",
        "Draft special interrogatories on the defense experts for Delta Example Corporation",
        "Draft RFAs to Delta Example Corporation; copy defense counsel",
    ],
)
def test_the_defense_followed_by_counsel_an_expert_or_a_possessive_is_not_every_defendant(text):
    assert not parties._DEFENSE.search(text) and not parties._EVERY.search(text)
    got = parties.resolve(text, [DELTA, CITY]).planned
    assert len(got) == 1 and parties.same(got[0], DELTA), got


# ---- 3. the caption names nobody: party_unknown with nothing spent ---------------------------


def test_to_the_defendants_with_no_court_paper_and_no_other_party_drafts_as_main_did(tmp_path, pricing, monkeypatch):
    """FALSIFIER (final review, 11-19): a matter whose only complaint is a scan
    stopped before the transcription; main drafted it. It still does."""
    seen: dict = {}
    _stop_at_finish(monkeypatch, seen)
    seat = seat_with(standard_docs()[:2], record=_record())  # no court paper in the file, no other side recorded
    r = _runner(tmp_path, pricing, "Please draft RFAs set one to the defendants.", "discovery_set", Client(), seat)
    v = r.run()
    assert "party_unknown" not in (v.reason or ""), v.reason
    assert seen["units"] == [{"key": "", "variant": "requests_for_admission", "label": ""}]


# ---- 3b. a request naming no kind of set ------------------------------------------------------


def test_no_set_named_and_no_firm_default_drafts_the_standard_package(tmp_path, pricing, monkeypatch):
    """FALSIFIER (final review, 11-19): with no default sets authored (the firm
    seat today), a request naming no set stopped request_incomplete. Since
    2026-11-23 "discovery" naming no kind is the standard package, to the party
    it names (``set_package.py``), never a question to the attorney."""
    seen: dict = {}
    _stop_at_finish(monkeypatch, seen)
    client, seat = _two_defendants()
    r = _runner(
        tmp_path, pricing, "Please draft discovery to Delta Example Corporation.", "discovery_set", client, seat
    )
    v = r.run()
    assert "request_incomplete" not in (v.reason or ""), v.reason
    assert [(p["label"], p["party"]) for p in seen["units"]] == [(k, DELTA) for k in (SROG, RFA, RFP)]


def test_a_request_naming_no_kind_and_not_discovery_drafts_the_one_set_as_main_did(tmp_path, pricing, monkeypatch):
    seen: dict = {}
    _stop_at_finish(monkeypatch, seen)
    client, seat = _two_defendants()
    request = "Please draft a set to Delta Example Corporation about the brake records."
    r = _runner(tmp_path, pricing, request, "discovery_set", client, seat)
    v = r.run()
    assert "request_incomplete" not in (v.reason or ""), v.reason
    assert seen["units"] == [{"key": "", "variant": None, "label": ""}]


def test_a_request_naming_a_kind_the_lane_does_not_split_never_takes_the_defaults():
    assert sets.names_any_set("Please draft form interrogatories to the defense")
    assert not sets.names_any_set(DEFENSE)
    assert [u.key for u in sets.default_variants({sets.DEFAULTS_KEY: DEFAULTS})] == DEFAULTS["sets"]
    assert sets.default_variants({}) is None


@pytest.mark.parametrize(
    "body, match",
    [
        ({"sets": []}, "one or more"),
        ({"sets": ["form_interrogatories"]}, "is not one of"),
        ({"sets": ["requests_for_admission", "requests_for_admission"]}, "named twice"),
        ({"sets": ["requests_for_admission"], "to": "all"}, "expected exactly"),
    ],
)
def test_the_firm_default_sets_input_is_a_closed_shape(body, match):
    assert any(match in e for e in sets.validate_defaults(body))
    assert sets.default_variants({sets.DEFAULTS_KEY: body}) is None


def test_one_default_set_keeps_its_own_unit_and_instruction():
    plan = parties.plan_units([sets.Unit("requests_for_admission", RFA)], [], keyed=True)
    assert plan == [{"key": "set-1", "variant": "requests_for_admission", "label": RFA}]


# ---- 4. responses: SMD's served.json, or nothing is spent ---------------------------------------


def _response_run(tmp_path, pricing, client=None, request="Please draft responses to the discovery on matter 100001."):
    seat = seat_with(standard_docs())
    r = _runner(tmp_path, pricing, request, "discovery_response", client or Client(), seat)
    # Our client's names, as the facts stage reads them (a full run writes them again).
    (r.data / "facts.json").write_text(json.dumps({"client_names": ["Gamma Example"], "matter_number": "100001"}))
    return r


def test_responses_with_no_served_set_in_the_file_end_served_none_with_nothing_spent_and_the_frozen_rules_for_smd(
    tmp_path, pricing
):
    client = Client()
    r = _response_run(tmp_path, pricing, client)
    frozen = {"kind": "discovery_response", "attorney": None, "reader": None, "rules": [{"id": "r1", "text": "x"}]}
    (r.data / "wording-rules.json").write_text(json.dumps(frozen))  # as freeze() leaves it when switched on
    v = r.run()
    # The finder runs first (served_find.py): a file with no served set stops it, nothing read or paid.
    assert v.outcome == "failed" and "served_none: " in v.reason, (v.stage, v.reason)
    assert v.dollars == 0 and client.calls == [] and v.stage == "find"
    note = json.loads((r.job.job_dir / served.STOP_NOTE).read_text())
    assert note["code"] == "served_none" and note["wording_rules"] == frozen
    assert note["write"]["file"] == served.FILE


def _set(set_type, n, by, number=1, text=None):
    body = text if text is not None else "\n\n".join(f"REQUEST NO. {i}: Example request {i}." for i in range(1, n + 1))
    return {
        "set_type": set_type,
        "set_number": number,
        "propounding_party": "Delta Example Corporation",
        "responding_party": "Gamma Example",
        "request_count": n,
        "request_text": body,
        "drafted_by": by,
    }


THREE = [
    _set("special_interrogatories", 22, "operator"),
    _set("form_interrogatories", 62, "smd"),
    _set("requests_for_production", 19, "operator"),
]


def _write_served(job_dir: Path, sets_: list) -> None:
    (job_dir / served.FILE).write_text(json.dumps({"sets": sets_}))


def test_served_sets_plan_one_document_per_operator_set_each_with_its_verbatim_requests(tmp_path, pricing):
    r = _response_run(tmp_path, pricing)
    three = [*THREE]
    three[0] = _set("special_interrogatories", 1, "operator", text="SPECIAL INTERROGATORY NO. 1: IDENTIFY the driver.")
    _write_served(r.job.job_dir, three)
    units = r.units()
    assert [(u.key, u.label) for u in units] == [
        ("set-1", "Responses to Special Interrogatories, Set One"),
        ("set-2", "Responses to Requests for Production, Set One"),
    ]
    assert "SPECIAL INTERROGATORY NO. 1: IDENTIFY the driver." in units[0].instruction
    assert "exactly 1 responses" in units[0].instruction and "exactly 19 responses" in units[1].instruction
    assert "Form Interrogatories" in units[0].instruction  # told the other sets are answered separately
    (r.data / "facts.json").write_text(json.dumps({"client_name": "Gamma Example", "matter_number": "100001"}))
    assert units[0].instruction in r._drafter(units[0]).context  # what compose sends the model


def test_the_delivered_job_lists_two_sets_filed_and_one_being_prepared_by_us(tmp_path, pricing):
    r = _response_run(tmp_path, pricing)
    _write_served(r.job.job_dir, THREE)
    r.units()
    rows = r.served_outcomes()
    assert [(x["set"], x["status"]) for x in rows] == [
        ("Responses to Special Interrogatories, Set One", "filed"),
        ("Responses to Form Interrogatories, Set One", "being prepared by us"),
        ("Responses to Requests for Production, Set One", "filed"),
    ]
    assert all(x["document"] and x["document"].endswith(".docx") for x in rows if x["status"] == "filed")
    served.write_outcomes(r.data, rows)
    lines = json.loads((r.data / served.OUTCOMES).read_text())["lines"]
    assert lines[1] == "Responses to Form Interrogatories, Set One: being prepared by us."
    assert lines[0].startswith('Responses to Special Interrogatories, Set One: filed as "')


def test_a_served_plan_is_frozen_so_an_edited_hand_file_does_not_change_a_resumed_job(tmp_path, pricing):
    r = _response_run(tmp_path, pricing)
    _write_served(r.job.job_dir, THREE)
    first = [(u.key, u.label) for u in r.units()]
    _write_served(r.job.job_dir, [_set("requests_for_admission", 5, "operator")])
    r2 = DraftingRun(r.job.job_dir, inputs_dir=str(tmp_path / "inputs"), pricing=str(pricing),
                     seat_factory=lambda: None, client=Client(), log=lambda _m: None)  # fmt: skip
    assert [(u.key, u.label) for u in r2.units()] == first


def test_served_none_then_smd_writes_the_file_and_a_resume_plans_from_it(tmp_path, pricing, monkeypatch):
    seen: dict = {}
    r = _response_run(tmp_path, pricing)
    assert "served_none: " in r.run().reason
    _write_served(r.job.job_dir, THREE)
    _stop_at_estimate(monkeypatch, seen)
    r2 = DraftingRun(r.job.job_dir, inputs_dir=str(tmp_path / "inputs"), pricing=str(pricing),
                     seat_factory=lambda: seat_with(standard_docs()), client=Client(), log=lambda _m: None)  # fmt: skip
    r2.run()
    assert [p["label"] for p in seen["units"]] == [
        "Responses to Special Interrogatories, Set One",
        "Responses to Requests for Production, Set One",
    ]


@pytest.mark.parametrize(
    "sets_, code",
    [
        ([_set("requests_for_inspection_of_land", 4, "operator")], "served_other_set"),
        ([_set("special_interrogatories", 0, "operator")], "served_invalid"),
        ([{**_set("special_interrogatories", 3, "operator"), "drafted_by": "the firm"}], "served_invalid"),
        ([{**_set("special_interrogatories", 3, "operator"), "notes": "x"}], "served_invalid"),
        ([_set("form_interrogatories", 62, "smd")], "served_invalid"),  # nothing for the Operator to draft
        (
            [_set("special_interrogatories", 3, "operator"), _set("special_interrogatories", 3, "operator")],
            "served_invalid",
        ),
        # Review, 11-19: an Operator form set passed numbered 1, 2, 3 for 1.1, 2.1, 3.1.
        ([_set("form_interrogatories", 3, "operator")], "served_invalid"),
        # A count that is not the numbered requests in the text.
        ([{**_set("special_interrogatories", 3, "operator"), "request_count": 4}], "served_invalid"),
        # A responding party that is not our client: SMD drafts it, or corrects the party.
        ([{**_set("special_interrogatories", 3, "operator"), "responding_party": "Zeta Example"}], "served_invalid"),
        # A kind outside the list recorded as "other" may only be SMD's.
        ([{**_set("other", 3, "operator"), "label": "Demands for Inspection of Land"}], "served_invalid"),
        ([{**_set("other", 3, "smd")}], "served_invalid"),  # "other" needs its label
    ],
)
def test_a_served_file_outside_the_closed_shape_ends_before_spend(tmp_path, pricing, sets_, code):
    client = Client()
    r = _response_run(tmp_path, pricing, client)
    _write_served(r.job.job_dir, sets_)
    v = r.run()
    assert v.outcome == "failed" and f"{code}: " in v.reason, (v.stage, v.reason)
    assert v.dollars == 0 and client.calls == []
    assert json.loads((r.job.job_dir / served.STOP_NOTE).read_text())["code"] == code


def test_a_kind_outside_the_list_is_recorded_as_other_and_named_in_the_outcomes(tmp_path, pricing):
    r = _response_run(tmp_path, pricing)
    land = {**_set("other", 4, "smd"), "label": "Demands for Inspection of Land"}
    _write_served(r.job.job_dir, [_set("special_interrogatories", 3, "operator"), land])
    r.units()
    rows = r.served_outcomes()
    assert rows[1] == {
        "set": "Responses to Demands for Inspection of Land, Set One",
        "drafted_by": "smd",
        "status": "being prepared by us",
        "document": None,
    }
    with pytest.raises(outcome.DraftingFailed, match="a form interrogatory set is drafted by SMD for now; mark it"):
        served.parse(json.dumps({"sets": [_set("form_interrogatories", 3, "operator")]}).encode(), tmp_path)


def test_a_served_file_that_is_not_json_or_has_no_sets_is_invalid(tmp_path):
    (tmp_path / served.FILE).write_text("{not json")
    with pytest.raises(outcome.DraftingFailed, match="^served_invalid: "):
        served.read(tmp_path)
    (tmp_path / served.FILE).write_text(json.dumps({"sets": []}))
    with pytest.raises(outcome.DraftingFailed, match="^served_invalid: "):
        served.read(tmp_path)


def test_a_request_text_path_is_read_from_inside_the_job_dir_only(tmp_path):
    (tmp_path / "srog.txt").write_text("SPECIAL INTERROGATORY NO. 1: IDENTIFY the driver.")
    one = {k: v for k, v in _set("special_interrogatories", 1, "operator").items() if k != "request_text"}
    _write_served(tmp_path, [{**one, "request_text_path": "srog.txt"}])
    assert "IDENTIFY the driver" in served.read(tmp_path)[0].request_text
    _write_served(tmp_path, [{**one, "request_text_path": "../srog.txt"}])
    with pytest.raises(outcome.DraftingFailed, match="^served_invalid: .*inside the job directory"):
        served.read(tmp_path)


# ---- 6. the responses' numbering, in code ----------------------------------------------------


def _responses(label: str, numbers: list[int], restate: bool = True) -> str:
    parts = []
    for n in numbers:
        if restate:
            parts.append(f"{label} NO. {n}:\nExample request {n}.")
        parts.append(f"RESPONSE TO {label} NO. {n}:\nResponding party responds as follows.")
    return "\n\n".join(parts)


SERVED_MAP = {"set-1": {"set_type": "special_interrogatories", "count": 22}}


def _paper(text: str, key: str = "set-1") -> review_mod.Paper:
    return review_mod.Paper(key, "Responses to Special Interrogatories, Set One", text, "plain")


def test_a_clean_response_set_has_no_numbering_finding():
    text = _responses("SPECIAL INTERROGATORY", list(range(1, 23)))
    assert review_mod.response_findings([_paper(text)], SERVED_MAP) == []


@pytest.mark.parametrize(
    "numbers, said",
    [
        ([n for n in range(1, 23) if n != 14], "no response to number 14"),
        ([*range(1, 10), 9, *range(10, 23)], "two or more responses to number 9"),
        ([*range(1, 23), 23], "past the served set's 22"),
        ([2, 1, *range(3, 23)], "out of order"),
    ],
)
def test_a_gap_a_double_response_an_extra_or_the_wrong_order_holds_the_job(numbers, said):
    found = review_mod.response_findings([_paper(_responses("SPECIAL INTERROGATORY", numbers))], SERVED_MAP)
    assert any(said in f.finding for f in found), [f.finding for f in found]
    assert all(f.grade == review_mod.MUST for f in found)


@pytest.mark.parametrize(
    "prose",
    ["Response to this interrogatory will be supplemented.", "RESPONSE TO ALL REQUESTS: responding party objects."],
)
def test_a_prose_line_starting_response_is_not_a_second_response(prose):
    """FALSIFIER (review, 11-19): a body line counted as a second response."""
    text = _responses("SPECIAL INTERROGATORY", list(range(1, 23))).replace(
        "RESPONSE TO SPECIAL INTERROGATORY NO. 3:\nResponding party responds as follows.",
        f"RESPONSE TO SPECIAL INTERROGATORY NO. 3:\nResponding party responds as follows.\n{prose}",
    )
    assert review_mod.response_findings([_paper(text)], SERVED_MAP) == []


def test_a_lettered_subpart_belongs_to_its_number_and_is_no_duplicate():
    """FALSIFIER: "NO. 12(a)" read as a second response to 12."""
    text = _responses("SPECIAL INTERROGATORY", list(range(1, 23))).replace(
        "RESPONSE TO SPECIAL INTERROGATORY NO. 12:\nResponding party responds as follows.",
        "RESPONSE TO SPECIAL INTERROGATORY NO. 12(a):\nPart a.\n\nRESPONSE TO SPECIAL INTERROGATORY NO. 12(b):\nB.",
    )
    assert review_mod.response_findings([_paper(text)], SERVED_MAP) == []


def test_a_restated_request_must_be_the_served_text_word_for_word():
    """The served text is what is answered: a reworded restatement holds."""
    served_text = {i: f"Example request {i}." for i in range(1, 23)}
    m = {"set-1": {**SERVED_MAP["set-1"], "requests": served_text}}
    clean = _responses("SPECIAL INTERROGATORY", list(range(1, 23)))
    assert review_mod.response_findings([_paper(clean)], m) == []
    reworded = clean.replace("NO. 7:\nExample request 7.", "NO. 7:\nA reworded request 7.")
    found = review_mod.response_findings([_paper(reworded)], m)
    assert [f.finding for f in found] == [
        "Responses to Special Interrogatories, Set One: request 7 is not restated as served"
    ]
    spaced = clean.replace("NO. 7:\nExample request 7.", "NO. 7:\nExample   request\n7.")
    assert review_mod.response_findings([_paper(spaced)], m) == []  # whitespace aside


def test_the_code_check_runs_for_a_response_job_through_code_findings():
    text = _responses("SPECIAL INTERROGATORY", [n for n in range(1, 23) if n != 14])
    found = review_mod.code_findings("discovery_response", [_paper(text)], "", "", {}, False, served=SERVED_MAP)
    assert any("number 14" in f.finding and f.grade == review_mod.MUST for f in found)
    assert not any(
        "number 14" in f.finding
        for f in review_mod.code_findings("discovery_response", [_paper(text)], "", "", {}, False)
    )


def test_a_form_set_is_counted_by_its_dotted_numbers():
    nums = ["1.1", "2.1", "2.2", "4.1"]
    text = "\n\n".join(f"RESPONSE TO FORM INTERROGATORY NO. {n}:\nAnswer." for n in nums)
    form = {"set-1": {"set_type": "form_interrogatories", "count": 4}}
    assert review_mod.response_findings([_paper(text)], form) == []
    form["set-1"]["count"] = 5
    assert review_mod.response_findings([_paper(text)], form)


def test_responses_are_a_strict_class():
    from jobs.drafting import compose

    assert "discovery_response" in compose.STRICT_CLASSES and "discovery_set" in compose.STRICT_CLASSES


# ---- 7. what worked still works ---------------------------------------------------------------


def test_a_set_request_naming_its_parties_and_sets_plans_as_before(tmp_path, pricing, monkeypatch):
    seen: dict = {}
    _stop_at_finish(monkeypatch, seen)
    client, seat = _two_defendants()
    request = "Please draft RFAs set one and SROGs set one to Delta Example Corporation and to City of Exampletown."
    r = _runner(tmp_path, pricing, request, "discovery_set", client, seat, defaults=DEFAULTS)  # defaults unused
    r.run()
    assert [(p["label"], p["party"]) for p in seen["units"]] == [(k, p) for p in (DELTA, CITY) for k in (RFA, SROG)]


def test_a_job_with_a_frozen_plan_resumes_without_planning_again(tmp_path, pricing, monkeypatch):
    seen: dict = {}
    _stop_at_finish(monkeypatch, seen)
    client, seat = _two_defendants()
    r = _runner(tmp_path, pricing, DEFENSE, "discovery_set", client, seat)
    frozen = [{"key": "", "variant": None, "label": ""}]
    r._put("units", frozen)  # planned by the release before this one
    r.run()
    assert seen["units"] == frozen


def test_over_its_allowance_a_job_pays_nothing(tmp_path, pricing):
    """The allowance check is first, as on main: no model call at all."""
    client, seat = _two_defendants()
    r = _runner(tmp_path, pricing, DEFENSE, "discovery_set", client, seat, defaults=DEFAULTS)
    doc = json.loads((r.job.job_dir / "job.json").read_text())
    (r.job.job_dir / "job.json").write_text(json.dumps({**doc, "allowance_remaining": 0}))
    r2 = DraftingRun(r.job.job_dir, inputs_dir=str(tmp_path / "inputs"), pricing=str(pricing),
                     seat_factory=lambda: seat, client=client, log=lambda _m: None)  # fmt: skip
    v = r2.run()
    assert v.reason.startswith("limit: drafting_allowance_per_cycle"), v.reason
    assert client.calls == [] and v.dollars == 0


@pytest.mark.parametrize(
    "request_text, want",
    [
        ("Please draft discovery before the deposition next week", DEFAULTS["sets"]),
        ("Please draft discovery for document production issues", DEFAULTS["sets"]),
        (
            "Please draft RFAs and the usual",
            ["requests_for_admission", "special_interrogatories", "requests_for_production"],
        ),  # fmt: skip
        ("Please draft RFAs set one", ["requests_for_admission"]),
    ],
)
def test_prose_never_names_a_set_and_the_usual_adds_the_firm_defaults(tmp_path, pricing, request_text, want):
    client, seat = _two_defendants()
    r = _runner(tmp_path, pricing, request_text + " to Delta Example Corporation", "discovery_set", client, seat,
                defaults=DEFAULTS)  # fmt: skip
    (r.data / "caption.json").write_text(json.dumps({"fields": {"defendants": [{"value": DELTA}]}}))
    assert [u.variant for u in r.units()] == want


def test_the_usual_with_no_firm_defaults_drafts_the_named_sets_and_never_stops(tmp_path, pricing):
    """FALSIFIER (final review, 11-19): no defaults authored made a job fail that main ran."""
    client, seat = _two_defendants()
    r = _runner(tmp_path, pricing, "Please draft RFAs and the usual to Delta Example Corporation", "discovery_set",
                client, seat)  # fmt: skip
    (r.data / "caption.json").write_text(json.dumps({"fields": {"defendants": [{"value": DELTA}]}}))
    assert [u.variant for u in r.units()] == ["requests_for_admission"]


# ---- 5b. end to end: two documents filed, one set with SMD ------------------------------------


def _response_doc(label: str, title: str, n: int) -> str:
    items = "\n\n".join(
        f"{label} NO. {i}:\n\nExample request {i}.\n\nRESPONSE TO {label} NO. {i}:\n\nResponding party responds."
        for i in range(1, n + 1)
    )
    return (
        "| | |\n| --- | --- |\n| GAMMA EXAMPLE, Plaintiff, v. DELTA EXAMPLE CORPORATION, Defendant. | Case No. CV-0001 |\n"
        f"| | **PLAINTIFF GAMMA EXAMPLE'S RESPONSES TO {title}, SET ONE** |\n\n"
        "| | |\n| --- | --- |\n| PROPOUNDING PARTY: | DELTA EXAMPLE CORPORATION |\n| RESPONDING PARTY: | GAMMA EXAMPLE |\n"
        f"| SET NO.: | ONE |\n\n# RESPONSES\n\n{items}\n\nDated: {{{{ATTORNEY: date}}}}\n\n"
        "=== ATTORNEY NOTES ===\n\n## REQUEST BASIS\n\nNone.\n"
    )


class ResponsesClient(Client):
    def __init__(self, *a, by_label: dict[str, str], **kw):
        super().__init__(*a, **kw)
        self.by_label = by_label

    def _answer(self, params):
        system = json.dumps(params.get("system"))
        if "COMPOSE-PROMPT" in system and "REPAIR-PROMPT" not in system:
            user = json.dumps(params.get("messages"))
            for label, text in self.by_label.items():
                if f"draft ONLY the {label}," in user:
                    return text
        return super()._answer(params)


def test_two_operator_sets_deliver_two_documents_and_the_outcomes_name_the_smd_set(tmp_path, pricing):
    small = [
        _set("special_interrogatories", 3, "operator"),
        _set("form_interrogatories", 62, "smd"),
        _set("requests_for_production", 2, "operator"),
    ]
    client = ResponsesClient(
        by_label={
            "Responses to Special Interrogatories, Set One": _response_doc(
                "SPECIAL INTERROGATORY", "SPECIAL INTERROGATORIES", 3
            ),
            "Responses to Requests for Production, Set One": _response_doc(
                "REQUEST FOR PRODUCTION", "REQUESTS FOR PRODUCTION", 2
            ),
        }
    )
    r = _response_run(tmp_path, pricing, client)
    _write_served(r.job.job_dir, small)
    v = r.run()
    assert v.outcome == "delivered", (v.stage, v.reason)
    assert len([f for f in v.files if f["role"] == "draft"]) == 2
    rows = json.loads((r.data / served.OUTCOMES).read_text())["sets"]
    assert [x["status"] for x in rows] == ["filed", "being prepared by us", "filed"]
    filed = {f["name"] for f in v.files}
    assert all(x["document"] in filed for x in rows if x["status"] == "filed")


def test_a_drafted_set_missing_a_response_is_held_by_the_code_check_and_nothing_is_filed(tmp_path, pricing):
    gap = _response_doc("SPECIAL INTERROGATORY", "SPECIAL INTERROGATORIES", 3).replace(
        "RESPONSE TO SPECIAL INTERROGATORY NO. 2:\n\nResponding party responds.\n\n", ""
    )
    client = ResponsesClient(by_label={"Responses to Special Interrogatories, Set One": gap})
    r = _response_run(tmp_path, pricing, client)
    _write_served(r.job.job_dir, [_set("special_interrogatories", 3, "operator")])
    v = r.run()
    assert v.outcome == "failed" and v.reason.startswith("review_held: "), (v.stage, v.reason)
    assert v.files == [] and not (r.data / served.OUTCOMES).exists()
    rec = json.loads((r.data / "review.json").read_text())
    assert any("no response to number 2" in f["finding"] for f in rec["findings"])


# ---- the lead's follow-ups: codes, the firm input, the reply, the page, the CLI ---------------


def test_the_served_codes_are_reason_codes_so_no_verdict_reads_unexpected(tmp_path, pricing):
    assert set(served.PLAN_STOPS) <= set(outcome.REASON_CODES)
    r = _response_run(tmp_path, pricing)
    assert r.run().reason.startswith("served_none: ")


def test_the_delivered_verdict_carries_served_sets_and_every_other_verdict_is_unchanged():
    plain = outcome.Verdict("delivered", files=[{"name": "A.docx"}]).to_list()[0]
    assert "served_sets" not in plain
    rows = [{"set": "Responses to Special Interrogatories, Set One", "status": "filed", "document": "A.docx"}]
    assert outcome.Verdict("delivered", served_sets=rows).to_list()[0]["served_sets"] == rows


@pytest.mark.parametrize(
    "body, ok",
    [(DEFAULTS, True), ({"sets": ["form_interrogatories"]}, False), ({"sets": []}, False), ({"to": "all"}, False)],
)
def test_the_firm_schema_accepts_and_validates_discovery_defaults(tmp_path, body, ok):
    from jobs.drafting import firm as firm_mod

    root = make_inputs(tmp_path / "in", **{sets.DEFAULTS_KEY: body})
    if ok:
        assert firm_mod.load(root).data[sets.DEFAULTS_KEY] == body
    else:
        with pytest.raises(firm_mod.DraftingConfigError, match=sets.DEFAULTS_KEY):
            firm_mod.load(root)


SERVED_RUNNER = """
import json, sys, pathlib
jd = pathlib.Path(sys.argv[1])
rows = [{"set": "Responses to Special Interrogatories, Set One", "drafted_by": "operator", "status": "filed",
         "document": "A.docx"},
        {"set": "Responses to Form Interrogatories, Set One", "drafted_by": "smd", "status": "being prepared by us",
         "document": None}]
out = [{"unit": "drafting", "kind": "drafting", "outcome": "delivered", "stage": "file", "reason": None,
        "dollars": 1.0, "document_class": "discovery_response", "folder_id": "folder-9",
        "files": [{"name": "A.docx", "size": 10, "role": "draft"}], "served_sets": rows,
        "proof": {"readback": "confirmed", "files": [{"name": "A.docx", "file_id": "f1", "size": 10,
                  "sha256": "a" * 64}], "review": "passed", "review_cents": 0}}]
(jd / 'verdict.json').write_text(json.dumps(out))
print(json.dumps(out))
"""


def test_the_lane_records_the_served_sets_in_the_delivery(tmp_path):
    import test_drafting_lane as dl

    lane, client = dl._lane(tmp_path, script=SERVED_RUNNER)
    dl._submit(lane, client)
    assert lane.tick() == "delivered"
    sets_ = client.records[-1][2]["delivery"]["served_sets"]
    assert [x["status"] for x in sets_] == ["filed", "being prepared by us"]
    # FALSIFIER (review, 11-19): the owed set had no durable record once delivered.
    owed = [r for r in client.requests if r["action"] == "delivery_proof_alert" and r["code"] == "served_owed"]
    assert [a["detail"] for a in owed] == ["1 owed by SMD: Responses to Form Interrogatories, Set One"]


# ---- 4b. SMD records a set it owed on a delivered response job ---------------------------------

from test_drafting_deliver_by_smd import _ended, _rec, _row, seat  # noqa: E402,F401 - the real broker fixture

FORM = "Responses to Form Interrogatories, Set One"
SROG_SET = "Responses to Special Interrogatories, Set One"


def _delivered_with_owed(seat, owed: list[str]) -> str:
    """A response job delivered by its runner with two files and ``owed`` sets SMD still owes."""
    from jobs.drafting import deliver_by_smd as dbs

    _broker, _client, lane, _db, _q = seat
    jid = _ended(seat, "failed")
    dbs.apply(lane, dbs.plan(lane, jid, _rec(), quiet=True))
    jd = lane.job_dir(jid)
    proof = [{"file_id": f["file_id"], "size": f["bytes"], "sha256": f["sha256"]} for f in _rec()["files"]]
    (jd / "verdict.json").write_text(json.dumps([{"outcome": "delivered", "proof": {"files": proof}}]))
    rows = [{"set": SROG_SET, "drafted_by": "operator", "status": "filed", "document": "A.docx"}]
    rows += [{"set": s, "drafted_by": "smd", "status": "being prepared by us", "document": None} for s in owed]
    (jd / "data").mkdir(exist_ok=True)
    served.write_outcomes(jd / "data", rows)
    return jid


HAND = {"folder_id": "folder-0101", "files": [{"file_id": "file-form", "name": "Form Responses.docx", "bytes": 9000,
                                             "sha256": "c" * 64}]}  # fmt: skip


TELL = f'tell the attorney: {FORM} filed as "Form Responses.docx"'


def _owed_alerts(client) -> list[str]:
    return [r["detail"] for r in client.requests if r.get("code") == "served_owed"]


def test_smd_records_an_owed_set_on_a_delivered_job_proof_first_and_marks_it_filed(seat):
    from jobs.drafting import deliver_by_smd as dbs

    broker, client, lane, _db, _q = seat
    jid = _delivered_with_owed(seat, [FORM])
    p = dbs.plan_set(lane, jid, json.loads(json.dumps(HAND)), FORM, quiet=False)
    assert p["reply"] == f'{FORM}: filed as "Form Responses.docx".'
    dbs.apply_set(lane, p)
    row = _row(broker, jid)
    assert row["state"] == "delivered"
    delivery = json.loads(row["delivery_json"])
    assert [f["name"] for f in delivery["files"]][-1] == "Form Responses.docx" and len(delivery["files"]) == 3
    assert [x["file_id"] for x in broker.drafting.ledger.proven_files(jid)][-1] == "file-form"
    filed = delivery["served_sets"][1]
    assert filed["status"] == "filed" and filed["told"] is False and filed["recorded_at"]
    rows = json.loads((lane.job_dir(jid) / "data" / served.OUTCOMES).read_text())["sets"]
    assert rows[1]["status"] == "filed" and rows[1]["document"] == "Form Responses.docx"
    with pytest.raises(dbs.Refused, match="already filed"):
        dbs.plan_set(lane, jid, json.loads(json.dumps(HAND)), FORM, quiet=True)


def test_an_smd_filed_set_stays_owed_until_the_attorney_is_told(seat):
    """FALSIFIER (final review, 11-19): the attorney was told "being prepared
    by us"; a filed set with no note to him was closed and he never heard."""
    from jobs.drafting import deliver_by_smd as dbs

    broker, client, lane, _db, _q = seat
    land = "Responses to Demands for Inspection of Land, Set One"
    jid = _delivered_with_owed(seat, [FORM, land])
    with pytest.raises(dbs.Refused, match="not filed yet"):
        dbs.plan_told(lane, jid, FORM)
    dbs.apply_set(lane, dbs.plan_set(lane, jid, json.loads(json.dumps(HAND)), FORM, quiet=False))
    assert _owed_alerts(client) == [f"2 owed by SMD: {TELL}; {land}"]
    dbs.apply_told(lane, dbs.plan_told(lane, jid, FORM))
    assert json.loads(_row(broker, jid)["delivery_json"])["served_sets"][1]["told"] is True
    with pytest.raises(dbs.Refused, match="already marked told"):
        dbs.plan_told(lane, jid, FORM)


def test_a_set_still_owed_after_one_is_recorded_pages_again(seat):
    from jobs.drafting import deliver_by_smd as dbs

    _broker, client, lane, _db, _q = seat
    other = "Responses to Demands for Inspection of Land, Set One"
    jid = _delivered_with_owed(seat, [FORM, other])
    dbs.apply_set(lane, dbs.plan_set(lane, jid, json.loads(json.dumps(HAND)), FORM, quiet=True))
    assert _owed_alerts(client) == [f"2 owed by SMD: {TELL}; {other}"]


def test_a_file_id_already_on_the_job_is_refused(seat):
    from jobs.drafting import deliver_by_smd as dbs

    _broker, _client, lane, _db, _q = seat
    jid = _delivered_with_owed(seat, [FORM])
    dup = json.loads(json.dumps(HAND))
    dup["files"][0]["file_id"] = _rec()["files"][0]["file_id"]
    with pytest.raises(dbs.Refused, match="is already filed on job"):
        dbs.plan_set(lane, jid, dup, FORM, quiet=True)


def test_a_rerun_finishes_a_set_whose_local_copy_did_not_land(seat, monkeypatch):
    """FALSIFIER: the ledger took the set and the job's own copy failed; a
    rerun refused "already filed" and the local outcomes stayed wrong."""
    from jobs.drafting import deliver_by_smd as dbs, served_deliver

    broker, _client, lane, _db, _q = seat
    jid = _delivered_with_owed(seat, [FORM])
    p = dbs.plan_set(lane, jid, json.loads(json.dumps(HAND)), FORM, quiet=True)
    real = served_deliver.served_mod.write_outcomes

    def lost(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(served_deliver.served_mod, "write_outcomes", lost)
    with pytest.raises(OSError):
        dbs.apply_set(lane, p)
    monkeypatch.setattr(served_deliver.served_mod, "write_outcomes", real)
    again = dbs.plan_set(lane, jid, json.loads(json.dumps(HAND)), FORM, quiet=True)
    assert again["finish"] is True
    dbs.apply_set(lane, again)
    rows = json.loads((lane.job_dir(jid) / "data" / served.OUTCOMES).read_text())["sets"]
    assert rows[1]["status"] == "filed"
    assert len(json.loads(_row(broker, jid)["delivery_json"])["files"]) == 3  # nothing recorded twice


def test_a_set_on_a_job_not_delivered_or_not_owed_is_refused(seat):
    from jobs.drafting import deliver_by_smd as dbs

    _broker, _client, lane, _db, _q = seat
    jid = _ended(seat, "failed")
    with pytest.raises(dbs.Refused, match="records a set owed on a delivered job"):
        dbs.plan_set(lane, jid, json.loads(json.dumps(HAND)), FORM, quiet=True)
    jid2 = _delivered_with_owed(seat, [FORM])
    with pytest.raises(dbs.Refused, match="has no served set"):
        dbs.plan_set(lane, jid2, json.loads(json.dumps(HAND)), "Responses to Something Else, Set Two", quiet=True)


def test_smds_page_for_a_stop_before_spend_says_what_to_do_and_counts_the_frozen_rules(tmp_path):
    import test_job_notes as tjn

    from jobs import job_notes

    lane, client = tjn._lane(tmp_path)
    jid = tjn.JOB
    tjn.dl._submit(lane, client, jid)
    (lane.jobs / jid).mkdir(parents=True, exist_ok=True)
    rules = {"rules": [{"id": "a"}, {"id": "b"}]}
    (lane.jobs / jid / served.STOP_NOTE).write_text(json.dumps({"code": "served_unknown", "wording_rules": rules}))
    client.rows[jid] = {"state": "failed", "reason": "served_unknown: SMD has not named the served sets", "attempt": 1}
    job_notes.tell(lane, jid, why="t")
    (alert,) = tjn._alerts(client)
    assert alert["detail"] == (
        "failed (failed): served_unknown: SMD has not named the served sets; next: name the served sets "
        "(jobs.drafting.served write), then resume (smd-needs.json, 2 rules); " + job_notes.NOT_TOLD
    )
    nxt = alert["detail"].split("; next: ")[1].split("; the requester")[0]
    assert len(nxt) <= 90, nxt
    # A long reason (a party list) is cut so the next line still fits the broker's 300 characters.
    client.rows[jid] = {"state": "failed", "reason": "party_unknown: " + "Example Party; " * 40, "attempt": 3}
    job_notes.tell(lane, jid, why="t")
    long = tjn._alerts(client)[-1]["detail"]
    assert len(long) <= job_notes.ALERT_MAX and "; next: name the parties" in long, long
    client.rows[jid] = {"state": "failed", "reason": "gate_refused: x", "attempt": 2}
    job_notes.tell(lane, jid, why="t")
    assert "next:" not in tjn._alerts(client)[-1]["detail"]  # every other stop's page is unchanged


JID = "01DRAFTJ0B000000000000000A"


class _Broker:
    def __init__(self, state: str = "failed") -> None:
        self.state, self.asked = state, []

    def _request(self, req):
        self.asked.append(req)
        if req["action"] == "drafting_job_status":
            return {"job": {"state": self.state}}
        return {"ok": True}


def _cli_job(tmp_path, monkeypatch, cls="discovery_response"):
    from jobs.drafting import served_cli

    run = tmp_path / "run"
    jd = run / "drafting-jobs" / JID
    (jd / "data").mkdir(parents=True)
    (jd / "job.json").write_text(json.dumps({"document_class": cls}))
    (jd / "data" / "facts.json").write_text(json.dumps({"client_names": ["Gamma Example"]}))
    monkeypatch.setenv(served_cli.RUN_DIR_ENV, str(run))
    src = tmp_path / "staff"
    src.mkdir()
    (src / "srog.txt").write_text("SPECIAL INTERROGATORY NO. 1: IDENTIFY the driver.")
    one = {**served_cli.template()["sets"][0], "propounding_party": "Delta Example Corporation",
           "responding_party": "Gamma Example", "request_text_path": "srog.txt"}  # fmt: skip
    (src / "served.json").write_text(json.dumps({"sets": [one]}))
    return jd, src, one


def test_the_cli_writes_a_validated_served_file_and_names_the_resume(tmp_path, monkeypatch, capsys):
    jd, src, one = _cli_job(tmp_path, monkeypatch)
    assert served.main(["template", JID]) == 0 and json.loads(capsys.readouterr().out)["sets"]
    assert served.main(["write", JID, "--from", str(src / "served.json")], client=_Broker()) == 0
    said = capsys.readouterr().out
    assert "Responses to Special Interrogatories, Set One: 1 requests, the Operator drafts it" in said
    assert f"python -m jobs.drafting.served resume {JID}" in said
    assert "IDENTIFY the driver" in served.read(jd)[0].request_text  # inlined: the job dir needs nothing else
    assert oct((jd / served.FILE).stat().st_mode & 0o777) == "0o644"
    (src / "bad.json").write_text(json.dumps({"sets": [{**one, "set_type": "requests_for_inspection_of_land"}]}))
    with pytest.raises(SystemExit, match="served_other_set"):
        served.main(["write", JID, "--from", str(src / "bad.json")], client=_Broker())
    assert served.main(["show", JID]) == 0 and "1 requests" in capsys.readouterr().out
    with pytest.raises(SystemExit, match="not a job id"):
        served.main(["show", "../../etc"])


@pytest.mark.parametrize("state", ["running", "delivered"])
def test_the_cli_refuses_a_running_or_delivered_job(tmp_path, monkeypatch, state):
    _jd, src, _one = _cli_job(tmp_path, monkeypatch)
    for verb in (["write", JID, "--from", str(src / "served.json")], ["resume", JID, "--why", "x"]):
        with pytest.raises(SystemExit, match=f"is {state}"):
            served.main(verb, client=_Broker(state))


def test_the_cli_refuses_a_job_that_is_not_discovery_responses(tmp_path, monkeypatch):
    _jd, src, _one = _cli_job(tmp_path, monkeypatch, cls="discovery_set")
    with pytest.raises(SystemExit, match="not discovery responses"):
        served.main(["write", JID, "--from", str(src / "served.json")], client=_Broker())


def test_the_cli_never_writes_through_a_link_the_runner_planted(tmp_path, monkeypatch):
    jd, src, _one = _cli_job(tmp_path, monkeypatch)
    outside = tmp_path / "outside.txt"
    outside.write_text("root's file")
    (jd / served.FILE).symlink_to(outside)
    with pytest.raises(SystemExit, match="is a link"):
        served.main(["write", JID, "--from", str(src / "served.json")], client=_Broker())
    assert outside.read_text() == "root's file"


def test_the_cli_replan_replaces_a_frozen_plan_until_a_document_is_composed(tmp_path, monkeypatch, capsys):
    jd, src, one = _cli_job(tmp_path, monkeypatch)
    data = jd / "data"
    (data / served.PLAN).write_text(json.dumps({"sets": []}))
    (data / "state.json").write_text(json.dumps({"units": [{"key": "set-1"}], "plan": {"status": "done"}}))
    args = ["replan", JID, "--from", str(src / "served.json"), "--by", "smd", "--why", "the count was wrong"]
    assert served.main(args, client=_Broker()) == 0
    assert json.loads((data / served.PLAN).read_text())["sets"][0]["request_count"] == 1
    assert "units" not in json.loads((data / "state.json").read_text())
    from jobs.drafting import served_cli

    rec = json.loads((data / served_cli.REPLANS).read_text().splitlines()[-1])
    assert rec["by"] == "smd" and rec["why"] == "the count was wrong"
    (data / "state.json").write_text(json.dumps({"set-1:compose": {"status": "done"}}))
    with pytest.raises(SystemExit, match="has composed a document"):
        served.main(args, client=_Broker())


def test_the_cli_takes_the_daemons_environment_in_a_bare_root_shell(tmp_path, monkeypatch):
    """FALSIFIER (final review, 11-19): from `fly ssh console` the socket and
    run dir were unset, so write, replan and resume failed."""
    from jobs.drafting import served_cli

    proc = tmp_path / "proc"
    (proc / "42").mkdir(parents=True)
    (proc / "42" / "cmdline").write_bytes(b"/opt/jobs/.venv/bin/python\0-m\0jobs.daemon\0")
    env = f"{served_cli.SOCKET_ENV}=/run/broker.sock\0{served_cli.RUN_DIR_ENV}=/run/x\0SECRET=s\0".encode()
    (proc / "42" / "environ").write_bytes(env)
    for name in (*served_cli.ENV, "SECRET"):
        monkeypatch.delenv(name, raising=False)
    served_cli.load_daemon_env(proc)
    import os

    assert os.environ[served_cli.SOCKET_ENV] == "/run/broker.sock" and os.environ[served_cli.RUN_DIR_ENV] == "/run/x"
    assert "SECRET" not in os.environ  # by name only
    assert served_cli.job_dir_for(JID) == Path("/run/x/drafting-jobs") / JID


def test_the_cli_replan_refuses_a_data_dir_that_is_a_link(tmp_path, monkeypatch):
    jd, src, _one = _cli_job(tmp_path, monkeypatch)
    real = tmp_path / "elsewhere"
    (jd / "data").rename(real)
    (jd / "data").symlink_to(real)
    args = ["replan", JID, "--from", str(src / "served.json"), "--by", "smd", "--why", "x"]
    with pytest.raises(SystemExit, match="not the job's own data directory"):
        served.main(args, client=_Broker())


def test_the_cli_resume_asks_the_broker_with_a_reason(tmp_path, monkeypatch):
    _cli_job(tmp_path, monkeypatch)
    b = _Broker()
    assert served.main(["resume", JID, "--why", "named the sets"], client=b) == 0
    assert b.asked[-1] == {"action": "drafting_job_resume", "job_id": JID, "reason": "named the sets"}


@pytest.mark.parametrize(
    "label_text",
    [
        "RESPONSE TO SPECIAL INTERROGATORY NO. {n}:",  # the default labels
        "Response No. {n}:",  # a firm that labels responses briefly
        "RESPONSE TO SPECIALLY PREPARED INTERROGATORY NO. {n}:",
        "RESPONSE:",  # unnumbered, under the restated request
    ],
)
def test_the_numbering_check_is_tolerant_of_the_label_and_strict_on_the_numbers(label_text):
    def doc(numbers):
        return "\n\n".join(
            f"SPECIAL INTERROGATORY NO. {n}:\nExample.\n\n{label_text.format(n=n)}\nAnswer." for n in numbers
        )

    m = {"set-1": {"set_type": "special_interrogatories", "count": 5}}
    assert review_mod.response_findings([_paper(doc([1, 2, 3, 4, 5]))], m) == []
    assert review_mod.response_findings([_paper(doc([1, 2, 4, 5]))], m)  # a gap still holds
