"""What a chronology job is for and where it is (``medchron_progress``, 2026-11-19).

* A second request for the same person (surname and date of birth, spelled
  another way) while one is active on the matter is refused by the live
  job's id and state; two clients on one matter are both accepted.
* The status read names the clients, the kind and the stage, never the
  expected finish; a stage note moves only a running row, and writes no
  audit row.
* An update's new documents are computed in code from the listing and the
  coverage record.
* A date of birth submitted on two documents records which two.

Every name, date and id here is invented.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import pytest

# The broker suite runs in importlib mode: the sibling helper module by path.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_medchron_verbs import AGENT_UID, ROOT, audit_types, call, envelope, verbs  # noqa: F401 - verbs is the fixture

OTHER_MATTER = {"id": "m-9", "number": "2026-PI-909", "title": "Other v. Other"}


def _submit(v, **over):
    return call(v, "medchron_job_submit", envelope=envelope(**over))


def _record(v, job_id, state, **fields):
    return call(v, "medchron_job_record", peer_pid=1, peer_uid=ROOT, job_id=job_id, state=state, fields=fields)


# ---- duplicate by person ------------------------------------------------------------
def test_the_same_person_spelled_another_way_while_running_is_refused_by_the_live_job(verbs):
    v, _, _ = verbs
    first = _submit(v)["job_id"]
    _record(v, first, "running")
    again = _submit(
        v,
        units=[{"client_name": "Alpha  B. Example", "surname": "example ", "dob": "01/02/1980"}],
        injuries="neck and back",
    )
    assert again["accepted"] is False
    assert again["job_id"] == first and again["job_state"] == "running"
    assert f"already running as job {first}" in again["reason"]


def test_a_spelling_that_folds_spaces_and_case_is_one_person(verbs):
    v, _, _ = verbs
    first = _submit(v, units=[{"client_name": "Gamma De La Cruz", "surname": "De La Cruz", "dob": "03/04/1975"}])
    again = _submit(v, units=[{"client_name": "Gamma Delacruz", "surname": "delacruz", "dob": "03/04/1975"}])
    assert again["accepted"] is False and again["job_id"] == first["job_id"]
    assert again["job_state"] == "submitted"


def test_two_clients_on_one_matter_are_both_accepted(verbs):
    v, _, _ = verbs
    a = _submit(v, units=[{"client_name": "Alpha Example", "surname": "Example", "dob": "01/02/1980"}])
    b = _submit(v, units=[{"client_name": "Beta Sample", "surname": "Sample", "dob": "05/06/1990"}])
    assert a["accepted"] and b["accepted"] and a["job_id"] != b["job_id"]


def test_the_same_surname_with_another_birth_date_is_another_person(verbs):
    v, _, _ = verbs
    assert _submit(v)["accepted"]
    assert _submit(v, units=[{"client_name": "Delta Example", "surname": "Example", "dob": "07/08/2001"}])["accepted"]


def test_the_same_person_on_another_matter_or_after_the_job_ended_is_accepted(verbs):
    v, _, _ = verbs
    first = _submit(v)["job_id"]
    assert _submit(v, matter=OTHER_MATTER)["accepted"]
    _record(v, first, "running")
    _record(v, first, "failed", reason="map: a stage failed")
    assert _submit(v, injuries="a later ask")["accepted"]


def test_an_update_is_not_refused_by_the_same_persons_held_run(verbs):
    """The rerouted run: the RUN held because the matter holds a delivery, and
    the DELIVER turn resubmits the UPDATE for the same person."""
    v, _, _ = verbs
    first = _submit(v)["job_id"]
    _record(v, first, "running")
    _record(v, first, "held", reason="rerouted_to_update: the matter already holds a delivered chronology")
    upd = envelope(
        update={"into": "delivered_chronology"},
        selection={"include_file_ids": ["doc-1"]},
    )
    assert v.ledger.active_duplicate(upd) is None
    assert v.ledger.active_duplicate(envelope(injuries="other words")) == (first, "held")


def test_a_row_written_before_kind_was_kept_is_left_to_the_work_digest_rule(verbs):
    v, _, _ = verbs
    first = _submit(v)["job_id"]
    conn = sqlite3.connect(v.ledger._db_path)
    conn.execute("UPDATE medchron_jobs SET kind = NULL WHERE id = ?", (first,))
    conn.commit()
    conn.close()
    assert _submit(v, injuries="other words")["accepted"]


# ---- status: clients, kind, stage ---------------------------------------------------
def test_a_running_rows_status_names_its_client_kind_and_stage_and_never_its_finish(verbs):
    v, ledger, _ = verbs
    job = _submit(v)["job_id"]
    _record(v, job, "running")
    before = audit_types(ledger._db_path)
    _record(v, job, "running", stage="compose", expected_finish="2026-11-20T03:00:00Z")
    assert audit_types(ledger._db_path) == before, "a stage note writes no audit row"
    got = call(v, "medchron_job_status", job_id=job)["job"]
    assert got["clients"] == ["Alpha Example"] and got["kind"] == "run" and got["stage"] == "compose"
    assert "expected_finish" not in got and "dob" not in json.dumps(got)
    listed = call(v, "medchron_job_status")["jobs"][0]
    assert listed["clients"] == ["Alpha Example"] and listed["stage"] == "compose"
    conn = sqlite3.connect(v.ledger._db_path)
    stored = conn.execute("SELECT expected_finish FROM medchron_jobs WHERE id = ?", (job,)).fetchone()[0]
    conn.close()
    assert stored == "2026-11-20T03:00:00Z"


def test_a_stage_note_never_moves_a_held_row_back_to_running(verbs):
    """MUTATION: route the note through the ordinary transition (held ->
    running is a legal edge) and the late note re-opens the hold."""
    v, _, _ = verbs
    job = _submit(v)["job_id"]
    _record(v, job, "running")
    _record(v, job, "running", stage="compose")
    _record(v, job, "held", reason="review_held: one must-fix")
    with pytest.raises(ValueError, match="lands only on a running job"):
        _record(v, job, "running", stage="review")
    # The real ledger, the note applied AFTER the held record, as a beat in flight would.
    with pytest.raises(ValueError, match="lands only on a running job"):
        v.ledger.record(job, "running", {"stage": "review", "expected_finish": "2026-11-20T03:00:00Z"})
    row = v.ledger.read(job)
    assert row["state"] == "held" and row["stage"] == "compose" and row["expected_finish"] is None


def test_twins_are_refused_and_the_refusal_never_claims_the_job_is_the_requesters_client(verbs):
    """The match is surname and birth date, so twins match. The refusal says
    a chronology for a client with that surname and birth date is in flight
    and names whom the job was submitted for, never that it is this client's."""
    v, _, _ = verbs
    a = _submit(v, units=[{"client_name": "Alpha Example", "surname": "Example", "dob": "01/02/1980"}])["job_id"]
    b = _submit(v, units=[{"client_name": "Beta Example", "surname": "Example", "dob": "01/02/1980"}])
    assert b["accepted"] is False and b["job_id"] == a
    assert "a client with the surname and date of birth of Beta Example" in b["reason"]
    assert "(submitted for Alpha Example)" in b["reason"]
    assert b["clients"][0]["job_clients"] == ["Alpha Example"]


def test_a_stage_name_outside_the_dags_spelling_is_refused(verbs):
    v, _, _ = verbs
    job = _submit(v)["job_id"]
    _record(v, job, "running")
    with pytest.raises(ValueError, match="stage must match"):
        _record(v, job, "running", stage="Compose; DROP")


# ---- an update's new documents --------------------------------------------------------
def _delivered(v, covered, uncovered):
    call(
        v,
        "medchron_backfill_covered",
        peer_pid=1,
        peer_uid=ROOT,
        matter_id="m-1",
        matter_number="2026-PI-102",
        delivered_at="2026-10-01",
        source="test",
        covered={"covered": covered, "uncovered": uncovered, "total": len(covered) + len(uncovered)},
    )


def _named(v, client_key):
    """The backfilled delivery's row records which client it was for."""
    conn = sqlite3.connect(v.ledger._db_path)
    conn.execute("UPDATE medchron_jobs SET client_keys = ? WHERE state = 'delivered'", (json.dumps([client_key]),))
    conn.commit()
    conn.close()


def test_an_updates_new_documents_are_the_listing_minus_what_was_covered(verbs):
    v, _, _ = verbs
    _delivered(v, ["d-1", "d-2"], ["d-3"])
    _named(v, "alpha example")
    got = call(
        v,
        "medchron_update_files",
        peer_pid=999,
        peer_uid=AGENT_UID,
        matter_id="m-1",
        client_name="alpha  example",
        listing_ids=["d-4", "d-1", "d-3", "d-2", "d-4"],
    )
    assert got["coverage_record"] is True and got["coverage_complete"] is True
    assert got["new_document_ids"] == ["d-4", "d-3"] and got["count"] == 2
    assert got["previously_uncovered"] == ["d-3"]


def test_a_delivery_that_names_no_client_is_never_subtracted(verbs):
    """A matter whose only delivered row recorded no client: it may be any
    client's delivery, so nothing is subtracted and the answer says the set
    is not complete, on every matter, joint or not. MUTATION: count a
    NULL-client row for the asking client and d-1 and d-2 leave the answer."""
    v, _, _ = verbs
    _delivered(v, ["d-1", "d-2"], ["d-3"])
    got = call(
        v, "medchron_update_files", matter_id="m-1", client_name="Alpha Example", listing_ids=["d-1", "d-2", "d-4"]
    )
    assert got["coverage_complete"] is False and "name no client" in got["coverage_note"]
    assert got["coverage_record"] is False and got["new_document_ids"] is None


def test_a_delivery_with_a_recorded_client_subtracts_for_that_client_only(verbs):
    v, _, _ = verbs
    _delivered(v, ["d-1", "d-2"], ["d-3"])
    _named(v, "beta sample")
    beta = call(v, "medchron_update_files", matter_id="m-1", client_name="Beta Sample", listing_ids=["d-1", "d-4"])
    alpha = call(v, "medchron_update_files", matter_id="m-1", client_name="Alpha Example", listing_ids=["d-1", "d-4"])
    assert beta["new_document_ids"] == ["d-4"] and beta["coverage_complete"] is True
    assert alpha["coverage_record"] is False and alpha["coverage_complete"] is True


def test_with_no_coverage_record_nothing_is_computed(verbs):
    v, _, _ = verbs
    got = call(v, "medchron_update_files", matter_id="m-1", client_name="Alpha Example", listing_ids=["d-1"])
    assert got == {
        "ok": True,
        "coverage_complete": True,
        "coverage_record": False,
        "new_document_ids": None,
        "count": None,
    }


# ---- a request in which only some clients have a live job ------------------------
ALPHA = {"client_name": "Alpha Example", "surname": "Example", "dob": "01/02/1980", "folder_prefix": "Alpha"}
BETA = {"client_name": "Beta Sample", "surname": "Sample", "dob": "05/06/1990", "folder_prefix": "Beta"}


def test_two_clients_one_live_names_the_live_one_and_leaves_the_other_to_submit(verbs):
    v, _, _ = verbs
    live = _submit(v, units=[ALPHA])["job_id"]
    both = _submit(v, units=[ALPHA, BETA])
    assert both["accepted"] is False and both["uncovered_clients"] == ["Beta Sample"]
    assert [(c["client_name"], c["job_id"]) for c in both["clients"]] == [
        ("Alpha Example", live),
        ("Beta Sample", None),
    ]
    assert f"of Alpha Example is already queued as job {live}" in both["reason"]
    assert "Not covered by any job: Beta Sample" in both["reason"]
    assert _submit(v, units=[BETA])["accepted"], "the uncovered client is accepted on its own"


def test_two_clients_both_live_is_refused_naming_both_jobs(verbs):
    v, _, _ = verbs
    a = _submit(v, units=[ALPHA])["job_id"]
    b = _submit(v, units=[BETA])["job_id"]
    both = _submit(v, units=[ALPHA, BETA])
    assert both["accepted"] is False and "uncovered_clients" not in both
    assert a in both["reason"] and b in both["reason"]
    assert {c["job_id"] for c in both["clients"]} == {a, b}


def test_the_update_files_read_needs_the_listing_and_refuses_an_unknown_caller(verbs):
    v, _, _ = verbs
    with pytest.raises(ValueError, match="listing_ids"):
        call(v, "medchron_update_files", matter_id="m-1", client_name="Alpha Example")
    with pytest.raises(PermissionError):
        call(v, "medchron_update_files", peer_pid=999, peer_uid=12345, matter_id="m-1", client_name="A", listing_ids=[])


# ---- the two documents a date of birth was read from --------------------------------
def test_the_two_documents_a_birth_date_was_read_from_ride_the_submitted_row(verbs):
    v, ledger, _ = verbs
    job = _submit(v, dob_sources=["doc-11", "doc-12"])["job_id"]
    conn = sqlite3.connect(ledger._db_path)
    meta = [
        json.loads(m)
        for (m,) in conn.execute("SELECT metadata FROM audit_log WHERE action_type = ?", ("MEDCHRON_JOB_SUBMITTED",))
    ]
    conn.close()
    assert meta[-1]["job_id"] == job and meta[-1]["dob_sources"] == ["doc-11", "doc-12"]


@pytest.mark.parametrize("bad", [["doc-11"], ["doc-11", "doc-11"], "doc-11", ["doc 11", "doc-12"]])
def test_birth_date_sources_that_are_not_two_document_ids_are_refused(verbs, bad):
    v, _, _ = verbs
    got = _submit(v, dob_sources=bad)
    assert got["accepted"] is False and "dob_sources" in got["reason"]
