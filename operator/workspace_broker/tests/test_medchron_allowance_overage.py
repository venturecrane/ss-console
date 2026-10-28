"""``medchron_job_resume`` with SMD's approved overage (2026-10-28).

A chronology held on the cycle page allowance resumes only when the request
carries an approval (``medchron.allowance approve`` on the seat, root only, no
agent tool). The resume's audit row is the billable record of the overage.
Every other use of the extra is refused, and a plain resume of an allowance
hold is still refused."""

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

ALLOWANCE_HOLD = (
    "chronology_package_page_allowance_per_month: the matter's file is 1,200 pages and 1,000 pages "
    "remain in the cycle ending Nov 14's allowance; the package was not started"
)
AUDIT_HOLD = "audit coverage: a live claim is not finally SUPPORTED; last output: GATE FAIL"
CAP_HOLD = "per_job_cap_usd: this run's projected cost would exceed the job's cost cap before audit"
APPROVAL = {"allowance_approved_pages": 200, "allowance_approved_by": "Scott"}


@pytest.fixture
def verbs(tmp_path):
    db = str(tmp_path / "audit.db")
    ledger = LedgerWriter(db)
    (tmp_path / "customer.yaml").write_text(CUSTOMER_YAML)
    queue = tmp_path / "queue"
    v = MedchronVerbs(
        MedchronLedger(db, queue),
        customer_yaml=str(tmp_path / "customer.yaml"),
        customer_slug="example",
        audit_append=ledger.append,
        gateway_pid=GATEWAY_PID,
        resolve_agent_uid=lambda: AGENT_UID,
    )
    return v, db, queue


def call(v, action, peer_pid=GATEWAY_PID, peer_uid=AGENT_UID, **req):
    return medchron_dispatch(v, action, {"action": action, **req}, peer_pid, peer_uid)


def _held_job(v, reason: str) -> str:
    env = {
        "matter": {"id": "m-1", "number": "2026-PI-102", "title": "Example v. Example"},
        "units": [{"client_name": "Alpha Example", "surname": "Example", "dob": "01/02/1980"}],
        "incident": {"date": "2026-01-15", "source": "administrator_request"},
        "requested_by": "admin@example.test",
    }
    j = call(v, "medchron_job_submit", envelope=env)["job_id"]
    call(v, "medchron_job_record", peer_uid=ROOT, job_id=j, state="running", fields={})
    call(v, "medchron_job_record", peer_uid=ROOT, job_id=j, state="held", fields={"reason": reason, "pages": 10})
    return j


def _resume(v, j, peer_uid=ROOT, **extra):
    return call(v, "medchron_job_resume", peer_uid=peer_uid, job_id=j, reason="approved", redo=[], **extra)


def test_an_allowance_hold_still_refuses_a_plain_resume(verbs):
    v, _db, queue = verbs
    j = _held_job(v, ALLOWANCE_HOLD)
    with pytest.raises(ValueError, match="not failed"):
        _resume(v, j)
    assert not (queue / f".resume-{j}.json").exists()


def test_an_approved_overage_resumes_the_hold_and_audits_the_pages(verbs):
    v, db, queue = verbs
    j = _held_job(v, ALLOWANCE_HOLD)
    assert _resume(v, j, **APPROVAL)["queued"] is True
    marker = json.loads((queue / f".resume-{j}.json").read_text())
    assert marker["allowance_approved_pages"] == 200 and marker["allowance_approved_by"] == "Scott"
    conn = sqlite3.connect(db)
    try:
        atype, meta = conn.execute("SELECT action_type, metadata FROM audit_log ORDER BY rowid DESC LIMIT 1").fetchone()
    finally:
        conn.close()
    assert atype == "MEDCHRON_JOB_RUNNING"
    assert json.loads(meta)["allowance_overage_approved"] == {"pages": 200, "by": "Scott"}
    assert "$" not in meta and "USD" not in meta


@pytest.mark.parametrize(
    ("reason", "extra", "match"),
    [
        (AUDIT_HOLD, APPROVAL, "not held on the page allowance"),
        # A page approval never lifts the dollar cap.
        (CAP_HOLD, APPROVAL, "not held on the page allowance"),
        (ALLOWANCE_HOLD, {**APPROVAL, "allowance_approved_pages": 0}, "positive integer"),
        (ALLOWANCE_HOLD, {**APPROVAL, "allowance_approved_pages": True}, "positive integer"),
        (ALLOWANCE_HOLD, {**APPROVAL, "allowance_approved_by": "  "}, "who approved"),
        (ALLOWANCE_HOLD, {"allowance_approved_pages": 5}, "who approved"),
    ],
)
def test_an_approved_overage_is_refused_off_an_allowance_hold_or_malformed(verbs, reason, extra, match):
    v, _db, queue = verbs
    j = _held_job(v, reason)
    with pytest.raises(ValueError, match=match):
        _resume(v, j, **extra)
    assert not (queue / f".resume-{j}.json").exists()


def test_an_approved_overage_is_root_only(verbs):
    v, _db, queue = verbs
    j = _held_job(v, ALLOWANCE_HOLD)
    with pytest.raises(PermissionError):
        _resume(v, j, peer_uid=AGENT_UID, **APPROVAL)
    assert not (queue / f".resume-{j}.json").exists()
