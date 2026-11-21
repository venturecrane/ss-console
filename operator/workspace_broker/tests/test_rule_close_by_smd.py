"""rule_close_by_smd: SMD ends a stated rule with no letter (2026-11-20, the
rules-by-hand stopgap). Root only, rule rows only, exactly once, never reported
by any sweeper query, and a committed rule is refused. All values are synthetic.
"""

from __future__ import annotations

import json
import os
import socketserver
import tempfile
import threading
import time

import pytest

from workspace_broker import rule_close
from workspace_broker.establishment import (
    RULE_CLOSED_BY_SMD_ACTION_TYPE,
    RULE_TTL_SECONDS,
    EstablishmentValidationError,
)
from workspace_broker.tests.test_ops_close_by_smd import (
    AGENT_UID,
    REQUESTER,
    SMD,
    _RootHandler,
    _agent,
    _broker,
    _ops_propose,
    _root,
    _rows,
    _sweeper_fetch,
)

REASON = "SMD records this rule by hand."
RULE_TEXT = "Lead every memo with the answer."


def _rule(broker) -> str:
    return _agent(
        broker,
        action="establish_propose",
        scope="person",
        subject={"person": REQUESTER},
        text=RULE_TEXT,
        instructed_by=REQUESTER,
        source_ref="msg-r1",
    )["proposal_id"]


def _close(broker, proposal_id, **over):
    request = {"action": "rule_close_by_smd", "proposal_id": proposal_id, "closed_by": SMD, "reason": REASON}
    request.update(over)
    return _root(broker, **request)


def test_the_agent_uid_is_refused(tmp_path):
    broker = _broker(tmp_path)
    rid = _rule(broker)
    with pytest.raises(PermissionError, match="rule_close_by_smd is not permitted"):
        broker.handle(
            {"action": "rule_close_by_smd", "proposal_id": rid, "closed_by": SMD, "reason": REASON},
            peer_pid=9999,
            peer_uid=AGENT_UID,
        )
    assert broker.establishment.pending.get(rid)["smd_closed_at"] is None


def test_root_closes_a_rule_and_nothing_reports_it(tmp_path):
    broker = _broker(tmp_path)
    rid = _rule(broker)
    out = _close(broker, rid)
    assert out["before"]["state"] == "open" and out["after"]["state"] == "closed_by_smd"
    row = broker.establishment.pending.get(rid)
    assert row["lapsed_at"] is not None and row["lapse_notified_at"] is not None
    assert row["consumed_at"] is None
    pending = broker.establishment.pending
    assert pending.unreported_outcomes_for(None) == [] and _sweeper_fetch(broker) == []
    assert pending.claim_notify(rid, "gateway") is False
    rows = _rows(broker, RULE_CLOSED_BY_SMD_ACTION_TYPE)
    assert len(rows) == 1
    assert REASON not in rows[0]["metadata"] and RULE_TEXT not in rows[0]["metadata"]
    assert json.loads(rows[0]["metadata"])["proposal_id"] == rid
    with pytest.raises(EstablishmentValidationError, match="already closed by SMD"):
        _close(broker, rid)


def test_an_expired_own_rule_is_already_inert(tmp_path):
    """A person's own rule that expires is ended silently by the sweep (no
    letter is owed for it), so there is nothing left to close."""
    broker = _broker(tmp_path)
    rid = _rule(broker)
    import sqlite3

    now = time.time()
    conn = sqlite3.connect(broker.db_path)  # type: ignore[attr-defined]
    conn.execute(
        "UPDATE pending_rules SET created_at=?, expires_at=? WHERE proposal_id=?",
        (now - RULE_TTL_SECONDS - 60, now - 60, rid),
    )
    conn.commit()
    conn.close()
    with pytest.raises(EstablishmentValidationError, match="nobody is owed a note"):
        _close(broker, rid)
    assert _sweeper_fetch(broker) == []


def test_a_committed_rule_and_an_ops_card_are_refused(tmp_path):
    broker = _broker(tmp_path)
    rid = _rule(broker)
    import sqlite3

    conn = sqlite3.connect(broker.db_path)  # type: ignore[attr-defined]
    conn.execute("UPDATE pending_rules SET consumed_at=? WHERE proposal_id=?", (time.time(), rid))
    conn.commit()
    conn.close()
    with pytest.raises(EstablishmentValidationError, match="already committed"):
        _close(broker, rid)
    assert broker.establishment.pending.close_rule_by_smd(rid, SMD, REASON) is False
    with pytest.raises(EstablishmentValidationError, match="is not a rule"):
        _close(broker, _ops_propose(broker))
    assert _rows(broker, RULE_CLOSED_BY_SMD_ACTION_TYPE) == []


def test_a_dry_run_writes_nothing(tmp_path):
    broker = _broker(tmp_path)
    rid = _rule(broker)
    plan = _close(broker, rid, dry_run=True)
    assert plan["dry_run"] is True and plan["would_become"] == "closed_by_smd"
    assert plan["before"]["text"] == RULE_TEXT
    assert broker.establishment.pending.get(rid)["smd_closed_at"] is None
    assert _rows(broker, RULE_CLOSED_BY_SMD_ACTION_TYPE) == []


@pytest.fixture
def served(tmp_path):
    broker = _broker(tmp_path)
    sock_dir = tempfile.mkdtemp(prefix="rc", dir="/tmp" if os.path.isdir("/tmp") else None)
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


def test_the_cli_dry_run_then_closes(served, capsys):
    broker, sock = served
    rid = _rule(broker)
    argv = [rid, "--by", SMD, "--reason", REASON, "--socket", sock]
    assert rule_close.main([*argv, "--dry-run"]) == 0
    assert '"would_become": "closed_by_smd"' in capsys.readouterr().out
    assert rule_close.main(argv) == 0
    out = capsys.readouterr().out
    assert '"before"' in out and '"after"' in out
    assert rule_close.main(argv) == 2
    assert "REFUSED: rule" in capsys.readouterr().err


def test_the_cli_says_when_the_broker_is_unreachable(tmp_path, capsys):
    assert rule_close.main(["0123abcd", "--by", SMD, "--reason", REASON, "--socket", str(tmp_path / "x")]) == 3
    assert "UNREACHABLE" in capsys.readouterr().err
