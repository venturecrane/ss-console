"""The litigation job end to end on a fake seat and a scripted model: the
staged walk, the read-back, the state commit, resume, holds and failures."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from litigation_testkit import (
    LIBRARY,
    M1,
    LitSeat,
    ScriptedLit,
    first_text,
    good_result,
    make_inputs,
    make_job,
    matter_docs,
)
from medchron.litigation import manifest, vocab
from medchron.litigation.run import LitigationRun
from medchron_testkit import make_pdf
import datetime as dt

TODAY = dt.date(2026, 10, 7)


def _run(
    tmp_path: Path, client: ScriptedLit, seat: LitSeat | None = None, job: str = "job", **kw
) -> tuple[LitigationRun, LitSeat]:
    seat = seat or LitSeat({M1: matter_docs()})
    jd = make_job(tmp_path / job, **kw)
    r = LitigationRun(
        jd,
        inputs_dir=str(make_inputs(tmp_path / "inputs")),
        pricing=str(tmp_path / "pricing.json"),
        state_dir=tmp_path / "state",
        seat_factory=lambda: seat,
        client=client,
        log=lambda m: None,
        today=lambda: TODAY,
        readback_pause=0.0,
    )
    return r, seat


@pytest.fixture(autouse=True)
def _pricing(tmp_path: Path, pricing_path: Path) -> None:
    (tmp_path / "pricing.json").write_text(pricing_path.read_text())


def test_a_full_run_delivers_a_read_back_workbook_and_commits_state(tmp_path):
    r, seat = _run(tmp_path, ScriptedLit())
    v = r.run()
    assert v.verdict == "delivered", v.reason
    assert v.matters_total == 1 and v.matters_reread == 1 and v.cents > 0
    f = v.files[0]
    assert f["role"] == "workbook" and f["name"].startswith("Litigation status 2026-10-07") and f["sha256"]
    assert seat.created and seat.created[0]["name"] == "Litigation Status"
    st = manifest.load_prior(tmp_path / "state", M1)
    assert st["defendants"][0]["answered"]["source"]["name"] == "Answer Delta.pdf"
    assert set(st["fields_read"]) == set(vocab.GROUPS)
    assert "f-b1" in manifest.load_manifest(tmp_path / "state", M1)
    out = json.loads(v.to_json())
    assert set(out) == {
        "verdict",
        "stage",
        "cents",
        "matters_total",
        "matters_reread",
        "flags_new",
        "files",
        "folder_id",
    }


def test_an_unchanged_matter_is_not_read_again(tmp_path):
    r, seat = _run(tmp_path, ScriptedLit())
    assert r.run().verdict == "delivered"
    client2 = ScriptedLit()
    r2, _ = _run(tmp_path, client2, seat=seat, job="second", job_id="01KTJ0BX0000000000000000BB")
    v = r2.run()
    assert v.verdict == "delivered", v.reason
    assert v.matters_reread == 0 and client2.calls == []


def test_a_new_court_paper_rereads_but_a_new_medical_bill_does_not(tmp_path):
    from medchron.litigation.firm import load

    firm = load(make_inputs(tmp_path / "in"))
    files = [{"id": "a", "name": "Complaint", "ext": ".pdf", "modified": "2026-03-01T00:00:00"}]
    prior = {"fields_read": list(vocab.GROUPS)}
    man = manifest.current_manifest(files)
    bill = files + [{"id": "b", "name": "Clinic bill", "ext": ".pdf", "modified": "2026-09-01T00:00:00"}]
    assert manifest.plan_matter(bill, prior, man, firm, TODAY)["reason"] == "unchanged"
    pos = files + [{"id": "c", "name": "POS Delta", "ext": ".pdf", "modified": "2026-09-01T00:00:00"}]
    p = manifest.plan_matter(pos, prior, man, firm, TODAY)
    assert p["reason"] == "changed" and p["trigger_files"] == ["c"]
    email = files + [{"id": "e", "name": "Re: lunch", "ext": ".msg", "modified": "2026-09-01T00:00:00"}]
    assert manifest.plan_matter(email, prior, man, firm, TODAY)["reason"] == "changed"  # among the newest emails
    seed = manifest.plan_matter(files, {"fields_read": ["case", "defendants"]}, {"a": ""}, firm, TODAY)
    assert seed["read_groups"] == ["discovery"] and seed["reason"] == "missing_fields"
    two = manifest.plan_matter(files, {**prior, "provenance": {"two_pass": True}}, man, firm, TODAY)
    assert two["audit"] == "all" and two["read_groups"] == []


def test_a_gate_refusal_holds_and_files_nothing(tmp_path):
    def bad(n):
        res = good_result(n)
        res["defendants"][0]["answered"] = {"date": "2026-04-30", "doc": None}  # a date with no source
        return res

    r, seat = _run(tmp_path, ScriptedLit(result_fn=bad))
    v = r.run()
    assert v.verdict == "held" and v.reason.startswith("gate_refused: ") and v.stage == "gates"
    assert not seat.sent and manifest.load_prior(tmp_path / "state", M1) is None


def test_a_read_that_never_records_marks_the_matter_unread_and_holds(tmp_path):
    client = ScriptedLit(never_finish=True)
    r, seat = _run(tmp_path, client)
    v = r.run()
    assert v.verdict == "held" and "matter could not be read" in v.reason and v.stage == "gates"
    assert seat.sent == []
    u = json.loads((r.data / "m" / M1 / "unread.json").read_text())
    assert u["stage"] == "read1" and u["reason"].startswith("read_incomplete: ")
    # the cap's last call is forced: only the final tool, chosen, no thinking
    last = client.calls[-1]
    assert last["tool_choice"] == {"type": "tool", "name": "record_result"} and "thinking" not in last
    assert [t["name"] for t in last["tools"]] == ["record_result"]


def test_one_matters_failure_does_not_stop_the_others(tmp_path, monkeypatch):
    from litigation_testkit import M2

    monkeypatch.setattr("medchron.llm.time.sleep", lambda s: None)

    calls = {"n": 0}

    class FlakyFirst(ScriptedLit):
        def _msg(self, params):
            if "MATTER: 100001 " in first_text(params):
                calls["n"] += 1
                raise ValueError("a transport failure on this matter only")
            return super()._msg(params)

    docs2 = [("m2" + fid[1:], n, b, d) for fid, n, b, d in matter_docs()]
    seat = LitSeat({M1: matter_docs(), M2: docs2})
    r, _ = _run(tmp_path, FlakyFirst(), seat=seat)
    v = r.run()
    assert v.verdict == "held" and "matter could not be read (1)" in v.reason
    assert (r.data / "m" / M2 / "read3.json").is_file() and (r.data / "m" / M1 / "unread.json").is_file()


def test_the_cap_stops_the_run_inside_a_read(tmp_path):
    r, _ = _run(tmp_path, ScriptedLit(), cents=10_000)  # the month is already at its budget
    v = r.run()
    assert v.verdict == "failed" and v.reason.startswith("limit: monthly_budget_usd")


def test_a_resume_skips_finished_stages_and_matters(tmp_path):
    client = ScriptedLit()
    r, seat = _run(tmp_path, client)
    seat.crash_after = 0  # the send raises: the intent row stays, nothing resent
    v = r.run()
    assert v.verdict == "failed" and v.stage == "file"
    calls = len(client.calls)
    seat.crash_after = None
    # the send raised before landing: a person reads the folder; here we let the
    # same file land by hand, the way a lost response would leave it
    seat.add_file(LIBRARY, seat.created[0]["id"], r._out().name, r._out().read_bytes())
    r2 = LitigationRun(
        r.job.job_dir,
        inputs_dir=str(tmp_path / "inputs"),
        pricing=str(tmp_path / "pricing.json"),
        state_dir=tmp_path / "state",
        seat_factory=lambda: seat,
        client=client,
        log=lambda m: None,
        readback_pause=0.0,
    )
    v2 = r2.run()
    assert v2.verdict == "delivered", v2.reason
    assert len(client.calls) == calls  # no read was paid for twice


def test_a_missing_file_is_an_integrity_flag_not_absence(tmp_path):
    seat = LitSeat({M1: matter_docs() + [("f-x1", "Answer Other.pdf", make_pdf(["x"]), "2026-06-01T00:00:00")]})
    seat.gone.add("f-x1")
    r, _ = _run(tmp_path, ScriptedLit(), seat=seat)
    v = r.run()
    assert v.verdict == "delivered", v.reason
    final = json.loads((r.data / "final.json").read_text())[0]
    assert any("Answer Other.pdf" in f and "content is missing" in f for f in final["matter_flags"])
