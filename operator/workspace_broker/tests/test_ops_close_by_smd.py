"""ops_close_by_smd: SMD ends an operations request with no letter (2026-10-13).

The case: SMD finished a requested change by hand and the Operator already
answered the requester in her own thread. Every agent-side ending writes to her
again (``done`` / ``declined`` are reported by the seat's sweeper, and an
unanswered card lapses into a "lapsed unanswered" letter after seven days), and
the one silent ending, ``withdrawn``, says something false. These tests pin the
fourth ending: root only, ops rows only, exactly once, and invisible to every
query the seat's sweeper (overlay ``plugins/hermes-smd-establishment/
sweeper.py``) and the requester's own next turn read outcomes from.

All values are synthetic.
"""

from __future__ import annotations

import json
import os
import socketserver
import sqlite3
import sys
import tempfile
import threading
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from workspace_broker import ops_close
from workspace_broker.audit_ledger import LedgerWriter
from workspace_broker.establishment import (
    OPS_REQUEST_CLOSED_BY_SMD_ACTION_TYPE,
    OPS_REQUEST_RESOLVED_ACTION_TYPE,
    RULE_TTL_SECONDS,
    EstablishmentStore,
    EstablishmentValidationError,
)
from workspace_broker.server import Broker

AGENT_UID = 1000
GATEWAY_PID = 42
REQUESTER = "requester@firm.example"
SMD = "captain@smd.example"
OPS_TEXT = "Send me a digest of open matters every Monday morning."
REASON = "SMD set this up by hand; the Operator already replied in the requester's thread."
SOURCE_REF = "vfy_01TESTTESTTESTTESTTESTTEST"


def _broker(tmp_path: Path) -> Broker:
    spool = tmp_path / "establish-spool"
    for child in ("staging", "runs", "results"):
        (spool / child).mkdir(parents=True)
    broker = Broker.__new__(Broker)
    broker.customer_slug = "smd"
    broker.gateway_pid = GATEWAY_PID
    broker.agent_uid = AGENT_UID
    db_path = str(tmp_path / "audit.db")
    broker.ledger = LedgerWriter(db_path)
    broker.establishment = EstablishmentStore(spool, broker.ledger, pending_db_path=db_path)
    broker.db_path = db_path  # type: ignore[attr-defined]
    return broker


def _agent(broker: Broker, **request):
    return broker.handle(request, peer_pid=9999, peer_uid=AGENT_UID)


def _root(broker: Broker, **request):
    return broker.handle(request, peer_pid=7777, peer_uid=0)


def _ops_propose(broker: Broker) -> str:
    return _agent(broker, action="ops_propose", text=OPS_TEXT, instructed_by=REQUESTER, source_ref="msg-1")[
        "proposal_id"
    ]


def _close(broker: Broker, proposal_id: str, **over):
    request = {
        "action": "ops_close_by_smd",
        "proposal_id": proposal_id,
        "closed_by": SMD,
        "source_ref": SOURCE_REF,
        "reason": REASON,
    }
    request.update(over)
    return _root(broker, **request)


def _rows(broker: Broker, action_type: str) -> list[dict]:
    conn = sqlite3.connect(broker.db_path)  # type: ignore[attr-defined]
    conn.row_factory = sqlite3.Row
    try:
        return [
            dict(r) for r in conn.execute("SELECT * FROM audit_log WHERE action_type=? ORDER BY id", (action_type,))
        ]
    finally:
        conn.close()


def _age_out(broker: Broker, proposal_id: str) -> None:
    now = time.time()
    conn = sqlite3.connect(broker.db_path)  # type: ignore[attr-defined]
    try:
        conn.execute(
            "UPDATE pending_rules SET created_at=?, expires_at=? WHERE proposal_id=?",
            (now - RULE_TTL_SECONDS - 60, now - 60, proposal_id),
        )
        conn.commit()
    finally:
        conn.close()


def _sweeper_fetch(broker: Broker) -> list[dict]:
    """The seat sweeper's exact query: senderless, outcomes included."""
    return _agent(broker, action="establish_pending", include_outcomes=True)["pending"]


def _requester_fetch(broker: Broker) -> list[dict]:
    """The requester's own next turn: what of theirs ended unreported."""
    return _agent(broker, action="establish_pending", sender=REQUESTER, include_outcomes=True)["pending"]


# --- who may call it ---------------------------------------------------------


def test_the_agent_uid_is_refused(tmp_path):
    broker = _broker(tmp_path)
    proposal_id = _ops_propose(broker)
    with pytest.raises(PermissionError, match="ops_close_by_smd is not permitted"):
        broker.handle(
            {
                "action": "ops_close_by_smd",
                "proposal_id": proposal_id,
                "closed_by": SMD,
                "source_ref": SOURCE_REF,
                "reason": REASON,
            },
            peer_pid=9999,
            peer_uid=AGENT_UID,
        )
    assert broker.establishment.pending.get(proposal_id)["smd_closed_at"] is None


def test_the_gateway_is_refused(tmp_path):
    broker = _broker(tmp_path)
    proposal_id = _ops_propose(broker)
    with pytest.raises(PermissionError, match="ops_close_by_smd is not permitted"):
        broker.handle(
            {
                "action": "ops_close_by_smd",
                "proposal_id": proposal_id,
                "closed_by": SMD,
                "source_ref": SOURCE_REF,
                "reason": REASON,
            },
            peer_pid=GATEWAY_PID,
            peer_uid=AGENT_UID,
        )
    assert broker.establishment.pending.get(proposal_id)["lapse_notified_at"] is None


# --- the close itself --------------------------------------------------------


def test_root_closes_an_open_ops_request_and_no_sweeper_path_ever_reports_it(tmp_path):
    broker = _broker(tmp_path)
    proposal_id = _ops_propose(broker)

    closed = _close(broker, proposal_id)

    assert closed["ok"] is True
    assert closed["state"] == "closed_by_smd"
    assert closed["state_before"] == "open"
    row = broker.establishment.pending.get(proposal_id)
    assert row["smd_closed_at"] is not None
    assert row["lapse_notified_at"] is not None
    assert row["lapsed_at"] is not None
    assert row["consumed_at"] is None and row["declined_at"] is None
    assert row["resolved_by"] == SMD
    assert row["outcome_reason"] == REASON

    pending = broker.establishment.pending
    assert pending.unreported_outcomes_for(None) == []
    assert pending.unreported_outcomes_for(REQUESTER) == []
    assert _sweeper_fetch(broker) == []
    assert _requester_fetch(broker) == []
    # No observer can claim or mark a letter for it.
    assert pending.claim_notify(proposal_id, "gateway") is False
    assert pending.mark_outcome_reported(proposal_id) is False
    # Expiry later changes nothing: still closed, still unreported.
    _age_out(broker, proposal_id)
    broker.establishment.sweep()
    assert pending.unreported_outcomes_for(None) == []
    # The lookup the seat uses after an answer reads the new word, never
    # "lapsed" (which would say nobody answered) or a reportable state.
    view = _agent(broker, action="establish_pending", proposal_id=proposal_id, include_outcomes=True)["pending"][0]
    assert view["state"] == "closed_by_smd"
    assert view["lapse_notified"] is True


def test_a_closed_request_cannot_then_be_answered_by_the_agent(tmp_path):
    broker = _broker(tmp_path)
    proposal_id = _ops_propose(broker)
    _close(broker, proposal_id)
    with pytest.raises(EstablishmentValidationError):
        _agent(
            broker, action="ops_resolve", proposal_id=proposal_id, outcome="done", resolved_by=SMD, source_ref="msg-2"
        )
    assert broker.establishment.pending.get(proposal_id)["consumed_at"] is None
    assert _rows(broker, OPS_REQUEST_RESOLVED_ACTION_TYPE) == []


@pytest.mark.parametrize("outcome", ["done", "declined"])
def test_done_and_declined_still_report_and_cannot_be_closed_quietly(tmp_path, outcome):
    """The falsifier: the exclusion is the close's stamp, not a broken query."""
    broker = _broker(tmp_path)
    proposal_id = _ops_propose(broker)
    _agent(
        broker,
        action="ops_resolve",
        proposal_id=proposal_id,
        outcome=outcome,
        resolved_by=SMD,
        source_ref="msg-2",
        reason="no" if outcome == "declined" else None,
    )

    assert [r["proposal_id"] for r in _sweeper_fetch(broker)] == [proposal_id]
    with pytest.raises(EstablishmentValidationError, match="already (answered|declined)"):
        _close(broker, proposal_id)
    assert broker.establishment.pending.get(proposal_id)["smd_closed_at"] is None
    assert [r["proposal_id"] for r in _sweeper_fetch(broker)] == [proposal_id]


def test_a_second_close_is_refused(tmp_path):
    broker = _broker(tmp_path)
    proposal_id = _ops_propose(broker)
    _close(broker, proposal_id)
    with pytest.raises(EstablishmentValidationError, match="already closed by SMD"):
        _close(broker, proposal_id)
    assert len(_rows(broker, OPS_REQUEST_CLOSED_BY_SMD_ACTION_TYPE)) == 1
    # And the store's own UPDATE refuses it even past the named check.
    assert broker.establishment.pending.close_ops_by_smd(proposal_id, SMD, REASON) is False


def test_a_rule_is_refused(tmp_path):
    broker = _broker(tmp_path)
    rule_id = _agent(
        broker,
        action="establish_propose",
        scope="firm_adjust",
        subject={"output_class": "outbound", "property": "voice"},
        text="In client letters, no pleasantries.",
        instructed_by=REQUESTER,
        source_ref="msg-3",
        for_admin=True,
    )["proposal_id"]
    with pytest.raises(EstablishmentValidationError, match="not an operations request"):
        _close(broker, rule_id)
    assert broker.establishment.pending.close_ops_by_smd(rule_id, SMD, REASON) is False
    assert broker.establishment.pending.get(rule_id)["lapse_notified_at"] is None


def test_an_expired_request_the_sweep_has_lapsed_closes_quietly(tmp_path):
    """The verb's own sweep marks it lapsed first; nobody has claimed or sent
    the lapse letter, so it is still SMD's to close -- and then never mailed."""
    broker = _broker(tmp_path)
    proposal_id = _ops_propose(broker)
    _age_out(broker, proposal_id)

    closed = _close(broker, proposal_id)

    assert closed["state"] == "closed_by_smd"
    assert closed["state_before"] == "lapsed"
    assert _sweeper_fetch(broker) == []
    assert broker.establishment.pending.claim_notify(proposal_id, "gateway") is False


def test_an_expired_request_nobody_has_swept_closes_quietly(tmp_path):
    """The store's UPDATE directly, with no sweep in front of it."""
    broker = _broker(tmp_path)
    proposal_id = _ops_propose(broker)
    _age_out(broker, proposal_id)
    pending = broker.establishment.pending
    assert pending.get(proposal_id)["lapsed_at"] is None

    assert pending.close_ops_by_smd(proposal_id, SMD, REASON) is True
    broker.establishment.sweep()
    assert pending.unreported_outcomes_for(None) == []


def test_a_request_whose_lapse_letter_is_claimed_is_refused(tmp_path):
    broker = _broker(tmp_path)
    proposal_id = _ops_propose(broker)
    _age_out(broker, proposal_id)
    broker.establishment.sweep()
    assert broker.establishment.pending.claim_notify(proposal_id, "webhook-gate") is True

    with pytest.raises(EstablishmentValidationError, match="letter claimed by webhook-gate"):
        _close(broker, proposal_id)
    row = broker.establishment.pending.get(proposal_id)
    assert row["smd_closed_at"] is None and row["lapse_notified_at"] is None
    assert broker.establishment.pending.close_ops_by_smd(proposal_id, SMD, REASON) is False
    assert _rows(broker, OPS_REQUEST_CLOSED_BY_SMD_ACTION_TYPE) == []


def test_a_withdrawn_or_already_reported_request_is_refused(tmp_path):
    broker = _broker(tmp_path)
    proposal_id = _ops_propose(broker)
    _agent(
        broker, action="ops_resolve", proposal_id=proposal_id, outcome="withdrawn", resolved_by=SMD, source_ref="msg-2"
    )
    with pytest.raises(EstablishmentValidationError, match="nobody is owed a note"):
        _close(broker, proposal_id)


# --- the ledger row ----------------------------------------------------------


def test_the_ledger_row_carries_ids_and_never_the_reason(tmp_path):
    broker = _broker(tmp_path)
    proposal_id = _ops_propose(broker)
    _close(broker, proposal_id)

    rows = _rows(broker, OPS_REQUEST_CLOSED_BY_SMD_ACTION_TYPE)
    assert len(rows) == 1
    metadata = json.loads(rows[0]["metadata"])
    assert metadata["proposal_id"] == proposal_id
    assert metadata["closed_by"] == SMD
    assert metadata["source_ref"] == SOURCE_REF
    assert metadata["instructed_by"] == REQUESTER
    assert metadata["has_reason"] is True
    assert len(metadata["text_sha256"]) == 64
    assert REASON not in rows[0]["metadata"]
    assert "by hand" not in rows[0]["metadata"]
    assert OPS_TEXT not in rows[0]["metadata"]
    # It is not filed as an answer the requester is told about.
    assert _rows(broker, OPS_REQUEST_RESOLVED_ACTION_TYPE) == []


# --- the fields --------------------------------------------------------------


@pytest.mark.parametrize(
    ("over", "match"),
    [
        ({"reason": "   "}, "reason is required"),
        ({"reason": None}, "reason is required"),
        ({"source_ref": ""}, "source_ref"),
        ({"closed_by": "not-an-address"}, "closed_by"),
        ({"dry_run": "yes"}, "dry_run must be a boolean"),
    ],
)
def test_every_field_is_required_and_checked(tmp_path, over, match):
    broker = _broker(tmp_path)
    proposal_id = _ops_propose(broker)
    with pytest.raises(EstablishmentValidationError, match=match):
        _close(broker, proposal_id, **over)
    assert broker.establishment.pending.get(proposal_id)["smd_closed_at"] is None


def test_a_dry_run_writes_nothing(tmp_path):
    broker = _broker(tmp_path)
    proposal_id = _ops_propose(broker)

    plan = _close(broker, proposal_id, dry_run=True)

    assert plan["dry_run"] is True
    assert plan["state"] == "open"
    assert plan["would_become"] == "closed_by_smd"
    assert plan["instructed_by"] == REQUESTER
    row = broker.establishment.pending.get(proposal_id)
    assert row["smd_closed_at"] is None and row["lapse_notified_at"] is None
    assert _rows(broker, OPS_REQUEST_CLOSED_BY_SMD_ACTION_TYPE) == []
    # A dry run still names a refusal.
    _close(broker, proposal_id)
    with pytest.raises(EstablishmentValidationError, match="already closed"):
        _close(broker, proposal_id, dry_run=True)


# --- the root CLI ------------------------------------------------------------


class _RootHandler(socketserver.StreamRequestHandler):
    """Serves one request the way server.RequestHandler does, as uid 0.

    SO_PEERCRED is Linux-only, so the peer is asserted rather than read; the
    uid gate itself is covered by the two refusal tests above."""

    def handle(self) -> None:
        from workspace_broker.request_errors import error_response_for

        request = json.loads(self.rfile.readline())
        try:
            response = self.server.broker.handle(request, 7777, 0)  # type: ignore[attr-defined]
        except Exception as exc:  # noqa: BLE001 - mirror the real handler's one reply shape
            response = error_response_for(exc)
        self.wfile.write(json.dumps(response).encode("utf-8") + b"\n")


@pytest.fixture
def served(tmp_path):
    broker = _broker(tmp_path)
    # A short path: AF_UNIX paths are capped near 104 bytes on macOS.
    sock_dir = tempfile.mkdtemp(prefix="oc", dir="/tmp" if os.path.isdir("/tmp") else None)
    sock_path = os.path.join(sock_dir, "b.sock")
    server = socketserver.ThreadingUnixStreamServer(sock_path, _RootHandler)
    server.broker = broker  # type: ignore[attr-defined]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield broker, sock_path
    finally:
        server.shutdown()
        server.server_close()
        os.unlink(sock_path)
        os.rmdir(sock_dir)


def _cli(sock_path: str, proposal_id: str, *extra: str) -> int:
    return ops_close.main(
        [proposal_id, "--by", SMD, "--source-ref", SOURCE_REF, "--reason", REASON, "--socket", sock_path, *extra]
    )


def test_the_cli_dry_run_then_closes_through_the_socket(served, capsys):
    broker, sock_path = served
    proposal_id = _ops_propose(broker)

    assert _cli(sock_path, proposal_id, "--dry-run") == 0
    assert '"would_become": "closed_by_smd"' in capsys.readouterr().out
    assert broker.establishment.pending.get(proposal_id)["smd_closed_at"] is None

    assert _cli(sock_path, proposal_id.upper()) == 0
    assert broker.establishment.pending.get(proposal_id)["smd_closed_at"] is not None
    assert _sweeper_fetch(broker) == []

    assert _cli(sock_path, proposal_id) == 2
    assert "REFUSED: operations request" in capsys.readouterr().err


def test_the_cli_says_when_the_broker_is_unreachable(tmp_path, capsys):
    assert _cli(str(tmp_path / "absent.sock"), "0123abcd") == 3
    assert "UNREACHABLE" in capsys.readouterr().err
