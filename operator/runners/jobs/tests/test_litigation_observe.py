"""``status_from_events: observe`` (the seat's default). The list prints and the
holds fire as they did before the switch existed; a run also records what the
derivation would have done. ``litigation_observe_golden.json`` is
``litigation_observe_shapes.py`` run on origin/main before the switch."""

from __future__ import annotations

import copy
import datetime as dt
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import litigation_observe_shapes as shapes
from litigation_testkit import M1, LitSeat, ScriptedLit, good_result, make_inputs, make_job, matter_docs
from jobs.litigation import defstatus, eventmode, finalload, manifest, read, statusshadow, update, vocab
from jobs.litigation.discovery12 import batch, sections
from jobs.litigation.run import LitigationRun
from jobs_testkit import make_pdf

TODAY = dt.date(2026, 10, 7)
GOLDEN = json.loads((Path(__file__).parent / "litigation_observe_golden.json").read_text(encoding="utf-8"))
SECRET_NAME = "Delta Example"


@pytest.fixture(autouse=True)
def _no_seat_config(tmp_path: Path, pricing_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "pricing.json").write_text(pricing_path.read_text())
    monkeypatch.setenv("SMD_CUSTOMER_YAML_PATH", str(tmp_path / "absent.yaml"))


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


def _shadow_result(**events: dict) -> object:
    def fn(n: dict[str, int]) -> dict:
        raw = good_result(n)
        for f, (date, doc) in events.items():
            raw["defendants"][0][f] = {"date": date, "doc": n[doc]}
        return raw

    return fn


def _doc(tmp_path: Path, job_id: str = "01KTJ0BX0000000000000000AA") -> dict:
    return json.loads((tmp_path / "state" / "status_shadow" / "runs" / f"{job_id}.json").read_text())


# ---- what the firm sees is what main shows ------------------------------------------------------
@pytest.mark.parametrize("name", sorted(GOLDEN["shapes"]))
def test_the_printed_list_and_the_hold_decision_equal_mains_on_the_answer_key(name: str) -> None:
    got = shapes.shapes_one(name)
    assert got == GOLDEN["shapes"][name]


def test_the_shapes_are_all_compared() -> None:
    assert sorted(shapes.shapes()) == sorted(GOLDEN["shapes"])


def test_whole_runs_equal_mains() -> None:
    """Delivered and held, a first read and an update, final.json, gates.json,
    changes.json and what the lane stored, value for value."""
    got = shapes.scenarios()
    assert sorted(got) == sorted(GOLDEN["runs"])
    for name, want in GOLDEN["runs"].items():
        assert got[name] == want, name


def test_a_read_is_asked_for_a_status_and_for_the_events(tmp_path: Path) -> None:
    client = ScriptedLit()
    assert _run(tmp_path, client, LitSeat({M1: matter_docs()})).run().verdict == "delivered"
    tools = [t for c in client.calls for t in c.get("tools") or [] if t["name"] == "record_result"]
    props = tools[0]["input_schema"]["properties"]
    d = props["defendants"]["items"]
    assert d["required"] == ["name", "status"] and "status" in d["properties"]
    assert set(vocab.SHADOW_EVENTS) <= set(d["properties"]) and "role" in d["properties"]
    assert "next_court_date" in props and "court_dates" not in props
    assert d["properties"]["sent_for_service"]["type"] == "object"
    firm = SimpleNamespace(data={"form_hints": {}})
    for role in ("read", "verify", "audit"):
        text = read.system_for(role, firm, TODAY)
        for s in vocab.DEFENDANT_STATUSES:
            assert f'"{s}"' in text
        assert "DATED EVENTS" in text and "must be the next one AFTER today" in text
    assert "copy every current value" in update.TAIL_OBSERVE
    assert "a status" in read.ask("h", [vocab.GROUP_DEFENDANTS], "l")


def test_a_firm_role_it_cannot_read_is_still_plaintiff_and_unflagged(tmp_path: Path) -> None:
    raw = good_result({"f-c1": 1, "f-p1": 1, "f-a1": 1})
    raw["firm_role"] = "co-counsel"
    out = read.to_state(shapes.Ctx(), raw, list(vocab.GROUPS), TODAY)
    assert out["firm_role"] == "plaintiff" and out["matter_flags"] == []


def test_discovery_keeps_the_reads_not_yet_due() -> None:
    e = {
        "item": "RFAs, Set One",
        "direction": "other",
        "due": {"date": "2026-01-01"},
        "response": {"value": "not yet due"},
    }
    src = lambda n, _d=None: None  # noqa: E731 - a one-line stub source, no document behind any number
    got = sections.to_result(src, {"sections": {"rfas": [e]}}, TODAY)
    assert got["sections"]["rfas"][0]["response"]["value"] == "not yet due"
    e2 = {**e, "response": {"value": "no"}, "due": {"date": "2027-01-01"}}
    got = sections.to_result(src, {"sections": {"rfas": [e2]}}, TODAY)
    assert got["sections"]["rfas"][0]["response"]["value"] == "no"
    assert got["sections"]["rfas"] and "not yet due" in sections.system_for("read", TODAY)
    enum = batch.tool_for("read")["input_schema"]["properties"]["sections"]["properties"]["rfas"]["items"]
    assert "not yet due" in enum["properties"]["response"]["properties"]["value"]["enum"]
    bad = {**e, "response": {"value": "maybe"}}
    assert (
        sections.to_result(src, {"sections": {"rfas": [bad]}}, TODAY)["sections"]["rfas"][0]["response"]["value"]
        == "unclear"
    )


# ---- a verdict on an event reaches only the shadow ----------------------------------------------
def _res() -> dict:
    raw = shapes.answer({"events": {"served": "2026-04-10", "dismissed": "2026-06-01"}}, vocab.SERVED_NO_ANSWER)
    return read.to_state(shapes.Ctx(), raw, list(vocab.GROUPS), TODAY)


def test_the_events_ride_apart_from_the_lists_values() -> None:
    d = _res()["defendants"][0]
    assert d["status"] == vocab.SERVED_NO_ANSWER and "dismissed" not in d and "status_from" not in d
    assert d["shadow"]["dismissed"]["date"] == "2026-06-01"


def test_a_verdict_on_an_event_changes_only_the_shadow() -> None:
    res = _res()
    before = copy.deepcopy({k: v for k, v in res["defendants"][0].items() if k != "shadow"})
    v = {"path": "defendants[0].dismissed", "verdict": "overturned", "corrected": {"date": "2026-06-02"}, "doc": 6}
    log = read.apply_verdicts(shapes.Ctx(), res, [v], "verify", TODAY)
    d = res["defendants"][0]
    assert log == [] and d["shadow"]["dismissed"]["date"] == "2026-06-02" and d["shadow"]["overturned"] == ["dismissed"]
    assert {k: x for k, x in d.items() if k != "shadow"} == before


def test_an_unclear_on_an_event_drops_it_and_leaves_no_flag() -> None:
    res = _res()
    read.apply_verdicts(
        shapes.Ctx(), res, [{"path": "defendants[0].dismissed", "verdict": "unclear", "why": "x"}], "audit", TODAY
    )
    d = res["defendants"][0]
    assert "dismissed" not in d["shadow"] and d["flags"] == []


def test_a_correction_to_no_date_takes_the_event_away() -> None:
    res = _res()
    v = {"path": "defendants[0].dismissed", "verdict": "overturned", "corrected": {"date": None}, "doc": 6}
    assert read.apply_verdicts(shapes.Ctx(), res, [v], "verify", TODAY) == []
    assert "dismissed" not in res["defendants"][0]["shadow"]


def test_a_reader_may_still_set_a_status_and_a_bad_one_is_logged() -> None:
    res = _res()
    missed: list = []
    good = {"path": "defendants[0].status", "verdict": "overturned", "corrected": vocab.D_SETTLED, "doc": 7}
    bad = {"path": "defendants[0].status", "verdict": "overturned", "corrected": "Gone"}
    log = read.apply_verdicts(shapes.Ctx(), res, [good, bad], "verify", TODAY, missed)
    assert res["defendants"][0]["status"] == vocab.D_SETTLED and len(log) == 1 and len(missed) == 1
    read.apply_verdicts(shapes.Ctx(), res, [{"path": "defendants[0].status", "verdict": "unclear"}], "audit", TODAY)
    assert res["defendants"][0]["status"] == vocab.D_UNCLEAR and "status_unclear" not in res["defendants"][0]


def test_a_court_dates_verdict_is_not_this_modes(tmp_path: Path) -> None:
    res = _res()
    missed: list = []
    v = {"path": "court_dates", "verdict": "overturned", "corrected": [{"date": "2026-11-01"}]}
    read.apply_verdicts(shapes.Ctx(), res, [v], "verify", TODAY, missed)
    assert res["next_court_date"]["date"] == "2026-11-20" and "court_dates_read" not in res and missed


def test_a_corrected_next_court_date_keeps_no_list() -> None:
    res = _res()
    v = {
        "path": "next_court_date",
        "verdict": "overturned",
        "corrected": {"date": "2026-11-21", "event": "CMC"},
        "doc": 11,
    }
    read.apply_verdicts(shapes.Ctx(), res, [v], "verify", TODAY)
    assert res["next_court_date"]["date"] == "2026-11-21" and "court_dates_read" not in res


def test_the_guard_leaves_a_stated_status_alone_while_observing() -> None:
    prior = {
        "defendants": [
            {"name": "Delta Example", "status": vocab.NOT_SERVED, "out_for_service": {"value": None, "source": None}}
        ]
    }
    fresh = copy.deepcopy(prior)
    fresh["defendants"][0]["status"] = vocab.D_SETTLED
    fresh["defendants"][0]["out_for_service"] = {"value": "yes", "source": {"file_id": "old"}}
    out, log = update.guard(prior, fresh, [vocab.GROUP_DEFENDANTS], ["new"], {}, today=TODAY)
    d = out["defendants"][0]
    assert d["status"] == vocab.D_SETTLED and d["out_for_service"]["value"] == "yes"
    assert "defendants[Delta Example].status" in log["changed"] and "status_from" not in d


# ---- the record ----------------------------------------------------------------------------------
def test_a_run_records_each_defendant_and_its_rate(tmp_path: Path) -> None:
    client = ScriptedLit(result_fn=_shadow_result(dismissed=("2026-06-01", "f-a1")))
    r = _run(tmp_path, client, LitSeat({M1: matter_docs()}))
    assert r.run().verdict == "delivered"
    doc = _doc(tmp_path)
    assert doc["mode"] == "observe" and doc["today"] == TODAY.isoformat()
    [row] = doc["rows"]
    assert row["printed"] == vocab.ANSWERED and row["derived"] == vocab.D_DISMISSED and row["differs"] is True
    assert row["rule"] == "dismissed" and row["read"] is True and row["index"] == 0 and row["number"] == "100001"
    c = doc["counts"]
    assert (c["defendants_read"], c["differ"], c["would_hold"]) == (1, 1, 0)
    assert c["list_would_hold"] is False and c["list_held_by_the_gates_as_they_are"] is False


def test_a_row_holds_no_name_and_no_document_text(tmp_path: Path) -> None:
    def noted(n: dict[str, int]) -> dict:
        raw = good_result(n)
        raw["defendants"][0]["flags"] = ["Default entered per the clerk's letter of the eighth."]
        raw["defendants"][0]["answered"] = {"date": None, "doc": None}
        raw["defendants"][0]["status"] = vocab.SERVED_NO_ANSWER
        return raw

    assert _run(tmp_path, ScriptedLit(result_fn=noted), LitSeat({M1: matter_docs()})).run().verdict == "delivered"
    text = json.dumps(_doc(tmp_path))
    for leak in (SECRET_NAME, "clerk's letter", "POS Delta", "Answer Delta", "Gamma"):
        assert leak not in text


def test_a_note_the_derivation_would_hold_on_is_named(tmp_path: Path) -> None:
    def noted(n: dict[str, int]) -> dict:
        raw = good_result(n)
        d = raw["defendants"][0]
        d.update(
            status=vocab.SERVED_NO_ANSWER, flags=["Default entered 2026-06-01."], answered={"date": None, "doc": None}
        )
        return raw

    v = _run(tmp_path, ScriptedLit(result_fn=noted), LitSeat({M1: matter_docs()})).run()
    assert v.verdict == "delivered"  # today's reconcile settles it for the firm
    [row] = _doc(tmp_path)["rows"]
    assert row["printed"] == vocab.DEFAULT and row["derived"] == vocab.SERVED_NO_ANSWER
    assert row["would_hold"] and any("note states" in w for w in row["why"])
    assert _doc(tmp_path)["counts"]["list_would_hold"] is True


def test_an_uncited_event_would_hold_while_the_list_delivers(tmp_path: Path) -> None:
    def uncited(n: dict[str, int]) -> dict:
        raw = good_result(n)
        raw["defendants"][0]["settled"] = {"date": "2026-06-01", "doc": None}
        return raw

    assert _run(tmp_path, ScriptedLit(result_fn=uncited), LitSeat({M1: matter_docs()})).run().verdict == "delivered"
    [row] = _doc(tmp_path)["rows"]
    assert row["derived"] == vocab.D_UNCLEAR and row["why"] == ["date without source: settled"]


def test_a_gate_as_it_is_is_not_counted_as_a_new_hold(tmp_path: Path) -> None:
    bad = {"path": "defendants[Delta Example].answered", "verdict": "overturned", "corrected": {"date": "2027-01-01"}}
    v = _run(tmp_path, ScriptedLit(verdicts=[bad]), LitSeat({M1: matter_docs()})).run()
    assert v.verdict == "held"
    c = _doc(tmp_path)["counts"]
    assert c["list_held_by_the_gates_as_they_are"] is True
    assert not any("future date" in w and "answered" in w for w in c["reasons"])


def test_a_status_falling_below_the_last_lists_is_a_parity_reason(tmp_path: Path) -> None:
    seat = LitSeat({M1: matter_docs()})
    r = _run(tmp_path, ScriptedLit(result_fn=_settled_stated), seat)
    assert r.run().verdict == "delivered"  # the first list states "Settled" with no settled event
    [row] = _doc(tmp_path)["rows"]
    assert row["printed"] == vocab.D_SETTLED and row["derived"] == vocab.ANSWERED and not row["would_hold"]
    # A full read of the same papers, its events giving less than the stored list said.
    r2 = _run(tmp_path, ScriptedLit(result_fn=_settled_stated), seat, job="j2", job_id="01KTJ0BX0000000000000000BB")
    assert r2.run().verdict == "delivered"
    plan = json.loads((r2.data / "plan.json").read_text())
    plan[M1].update(read_groups=list(vocab.GROUPS), audit="all")
    (r2.data / "plan.json").write_text(json.dumps(plan))
    ctx = r2._ctx(M1)
    n = {k: ctx.by_id[k] for k in ("f-c1", "f-p1", "f-a1")}
    res = read.to_state(ctx, _settled_stated(n), list(vocab.GROUPS), TODAY)
    r2._mfile(M1, "read3.json").write_text(json.dumps({"result": res, "overturns": []}))
    r2._gates()
    [row2] = _doc(tmp_path, "01KTJ0BX0000000000000000BB")["rows"]
    assert row2["would_hold"] and any(w.startswith("parity: status") for w in row2["why"])


def _settled_stated(n: dict[str, int]) -> dict:
    raw = good_result(n)
    raw["defendants"][0]["status"] = vocab.D_SETTLED
    return raw


def test_a_matter_not_read_this_run_is_not_in_the_rate(tmp_path: Path) -> None:
    seat = LitSeat({M1: matter_docs()})
    assert _run(tmp_path, ScriptedLit(), seat).run().verdict == "delivered"
    v = _run(tmp_path, ScriptedLit(), seat, job="j2", job_id="01KTJ0BX0000000000000000BB").run()
    assert v.verdict == "delivered"
    doc = _doc(tmp_path, "01KTJ0BX0000000000000000BB")
    assert doc["counts"]["defendants_read"] == 0 and doc["counts"]["defendants_on_list"] == 1
    [row] = doc["rows"]
    assert row["read"] is False and row["derived"] == row["printed"] and row["rule"] is None


def test_an_update_is_judged_on_the_events_earlier_lists_found(tmp_path: Path) -> None:
    """A dismissal read in January, and a later update that sees only an arrived
    paper: the derivation keeps the dismissal, and an uncited change goes back."""
    seat = LitSeat({M1: matter_docs()})
    client = ScriptedLit(result_fn=_shadow_result(dismissed=("2026-06-01", "f-a1")))
    assert _run(tmp_path, client, seat).run().verdict == "delivered"
    stored = json.loads((tmp_path / "state" / "status_shadow" / "events" / f"{M1}.json").read_text())
    assert stored["deltaexample"]["dismissed"]["date"] == "2026-06-01"
    blob = make_pdf(["PROOF OF SERVICE on a different party, 09/29/2026"])
    seat.blobs["f-p2"] = blob
    seat.per[M1].append(
        {
            "id": "f-p2",
            "name": "POS Zeta",
            "ext": ".pdf",
            "size": len(blob),
            "created": "2026-09-30T00:00:00",
            "modified": "2026-09-30T00:00:00",
            "folderId": None,
            "deleted": False,
        }
    )

    def uncited_event(values: dict) -> dict:
        values["defendants"][0]["settled"] = {"date": "2026-09-01", "doc": 3}  # a paper already on the matter
        return values

    r = _run(tmp_path, ScriptedLit(update_fn=uncited_event), seat, job="j2", job_id="01KTJ0BX0000000000000000BB")
    assert r.run().verdict == "delivered"
    [row] = _doc(tmp_path, "01KTJ0BX0000000000000000BB")["rows"]
    assert row["read"] is True and row["printed"] == vocab.ANSWERED
    # No event moved: an asserted status stands, so the derivation reports what it would print.
    assert row["derived"] == vocab.ANSWERED and row["differs"] is False


def test_a_measurement_that_fails_changes_nothing_and_says_so(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*_a: object, **_k: object) -> None:
        raise RuntimeError("no")

    monkeypatch.setattr(statusshadow, "_build", boom)
    assert _run(tmp_path, ScriptedLit(), LitSeat({M1: matter_docs()})).run().verdict == "delivered"
    assert "RuntimeError" in _doc(tmp_path)["error"]


def test_the_read_command_gives_the_rate(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    client = ScriptedLit(result_fn=_shadow_result(dismissed=("2026-06-01", "f-a1")))
    assert _run(tmp_path, client, LitSeat({M1: matter_docs()})).run().verdict == "delivered"
    assert statusshadow.main([str(tmp_path / "state")]) == 0
    got = json.loads(capsys.readouterr().out)
    assert got["total"] == {
        "defendants_read": 1,
        "differ": 1,
        "would_hold": 0,
        "lists_that_would_have_held": 0,
        "runs": 1,
    }
    assert got["runs"][0]["job_id"] == "01KTJ0BX0000000000000000AA"
    assert statusshadow.rates(tmp_path / "nothing") == {
        "total": {"defendants_read": 0, "differ": 0, "would_hold": 0, "lists_that_would_have_held": 0, "runs": 0},
        "runs": [],
    }


def test_a_held_list_commits_no_events(tmp_path: Path) -> None:
    bad = {"path": "defendants[Delta Example].answered", "verdict": "overturned", "corrected": {"date": "2027-01-01"}}
    client = ScriptedLit(result_fn=_shadow_result(dismissed=("2026-06-01", "f-a1")), verdicts=[bad])
    assert _run(tmp_path, client, LitSeat({M1: matter_docs()})).run().verdict == "held"
    assert not (tmp_path / "state" / "status_shadow" / "events").exists()
    assert (tmp_path / "state" / "status_shadow" / "runs").exists()


def test_enforce_records_no_shadow(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "customer.yaml").write_text("logic_in_code:\n  status_from_events: enforce\n")
    monkeypatch.setenv("SMD_CUSTOMER_YAML_PATH", str(tmp_path / "customer.yaml"))

    def events(n: dict[str, int]) -> dict:
        raw = good_result(n)
        raw["defendants"][0]["sent_for_service"] = []
        return raw

    assert _run(tmp_path, ScriptedLit(result_fn=events), LitSeat({M1: matter_docs()})).run().verdict == "delivered"
    assert not (tmp_path / "state" / "status_shadow").exists()
    d = manifest.load_prior(tmp_path / "state", M1)["defendants"][0]
    assert d["status_from"] == vocab.FROM_DERIVED


def test_the_checks_are_shown_the_events_and_the_name_paths() -> None:
    res = _res()
    shown = json.loads(read.for_model(res, shapes.Ctx()))["defendants"][0]
    assert shown["dismissed"]["date"] == "2026-06-01" and "shadow" not in shown
    firm = SimpleNamespace(data={"form_hints": {}})
    for role in ("verify", "audit"):
        assert "defendants[<exact name>].status" in read.system_for(role, firm, TODAY)
        assert "defendants[<index>]" not in read.system_for(role, firm, TODAY)


def test_an_update_read_is_told_to_copy_the_values_as_it_always_was(tmp_path: Path) -> None:
    seat = LitSeat({M1: matter_docs()})
    assert _run(tmp_path, ScriptedLit(), seat).run().verdict == "delivered"
    blob = make_pdf(["PROOF OF SERVICE on a different party, 09/29/2026"])
    seat.blobs["f-p2"] = blob
    seat.per[M1].append(
        {
            "id": "f-p2",
            "name": "POS Zeta",
            "ext": ".pdf",
            "size": len(blob),
            "created": "2026-09-30T00:00:00",
            "modified": "2026-09-30T00:00:00",
            "folderId": None,
            "deleted": False,
        }
    )
    client = ScriptedLit()
    assert _run(tmp_path, client, seat, job="j2", job_id="01KTJ0BX0000000000000000BB").run().verdict == "delivered"
    system = json.dumps(client.calls[0]["system"])
    assert "copy every current value" in system and "Report ONLY" not in system


def test_a_stated_status_rests_on_the_answer_or_service_as_it_always_did() -> None:
    d = {
        "name": "Delta Example",
        "status": vocab.OUT_FOR_SERVICE,
        "out_for_service": {"value": "yes", "source": {"file_id": "f-o"}},
        "served": {"date": None, "source": None},
        "answered": {"date": None, "source": None},
    }
    assert defstatus.basis(d) == d["served"]


def _reread(tmp_path: Path, seat: LitSeat, groups: list[str], edit: object) -> dict:
    """A second job, handed one more read of the same papers."""
    r = _run(tmp_path, ScriptedLit(), seat, job="j2", job_id="01KTJ0BX0000000000000000BB")
    assert r.run().verdict == "delivered"
    plan = json.loads((r.data / "plan.json").read_text())
    plan[M1].update(read_groups=groups, audit="changed")
    (r.data / "plan.json").write_text(json.dumps(plan))
    ctx = r._ctx(M1)
    raw = good_result({k: ctx.by_id[k] for k in ("f-c1", "f-p1", "f-a1")})
    edit(raw)  # type: ignore[operator]
    res = read.to_state(ctx, raw, list(vocab.GROUPS), TODAY)
    r._mfile(M1, "read3.json").write_text(json.dumps({"result": res, "overturns": []}))
    r._gates()
    return _doc(tmp_path, "01KTJ0BX0000000000000000BB")


def test_a_hold_parity_has_today_is_not_counted_as_a_new_one(tmp_path: Path) -> None:
    seat = LitSeat({M1: matter_docs()})
    assert _run(tmp_path, ScriptedLit(), seat).run().verdict == "delivered"

    def moved_the_service_date(raw: dict) -> None:
        raw["defendants"][0]["served"]["date"] = "2026-04-12"

    [row] = _reread(tmp_path, seat, list(vocab.GROUPS), moved_the_service_date)["rows"]
    assert row["read"] is True and not any("served" in w for w in row["why"])


def test_a_read_of_the_case_alone_does_not_count_its_defendants(tmp_path: Path) -> None:
    seat = LitSeat({M1: matter_docs()})
    assert _run(tmp_path, ScriptedLit(), seat).run().verdict == "delivered"
    doc = _reread(tmp_path, seat, [vocab.GROUP_CASE], lambda raw: None)
    assert doc["counts"]["defendants_read"] == 0 and doc["rows"][0]["read"] is False


# ---- the switch ------------------------------------------------------------------------------------
@pytest.mark.parametrize(
    "body,enforced",
    [
        (None, False),
        ("customer_id: x\n", False),
        ("logic_in_code:\n  conflict_fence: enforce\n", False),
        ("logic_in_code:\n  status_from_events: observe\n", False),
        ("logic_in_code:\n  status_from_events: enforce\n", True),
        ("logic_in_code:\n  status_from_events: maybe\n", True),
        ("a: [unclosed\n", True),
    ],
)
def test_the_switch_decides_the_jobs_mode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, body: str | None, enforced: bool
) -> None:
    path = tmp_path / "customer.yaml"
    if body is not None:
        path.write_text(body)
    monkeypatch.setenv("SMD_CUSTOMER_YAML_PATH", str(path))
    r = _run(tmp_path, ScriptedLit(), LitSeat({M1: matter_docs()}))
    assert r.events_enforced is enforced


def test_one_job_is_one_mode_however_it_resumes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    r = _run(tmp_path, ScriptedLit(), LitSeat({M1: matter_docs()}))
    assert r.events_enforced is False and r._state()[eventmode.STATE_KEY] == "observe"
    path = tmp_path / "customer.yaml"
    path.write_text("logic_in_code:\n  status_from_events: enforce\n")
    monkeypatch.setenv("SMD_CUSTOMER_YAML_PATH", str(path))
    assert _run(tmp_path, ScriptedLit(), LitSeat({M1: matter_docs()})).events_enforced is False  # the same job
    assert _run(tmp_path, ScriptedLit(), LitSeat({M1: matter_docs()}), job="j2").events_enforced is True


def test_an_unreadable_switch_is_logged(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    path = tmp_path / "customer.yaml"
    path.write_text("logic_in_code:\n  status_from_events: maybe\n")
    monkeypatch.setenv("SMD_CUSTOMER_YAML_PATH", str(path))
    said: list[str] = []
    assert eventmode.current(said.append) == eventmode.ENFORCE
    assert said and "enforcing" in said[0]


# ---- a hand finish, in both modes -----------------------------------------------------------------
def _hand_finish(tmp_path: Path, final: dict) -> dict:
    state, job = tmp_path / "state", tmp_path / "hand"
    (job / "data" / "files").mkdir(parents=True)
    (job / "job.json").write_text(json.dumps({"job_id": "01NEWJOB"}))
    (job / "data" / "state.json").write_text(json.dumps({"today": "2026-10-23"}))
    listing = [{"id": f, "modified": "2026-05-02T10:00:00"} for f in ("f-c1", "f-p1", "f-a1", "f-b1")]
    (job / "data" / "files" / f"{M1}.json").write_text(json.dumps(listing))
    fj = job / "data" / "final.json"
    fj.write_text(json.dumps([final]))
    out = finalload.load_final(state, fj, job / "data" / "files")
    assert out["loaded"] == 1 and out["refused"] == {}
    return manifest.load_prior(state, M1)


def test_a_hand_finish_on_an_observe_job_loads_as_it_always_did(tmp_path: Path) -> None:
    client = ScriptedLit(result_fn=_shadow_result(dismissed=("2026-06-01", "f-a1")))
    r = _run(tmp_path, client, LitSeat({M1: matter_docs()}))
    assert r.run().verdict == "delivered"
    final = json.loads((r.data / "final.json").read_text())[0]
    assert "shadow" not in json.dumps(final) and "dismissed" not in final["defendants"][0]
    final["defendants"][0]["status"] = vocab.D_DISMISSED  # a person's correction
    d = _hand_finish(tmp_path, final)["defendants"][0]
    assert d["status"] == vocab.D_DISMISSED and "status_from" not in d


def test_a_hand_finish_on_an_enforce_job_keeps_the_correction(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "customer.yaml").write_text("logic_in_code:\n  status_from_events: enforce\n")
    monkeypatch.setenv("SMD_CUSTOMER_YAML_PATH", str(tmp_path / "customer.yaml"))
    r = _run(tmp_path, ScriptedLit(), LitSeat({M1: matter_docs()}))
    assert r.run().verdict == "delivered"
    final = json.loads((r.data / "final.json").read_text())[0]
    assert final["defendants"][0]["status_from"] == vocab.FROM_DERIVED
    final["defendants"][0]["status"] = vocab.D_SETTLED  # its events do not give this
    d = _hand_finish(tmp_path, final)["defendants"][0]
    assert d["status"] == vocab.D_SETTLED and "status_from" not in d
