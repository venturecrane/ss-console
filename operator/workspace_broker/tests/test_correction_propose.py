"""correction_propose verb: uid-gated, CORRECTION_PROPOSED-only (ADR 0083 §4, #2091).

The Operator captures a correction a customer stated. It never applies one, and
nothing it writes here reaches a spec file — promotion is a portal-side act by a
Named Administrator. These tests hold that shape at the seam where it could
quietly stop being true.

Same caller shape as the other narrow verbs: an ``execute_code`` turn runs as
the agent uid on a non-gateway PID, so the gateway-PID-gated ``audit_append``
refuses it and this verb is the door.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from workspace_broker.audit_ledger import LedgerWriter
from workspace_broker.corrections import (
    CORRECTION_ACTION_TYPE,
    CorrectionValidationError,
    build_correction_row,
)
from workspace_broker.server import Broker

AGENT_UID = 1000
GATEWAY_PID = 42


def _broker(tmp_path: Path) -> Broker:
    broker = Broker.__new__(Broker)
    broker.customer_slug = "smd"
    broker.gateway_pid = GATEWAY_PID
    broker.agent_uid = AGENT_UID
    broker.ledger = LedgerWriter(str(tmp_path / "audit.db"))
    return broker


def _proposal(**overrides) -> dict:
    proposal = {
        "output_class": "client_email",
        "spec_property": "format",
        "statement": "Could this be a table instead of text",
        "stated_by": "Christa",
        "source_ref": "01JABCDEF",
    }
    proposal.update(overrides)
    return proposal


def _stored_metadata(tmp_path: Path) -> dict:
    conn = sqlite3.connect(str(tmp_path / "audit.db"))
    row = conn.execute("SELECT action_type, metadata FROM audit_log ORDER BY rowid DESC LIMIT 1").fetchone()
    conn.close()
    assert row[0] == CORRECTION_ACTION_TYPE
    return json.loads(row[1])


# ---------------------------------------------------------------------------
# The gate
# ---------------------------------------------------------------------------


def test_capture_from_agent_uid_nongateway_pid_writes_a_row(tmp_path: Path) -> None:
    """The load-bearing case: an execute_code turn (agent uid, foreign PID)."""
    broker = _broker(tmp_path)
    resp = broker.handle(
        {"action": "correction_propose", "proposal": _proposal()},
        peer_pid=9999,
        peer_uid=AGENT_UID,
    )
    assert resp["ok"] is True
    assert resp["status"] == "proposed"
    assert isinstance(resp["id"], str) and len(resp["id"]) == 26  # ULID
    assert broker.ledger.count() == 1


def test_capture_rejected_from_foreign_uid(tmp_path: Path) -> None:
    broker = _broker(tmp_path)
    with pytest.raises(PermissionError):
        broker.handle(
            {"action": "correction_propose", "proposal": _proposal()},
            peer_pid=9999,
            peer_uid=AGENT_UID + 1,
        )
    assert broker.ledger.count() == 0


def test_capture_rejected_when_agent_uid_unresolved(tmp_path: Path) -> None:
    """agent_uid=None (pre-verb image / __new__ default) is fail-closed."""
    broker = _broker(tmp_path)
    broker.agent_uid = None
    broker.gateway_pid = 999999999  # the /proc stat fallback must also fail
    with pytest.raises(PermissionError):
        broker.handle(
            {"action": "correction_propose", "proposal": _proposal()},
            peer_pid=9999,
            peer_uid=AGENT_UID,
        )
    assert broker.ledger.count() == 0


def test_capture_rejected_when_peer_uid_missing(tmp_path: Path) -> None:
    """Two-arg handle() callers (legacy wire) cannot reach this verb."""
    broker = _broker(tmp_path)
    with pytest.raises(PermissionError):
        broker.handle(
            {"action": "correction_propose", "proposal": _proposal()},
            peer_pid=GATEWAY_PID,
        )
    assert broker.ledger.count() == 0


def test_capture_rejected_when_ledger_unconfigured(tmp_path: Path) -> None:
    broker = _broker(tmp_path)
    broker.ledger = None
    with pytest.raises(ValueError):
        broker.handle(
            {"action": "correction_propose", "proposal": _proposal()},
            peer_pid=9999,
            peer_uid=AGENT_UID,
        )


# ---------------------------------------------------------------------------
# status is a constant, not a field
# ---------------------------------------------------------------------------


def test_status_is_always_proposed(tmp_path: Path) -> None:
    broker = _broker(tmp_path)
    broker.handle(
        {"action": "correction_propose", "proposal": _proposal()},
        peer_pid=9999,
        peer_uid=AGENT_UID,
    )
    assert _stored_metadata(tmp_path)["status"] == "proposed"


@pytest.mark.parametrize("claimed", ["approved", "promoted", "applied", "", None])
def test_a_caller_supplied_status_is_ignored(tmp_path: Path, claimed) -> None:
    """The verb's whole point. A caller that names its own status is not
    rejected — it is simply not read, so no spelling of the field works."""
    broker = _broker(tmp_path)
    broker.handle(
        {"action": "correction_propose", "proposal": _proposal(status=claimed)},
        peer_pid=9999,
        peer_uid=AGENT_UID,
    )
    assert _stored_metadata(tmp_path)["status"] == "proposed"


def test_action_type_is_locked_to_correction_proposed(tmp_path: Path) -> None:
    """The verb cannot forge any other audit row."""
    broker = _broker(tmp_path)
    broker.handle(
        {"action": "correction_propose", "proposal": _proposal(action_type="REPLY_SENT")},
        peer_pid=9999,
        peer_uid=AGENT_UID,
    )
    conn = sqlite3.connect(str(tmp_path / "audit.db"))
    types = [r[0] for r in conn.execute("SELECT action_type FROM audit_log")]
    conn.close()
    assert types == [CORRECTION_ACTION_TYPE]


def test_unknown_fields_are_dropped_not_stored(tmp_path: Path) -> None:
    """The row is rebuilt from a bounded field set, so an invented field has
    nowhere to land — it is not an error, it is simply never looked for."""
    broker = _broker(tmp_path)
    broker.handle(
        {
            "action": "correction_propose",
            "proposal": _proposal(spec_path="/opt/data/specs/classes/x/voice.md"),
        },
        peer_pid=9999,
        peer_uid=AGENT_UID,
    )
    metadata = _stored_metadata(tmp_path)
    assert "spec_path" not in metadata
    assert set(metadata) == {
        "status",
        "output_class",
        "spec_property",
        "statement",
        "stated_by",
        "source_ref",
    }


# ---------------------------------------------------------------------------
# Broker-side validation
# ---------------------------------------------------------------------------


def test_proposal_must_be_an_object() -> None:
    for bad in ["client_email", ["client_email"], None, 7]:
        with pytest.raises(CorrectionValidationError):
            build_correction_row(bad)


def test_output_class_charset_is_enforced_not_sanitized() -> None:
    for bad in ["../smd", "Client_Email", "client email", "client/email", "a" * 65, ""]:
        with pytest.raises(CorrectionValidationError):
            build_correction_row(_proposal(output_class=bad))


def test_spec_property_is_closed() -> None:
    for bad in ["tone", "delivery", "VOICE", ""]:
        with pytest.raises(CorrectionValidationError):
            build_correction_row(_proposal(spec_property=bad))


def test_statement_is_required_and_bounded() -> None:
    with pytest.raises(CorrectionValidationError):
        build_correction_row(_proposal(statement="   "))
    with pytest.raises(CorrectionValidationError):
        build_correction_row(_proposal(statement="x" * 4001))


def test_optional_provenance_may_be_absent() -> None:
    row = build_correction_row(_proposal(stated_by=None, source_ref=None))
    metadata = json.loads(row["metadata"])
    assert metadata["stated_by"] is None
    assert metadata["source_ref"] is None


def test_capture_row_joins_the_hash_chain(tmp_path: Path) -> None:
    """A capture is an ordinary chained ledger row, not a side store — so it is
    as tamper-evident as everything else the seat records."""
    broker = _broker(tmp_path)
    broker.handle(
        {
            "action": "audit_append",
            "row": {"action_type": "TOOL_CALL_COMPLETED", "actor": "agent", "actor_role": "agent"},
        },
        peer_pid=GATEWAY_PID,
        peer_uid=AGENT_UID,
    )
    broker.handle(
        {"action": "correction_propose", "proposal": _proposal()},
        peer_pid=9999,
        peer_uid=AGENT_UID,
    )
    conn = sqlite3.connect(str(tmp_path / "audit.db"))
    rows = conn.execute("SELECT action_type, prev_hash, row_hash FROM audit_log ORDER BY rowid").fetchall()
    conn.close()
    assert [r[0] for r in rows] == ["TOOL_CALL_COMPLETED", CORRECTION_ACTION_TYPE]
    assert rows[1][1] == rows[0][2]
    assert rows[1][2] is not None
