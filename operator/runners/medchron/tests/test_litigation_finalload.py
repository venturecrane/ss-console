"""`baseline final`: a delivered job's values into the lane's state, per group.

Every name, number and date is invented. The stored state carries a discovery
group and a sent-out date the source job did not read; both must survive.
"""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import pytest

from litigation_testkit import M1, M2
from medchron.litigation import baseline, finalload, manifest, vocab


def _src(fid: str, name: str = "Paper.pdf") -> dict:
    return {"file_id": fid, "name": name, "doc_date": None}


STORED = {
    "matter_id": M1,
    "case_name": "Gamma v. Delta",
    "case_status": {"value": vocab.ACTIVE, "detail": "", "source": _src("f-c1")},
    "complaint_filed": {"date": "2026-03-02", "source": _src("f-c1")},
    "next_court_date": {"date": None, "event": "", "source": None},
    "court": "Superior Court of Exampletown",
    "case_number": "CV-0001",
    "firm_role": "plaintiff",
    "defendants": [
        {
            "name": "Delta Example",
            "status": vocab.SERVED_NO_ANSWER,
            "served": {"date": "2026-04-10", "source": _src("f-p1")},
            "answered": {"date": None, "source": None},
            "sent_for_service": {"date": "2026-03-20", "source": _src("f-p1")},
            "flags": [],
        }
    ],
    "discovery_propounded": [{"set": "Form Interrogatories, Set One", "date": "2026-05-01", "source": _src("f-d1")}],
    "discovery_served_on_client": [],
    "fields_read": ["case", "defendants", "discovery"],
    "provenance": {"job_id": "OLD", "read_at": "2026-10-07"},
}


def _final() -> dict:
    m = copy.deepcopy(STORED)
    del m["provenance"]
    m["case_status"] = {"value": vocab.SETTLED_OWED, "detail": "notice of settlement — filed", "source": _src("f-s1")}
    m["defendants"][0].pop("sent_for_service")  # this job did not read it
    m["defendants"][0]["status"] = vocab.ANSWERED
    m["defendants"][0]["answered"] = {"date": "2026-04-30", "source": _src("f-a1")}
    m["discovery_propounded"] = []  # not read on a court-only list
    m["fields_read"] = ["case", "defendants"]
    m.update(number="100001", title="Gamma v. Delta", responsible="x", integrity=[], overturns=[{"pass": 2}])
    return m


def _setup(tmp_path: Path, final: list | dict | None = None, stored: dict | None = STORED) -> tuple[Path, Path, Path]:
    state, job = tmp_path / "state", tmp_path / "job"
    if stored is not None:
        manifest.commit(state, M1, stored, [{"id": "f-c1", "modified": "2026-03-03"}])
    (job / "data" / "files").mkdir(parents=True)
    (job / "job.json").write_text(json.dumps({"job_id": "01NEWJOB"}))
    (job / "data" / "state.json").write_text(json.dumps({"today": "2026-10-23"}))
    listing = [{"id": f, "modified": "2026-05-02T10:00:00"} for f in ("f-c1", "f-p1", "f-a1", "f-s1", "f-d1")]
    (job / "data" / "files" / f"{M1}.json").write_text(json.dumps(listing))
    fj = job / "data" / "final.json"
    fj.write_text(json.dumps([_final()] if final is None else final))
    return state, fj, job / "data" / "files"


def _hash(root: Path) -> dict[str, str]:
    return {
        str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest() for p in root.rglob("*") if p.is_file()
    }


def test_groups_merge_and_what_the_job_did_not_read_is_kept(tmp_path):
    state, fj, files = _setup(tmp_path)
    out = baseline.load_final(state, fj, files)
    m = manifest.load_prior(state, M1)
    assert m["case_status"]["value"] == vocab.SETTLED_OWED
    assert m["defendants"][0]["answered"]["date"] == "2026-04-30"
    assert m["defendants"][0]["sent_for_service"]["date"] == "2026-03-20"  # kept
    assert m["discovery_propounded"] == STORED["discovery_propounded"]  # not carried, kept
    assert m["fields_read"] == ["case", "defendants", "discovery"]
    assert m["provenance"] == {"job_id": "01NEWJOB", "read_at": "2026-10-23", "loaded_from": "final"}
    assert not {"overturns", "responsible", "integrity"} & set(m)
    assert m["case_status"]["detail"] == "notice of settlement, filed" and out["normalized"] == 1
    assert manifest.load_manifest(state, M1) == {
        f: "2026-05-02T10:00:00" for f in ("f-c1", "f-p1", "f-a1", "f-s1", "f-d1")
    }
    assert (out["loaded"], out["groups_replaced"], out["kept"], out["sent_for_service_kept"]) == (1, 2, 1, 1)
    again = baseline.load_final(state, fj, files)
    assert again["loaded"] == 0 and again["unchanged"] == 1  # a second load changes nothing


def test_a_dry_run_writes_nothing_and_reports_the_gaps_left(tmp_path):
    state, fj, files = _setup(tmp_path)
    before = _hash(state)
    out = finalload.load_final(state, fj, files, dry_run=True)
    assert _hash(state) == before
    assert out["loaded"] == 1 and out["dry_run"] is True
    assert set(out["gaps"]) == {"hard", "soft"}


def test_an_uncited_date_refuses_its_group_and_the_stored_one_stands(tmp_path):
    f = _final()
    f["defendants"][0]["answered"] = {"date": "2026-04-30", "source": "the answer in the file"}  # prose, no file id
    state, fj, files = _setup(tmp_path, [f])
    out = finalload.load_final(state, fj, files)
    m = manifest.load_prior(state, M1)
    assert out["refused"] == {"uncited": 1}
    assert m["defendants"] == STORED["defendants"] and m["case_status"]["value"] == vocab.SETTLED_OWED
    assert m["fields_read"] == ["case", "discovery"]  # the refused group is read again next run


def test_nothing_replaced_advances_no_provenance_and_commits_no_manifest(tmp_path):
    f = _final()
    f["complaint_filed"] = {"date": "2026-03-02", "source": None}  # case refused
    f["fields_read"] = ["case"]
    state, fj, files = _setup(tmp_path, [f])
    man = (state / "manifest" / f"{M1}.json").read_bytes()
    out = finalload.load_final(state, fj, files)
    m = manifest.load_prior(state, M1)
    assert out["groups_replaced"] == 0 and out["refused_only"] == 1 and out["loaded"] == 0
    assert m["provenance"] == STORED["provenance"] and (state / "manifest" / f"{M1}.json").read_bytes() == man
    assert m["fields_read"] == ["defendants", "discovery"] and m["case_status"] == STORED["case_status"]
    assert "number" not in m  # a matter's own fields ride only with a delivered group


def test_a_same_day_delivery_by_another_job_is_never_overwritten(tmp_path):
    other = {**STORED, "provenance": {"job_id": "01OTHER", "read_at": "2026-10-23"}}
    state, fj, files = _setup(tmp_path, stored=other)
    before = _hash(state)
    assert finalload.load_final(state, fj, files)["skipped_newer"] == 1 and _hash(state) == before
    same = {**STORED, "provenance": {"job_id": "01NEWJOB", "read_at": "2026-10-23"}}
    state2, fj2, files2 = _setup(tmp_path / "b", stored=same)
    assert finalload.load_final(state2, fj2, files2)["loaded"] == 1  # the same job loads again


def test_a_defendant_that_is_not_a_record_is_refused_not_a_crash(tmp_path):
    f = _final()
    f["defendants"].append("Epsilon Example")
    state, fj, files = _setup(tmp_path, [f])
    out = finalload.load_final(state, fj, files)
    assert out["refused"] == {"malformed": 1}
    assert manifest.load_prior(state, M1)["defendants"] == STORED["defendants"]


def test_a_date_not_iso_is_refused(tmp_path):
    f = _final()
    f["complaint_filed"] = {"date": "03/02/2026", "source": _src("f-c1")}
    state, fj, files = _setup(tmp_path, [f])
    out = finalload.load_final(state, fj, files)
    assert out["refused"] == {"non_iso_date": 1}
    assert manifest.load_prior(state, M1)["case_status"]["value"] == vocab.ACTIVE


def test_newer_stored_state_is_not_touched(tmp_path):
    newer = {**STORED, "provenance": {"job_id": "LATER", "read_at": "2026-10-30"}}
    state, fj, files = _setup(tmp_path, stored=newer)
    before = _hash(state)
    out = finalload.load_final(state, fj, files)
    assert out["skipped_newer"] == 1 and out["loaded"] == 0 and _hash(state) == before


def test_a_matter_with_no_stored_state_is_loaded_from_what_it_carries(tmp_path):
    f = {**_final(), "matter_id": M2}
    state, fj, files = _setup(tmp_path, [f])
    (files / f"{M2}.json").write_text(json.dumps([{"id": "f-c1", "modified": "2026-05-02T10:00:00"}]))
    finalload.load_final(state, fj, files)
    m = manifest.load_prior(state, M2)
    assert m["fields_read"] == ["case", "defendants"] and "discovery_propounded" not in m


def test_another_shape_is_refused_whole_and_the_cli_says_so(tmp_path, capsys):
    state, fj, files = _setup(tmp_path, {"rows": []})
    with pytest.raises(finalload.FinalShapeError):
        finalload.load_final(state, fj, files)
    assert baseline.main(["final", str(fj), str(files), str(state)]) == 2
    assert "not a job's final.json" in json.loads(capsys.readouterr().out)["error"]


def test_the_cli_dry_run_prints_one_line(tmp_path, capsys):
    state, fj, files = _setup(tmp_path)
    before = _hash(state)
    assert baseline.main(["final", str(fj), str(files), str(state), "--job-id", "X1", "--dry-run"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["loaded"] == 1 and out["dry_run"] is True and _hash(state) == before
    assert baseline.main(["final", str(fj)]) == 2
