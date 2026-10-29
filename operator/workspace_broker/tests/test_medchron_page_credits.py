"""SMD's page credits against the chronology page allowance (2026-10-29).

A correction of SMD's own miss is not the firm's to pay for: the root-only
``medchron_allowance_credit`` verb waives pages from the cycle its credit is
written in. Used = debited minus credited in the window, floored at 0; cents
and documents untouched; another cycle untouched; a credit linked to a job is
that job's own headroom when the runner meters it with the job excluded."""

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

NOW = "2026-10-20T12:00:00Z"
OCT = "2026-10-05T00:00:00Z"
SEP = "2026-09-05T00:00:00Z"


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
    return v, db


def call(v, action, peer_pid=GATEWAY_PID, peer_uid=AGENT_UID, **req):
    return medchron_dispatch(v, action, {"action": action, **req}, peer_pid, peer_uid)


def _job(v, db, *, pages: int, cents: int, created_at: str, number: str = "2026-PI-102") -> str:
    env = {
        "matter": {"id": f"m-{number}", "number": number, "title": "Example v. Example"},
        "units": [{"client_name": "Alpha Example", "surname": "Example", "dob": "01/02/1980"}],
        "incident": {"date": "2026-01-15", "source": "administrator_request"},
        "requested_by": "admin@example.test",
    }
    j = call(v, "medchron_job_submit", envelope=env)["job_id"]
    call(v, "medchron_job_record", peer_uid=ROOT, job_id=j, state="running", fields={})
    call(
        v,
        "medchron_job_record",
        peer_uid=ROOT,
        job_id=j,
        state="delivered",
        fields={"pages": pages, "cents": cents, "documents": 3, "folder_id": "f"},
    )
    with sqlite3.connect(db) as c:
        c.execute("UPDATE medchron_jobs SET created_at = ? WHERE id = ?", (created_at, j))
    return j


def _credit_at(v, db, created_at: str, **req) -> dict:
    """A credit written at ``created_at``. One with no job applies then; one
    with a job applies in the job's own window, whatever its write time."""
    out = call(v, "medchron_allowance_credit", peer_uid=ROOT, **req)
    with sqlite3.connect(db) as c:
        c.execute("UPDATE medchron_page_credits SET created_at = ? WHERE id = ?", (created_at, out["credit"]["id"]))
        if not req.get("job_id"):
            c.execute("UPDATE medchron_page_credits SET applies_at = ? WHERE id = ?", (created_at, out["credit"]["id"]))
    return out


def _state(v, exclude: str | None = None) -> dict:
    return v._db.allowance(1000, now=NOW, exclude_job_id=exclude)


def test_a_credit_reduces_used_and_raises_remaining_in_its_cycle_pages_only(verbs):
    v, db = verbs
    _job(v, db, pages=900, cents=5000, created_at=OCT)
    before = _state(v)
    assert (before["used"], before["remaining"]) == (900, 100)
    _credit_at(v, db, OCT, pages=300, by="Scott", why="re-read of files SMD missed")
    after = _state(v)
    assert (after["used"], after["remaining"], after["pages_used"]) == (600, 400, 600)
    assert (after["pages_debited"], after["pages_credited"]) == (900, 300)
    # Cents and documents are SMD's real cost and the firm's documents: untouched.
    assert (after["cents_used"], after["documents_used"]) == (before["cents_used"], before["documents_used"])


def test_a_credit_never_takes_used_below_zero(verbs):
    v, db = verbs
    _job(v, db, pages=100, cents=10, created_at=OCT)
    _credit_at(v, db, OCT, pages=856, by="Scott", why="SMD's miss")
    s = _state(v)
    assert (s["used"], s["remaining"]) == (0, 1000)


def test_a_credit_in_another_cycle_has_no_effect(verbs):
    v, db = verbs
    _job(v, db, pages=900, cents=10, created_at=OCT)
    _credit_at(v, db, SEP, pages=300, by="Scott", why="last cycle's correction")
    s = _state(v)
    assert (s["used"], s["remaining"], s["pages_credited"]) == (900, 100, 0)


def test_a_job_credit_written_in_a_later_cycle_lands_in_the_jobs_cycle(verbs):
    """Credit written Nov 1 for an October job lightens October, not November:
    the job's delivery "consumed" and its cycle's "remains" come from one rule."""
    v, db = verbs
    oct_job = _job(v, db, pages=900, cents=10, created_at=OCT)
    _job(v, db, pages=200, cents=10, created_at="2026-11-02T00:00:00Z", number="NOVEMBER")
    out = _credit_at(v, db, "2026-11-01T09:00:00Z", pages=300, by="Scott", why="SMD's miss", job_id=oct_job)
    assert out["credit"]["applies_at"] == OCT
    october = v._db.allowance(1000, now=NOW)
    november = v._db.allowance(1000, now="2026-11-20T12:00:00Z")
    assert (october["used"], october["remaining"]) == (600, 400)
    assert (november["used"], november["remaining"], november["pages_credited"]) == (200, 800, 0)
    # The job's own delivery figure nets the same 300 pages.
    assert v._db.allowance(1000, now=NOW, exclude_job_id=oct_job)["job_pages_credited"] == 300


def test_a_job_linked_credit_is_that_jobs_own_headroom_when_it_is_metered(verbs):
    """The runner meters a job with its own pages excluded; the credit for
    those pages has to come back as headroom or a correction larger than the
    cycle's remainder would hold on pages the firm is not paying for."""
    v, db = verbs
    _job(v, db, pages=800, cents=10, created_at=OCT, number="OTHER")
    fix = _job(v, db, pages=0, cents=0, created_at=OCT)
    _credit_at(v, db, OCT, pages=856, by="Scott", why="SMD's miss", job_id=fix)
    own = _state(v, exclude=fix)
    # Others' 800 pages stand; the 856 waived pages are this job's headroom.
    assert (own["used"], own["remaining"]) == (800, 200 + 856)
    # The same credit, seen from any other caller, reduces the cycle's usage.
    assert _state(v)["used"] == 0


def test_the_hold_no_longer_fires_when_the_credit_brings_the_job_under(verbs):
    """The runner's page check, fed the broker's figure exactly as the daemon
    stamps it (`job.stamp_period`)."""
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "runners" / "medchron"))
    from medchron.limits import LimitHold, Limits

    v, db = verbs
    _job(v, db, pages=800, cents=10, created_at=OCT, number="OTHER")
    fix = _job(v, db, pages=0, cents=0, created_at=OCT)

    def check() -> None:
        state = _state(v, exclude=fix)
        lim = Limits(
            cap_usd=1000.0,
            monthly_budget_usd=10000.0,
            usd_per_scanned_page=0.01,
            usd_per_audit_claim=0.01,
            month_cents_used=state["cents_used"],
            allowance_remaining_pages=int(state["remaining"]),
        )
        lim.check_before_first_paid(pages=1000, projected_usd=1.0, spent_usd=0.0, stage="vision")

    with pytest.raises(LimitHold):
        check()
    _credit_at(v, db, OCT, pages=856, by="Scott", why="SMD's miss", job_id=fix)
    check()


def test_the_credit_writes_its_audit_row_and_answers_with_the_new_figure(verbs):
    v, db = verbs
    j = _job(v, db, pages=900, cents=10, created_at=NOW)
    out = call(v, "medchron_allowance_credit", peer_uid=ROOT, pages=856, by="Scott", why="SMD's miss", job_id=j)
    assert out["credit"]["pages"] == 856 and out["allowance"]["used"] == 44
    with sqlite3.connect(db) as c:
        atype, meta = c.execute(
            "SELECT action_type, metadata FROM audit_log WHERE action_type = 'MEDCHRON_ALLOWANCE_CREDITED'"
        ).fetchone()
    m = json.loads(meta)
    assert atype == "MEDCHRON_ALLOWANCE_CREDITED"
    assert m == {"credit_id": out["credit"]["id"], "pages": 856, "by": "Scott", "job_id": j}
    # The free-text reason stays in the ledger row, never the audit row.
    assert "why" not in m


@pytest.mark.parametrize(
    ("req", "says"),
    [
        ({"pages": 0, "by": "S", "why": "w"}, "greater than 0"),
        ({"pages": -5, "by": "S", "why": "w"}, "greater than 0"),
        ({"pages": True, "by": "S", "why": "w"}, "greater than 0"),
        ({"pages": "856", "by": "S", "why": "w"}, "greater than 0"),
        ({"pages": 856, "by": " ", "why": "w"}, "by"),
        ({"pages": 856, "by": "S", "why": ""}, "why"),
        ({"pages": 856, "by": "S", "why": "w", "job_id": "NOPE"}, "no such job"),
    ],
)
def test_refusals_write_nothing(verbs, req, says):
    v, db = verbs
    with pytest.raises(ValueError, match=says):
        call(v, "medchron_allowance_credit", peer_uid=ROOT, **req)
    with sqlite3.connect(db) as c:
        assert c.execute("SELECT COUNT(*) FROM medchron_page_credits").fetchone()[0] == 0


@pytest.mark.parametrize(("pid", "uid"), [(GATEWAY_PID, AGENT_UID), (1, AGENT_UID), (GATEWAY_PID, 10001)])
def test_only_root_may_credit(verbs, pid, uid):
    v, _db = verbs
    with pytest.raises(PermissionError):
        call(v, "medchron_allowance_credit", peer_pid=pid, peer_uid=uid, pages=1, by="S", why="w")


def test_the_job_list_carries_credits_for_the_console(verbs):
    v, db = verbs
    _credit_at(v, db, OCT, pages=856, by="Scott", why="SMD's miss")
    out = call(v, "medchron_job_list", peer_pid=1, peer_uid=AGENT_UID)
    assert [set(c) for c in out["credits"]] == [{"id", "created_at", "applies_at", "pages", "job_id"}]
    assert out["credits"][0]["pages"] == 856


def test_an_existing_ledger_gains_the_credits_table(tmp_path):
    """Migration-safe: a seat DB written before credits gains the table on the
    next ledger open, and its jobs are untouched."""
    db = str(tmp_path / "audit.db")
    MedchronLedger(db, tmp_path / "q")
    with sqlite3.connect(db) as c:
        c.execute("DROP TABLE medchron_page_credits")
    MedchronLedger(db, tmp_path / "q")
    with sqlite3.connect(db) as c:
        assert c.execute("SELECT COUNT(*) FROM medchron_page_credits").fetchone()[0] == 0


def test_job_status_carries_the_jobs_smd_page_credit(verbs):
    """`medchron_job_status(job_id)` exposes `smd_page_credit_pages` (int, 0
    when none): what the Operator reads to decide the waiver sentence."""
    v, db = verbs
    credited = _job(v, db, pages=900, cents=10, created_at=OCT)
    other = _job(v, db, pages=100, cents=10, created_at=OCT, number="OTHER")
    assert call(v, "medchron_job_status", job_id=credited)["job"]["smd_page_credit_pages"] == 0
    _credit_at(v, db, OCT, pages=451, by="Scott", why="SMD's miss", job_id=credited)
    _credit_at(v, db, OCT, pages=7, by="Scott", why="unlinked")
    assert call(v, "medchron_job_status", job_id=credited)["job"]["smd_page_credit_pages"] == 451
    assert call(v, "medchron_job_status", job_id=other)["job"]["smd_page_credit_pages"] == 0
    assert call(v, "medchron_job_status", job_id="NOPE")["job"] is None
