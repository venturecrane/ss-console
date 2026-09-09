"""The routine-11 broker verbs (ss#2614): medchron_job_submit,
medchron_job_status, medchron_allowance, medchron_job_list,
medchron_job_record. Peer gating per verb, the allowance arithmetic, the
envelope refusals, and the monotonic ledger transitions with one pinned
audit type each."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from workspace_broker.audit_ledger import LedgerWriter  # noqa: E402
from workspace_broker.medchron_ledger import (  # noqa: E402
    MedchronLedger,
    allowance_from_customer_yaml,
    validate_envelope,
)
from workspace_broker.medchron_verbs import MedchronVerbs, medchron_dispatch  # noqa: E402

GATEWAY_PID = 4242
AGENT_UID = 10000
ROOT = 0

CUSTOMER_YAML = """
personas:
  - slug: operator
    skills:
      - name: other-skill
      - name: medical-chronology-maintainer
        enabled: true
        settings:
          treatment_gap_flag_days: 45
          chronology_package_page_allowance_per_month: 1000
"""


def envelope(**over):
    e = {
        "matter": {"id": "m-1", "number": "2026-PI-102", "title": "Example v. Example"},
        "units": [{"client_name": "Alpha Example", "surname": "Example", "dob": "01/02/1980"}],
        "incident": {"date": "2026-01-15", "source": "administrator_request"},
        "requested_by": "admin@example.test",
    }
    e.update(over)
    return e


@pytest.fixture
def verbs(tmp_path):
    db = str(tmp_path / "audit.db")
    ledger = LedgerWriter(db)
    (tmp_path / "customer.yaml").write_text(CUSTOMER_YAML)
    queue = tmp_path / "queue"
    v = MedchronVerbs(MedchronLedger(db, queue), customer_yaml=str(tmp_path / "customer.yaml"),
                      customer_slug="example", audit_append=ledger.append, gateway_pid=GATEWAY_PID,
                      resolve_agent_uid=lambda: AGENT_UID)
    return v, ledger, queue


def call(v, action, peer_pid=GATEWAY_PID, peer_uid=AGENT_UID, **req):
    return medchron_dispatch(v, action, {"action": action, **req}, peer_pid, peer_uid)


def audit_types(db_path: str) -> list[str]:
    import sqlite3

    conn = sqlite3.connect(db_path)
    try:
        return [r[0] for r in conn.execute("SELECT action_type FROM audit_log ORDER BY rowid")]
    finally:
        conn.close()


# -- gates -----------------------------------------------------------------


def test_submit_is_gateway_or_root_only(verbs):
    v, _, _ = verbs
    with pytest.raises(PermissionError):
        call(v, "medchron_job_submit", peer_pid=999, peer_uid=AGENT_UID, envelope=envelope())
    assert call(v, "medchron_job_submit", envelope=envelope())["accepted"]
    assert call(v, "medchron_job_submit", peer_pid=999, peer_uid=ROOT, envelope=envelope())["accepted"]


def test_record_is_root_only_and_list_is_agent_or_root(verbs):
    v, _, _ = verbs
    job_id = call(v, "medchron_job_submit", envelope=envelope())["job_id"]
    with pytest.raises(PermissionError):
        call(v, "medchron_job_record", job_id=job_id, state="running", fields={})
    with pytest.raises(PermissionError):
        call(v, "medchron_job_record", peer_pid=999, peer_uid=AGENT_UID, job_id=job_id, state="running", fields={})
    assert call(v, "medchron_job_record", peer_pid=1, peer_uid=ROOT, job_id=job_id, state="running",
                fields={})["job"]["state"] == "running"
    with pytest.raises(PermissionError):
        call(v, "medchron_job_list", peer_pid=999, peer_uid=12345)
    assert [j["id"] for j in call(v, "medchron_job_list", peer_pid=999, peer_uid=AGENT_UID)["jobs"]] == [job_id]


def test_a_broker_without_the_ledger_refuses(tmp_path):
    with pytest.raises(ValueError):
        medchron_dispatch(None, "medchron_allowance", {}, GATEWAY_PID, AGENT_UID)
    v = MedchronVerbs(None, customer_yaml="/nonexistent", customer_slug="x", audit_append=lambda r: None,
                      gateway_pid=GATEWAY_PID, resolve_agent_uid=lambda: AGENT_UID)
    with pytest.raises(ValueError):
        call(v, "medchron_allowance")


# -- envelope ----------------------------------------------------------------


@pytest.mark.parametrize(
    "bad",
    [
        {"matter": {"id": "", "number": "x"}},
        {"units": []},
        {"units": [{"client_name": "A", "surname": "B", "dob": "1980-01-02"}]},
        {"units": [{"client_name": "A", "surname": "B", "dob": "01/02/1980"},
                   {"client_name": "C", "surname": "D", "dob": "01/02/1981"}]},   # joint without folder_prefix
        {"incident": {"date": "01/15/2026", "source": "administrator_request"}},
        {"incident": {"date": "2026-01-15", "source": "guess"}},
        {"cap_usd": 0},
    ],
)
def test_submit_refuses_a_malformed_envelope_in_prose(verbs, bad):
    v, _, queue = verbs
    r = call(v, "medchron_job_submit", envelope=envelope(**bad))
    assert r["ok"] and r["accepted"] is False and r["reason"]
    assert not queue.exists() or not list(queue.glob("*.json"))


def test_validate_envelope_keeps_only_known_keys_and_derives_unit_slugs():
    e = validate_envelope(envelope(secret="no", injuries="neck", cap_usd=25))
    assert set(e) == {"matter", "units", "incident", "injuries", "cap_usd", "requested_by"}
    assert e["units"][0]["unit"] == "example" and e["units"][0]["name_token"] == "Example"
    assert e["matter"]["title"] == "Example v. Example"


# -- allowance ---------------------------------------------------------------


def test_allowance_reads_the_skill_settings_and_fails_closed(tmp_path):
    p = tmp_path / "c.yaml"
    p.write_text(CUSTOMER_YAML)
    assert allowance_from_customer_yaml(p) == 1000
    p.write_text(CUSTOMER_YAML.replace("enabled: true", "enabled: false"))
    assert allowance_from_customer_yaml(p) is None
    p.write_text("personas:\n  - slug: operator\n    skills:\n      - name: other\n")
    assert allowance_from_customer_yaml(p) is None
    assert allowance_from_customer_yaml(tmp_path / "missing.yaml") is None


def _deliver(v, job_id, **fields):
    call(v, "medchron_job_record", peer_uid=ROOT, job_id=job_id, state="running", fields={})
    call(v, "medchron_job_record", peer_uid=ROOT, job_id=job_id, state="delivered", fields=fields)


def test_allowance_counts_the_months_pages_and_submit_stops_at_the_crossing(verbs):
    v, _, _ = verbs
    a = call(v, "medchron_allowance")
    assert (a["allowance"], a["used"], a["remaining"], a["authored"]) == (1000, 0, 1000, True)
    assert a["unit"] == "pages" and a["pages_used"] == 0 and a["cents_used"] == 0
    j1 = call(v, "medchron_job_submit", envelope=envelope())["job_id"]
    _deliver(v, j1, documents=60, pages=600, cents=4100, folder_id="f-1")
    a = call(v, "medchron_allowance")
    assert (a["used"], a["remaining"]) == (600, 400)
    assert (a["pages_used"], a["pages_remaining"], a["documents_used"], a["cents_used"]) == (600, 400, 60, 4100)
    r = call(v, "medchron_job_submit", envelope=envelope())
    assert r["accepted"] and r["allowance_remaining_pages"] == 400 and r["unit"] == "pages"
    _deliver(v, r["job_id"], documents=45, pages=450, cents=100)
    r = call(v, "medchron_job_submit", envelope=envelope())
    assert r["accepted"] is False
    assert "monthly page allowance is spent (1,050 of 1,000 pages in" in r["reason"]


def test_submit_refuses_when_no_allowance_is_authored(verbs, tmp_path):
    v, _, _ = verbs
    v.customer_yaml = str(tmp_path / "nope.yaml")
    r = call(v, "medchron_job_submit", envelope=envelope())
    assert r["accepted"] is False and "no monthly page allowance" in r["reason"]


def test_the_old_document_key_reads_as_unauthored_and_the_refusal_names_the_rename(verbs, tmp_path):
    """A seat that still carries only the pre-2026-09-09 document key submits
    NOTHING. Reading it as a page allowance would meter 2,000 pages as 2,000
    documents; falling back silently would leave the seat metered in the wrong
    unit with nobody told. The refusal names the key the firm must author."""
    v, _, _ = verbs
    p = tmp_path / "old.yaml"
    p.write_text(CUSTOMER_YAML.replace("chronology_package_page_allowance_per_month",
                                       "chronology_package_document_allowance_per_month"))
    assert allowance_from_customer_yaml(p) is None
    v.customer_yaml = str(p)
    r = call(v, "medchron_job_submit", envelope=envelope())
    assert r["accepted"] is False
    assert "chronology_package_page_allowance_per_month" in r["reason"]


# -- the one debit rule -------------------------------------------------------


def test_a_job_debits_the_month_whenever_it_recorded_cents(verbs):
    """The rule before 2026-09-09 counted DELIVERED jobs only, so a run that
    read pages and spent real money left no mark when it held or failed after
    the money had moved. Each case below is a separate falsifier of that."""
    v, _, _ = verbs
    held = call(v, "medchron_job_submit", envelope=envelope())["job_id"]
    call(v, "medchron_job_record", peer_uid=ROOT, job_id=held, state="running", fields={})
    call(v, "medchron_job_record", peer_uid=ROOT, job_id=held, state="held",
         fields={"pages": 120, "cents": 350, "reason": "per_job_cap_usd: ..."})
    assert call(v, "medchron_allowance")["used"] == 120        # a HELD job with cents debits

    failed = call(v, "medchron_job_submit", envelope=envelope())["job_id"]
    call(v, "medchron_job_record", peer_uid=ROOT, job_id=failed, state="running", fields={})
    call(v, "medchron_job_record", peer_uid=ROOT, job_id=failed, state="failed", fields={"pages": 30, "cents": 90})
    assert call(v, "medchron_allowance")["used"] == 150        # a FAILED job with cents debits

    free = call(v, "medchron_job_submit", envelope=envelope())["job_id"]
    call(v, "medchron_job_record", peer_uid=ROOT, job_id=free, state="running", fields={})
    call(v, "medchron_job_record", peer_uid=ROOT, job_id=free, state="held", fields={"pages": 9_000, "cents": 0})
    a = call(v, "medchron_allowance")
    assert a["used"] == 150 and a["cents_used"] == 440         # a ZERO-CENT hold does NOT debit


def test_exclude_job_id_leaves_out_exactly_that_row(verbs):
    """What a resume needs: the run must not be metered against the cents it
    already recorded, and must still be metered against every other job."""
    v, _, _ = verbs
    a = call(v, "medchron_job_submit", envelope=envelope())["job_id"]
    b = call(v, "medchron_job_submit", envelope=envelope())["job_id"]
    _deliver(v, a, pages=100, cents=500)
    _deliver(v, b, pages=200, cents=700)
    assert call(v, "medchron_allowance")["used"] == 300
    only_b = call(v, "medchron_allowance", exclude_job_id=a)
    assert only_b["used"] == 200 and only_b["cents_used"] == 700
    only_a = call(v, "medchron_allowance", exclude_job_id=b)
    assert only_a["used"] == 100 and only_a["cents_used"] == 500
    assert call(v, "medchron_allowance", exclude_job_id="not-a-job")["used"] == 300


def test_a_job_debits_the_month_it_was_created_in_not_the_month_its_cents_landed(verbs):
    """A run that starts on the 31st and finishes on the 1st debits the month
    it was CREATED in, on this surface and on the console's.

    A month-of-charge key was written first and reverted the same day: the
    column it needs is not in PROJECTION, and PROJECTION's shape is pinned by
    the overlay this release, so the console could never see it. The seat would
    have debited the new month while the console showed the old one -- the two
    surfaces disagreeing about the same month, which is the one thing this rule
    exists to prevent. `created_at` is a column both surfaces already have.
    """
    import sqlite3

    v, _, _ = verbs
    job = call(v, "medchron_job_submit", envelope=envelope())["job_id"]
    # Backdate the row to the last day of the previous month, then record the
    # cents now: creation in one month, charge in the next.
    conn = sqlite3.connect(v.ledger._db_path)
    try:
        conn.execute("UPDATE medchron_jobs SET created_at=? WHERE id=?", ("2026-08-31T23:50:00.000Z", job))
        conn.commit()
    finally:
        conn.close()
    _deliver(v, job, pages=420, cents=1500)

    august = v.ledger.allowance(1000, now="2026-08-31T23:59:00.000Z")
    september = v.ledger.allowance(1000, now="2026-09-01T00:10:00.000Z")
    assert (august["used"], august["cents_used"]) == (420, 1500)
    assert (september["used"], september["cents_used"]) == (0, 0)


# -- queue + ledger + audit --------------------------------------------------


def test_submit_writes_the_row_then_the_queue_file_with_the_remainder(verbs):
    v, ledger, queue = verbs
    r = call(v, "medchron_job_submit", envelope=envelope(request_ref="thread-9"))
    files = list(queue.glob("*.json"))
    assert [f.stem for f in files] == [r["job_id"]]
    q = json.loads(files[0].read_text())
    # Both keys, the same integer, for one release: the overlay's pinned tool
    # relays the document key by name and an old broker restarting in the
    # rollout window still reads it.
    assert q["job_id"] == r["job_id"]
    assert q["allowance_remaining_pages"] == 1000 == q["allowance_remaining_documents"]
    assert q["matter"]["number"] == "2026-PI-102" and q["request_ref"] == "thread-9"
    assert files[0].stat().st_mode & 0o777 == 0o640
    row = call(v, "medchron_job_status", job_id=r["job_id"])["job"]
    assert row["state"] == "submitted" and "matter_id" not in row     # the projection: counts and states only
    assert audit_types(ledger._db_path) == ["MEDCHRON_JOB_SUBMITTED"]


def test_transitions_are_monotonic_and_each_pins_its_audit_type(verbs):
    v, ledger, _ = verbs
    j = call(v, "medchron_job_submit", envelope=envelope())["job_id"]
    rec = lambda state, **fields: call(v, "medchron_job_record", peer_uid=ROOT, job_id=j, state=state, fields=fields)  # noqa: E731
    rec("running")
    rec("held", reason="seat paused")
    rec("running")
    with pytest.raises(ValueError):
        rec("submitted")
    row = rec("delivered", documents=12, pages=300, cents=1200, folder_id="f-9",
              delivery={"files": [{"name": "A.docx", "sha256": "ab", "bytes": 10}]})["job"]
    assert (row["documents"], row["pages"], row["cents"], row["folder_id"]) == (12, 300, 1200, "f-9")
    with pytest.raises(ValueError):
        rec("running")
    assert audit_types(ledger._db_path) == [
        "MEDCHRON_JOB_SUBMITTED", "MEDCHRON_JOB_RUNNING", "MEDCHRON_JOB_HELD", "MEDCHRON_JOB_RUNNING",
        "MEDCHRON_JOB_DELIVERED",
    ]
    with pytest.raises(ValueError):
        call(v, "medchron_job_record", peer_uid=ROOT, job_id="nope", state="running", fields={})
    with pytest.raises(ValueError):
        rec("failed", pages=-1)


def test_audit_rows_carry_counts_and_ids_never_the_envelope(verbs):
    import sqlite3

    v, ledger, _ = verbs
    j = call(v, "medchron_job_submit", envelope=envelope(injuries="a very private description"))["job_id"]
    conn = sqlite3.connect(ledger._db_path)
    try:
        (meta, matter_ref, actor) = conn.execute(
            "SELECT metadata, matter_ref, actor FROM audit_log ORDER BY rowid DESC LIMIT 1").fetchone()
    finally:
        conn.close()
    assert "private" not in meta and "Example" not in meta and json.loads(meta)["job_id"] == j
    assert matter_ref == "m-1" and actor == "workspace-broker"


def test_a_same_state_record_is_a_note_with_an_audit_row(verbs):
    v, ledger, _ = verbs
    j = call(v, "medchron_job_submit", envelope=envelope())["job_id"]
    call(v, "medchron_job_record", peer_uid=ROOT, job_id=j, state="running", fields={})
    call(v, "medchron_job_record", peer_uid=ROOT, job_id=j, state="delivered",
         fields={"documents": 3, "pages": 30, "cents": 100})
    # ss#2616: a lost deliver wake re-records the same state as a note.
    row = call(v, "medchron_job_record", peer_uid=ROOT, job_id=j, state="delivered",
               fields={"wake": {"wake_failed": True, "outcome": "status 404"}})["job"]
    assert row["state"] == "delivered" and row["documents"] == 3
    assert audit_types(ledger._db_path)[-2:] == ["MEDCHRON_JOB_DELIVERED", "MEDCHRON_JOB_DELIVERED"]
    import sqlite3

    conn = sqlite3.connect(ledger._db_path)
    try:
        meta = conn.execute("SELECT metadata FROM audit_log ORDER BY rowid DESC LIMIT 1").fetchone()[0]
    finally:
        conn.close()
    assert json.loads(meta)["wake"] == {"wake_failed": True, "outcome": "status 404"}
    # Real transitions stay monotonic.
    with pytest.raises(ValueError):
        call(v, "medchron_job_record", peer_uid=ROOT, job_id=j, state="running", fields={})


def test_submit_sanitizes_selection_to_known_keys(verbs):
    v, _, queue = verbs
    r = call(v, "medchron_job_submit",
             envelope=envelope(selection={"include_file_ids": ["f-1", "f-2"], "sneaky": ["x"]}))
    assert r["accepted"]
    q = json.loads(next(queue.glob("*.json")).read_text())
    assert q["selection"] == {"include_file_ids": ["f-1", "f-2"]}
