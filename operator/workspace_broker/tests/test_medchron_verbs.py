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

from workspace_broker.audit_ledger import LedgerWriter
from workspace_broker.medchron_ledger import (
    MedchronLedger,
    allowance_from_customer_yaml,
    validate_envelope,
)
from workspace_broker.medchron_verbs import MedchronVerbs, medchron_dispatch

GATEWAY_PID = 4242
AGENT_UID = 10000
ROOT = 0

CUSTOMER_YAML = """
scope:
  admins:
    - admin@example.test
    - Other.Admin@Example.test
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
    v = MedchronVerbs(
        MedchronLedger(db, queue),
        customer_yaml=str(tmp_path / "customer.yaml"),
        customer_slug="example",
        audit_append=ledger.append,
        gateway_pid=GATEWAY_PID,
        resolve_agent_uid=lambda: AGENT_UID,
    )
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
    # A different matter, because the same one twice is now a duplicate refusal
    # and this test is about WHO may call, not about how often.
    assert call(
        v,
        "medchron_job_submit",
        peer_pid=999,
        peer_uid=ROOT,
        envelope=envelope(matter={"id": "m-root", "number": "2026-PI-777", "title": "Root v. Root"}),
    )["accepted"]


def test_record_is_root_only_and_list_is_agent_or_root(verbs):
    v, _, _ = verbs
    job_id = call(v, "medchron_job_submit", envelope=envelope())["job_id"]
    with pytest.raises(PermissionError):
        call(v, "medchron_job_record", job_id=job_id, state="running", fields={})
    with pytest.raises(PermissionError):
        call(v, "medchron_job_record", peer_pid=999, peer_uid=AGENT_UID, job_id=job_id, state="running", fields={})
    assert (
        call(v, "medchron_job_record", peer_pid=1, peer_uid=ROOT, job_id=job_id, state="running", fields={})["job"][
            "state"
        ]
        == "running"
    )
    with pytest.raises(PermissionError):
        call(v, "medchron_job_list", peer_pid=999, peer_uid=12345)
    assert [j["id"] for j in call(v, "medchron_job_list", peer_pid=999, peer_uid=AGENT_UID)["jobs"]] == [job_id]


def test_a_broker_without_the_ledger_refuses(tmp_path):
    with pytest.raises(ValueError):
        medchron_dispatch(None, "medchron_allowance", {}, GATEWAY_PID, AGENT_UID)
    v = MedchronVerbs(
        None,
        customer_yaml="/nonexistent",
        customer_slug="x",
        audit_append=lambda r: None,
        gateway_pid=GATEWAY_PID,
        resolve_agent_uid=lambda: AGENT_UID,
    )
    with pytest.raises(ValueError):
        call(v, "medchron_allowance")


# -- envelope ----------------------------------------------------------------


@pytest.mark.parametrize(
    "bad",
    [
        {"matter": {"id": "", "number": "x"}},
        {"units": []},
        {"units": [{"client_name": "A", "surname": "B", "dob": "1980-01-02"}]},
        {
            "units": [
                {"client_name": "A", "surname": "B", "dob": "01/02/1980"},
                {"client_name": "C", "surname": "D", "dob": "01/02/1981"},
            ]
        },  # joint without folder_prefix
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


def test_allowance_counts_the_cycles_pages_and_submit_stops_at_the_crossing(verbs):
    v, _, _ = verbs
    a = call(v, "medchron_allowance")
    assert (a["allowance"], a["used"], a["remaining"], a["authored"]) == (1000, 0, 1000, True)
    assert a["unit"] == "pages" and a["pages_used"] == 0 and a["cents_used"] == 0
    j1 = call(v, "medchron_job_submit", envelope=envelope())["job_id"]
    _deliver(v, j1, documents=60, pages=600, cents=4100, folder_id="f-1")
    a = call(v, "medchron_allowance")
    assert (a["used"], a["remaining"]) == (600, 400)
    assert (a["pages_used"], a["pages_remaining"], a["documents_used"], a["cents_used"]) == (600, 400, 60, 4100)
    # Distinct matters from here: since 2026-09-16 the same work debits once
    # per cycle, so a second delivery of the FIRST envelope would not add to
    # `used`. That case has its own tests below; this one is about the sum.
    second = envelope(matter={"id": "m-2", "number": "2026-PI-103", "title": "Second v. Second"})
    third = envelope(matter={"id": "m-3", "number": "2026-PI-104", "title": "Third v. Third"})
    r = call(v, "medchron_job_submit", envelope=second)
    assert r["accepted"] and r["allowance_remaining_pages"] == 400 and r["unit"] == "pages"
    _deliver(v, r["job_id"], documents=45, pages=450, cents=100)
    r = call(v, "medchron_job_submit", envelope=third)
    assert r["accepted"] is False
    assert "page allowance is spent (1,050 of 1,000 pages in" in r["reason"]


def test_submit_refuses_when_no_allowance_is_authored(verbs, tmp_path):
    v, _, _ = verbs
    v.customer_yaml = str(tmp_path / "nope.yaml")
    r = call(v, "medchron_job_submit", envelope=envelope())
    assert r["accepted"] is False and "no page allowance" in r["reason"]


def test_the_old_document_key_reads_as_unauthored_and_the_refusal_names_the_rename(verbs, tmp_path):
    """A seat that still carries only the pre-2026-09-09 document key submits
    NOTHING. Reading it as a page allowance would meter 2,000 pages as 2,000
    documents; falling back silently would leave the seat metered in the wrong
    unit with nobody told. The refusal names the key the firm must author."""
    v, _, _ = verbs
    p = tmp_path / "old.yaml"
    p.write_text(
        CUSTOMER_YAML.replace(
            "chronology_package_page_allowance_per_month", "chronology_package_document_allowance_per_month"
        )
    )
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

    def other(n):
        """A distinct matter per case: this test is about which STATES debit,
        and the same matter three times is now a duplicate refusal."""
        return envelope(matter={"id": f"m-{n}", "number": f"2026-PI-{n}", "title": "Example v. Example"})

    held = call(v, "medchron_job_submit", envelope=other(201))["job_id"]
    call(v, "medchron_job_record", peer_uid=ROOT, job_id=held, state="running", fields={})
    call(
        v,
        "medchron_job_record",
        peer_uid=ROOT,
        job_id=held,
        state="held",
        fields={"pages": 120, "cents": 350, "reason": "per_job_cap_usd: ..."},
    )
    assert call(v, "medchron_allowance")["used"] == 120  # a HELD job with cents debits

    failed = call(v, "medchron_job_submit", envelope=other(202))["job_id"]
    call(v, "medchron_job_record", peer_uid=ROOT, job_id=failed, state="running", fields={})
    call(v, "medchron_job_record", peer_uid=ROOT, job_id=failed, state="failed", fields={"pages": 30, "cents": 90})
    assert call(v, "medchron_allowance")["used"] == 150  # a FAILED job with cents debits

    free = call(v, "medchron_job_submit", envelope=other(203))["job_id"]
    call(v, "medchron_job_record", peer_uid=ROOT, job_id=free, state="running", fields={})
    call(v, "medchron_job_record", peer_uid=ROOT, job_id=free, state="held", fields={"pages": 9_000, "cents": 0})
    a = call(v, "medchron_allowance")
    assert a["used"] == 150 and a["cents_used"] == 440  # a ZERO-CENT hold does NOT debit


def test_exclude_job_id_leaves_out_exactly_that_row(verbs):
    """What a resume needs: the run must not be metered against the cents it
    already recorded, and must still be metered against every other job."""
    v, _, _ = verbs
    # Two DIFFERENT matters: one envelope submitted twice is a duplicate and
    # the broker now refuses the second, which is a separate test below.
    a = call(v, "medchron_job_submit", envelope=envelope())["job_id"]
    b = call(
        v,
        "medchron_job_submit",
        envelope=envelope(matter={"id": "m-2", "number": "2026-PI-103", "title": "Other v. Other"}),
    )["job_id"]
    _deliver(v, a, pages=100, cents=500)
    _deliver(v, b, pages=200, cents=700)
    assert call(v, "medchron_allowance")["used"] == 300
    only_b = call(v, "medchron_allowance", exclude_job_id=a)
    assert only_b["used"] == 200 and only_b["cents_used"] == 700
    only_a = call(v, "medchron_allowance", exclude_job_id=b)
    assert only_a["used"] == 100 and only_a["cents_used"] == 500
    assert call(v, "medchron_allowance", exclude_job_id="not-a-job")["used"] == 300


def test_exclude_job_id_leaves_out_that_jobs_whole_work_group(verbs):
    """A resume of attempt two of a chronology is metered against OTHER work
    only. Excluding one id would leave attempt one's pages in the figure and
    refuse the resume for pages it is itself re-reading. Falsifier: the old
    `id <> ?` form returns 300 here, not 200."""
    v, _, _ = verbs
    first = call(v, "medchron_job_submit", envelope=envelope())["job_id"]
    call(v, "medchron_job_record", peer_uid=ROOT, job_id=first, state="running", fields={})
    call(v, "medchron_job_record", peer_uid=ROOT, job_id=first, state="failed", fields={"pages": 100, "cents": 500})
    second = call(v, "medchron_job_submit", envelope=envelope())["job_id"]  # same work, relaunched
    other = call(
        v,
        "medchron_job_submit",
        envelope=envelope(matter={"id": "m-2", "number": "2026-PI-103", "title": "Other v. Other"}),
    )["job_id"]
    _deliver(v, other, pages=200, cents=700)
    call(v, "medchron_job_record", peer_uid=ROOT, job_id=second, state="running", fields={})
    call(v, "medchron_job_record", peer_uid=ROOT, job_id=second, state="held", fields={"pages": 100, "cents": 40})
    without_second = call(v, "medchron_allowance", exclude_job_id=second)
    assert without_second["used"] == 200, "attempt one's pages are the same work and must go with attempt two"
    assert without_second["cents_used"] == 700


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


def test_the_cycle_window_decides_which_rows_are_debited(verbs):
    """The behaviour the whole change exists for: the same ledger row counts or
    does not, depending on the firm's billing cycle rather than the calendar.

    A job created 2026-09-10 is INSIDE September by the calendar, and OUTSIDE a
    cycle anchored on the 15th (which runs 08-15 to 09-15 at that moment, and
    09-15 to 10-15 after it). Falsifier: revert the debit predicate to
    `substr(created_at,1,7)` and the anchored reads below return 420, because a
    prefix match cannot express a window that does not start on the 1st.
    """
    import sqlite3

    v, _, _ = verbs
    job = call(v, "medchron_job_submit", envelope=envelope())["job_id"]
    conn = sqlite3.connect(v.ledger._db_path)
    try:
        conn.execute("UPDATE medchron_jobs SET created_at=? WHERE id=?", ("2026-09-10T12:00:00.000Z", job))
        conn.commit()
    finally:
        conn.close()
    _deliver(v, job, pages=420, cents=1500)

    now = "2026-09-20T12:00:00.000Z"
    calendar = v.ledger.allowance(1000, now=now)
    assert (calendar["used"], calendar["month"]) == (420, "2026-09")

    anchored = v.ledger.allowance(1000, now=now, anchor_day=15)
    assert anchored["used"] == 0, "a 09-10 row is not in the cycle that began 09-15"
    assert anchored["month"] == "the cycle ending Oct 14"
    assert (anchored["cycle_start"], anchored["cycle_end"]) == (
        "2026-09-15T00:00:00.000Z",
        "2026-10-15T00:00:00.000Z",
    )

    # The cycle BEFORE that one does hold it.
    prior = v.ledger.allowance(1000, now="2026-09-14T12:00:00.000Z", anchor_day=15)
    assert prior["used"] == 420 and prior["cycle_start"] == "2026-08-15T00:00:00.000Z"


def test_an_unreadable_anchor_refuses_and_names_the_key(verbs, tmp_path):
    """Authored-but-invalid must never be quietly demoted to the calendar month:
    a firm metered on a window it did not author is the harm."""
    yaml_path = tmp_path / "customer.yaml"
    yaml_path.write_text(
        "personas:\n"
        "  - skills:\n"
        "      - name: medical-chronology-maintainer\n"
        "        settings:\n"
        "          chronology_package_page_allowance_per_month: 1000\n"
        "          chronology_package_cycle_anchor_day: '15'\n",
        encoding="utf-8",
    )
    v, _, _ = verbs
    v.customer_yaml = str(yaml_path)
    r = call(v, "medchron_job_submit", envelope=envelope())
    assert r["accepted"] is False
    assert "chronology_package_cycle_anchor_day" in r["reason"]
    a = call(v, "medchron_allowance")
    assert a["invalid"] is True and a["authored"] is False


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
    assert row["state"] == "submitted" and "matter_id" not in row  # the projection: counts and states only
    assert audit_types(ledger._db_path) == ["MEDCHRON_JOB_SUBMITTED"]


def test_transitions_are_monotonic_and_each_pins_its_audit_type(verbs):
    v, ledger, _ = verbs
    j = call(v, "medchron_job_submit", envelope=envelope())["job_id"]
    rec = lambda state, **fields: call(v, "medchron_job_record", peer_uid=ROOT, job_id=j, state=state, fields=fields)  # noqa: E731 - test-local shorthand for the verb call; a def adds only a name
    rec("running")
    rec("held", reason="seat paused")
    rec("running")
    with pytest.raises(ValueError):
        rec("submitted")
    row = rec(
        "delivered",
        documents=12,
        pages=300,
        cents=1200,
        folder_id="f-9",
        delivery={"files": [{"name": "A.docx", "sha256": "ab", "bytes": 10}]},
    )["job"]
    assert (row["documents"], row["pages"], row["cents"], row["folder_id"]) == (12, 300, 1200, "f-9")
    with pytest.raises(ValueError):
        rec("running")
    assert audit_types(ledger._db_path) == [
        "MEDCHRON_JOB_SUBMITTED",
        "MEDCHRON_JOB_RUNNING",
        "MEDCHRON_JOB_HELD",
        "MEDCHRON_JOB_RUNNING",
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
            "SELECT metadata, matter_ref, actor FROM audit_log ORDER BY rowid DESC LIMIT 1"
        ).fetchone()
    finally:
        conn.close()
    assert "private" not in meta and "Example" not in meta and json.loads(meta)["job_id"] == j
    assert matter_ref == "m-1" and actor == "workspace-broker"


def test_a_same_state_record_is_a_note_with_an_audit_row(verbs):
    v, ledger, _ = verbs
    j = call(v, "medchron_job_submit", envelope=envelope())["job_id"]
    call(v, "medchron_job_record", peer_uid=ROOT, job_id=j, state="running", fields={})
    call(
        v,
        "medchron_job_record",
        peer_uid=ROOT,
        job_id=j,
        state="delivered",
        fields={"documents": 3, "pages": 30, "cents": 100},
    )
    # ss#2616: a lost deliver wake re-records the same state as a note.
    row = call(
        v,
        "medchron_job_record",
        peer_uid=ROOT,
        job_id=j,
        state="delivered",
        fields={"wake": {"wake_failed": True, "outcome": "status 404"}},
    )["job"]
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
    r = call(
        v, "medchron_job_submit", envelope=envelope(selection={"include_file_ids": ["f-1", "f-2"], "sneaky": ["x"]})
    )
    assert r["accepted"]
    q = json.loads(next(queue.glob("*.json")).read_text())
    assert q["selection"] == {"include_file_ids": ["f-1", "f-2"]}


# -- who may spend the allowance, and how many times --------------------------
#
# These cover the two controls the seat CANNOT enforce for itself. The firm's
# initiation authority reaches the agent as a prompt injection, not a gate, so
# "only a Named Administrator may ask" holds here or nowhere; and nothing
# de-duplicated a submission before this release, so an administrator who asked
# twice bought the same package twice.


def test_a_non_administrator_cannot_buy_a_chronology_package(verbs):
    v, _, queue = verbs
    r = call(v, "medchron_job_submit", envelope=envelope(requested_by="paralegal@example.test"))
    assert r["accepted"] is False
    assert "Named Administrator" in r["reason"]
    assert list(queue.glob("*.json")) == [], "a refused submission must queue nothing"


def test_an_administrator_is_matched_case_insensitively_inside_prose(verbs):
    v, _, _ = verbs
    r = call(v, "medchron_job_submit", envelope=envelope(requested_by="Other Admin <OTHER.ADMIN@example.TEST>"))
    assert r["accepted"] is True


def test_a_requester_with_no_address_is_refused(verbs):
    """The field is agent-composed prose. A display name is not an identity, and
    the broker will not queue paid work it cannot attribute."""
    v, _, _ = verbs
    r = call(v, "medchron_job_submit", envelope=envelope(requested_by="Christa, Example Firm"))
    assert r["accepted"] is False and "email address" in r["reason"]


def _yaml_with_admins(admins_block: str) -> str:
    """The fixture config with only the admins half varied, so a refusal about
    the admin list cannot be the allowance check firing first."""
    head, _, tail = CUSTOMER_YAML.partition("personas:")
    del head
    return admins_block + "personas:" + tail


def test_an_unreadable_admin_list_refuses_rather_than_admitting_everyone(verbs, tmp_path):
    v, _, _ = verbs
    (tmp_path / "customer.yaml").write_text(_yaml_with_admins(""))  # no scope.admins at all
    r = call(v, "medchron_job_submit", envelope=envelope())
    assert r["accepted"] is False and "scope.admins" in r["reason"]


def test_an_empty_admin_list_refuses_and_says_so_distinctly(verbs, tmp_path):
    v, _, _ = verbs
    (tmp_path / "customer.yaml").write_text(_yaml_with_admins("scope:\n  admins: []\n"))
    r = call(v, "medchron_job_submit", envelope=envelope())
    assert r["accepted"] is False and "empty" in r["reason"]


def test_the_same_package_asked_for_twice_is_refused_and_names_the_twin(verbs):
    v, _, queue = verbs
    first = call(v, "medchron_job_submit", envelope=envelope())
    assert first["accepted"] is True
    second = call(v, "medchron_job_submit", envelope=envelope())
    assert second["accepted"] is False
    assert second["job_id"] == first["job_id"]
    assert second["job_state"] == "submitted"
    assert first["job_id"] in second["reason"]
    assert len(list(queue.glob("*.json"))) == 1, "the duplicate must not queue a second job"


@pytest.mark.parametrize(
    ("state", "phrase"),
    [
        ("submitted", "has not started yet"),
        ("running", "already running"),
        ("held", "waiting on a decision"),
    ],
)
def test_the_refusal_says_what_is_true_of_THAT_job(verbs, state, phrase):
    """'Already running' about a job that stopped hours ago for a spend decision
    is the kind of confident wrong sentence somebody then relays to a client."""
    v, _, _ = verbs
    first = call(v, "medchron_job_submit", envelope=envelope())["job_id"]
    if state != "submitted":
        call(v, "medchron_job_record", peer_uid=ROOT, job_id=first, state="running", fields={})
    if state == "held":
        call(v, "medchron_job_record", peer_uid=ROOT, job_id=first, state="held", fields={"reason": "cap"})
    again = call(v, "medchron_job_submit", envelope=envelope())
    assert again["accepted"] is False and phrase in again["reason"]


def test_provenance_does_not_make_it_a_different_job(verbs):
    """The duplicate key is the WORK, not who asked or how they worded it. An
    administrator who asks again in the same thread produces a different
    request_ref, and that must not read as a new package."""
    v, _, _ = verbs
    first = call(v, "medchron_job_submit", envelope=envelope(request_ref="first ask"))["job_id"]
    again = call(
        v,
        "medchron_job_submit",
        envelope=envelope(requested_by="Other.Admin@Example.test", request_ref="following up"),
    )
    assert again["accepted"] is False and again["job_id"] == first


def test_a_finished_package_may_be_rebuilt(verbs):
    """Terminal jobs are not duplicates: a delivered package the firm wants
    rebuilt, or a failed one worth retrying, is a legitimate second ask. And
    since 2026-09-16 the second row is the same WORK, so the cycle's pages
    count it once (cents still sum: both launches spent money)."""
    v, _, _ = verbs
    first = call(v, "medchron_job_submit", envelope=envelope())["job_id"]
    _deliver(v, first, pages=10, cents=100)
    again = call(v, "medchron_job_submit", envelope=envelope())
    assert again["accepted"] is True and again["job_id"] != first
    _deliver(v, again["job_id"], pages=10, cents=100)
    a = call(v, "medchron_allowance")
    assert a["used"] == 10, "the rule before 2026-09-16 read 20 here"
    assert a["cents_used"] == 200


def test_a_relaunch_of_the_same_work_debits_its_pages_once(verbs):
    """The live incident (first client seat, matter 200454): one 3,568-page
    chronology, three `cents > 0` rows, month read 10,704. Falsifier: the old
    SUM rule returns 7,136 here."""
    v, _, _ = verbs
    first = call(v, "medchron_job_submit", envelope=envelope())["job_id"]
    call(v, "medchron_job_record", peer_uid=ROOT, job_id=first, state="running", fields={})
    # 356 pages against the fixture's 1,000-page allowance (the live figure was
    # 3,568 of 15,000; same ratio, so the relaunch clears the pre-flight).
    call(v, "medchron_job_record", peer_uid=ROOT, job_id=first, state="failed", fields={"pages": 356, "cents": 900})
    relaunch = call(v, "medchron_job_submit", envelope=envelope())
    assert relaunch["accepted"] is True and relaunch["job_id"] != first
    _deliver(v, relaunch["job_id"], pages=356, cents=900)
    a = call(v, "medchron_allowance")
    assert a["used"] == 356 and a["pages_used"] == 356, "the SUM rule read 712 here"
    assert a["cents_used"] == 1800
    listed = call(v, "medchron_job_list")
    assert {j["id"] for j in listed["jobs"]} >= {first, relaunch["job_id"]}, "both rows stay in the ledger"


def test_the_larger_attempt_wins_in_a_group(verbs):
    """An attempt that died partway read fewer pages than the one that
    delivered; the work read the file once and the largest attempt is that
    read. Falsifier: a MIN or a first-row rule returns 1,200."""
    v, _, _ = verbs
    first = call(v, "medchron_job_submit", envelope=envelope())["job_id"]
    call(v, "medchron_job_record", peer_uid=ROOT, job_id=first, state="running", fields={})
    call(v, "medchron_job_record", peer_uid=ROOT, job_id=first, state="failed", fields={"pages": 120, "cents": 300})
    second = call(v, "medchron_job_submit", envelope=envelope())["job_id"]
    _deliver(v, second, pages=356, cents=900)
    assert call(v, "medchron_allowance")["used"] == 356


def test_an_update_with_a_different_selection_is_its_own_debit(verbs):
    """An UPDATE names the new document ids in `selection`, which changes the
    work digest, so it debits exactly its own pages on top of the run's.
    Guards over-grouping: a rule that grouped by matter would return 600."""
    v, _, _ = verbs
    run = call(v, "medchron_job_submit", envelope=envelope())["job_id"]
    _deliver(v, run, pages=600, cents=900)
    update = call(
        v,
        "medchron_job_submit",
        envelope=envelope(selection={"include_file_ids": ["doc-new-1", "doc-new-2"]}),
    )
    assert update["accepted"] is True
    _deliver(v, update["job_id"], pages=40, cents=20)
    assert call(v, "medchron_allowance")["used"] == 640


def test_a_row_without_a_work_digest_is_its_own_group(verbs):
    """Rows written before the digest column exist on live seats. They are
    never guessed into another row's group: two twins with one digest blanked
    out sum, exactly as before 2026-09-16."""
    v, _, _ = verbs
    first = call(v, "medchron_job_submit", envelope=envelope())["job_id"]
    _deliver(v, first, pages=100, cents=50)
    second = call(v, "medchron_job_submit", envelope=envelope())["job_id"]
    _deliver(v, second, pages=100, cents=50)
    assert call(v, "medchron_allowance")["used"] == 100
    import sqlite3

    conn = sqlite3.connect(v._db._db_path)
    conn.execute("UPDATE medchron_jobs SET work_digest = NULL WHERE id = ?", (first,))
    conn.commit()
    conn.close()
    assert call(v, "medchron_allowance")["used"] == 200


def test_units_order_does_not_change_the_work_digest():
    """A joint matter re-asked with the clients listed in a different order
    is the same chronology. Falsifier: hashing the list as given differs."""
    from workspace_broker.medchron_ledger import work_digest

    a = {"client_name": "Alpha Example", "surname": "Example", "dob": "01/02/1980", "folder_prefix": "/ALPHA"}
    b = {"client_name": "Beta Other", "surname": "Other", "dob": "03/04/1982", "folder_prefix": "/BETA"}
    e1 = validate_envelope(envelope(units=[a, b]))
    e2 = validate_envelope(envelope(units=[b, a]))
    assert work_digest(e1) == work_digest(e2)
    assert work_digest(e1) != work_digest(validate_envelope(envelope(units=[a])))


def test_a_different_matter_is_not_a_duplicate(verbs):
    v, _, _ = verbs
    call(v, "medchron_job_submit", envelope=envelope())
    other = call(
        v,
        "medchron_job_submit",
        envelope=envelope(matter={"id": "m-9", "number": "2026-PI-999", "title": "Other v. Other"}),
    )
    assert other["accepted"] is True


# ---- the covered/uncovered record (2026-09-17) --------------------------------
def _submitted(v):
    """One accepted job id, the way the other tests get one."""
    resp = call(v, "medchron_job_submit", peer_uid=AGENT_UID, envelope=envelope())
    assert resp["accepted"], resp
    return resp["job_id"]


def test_a_delivery_records_what_it_covered_and_what_it_did_not(verbs):
    """Routine 11's UPDATE reads only what a delivery did not cover (agreement
    Exhibit A), so the delivery has to write that down. Before 2026-09-17 no
    layer did, and an update either re-read the whole file or guessed at a delta."""
    v, _ledger, _queue = verbs
    job_id = _submitted(v)
    call(v, "medchron_job_record", peer_uid=ROOT, job_id=job_id, state="running", fields={})
    resp = call(
        v,
        "medchron_job_record",
        peer_uid=ROOT,
        job_id=job_id,
        state="delivered",
        fields={
            "documents": 4,
            "pages": 40,
            "cents": 100,
            "covered": {"covered": ["b", "a"], "uncovered": ["c"], "pulled": 3},
        },
    )
    assert resp["ok"], resp

    job = call(v, "medchron_job_status", job_id=job_id)["job"]
    assert job["covered_document_ids"] == ["a", "b"]
    assert job["uncovered_document_ids"] == ["c"]


def test_a_job_with_no_record_reads_as_unknown_not_as_nothing_covered(verbs):
    """Nothing-was-covered and nobody-wrote-it-down send an update in opposite
    directions, so an absent record stays absent rather than reading empty."""
    v, _ledger, _queue = verbs
    job_id = _submitted(v)
    job = call(v, "medchron_job_status", job_id=job_id)["job"]
    assert job["covered_document_ids"] is None
    assert job["uncovered_document_ids"] is None


def test_a_coverage_payload_that_does_not_add_up_is_refused(verbs):
    """The set arithmetic is the falsifier: an overlap lets an update decide
    either way, and a total that disagrees with what the run pulled means a
    stage dropped rows between the coverage gate and this record."""
    v, _ledger, _queue = verbs
    job_id = _submitted(v)
    call(v, "medchron_job_record", peer_uid=ROOT, job_id=job_id, state="running", fields={})

    for bad, why in [
        ({"covered": ["a"], "uncovered": ["a"]}, "share"),
        ({"covered": ["a", "a"], "uncovered": []}, "repeats"),
        ({"covered": ["a"], "uncovered": ["b"], "pulled": 5}, "pulled"),
        ({"covered": "a", "uncovered": []}, "list"),
        ({"covered": [""], "uncovered": []}, "document id"),
    ]:
        with pytest.raises(ValueError) as exc:
            call(
                v,
                "medchron_job_record",
                peer_uid=ROOT,
                job_id=job_id,
                state="delivered",
                fields={"covered": bad},
            )
        assert why in str(exc.value), (bad, str(exc.value))

    assert call(v, "medchron_job_status", job_id=job_id)["job"]["covered_document_ids"] is None


# ---- a matter's cumulative coverage, and the backfill (ss#2834) ---------------
def _backfill(v, matter_id, number, covered, uncovered, delivered_at, source="backfill"):
    return call(
        v,
        "medchron_backfill_covered",
        peer_uid=ROOT,
        matter_id=matter_id,
        matter_number=number,
        delivered_at=delivered_at,
        source=source,
        covered={"covered": covered, "uncovered": uncovered},
    )


def test_a_matters_coverage_is_cumulative_so_a_second_update_does_not_reread_the_first(verbs):
    """The reason this is `delivered_coverage` and not `latest_delivered`.

    A delivery's record covers only its own units; nothing unions it with the
    delivery before it. Read the newest row alone and the SECOND update is told
    the FIRST chronology's documents were never covered -- so it re-reads the
    whole matter, thousands of pages against the cycle allowance, on every
    matter, every time. This fails on a newest-row read: `a` and `b` are only in
    the original delivery's covered set.
    """
    v, _ledger, _queue = verbs
    mid = "m-cumulative"
    _backfill(v, mid, "900011", ["a", "b"], ["x"], "2026-08-28")
    _backfill(v, mid, "900011", ["c"], ["y"], "2026-09-10", source="update-1")

    matter = call(v, "medchron_job_status", matter_id=mid)["matter"]
    assert matter["covered_document_ids"] == ["a", "b", "c"]
    assert matter["uncovered_document_ids"] == ["x", "y"]
    assert matter["deliveries"] == 2


def test_uncovered_wins_the_union_across_deliveries(verbs):
    """A document one delivery cited and another could not use is re-read.

    Re-reading costs pages. A record dropped from a filed litigation chronology
    cannot be recovered, so the union breaks toward reading again.
    """
    v, _ledger, _queue = verbs
    mid = "m-conflict"
    _backfill(v, mid, "900022", ["shared"], [], "2026-08-27")
    _backfill(v, mid, "900022", [], ["shared"], "2026-09-01", source="update-1")

    matter = call(v, "medchron_job_status", matter_id=mid)["matter"]
    assert matter["covered_document_ids"] == []
    assert matter["uncovered_document_ids"] == ["shared"]


def test_failed_rows_contribute_nothing_to_a_matters_coverage(verbs):
    """Seeded with the shape the live A&P seat actually carries: every row on the
    matter failed or held, none delivered. A job that failed covered nothing, and
    `failed` has no transition out of it, so filtering by state is both the only
    cleanup available and the correct one."""
    v, _ledger, _queue = verbs
    job_id = _submitted(v)
    call(v, "medchron_job_record", peer_uid=ROOT, job_id=job_id, state="running", fields={})
    call(
        v,
        "medchron_job_record",
        peer_uid=ROOT,
        job_id=job_id,
        state="failed",
        fields={"covered": {"covered": ["ghost"], "uncovered": []}},
    )
    matter_id = envelope()["matter"]["id"]
    assert call(v, "medchron_job_status", matter_id=matter_id)["matter"] is None

    _backfill(v, matter_id, "900033", ["real"], [], "2026-09-16")
    matter = call(v, "medchron_job_status", matter_id=matter_id)["matter"]
    assert matter["covered_document_ids"] == ["real"]
    assert matter["deliveries"] == 1


def test_a_backfill_writes_no_queue_file_so_it_cannot_launch_a_paid_run(verbs):
    """The expensive falsifier.

    `submit()` writes a queue envelope and the runner daemon claims what it finds
    there, so registering thirteen delivered chronologies through the submit path
    would launch thirteen real, paid chronology runs. This asserts the backfill
    leaves the queue directory exactly as it found it.
    """
    v, _ledger, queue = verbs
    before = sorted(p.name for p in queue.glob("*.json")) if queue.is_dir() else []
    _backfill(v, "m-noqueue", "900044", ["a"], [], "2026-08-25")
    after = sorted(p.name for p in queue.glob("*.json")) if queue.is_dir() else []
    assert after == before


def test_a_backfilled_row_never_debits_the_firms_allowance(verbs):
    """History being written down is not billable work. Two independent reasons
    it cannot move the meter, and this asserts the outcome of both: the row
    carries zero cents (`_DEBITS_SQL` counts only `cents > 0`) and its
    `created_at` is the real delivery date, outside the current cycle."""
    v, _ledger, _queue = verbs
    before = call(v, "medchron_allowance")
    _backfill(v, "m-meter", "900055", ["a", "b", "c"], ["d"], "2026-08-27")
    after = call(v, "medchron_allowance")
    assert after["used"] == before["used"]
    assert after["remaining"] == before["remaining"]


def test_a_backfill_is_idempotent_so_a_partial_run_is_safe_to_repeat(verbs):
    """A re-run corrects the record rather than leaving two rows claiming to be
    the same delivery."""
    v, _ledger, _queue = verbs
    mid = "m-idem"
    first = _backfill(v, mid, "900066", ["a"], [], "2026-08-27")["job"]["id"]
    second = _backfill(v, mid, "900066", ["a", "b"], [], "2026-08-27")["job"]["id"]
    assert first == second
    matter = call(v, "medchron_job_status", matter_id=mid)["matter"]
    assert matter["deliveries"] == 1
    assert matter["covered_document_ids"] == ["a", "b"]


def test_status_refuses_a_job_id_and_a_matter_id_together(verbs):
    """Resolving by precedence would hand an update a delta measured against
    something other than what it asked for, silently."""
    v, _ledger, _queue = verbs
    with pytest.raises(ValueError) as exc:
        call(v, "medchron_job_status", job_id="01J", matter_id="m")
    assert "not both" in str(exc.value)


def test_the_list_paths_return_counts_not_the_id_arrays(verbs):
    """A matter carries hundreds of document ids and `project()` returns them
    three times over, so twenty rows of them is hundreds of kilobytes in a
    client-facing turn on a 1 vCPU / 1GB seat. `None` survives as `None`: an
    absent record and an empty one send an update in opposite directions."""
    v, _ledger, _queue = verbs
    _backfill(v, "m-list", "900077", ["a", "b"], ["c"], "2026-08-26")
    bare = _submitted(v)

    for resp in (call(v, "medchron_job_status"), call(v, "medchron_job_list", peer_uid=ROOT)):
        rows = {r["id"]: r for r in resp["jobs"]}
        filled = next(r for r in rows.values() if r["matter_number"] == "900077")
        assert filled["covered_count"] == 2
        assert filled["uncovered_count"] == 1
        for gone in ("covered_json", "covered_document_ids", "uncovered_document_ids"):
            assert gone not in filled
        assert rows[bare]["covered_count"] is None


def test_a_backfill_needs_a_real_delivery_date_and_a_coverage_object(verbs):
    """The date is load-bearing, not decoration: it is what keeps the row out of
    the current billing cycle and the ledger chronologically honest."""
    v, _ledger, _queue = verbs
    for kwargs, why in [
        ({"delivered_at": "", "covered": {"covered": [], "uncovered": []}}, "delivered_at"),
        ({"delivered_at": "August 27", "covered": {"covered": [], "uncovered": []}}, "YYYY-MM-DD"),
        ({"delivered_at": "2026-08-27", "covered": "nope"}, "covered object"),
    ]:
        with pytest.raises(ValueError) as exc:
            call(
                v,
                "medchron_backfill_covered",
                peer_uid=ROOT,
                matter_id="m-bad",
                matter_number="1",
                source="backfill",
                **kwargs,
            )
        assert why in str(exc.value), (kwargs, str(exc.value))


def test_a_backfill_is_root_only_and_writes_its_own_audit_type(verbs):
    """No agent tool and no agent uid: an update skips whatever the record says
    was covered, so an agent-reachable path is a path a client conversation could
    use to make a chronology omit medical records. The audit row is the
    governance trail the 2026-09-01 raw-sqlite incident did not leave."""
    v, ledger, _queue = verbs
    with pytest.raises(PermissionError):
        call(
            v,
            "medchron_backfill_covered",
            peer_uid=AGENT_UID,
            matter_id="m-gate",
            matter_number="1",
            delivered_at="2026-08-27",
            source="backfill",
            covered={"covered": [], "uncovered": []},
        )

    _backfill(v, "m-gate", "900088", ["a"], ["b"], "2026-08-27")
    assert "MEDCHRON_COVERAGE_BACKFILLED" in audit_types(v._db._db_path)
