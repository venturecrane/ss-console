"""The hourly triage lists a chronology ending the broker did not take as its
own line, in the page's own sentence, until an ending record for the job lands.

The seat's lane pages ``ending_unrecorded`` (``runners/jobs/jobs/medchron/
unrecorded.py``) past its retry bound and at the wipe. It is not a proof
failure: the job may well have delivered, and "NOT proven delivered" said of it
would be false and would never close. Here the page is written by the REAL
broker (``delivery_proof.append_alert`` into a real ``LedgerWriter``).
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from bin.lib import work_health as wh
from bin.lib import work_health_proof as proof
from workspace_broker import delivery_proof
from workspace_broker.audit_ledger import LedgerWriter
from workspace_broker.server import Broker

CFG = wh.load_config()
SEAT = "example-seat"
JOB = "01M73RVKPTYZP2A1CF3R5P0ERW"
SAID = (
    "its delivered ending is not recorded after 10 tries in 9 min (internal_error); its wake was owed and "
    "went (the firm is told it was delivered); its row says running, so the same work is refused as "
    "already running until a retry lands it"
)


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
    delivery_proof.append_alert(broker, lane="medchron", job_id=JOB, code=code, attempt=1, detail=detail)


def _ended(at: datetime, state: str = "DELIVERED") -> dict:
    ts = at.strftime("%Y-%m-%dT%H:%M:%S.000Z")
    return {"id": 10**6, "ts": ts, "action_type": f"MEDCHRON_JOB_{state}", "metadata": json.dumps({"job_id": JOB})}


def test_an_unrecorded_ending_is_its_own_line_in_the_pages_sentence(tmp_path):
    """MUTATION: put ``ending_unrecorded`` back among the proof words and the
    line says NOT proven delivered of a job that delivered."""
    broker = _seat(tmp_path)
    _alert(broker, "ending_unrecorded", SAID)
    now = datetime.now(timezone.utc) + timedelta(minutes=1)
    (it,) = proof.delivery_proof_items(SEAT, _rows(tmp_path), CFG, now)
    assert it.key.endswith(f"proof:{JOB}:ending_unrecorded")
    assert "ending is not in its record" in it.what and SAID in it.what
    assert "NOT proven delivered" not in it.what


def test_it_closes_when_an_ending_record_lands(tmp_path):
    broker = _seat(tmp_path)
    _alert(broker, "ending_unrecorded", SAID)
    now = datetime.now(timezone.utc) + timedelta(minutes=5)
    for state in ("DELIVERED", "FAILED", "HELD"):
        rows = _rows(tmp_path) + [_ended(now - timedelta(minutes=1), state)]
        assert proof.delivery_proof_items(SEAT, rows, CFG, now) == [], state
    # An ending record from BEFORE the page does not close it.
    rows = _rows(tmp_path) + [_ended(now - timedelta(hours=2))]
    assert len(proof.delivery_proof_items(SEAT, rows, CFG, now)) == 1


def test_it_never_hides_a_real_proof_failure_of_the_same_job(tmp_path):
    broker = _seat(tmp_path)
    _alert(broker, "readback_mismatch", "the files on the matter differ")
    _alert(broker, "ending_unrecorded", SAID)
    now = datetime.now(timezone.utc) + timedelta(minutes=1)
    keys = sorted(i.key.rsplit(":", 1)[1] for i in proof.delivery_proof_items(SEAT, _rows(tmp_path), CFG, now))
    assert keys == ["ending_unrecorded", "readback_mismatch"]
