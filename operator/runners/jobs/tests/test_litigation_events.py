"""Events in, status out (ADR 0104 mechanism 6). A read reports dated events and
court dates; code derives the status and the next court date, applies the
checks' verdicts, and reads a report stored before any of this as it was."""

from __future__ import annotations

import copy
import datetime as dt
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from litigation_testkit import M1, LitSeat, ScriptedLit, good_result, make_inputs, make_job, matter_docs
from jobs.litigation import answerkey, answers, carry, defstatus, gates, parity, read, update, vocab
from jobs.litigation.run import LitigationRun
from jobs.litigation.tools import MatterContext
from jobs_testkit import make_pdf

TODAY = dt.date(2026, 10, 7)
KEY = json.loads((Path(__file__).parent / "litigation_status_answer_key.json").read_text(encoding="utf-8"))


@pytest.fixture(autouse=True)
def _pricing(tmp_path: Path, pricing_path: Path) -> None:
    (tmp_path / "pricing.json").write_text(pricing_path.read_text())


def _ctx(tmp_path: Path, seat: LitSeat | None = None) -> MatterContext:
    seat = seat or LitSeat({M1: matter_docs()})
    return MatterContext(M1, seat.list_files(M1), tmp_path, 40, seat=seat, log=lambda m: None)


def _nums(ctx: MatterContext) -> dict[str, int]:
    return {k: ctx.by_id[k] for k in ("f-c1", "f-p1", "f-a1")}


def _src(fid: str = "f1") -> dict:
    return {"file_id": fid, "name": f"{fid}.pdf", "doc_date": None}


def _dated(date: str, fid: str = "f1") -> dict:
    return {"date": date, "source": _src(fid)}


def _run(
    tmp_path: Path, client: ScriptedLit, seat: LitSeat, job: str = "job", job_id: str = "01KTJ0BX0000000000000000AA"
):
    return LitigationRun(
        make_job(tmp_path / job, job_id=job_id),
        inputs_dir=str(make_inputs(tmp_path / "inputs")),
        pricing=str(tmp_path / "pricing.json"),
        state_dir=tmp_path / "state",
        seat_factory=lambda: seat,
        client=client,
        log=lambda m: None,
        today=lambda: TODAY,
        readback_pause=0.0,
    )


def _arrive(seat: LitSeat, fid: str, name: str, text: str, when: str = "2026-09-30T00:00:00") -> None:
    blob = make_pdf([text])
    seat.blobs[fid] = blob
    seat.per[M1].append(
        {
            "id": fid,
            "name": name,
            "ext": ".pdf",
            "size": len(blob),
            "created": when,
            "modified": when,
            "folderId": None,
            "deleted": False,
        }
    )


# ---- the answer key: the table the firm's list rests on --------------------------------------
@pytest.mark.parametrize("row", KEY["rows"], ids=[r["id"] for r in KEY["rows"]])
def test_the_answer_key(row: dict) -> None:
    got = defstatus.decide(answerkey.defendant(row), dt.date.fromisoformat(KEY["today"]), row.get("stored_status"))
    assert (got.status, got.rule) == (row["status"], row["rule"])


def test_the_answer_key_has_a_row_for_every_rule() -> None:
    assert {r for r, _t, _s, _f in defstatus._TABLE} <= {row["rule"] for row in KEY["rows"]}


def test_the_key_checker_names_a_row_that_differs() -> None:
    assert answerkey.check(KEY) == []
    bad = copy.deepcopy(KEY)
    bad["rows"][0]["status"] = vocab.NOT_SERVED
    assert [x["id"] for x in answerkey.check(bad)] == [KEY["rows"][0]["id"]]


# ---- what a read reports, and what code works out ----------------------------------------------
def test_no_read_can_return_a_status() -> None:
    props = read.RESULT_SCHEMA["properties"]["defendants"]["items"]
    assert "status" not in props["properties"] and props["required"] == ["name"]
    assert set(vocab.DEFENDANT_EVENTS) <= set(props["properties"])
    assert props["properties"]["sent_for_service"]["type"] == "array"
    assert "next_court_date" not in read.RESULT_SCHEMA["properties"]
    assert read.RESULT_SCHEMA["properties"]["court_dates"]["type"] == "array"


def test_a_typed_status_is_not_read(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    raw = good_result(_nums(ctx))
    raw["defendants"][0]["status"] = vocab.D_DISMISSED
    d = answers.to_state(ctx, raw, list(vocab.GROUPS), TODAY)["defendants"][0]
    assert d["status"] == vocab.ANSWERED and d["status_from"] == vocab.FROM_DERIVED


@pytest.mark.parametrize("role", ["read", "verify", "audit"])
def test_the_prompt_asks_for_events_and_no_status(tmp_path: Path, role: str) -> None:
    firm = SimpleNamespace(data={"form_hints": {}})
    text = read.system_for(role, firm, TODAY)
    assert "Report every dated event you find, each with its document" in text
    assert "do not\nreport a status" in text
    assert "must be the next one AFTER today" not in text and "record\nthe most recent request" not in text


def test_each_event_reaches_state_and_a_null_date_does_not(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    doc = ctx.by_id["f-p1"]
    raw = {"name": "X", **{f: {"date": "2026-05-01", "doc": doc} for f in ("dismissed", "default_entered")}}
    raw["settled"] = {"date": None, "doc": doc}  # a document cited for a negative
    d = answers.defendant(ctx, raw, TODAY)
    assert d["dismissed"]["date"] == "2026-05-01" and d["dismissed"]["source"]["name"] == "POS Delta.pdf"
    assert "settled" not in d and d["status"] == vocab.D_DISMISSED


def test_the_most_recent_request_is_codes_pick(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    doc = ctx.by_id["f-p1"]
    rows = [{"date": "2026-03-20", "doc": doc}, {"date": "2026-05-02", "doc": doc}, {"date": "2026-04-01", "doc": doc}]
    assert answers.latest_sent(ctx, rows)["date"] == "2026-05-02"
    assert answers.latest_sent(ctx, []) == {"date": None, "source": None}
    assert answers.latest_sent(ctx, {"date": "2026-03-20", "doc": doc})["date"] == "2026-03-20"
    # A date that is not a date goes to the gate; it is never ordered with the rest.
    assert answers.latest_sent(ctx, [*rows, {"date": "soon", "doc": doc}])["date"] == "soon"


def test_the_next_court_date_is_codes_pick(tmp_path: Path) -> None:
    rows = [
        {"date": "2026-12-01", "event": "Trial", "source": _src()},
        {"date": "2026-10-07", "event": "CMC", "source": _src()},
        {"date": "2026-09-01", "event": "Old CMC", "source": _src()},
    ]
    assert answers.next_court(rows, TODAY)["event"] == "CMC"  # today counts
    assert answers.next_court(rows, TODAY + dt.timedelta(days=1))["event"] == "Trial"
    assert answers.next_court(rows, dt.date(2027, 1, 1)) == {"date": None, "event": None, "source": None}
    assert answers.next_court([{"date": "soon", "event": "CMC", "source": None}, *rows], TODAY)["date"] == "soon"


def test_a_passed_court_date_moves_on_to_the_next_without_a_read(tmp_path: Path) -> None:
    m = {
        "court_dates_read": [
            {"date": "2026-10-01", "event": "CMC", "source": _src()},
            {"date": "2026-12-01", "event": "Trial", "source": _src()},
        ],
        "next_court_date": {"date": "2026-10-01", "event": "CMC", "source": _src()},
    }
    answers.repick_court(m, TODAY)
    assert m["next_court_date"]["event"] == "Trial"
    legacy = {"next_court_date": {"date": "2026-10-01", "event": "CMC", "source": _src()}}
    answers.repick_court(legacy, TODAY)
    assert legacy["next_court_date"]["date"] == "2026-10-01"  # no list: the report keeps its value


@pytest.mark.parametrize("bad", ["co-counsel", None, 7])
def test_a_firm_role_it_cannot_read_is_unclear_and_flagged(tmp_path: Path, bad: object) -> None:
    ctx = _ctx(tmp_path)
    raw = good_result(_nums(ctx))
    raw["firm_role"] = bad
    out = answers.to_state(ctx, raw, list(vocab.GROUPS), TODAY)
    assert out["firm_role"] == vocab.UNCLEAR and answers.FIRM_ROLE_UNCLEAR in out["matter_flags"]
    raw["firm_role"] = "defense"
    out = answers.to_state(ctx, raw, list(vocab.GROUPS), TODAY)
    assert out["firm_role"] == "defense" and answers.FIRM_ROLE_UNCLEAR not in out["matter_flags"]


# ---- verdicts -----------------------------------------------------------------------------------
def _result(ctx: MatterContext) -> dict:
    return answers.to_state(ctx, good_result(_nums(ctx)), list(vocab.GROUPS), TODAY)


def test_a_verdict_names_a_defendant_by_index_and_its_status_follows(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    res = _result(ctx)
    missed: list = []
    log = read.apply_verdicts(
        ctx,
        res,
        [{"path": "defendants[0].dismissed", "verdict": "overturned", "corrected": {"date": "2026-08-01"}, "doc": 1}],
        "verify",
        TODAY,
        missed,
    )
    d = res["defendants"][0]
    assert d["status"] == vocab.D_DISMISSED and d["dismissed"]["date"] == "2026-08-01" and missed == []
    assert [x["path"] for x in log] == ["defendants[Delta Example].dismissed"]  # parity's form: by name


@pytest.mark.parametrize(
    "verdict,reason",
    [
        (
            {"path": "defendants[9].served", "verdict": "overturned", "corrected": {"date": "2026-01-01"}},
            "no defendant",
        ),
        ({"path": "defendants[Nobody].served", "verdict": "overturned", "corrected": {}}, "no defendant"),
        ({"path": "defendants[9].status", "verdict": "unclear"}, "no defendant"),
        ({"path": "firm_role", "verdict": "overturned", "corrected": "amicus"}, "does not parse"),
        ({"path": "made_up", "verdict": "overturned", "corrected": "x"}, "does not parse"),
        ({"path": "defendants[+]", "verdict": "overturned", "corrected": {"status": vocab.ANSWERED}}, "no name"),
        ({"path": "defendants[+]", "verdict": "overturned", "corrected": {"name": "Delta Example"}}, "already"),
        ({"path": "court_dates", "verdict": "overturned", "corrected": "soon"}, "whole corrected list"),
    ],
)
def test_a_verdict_that_cannot_be_applied_is_logged_and_changes_nothing(
    tmp_path: Path, verdict: dict, reason: str
) -> None:
    ctx = _ctx(tmp_path)
    res = _result(ctx)
    before = copy.deepcopy(res)
    missed: list = []
    assert read.apply_verdicts(ctx, res, [verdict], "audit", TODAY, missed) == []
    assert res == before
    assert len(missed) == 1 and reason in missed[0]["reason"] and missed[0]["pass"] == "audit"


def test_the_old_name_form_still_applies_for_one_release(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    res = _result(ctx)
    n = ctx.by_id["f-p1"]
    v = {
        "path": "defendants[Delta Example].served",
        "verdict": "overturned",
        "corrected": {"date": "2026-04-09"},
        "doc": n,
    }
    read.apply_verdicts(ctx, res, [v], "verify", TODAY)
    assert res["defendants"][0]["served"]["date"] == "2026-04-09"


def test_a_verdict_overturning_a_status_is_logged_as_unmatched(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    res = _result(ctx)
    missed: list = []
    v = {"path": "defendants[0].status", "verdict": "overturned", "corrected": vocab.D_DISMISSED, "doc": 1}
    assert read.apply_verdicts(ctx, res, [v], "verify", TODAY, missed) == []
    assert res["defendants"][0]["status"] == vocab.ANSWERED
    assert missed[0]["path"] == "defendants[0].status" and "correct the event" in missed[0]["reason"]


def test_an_unclear_status_verdict_marks_the_defendant_and_survives_a_settle(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    res = _result(ctx)
    v = {"path": "defendants[0].status", "verdict": "unclear", "why": "the papers disagree"}
    log = read.apply_verdicts(ctx, res, [v], "audit", TODAY)
    d = res["defendants"][0]
    assert d["status"] == vocab.D_UNCLEAR and d["status_unclear"] == "the papers disagree"
    assert log[0]["old"] == vocab.ANSWERED and log[0]["new"] == vocab.D_UNCLEAR
    defstatus.settle(res, TODAY)
    assert d["status"] == vocab.D_UNCLEAR


def test_a_corrected_court_list_moves_the_next_date(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    res = _result(ctx)
    n = ctx.by_id["f-c1"]
    rows = [{"date": "2026-12-01", "event": "Trial"}, {"date": "2026-11-01", "event": "CMC"}]
    log = read.apply_verdicts(
        ctx, res, [{"path": "court_dates", "verdict": "overturned", "corrected": rows, "doc": n}], "verify", TODAY
    )
    assert res["next_court_date"]["date"] == "2026-11-01" and len(res["court_dates_read"]) == 2
    assert log[0]["path"] == "next_court_date" and log[0]["source"]["name"] == "Complaint Gamma.pdf"


def test_the_readers_see_an_index_not_the_bookkeeping(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    res = _result(ctx)
    res["court_dates_read"] = [{"date": "2026-11-01", "event": "CMC", "source": None}]
    shown = json.loads(read.for_model(res, ctx))
    assert shown["defendants"][0]["index"] == 0 and "status_from" not in shown["defendants"][0]
    assert shown["court_dates"][0]["event"] == "CMC" and "court_dates_read" not in shown
    assert "defendants[<index>].sent_for_service" in read.PATHS or "defendants[<index>].<event>" in read.PATHS
    assert "<exact name>" not in read.PATHS


def test_a_check_pass_records_what_it_could_not_apply(tmp_path: Path) -> None:
    client = ScriptedLit(
        verdicts=[
            {"path": "defendants[7].served", "verdict": "overturned", "corrected": {"date": "2026-01-01"}},
            {"path": "defendants[0].status", "verdict": "overturned", "corrected": vocab.D_SETTLED},
        ],
        audit=[{"path": "defendants[Nobody].answered", "verdict": "overturned", "corrected": {"date": "2026-01-01"}}],
    )
    assert _run(tmp_path, client, LitSeat({M1: matter_docs()})).run().verdict == "delivered"
    m = tmp_path / "job" / "data" / "m" / M1
    assert [x["path"] for x in json.loads((m / "read2.json").read_text())["unmatched_verdicts"]] == [
        "defendants[7].served",
        "defendants[0].status",
    ]
    assert [x["path"] for x in json.loads((m / "read3.json").read_text())["unmatched_verdicts"]] == [
        "defendants[Nobody].answered"
    ]


# ---- a report already stored on a seat ------------------------------------------------------------
def _stored(status: str, **extra: object) -> dict:
    """A defendant as a report read before the derivation stored it: an
    asserted status and no ``status_from``."""
    return {
        "name": "Delta Example",
        "status": status,
        "out_for_service": {"value": None, "source": None},
        "served": {"date": "2026-04-10", "method": "personal", "source": _src("old-pos")},
        "answered": {"date": None, "source": None},
        "flags": [],
        **extra,
    }


def _stored_matter(*defendants: dict) -> dict:
    return {
        "matter_id": "m1",
        "number": "100001",
        "responsible": "Alpha Example",
        "case_name": "A v. B",
        "firm_role": "plaintiff",
        "case_status": {"value": vocab.ACTIVE, "detail": None, "source": _src("old-complaint")},
        "complaint_filed": {"date": "2026-03-02", "source": _src("old-complaint")},
        "next_court_date": {"date": "2026-11-01", "event": "CMC", "source": _src("old-notice")},
        "defendants": list(defendants),
        "discovery_propounded": [],
        "discovery_served_on_client": [],
        "matter_flags": [],
    }


@pytest.mark.parametrize("status", vocab.DEFENDANT_STATUSES)
def test_a_stored_status_prints_as_it_did(status: str) -> None:
    """Dismissal, settlement and default had no dated field when it was read:
    its events alone cannot give it, so they are not asked to."""
    m = _stored_matter(_stored(status))
    assert defstatus.settle(m, TODAY) == []
    assert m["defendants"][0]["status"] == status and "status_from" not in m["defendants"][0]


def test_a_stored_note_is_still_reconciled_and_a_derived_one_holds() -> None:
    stored = _stored_matter(_stored(vocab.SERVED_NO_ANSWER, flags=["Default entered 2026-06-01."]))
    assert [x["pass"] for x in defstatus.settle(stored, TODAY)] == ["status-note"]
    assert stored["defendants"][0]["status"] == vocab.DEFAULT
    derived = _stored_matter(
        _stored(vocab.SERVED_NO_ANSWER, flags=["Default entered 2026-06-01."], status_from=vocab.FROM_DERIVED)
    )
    assert defstatus.settle(derived, TODAY) == []
    assert derived["defendants"][0]["status"] == vocab.SERVED_NO_ANSWER
    assert gates.rule_matters([derived], TODAY)[gates.NOTE_RULE]


def test_an_update_with_no_event_leaves_a_stored_status_alone() -> None:
    prior = _stored_matter(_stored(vocab.D_DISMISSED))
    fresh = copy.deepcopy(prior)
    fresh["defendants"][0]["status"] = vocab.SERVED_NO_ANSWER  # a read cannot move it
    out, log = update.guard(prior, fresh, list(vocab.GROUPS), ["new"], {"new": ""}, today=TODAY)
    d = out["defendants"][0]
    assert d["status"] == vocab.D_DISMISSED and "status_from" not in d and "stored_status" not in d
    assert log["changed"] == []


@pytest.mark.parametrize(
    "stored,field,want",
    [
        (vocab.D_DISMISSED, "answered", vocab.D_DISMISSED),  # a paper arriving cannot bring it back
        (vocab.DEFAULT, "answered", vocab.D_UNCLEAR),
        (vocab.SERVED_NO_ANSWER, "answered", vocab.ANSWERED),
        (vocab.SERVED_NO_ANSWER, "default_entered", vocab.DEFAULT),
        (vocab.D_SETTLED, "dismissed", vocab.D_DISMISSED),
        (vocab.UIM, "answered", vocab.UIM),
    ],
)
def test_an_arrived_event_is_read_beside_a_stored_status(stored: str, field: str, want: str) -> None:
    prior = _stored_matter(_stored(stored))
    fresh = copy.deepcopy(prior)
    fresh["defendants"][0][field] = {"date": "2026-09-30", "source": _src("new")}
    out, log = update.guard(prior, fresh, list(vocab.GROUPS), ["new"], {"new": "filed 09/30/2026"}, today=TODAY)
    d = out["defendants"][0]
    assert d["status"] == want and d["status_from"] == vocab.FROM_DERIVED and d["stored_status"] == stored
    assert f"defendants[Delta Example].{field}" in log["changed"]
    # A later run works it out again from the same inputs, and it holds.
    assert defstatus.derive(d, TODAY) == want


def test_an_event_the_guard_put_back_leaves_the_stored_status() -> None:
    prior = _stored_matter(_stored(vocab.SERVED_NO_ANSWER))
    fresh = copy.deepcopy(prior)
    fresh["defendants"][0]["dismissed"] = {"date": "2026-09-30", "source": _src("old-pos")}  # not an arrived paper
    out, log = update.guard(prior, fresh, list(vocab.GROUPS), ["new"], {"new": ""}, today=TODAY)
    d = out["defendants"][0]
    assert d["status"] == vocab.SERVED_NO_ANSWER and "dismissed" not in d and "status_from" not in d
    assert "defendants[Delta Example].dismissed" in log["reverted"]


def test_a_full_read_replaces_a_stored_status(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    prior = _stored_matter(_stored(vocab.SERVED_NO_ANSWER))
    fresh = answers.to_state(ctx, good_result(_nums(ctx)), list(vocab.GROUPS), TODAY)
    d = read.merge(prior, fresh, list(vocab.GROUPS))["defendants"][0]
    assert d["status"] == vocab.ANSWERED and d["status_from"] == vocab.FROM_DERIVED and "stored_status" not in d


def test_a_seed_is_marked_and_a_later_read_overrides_it() -> None:
    from jobs.litigation import baseline

    row = {"defendants": [{"name": "Delta Example", "status": "dismissed 2019"}], "case_status": "Active"}
    seed = baseline.from_firm(row, [])
    d = seed["defendants"][0]
    assert d["status"] == vocab.D_DISMISSED and d["status_from"] == vocab.FROM_SEED and defstatus.asserted(d)
    two = baseline.from_two_pass([{"defendant": "Delta Example", "status": "served"}], [])
    assert two["defendants"][0]["status_from"] == vocab.FROM_SEED


def test_the_run_prints_a_stored_list_unchanged_and_delivers(tmp_path: Path) -> None:
    """The first run of this code on a seat, with nothing new on a matter: no
    read, no new key, the status as it printed."""
    seat = LitSeat({M1: matter_docs()})
    assert _run(tmp_path, ScriptedLit(), seat).run().verdict == "delivered"
    path = tmp_path / "state" / "matters" / f"{M1}.json"
    stored = json.loads(path.read_text())
    for k in ("status_from", "stored_status"):
        stored["defendants"][0].pop(k, None)
    stored["defendants"][0]["status"] = vocab.D_SETTLED  # asserted, no dated field
    stored.pop("court_dates_read", None)
    path.write_text(json.dumps(stored))
    v = _run(tmp_path, ScriptedLit(), seat, job="j2", job_id="01KTJ0BX0000000000000000BB").run()
    assert v.verdict == "delivered", v.reason
    assert v.matters_reread == 0
    d = json.loads(path.read_text())["defendants"][0]
    assert d["status"] == vocab.D_SETTLED and "status_from" not in d


def test_the_final_values_follow_a_carried_event_and_the_calendar(tmp_path: Path) -> None:
    """What the gates and the list read (``LitigationRun.final``): the status
    after the carry, and the next court date on this run's today."""
    seat = LitSeat({M1: matter_docs()})
    first = _run(tmp_path, ScriptedLit(), seat)
    assert first.run().verdict == "delivered"
    read3 = json.loads(first._mfile(M1, "read3.json").read_text())["result"]
    # A second job, so the papers are not new since the last list.
    r = _run(tmp_path, ScriptedLit(), seat, job="j2", job_id="01KTJ0BX0000000000000000BB")
    assert r.run().verdict == "delivered"
    d = read3["defendants"][0]
    d["answered"] = {"date": None, "source": None}  # this read left it out; its document has not changed
    d["status"] = vocab.SERVED_NO_ANSWER
    read3["court_dates_read"] = [
        {"date": "2026-09-01", "event": "CMC", "source": d["served"]["source"]},
        {"date": "2026-11-20", "event": "Trial", "source": d["served"]["source"]},
    ]
    read3["next_court_date"] = read3["court_dates_read"][0]
    r._mfile(M1, "read3.json").write_text(json.dumps({"result": read3, "overturns": []}))
    out = r.final(next(x for x in r._matters() if x["id"] == M1))
    assert out["defendants"][0]["answered"]["date"] == "2026-04-30"
    assert out["defendants"][0]["status"] == vocab.ANSWERED
    assert out["next_court_date"]["event"] == "Trial"


# ---- update mode: changes only -------------------------------------------------------------------
def test_an_update_read_returns_changes_and_code_keeps_the_rest(tmp_path: Path) -> None:
    seat = LitSeat({M1: matter_docs()})
    assert _run(tmp_path, ScriptedLit(), seat).run().verdict == "delivered"
    first = json.loads((tmp_path / "state" / "matters" / f"{M1}.json").read_text())
    _arrive(seat, "f-d1", "POS dismissal Delta.pdf", "REQUEST FOR DISMISSAL entered 09/29/2026")

    def only_the_change(_values: dict) -> dict:
        # Doc numbers run newest first, so the arrived paper is doc 1.
        return {"defendants": [{"name": "Delta Example", "dismissed": {"date": "2026-09-29", "doc": 1}}]}

    v = _run(
        tmp_path, ScriptedLit(update_fn=only_the_change), seat, job="j2", job_id="01KTJ0BX0000000000000000BB"
    ).run()
    assert v.verdict == "delivered", v.reason
    now = json.loads((tmp_path / "state" / "matters" / f"{M1}.json").read_text())
    d, was = now["defendants"][0], first["defendants"][0]
    assert d["status"] == vocab.D_DISMISSED and d["dismissed"]["date"] == "2026-09-29"
    for f in ("served", "answered", "sent_for_service"):
        assert d[f] == was[f]
    for k in ("case_name", "complaint_filed", "case_status", "firm_role"):
        assert now[k] == first[k]


def test_the_update_prompt_asks_for_changes_only() -> None:
    assert "Report ONLY what an arrived document changes or adds" in update.TAIL
    assert "copy every current value" not in update.TAIL


def test_overlay_keeps_what_the_read_left_out(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    prior = _stored_matter(_stored(vocab.SERVED_NO_ANSWER), {**_stored(vocab.NOT_SERVED), "name": "Echo Example"})
    prior["discovery_propounded"] = [
        {"set": "Form Interrogatories, Set One", "served_on": "X", "date": "2026-05-01", "source": _src()}
    ]
    prior["matter_flags"] = ["An old flag."]
    n = ctx.by_id["f-p1"]
    raw = {
        "defendants": [
            {"name": "Echo Example", "served": {"date": "2026-09-01", "doc": n}},
            {"name": "Zeta New", "served": {"date": "2026-09-02", "doc": n}},
        ],
        "discovery_propounded": [
            {"set": "Requests for Admission, Set One", "served_on": "X", "date": "2026-09-03", "doc": n}
        ],
        "matter_flags": ["A new flag."],
    }
    out = update.overlay(ctx, prior, raw, list(vocab.GROUPS), TODAY)
    assert [d["name"] for d in out["defendants"]] == ["Delta Example", "Echo Example", "Zeta New"]
    assert out["defendants"][0] == prior["defendants"][0]
    assert out["defendants"][1]["served"]["date"] == "2026-09-01"
    assert out["defendants"][1]["status"] == vocab.NOT_SERVED  # the guard settles it, from what stood
    assert out["defendants"][2]["status"] == vocab.SERVED_NO_ANSWER
    assert out["complaint_filed"] == prior["complaint_filed"] and out["case_status"] == prior["case_status"]
    assert out["next_court_date"] == prior["next_court_date"]
    assert len(out["discovery_propounded"]) == 2 and out["matter_flags"] == ["An old flag.", "A new flag."]


def test_overlay_keeps_the_later_of_two_sent_dates(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    n = ctx.by_id["f-p1"]
    prior = _stored_matter(_stored(vocab.SERVED_NO_ANSWER, sent_for_service=_dated("2026-05-01")))

    def after(rows: list) -> object:
        raw = {"defendants": [{"name": "Delta Example", "sent_for_service": rows}]}
        return update.overlay(ctx, prior, raw, list(vocab.GROUPS), TODAY)["defendants"][0]["sent_for_service"]["date"]

    assert after([{"date": "2026-06-01", "doc": n}]) == "2026-06-01"
    assert after([{"date": "2026-04-01", "doc": n}]) == "2026-05-01"
    assert after([]) == "2026-05-01"


def test_a_court_date_no_arrived_paper_carries_puts_its_list_back(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    prior = _stored_matter(_stored(vocab.SERVED_NO_ANSWER))
    n = ctx.by_id["f-p1"]
    raw = {"court_dates": [{"date": "2026-10-20", "event": "CMC", "doc": n}]}
    res = update.overlay(ctx, prior, raw, [vocab.GROUP_CASE], TODAY)
    assert res["next_court_date"]["date"] == "2026-10-20"
    out, log = update.guard(
        prior, read.merge(prior, res, [vocab.GROUP_CASE]), [vocab.GROUP_CASE], ["new"], {"new": ""}, today=TODAY
    )
    assert out["next_court_date"]["date"] == "2026-11-01" and "court_dates_read" not in out
    assert "next_court_date" in log["reverted"]
    out, log = update.guard(
        prior,
        read.merge(prior, res, [vocab.GROUP_CASE]),
        [vocab.GROUP_CASE],
        ["f-p1"],
        {"f-p1": "CMC on 10/20/2026"},
        today=TODAY,
    )
    assert out["next_court_date"]["date"] == "2026-10-20" and "next_court_date" in log["changed"]


def test_a_role_or_out_for_service_stands_only_on_an_arrived_document() -> None:
    prior = _stored_matter(_stored(vocab.SERVED_NO_ANSWER))
    fresh = copy.deepcopy(prior)
    fresh["defendants"][0]["role"] = {"value": vocab.ROLE_UIM, "source": _src("old-pos")}
    fresh["defendants"][0]["out_for_service"] = {"value": "yes", "source": _src("old-pos")}
    out, log = update.guard(prior, fresh, list(vocab.GROUPS), ["new"], {"new": ""}, today=TODAY)
    d = out["defendants"][0]
    assert "role" not in d and d["out_for_service"] == prior["defendants"][0]["out_for_service"]
    assert d["status"] == vocab.SERVED_NO_ANSWER and len(log["reverted"]) == 2
    fresh["defendants"][0]["role"] = {"value": vocab.ROLE_UIM, "source": _src("new")}
    out, _log = update.guard(prior, fresh, list(vocab.GROUPS), ["new"], {"new": ""}, today=TODAY)
    assert out["defendants"][0]["status"] == vocab.UIM


def test_a_side_the_last_list_could_not_say_can_be_read_by_an_update() -> None:
    prior = {**_stored_matter(), "firm_role": vocab.UNCLEAR}
    fresh = {**copy.deepcopy(prior), "firm_role": "defense"}
    assert update.guard(prior, fresh, [vocab.GROUP_CASE], [], {}, today=TODAY)[0]["firm_role"] == "defense"
    fresh = {**copy.deepcopy(_stored_matter()), "firm_role": "defense"}
    assert update.guard(_stored_matter(), fresh, [vocab.GROUP_CASE], [], {}, today=TODAY)[0]["firm_role"] == "plaintiff"


def test_a_new_paper_clears_the_last_lists_unclear_but_not_this_runs() -> None:
    old = _stored(vocab.D_UNCLEAR, status_from=vocab.FROM_DERIVED, status_unclear="the papers disagree")
    prior = _stored_matter(old)
    fresh = copy.deepcopy(prior)
    fresh["defendants"][0]["answered"] = {"date": "2026-09-30", "source": _src("new")}
    out, _log = update.guard(prior, fresh, list(vocab.GROUPS), ["new"], {"new": "filed 09/30/2026"}, today=TODAY)
    assert out["defendants"][0]["status"] == vocab.ANSWERED and "status_unclear" not in out["defendants"][0]
    # A check on this run marks it after an event arrived: the guard runs again and keeps the mark.
    fresh["defendants"][0]["status_unclear"] = "the new answer names someone else"
    prior2 = _stored_matter(_stored(vocab.SERVED_NO_ANSWER))
    out, _log = update.guard(prior2, fresh, list(vocab.GROUPS), ["new"], {"new": "filed 09/30/2026"}, today=TODAY)
    assert out["defendants"][0]["status"] == vocab.D_UNCLEAR


# ---- carry, gates, parity --------------------------------------------------------------------------
def _carry(prior: dict, result: dict) -> list[str]:
    return carry.carry_forward(prior, result, moved=set(), on_matter={"old-pos", "f-a"}, saved={}, today=TODAY)


def test_every_event_carries_onto_a_derived_defendant_and_the_status_follows() -> None:
    prior = _stored_matter(
        _stored(
            vocab.D_DISMISSED,
            status_from=vocab.FROM_DERIVED,
            dismissed=_dated("2026-08-01", "old-pos"),
            answered=_dated("2026-04-30", "f-a"),
        )
    )
    now = _stored_matter(_stored(vocab.SERVED_NO_ANSWER, status_from=vocab.FROM_DERIVED))
    assert sorted(_carry(prior, now)) == ["defendants[Delta Example].answered", "defendants[Delta Example].dismissed"]
    defstatus.settle(now, TODAY)
    assert now["defendants"][0]["status"] == vocab.D_DISMISSED


def test_a_stored_status_still_rules_what_carries_onto_it() -> None:
    prior = _stored_matter(_stored(vocab.ANSWERED, answered=_dated("2026-04-30", "f-a")))
    now = _stored_matter(_stored(vocab.SERVED_NO_ANSWER))
    assert _carry(prior, now) == []


def _gate_issues(m: dict) -> dict:
    return gates.run(
        [m],
        today=TODAY,
        files={"m1": {"old-pos", "old-complaint", "old-notice", "f1"}},
        saved={},
        text_of=lambda *_: "",
        hits={},
    )["issues"]


OTHER = [f for f in vocab.DEFENDANT_EVENTS if f not in ("served", "answered")]


@pytest.mark.parametrize("field", OTHER)
def test_every_event_date_is_judged_like_every_other_date(field: str) -> None:
    base = _stored(vocab.SERVED_NO_ANSWER)
    assert _gate_issues(_stored_matter(base)) == {}
    assert "future date" in _gate_issues(_stored_matter({**base, field: _dated("2027-01-01")}))
    assert "date without source" in _gate_issues(
        _stored_matter({**base, field: {"date": "2026-05-01", "source": None}})
    )
    assert "date not YYYY-MM-DD" in _gate_issues(_stored_matter({**base, field: _dated("5/1/2026")}))
    assert "source is not a file on the matter" in _gate_issues(
        _stored_matter({**base, field: _dated("2026-05-01", "zz")})
    )


def test_a_derived_status_its_events_do_not_give_is_refused() -> None:
    ok = _stored(vocab.SERVED_NO_ANSWER, status_from=vocab.FROM_DERIVED)
    assert _gate_issues(_stored_matter(ok)) == {}
    bad = {**ok, "status": vocab.D_DISMISSED}
    assert _gate_issues(_stored_matter(bad))[gates.DERIVED_RULE] == [
        ("100001", "Delta Example", vocab.D_DISMISSED, vocab.SERVED_NO_ANSWER)
    ]
    # A status a read stated on a report stored earlier is not asked to follow its events.
    assert _gate_issues(_stored_matter({**ok, "status": vocab.D_DISMISSED, "status_from": vocab.FROM_SEED})) == {}


def _status_change(prior: dict, new: dict, **kw: object) -> dict:
    kw = {"moved_files": set(), "overturns": [], "today": TODAY, **kw}
    changes = parity.compare(prior, new, **kw)  # type: ignore[arg-type]
    return next(c for c in changes if c["path"].endswith(".status"))


def test_a_status_falling_below_what_the_last_list_stated_holds() -> None:
    prior = _stored_matter(_stored(vocab.D_DISMISSED))
    new = _stored_matter(_stored(vocab.SERVED_NO_ANSWER, status_from=vocab.FROM_DERIVED))
    assert not _status_change(prior, new)["explained"]
    # Even when its service date is on a document new since the last list.
    assert not _status_change(prior, new, moved_files={"old-pos"})["explained"]


def test_a_cited_event_lifting_a_stated_status_is_explained_on_the_same_papers() -> None:
    prior = _stored_matter(_stored(vocab.SERVED_NO_ANSWER))
    new = _stored_matter(
        _stored(vocab.DEFAULT, status_from=vocab.FROM_DERIVED, default_entered=_dated("2026-06-01", "old-pos"))
    )
    c = _status_change(prior, new)
    assert c["explained"] and "worked out from a cited event" in c["why"]


def test_a_derived_status_follows_its_explained_events() -> None:
    derived = {"status_from": vocab.FROM_DERIVED}
    prior = _stored_matter(_stored(vocab.SERVED_NO_ANSWER, **derived))
    new = _stored_matter(_stored(vocab.ANSWERED, answered=_dated("2026-09-30", "f-new"), **derived))
    assert _status_change(prior, new, moved_files={"f-new"})["explained"]
    # The same change with the answer's document not new: the event is unexplained and so is the status.
    new2 = _stored_matter(_stored(vocab.ANSWERED, answered=_dated("2026-09-30", "old-pos"), **derived))
    prior2 = _stored_matter(_stored(vocab.SERVED_NO_ANSWER, answered=_dated("2026-09-29", "old-pos"), **derived))
    prior2["defendants"][0]["status"] = vocab.ANSWERED
    new2["defendants"][0]["status"] = vocab.D_UNCLEAR
    assert not _status_change(prior2, new2)["explained"]


def test_a_status_follows_an_event_a_pass_overturned_on_the_same_papers() -> None:
    """The verify pass corrected a date from a document already on the matter,
    so the status moved with no new paper and its own basis is not cited."""
    derived = {"status_from": vocab.FROM_DERIVED}
    prior = _stored_matter(_stored(vocab.SERVED_NO_ANSWER, answered={"date": None, "source": None}, **derived))
    new = _stored_matter(_stored(vocab.ANSWERED, answered=_dated("2026-04-30", "old-pos"), **derived))
    prior["defendants"][0]["served"]["source"] = _src("old-pos")
    over = [{"path": "defendants[Delta Example].answered", "source": _src("old-pos")}]
    c = _status_change(prior, new, overturns=over)
    assert c["explained"] and "follows from the dated events" in c["why"]


def test_an_events_change_is_on_the_what_changed_list() -> None:
    derived = {"status_from": vocab.FROM_DERIVED}
    prior = _stored_matter(_stored(vocab.SERVED_NO_ANSWER, **derived))
    new = _stored_matter(_stored(vocab.D_DISMISSED, dismissed=_dated("2026-09-30", "f-new"), **derived))
    paths = {c["path"]: c for c in parity.compare(prior, new, moved_files={"f-new"}, overturns=[], today=TODAY)}
    assert paths["defendants[deltaexample].dismissed"]["explained"]
    assert paths["defendants[deltaexample].status"]["explained"]


def test_a_stored_report_reads_through_the_stored_status_basis() -> None:
    d = _stored(vocab.D_DISMISSED, answered=_dated("2026-04-30", "f-a"))
    assert defstatus.basis(d)["source"]["file_id"] == "f-a"  # no dismissal event: the answer's document
    d = _stored(vocab.D_DISMISSED, status_from=vocab.FROM_DERIVED, dismissed=_dated("2026-08-01", "f-d"))
    assert defstatus.basis(d)["source"]["file_id"] == "f-d"


# ---- the report's reading of the list ----------------------------------------------------------
def test_classify_tells_a_missing_event_from_a_rule_that_disagrees() -> None:
    assert answerkey.classify(_stored(vocab.SERVED_NO_ANSWER), TODAY)["class"] == answerkey.SAME
    got = answerkey.classify(_stored(vocab.D_DISMISSED), TODAY)
    assert got == {"from_events": vocab.SERVED_NO_ANSWER, "rule": "served", "class": answerkey.NOT_ON_FILE}
    # A stored "Not served" beside a cited service date: the stored status is below its own events.
    assert answerkey.classify(_stored(vocab.NOT_SERVED), TODAY)["class"] == answerkey.DISAGREES
    # A stored status above an event with no document: not a missing event, a defect to read.
    uncited = _stored(vocab.D_DISMISSED, served={"date": "2026-04-10", "source": None})
    got = answerkey.classify(uncited, TODAY)
    assert got["from_events"] == vocab.D_UNCLEAR and got["class"] == answerkey.DISAGREES


def test_compare_names_every_difference() -> None:
    rows = [
        {"matter_id": "m1", "index": 0, "printed": vocab.D_DISMISSED, "class": answerkey.NOT_ON_FILE},
        {"matter_id": "m1", "index": 1, "printed": vocab.ANSWERED, "class": answerkey.SAME},
        {"matter_id": "m2", "index": 0, "printed": vocab.DEFAULT, "class": answerkey.SAME},
    ]
    key = {"m1#0": vocab.D_DISMISSED, "m1#1": vocab.NOT_SERVED, "m3#0": vocab.ANSWERED}
    got = answerkey.compare({"status_derivation": {"rows": rows}}, key)
    assert got["printed_differs"] == [{"defendant": "m1#1", "key": vocab.NOT_SERVED, "printed": vocab.ANSWERED}]
    assert got["missing_from_report"] == ["m3#0"] and got["not_in_key"] == ["m2#0"]
    assert [r["index"] for r in got["from_events_differs"]] == [0]


def test_the_key_comes_from_the_stored_state(tmp_path: Path) -> None:
    (tmp_path / "matters").mkdir()
    (tmp_path / "matters" / "m1.json").write_text(json.dumps(_stored_matter(_stored(vocab.D_SETTLED))))
    assert answerkey.from_state(tmp_path) == {"m1#0": vocab.D_SETTLED}


def test_the_answer_key_cli(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = Path(__file__).parent / "litigation_status_answer_key.json"
    assert answerkey.main(["rules", str(path)]) == 0
    assert answerkey.main(["compare"]) == 2
    report = tmp_path / "r.json"
    report.write_text(json.dumps({"status_derivation": {"rows": []}}))
    key = tmp_path / "k.json"
    key.write_text(json.dumps({"m1#0": vocab.ANSWERED}))
    assert answerkey.main(["compare", str(report), str(key)]) == 1  # a line the report does not have
