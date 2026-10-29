"""A delivered job's coverage record is replaced by one recomputed from its job dir (2026-10-29).

A joint matter recorded every cited exhibit of both clients as uncovered, and
the next update re-filed exhibits the firm already had. ``medchron.covered_backfill``
(root, on the seat) recomputes the record and hands it to the root-only verb
``medchron_covered_recompute``, which changes only ``covered_json`` on a
DELIVERED row that already carries one. Synthetic names and ids throughout."""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from workspace_broker.audit_ledger import LedgerWriter
from workspace_broker.medchron_ledger import MedchronLedger
from workspace_broker.medchron_verbs import MedchronVerbs, medchron_dispatch

GATEWAY_PID = 4242
AGENT_UID = 10000
ROOT = 0
VERB = "medchron_covered_recompute"

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
BROKEN = {"covered": [], "uncovered": ["a1", "b1", "o1"], "pulled": 3}
FIXED = {"covered": ["a1", "b1"], "uncovered": ["o1"], "pulled": 3, "why": {"o1": "not_explained"}}


@pytest.fixture
def v(tmp_path):
    db = str(tmp_path / "audit.db")
    (tmp_path / "customer.yaml").write_text(CUSTOMER_YAML)
    verbs = MedchronVerbs(
        MedchronLedger(db, tmp_path / "queue"),
        customer_yaml=str(tmp_path / "customer.yaml"),
        customer_slug="example",
        audit_append=LedgerWriter(db).append,
        gateway_pid=GATEWAY_PID,
        resolve_agent_uid=lambda: AGENT_UID,
    )
    verbs.db_path = db  # type: ignore[attr-defined]
    return verbs


def _job(v, matter: str, *, deliver: bool = True, covered: dict | None = BROKEN) -> str:
    env = {
        "matter": {"id": matter, "number": f"2026-PI-{matter}", "title": "Sample v. Sample"},
        "units": [{"client_name": "Dana Sample", "surname": "Sample", "dob": "03/04/1975"}],
        "incident": {"date": "2026-02-01", "source": "administrator_request"},
        "requested_by": "admin@example.test",
    }
    out = medchron_dispatch(v, "medchron_job_submit", {"envelope": env}, GATEWAY_PID, ROOT)
    job_id = out["job_id"]
    medchron_dispatch(v, "medchron_job_record", {"job_id": job_id, "state": "running", "fields": {}}, GATEWAY_PID, ROOT)
    if deliver:
        fields = {"covered": covered} if covered is not None else {}
        medchron_dispatch(
            v, "medchron_job_record", {"job_id": job_id, "state": "delivered", "fields": fields}, GATEWAY_PID, ROOT
        )
    return job_id


def _call(v, job_id: str, covered=FIXED, *, dry_run: bool = False, uid=ROOT) -> dict:
    return medchron_dispatch(v, VERB, {"job_id": job_id, "covered": covered, "dry_run": dry_run}, GATEWAY_PID, uid)


def _row(v, job_id: str) -> dict:
    conn = sqlite3.connect(v.db_path)
    raw = conn.execute("SELECT covered_json FROM medchron_jobs WHERE id=?", (job_id,)).fetchone()[0]
    n = conn.execute("SELECT COUNT(*) FROM audit_log WHERE action_type='MEDCHRON_COVERAGE_RECOMPUTED'").fetchone()[0]
    conn.close()
    return {"covered": json.loads(raw) if raw else None, "audits": n}


def test_a_delivered_record_is_replaced_once_and_audited_in_counts(v):
    job_id = _job(v, "m-1")
    dry = _call(v, job_id, dry_run=True)
    assert dry["outcome"] == "recomputed" and _row(v, job_id)["covered"]["covered"] == []
    out = _call(v, job_id)
    assert out["outcome"] == "recomputed"
    assert (out["covered_before"], out["uncovered_before"], out["covered_after"], out["uncovered_after"]) == (
        0,
        3,
        2,
        1,
    )
    row = _row(v, job_id)
    assert row["covered"]["covered"] == ["a1", "b1"] and row["audits"] == 1
    status = medchron_dispatch(v, "medchron_job_status", {"matter_id": "m-1"}, GATEWAY_PID, ROOT)["matter"]
    assert status["covered_document_ids"] == ["a1", "b1"]
    assert _call(v, job_id)["outcome"] == "unchanged" and _row(v, job_id)["audits"] == 1
    conn = sqlite3.connect(v.db_path)
    meta = conn.execute("SELECT metadata FROM audit_log WHERE action_type='MEDCHRON_COVERAGE_RECOMPUTED'").fetchone()[0]
    conn.close()
    assert "a1" not in meta, "the audit row carries counts, never the ids"


def test_only_a_delivered_row_with_a_record_is_touched(v):
    assert _call(v, "01NOSUCHJOB")["outcome"] == "unknown_job"
    running = _job(v, "m-2", deliver=False)
    assert _call(v, running)["outcome"] == "not_delivered"
    bare = _job(v, "m-3", covered=None)
    assert _call(v, bare)["outcome"] == "no_record"
    assert _row(v, bare)["covered"] is None


def test_a_malformed_record_is_refused_and_nothing_changes(v):
    job_id = _job(v, "m-4")
    with pytest.raises(ValueError):
        _call(v, job_id, {"covered": ["a1"], "uncovered": ["a1"]})
    with pytest.raises(ValueError):
        _call(v, job_id, {"covered": ["a1"], "uncovered": [], "pulled": 5})
    assert _row(v, job_id)["covered"]["covered"] == []


def test_no_caller_but_root_reaches_it(v):
    job_id = _job(v, "m-5")
    for uid in (AGENT_UID, None):
        with pytest.raises(PermissionError):
            _call(v, job_id, uid=uid)
    assert _row(v, job_id)["covered"]["covered"] == []
