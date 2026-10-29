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


# ---- the backfill: a job submitted before units were kept ---------------------
def _forget_units(db):
    conn = sqlite3.connect(db)
    conn.execute("DELETE FROM medchron_job_units")
    conn.commit()
    conn.close()


def _audit_rows(db, action_type):
    conn = sqlite3.connect(db)
    try:
        return [r[0] for r in conn.execute("SELECT metadata FROM audit_log WHERE action_type=?", (action_type,))]
    except sqlite3.OperationalError:
        return []
    finally:
        conn.close()


def backfill(v, job_id, units, peer_uid=ROOT, **kw):
    return call(v, "medchron_units_backfill", peer_uid=peer_uid, job_id=job_id, units=units, **kw)


def test_a_backfilled_delivery_lends_its_date_to_the_next_update(verbs):
    """The A&P case: delivered before units were kept, so the update would ask."""
    v, db, queue = verbs
    first = call(v, "medchron_job_submit", envelope=run_envelope())
    deliver(v, first["job_id"])
    _forget_units(db)
    assert backfill(v, first["job_id"], run_envelope()["units"])["outcome"] == "recorded"
    matter = call(v, "medchron_job_status", matter_id="m-7")["matter"]
    assert matter["delivered_clients"] == [{"client_name": "Dana Sample", "surname": "Sample", "dob_on_record": True}]
    second = call(v, "medchron_job_submit", envelope=update_envelope())
    assert second["accepted"], second
    assert json.loads((queue / f"{second['job_id']}.json").read_text())["units"][0]["dob"] == DOB
    # The audit row names the job and counts the units; never a name or a date.
    (meta,) = _audit_rows(db, "MEDCHRON_UNITS_BACKFILLED")
    assert json.loads(meta) == {"job_id": first["job_id"], "units": 1}
    assert DOB not in meta and "Sample" not in meta


def test_a_backfill_is_idempotent_and_never_replaces_a_kept_row(verbs):
    v, db, _queue = verbs
    first = call(v, "medchron_job_submit", envelope=run_envelope())
    deliver(v, first["job_id"])
    _forget_units(db)
    assert backfill(v, first["job_id"], run_envelope()["units"])["outcome"] == "recorded"
    other = [{"client_name": "Dana Sample", "surname": "Sample", "dob": "09/09/1999"}]
    assert backfill(v, first["job_id"], other)["outcome"] == "already_recorded"
    assert backfill(v, first["job_id"], run_envelope()["units"])["outcome"] == "already_recorded"
    assert len(_audit_rows(db, "MEDCHRON_UNITS_BACKFILLED")) == 1
    # A job kept at submit is already recorded too.
    kept = call(v, "medchron_job_submit", envelope=run_envelope(matter={"id": "m-8", "number": "2026-PI-778"}))
    assert backfill(v, kept["job_id"], run_envelope()["units"])["outcome"] == "already_recorded"


def test_a_backfill_with_no_date_of_birth_keeps_nothing(verbs):
    v, db, _queue = verbs
    first = call(v, "medchron_job_submit", envelope=run_envelope())
    deliver(v, first["job_id"])
    _forget_units(db)
    bare = [{"client_name": "Dana Sample", "surname": "Sample", "dob": " "}]
    assert backfill(v, first["job_id"], bare)["outcome"] == "no_dob"
    assert backfill(v, first["job_id"], "not a list")["outcome"] == "no_dob"
    assert medchron_identity.recorded(v._db, first["job_id"]) is False
    assert _audit_rows(db, "MEDCHRON_UNITS_BACKFILLED") == []


def test_a_backfill_for_a_job_the_broker_never_queued_keeps_nothing(verbs):
    v, db, _queue = verbs
    assert backfill(v, "01UNKNOWN", run_envelope()["units"])["outcome"] == "unknown_job"
    assert medchron_identity.recorded(v._db, "01UNKNOWN") is False
    assert _audit_rows(db, "MEDCHRON_UNITS_BACKFILLED") == []


def test_a_backfill_naming_other_clients_keeps_nothing(verbs):
    v, db, _queue = verbs
    first = call(v, "medchron_job_submit", envelope=run_envelope())
    deliver(v, first["job_id"])
    _forget_units(db)
    other = [{"client_name": "Robin Sample", "surname": "Sample", "dob": DOB}]
    assert backfill(v, first["job_id"], other)["outcome"] == "names_differ"
    assert medchron_identity.recorded(v._db, first["job_id"]) is False


def test_a_dry_run_backfill_decides_and_writes_nothing(verbs):
    v, db, _queue = verbs
    first = call(v, "medchron_job_submit", envelope=run_envelope())
    deliver(v, first["job_id"])
    _forget_units(db)
    out = backfill(v, first["job_id"], run_envelope()["units"], dry_run=True)
    assert out["outcome"] == "recorded" and out["dry_run"] is True
    assert medchron_identity.recorded(v._db, first["job_id"]) is False
    assert _audit_rows(db, "MEDCHRON_UNITS_BACKFILLED") == []


@pytest.mark.parametrize("uid", [AGENT_UID, 1234, None])
def test_a_backfill_is_root_only(verbs, uid):
    """An update reuses the date this keeps, so it must come from the job's own
    envelope on the seat, never from a conversation the agent could steer."""
    v, db, _queue = verbs
    first = call(v, "medchron_job_submit", envelope=run_envelope())
    deliver(v, first["job_id"])
    _forget_units(db)
    with pytest.raises(PermissionError):
        backfill(v, first["job_id"], run_envelope()["units"], peer_uid=uid)
    assert medchron_identity.recorded(v._db, first["job_id"]) is False
