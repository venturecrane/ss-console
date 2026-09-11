"""The escalation raise witness: a raise the broker did not witness is refused.

``validate_append`` has always refused an ``acked`` with no prior raise — you
cannot silence an alarm that never rang. This is the other door into the same
silence: ``should_fire`` reads ``last_raised_date`` off a ``fired``/``chased``
row, so a raise recorded for an alert nobody received suppresses that deadline
for ``refire_days``. The alarm does not ring, and writes down that it rang.

Live shape this closes (pilot-smokeball):

* 2026-08-26T14:00Z — woke on 5 needs-you items, derived 5 ACK codes, attempted
  ONE ``create_memo`` (refused by the cross-matter fence), wrote 5 ``fired``
  rows, ended the turn. Zero sends. Those 5 deadlines went silent until 08-29.
* 2026-08-20T14:00Z — 77 appends, zero sends, no memo refusal anywhere.

The witness asks a question about the broker's OWN past behaviour: it holds the
only send credentials on the Machine and writes ``CONFIRM_SEND_DISPATCHED``
itself, so the agent supplies no evidence and can forge none.
"""

from __future__ import annotations

import json
import sqlite3
import sys
import threading
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from workspace_broker import escalation_ledger as el
from workspace_broker.audit_ledger import LedgerWriter
from workspace_broker.send_witness import dispatched_to_a_person
from workspace_broker.server import Broker

AGENT_UID = 1000
GATEWAY_PID = 42

SESSION = "cron_1b149663f32f_20260826_070050"
OTHER_SESSION = "cron_7651107620f7_20260826_071749"


def _broker(tmp_path: Path, *, audit: bool = True) -> Broker:
    broker = Broker.__new__(Broker)
    broker.customer_slug = "smd"
    broker.gateway_pid = GATEWAY_PID
    broker.agent_uid = AGENT_UID
    broker.ledger = None
    broker.escalation_ledger_path = str(tmp_path / "escalation-ledger.jsonl")
    broker._escalation_lock = threading.Lock()
    if audit:
        db = tmp_path / "audit.db"
        LedgerWriter(str(db))  # creates the schema
        broker.audit_db_path = str(db)
    return broker


def _dispatch(
    broker: Broker,
    *,
    ts: str,
    recipients: list[str],
    session_id: str | None = SESSION,
    action_type: str = "CONFIRM_SEND_DISPATCHED",
    outcome: str = "sent",
    skill_name: str | None = "deadline-miss-escalator",
) -> None:
    """Write an audit row of the shape the broker's own transmit_verbs.append_send_row writes.

    ``skill_name`` sits on its COLUMN, never in metadata -- that is where the
    broker moves it (B3, claims review 2026-09-04) and where the console's
    wake<->confirm join reads it. The witness itself reads only ``metadata``,
    so the column is here for shape fidelity, not because the witness needs it.
    """
    meta: dict = {"outcome": outcome, "recipients": recipients}
    if session_id is not None:
        meta["session_id"] = session_id
    conn = sqlite3.connect(broker.audit_db_path)
    try:
        conn.execute(
            "INSERT INTO audit_log (ts, action_type, actor, actor_role, skill_name, metadata)"
            " VALUES (?, ?, 'operator', 'agent', ?, ?)",
            (ts, action_type, skill_name, json.dumps(meta)),
        )
        conn.commit()
    finally:
        conn.close()


def _fired(session_id: str | None = SESSION, event: str = "fired") -> dict:
    ev = el.make_event(
        skill="deadline-miss-escalator",
        matter_id="2026-PI-101",
        item_key="item-under-test",
        event=event,
        attempt=1,
        token="ACK-7Q3M2K",
    )
    if session_id is not None:
        ev["session_id"] = session_id
    return ev


def _append(broker: Broker, event: dict):
    return broker.handle(
        {"action": "escalation_event_append", "event": event},
        peer_pid=9999,  # NOT the gateway PID
        peer_uid=AGENT_UID,
    )


# ---------------------------------------------------------------------------
# The witness itself
# ---------------------------------------------------------------------------


def test_a_dispatch_in_the_same_session_witnesses_the_raise(tmp_path: Path) -> None:
    broker = _broker(tmp_path)
    _dispatch(broker, ts="2026-08-25T14:01:09.323Z", recipients=["scott@smd.services"])
    assert dispatched_to_a_person(broker.audit_db_path, _fired()) is True


def test_no_dispatch_at_all_does_not_witness(tmp_path: Path) -> None:
    """The 2026-08-26 shape: the turn wrote fired rows having sent nothing."""
    broker = _broker(tmp_path)
    assert dispatched_to_a_person(broker.audit_db_path, _fired()) is False


def test_a_dispatch_in_a_DIFFERENT_session_does_not_witness(tmp_path: Path) -> None:
    """Pilot runs four other cron routines 17-41 minutes after the escalator. A
    neighbour's send must not authorise this skill's raise — that is the same
    forgery, laundered through another routine."""
    broker = _broker(tmp_path)
    _dispatch(
        broker,
        ts="2026-08-26T14:19:00.000Z",
        recipients=["scott@smd.services"],
        session_id=OTHER_SESSION,
    )
    assert dispatched_to_a_person(broker.audit_db_path, _fired()) is False


def test_a_probe_only_dispatch_does_not_witness(tmp_path: Path) -> None:
    """Otherwise a prove-out's own smoke send clears the falsifier it is meant to
    exercise. Mirrors _PROBE_RECIPIENT_PREFIX in the overlay's heartbeat.py."""
    broker = _broker(tmp_path)
    _dispatch(broker, ts="2026-08-26T14:01:00.000Z", recipients=["ss-probe-runner@agentmail.to"])
    assert dispatched_to_a_person(broker.audit_db_path, _fired()) is False


def test_a_mixed_dispatch_witnesses_on_the_real_recipient(tmp_path: Path) -> None:
    broker = _broker(tmp_path)
    _dispatch(
        broker,
        ts="2026-08-26T14:01:00.000Z",
        recipients=["ss-probe-runner@agentmail.to", "scott@smd.services"],
    )
    assert dispatched_to_a_person(broker.audit_db_path, _fired()) is True


def test_a_failed_send_does_not_witness(tmp_path: Path) -> None:
    """CONFIRM_SEND_FAILED rows exist precisely because the send did NOT go. A
    refusal must never be readable as a send."""
    broker = _broker(tmp_path)
    _dispatch(
        broker,
        ts="2026-08-26T14:01:00.000Z",
        recipients=["scott@smd.services"],
        action_type="CONFIRM_SEND_FAILED",
        outcome="refused",
    )
    assert dispatched_to_a_person(broker.audit_db_path, _fired()) is False


def test_an_audit_disabled_broker_allows_the_raise(tmp_path: Path) -> None:
    """Fail OPEN, uniquely here. A seat with no audit DB has no witness and never
    had one; refusing would turn it into a seat that cannot escalate at all."""
    broker = _broker(tmp_path, audit=False)
    assert dispatched_to_a_person(broker.audit_db_path, _fired()) is True


def test_an_unreadable_audit_db_allows_the_raise(tmp_path: Path) -> None:
    broker = _broker(tmp_path)
    broker.audit_db_path = str(tmp_path / "no-such-dir" / "audit.db")
    assert dispatched_to_a_person(broker.audit_db_path, _fired()) is True


def test_a_sessionless_event_falls_back_to_a_recent_window(tmp_path: Path) -> None:
    """Deployment-order tolerance: a seat can carry a new image with an older
    pinned overlay that does not yet plumb session_id. Refusing there would break
    every raise on that seat."""
    broker = _broker(tmp_path)
    assert dispatched_to_a_person(broker.audit_db_path, _fired(session_id=None)) is False
    conn = sqlite3.connect(broker.audit_db_path)
    try:
        conn.execute(
            "INSERT INTO audit_log (ts, action_type, actor, actor_role, metadata)"
            " VALUES (strftime('%Y-%m-%dT%H:%M:%fZ','now'), 'CONFIRM_SEND_DISPATCHED',"
            " 'operator', 'agent', ?)",
            (json.dumps({"outcome": "sent", "recipients": ["scott@smd.services"]}),),
        )
        conn.commit()
    finally:
        conn.close()
    assert dispatched_to_a_person(broker.audit_db_path, _fired(session_id=None)) is True


# ---------------------------------------------------------------------------
# End to end through the verb
# ---------------------------------------------------------------------------


def test_the_verb_refuses_an_unwitnessed_fired_and_writes_nothing(tmp_path: Path) -> None:
    broker = _broker(tmp_path)
    with pytest.raises(ValueError, match="dispatched no message"):
        _append(broker, _fired())
    assert el.read_ledger(broker.escalation_ledger_path) == []


def test_the_verb_accepts_a_witnessed_fired(tmp_path: Path) -> None:
    broker = _broker(tmp_path)
    _dispatch(broker, ts="2026-08-26T14:01:00.000Z", recipients=["scott@smd.services"])
    resp = _append(broker, _fired())
    assert resp["ok"] is True
    rows = el.read_ledger(broker.escalation_ledger_path)
    assert [r["event"] for r in rows] == ["fired"]


def test_an_ack_still_works_without_any_dispatch(tmp_path: Path) -> None:
    """An ack is not a claim that anyone was reached, so the witness must not
    gate it — and a broker with no dispatch history must still accept one."""
    broker = _broker(tmp_path)
    _dispatch(broker, ts="2026-08-26T14:01:00.000Z", recipients=["scott@smd.services"])
    _append(broker, _fired())
    acked = el.make_event(
        skill="deadline-miss-escalator",
        matter_id="2026-PI-101",
        item_key="item-under-test",
        event="acked",
        attempt=1,
        token="ACK-7Q3M2K",
    )
    resp = _append(broker, acked)
    assert resp["ok"] is True


# ---------------------------------------------------------------------------
# Retro-falsifier: the predicate replayed against REAL pilot-smokeball rows.
#
# A guard that only ever says "no" passes every test above. This is the check
# that can fail in BOTH directions: it must ALLOW the four days the escalator
# genuinely delivered and REFUSE the two it did not. Rows are the real ones
# (from the seat's audit_log, reduced to action_type/ts/recipients/session_id);
# the live replay is recorded separately as a crane_verify artifact, because
# audit.db is seat-local and CI cannot reach it.
# ---------------------------------------------------------------------------

#: (label, session, dispatch rows for that session, expected verdict)
_AUGUST: tuple[tuple[str, str, list[tuple[str, list[str]]], bool], ...] = (
    (
        "2026-08-25T14:00 delivered",
        "cron_6c073ab9b3fc_20260825_070034",
        [("2026-08-25T14:01:09.323Z", ["scott@smd.services"])],
        True,
    ),
    (
        "2026-08-24T14:00 delivered",
        "cron_3af5c6a4276e_20260824_070026",
        [("2026-08-24T14:04:10.943Z", ["scott@smd.services"])],
        True,
    ),
    (
        "2026-08-24T20:16 delivered",
        "cron_e0c4a934d927_20260824_074126",
        [("2026-08-24T14:43:32.682Z", ["smdurgan@smdurgan.com"])],
        True,
    ),
    (
        "2026-08-23T21:11 delivered (probe alongside a real recipient)",
        "20260823_211120_764641b3",
        [
            ("2026-08-23T21:11:32.017Z", ["ss-probe-runner@agentmail.to"]),
            ("2026-08-23T21:11:32.780Z", ["scott@smd.services"]),
        ],
        True,
    ),
    (
        "2026-08-26T14:02 five fired rows, ZERO sends",
        "cron_1b149663f32f_20260826_070050",
        [],
        False,
    ),
    (
        "2026-08-25T18:32 rehearsal raise, no send attempted",
        "cron_39f995e54853_20260825_183151",
        [],
        False,
    ),
)


@pytest.mark.parametrize(
    "label,session,dispatches,expected",
    _AUGUST,
    ids=[row[0] for row in _AUGUST],
)
def test_retro_falsifier_over_real_august_rows(
    tmp_path: Path,
    label: str,
    session: str,
    dispatches: list[tuple[str, list[str]]],
    expected: bool,
) -> None:
    broker = _broker(tmp_path)
    # Every day's rows land in one DB, so a day's verdict must come from ITS OWN
    # session — exactly the cross-contamination the session join exists to stop.
    for other_label, other_session, rows, _ in _AUGUST:
        for ts, recipients in rows:
            _dispatch(broker, ts=ts, recipients=recipients, session_id=other_session)
    assert dispatched_to_a_person(broker.audit_db_path, _fired(session_id=session)) is expected, label


def test_the_falsifier_covers_both_verdicts() -> None:
    """Guards the guard: if someone trims this table to one side, the suite stops
    being able to fail in both directions and silently measures nothing."""
    verdicts = {row[3] for row in _AUGUST}
    assert verdicts == {True, False}
