"""An UPDATE reuses the delivered job's date of birth (2026-10-29).

The firm was asked to confirm a client's date of birth on an update, after the
delivered chronology had already run on it. The broker now keeps each job's
units and fills an update's missing date from the newest delivered job for the
same client. The status read says only whether a date is on record, never the
date. Synthetic names, dates and ids throughout."""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from workspace_broker import medchron_identity
from workspace_broker.audit_ledger import LedgerWriter
from workspace_broker.medchron_ledger import MedchronLedger
from workspace_broker.medchron_verbs import MedchronVerbs, medchron_dispatch

GATEWAY_PID = 4242
AGENT_UID = 10000
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


def run_envelope(**over):
    e = {
        "matter": {"id": "m-7", "number": "2026-PI-777", "title": "Sample v. Sample"},
        "units": [{"client_name": "Dana Sample", "surname": "Sample", "dob": DOB}],
        "incident": {"date": "2026-02-01", "source": "administrator_request"},
        "requested_by": "admin@example.test",
    }
    e.update(over)
    return e


def update_envelope(unit=None, ids=("d-9",)):
    return run_envelope(
        units=[unit or {"client_name": "Dana Sample", "surname": "Sample"}],
        update={"into": "delivered_chronology"},
        selection={"include_file_ids": list(ids)},
    )


@pytest.fixture
def verbs(tmp_path):
    db = str(tmp_path / "audit.db")
    ledger = LedgerWriter(db)
    (tmp_path / "customer.yaml").write_text(CUSTOMER_YAML)
    v = MedchronVerbs(
        MedchronLedger(db, tmp_path / "queue"),
        customer_yaml=str(tmp_path / "customer.yaml"),
        customer_slug="example",
        audit_append=ledger.append,
        gateway_pid=GATEWAY_PID,
        resolve_agent_uid=lambda: AGENT_UID,
    )
    return v, db, tmp_path / "queue"


def call(v, action, peer_uid=AGENT_UID, **req):
    return medchron_dispatch(v, action, {"action": action, **req}, GATEWAY_PID, peer_uid)


def deliver(v, job_id):
    call(v, "medchron_job_record", peer_uid=ROOT, job_id=job_id, state="running", fields={})
    call(
        v,
        "medchron_job_record",
        peer_uid=ROOT,
        job_id=job_id,
        state="delivered",
        fields={"covered": {"covered": ["d-1"], "uncovered": [], "pulled": 1}},
    )


def test_an_update_with_no_dob_takes_the_delivered_jobs_date(verbs):
    v, _db, queue = verbs
    first = call(v, "medchron_job_submit", envelope=run_envelope())
    assert first["accepted"], first
    deliver(v, first["job_id"])
    second = call(v, "medchron_job_submit", envelope=update_envelope())
    assert second["accepted"], second
    queued = json.loads((queue / f"{second['job_id']}.json").read_text())
    assert queued["units"][0]["dob"] == DOB


def test_the_status_read_says_a_date_is_on_record_but_never_shows_it(verbs):
    v, _db, _queue = verbs
    first = call(v, "medchron_job_submit", envelope=run_envelope())
    deliver(v, first["job_id"])
    matter = call(v, "medchron_job_status", matter_id="m-7")["matter"]
    assert matter["delivered_clients"] == [{"client_name": "Dana Sample", "surname": "Sample", "dob_on_record": True}]
    assert DOB not in json.dumps(matter)


def test_a_date_the_operator_passed_is_never_overwritten(verbs):
    v, _db, queue = verbs
    first = call(v, "medchron_job_submit", envelope=run_envelope())
    deliver(v, first["job_id"])
    unit = {"client_name": "Dana Sample", "surname": "Sample", "dob": "05/06/1977"}
    second = call(v, "medchron_job_submit", envelope=update_envelope(unit))
    queued = json.loads((queue / f"{second['job_id']}.json").read_text())
    assert queued["units"][0]["dob"] == "05/06/1977"


def test_a_run_with_no_dob_is_still_refused(verbs):
    v, _db, _queue = verbs
    unit = {"client_name": "Dana Sample", "surname": "Sample"}
    out = call(v, "medchron_job_submit", envelope=run_envelope(units=[unit]))
    assert out["accepted"] is False
    assert "dob" in out["reason"]


def test_an_undelivered_job_lends_no_date(verbs):
    v, _db, _queue = verbs
    call(v, "medchron_job_submit", envelope=run_envelope())  # submitted, never delivered
    out = call(v, "medchron_job_submit", envelope=update_envelope())
    assert out["accepted"] is False
    assert "dob" in out["reason"]


def test_another_clients_date_is_never_lent(verbs):
    v, _db, _queue = verbs
    first = call(v, "medchron_job_submit", envelope=run_envelope())
    deliver(v, first["job_id"])
    other = {"client_name": "Robin Sample", "surname": "Sample"}
    out = call(v, "medchron_job_submit", envelope=update_envelope(other))
    assert out["accepted"] is False


def test_a_delivery_from_before_units_were_kept_shows_no_date_on_record(verbs):
    v, db, _queue = verbs
    first = call(v, "medchron_job_submit", envelope=run_envelope())
    deliver(v, first["job_id"])
    conn = sqlite3.connect(db)
    conn.execute("DELETE FROM medchron_job_units")
    conn.commit()
    conn.close()
    matter = call(v, "medchron_job_status", matter_id="m-7")["matter"]
    assert matter["delivered_clients"] == [{"client_name": "dana sample", "dob_on_record": False}]
    out = call(v, "medchron_job_submit", envelope=update_envelope())
    assert out["accepted"] is False


def test_the_newest_delivered_job_decides(verbs):
    v, _db, queue = verbs
    first = call(v, "medchron_job_submit", envelope=run_envelope())
    deliver(v, first["job_id"])
    unit = {"client_name": "Dana Sample", "surname": "Sample", "dob": "07/08/1979"}
    second = call(v, "medchron_job_submit", envelope=update_envelope(unit))
    deliver(v, second["job_id"])
    third = call(
        v,
        "medchron_job_submit",
        envelope=update_envelope(ids=("d-10",)),
    )
    assert third["accepted"], third
    queued = json.loads((queue / f"{third['job_id']}.json").read_text())
    assert queued["units"][0]["dob"] == "07/08/1979"


def test_fill_leaves_a_run_envelope_alone():
    env = run_envelope(units=[{"client_name": "Dana Sample", "surname": "Sample"}])
    assert medchron_identity.fill_update_dobs(object(), env) is env
