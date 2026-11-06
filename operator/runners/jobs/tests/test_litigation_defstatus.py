"""A defaulted defendant never prints "Served, no answer in file" (2026-11-02).

On the A&P list three defendants on two cases printed "Served, no answer in
file", one for 3,430 days, beside their own note "default entered <date>":
the seed matched status text by prefix with "served" ahead of "default", and
no gate compared a status with its note. All data here is synthetic.
"""

from __future__ import annotations

import copy
import datetime as dt

import pytest

from jobs.litigation import baseline, defstatus, gates, parity, vocab

TODAY = dt.date(2026, 11, 2)


def src(fid: str = "f1") -> dict:
    return {"file_id": fid, "name": "POS Delta.pdf", "doc_date": None}


def defendant(status: str, flags: list[str], answered: str | None = None) -> dict:
    return {
        "name": "Delta Example",
        "status": status,
        "served": {"date": "2017-03-01", "method": "personal", "source": src()},
        "answered": {"date": answered, "source": src("f3") if answered else None},
        "sent_for_service": {"date": "2017-02-20", "source": src("f6")},
        "flags": flags,
    }


def matter(*ds: dict) -> dict:
    return {
        "matter_id": "m1",
        "number": "100001",
        "responsible": "Alpha Example",
        "case_status": {"value": vocab.ACTIVE, "source": src("f0")},
        "complaint_filed": {"date": "2016-12-01", "source": src("f0")},
        "next_court_date": {"date": None, "source": None},
        "defendants": list(ds),
        "matter_flags": [],
    }


# ---- the seed converter: by rule, not by prefix ------------------------------------------
@pytest.mark.parametrize(
    "raw,want",
    [
        ("served 3/1/2017; default entered 1/5/2018", vocab.DEFAULT),  # the defect, verbatim shape
        ("Served; default entered 2018-01-05", vocab.DEFAULT),
        ("served, default judgment 2019-02-01", vocab.DEFAULT),
        ("Served 2017-03-01, later dismissed 2019-04-02", vocab.D_DISMISSED),
        ("served; answered 2017-04-01", vocab.ANSWERED),
        ("not served; dismissed without prejudice", vocab.D_DISMISSED),
        ("served, default entered, then dismissed", vocab.D_DISMISSED),
        ("Served, settled with plaintiff", vocab.D_SETTLED),
        # Exact vocabulary and the prefix table still decide everything else.
        (vocab.SERVED_NO_ANSWER, vocab.SERVED_NO_ANSWER),
        (vocab.ANSWERED_ORIGINAL, vocab.ANSWERED_ORIGINAL),
        (vocab.SERVICE_REJECTED, vocab.SERVICE_REJECTED),
        ("served 3/1/2017", vocab.SERVED_NO_ANSWER),
        ("served; no answer filed", vocab.SERVED_NO_ANSWER),
        ("served; request for entry of default filed", vocab.SERVED_NO_ANSWER),
        ("served; default not entered", vocab.SERVED_NO_ANSWER),
        ("served; default entered then set aside", vocab.SERVED_NO_ANSWER),
        ("served; motion to dismiss pending", vocab.SERVED_NO_ANSWER),
        ("served; answered the interrogatories", vocab.SERVED_NO_ANSWER),
        ("default", vocab.DEFAULT),
        ("not served", vocab.NOT_SERVED),
        ("", vocab.D_UNCLEAR),
    ],
)
def test_def_status_by_rule(raw: str, want: str) -> None:
    assert baseline.def_status(raw) == want


def test_default_and_answer_both_stated_is_unclear_not_a_guess() -> None:
    assert defstatus.stated("default entered 2018-01-05; answered 2018-03-01") == vocab.D_UNCLEAR
    # An undone default leaves the answer.
    assert defstatus.stated("default entered, set aside 2018-02-01; answered 2018-03-01") == vocab.ANSWERED


# ---- the gate: a no-answer status beside a note that states otherwise ------------------------
def _issues(m: dict) -> list:
    return gates.rule_matters([m], TODAY)[gates.NOTE_RULE]


def test_gate_refuses_served_no_answer_beside_a_default_note() -> None:
    m = matter(defendant(vocab.SERVED_NO_ANSWER, ["Default entered 2018-01-05."]))
    assert _issues(m) == [("100001", "Delta Example", vocab.SERVED_NO_ANSWER, vocab.DEFAULT)]
    assert gates.run(
        [m], today=TODAY, files={"m1": {"f0", "f1", "f3", "f6"}}, saved={}, text_of=lambda *_: "", hits={}
    )["issues"][gates.NOTE_RULE]


@pytest.mark.parametrize(
    "status,flags,answered,want",
    [
        (vocab.SERVED_NO_ANSWER, ["Dismissed without prejudice 2019-04-02."], None, vocab.D_DISMISSED),
        (vocab.APPEARED_NO_ANSWER, ["Defendant answered 2017-05-01."], "2017-05-01", vocab.ANSWERED),
        (vocab.NOT_SERVED, ["Default entered 2018-01-05."], None, vocab.DEFAULT),
    ],
)
def test_gate_refuses_each_outranking_note(status: str, flags: list[str], answered: str | None, want: str) -> None:
    assert _issues(matter(defendant(status, flags, answered)))[0][3] == want


@pytest.mark.parametrize(
    "status,flags",
    [
        (vocab.SERVED_NO_ANSWER, ["No answer in the file; request for entry of default not yet filed."]),
        (vocab.SERVED_NO_ANSWER, ["Answer due 2017-04-01."]),
        (vocab.SERVED_NO_ANSWER, []),
        (vocab.DEFAULT, ["Default entered 2018-01-05."]),  # the two agree
        (vocab.D_DISMISSED, ["Served 2017-03-01; dismissed 2019-04-02."]),
    ],
)
def test_gate_passes_when_status_and_note_agree(status: str, flags: list[str]) -> None:
    assert _issues(matter(defendant(status, flags))) == []


# ---- reconcile: the firm reads the specific status, with its note ------------------------
def test_reconcile_prints_the_notes_status_and_the_gate_then_passes() -> None:
    m = matter(
        defendant(vocab.SERVED_NO_ANSWER, ["Default entered 2018-01-05."]),
        {**defendant(vocab.SERVED_NO_ANSWER, []), "name": "Echo Example"},
    )
    log = defstatus.reconcile(m)
    assert [d["status"] for d in m["defendants"]] == [vocab.DEFAULT, vocab.SERVED_NO_ANSWER]
    assert m["defendants"][0]["flags"] == ["Default entered 2018-01-05."]  # the note still prints
    assert log == [
        {
            "path": "defendants[Delta Example].status",
            "old": vocab.SERVED_NO_ANSWER,
            "new": vocab.DEFAULT,
            "source": None,
            "why": "the defendant's own note states this status",
            "pass": "status-note",
        }
    ]
    assert _issues(m) == []


def test_reconcile_never_prints_answered_without_an_answer_date() -> None:
    m = matter(defendant(vocab.SERVED_NO_ANSWER, ["Defendant filed an answer 2026-03-02 per counsel's email."]))
    defstatus.reconcile(m)
    assert m["defendants"][0]["status"] == vocab.D_UNCLEAR
    out = gates.rule_matters([m], TODAY)
    assert out[gates.NOTE_RULE] == [] and out["answered but no answer date"] == []


@pytest.mark.parametrize(
    "status,note",
    [
        # 2026-11-06, one real list: six notes that name a state they do not find.
        (vocab.APPEARED_NO_ANSWER, "No default entry was found either, so the answered date stays null."),
        (vocab.SERVED_NO_ANSWER, "The claims administrator asked in August 2026 that the County be dismissed."),
        (vocab.NOT_SERVED, "The CMC form checks the box saying all parties were served, appeared or were dismissed."),
        (
            vocab.NOT_SERVED,
            "She was never served as an individual; her Estate (DOE 1) was served and answered instead.",
        ),
        (vocab.APPEARED_NO_ANSWER, "The plaintiff's own statement lists her as answered, but no answer supports it."),
        (vocab.APPEARED_NO_ANSWER, "Defense propounded discovery, which plaintiff answered on 2026-10-18."),
    ],
)
def test_a_note_that_names_a_state_without_finding_it_changes_nothing(status: str, note: str) -> None:
    m = matter(defendant(status, [note]))
    assert defstatus.reconcile(m) == [] and m["defendants"][0]["status"] == status
    assert gates.rule_matters([m], TODAY)[gates.NOTE_RULE] == []


# ---- parity: the corrected status is explained, not held -----------------------------------
def test_parity_explains_the_status_the_prior_note_stated() -> None:
    prior = matter(defendant(vocab.SERVED_NO_ANSWER, ["Default entered 2018-01-05."]))
    new = copy.deepcopy(prior)
    defstatus.reconcile(new)
    cs = parity.compare(prior, new, moved_files=set(), overturns=[], today=TODAY)
    [c] = [c for c in cs if c["path"].endswith(".status")]
    assert c["explained"] and "own note" in c["why"]


def test_parity_still_holds_a_status_change_the_note_does_not_state() -> None:
    prior = matter(defendant(vocab.SERVED_NO_ANSWER, ["Default entered 2018-01-05."]))
    new = copy.deepcopy(prior)
    new["defendants"][0]["status"] = vocab.D_SETTLED
    cs = parity.compare(prior, new, moved_files=set(), overturns=[], today=TODAY)
    assert [c["path"] for c in parity.unexplained(cs)] == ["defendants[deltaexample].status"]


def test_the_run_prints_the_notes_status_before_the_gates(tmp_path, pricing_path):
    """``LitigationRun.final`` (what the gates, parity and the list read)
    settles the contradiction, so the list is neither wrong nor held."""
    import json

    from litigation_testkit import M1, LitSeat, ScriptedLit, make_inputs, make_job, matter_docs
    from jobs.litigation import manifest
    from jobs.litigation.run import LitigationRun

    (tmp_path / "pricing.json").write_text(pricing_path.read_text())
    seat = LitSeat({M1: matter_docs()})
    r = LitigationRun(
        make_job(tmp_path / "job"),
        inputs_dir=str(make_inputs(tmp_path / "inputs")),
        pricing=str(tmp_path / "pricing.json"),
        state_dir=tmp_path / "state",
        seat_factory=lambda: seat,
        client=ScriptedLit(),
        log=lambda m: None,
        today=lambda: dt.date(2026, 10, 7),
        readback_pause=0.0,
    )
    assert r.run().verdict == "delivered"
    bad = copy.deepcopy(manifest.load_prior(tmp_path / "state", M1))
    d = bad["defendants"][0]
    d.update(status=vocab.SERVED_NO_ANSWER, answered={"date": None, "source": None})
    d["flags"] = ["Default entered 2026-06-01."]
    r._mfile(M1, "read3.json").write_text(json.dumps({"result": bad, "overturns": []}))
    m = next(x for x in r._matters() if x["id"] == M1)
    out = r.final(m)
    assert out["defendants"][0]["status"] == vocab.DEFAULT
    assert out["defendants"][0]["flags"] == ["Default entered 2026-06-01."]
    assert [o["pass"] for o in out["overturns"]] == ["status-note"]
    assert gates.rule_matters([out], TODAY)[gates.NOTE_RULE] == []


def test_a_read_value_with_an_em_dash_reaches_the_gates_without_one(tmp_path, pricing_path):
    """2026-10-23: a court field written "A \u2014 B" held a whole list on the em
    dash rule, though the loader would have written ", "."""
    import json

    from litigation_testkit import M1, LitSeat, ScriptedLit, make_inputs, make_job, matter_docs
    from jobs.litigation import manifest
    from jobs.litigation.run import LitigationRun

    (tmp_path / "pricing.json").write_text(pricing_path.read_text())
    r = LitigationRun(
        make_job(tmp_path / "job"),
        inputs_dir=str(make_inputs(tmp_path / "inputs")),
        pricing=str(tmp_path / "pricing.json"),
        state_dir=tmp_path / "state",
        seat_factory=lambda: LitSeat({M1: matter_docs()}),
        client=ScriptedLit(),
        log=lambda m: None,
        today=lambda: dt.date(2026, 10, 7),
        readback_pause=0.0,
    )
    assert r.run().verdict == "delivered"
    res = copy.deepcopy(manifest.load_prior(tmp_path / "state", M1))
    res["court"] = "Superior Court \u2014 Example County"
    r._mfile(M1, "read3.json").write_text(json.dumps({"result": res, "overturns": []}))
    out = r.final(next(x for x in r._matters() if x["id"] == M1))
    assert out["court"] == "Superior Court, Example County"
    assert gates.rule_matters([out], TODAY)["em dash in text"] == []
