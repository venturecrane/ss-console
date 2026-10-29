"""Recompute a delivered joint matter's coverage record from its job dir (2026-10-29).

A joint matter's record had every cited exhibit of both clients uncovered (each
unit judged the whole manifest). ``medchron.covered_backfill`` recomputes it with
the fixed ``covered_sets`` and hands it to the root-only broker verb
``medchron_covered_recompute``. Driven against the REAL broker verbs and ledger,
so the outcome rules are the broker's own. Synthetic names, dates and ids.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from workspace_broker.audit_ledger import LedgerWriter
from workspace_broker.medchron_ledger import MedchronLedger
from workspace_broker.medchron_verbs import MedchronVerbs, medchron_dispatch

from medchron import config as config_mod, covered_backfill
from medchron_testkit import job_yaml

GATEWAY_PID = 4242
ROOT = 0
#: The record as a joint matter delivered it before the fix: every cited file uncovered.
BROKEN = {
    "covered": [],
    "uncovered": ["a1", "b1", "f1", "o1"],
    "pulled": 4,
    "why": {"a1": "not_explained", "b1": "not_explained", "f1": "retrieval_failed", "o1": "not_explained"},
}

CUSTOMER_YAML = """
scope:
  admins:
    - admin@example.test
personas:
  - slug: operator
    skills:
      - name: medical-chronology-maintainer
        enabled: true
        settings:
          chronology_package_page_allowance_per_month: 1000
"""


def _envelope(matter_id: str, joint: bool) -> dict:
    alpha = {"unit": "alpha", "client_name": "Alpha Example", "surname": "Example", "dob": "01/01/1970"}
    units = [alpha]
    if joint:
        alpha["folder_prefix"] = "/Alpha_Example"
        units.append(
            {
                "unit": "beta",
                "client_name": "Beta Example",
                "surname": "Example",
                "dob": "02/02/1980",
                "folder_prefix": "/Beta_Example",
            }
        )
    return {
        "matter": {"id": matter_id, "number": f"2026-PI-{matter_id}", "title": "Example v. Example"},
        "units": units,
        "incident": {"date": "2026-02-01", "source": "administrator_request"},
        "requested_by": "admin@example.test",
    }


class _Broker:
    def __init__(self, verbs: MedchronVerbs, uid: int | None = ROOT) -> None:
        self.verbs, self.uid = verbs, uid

    def _request(self, req: dict) -> dict:
        return medchron_dispatch(self.verbs, req["action"], req, GATEWAY_PID, self.uid)


@pytest.fixture
def seat(tmp_path):
    db = str(tmp_path / "audit.db")
    (tmp_path / "customer.yaml").write_text(CUSTOMER_YAML)
    v = MedchronVerbs(
        MedchronLedger(db, tmp_path / "queue"),
        customer_yaml=str(tmp_path / "customer.yaml"),
        customer_slug="example",
        audit_append=LedgerWriter(db).append,
        gateway_pid=GATEWAY_PID,
        resolve_agent_uid=lambda: 10000,
    )
    jobs = tmp_path / "run" / "jobs"
    jobs.mkdir(parents=True)
    return v, db, jobs


def _seed_matter(d: Path) -> None:
    """alpha cites a1, beta cites b1; o1 is an orphan, f1 never arrived."""
    (d / "units").mkdir(parents=True, exist_ok=True)
    (d / "units.json").write_text(json.dumps({"alpha": {}, "beta": {}}))
    (d / "units" / "alpha.json").write_text(json.dumps([{"id": "a1", "name": "alpha clinic", "ext": ".pdf"}]))
    (d / "units" / "beta.json").write_text(json.dumps([{"id": "b1", "name": "beta hospital", "ext": ".pdf"}]))
    rows = [
        {"id": "a1", "name": "alpha clinic", "ext": ".pdf", "ok": True},
        {"id": "b1", "name": "beta hospital", "ext": ".pdf", "ok": True},
        {"id": "o1", "name": "loose page", "ext": ".pdf", "ok": True},
        {"id": "f1", "name": "torn page", "ext": ".pdf", "ok": False},
    ]
    (d / "raw_manifest.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    (d / "orphans.json").write_text(json.dumps({"orphans": [{"name": "loose page.pdf", "reason": "no folder"}]}))
    for unit, fname in (("alpha", "alpha clinic.pdf"), ("beta", "beta hospital.pdf")):
        (d / "runs" / unit).mkdir(parents=True, exist_ok=True)
        (d / "runs" / unit / "entries_final.md").write_text("01/02/2026\nClinic\n\nStrain. (Exhibit 1 - p. 1)\n")
        (d / "out" / unit).mkdir(parents=True, exist_ok=True)
        (d / "out" / unit / "page_map.json").write_text(json.dumps([{"exhibit": 1, "files": [{"file": fname}]}]))


def _delivered(v, jobs: Path, matter_id: str, *, joint: bool = True, seed: bool = True, state="delivered") -> str:
    out = medchron_dispatch(v, "medchron_job_submit", {"envelope": _envelope(matter_id, joint)}, GATEWAY_PID, ROOT)
    assert out["accepted"], out
    job_id = out["job_id"]
    medchron_dispatch(v, "medchron_job_record", {"job_id": job_id, "state": "running", "fields": {}}, GATEWAY_PID, ROOT)
    medchron_dispatch(
        v,
        "medchron_job_record",
        {"job_id": job_id, "state": "delivered", "fields": {"covered": BROKEN}},
        GATEWAY_PID,
        ROOT,
    )
    jd = jobs / job_id
    jd.mkdir()
    (jd / "job.yaml").write_text(job_yaml(jd / "data", joint=joint))
    (jd / "daemon.json").write_text(json.dumps({"state": state}))
    if seed:
        _seed_matter(jd / "data" / "example-matter")
    return job_id


def _covered(db: str, job_id: str) -> dict:
    conn = sqlite3.connect(db)
    raw = conn.execute("SELECT covered_json FROM medchron_jobs WHERE id=?", (job_id,)).fetchone()[0]
    conn.close()
    return json.loads(raw)


def _cfg(firm_config_path: Path):
    return config_mod.load(str(firm_config_path))


def test_a_joint_matters_record_is_recomputed_once_and_a_second_run_changes_nothing(seat, firm_config_path):
    v, db, jobs = seat
    job_id = _delivered(v, jobs, "m-7")
    cfg = _cfg(firm_config_path)

    dry = covered_backfill.run(jobs, _Broker(v), cfg, dry_run=True)
    assert dry == {"dry_run": True, "jobs_seen": 1, "recomputed": 1, "skipped": {}}
    assert _covered(db, job_id)["covered"] == []  # a dry run writes nothing

    first = covered_backfill.run(jobs, _Broker(v), cfg)
    assert first == {"dry_run": False, "jobs_seen": 1, "recomputed": 1, "skipped": {}}
    rec = _covered(db, job_id)
    assert rec["covered"] == ["a1", "b1"] and rec["uncovered"] == ["f1", "o1"]
    # The matter's union now tells an update both clients' cited files are delivered.
    matter = medchron_dispatch(v, "medchron_job_status", {"matter_id": "m-7"}, GATEWAY_PID, ROOT)["matter"]
    assert set(matter["covered_document_ids"]) >= {"a1", "b1"}

    again = covered_backfill.run(jobs, _Broker(v), cfg)
    assert again == {"dry_run": False, "jobs_seen": 1, "recomputed": 0, "skipped": {"unchanged": 1}}
    conn = sqlite3.connect(db)
    n = conn.execute("SELECT COUNT(*) FROM audit_log WHERE action_type='MEDCHRON_COVERAGE_RECOMPUTED'").fetchone()[0]
    meta = conn.execute("SELECT metadata FROM audit_log WHERE action_type='MEDCHRON_COVERAGE_RECOMPUTED'").fetchone()[0]
    conn.close()
    assert n == 1 and "a1" not in meta and json.loads(meta)["covered_after"] == 2


def test_skips_are_counted_by_reason(seat, firm_config_path):
    v, db, jobs = seat
    _delivered(v, jobs, "m-7")
    single = _delivered(v, jobs, "m-8", joint=False)
    gone = _delivered(v, jobs, "m-9", seed=False)
    held = _delivered(v, jobs, "m-10", state="held")
    (jobs / "01NEVERQUEUED").mkdir()
    (jobs / "01NEVERQUEUED" / "job.yaml").write_text(job_yaml(jobs / "01NEVERQUEUED" / "data", joint=True))
    (jobs / "01NEVERQUEUED" / "daemon.json").write_text(json.dumps({"state": "delivered"}))
    _seed_matter(jobs / "01NEVERQUEUED" / "data" / "example-matter")
    (jobs / "01BROKEN").mkdir()
    (jobs / "01BROKEN" / "daemon.json").write_text(json.dumps({"state": "delivered"}))
    (jobs / "01BROKEN" / "job.yaml").write_text("{not: [yaml")
    counts = covered_backfill.run(jobs, _Broker(v), _cfg(firm_config_path))
    assert counts == {
        "dry_run": False,
        "jobs_seen": 6,
        "recomputed": 1,
        "skipped": {
            "job_dir_gone": 1,
            "not_delivered": 1,
            "single_unit": 1,
            "unknown_job": 1,
            "unreadable_job": 1,
        },
    }
    for job_id in (single, gone, held):
        assert _covered(db, job_id)["covered"] == []
    assert "Example" not in json.dumps(counts)


def test_an_update_job_is_left_to_its_own_record(seat, firm_config_path, tmp_path):
    v, db, jobs = seat
    job_id = _delivered(v, jobs, "m-7")
    y = yaml.safe_load((jobs / job_id / "job.yaml").read_text())
    y["units"] = y["units"][:1]
    y["update"] = {"into": "delivered_chronology", "chronology_file_id": "f-1"}
    y["selection"] = {"include_file_ids": ["a1"]}
    (jobs / job_id / "job.yaml").write_text(yaml.safe_dump(y))
    counts = covered_backfill.run(jobs, _Broker(v), _cfg(firm_config_path))
    assert counts["skipped"] == {"update_job": 1}
    assert _covered(db, job_id)["covered"] == []


def test_the_broker_refuses_a_caller_that_is_not_root(seat, firm_config_path):
    v, db, jobs = seat
    job_id = _delivered(v, jobs, "m-7")
    with pytest.raises(PermissionError):
        covered_backfill.run(jobs, _Broker(v, uid=10000), _cfg(firm_config_path))
    assert _covered(db, job_id)["covered"] == []


def test_the_command_refuses_to_start_unless_root(monkeypatch, capsys):
    monkeypatch.setattr(covered_backfill.os, "geteuid", lambda: 1000)
    assert covered_backfill.main([]) == 2
    assert "REFUSED" in capsys.readouterr().err
