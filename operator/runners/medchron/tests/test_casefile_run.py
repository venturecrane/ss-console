"""The case file job end to end on a synthetic seat: inventory, the $0 screen,
the read with its validator, the second reader, the ranking, the numbered
lines, the File Status filed and read back per matter, the caseload workbook
filed and read back, the lane state committed, and the money per stage and per
matter in the verdict. Then a second job on the unchanged seat pays nothing."""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import pytest

import casefile_testkit as kit
from medchron.casefile import state as state_mod
from medchron.casefile.run import CasefileRun


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, pricing_path: Path) -> dict:
    for k, v in kit.environ(tmp_path).items():
        monkeypatch.setenv(k, v)
    return {
        "inputs": kit.make_inputs(tmp_path / "inputs"),
        "state": tmp_path / "state",
        "pricing": str(pricing_path),
        "tmp": tmp_path,
    }


def _run(env: dict, seat: kit.CfSeat, model: kit.Scripted, job: str = kit.JOB) -> tuple[CasefileRun, dict]:
    jd = kit.make_job(env["tmp"] / f"job-{job[-4:]}", job_id=job)
    r = CasefileRun(
        jd,
        inputs_dir=str(env["inputs"]),
        pricing=env["pricing"],
        state_dir=env["state"],
        seat_factory=lambda: seat,
        client=model,
        log=lambda m: None,
        today=lambda: dt.date(2026, 10, 26),
        readback_pause=0,
    )
    return r, json.loads(r.run().to_json())


def test_full_job_delivers_and_proposes_only_what_passes(env: dict) -> None:
    seat, model = kit.CfSeat(), kit.Scripted()
    _r, v = _run(env, seat, model)
    assert v["verdict"] == "delivered", v
    assert (v["matters_total"], v["matters_read"], v["matters_held"]) == (2, 2, 0)
    kinds = sorted((ln["kind"], ln["source_id"]) for ln in v["lines"])
    # the confirmed closure and the bill whose charge is in its text; never the money task
    assert ("task", kit.TASK_OPEN) in kinds
    assert all(sid != kit.TASK_MONEY for _k, sid in kinds)
    assert [k for k, _s in kinds].count("medicals") == 1
    assert [ln["n"] for ln in v["lines"]] == list(range(1, len(v["lines"]) + 1))
    roles = [f["role"] for f in v["files"]]
    assert roles.count("workbook") == 1 and roles.count("file_status") == 2
    assert v["proof"]["readback"] == "confirmed"
    # every File Status is in its own matter's "File Status" folder
    for mid in (kit.M1, kit.M2):
        folder = next(f for f in seat.folders[mid] if f["name"] == "File Status")
        assert any(r["folderId"] == folder["id"] and r["name"].startswith("File Status") for r in seat.files[mid])
    # money per stage and per matter, tokens included
    m = v["metrics"]
    assert set(m["stages"]) == {"casefile_read", "casefile_audit"}
    assert set(m["matters"]) == {kit.M1, kit.M2}
    assert m["matters"][kit.M1]["cache_read"] > 0 and v["cents"] > 0
    # the lane's state holds the delivered records and every write the lane made
    assert state_mod.load_prior(env["state"], kit.M1)["job_id"] == kit.JOB
    assert len(state_mod.own_ids(env["state"])) >= 3


def test_an_unchanged_caseload_is_already_current_and_pays_nothing(env: dict) -> None:
    seat = kit.CfSeat()
    _run(env, seat, kit.Scripted())
    model = kit.Scripted()
    _r, v = _run(env, seat, model, job="01KTJ0BX0000000000000000CG")
    assert v["verdict"] == "delivered", v
    assert (v["matters_current"], v["matters_read"]) == (2, 0)
    assert model.calls == [] and v["cents"] == 0
    assert [f["role"] for f in v["files"]] == ["workbook"]  # no File Status refiled


def test_a_changed_matter_is_read_with_its_prior_record_and_only_the_new_document(env: dict) -> None:
    seat = kit.CfSeat()
    _run(env, seat, kit.Scripted())
    seat.blobs["f5f5f5f5-f5f5-4f5f-8f5f-f5f5f5f5f5f5"] = kit.make_pdf(["EXAMPLE LETTER\nto Gamma Example"])
    seat.files[kit.M1].append(seat._row("f5f5f5f5-f5f5-4f5f-8f5f-f5f5f5f5f5f5", "New letter.pdf", "2026-10-25"))
    model = kit.Scripted()
    _r, v = _run(env, seat, model, job="01KTJ0BX0000000000000000CH")
    assert (v["matters_current"], v["matters_read"]) == (1, 1), v
    pack = kit.first_text(next(c for c in model.calls if c["tools"][-1]["name"] == "record_review"))
    assert "PRIOR REVIEW RECORD" in pack
    assert "DOCUMENTS NEW OR CHANGED SINCE THE PRIOR REVIEW (1 of 1" in pack


def test_a_refused_record_goes_back_to_the_reader_then_holds_only_that_matter(env: dict) -> None:
    seat = kit.CfSeat()
    seat.files[kit.M1] = [r for r in seat.files[kit.M1] if r["id"] != kit.DOC_SCAN]

    def always_bad(number: str, pack: str) -> dict:
        rec = kit.review_for(number, pack)
        if number == "100001":
            rec["open_tasks"] = rec["open_tasks"][:1]  # the money task left undispositioned
        return rec

    model = kit.Scripted(review_fn=always_bad)
    _r, v = _run(env, seat, model)
    assert v["verdict"] == "delivered" and v["matters_held"] == 1 and v["matters_read"] == 1
    reads = [c for c in model.calls if c["tools"][-1]["name"] == "record_review" and "100001" in kit.first_text(c)]
    assert len(reads) == 3  # the first answer plus validate_retries (2) refusals sent back
    assert "undispositioned" in json.dumps(reads[-1]["messages"][-1])


def test_an_unconfirmed_closure_is_never_proposed(env: dict) -> None:
    _r, v = _run(env, kit.CfSeat(), kit.Scripted(verdict="unsure"))
    assert v["verdict"] == "delivered"
    assert all(ln["kind"] != "task" for ln in v["lines"])


def test_every_matter_held_holds_the_job(env: dict) -> None:
    def broken(number: str, pack: str) -> dict:
        return {"number": number}

    _r, v = _run(env, kit.CfSeat(), kit.Scripted(review_fn=broken))
    assert v["verdict"] == "held" and v["reason"].startswith("all_matters_held:")


def test_a_name_the_staff_list_cannot_settle_holds_before_anything_is_paid(env: dict) -> None:
    seat, model = kit.CfSeat(), kit.Scripted()
    jd = kit.make_job(env["tmp"] / "job-x", scope={"assisting": {"names": ["Nobody Example"]}})
    r = CasefileRun(jd, inputs_dir=str(env["inputs"]), pricing=env["pricing"], state_dir=env["state"],
                    seat_factory=lambda: seat, client=model, log=lambda m: None, readback_pause=0)  # fmt: skip
    v = json.loads(r.run().to_json())
    assert v["verdict"] == "held" and v["reason"].startswith("scope_unresolved:")
    assert model.calls == []


def test_a_proof_run_stops_before_the_first_write(env: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MEDCHRON_PROOF_RUN", "1")
    seat = kit.CfSeat()
    _r, v = _run(env, seat, kit.Scripted())
    assert v["verdict"] == "dry_run" and seat.uploads == 0
    assert state_mod.load_prior(env["state"], kit.M1) is None


def test_the_library_matter_is_found_by_its_number_when_no_id_is_authored(env: dict) -> None:
    seat = kit.CfSeat()
    jd = kit.make_job(env["tmp"] / "job-n", file_to_matter_id="")
    r = CasefileRun(jd, inputs_dir=str(env["inputs"]), pricing=env["pricing"], state_dir=env["state"],
                    seat_factory=lambda: seat, client=kit.Scripted(), log=lambda m: None,
                    today=lambda: dt.date(2026, 10, 26), readback_pause=0)  # fmt: skip
    v = json.loads(r.run().to_json())
    assert v["verdict"] == "delivered", v
    assert any(f["name"].startswith("Case File Manager") for f in seat.files[kit.LIBRARY])


def test_a_library_number_that_is_not_exactly_one_open_matter_holds_before_anything_is_paid(env: dict) -> None:
    model = kit.Scripted()
    jd = kit.make_job(env["tmp"] / "job-u", file_to_matter_id="", file_to_matter_number="NO-SUCH")
    r = CasefileRun(jd, inputs_dir=str(env["inputs"]), pricing=env["pricing"], state_dir=env["state"],
                    seat_factory=kit.CfSeat, client=model, log=lambda m: None, readback_pause=0)  # fmt: skip
    v = json.loads(r.run().to_json())
    assert v["verdict"] == "held" and v["reason"].startswith("library_unresolved:") and model.calls == []


def test_a_hold_after_file_statuses_were_filed_reports_what_it_filed(env: dict) -> None:
    seat = kit.CfSeat()
    name = f"Case File Manager 2026-10-26 (Operator {kit.JOB[-6:]})"
    seat.create_folder(kit.LIBRARY, "Case File Manager")
    folder = seat.folders[kit.LIBRARY][-1]["id"]
    seat.files[kit.LIBRARY].append(  # somebody else's file under this run's own name
        {"id": "x-1", "name": name, "ext": ".xlsx", "size": 1, "folderId": folder,
         "created": "", "modified": "", "deleted": False}
    )  # fmt: skip
    _r, v = _run(env, seat, kit.Scripted())
    assert v["verdict"] == "held" and v["reason"].startswith("filing_refused:")
    assert [f["role"] for f in v["files"]] == ["file_status", "file_status"]


def test_the_text_cache_key_is_the_litigation_lanes_for_the_same_file(env: dict, monkeypatch) -> None:
    import litigation_testkit as lit

    from medchron.casefile import firm as cf_firm
    from medchron.casefile.run import cache_method_version
    from medchron.litigation import firm as lit_firm, textcache

    models = {"read": "m-r", "verify": "m-v", "audit": "m-a", "transcription": "m-ocr"}
    monkeypatch.setenv("MEDCHRON_LITIGATION_INPUTS", str(lit.make_inputs(env["tmp"] / "lit", models=models)))
    ours = cache_method_version(cf_firm.load(env["inputs"]))
    assert ours == textcache.method_version(lit_firm.load().model("transcription"))
    assert ours != textcache.method_version(cf_firm.load(env["inputs"]).model("transcription"))


def test_a_second_job_the_same_day_files_its_update_under_its_own_name(env: dict) -> None:
    seat = kit.CfSeat()
    _r, first = _run(env, seat, kit.Scripted())
    assert first["verdict"] == "delivered", first
    # A later job the same day whose screen sees the matter changed (state cleared
    # so it reads again): the File Status name it would use is already filed.
    import shutil

    shutil.rmtree(env["state"])
    _r2, v = _run(env, seat, kit.Scripted(), job="01KTJ0BX0000000000000000C2")
    assert v["verdict"] == "delivered", v
    assert v["metrics"]["filing_refused"] == 0
    names = sorted(f["name"] for f in v["files"] if f["role"] == "file_status")
    assert names and all(n.endswith("(Operator 0000C2).pdf") for n in names), names
