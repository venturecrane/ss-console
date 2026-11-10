"""The hourly triage lists a job whose usage shows spend while its row shows $0.

The seat's jobs daemon finds such a job (``runners/jobs/jobs/spend.py``) and
raises one ``spend_unrecorded`` proof alert; the broker writes it to the seat's
audit ledger as a DELIVERY_PROOF_FAILED row, which work-health reads. Here the
row is written by the REAL broker (``delivery_proof.append_alert`` into a real
``LedgerWriter``) and read back the way the seam serves it.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from bin.lib import work_health as wh
from bin.lib import work_health_sources as src
from workspace_broker import delivery_proof
from workspace_broker.audit_ledger import LedgerWriter
from workspace_broker.server import Broker

CFG = wh.load_config()
SEAT = "example-seat"
JOB = "01M73RVKPTYZP2A1CF3R5P0ERW"


def _seat(tmp_path: Path) -> Broker:
    broker = Broker.__new__(Broker)
    broker.ledger = LedgerWriter(str(tmp_path / "audit.db"))
    return broker


def _rows(tmp_path: Path) -> list[dict]:
    conn = sqlite3.connect(str(tmp_path / "audit.db"))
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute("SELECT id, ts, action_type, skill_name, metadata FROM audit_log")]
    finally:
        conn.close()


def _alert(broker: Broker, code: str, detail: str) -> None:
    delivery_proof.append_alert(broker, lane="litigation", job_id=JOB, code=code, attempt=1, detail=detail)


def test_a_planted_spend_alert_is_listed_as_money_not_delivery(tmp_path):
    """MUTATION: drop the code's own wording and the line says NOT proven delivered."""
    broker = _seat(tmp_path)
    _alert(broker, "spend_unrecorded", f"litigation job {JOB}: usage 500 cents, recorded 0 cents")
    now = datetime.now(timezone.utc) + timedelta(minutes=1)
    items = src.delivery_proof_items(SEAT, _rows(tmp_path), CFG, now)
    assert len(items) == 1
    it = items[0]
    assert it.kind == "job_failed" and it.key.endswith(f"proof:{JOB}:spend_unrecorded")
    assert "record shows $0 while its usage shows $5.00" in it.what
    assert "NOT proven delivered" not in it.what


def test_a_seat_with_no_spend_alert_lists_no_spend_line(tmp_path):
    broker = _seat(tmp_path)
    _alert(broker, "job_stopped", "failed (failed): gate_refused: x; the requester was not told")
    now = datetime.now(timezone.utc) + timedelta(minutes=1)
    items = src.delivery_proof_items(SEAT, _rows(tmp_path), CFG, now)
    assert [i for i in items if "spend_unrecorded" in i.key or "usage shows" in i.what] == []


def test_the_code_is_one_the_broker_accepts():
    assert "spend_unrecorded" in delivery_proof.CODES
