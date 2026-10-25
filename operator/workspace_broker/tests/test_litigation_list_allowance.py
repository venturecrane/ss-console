"""The billing cycle's litigation status-list allowance, broker side
(2026-10-24): the authored setting's reader, the count of DELIVERED status
lists per cycle (``LitigationLedger.delivered_lists``), the root status view's
``list_allowance`` the lane checks before a list starts, the cycle boundary on
the chronology skill's anchor, the hold reason's class, and root's
``over_allowance_ref`` that lets a quoted list run. Every name here is invented.
"""

from __future__ import annotations

import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from workspace_broker import hold_class
from workspace_broker.audit_ledger import LedgerWriter
from workspace_broker.litigation_intake import ListAllowanceInvalid, list_allowance_of
from workspace_broker.litigation_ledger import LitigationLedger
from workspace_broker.litigation_verbs import LitigationVerbs
from workspace_broker.server import Broker

GATEWAY_PID = 4242
AGENT_UID = 10000
LIBRARY = "1dad2f6b-7c5b-4cee-a06d-aab9e1e91a23"
ADMIN = "admin@firm.example"
VFY = "vfy_01M5ABCDEFGHJKMNPQRSTVWXYZ"

YAML = """
scope:
  admins:
    - admin@firm.example
personas:
  - slug: operator
    skills:
      - name: medical-chronology-maintainer
        enabled: true
        settings:
{chron}
      - name: litigation-status
        initiation:
          manual: true
          scheduled: true
          webhook: false
        enabled: true
        settings:
          folder_name: 'Litigation Status'
          file_to_matter_id: '{library}'
          scheduled_recipients: 'admin@firm.example'
{allowance}
    cron: []
self_initiation:
  document_library:
    operator_matter:
      number: 'OPS-LIBRARY'
"""
ANCHOR_15 = "          chronology_package_cycle_anchor_day: 15"
FIRM = "monthly_budget_usd: 750\nper_job_cap_usd: 500\n"


def _yaml(allowance: str = "40", chron: str = ANCHOR_15) -> str:
    line = "" if allowance is None else f"          litigation_list_allowance_per_cycle: {allowance}"
    return YAML.format(library=LIBRARY, allowance=line, chron=chron or "          {}")


def _seat(tmp_path: Path, now: datetime, allowance: str | None = "40", chron: str = ANCHOR_15):
    db = str(tmp_path / "audit.db")
    writer = LedgerWriter(db)
    y = tmp_path / "customer.yaml"
    y.write_text(_yaml(allowance, chron))
    firm = tmp_path / "litigation-broker-firm.yaml"
    firm.write_text(FIRM)
    ledger = LitigationLedger(db, tmp_path / "queue")
    verbs = LitigationVerbs(
        ledger, customer_yaml=str(y), audit_append=writer.append, firm_config_path=str(firm), now=lambda: now
    )
    broker = Broker.__new__(Broker)
    broker.gateway_pid = GATEWAY_PID
    broker.agent_uid = AGENT_UID
    broker.ledger = writer
    broker.litigation = verbs
    return broker, verbs, ledger, db


_N = [0]


def _row(db: str, created_at: str, state: str, report: str = "status") -> str:
    """One job row at a chosen instant and state (the ledger's own transitions
    are covered elsewhere; here only what the count reads matters)."""
    _N[0] += 1
    job_id = f"01M5TEST{_N[0]:018d}"
    conn = sqlite3.connect(db)
    try:
        conn.execute(
            "INSERT INTO litigation_jobs (id, created_at, updated_at, state, trigger, requester, request_ref, "
            "scope_key, scope_json, file_to_matter_id, envelope_digest, report) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (job_id, created_at, created_at, state, "request", ADMIN, f"<r{_N[0]}@firm.example>", "all", "{}",
             LIBRARY, "d", report),
        )  # fmt: skip
        conn.commit()
    finally:
        conn.close()
    return job_id


NOW = datetime(2026, 10, 20, 15, 0, tzinfo=timezone.utc)  # cycle Oct 15 .. Nov 15 on a 15th anchor


def test_the_40th_list_may_start_and_the_41st_may_not(tmp_path):
    _b, verbs, _l, db = _seat(tmp_path, NOW)
    for _ in range(39):
        _row(db, "2026-10-16T09:00:00.000Z", "delivered")
    state = verbs.list_allowance()
    assert (state["used"], state["remaining"], state["allowance"]) == (39, 1, 40)
    _row(db, "2026-10-17T09:00:00.000Z", "delivered")
    state = verbs.list_allowance()
    assert (state["used"], state["remaining"]) == (40, 0)
    assert state["cycle"] == "the cycle ending Nov 14" and state["unit"] == "lists"


def test_held_failed_unfinished_and_other_reports_never_count(tmp_path):
    _b, verbs, _l, db = _seat(tmp_path, NOW)
    for st in ("held", "failed", "queued", "running"):
        _row(db, "2026-10-16T09:00:00.000Z", st)
    _row(db, "2026-10-16T09:00:00.000Z", "delivered", report="deadlines")
    _row(db, "2026-10-16T09:00:00.000Z", "delivered", report="reassign")
    assert verbs.list_allowance()["used"] == 0
    one = _row(db, "2026-10-16T09:00:00.000Z", "delivered")
    assert verbs.list_allowance()["used"] == 1
    # the job asking is never counted against itself
    assert verbs.list_allowance(exclude=one)["used"] == 0


def test_the_anchor_day_resets_the_count(tmp_path):
    before = datetime(2026, 10, 14, 23, 59, tzinfo=timezone.utc)
    _b, verbs, _l, db = _seat(tmp_path, before)
    for _ in range(40):
        _row(db, "2026-10-14T12:00:00.000Z", "delivered")  # the last day of the cycle ending Oct 14
    assert verbs.list_allowance()["remaining"] == 0
    verbs._now = lambda: datetime(2026, 10, 15, 0, 0, tzinfo=timezone.utc)  # the anchor day: a new cycle
    state = verbs.list_allowance()
    assert (state["used"], state["remaining"], state["cycle_start"]) == (0, 40, "2026-10-15T00:00:00.000Z")


def test_without_an_anchor_the_cycle_is_the_calendar_month(tmp_path):
    _b, verbs, _l, db = _seat(tmp_path, NOW, chron="          {}")
    _row(db, "2026-09-30T23:00:00.000Z", "delivered")
    _row(db, "2026-10-01T00:00:00.000Z", "delivered")
    state = verbs.list_allowance()
    assert state["used"] == 1 and state["cycle_start"] == "2026-10-01T00:00:00.000Z"


def test_an_unset_allowance_is_no_cap(tmp_path):
    _b, verbs, _l, db = _seat(tmp_path, NOW, allowance=None)
    for _ in range(500):
        _row(db, "2026-10-16T09:00:00.000Z", "delivered")
    assert verbs.list_allowance() == {"authored": False}


@pytest.mark.parametrize("bad", ["'40'", "-1", "true", "4.5"])
def test_an_unreadable_allowance_is_never_read_as_no_cap(tmp_path, bad):
    _b, verbs, _l, _db = _seat(tmp_path, NOW, allowance=bad)
    state = verbs.list_allowance()
    assert state["authored"] is True and state["readable"] is False
    assert "litigation_list_allowance_per_cycle" in state["problem"]


def test_an_unreadable_anchor_under_an_authored_cap_is_unreadable(tmp_path):
    _b, verbs, _l, _db = _seat(tmp_path, NOW, chron="          chronology_package_cycle_anchor_day: '15'")
    state = verbs.list_allowance()
    assert state["readable"] is False and "chronology_package_cycle_anchor_day" in state["problem"]


def test_the_reader_takes_zero_and_refuses_what_it_cannot_read():
    assert list_allowance_of({}) is None and list_allowance_of(None) is None
    assert list_allowance_of({"litigation_list_allowance_per_cycle": 0}) == 0
    for bad in ("40", True, -3, 1.0):
        with pytest.raises(ListAllowanceInvalid):
            list_allowance_of({"litigation_list_allowance_per_cycle": bad})


def test_roots_status_view_carries_the_allowance_and_a_turns_does_not(tmp_path):
    broker, _v, _l, db = _seat(tmp_path, NOW)
    _row(db, "2026-10-16T09:00:00.000Z", "delivered")
    root = broker.handle({"action": "litigation_job_status"}, peer_pid=1, peer_uid=0)
    assert root["list_allowance"]["used"] == 1 and root["list_allowance"]["remaining"] == 39
    turn = broker.handle({"action": "litigation_job_status"}, peer_pid=GATEWAY_PID, peer_uid=AGENT_UID)
    assert "list_allowance" not in turn


def test_the_hold_reason_is_ours_and_pages_smd_to_quote():
    reason = "list_allowance: 40 of the 40 status lists included in the cycle ending Nov 14 are delivered"
    assert hold_class.classify("litigation", reason) == (hold_class.SMD_FINISHING, "limit_held")
    assert hold_class.wake_line("litigation", reason) == "Hold class: smd_finishing."


def _submit(broker, *, uid, pid, **extra):
    env = {
        "trigger": "request",
        "requester": ADMIN,
        "message_ref": "<quoted-1@firm.example>",
        "request_text": "Send me a fresh litigation status list.",
        "scope": {"all": True},
    }
    return broker.handle({"action": "litigation_job_submit", "envelope": env, **extra}, peer_pid=pid, peer_uid=uid)


def test_only_root_may_mark_a_list_quoted_and_the_mark_rides_the_row(tmp_path):
    broker, _v, ledger, _db = _seat(tmp_path, NOW)
    with pytest.raises(PermissionError):
        _submit(broker, uid=AGENT_UID, pid=GATEWAY_PID, over_allowance_ref=VFY)
    bad = _submit(broker, uid=0, pid=1, over_allowance_ref="see the email")
    assert bad["accepted"] is False and "over_allowance_ref" in bad["reason"]
    ok = _submit(broker, uid=0, pid=1, over_allowance_ref=VFY)
    assert ok["accepted"] is True
    assert ledger.read(ok["job_id"])["over_allowance_ref"] == VFY
    queued = (tmp_path / "queue" / f"{ok['job_id']}.json").read_text()
    assert "over_allowance_ref" not in queued  # the runner's job file is unchanged
