"""Broker-side hash-chain tests (#1686): LedgerWriter chains every append,
upgrades legacy ledgers with an anchored first link, and the verifier catches
mutation and deletion on the real sqlite artifact."""

from __future__ import annotations

import sqlite3

from workspace_broker.audit_ledger import CHAIN_COLUMN_ALTERS, LedgerWriter
from workspace_broker.chain import (
    CHAIN_COLUMNS,
    GENESIS,
    legacy_anchor,
    verify_chain,
)

_LEGACY_CREATE = (
    "CREATE TABLE audit_log ("
    "id TEXT PRIMARY KEY, ts TEXT NOT NULL, action_type TEXT NOT NULL, "
    "actor TEXT NOT NULL, actor_role TEXT, skill_name TEXT, matter_ref TEXT, "
    "input_digest TEXT, output_digest TEXT, diff_digest TEXT, "
    "trust_ceiling TEXT, metadata TEXT)"
)


def _export(db_path: str) -> list[dict]:
    cols = [*CHAIN_COLUMNS, "prev_hash", "row_hash"]
    conn = sqlite3.connect(db_path)
    try:
        cur = conn.execute(f"SELECT {', '.join(cols)} FROM audit_log ORDER BY rowid")
        return [dict(zip(cols, r)) for r in cur.fetchall()]
    finally:
        conn.close()


def _append_n(writer: LedgerWriter, n: int) -> list[str]:
    return [writer.append({"action_type": "DRAFT_CREATED", "actor": "agent", "actor_role": "agent"}) for _ in range(n)]


def test_fresh_ledger_chains_from_genesis(tmp_path):
    db = str(tmp_path / "audit.db")
    writer = LedgerWriter(db)
    _append_n(writer, 4)
    rows = _export(db)
    assert rows[0]["prev_hash"] == GENESIS
    report = verify_chain(rows)
    assert report["ok"] is True and report["chained"] == 4 and report["legacy"] == 0


def test_legacy_ledger_upgrades_and_anchors(tmp_path):
    db = str(tmp_path / "audit.db")
    conn = sqlite3.connect(db)
    conn.execute(_LEGACY_CREATE)
    conn.execute(
        "INSERT INTO audit_log (id, ts, action_type, actor) VALUES (?, ?, ?, ?)",
        ("01JLEGACYROW", "2026-07-01T00:00:00.000Z", "AGENT_RESUMED", "agent"),
    )
    conn.commit()
    conn.close()

    writer = LedgerWriter(db)  # ensure_schema applies CHAIN_COLUMN_ALTERS
    _append_n(writer, 2)
    rows = _export(db)
    assert rows[0]["row_hash"] is None  # legacy row untouched
    assert rows[1]["prev_hash"] == legacy_anchor("01JLEGACYROW")
    report = verify_chain(rows)
    assert report["ok"] is True and report["legacy"] == 1 and report["chained"] == 2


def test_ensure_schema_is_idempotent_on_upgraded_ledger(tmp_path):
    db = str(tmp_path / "audit.db")
    LedgerWriter(db)
    LedgerWriter(db)  # second init must not raise on duplicate columns
    assert len(CHAIN_COLUMN_ALTERS) == 2


def test_mutated_row_breaks_verification(tmp_path):
    db = str(tmp_path / "audit.db")
    _append_n(LedgerWriter(db), 3)
    conn = sqlite3.connect(db)
    conn.execute("UPDATE audit_log SET actor = 'captain' WHERE rowid = 2")
    conn.commit()
    conn.close()
    report = verify_chain(_export(db))
    assert report["ok"] is False
    assert any("mutated" in b["reason"] for b in report["breaks"])


def test_deleted_row_breaks_verification(tmp_path):
    db = str(tmp_path / "audit.db")
    _append_n(LedgerWriter(db), 4)
    conn = sqlite3.connect(db)
    conn.execute("DELETE FROM audit_log WHERE rowid = 2")
    conn.commit()
    conn.close()
    report = verify_chain(_export(db))
    assert report["ok"] is False


def test_chain_survives_writer_restarts(tmp_path):
    db = str(tmp_path / "audit.db")
    _append_n(LedgerWriter(db), 2)
    _append_n(LedgerWriter(db), 2)  # fresh writer, same file — chain continues
    report = verify_chain(_export(db))
    assert report["ok"] is True and report["chained"] == 4
