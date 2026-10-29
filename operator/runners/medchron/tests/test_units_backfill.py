"""Backfill a delivered job's units from its job dir still on the seat (2026-10-29).

Since 2026-10-29 the broker keeps each submitted job's units, so an update
reuses the delivered job's date of birth. A job submitted before that has none;
its envelope is still in ``<run dir>/jobs/<job_id>/`` until the dir is wiped.
``medchron.units_backfill`` reads each one present and hands its units to the
root-only broker verb. Driven here against the REAL broker verbs and ledger, so
the outcome rules are the broker's own. Synthetic names, dates and ids.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from workspace_broker import medchron_identity
from workspace_broker.audit_ledger import LedgerWriter
from workspace_broker.medchron_ledger import MedchronLedger
from workspace_broker.medchron_verbs import MedchronVerbs, medchron_dispatch

from medchron import units_backfill

GATEWAY_PID = 4242
ROOT = 0
DOB = "03/04/1975"

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


def _envelope(matter_id: str = "m-7", name: str = "Dana Sample", dob: str | None = DOB) -> dict:
    unit = {"client_name": name, "surname": name.split()[-1]}
    if dob is not None:
        unit["dob"] = dob
    return {
        "matter": {"id": matter_id, "number": f"2026-PI-{matter_id}", "title": "Sample v. Sample"},
        "units": [unit],
        "incident": {"date": "2026-02-01", "source": "administrator_request"},
        "requested_by": "admin@example.test",
    }


class _Broker:
    """The runner's broker client, answered by the real verbs as ``uid``."""

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


def _delivered_before_units_were_kept(v, db, jobs, envelope: dict) -> str:
    """A job submitted and delivered, its kept units dropped (as on a seat
    before 2026-10-29), its envelope in its job dir as the daemon left it."""
    out = medchron_dispatch(v, "medchron_job_submit", {"envelope": envelope}, GATEWAY_PID, ROOT)
    assert out["accepted"], out
    job_id = out["job_id"]
    for state, fields in (("running", {}), ("delivered", {"covered": {"covered": ["d-1"], "uncovered": []}})):
        medchron_dispatch(
            v, "medchron_job_record", {"job_id": job_id, "state": state, "fields": fields}, GATEWAY_PID, ROOT
        )
    conn = sqlite3.connect(db)
    conn.execute("DELETE FROM medchron_job_units WHERE job_id=?", (job_id,))
    conn.commit()
    conn.close()
    (jobs / job_id).mkdir()
    (jobs / job_id / "envelope.json").write_text(json.dumps(envelope))
    return job_id


def test_every_job_dir_present_is_kept_once_and_a_second_run_changes_nothing(seat, capsys):
    v, db, jobs = seat
    a = _delivered_before_units_were_kept(v, db, jobs, _envelope("m-7"))
    b = _delivered_before_units_were_kept(v, db, jobs, _envelope("m-8", name="Robin Sample"))
    first = units_backfill.run(jobs, _Broker(v))
    assert first == {"dry_run": False, "jobs_seen": 2, "recorded": 2, "skipped": {}}
    assert medchron_identity.recorded(v._db, a) and medchron_identity.recorded(v._db, b)
    again = units_backfill.run(jobs, _Broker(v))
    assert again == {"dry_run": False, "jobs_seen": 2, "recorded": 0, "skipped": {"already_recorded": 2}}
    # The matter now says a date is on record, so the update will not ask.
    matter = medchron_dispatch(v, "medchron_job_status", {"matter_id": "m-7"}, GATEWAY_PID, ROOT)["matter"]
    assert matter["delivered_clients"][0]["dob_on_record"] is True


def test_skips_are_counted_by_reason_and_nothing_names_a_client(seat):
    v, db, jobs = seat
    _delivered_before_units_were_kept(v, db, jobs, _envelope("m-7"))
    no_dob = _delivered_before_units_were_kept(v, db, jobs, _envelope("m-8"))
    (jobs / no_dob / "envelope.json").write_text(json.dumps(_envelope("m-8", dob=None)))
    (jobs / "01NEVERQUEUED").mkdir()
    (jobs / "01NEVERQUEUED" / "envelope.json").write_text(json.dumps(_envelope("m-9")))
    (jobs / "01NOENVELOPE").mkdir()
    (jobs / "01BROKEN").mkdir()
    (jobs / "01BROKEN" / "envelope.json").write_text("{not json")
    (jobs / "stray-file").write_text("x")  # not a job dir
    counts = units_backfill.run(jobs, _Broker(v))
    assert counts == {
        "dry_run": False,
        "jobs_seen": 5,
        "recorded": 1,
        "skipped": {"no_dob": 1, "no_envelope": 1, "unknown_job": 1, "unreadable_envelope": 1},
    }
    assert not medchron_identity.recorded(v._db, no_dob)
    assert not medchron_identity.recorded(v._db, "01NEVERQUEUED")
    text = json.dumps(counts)
    assert DOB not in text and "Sample" not in text


def test_a_dry_run_counts_and_keeps_nothing(seat):
    v, db, jobs = seat
    a = _delivered_before_units_were_kept(v, db, jobs, _envelope("m-7"))
    counts = units_backfill.run(jobs, _Broker(v), dry_run=True)
    assert counts == {"dry_run": True, "jobs_seen": 1, "recorded": 1, "skipped": {}}
    assert not medchron_identity.recorded(v._db, a)


def test_no_jobs_dir_is_zero_jobs(tmp_path, seat):
    v, _db, _jobs = seat
    assert units_backfill.run(tmp_path / "absent", _Broker(v)) == {
        "dry_run": False,
        "jobs_seen": 0,
        "recorded": 0,
        "skipped": {},
    }


def test_the_broker_refuses_a_caller_that_is_not_root(seat):
    v, db, jobs = seat
    a = _delivered_before_units_were_kept(v, db, jobs, _envelope("m-7"))
    with pytest.raises(PermissionError):
        units_backfill.run(jobs, _Broker(v, uid=10000))
    assert not medchron_identity.recorded(v._db, a)


def test_the_command_refuses_to_start_unless_root(monkeypatch, capsys):
    monkeypatch.setattr(units_backfill.os, "geteuid", lambda: 1000)
    assert units_backfill.main([]) == 2
    assert "REFUSED" in capsys.readouterr().err


def test_the_command_prints_counts_only(monkeypatch, capsys, seat):
    v, db, jobs = seat
    _delivered_before_units_were_kept(v, db, jobs, _envelope("m-7"))

    class _Daemon:
        def __init__(self) -> None:
            self.jobs, self.broker = jobs, _Broker(v)

    from medchron import daemon as daemon_mod

    monkeypatch.setattr(units_backfill.os, "geteuid", lambda: 0)
    monkeypatch.setattr(daemon_mod, "build", _Daemon)
    assert units_backfill.main([]) == 0
    out = capsys.readouterr().out
    assert json.loads(out) == {"dry_run": False, "jobs_seen": 1, "recorded": 1, "skipped": {}}
    assert DOB not in out and "Sample" not in out and "m-7" not in out
