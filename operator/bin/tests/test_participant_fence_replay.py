"""The participant-fence replay: each reconstruction rule, against a built ledger.

The replay is the evidence the fence is safe to ship (plan section 6), so it is
held to the same standard as the fence: the happy paths pass, the incident is
refused, and each path is pinned to the anchor and lane the new overlay would
pass.
"""

from __future__ import annotations

import importlib.util
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any

import pytest

_BIN = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("participant_fence_replay", _BIN / "participant-fence-replay.py")
assert _spec and _spec.loader
replay = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = replay  # dataclasses resolve their module by name
_spec.loader.exec_module(replay)
fence = replay.load_fence(None)

ALICE, BRUNO, DANA, SCOTT = "alice@firm.example", "bruno@firm.example", "dana@firm.example", "scott@smd.services"
JOB = "01M4BXJ2DYY5ARPXSMG869YMCR"

YAML = f"""
scope:
  inbound_allow_from: ['@firm.example', {SCOTT}]
  admins: [{ALICE}, {SCOTT}]
  rule_requests_to: [{ALICE}, {SCOTT}]
escalation:
  red_flag_recipients: [{ALICE}, {SCOTT}]
personas:
  - skills:
      - name: statute-watch
        enabled: true
        settings: {{recipient: {ALICE}}}
"""


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE audit_log (ts TEXT, action_type TEXT, skill_name TEXT, metadata TEXT)")
    conn.execute("CREATE TABLE medchron_jobs (id TEXT, requester TEXT, request_ref TEXT)")
    conn.execute("CREATE TABLE pending_rules (proposal_id TEXT, instructed_by TEXT)")
    return conn


def _row(conn, ts: str, action: str, meta: dict, skill: str | None = None) -> None:
    conn.execute("INSERT INTO audit_log VALUES (?,?,?,?)", (ts, action, skill, json.dumps(meta)))


def _send(conn, ts: str, to: list[str], *, session: str = "", skill: str | None = None, gid: str = "") -> None:
    meta = {"verb": "msgraph_send", "outcome": "sent", "recipients": to, "session_id": session, "graph_message_id": gid}
    _row(conn, ts, "CONFIRM_SEND_DISPATCHED", meta, skill)


class FakeMail:
    def __init__(self) -> None:
        self.messages: dict[str, dict[str, Any]] = {
            "REQ": {"sender": ALICE, "to": ["operator@firm.example"], "cc": [DANA], "conversation_id": "C1"},
            "JOBREQ": {"sender": ALICE, "to": ["operator@firm.example"], "cc": [], "conversation_id": "C2"},
        }
        self.sent: dict[str, dict[str, Any]] = {}

    def participants(self, message_id: str) -> dict[str, Any]:
        if message_id not in self.messages:
            raise LookupError("404")
        return self.messages[message_id]

    def find_by_imid(self, imid: str) -> str | None:
        return "JOBREQ" if imid == "<jobreq@firm.example>" else None

    def sent_message(self, send) -> dict[str, Any] | None:
        return self.sent.get(send.graph_message_id)


@pytest.fixture
def seat(tmp_path: Path):
    customer = tmp_path / "customer.yaml"
    customer.write_text(YAML)
    return fence.seat_facts(customer)


def _verdicts(conn, facts, mail) -> list:
    ledger = replay.read_ledger(conn, "2026-01-01T00:00:00")
    return [replay.judge(fence, facts, ledger, mail, s, "graph_message") for s in ledger.sends]


def test_a_routine_rides_its_authored_lane(seat) -> None:
    conn = _conn()
    _send(conn, "2026-10-01T08:00:00Z", [ALICE, SCOTT], skill="statute-watch")
    _send(conn, "2026-10-01T08:01:00Z", [ALICE], skill="deadline-miss-escalator")
    (statute, escalator) = _verdicts(conn, seat, FakeMail())
    assert (statute.lane, statute.decision) == ("skill:statute-watch", "allowed")
    assert (escalator.lane, escalator.decision) == ("escalation", "allowed")


def test_the_incident_is_refused_naming_the_attorney(seat) -> None:
    """A chronology's held wake emailed the requester AND an attorney who was
    not on her request (a law-firm seat, 2026-10-07)."""
    conn = _conn()
    conn.execute("INSERT INTO medchron_jobs VALUES (?,?,?)", (JOB, ALICE, "<jobreq@firm.example>"))
    _row(conn, "2026-10-07T21:22:03Z", "MEDCHRON_JOB_HELD", {"job_id": JOB})
    _row(conn, "2026-10-07T21:22:13Z", "TOOL_CALL_COMPLETED", {"session_id": "20261007_212208_x"})
    _send(conn, "2026-10-07T21:23:15Z", [ALICE, BRUNO], session="20261007_212208_x")
    (v,) = _verdicts(conn, seat, FakeMail())
    assert v.path == "job_wake:medchron_job" and v.anchor == {"kind": "medchron_job", "job_id": JOB}
    assert v.decision == "participants" and v.refused == [BRUNO]


def test_an_email_turn_anchors_on_the_email_that_opened_it(seat) -> None:
    conn = _conn()
    _row(conn, "2026-10-02T10:00:00Z", "INBOUND_RECEIVED", {"vendor_message_id": "REQ"})
    _row(conn, "2026-10-02T10:00:20Z", "TOOL_CALL_COMPLETED", {"session_id": "s-email"})
    _send(conn, "2026-10-02T10:01:00Z", [DANA], session="s-email")
    _send(conn, "2026-10-02T10:01:30Z", [BRUNO], session="s-email")
    allowed, refused = _verdicts(conn, seat, FakeMail())
    assert allowed.path == "email_turn(inferred)" and allowed.decision == "allowed"
    assert refused.decision == "participants" and refused.refused == [BRUNO]


def test_a_cron_turn_has_no_anchor(seat) -> None:
    conn = _conn()
    _send(conn, "2026-10-03T07:00:00Z", [BRUNO], session="cron_a726fd5efd24_20261003_070000")
    _send(conn, "2026-10-03T07:01:00Z", [SCOTT], session="cron_a726fd5efd24_20261003_070000")
    bruno, scott = _verdicts(conn, seat, FakeMail())
    assert (bruno.path, bruno.decision) == ("cron", "participants")
    assert scott.decision == "allowed"


def test_the_rule_loop_paths(seat) -> None:
    mail = FakeMail()
    mail.sent["ASK"] = {
        "subject": "A rule for the firm to approve [rule abcd1234]",
        "recipients": [ALICE, SCOTT, DANA],
    }
    mail.sent["OUT"] = {"subject": "Your rule is in effect [rule abcd1234]", "recipients": [DANA]}
    mail.sent["ELSE"] = {"subject": "Your rule is in effect [rule abcd1234]", "recipients": [BRUNO]}
    mail.sent["GONE"] = {"subject": "Your rule lapsed unanswered [rule 99999999]", "recipients": [DANA]}
    conn = _conn()
    conn.execute("INSERT INTO pending_rules VALUES (?,?)", ("abcd1234", DANA))
    _row(conn, "2026-10-04T09:00:00Z", "INBOUND_RECEIVED", {"vendor_message_id": "REQ"})
    _row(conn, "2026-10-04T09:00:10Z", "TOOL_CALL_COMPLETED", {"session_id": "s-rule"})
    _send(conn, "2026-10-04T09:00:30Z", [ALICE, SCOTT], session="s-rule", gid="ASK")
    _send(conn, "2026-10-05T09:00:00Z", [DANA], gid="OUT")
    _send(conn, "2026-10-05T09:01:00Z", [BRUNO], gid="ELSE")
    _send(conn, "2026-10-05T09:02:00Z", [DANA], gid="GONE")
    ask, outcome, elsewhere, gone = _verdicts(conn, seat, mail)
    assert (ask.path, ask.lane, ask.decision) == ("rule_dispatch", "rule_dispatch", "allowed")
    # A row written before the fence records no email: its letter reaches the
    # requester the row recorded, and nobody else.
    assert (outcome.path, outcome.decision) == ("rule_outcome", "allowed")
    assert (elsewhere.decision, elsewhere.refused) == ("participants", [BRUNO])
    assert gone.decision == "participants_unverifiable"


def test_a_swept_rule_row_is_recovered_from_its_proposal_row(seat) -> None:
    """The broker sweeps answered proposals; their RULE_PROPOSED rows remain
    and record who asked, which is what the letter is checked against."""
    mail = FakeMail()
    mail.sent["OUT"] = {"subject": "Your rule was declined [rule feed1234]", "recipients": [DANA]}
    conn = _conn()
    _row(conn, "2026-09-01T09:00:00Z", "RULE_PROPOSED", {"proposal_id": "feed1234", "instructed_by": DANA})
    _send(conn, "2026-10-05T09:00:00Z", [DANA], gid="OUT")
    (v,) = _verdicts(conn, seat, mail)
    assert (v.path, v.decision) == ("rule_outcome", "allowed")


def test_sent_items_the_ledger_does_not_account_for_are_counted_by_sender() -> None:
    conn = _conn()
    meta = {"verb": "msgraph_send", "outcome": "sent", "recipients": [DANA], "audit_row_token": "TOK1"}
    _row(conn, "2026-10-05T10:00:00Z", "CONFIRM_SEND_DISPATCHED", meta)
    ledger = replay.read_ledger(conn, "2026-01-01T00:00:00")

    class Ops:
        def mailbox(self) -> str:
            return "operator@firm.example"

    class Mail:
        _ops = Ops()

        def sent_items(self, since: str) -> list[dict[str, str]]:
            return [
                {"from": "operator@firm.example", "token": "TOK1"},
                {"from": "operator@firm.example", "token": ""},
                {"from": ALICE, "token": ""},
            ]

    out = replay.sent_items_summary(Mail(), ledger, "2026-01-01T00:00:00")
    assert out == {
        "read": True,
        "total": 3,
        "unaudited": 2,
        "unaudited_from": {"operator mailbox": 1, "staff send-as": 1},
    }
    assert replay.sent_items_summary(object(), ledger, "x") == {"read": False}


def test_replies_answer_their_own_email(seat) -> None:
    conn = _conn()
    meta = {"verb": "msgraph_reply", "outcome": "sent", "recipients": [BRUNO], "session_id": "s"}
    _row(conn, "2026-10-05T10:00:00Z", "CONFIRM_SEND_DISPATCHED", meta)
    (v,) = _verdicts(conn, seat, FakeMail())
    assert (v.path, v.decision) == ("reply", "allowed")


def test_the_report_names_staff_and_hides_outsiders(seat) -> None:
    conn = _conn()
    _send(conn, "2026-10-06T10:00:00Z", [BRUNO, "client@outside.example"], session="cron_x_20261006_100000")
    out = replay.report(seat, _verdicts(conn, seat, FakeMail()))
    assert out["would_refuse"][0]["recipients"] == [BRUNO, "outside"]
    assert "client@outside.example" not in json.dumps(out)


def test_main_needs_exactly_one_mailbox(tmp_path: Path) -> None:
    with pytest.raises(SystemExit):
        replay.main(["--audit-db", "x", "--customer", "y"])


def test_graph_mail_only_ever_gets() -> None:
    """The mailbox reads are GETs on the read credential; nothing else is
    callable through the replay's Graph wrapper."""
    calls: list[tuple[str, str]] = []

    class Ops:
        def _request(self, path, method, body, **kw):
            calls.append((method, kw.get("role", "")))
            return {"value": [], "id": "X", "from": {"emailAddress": {"address": "a@b.example"}}}

        def _mail_path(self, *parts):
            return "/" + "/".join(parts)

        def mailbox(self):
            return "operator@firm.example"

    mail = replay.GraphMail(Ops(), Path("read.json"))
    mail.participants("X")
    mail.find_by_imid("<x@y>")
    mail.sent_message(replay.Send("t", "a", "msgraph_send", [], graph_message_id="X"))
    assert calls and all(c == ("GET", "read") for c in calls)
