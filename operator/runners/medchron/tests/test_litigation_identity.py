"""Same paper, different words; one defendant spelled two ways; a value the
read left out on an unchanged document (2026-10-14 proof run: 29 parity holds,
almost all of them these three classes). Every name and value here is
synthetic; the shapes are the ones the proof run produced."""

from __future__ import annotations

import copy
import datetime as dt
import json

from medchron.litigation import carry, identity, parity, read, update, vocab

TODAY = dt.date(2026, 10, 14)


def src(fid: str) -> dict:
    return {"file_id": fid, "name": f"{fid}.pdf", "doc_date": None}


def theirs(name: str, party: str, date: str | None, fid: str, resp: dict | None = None) -> dict:
    return {
        "set": name,
        "served_by": party,
        "date": date,
        "source": src(fid),
        "responses_served": resp or {"value": "no", "date": None, "source": None},
    }


def ours(name: str, party: str, date: str | None, fid: str) -> dict:
    return {"set": name, "served_on": party, "date": date, "source": src(fid)}


def matter(**kw) -> dict:
    base = {
        "case_status": {"value": vocab.ACTIVE, "source": src("fc")},
        "complaint_filed": {"date": "2026-01-05", "source": src("fc")},
        "next_court_date": {"date": None, "source": None},
        "defendants": [],
        "discovery_propounded": [],
        "discovery_served_on_client": [],
    }
    return {**base, **kw}


def unexplained(prior: dict, new: dict, **kw) -> list[str]:
    args = {"moved_files": set(), "overturns": [], "today": TODAY, **kw}
    return [c["path"] for c in parity.unexplained(parity.compare(prior, new, **args))]


# ---- 1. same paper, different words -----------------------------------------------------
def test_a_renamed_set_is_the_same_set():
    party = "Defendant Quill Example"
    prior = matter(
        discovery_served_on_client=[
            theirs("Form Interrogatories, Set One", party, "2026-09-01", "fa"),
            theirs("Requests for Production, Set One", party, "2026-09-01", "fb"),
            theirs("Deposition notice", party, "2026-09-01", "fd", {"value": "unclear", "date": None, "source": None}),
        ]
    )
    new = matter(
        discovery_served_on_client=[
            theirs("Form Interrogatories - General, Set One", "Quill Example", "2026-09-01", "fz"),
            theirs(
                "Demand for Production and Inspection of Documents, Set One (due 2026-10-30)", party, "2026-09-01", "fz"
            ),
            theirs(
                "Notice of Deposition of Plaintiff with request to produce documents (set for 2026-12-01 by video)",
                party,
                "2026-09-01",
                "fz",
                {"value": "unclear", "date": None, "source": None},
            ),
        ]
    )
    cs = parity.compare(prior, new, moved_files=set(), overturns=[], today=TODAY)
    assert [c for c in cs if c["path"].startswith("discovery")] == []
    other = copy.deepcopy(new)
    other["discovery_served_on_client"][0]["set"] = "Requests for Production of Documents, Set One"
    other["discovery_served_on_client"][1]["set"] = "Form Interrogatories, Set 1"
    assert unexplained(prior, other) == []


def test_a_real_change_on_the_same_paper_still_holds():
    prior = matter(
        discovery_served_on_client=[theirs("Form Interrogatories, Set One", "Quill Example", "2026-09-01", "fa")]
    )
    new = copy.deepcopy(prior)
    new["discovery_served_on_client"][0]["set"] = "Form Interrogatories - General, Set One"
    new["discovery_served_on_client"][0]["date"] = "2026-09-03"
    bad = unexplained(prior, new)
    assert bad == ["discovery_served_on_client[form_interrogatories#1|quillexample].date"]
    cs = parity.compare(prior, new, moved_files=set(), overturns=[], today=TODAY)
    assert cs[0]["label"].startswith("discovery_served_on_client[Form Interrogatories - General, Set One|")


def test_a_different_set_number_or_kind_is_a_different_set():
    assert identity.set_identity("Special Interrogatories, Set Two") != identity.set_identity(
        "Special Interrogatories, Set One"
    )
    assert identity.set_identity("Form Interrogatories - Employment, Set One") != identity.set_identity(
        "Form Interrogatories - General, Set One"
    )
    assert identity.set_kinds("Notice of Deposition and Request for Production of Documents") == ["deposition"]


def test_a_combined_name_is_one_row_per_set_and_matches_separate_rows():
    party = "Ember Example LLC"
    names = ("Form Interrogatories", "Special Interrogatories", "Requests for Admission", "Requests for Production")
    prior = matter(
        discovery_propounded=[ours(f"{n}, Set One", party, "2026-08-06", f"p{i}") for i, n in enumerate(names)]
    )
    combined = (
        "Form Interrogatories, Special Interrogatories, Requests for Admission and Requests for Production, Set One"
    )
    new = matter(discovery_propounded=[ours(combined, party, "2026-08-06", "pmail")])
    assert unexplained(prior, new) == []
    rows = identity.split_rows(new["discovery_propounded"])
    assert sorted(r["set"] for r in rows) == sorted(f"{n}, Set One" for n in names)
    assert all(r["date"] == "2026-08-06" and r["source"]["file_id"] == "pmail" for r in rows)
    moved = copy.deepcopy(new)
    moved["discovery_propounded"][0]["date"] = "2026-08-09"
    assert len(unexplained(prior, moved)) == 4  # a real change on the combined paper shows on every set


def test_the_read_and_the_update_guard_split_a_combined_set():
    class Ctx:
        def source(self, doc, doc_date=None):
            return src(f"d{doc}") if doc else None

    raw = {
        "discovery_propounded": [
            {
                "set": "Form Interrogatories and Requests for Admission, Set Two",
                "served_on": "X",
                "date": "2026-08-01",
                "doc": 3,
            }
        ]
    }
    out = read.to_state(Ctx(), raw, [vocab.GROUP_DISCOVERY])
    assert [r["set"] for r in out["discovery_propounded"]] == [
        "Form Interrogatories, Set Two",
        "Requests for Admission, Set Two",
    ]
    prior = {"discovery_propounded": out["discovery_propounded"], "discovery_served_on_client": []}
    fresh = {**prior, "discovery_propounded": [{**raw["discovery_propounded"][0], "source": src("d3")}]}
    got, log = update.guard(prior, fresh, [vocab.GROUP_DISCOVERY], [], {})
    assert log == {"changed": [], "reverted": []} and len(got["discovery_propounded"]) == 2


def test_party_identity_drops_aliases_and_role_words_but_keeps_doe_numbers():
    pk = identity.party_key
    assert pk("Harbor Builders, Inc. (erroneously sued as Harbour Builders)") == pk("Harbor Builders, Inc.")
    assert pk("Defendant Quill Example") == pk("Quill Example")
    assert pk("Defendants Quill Example") == pk("Quill Example")
    assert pk("Doe 1") != pk("Doe 2")
    assert pk("Doe 2") == pk("DOE 2")
    assert pk("Juniper Homes LP (Doe 2)") == pk("Juniper Homes LP")
    assert pk("Juniper Homes LP (Doe 1)") != pk("Juniper Villas LP (Doe 2)")
    prior = matter(
        discovery_served_on_client=[
            theirs(
                "Requests for Production, Set One",
                "Harbor Builders, Inc. (erroneously sued as Harbour Builders)",
                "2026-07-24",
                "fh",
            )
        ]
    )
    new = matter(
        discovery_served_on_client=[
            theirs("Requests for Production, Set One", "Harbor Builders, Inc.", "2026-07-24", "fh")
        ]
    )
    assert unexplained(prior, new) == []


# ---- 2. one defendant, two spellings ----------------------------------------------------
def _d(name: str, status: str, ans: str | None = None) -> dict:
    return {
        "name": name,
        "status": status,
        "served": {"date": "2026-05-01" if ans else None, "method": None, "source": src("fs") if ans else None},
        "answered": {"date": ans, "source": src("fa") if ans else None},
        "flags": [],
    }


def test_a_one_letter_typo_of_a_given_name_is_one_defendant():
    ds = identity.merge_defendants(
        [_d("Rashidan Okonwo", vocab.ANSWERED, "2026-09-28"), _d("Rshidan Okonwo", vocab.D_UNCLEAR)]
    )
    assert [(d["name"], d["status"]) for d in ds] == [("Rashidan Okonwo", vocab.ANSWERED)]
    # whichever comes first, the better-read entry is kept
    ds = identity.merge_defendants(
        [_d("Rshidan Okonwo", vocab.D_UNCLEAR), _d("Rashidan Okonwo", vocab.ANSWERED, "2026-09-28")]
    )
    assert [(d["name"], d["status"]) for d in ds] == [("Rashidan Okonwo", vocab.ANSWERED)]
    prior = matter(defendants=[_d("Rashidan Okonwo", vocab.ANSWERED, "2026-09-28")])
    new = matter(
        defendants=[_d("Rashidan Okonwo", vocab.ANSWERED, "2026-09-28"), _d("Rshidan Okonwo", vocab.D_UNCLEAR)]
    )
    assert unexplained(prior, new) != []  # parity alone would hold; the merge step folds it
    merged = read.merge(None, {"defendants": new["defendants"]}, [vocab.GROUP_DEFENDANTS])
    assert len(merged["defendants"]) == 1
    carry.carry_forward(prior, new, moved=set(), on_matter=set(), saved={}, today=TODAY)
    assert unexplained(prior, new) == []


def test_two_different_people_are_never_merged():
    pairs = [
        ("Rashidan Okonwo", "Rashidan Okonkwo-Bell"),  # different surname
        ("Rashidan Okonwo", "Rashidan Okafor"),  # different surname
        ("Marisol Vance", "Marisa Vance"),  # given names two letters apart
        ("Ana Vance", "Ann Vance"),  # too short to call a typo
        ("Doe 1", "Doe 2"),
        ("Juniper Homes LP", "Juniper Villas LP"),
        ("Leo Marsh Sr", "Leo Marsh Jr"),  # the last words differ
        ("Okonwo", "Okonwa"),  # one word: no given name to call a typo
    ]
    for a, b in pairs:
        assert not identity.same_person(a, b), (a, b)
        assert len(identity.merge_defendants([_d(a, vocab.ANSWERED, "2026-09-01"), _d(b, vocab.NOT_SERVED)])) == 2


# ---- 3. a value the read left out on an unchanged document -------------------------------
def _carry(prior, new, **kw):
    args = {"moved": set(), "on_matter": {"fa", "fb", "fs", "fc"}, "saved": {}, "today": TODAY, **kw}
    return carry.carry_forward(prior, new, **args)


def _vanish():
    prior = matter(
        discovery_served_on_client=[theirs("Form Interrogatories, Set One", "Quill Example", "2026-06-12", "fa")]
    )
    new = copy.deepcopy(prior)
    new["discovery_served_on_client"][0]["date"] = None
    return prior, new


def test_a_vanished_value_on_an_unchanged_source_is_carried_forward():
    prior, new = _vanish()
    assert unexplained(prior, new) == ["discovery_served_on_client[form_interrogatories#1|quillexample].date"]
    assert _carry(prior, new) == ["discovery_served_on_client[form_interrogatories#1|quillexample].date"]
    assert new["discovery_served_on_client"][0]["date"] == "2026-06-12"
    assert unexplained(prior, new) == []


def test_a_vanished_value_is_not_carried_when_its_source_moved_or_left():
    prior, new = _vanish()
    assert _carry(prior, new, moved={"fa"}) == [] and new["discovery_served_on_client"][0]["date"] is None
    assert unexplained(prior, new, moved_files={"fa"})  # changed paper, no date read: a person looks
    prior, new = _vanish()
    assert _carry(prior, new, on_matter={"fb"}) == []


def test_a_seed_value_read_again_is_not_carried_and_its_drop_is_explained():
    """``seedcheck``: a date that is its file's save date and is not in the
    file's text was read again on purpose; it never rides forward."""
    prior, new = _vanish()
    pair = {("fa", "2026-06-12")}
    assert _carry(prior, new, unsupported=pair) == []
    assert unexplained(prior, new, unsupported=pair) == []
    cs = parity.compare(prior, new, moved_files=set(), overturns=[], today=TODAY, unsupported=pair)
    assert cs and "not supported" in cs[0]["why"]


def test_a_save_date_rides_forward_only_when_the_text_carries_it():
    prior, new = _vanish()
    saved = {"fa": {"2026-06-12"}}
    assert _carry(prior, new, saved=saved) == []  # no text held: the gates would refuse it
    assert _carry(prior, new, saved=saved, text_of=lambda fid: "served June 2, 2026") == []
    assert _carry(prior, new, saved=saved, text_of=lambda fid: "served June 12, 2026")


def test_a_different_answer_is_never_overwritten():
    prior, new = _vanish()
    new["discovery_served_on_client"][0]["date"] = "2026-06-20"
    assert _carry(prior, new) == [] and new["discovery_served_on_client"][0]["date"] == "2026-06-20"
    prior = matter(
        discovery_served_on_client=[
            theirs(
                "Requests for Admission, Set One",
                "Quill Example",
                "2026-06-12",
                "fa",
                {"value": "yes", "date": "2026-07-01", "source": src("fb")},
            )
        ]
    )
    new = copy.deepcopy(prior)
    new["discovery_served_on_client"][0]["responses_served"] = {"value": "no", "date": None, "source": None}
    assert _carry(prior, new) == []
    new["discovery_served_on_client"][0]["responses_served"] = {"value": None, "date": None, "source": None}
    assert _carry(prior, new) == ["discovery_served_on_client[admission#1|quillexample].responses"]


def test_a_defendant_date_is_carried_only_where_the_status_allows_it():
    prior = matter(defendants=[_d("Quill Example", vocab.ANSWERED, "2026-04-30")])
    new = copy.deepcopy(prior)
    new["defendants"][0]["answered"] = {"date": None, "source": None}
    assert _carry(prior, new) == ["defendants[Quill Example].answered"]
    new = copy.deepcopy(prior)
    new["defendants"][0].update(status=vocab.SERVED_NO_ANSWER, answered={"date": None, "source": None})
    assert _carry(prior, new) == []  # the read says no answer: that is its answer, not an omission
    new = copy.deepcopy(prior)
    new["defendants"][0].update(
        status=vocab.NOT_SERVED, served={"date": None, "source": None}, answered={"date": None, "source": None}
    )
    assert _carry(prior, new) == []


def test_court_dates_carry_only_while_they_hold():
    prior = matter(next_court_date={"date": "2026-11-20", "event": "CMC", "source": src("fc")})
    new = copy.deepcopy(prior)
    new["next_court_date"] = {"date": None, "event": "", "source": None}
    assert _carry(prior, new) == ["next_court_date"]
    new["next_court_date"] = {"date": None, "event": "", "source": None}
    assert _carry(prior, new, today=dt.date(2026, 11, 21)) == []


def test_the_run_carries_forward_before_the_gates_and_parity(tmp_path, pricing_path):
    """``LitigationRun.final`` is what the gates, parity and the workbook read."""
    from litigation_testkit import M1, LitSeat, ScriptedLit, make_inputs, make_job, matter_docs
    from medchron.litigation import manifest
    from medchron.litigation.run import LitigationRun

    (tmp_path / "pricing.json").write_text(pricing_path.read_text())

    def make(job: str, **kw):
        return LitigationRun(
            make_job(tmp_path / job, **kw),
            inputs_dir=str(make_inputs(tmp_path / "inputs")),
            pricing=str(tmp_path / "pricing.json"),
            state_dir=tmp_path / "state",
            seat_factory=lambda: seat,
            client=ScriptedLit(),
            log=lambda m: None,
            today=lambda: dt.date(2026, 10, 7),
            readback_pause=0.0,
        )

    seat = LitSeat({M1: matter_docs()})
    r = make("job")
    assert r.run().verdict == "delivered"
    prior = manifest.load_prior(tmp_path / "state", M1)
    assert prior["defendants"][0]["answered"]["date"] == "2026-04-30"
    dropped = copy.deepcopy(prior)
    dropped["defendants"][0]["answered"] = {"date": None, "source": None}
    dropped["defendants"].append(
        {**copy.deepcopy(prior["defendants"][0]), "name": "Dlta Example", "status": vocab.D_UNCLEAR}
    )
    r._mfile(M1, "read3.json").write_text(json.dumps({"result": dropped, "overturns": []}))
    plan = r._plan()
    plan[M1]["moved"] = []  # the second run: nothing on the matter changed
    (r.data / "plan.json").write_text(json.dumps(plan))
    m = next(x for x in r._matters() if x["id"] == M1)
    out = r.final(m)
    assert out["defendants"][0]["answered"]["date"] == "2026-04-30"
    assert len(out["defendants"]) == 1
