"""The 2026-11-05 request, as falsifiers: discovery to two defendants in one
email drafted to one of them, and SMD submitted, seeded and fixed a second job
by hand.

The design (``parties.py``): the request names the parties, the caption
verifies them. Every rule here fails on the code before it: one unit per
(named party, set); the caption is cut where the request's names fall, never
by a grammar; a scope line narrows; "all defendants" uses the caption only
when it splits cleanly; anything code cannot settle stops for SMD; each set
carries its own party in its identification block, title, YOU and file name;
one paid read of the record; one-party requests and frozen plans run as before.

The caption shapes are the real ones, de-identified (Example / Exampletown).
"""

from __future__ import annotations

import json

import pytest

from drafting_testkit import CAPTION, seat_with
from jobs.drafting import parties, render, sets
from test_drafting_review import KINDS, Client, _run, docs_with_note, one_set

RFA, RFP, SROG = "Requests for Admission", "Requests for Production", "Special Interrogatories"
DISTRICT, CITY = "EXAMPLETOWN UNIFIED SCHOOL DISTRICT", "CITY OF EXAMPLETOWN"
#: The 11-05 caption's shape: the District, the City and the State in one value, then an agency.
V1 = f"{DISTRICT}, {CITY}, STATE OF CA, and DOES 1 - 100"
V2 = "EXAMPLETOWN SHERIFF'S DEPARTMENT, County of Exampletown, State of CA - Department of Example Services"
VALUES = [V1, V2]
#: The parties named after "propound discovery to A and to B", the set words in the next sentence.
TWO = (
    "Please propound discovery to Exampletown Unified School District and to City of Exampletown. "
    "We need RFA, RFPD and SROG, set one."
)
SCOPED = TWO + "\n\nSMD, re-submitted: to Exampletown Unified School District only."


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


def _cap(*values: str) -> dict:
    return {"fields": {"plaintiffs": [{"value": "GAMMA EXAMPLE"}], "defendants": [{"value": v} for v in values]}}


# ---- the request names the parties, the caption verifies them --------------------------


def test_the_request_names_split_the_caption_where_they_fall():
    # "City of X" inside the District's value is its own party because the request names it.
    assert parties.resolve(TWO, VALUES).planned == [DISTRICT, CITY]
    assert sets.variants(TWO) and len(sets.variants(TWO)) == 3


def test_names_in_the_request_win_over_every_defendant_words_on_a_caption_that_does_not_split():
    assert parties.resolve(TWO + " Serve both defendants.", VALUES).planned == [DISTRICT, CITY]


def test_a_scope_line_narrows_to_the_names_it_carries():
    assert parties.resolve(SCOPED, VALUES).planned == [DISTRICT]
    assert parties.resolve(SCOPED, VALUES).known == [DISTRICT, CITY]
    with pytest.raises(parties.PartyUnknown, match="limits its sets"):
        parties.resolve(TWO + "\n\nSMD: to Acme Paving Inc. only.", VALUES)


def test_all_defendants_on_a_caption_that_does_not_split_cleanly_stops_quoting_it():
    with pytest.raises(parties.PartyUnknown, match="CITY OF EXAMPLETOWN"):
        parties.resolve("Draft RFAs set one to all defendants.", VALUES)


def test_all_defendants_on_a_caption_that_splits_cleanly_is_every_party():
    clean = f"{CITY}, a public entity; {DISTRICT}, a public entity; and DOES 1 through 50, inclusive"
    assert parties.resolve("Draft RFAs set one to all defendants.", [clean]).planned == [CITY, DISTRICT]
    with pytest.raises(parties.PartyUnknown, match="names 1"):
        parties.resolve("Draft RFAs set one to both defendants.", ["DELTA EXAMPLE, an individual"])


def test_two_request_names_that_are_one_caption_span_stop():
    req = f"Propound discovery to {DISTRICT}, {CITY} and to {CITY}. RFAs set one."
    with pytest.raises(parties.PartyUnknown, match="one party on the caption"):
        parties.resolve(req, VALUES)


def test_a_request_naming_none_of_several_defendants_stops():
    with pytest.raises(parties.PartyUnknown, match="names none"):
        parties.resolve("Draft RFAs set one to the defendant.", VALUES)


def test_one_name_binds_that_party_when_there_are_several_and_nothing_when_there_is_one():
    assert parties.resolve("Draft RFAs to City of Exampletown.", VALUES).planned == [CITY]
    assert parties.resolve("Draft RFAs to Delta Example.", ["DELTA EXAMPLE, an individual"]).planned == []
    assert parties.resolve("Draft RFAs to the defendant.", ["DELTA EXAMPLE"]).planned == []


@pytest.mark.parametrize(
    "value, want",
    [
        (f"{CITY}, a public entity; {DISTRICT}, a public entity; and DOES 1 through 50", [CITY, DISTRICT]),
        ("SMITH AND SONS, INC., a California corporation; and DOES 1 to 25", ["SMITH AND SONS, INC."]),
        ("BOARD OF TRUSTEES OF ALPHA AND BETA, a public entity", ["BOARD OF TRUSTEES OF ALPHA AND BETA"]),
        ("DELTA EXAMPLE", ["DELTA EXAMPLE"]),
    ],
)
def test_a_clean_caption_value_splits_into_whole_parties_as_printed(value, want):
    assert parties.entities(value) == want


@pytest.mark.parametrize("value", [V1, V2, "CITY OF EXAMPLETOWN, Exampletown, and DOES 1-10"])
def test_a_qualifier_after_a_name_or_a_stray_word_is_never_a_clean_split(value):
    with pytest.raises(parties.Undecided):
        parties.entities(value)


def test_smd_may_name_the_parties_by_hand_and_each_is_checked(tmp_path):
    (tmp_path / parties.OVERRIDE).write_text(json.dumps({"parties": ["City of Exampletown"]}))
    assert parties.override(tmp_path, VALUES) == [CITY]
    (tmp_path / parties.OVERRIDE).write_text(json.dumps({"parties": ["Acme Paving"]}))
    with pytest.raises(parties.PartyUnknown):
        parties.override(tmp_path, VALUES)
    assert parties.override(tmp_path / "none", VALUES) is None


def test_the_other_side_is_the_courts_caption_and_falls_back_to_the_record():
    record = [{"side": "client", "name": "Gamma Example"}, {"side": "other", "name": "City of Exampletown"}]
    assert parties.other_values({**_cap(V1, V2), "parties": record}, {}) == VALUES
    assert parties.other_values({"fields": {}, "parties": record}, {}) == ["City of Exampletown"]


def test_the_rehearsal_prints_six_units_for_the_real_shape(tmp_path, capsys):
    (tmp_path / "data").mkdir()
    (tmp_path / "job.json").write_text(json.dumps({"request_text": TWO}))
    (tmp_path / "data" / "caption.json").write_text(json.dumps(_cap(V1, V2)))
    assert parties.main([str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "units (6):" in out and f"set-6: Special Interrogatories to {CITY}" in out


# ---- the party written into the set ------------------------------------------------------


def _to(label: str, party: str) -> str:
    return one_set(label).replace("DELTA EXAMPLE", party)


def test_the_unit_party_is_written_into_its_identification_block_title_and_you():
    doc = _to(RFA, CITY).split("=== ATTORNEY")[0]
    out, notes, problems = parties.bind(doc, DISTRICT, [DISTRICT, CITY])
    assert render.party(out, "RESPONDING PARTY") == DISTRICT
    assert f"TO DEFENDANT {DISTRICT}, SET ONE" in out
    assert sets.definition_texts(out)["YOU"][2] == f'"YOU" means {DISTRICT}.'
    assert notes and problems == []
    again, none, _ = parties.bind(out, DISTRICT, [DISTRICT, CITY])
    assert again == out and none == []


def test_a_definition_of_you_still_naming_another_party_is_a_problem():
    # Markup inside the name: the swap cannot reach it, the check still reads it.
    doc = _to(RFA, DISTRICT).replace(f'"YOU" means {DISTRICT}.', '"YOU" means **CITY** OF EXAMPLETOWN.')
    _out, _notes, problems = parties.bind(doc, DISTRICT, [DISTRICT, CITY])
    assert problems == ['"YOU" is defined as another party, not ' + DISTRICT]


def test_a_set_to_another_party_keeps_its_own_you_when_aligned():
    canon = _to(RFA, CITY).split("=== ATTORNEY")[0]
    later = _to(SROG, DISTRICT).split("=== ATTORNEY")[0]
    out, _ = sets.align(later, canon, "x", keep=parties.PARTY_TERMS)
    assert sets.definition_texts(out)["YOU"][2] == f'"YOU" means {DISTRICT}.'
    assert "YOU" not in parties.drop_party_terms(sets.front_matter(canon))


# ---- end to end -------------------------------------------------------------------------


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


def _seat_and_caption():
    cap = {**CAPTION, "defendants": VALUES}  # as the 11-05 complaint's caption reads
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


def test_two_named_parties_get_every_set_each_in_one_job_and_one_delivery(tmp_path, pricing):
    cap, seat = _seat_and_caption()
    drafts = {(label, p): _to(label, p) for label in KINDS for p in (DISTRICT, CITY)}
    drafts[(RFA, CITY)] = _to(RFA, DISTRICT)  # the model wrote the wrong party: code binds the right one
    client = PartyClient(caption=[cap], by_party=drafts)
    r, v, seat = _run(tmp_path, pricing, client, request=TWO, seat=seat)
    assert v.outcome == "delivered", (v.stage, v.reason)
    plan = json.loads((r.data / "state.json").read_text())["units"]
    assert [(p["label"], p["party"]) for p in plan] == [(k, p) for p in (DISTRICT, CITY) for k in (RFA, RFP, SROG)]
    drafts_filed = [f["name"] for f in v.files if f["role"] == "draft"]
    assert len(drafts_filed) == 6
    for p in (DISTRICT, CITY):
        assert sum(f" to {p} " in n for n in drafts_filed) == 3, drafts_filed
    for i, p in enumerate(plan, 1):
        final = (r.data / f"set-{i}" / "draft-final.md").read_text()
        assert render.party(final, "RESPONDING PARTY") == p["party"], i
        assert sets.definition_texts(final)["YOU"][2] == f'"YOU" means {p["party"]}.', i
    assert client.count("DIGEST-PROMPT") == 1  # one paid read of the record for all six
    assert [f["role"] for f in v.files].count("attorney_notes") == 1


def test_the_scope_line_drafts_three_sets_to_the_district_whose_you_never_names_the_city(tmp_path, pricing):
    cap, seat = _seat_and_caption()
    drafts = {(label, DISTRICT): _to(label, CITY) for label in KINDS}  # every compose wrote the City
    client = PartyClient(caption=[cap], by_party=drafts)
    r, v, _ = _run(tmp_path, pricing, client, request=SCOPED, seat=seat)
    assert v.outcome == "delivered", (v.stage, v.reason)
    plan = json.loads((r.data / "state.json").read_text())["units"]
    assert [p["party"] for p in plan] == [DISTRICT] * 3
    for i in (1, 2, 3):
        final = (r.data / f"set-{i}" / "draft-final.md").read_text()
        you = sets.definition_texts(final)["YOU"][2]
        assert DISTRICT in you and CITY not in you.upper()
        assert render.party(final, "RESPONDING PARTY") == DISTRICT


def test_a_request_naming_no_party_of_several_fails_before_any_compose(tmp_path, pricing):
    cap, seat = _seat_and_caption()
    client = PartyClient(caption=[cap], by_party={})
    _r, v, seat = _run(tmp_path, pricing, client, request="Please draft RFAs set one to the defendant.", seat=seat)
    assert v.outcome == "failed" and v.reason.startswith("party_unknown: "), (v.stage, v.reason)
    assert client.count("COMPOSE-PROMPT") == 0 and seat.created == []


def _units_for(tmp_path, pricing, request_text, cap=None):
    from drafting_testkit import make_inputs, make_job
    from jobs.drafting.run import DraftingRun

    _c, seat = _seat_and_caption()
    make_inputs(tmp_path / "inputs")
    jd = make_job(tmp_path / "job", cls="discovery_set")
    doc = json.loads((jd / "job.json").read_text())
    doc["request_text"] = request_text
    (jd / "job.json").write_text(json.dumps(doc))
    r = DraftingRun(
        jd,
        inputs_dir=str(tmp_path / "inputs"),
        pricing=str(pricing),
        seat_factory=lambda: seat,
        client=Client(),
        log=lambda _m: None,
    )
    (r.data / "caption.json").write_text(json.dumps(cap or _cap(V1, V2)))
    return r


def test_one_set_to_one_scoped_party_keeps_its_party(tmp_path, pricing):
    r = _units_for(
        tmp_path, pricing, TWO.replace("RFA, RFPD and SROG", "RFAs") + "\n\nSMD: to City of Exampletown only."
    )
    assert [(u.key, u.party) for u in r.units()] == [("set-1", CITY)]


def test_a_request_to_one_party_of_one_plans_exactly_as_before(tmp_path, pricing):
    r = _units_for(tmp_path, pricing, "Draft RFAs set one and SROGs set one to Delta Example.", _cap("DELTA EXAMPLE"))
    assert [(u.key, u.label, u.party) for u in r.units()] == [("set-1", RFA, None), ("set-2", SROG, None)]


def test_a_plan_frozen_before_parties_resumes_unchanged(tmp_path, pricing):
    r = _units_for(tmp_path, pricing, TWO)  # a fresh plan here would be six units with parties
    frozen = [{"key": f"set-{i}", "variant": u.key, "label": u.label} for i, u in enumerate(sets.variants(TWO), 1)]
    r._put("units", frozen)
    assert [(u.key, u.label, u.party) for u in r.units()] == [(p["key"], p["label"], None) for p in frozen]
    assert json.loads((r.data / "state.json").read_text())["units"] == frozen


# ---- a request spelling with an extra word (rehearsal 4) -----------------------------------

N_DISTRICT, N_CITY = "NORTHFIELD JOINT UNION SCHOOL DISTRICT", "CITY OF NORTHFIELD"
N_VALUES = [f"{N_DISTRICT}, {N_CITY}, STATE OF CA, and DOES 1-100"]
N_TWO = (
    "Please propound discovery to Northfield Joint Union High School District and to City of Northfield. "
    "We need RFA, RFPD and SROG, set one."
)
N_SCOPED = N_TWO + "\n\nSMD: Northfield Joint Union High School District only."


def test_a_request_spelling_with_an_extra_word_names_the_caption_party():
    assert parties.resolve(N_TWO, N_VALUES).planned == [N_DISTRICT, N_CITY]
    assert parties.same("Northfield Joint Union School District", "Northfield Joint Union High School District")
    assert not parties.same("Northfield School District", "Northfield Joint Union High School District")


def test_the_scope_line_in_the_requests_spelling_narrows_to_that_party():
    assert parties.resolve(N_SCOPED, N_VALUES).planned == [N_DISTRICT]


def test_a_near_miss_spelling_stops_quoting_both_never_drops_the_party():
    req = N_TWO.replace("Northfield Joint Union High School District", "Northfield Union School District")
    with pytest.raises(parties.PartyUnknown, match=f'may mean "{N_DISTRICT}"'):
        parties.resolve(req, N_VALUES)


def test_one_request_name_inside_two_caption_parties_stops():
    values = ["NORTHFIELD SCHOOL DISTRICT", "NORTHFIELD HIGH SCHOOL DISTRICT", N_CITY]
    with pytest.raises(parties.PartyUnknown, match="could be"):
        parties.resolve("Propound discovery to Northfield High School District and to City of Northfield.", values)


def test_the_extra_word_shape_plans_six_units_and_three_for_the_scope_line(tmp_path, pricing):
    r = _units_for(tmp_path / "a", pricing, N_TWO, _cap(*N_VALUES))
    assert [u.party for u in r.units()] == [N_DISTRICT] * 3 + [N_CITY] * 3
    r = _units_for(tmp_path / "b", pricing, N_SCOPED, _cap(*N_VALUES))
    assert [u.party for u in r.units()] == [N_DISTRICT] * 3


# ---- the firm's signature and a quoted reply are not the request (rehearsal 5) --------------

SIGNED = N_TWO + "\n\nThanks,\nAlpha Example\nExample Law, 123 Main St, Northfield, CA 95555, Tel 555-555-0100"


def test_a_signature_carrying_ca_before_the_zip_plans_six_units_and_never_stops(tmp_path, pricing):
    assert parties.resolve(SIGNED, N_VALUES).planned == [N_DISTRICT, N_CITY]
    r = _units_for(tmp_path, pricing, SIGNED, _cap(*N_VALUES))
    assert [u.party for u in r.units()] == [N_DISTRICT] * 3 + [N_CITY] * 3


def test_a_near_miss_needs_two_shared_words_and_more_than_half():
    # one distinctive word, shared once, and the caption spelling not printed: one word is never enough
    one_word = [*N_VALUES, "OF THE SHERIFF, a public entity"]
    assert parties.resolve(N_TWO + " The Sheriff wrote the report.", one_word).planned == [N_DISTRICT, N_CITY]
    # two of an unserved defendant's five words: not more than half
    five = [*N_VALUES, "SOUTHBROOK VALLEY WATER IRRIGATION AGENCY, a public entity"]
    assert parties.resolve(N_TWO + " See the Southbrook Valley minutes.", five).planned == [N_DISTRICT, N_CITY]


def test_a_pure_jurisdiction_is_never_chased_as_a_near_miss():
    values = [f"{N_DISTRICT}, {N_CITY}, STATE OF CALIFORNIA, and DOES 1-100"]
    req = N_TWO + " It was filed in California State court."
    assert parties.resolve(req, values).planned == [N_DISTRICT, N_CITY]


#: A defendant the request does not serve, whose words the firm's signature shares.
UNSERVED = "SOUTHBROOK VALLEY WATER AGENCY, a public entity"


def test_the_signature_words_in_the_body_are_a_near_miss():  # the control: the strip is what saves the job
    with pytest.raises(parties.PartyUnknown, match="may mean"):
        parties.resolve(N_TWO + " Southbrook Valley Water Counsel Group asked too.", [*N_VALUES, UNSERVED])


@pytest.mark.parametrize(
    "tail",
    [
        "\n\nThanks,\nSouthbrook Valley Water Counsel Group",  # after a sign-off
        "\n\nAlpha Example\nSouthbrook Valley Water Counsel Group",  # after a line of only a name
        "\n\n> Southbrook Valley Water Agency wrote: please hold",  # a quoted reply
        "\n\nSouthbrook Valley Water Counsel Plaza, Suite 4, 95555",  # a ZIP line
        "\n\nSouthbrook Valley Water Counsel desk (555) 555-0100",  # a phone line
    ],
)
def test_signature_and_quoted_lines_are_not_read_for_near_misses(tail):
    assert parties.resolve(N_TWO + tail, [*N_VALUES, UNSERVED]).planned == [N_DISTRICT, N_CITY]


def test_a_case_number_line_naming_the_parties_is_still_read():
    req = (
        "Case No. 34567, please propound discovery to Northfield Joint Union High School District and to City of "
        "Northfield. We need RFA, RFPD and SROG, set one."
    )
    assert parties.resolve(req, N_VALUES).planned == [N_DISTRICT, N_CITY]
    # an address is still skipped: a ZIP after a state, or a street word with a five-digit number
    assert parties.request_body("Example Law, Northfield, CA 95555\n123 Main St 95555\nkeep") == "keep"


def test_a_bracketed_smd_scope_note_after_a_signature_narrows_to_one_party():
    note = "\n\n[SMD, at the firm's direction: to Northfield Joint Union High School District only.]"
    assert parties.resolve(SIGNED + note, N_VALUES).planned == [N_DISTRICT]


def test_smds_hand_line_after_a_signature_is_still_read():
    assert parties.resolve(SIGNED + "\n\nSMD: Northfield Joint Union High School District only.", N_VALUES).planned == [
        N_DISTRICT
    ]
