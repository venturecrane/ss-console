"""The 2026-11-05 request, as falsifiers: discovery to two defendants in one
email drafted to one of them, and SMD submitted, seeded and fixed a second job
by hand.

Each rule here fails on the code before ``parties.py``: the planner plans one
unit per (party, set); a name that is none of the matter's parties stops the
job for SMD; each set carries its own party in its identification block, its
title, its definition of YOU and its file name; sets to different parties share
one paid read of the record; a request to one party, and a plan already frozen
in an in-flight job, run exactly as before.

Every name and sentence is invented (Example / Exampletown).
"""

from __future__ import annotations

import json

import pytest

from drafting_testkit import CAPTION, seat_with
from jobs.drafting import parties, render, sets
from test_drafting_review import KINDS, Client, _run, docs_with_note, one_set

RFA, RFP, SROG = "Requests for Admission", "Requests for Production", "Special Interrogatories"
CITY, DISTRICT = "CITY OF EXAMPLETOWN", "EXAMPLETOWN UNIFIED SCHOOL DISTRICT"
THEIRS, OURS = [CITY, DISTRICT], ["GAMMA EXAMPLE"]
TWO = "Please draft plaintiff's RFA, RFPD and SROG set one to the City of Exampletown and Exampletown Unified School District."


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


# ---- who the request serves ------------------------------------------------------------


@pytest.mark.parametrize(
    "request_text, want",
    [
        (TWO, [CITY, DISTRICT]),
        ("SROGs set one to the School District and RFAs to the City.", [DISTRICT, CITY]),
        ("Draft RFAs set one to both defendants.", [CITY, DISTRICT]),
        ("Draft RFAs set one to defendants.", [CITY, DISTRICT]),
        ("Draft RFAs set one to Defendant City of Exampletown.", []),  # one party: as before
        ("Draft RFAs set one to the defendant.", []),
    ],
)
def test_the_parties_are_read_from_the_request_and_the_matter(request_text, want):
    assert parties.plan(request_text, THEIRS, OURS) == want


@pytest.mark.parametrize(
    "request_text",
    [
        "Draft RFAs and SROGs to the City and Acme Paving Inc.",  # none of the matter's parties
        "Draft RFAs to Exampletown and the City.",  # "Exampletown" could be either
    ],
)
def test_a_name_that_is_not_one_party_of_the_matter_stops_rather_than_guesses(request_text):
    with pytest.raises(parties.PartyUnknown):
        parties.plan(request_text, THEIRS, OURS)


def test_a_matter_with_one_other_side_party_never_plans_parties():
    assert parties.plan("Draft RFAs to the City and Acme Paving Inc.", [CITY], OURS) == []


def test_the_client_named_as_the_one_who_serves_is_not_a_party_served():
    req = "Draft RFAs on behalf of Gamma Example, served on the City and the School District."
    assert parties.plan(req, THEIRS, OURS) == [CITY, DISTRICT]


def test_smd_may_name_the_parties_by_hand_and_each_is_checked(tmp_path):
    (tmp_path / parties.OVERRIDE).write_text(json.dumps({"parties": ["City of Exampletown"]}))
    assert parties.override(tmp_path, THEIRS) == [CITY]
    (tmp_path / parties.OVERRIDE).write_text(json.dumps({"parties": ["Acme Paving"]}))
    with pytest.raises(parties.PartyUnknown):
        parties.override(tmp_path, THEIRS)
    assert parties.override(tmp_path / "none", THEIRS) is None


def test_the_matter_parties_are_the_courts_caption_and_fall_back_to_the_record():
    fields = {
        "plaintiffs": [{"value": "GAMMA EXAMPLE"}],
        "defendants": [{"value": CITY + " and DOES 1-25"}, {"value": DISTRICT}],
    }
    record = [{"side": "client", "name": "Gamma Example"}, {"side": "other", "name": "City of Exampletown"}]
    assert parties.matter_parties({"fields": fields, "parties": record}, {}) == (["GAMMA EXAMPLE"], THEIRS)
    assert parties.matter_parties({"fields": {}, "parties": record}, {})[1] == ["City of Exampletown"]


# ---- the party written into the set ------------------------------------------------------


def _to(label: str, party: str) -> str:
    return one_set(label).replace("DELTA EXAMPLE", party)


def test_the_unit_party_is_written_into_its_identification_block_title_and_you():
    doc = _to(RFA, CITY).split("=== ATTORNEY")[0]
    out, notes, problems = parties.bind(doc, DISTRICT, THEIRS)
    assert render.party(out, "RESPONDING PARTY") == DISTRICT
    assert f"TO DEFENDANT {DISTRICT}, SET ONE" in out
    assert sets.definition_texts(out)["YOU"][2] == f'"YOU" means {DISTRICT}.'
    assert notes and problems == []
    again, none, _ = parties.bind(out, DISTRICT, THEIRS)
    assert again == out and none == []


def test_a_definition_of_you_still_naming_another_party_is_a_problem():
    # Markup inside the name: the swap cannot reach it, the check still reads it.
    doc = _to(RFA, DISTRICT).replace(f'"YOU" means {DISTRICT}.', '"YOU" means **CITY** OF EXAMPLETOWN.')
    _out, _notes, problems = parties.bind(doc, DISTRICT, THEIRS)
    assert problems == ['"YOU" is defined as another party, not ' + DISTRICT]


def test_a_set_to_another_party_keeps_its_own_you_when_aligned():
    canon = _to(RFA, CITY).split("=== ATTORNEY")[0]
    later = _to(SROG, DISTRICT).split("=== ATTORNEY")[0]
    out, _ = sets.align(later, canon, "x", keep=parties.PARTY_TERMS)
    assert sets.definition_texts(out)["YOU"][2] == f'"YOU" means {DISTRICT}.'
    assert "YOU" not in parties.drop_party_terms(sets.front_matter(canon))


# ---- end to end: the 11-05 request ---------------------------------------------------------


class PartyClient(Client):
    """Composes keyed by (set label, party) as the job's instruction names them."""

    def __init__(self, *a, by_party: dict[tuple[str, str], str], **kw):
        super().__init__(*a, **kw)
        self.by_party = by_party

    def _answer(self, params):
        system = json.dumps(params.get("system"))
        if "COMPOSE-PROMPT" in system and "REPAIR-PROMPT" not in system:
            user = json.dumps(params.get("messages"))
            for (label, party), text in self.by_party.items():
                if f"draft ONLY the {label} set to {party}," in user:
                    return text
        return super()._answer(params)


def _two_defendants():
    cap = {**CAPTION, "defendants": [CITY, DISTRICT]}
    record = {
        "case_number": "CV-0001",
        "court": "Superior Court",
        "county": "Exampletown",
        "parties": [
            {"side": "client", "name": "Gamma Example"},
            {"side": "other", "name": "City of Exampletown"},
            {"side": "other", "name": "Exampletown Unified School District"},
        ],
        "attorney_email": "",
    }
    return json.dumps(cap), seat_with(docs_with_note(), record=record)


def test_two_defendants_get_every_set_each_in_one_job_and_one_delivery(tmp_path, pricing):
    cap, seat = _two_defendants()
    drafts = {(label, p): _to(label, p) for label in KINDS for p in THEIRS}
    drafts[(RFA, DISTRICT)] = _to(RFA, CITY)  # the model wrote the wrong party: code binds the right one
    client = PartyClient(caption=[cap], by_party=drafts)
    r, v, seat = _run(tmp_path, pricing, client, request=TWO, seat=seat)
    assert v.outcome == "delivered", (v.stage, v.reason)
    plan = json.loads((r.data / "state.json").read_text())["units"]
    assert [(p["label"], p["party"]) for p in plan] == [(k, p) for p in THEIRS for k in (RFA, RFP, SROG)]
    drafts_filed = [f["name"] for f in v.files if f["role"] == "draft"]
    assert len(drafts_filed) == 6
    for p in THEIRS:
        assert sum(f" to {p} " in n for n in drafts_filed) == 3, drafts_filed
    for i, p in enumerate(plan, 1):
        final = (r.data / f"set-{i}" / "draft-final.md").read_text()
        assert render.party(final, "RESPONDING PARTY") == p["party"], i
        assert sets.definition_texts(final)["YOU"][2] == f'"YOU" means {p["party"]}.', i
        assert len(sets.kinds_in(final.split("=== ATTORNEY")[0])) == 1
    # one paid read of the record, shared by all six sets
    assert client.count("DIGEST-PROMPT") == 1
    assert [f["role"] for f in v.files].count("attorney_notes") == 1


def test_a_request_to_a_party_the_matter_does_not_list_fails_before_any_compose(tmp_path, pricing):
    cap, seat = _two_defendants()
    client = PartyClient(caption=[cap], by_party={})
    req = "Please draft RFAs set one to the City of Exampletown and to Acme Paving Inc."
    _r, v, seat = _run(tmp_path, pricing, client, request=req, seat=seat)
    assert v.outcome == "failed" and v.reason.startswith("party_unknown: "), (v.stage, v.reason)
    assert client.count("COMPOSE-PROMPT") == 0 and seat.created == []


def test_a_request_to_one_party_plans_exactly_as_before(tmp_path, pricing):
    cap, seat = _two_defendants()
    client = Client(caption=[cap], drafts={label: one_set(label) for label in KINDS})
    req = "Please draft RFAs set one and SROGs set one to Defendant City of Exampletown."
    r, v, _ = _run(tmp_path, pricing, client, request=req, seat=seat)
    assert v.outcome == "delivered", (v.stage, v.reason)
    plan = json.loads((r.data / "state.json").read_text())["units"]
    assert plan == [
        {"key": "set-1", "variant": "requests_for_admission", "label": RFA},
        {"key": "set-2", "variant": "special_interrogatories", "label": SROG},
    ]


def test_a_plan_frozen_before_parties_resumes_unchanged(tmp_path, pricing):
    from drafting_testkit import make_inputs, make_job
    from jobs.drafting.run import DraftingRun

    _cap, seat = _two_defendants()
    make_inputs(tmp_path / "inputs")
    jd = make_job(tmp_path / "job", cls="discovery_set")
    doc = json.loads((jd / "job.json").read_text())
    doc["request_text"] = TWO
    (jd / "job.json").write_text(json.dumps(doc))
    r = DraftingRun(
        jd,
        inputs_dir=str(tmp_path / "inputs"),
        pricing=str(pricing),
        seat_factory=lambda: seat,
        client=Client(),
        log=lambda _m: None,
    )
    frozen = [{"key": f"set-{i}", "variant": u.key, "label": u.label} for i, u in enumerate(sets.variants(TWO), 1)]
    # The caption names both defendants: a fresh plan would be six units with parties.
    fields = {"plaintiffs": [{"value": "GAMMA EXAMPLE"}], "defendants": [{"value": CITY}, {"value": DISTRICT}]}
    (r.data / "caption.json").write_text(json.dumps({"fields": fields}))
    r._put("units", frozen)
    assert [(u.key, u.label, u.party) for u in r.units()] == [(p["key"], p["label"], None) for p in frozen]
    assert json.loads((r.data / "state.json").read_text())["units"] == frozen
