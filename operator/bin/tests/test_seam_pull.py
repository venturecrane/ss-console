"""Tests for bin/lib/seam_pull.py (pull-before-destroy, #1355).

Locks four properties:

* the key derivation matches the provisioning derivation exactly
  (openssl dgst -sha256 -hmac over the bare slug);
* the preserver writes the full-column CSV AND the sqlite snapshot
  (audit_log + ADR-0016 memory tables) the evidence generator consumes;
* idempotent per UTC date (same contract as the stub it replaces);
* a transport failure RAISES — preservation must fail loud, never archive
  a partial pull as complete.
"""

from __future__ import annotations

import asyncio
import csv
import json
import sqlite3
import sys
from pathlib import Path
from typing import Optional

import pytest

_HERE = Path(__file__).resolve()
sys.path.insert(0, str(_HERE.parents[2]))

from bin.lib.seam_pull import (  # noqa: E402 - the import needs the sys.path shim above it (packaging follow-up named in pyproject.toml)
    AUDIT_COLUMNS,
    MEMORY_EXPORT_TABLES,
    SeamAuditLogPreserver,
    _write_memory_snapshot,
    derive_runtime_read_key,
    seam_client_from_env,
)


# ---------------------------------------------------------------------------
# Served column names are identifiers or the snapshot refuses
# ---------------------------------------------------------------------------


def test_memory_snapshot_refuses_non_identifier_column_names():
    conn = sqlite3.connect(":memory:")
    rows = [{"good": 1, 'evil") ; DROP TABLE x; --': 2, "_rowid": 9}]
    with pytest.raises(ValueError, match="non-identifier column names"):
        _write_memory_snapshot(conn, "memory_facts", rows)
    assert conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall() == []


def test_memory_snapshot_writes_identifier_columns_and_drops_rowid():
    conn = sqlite3.connect(":memory:")
    rows = [{"subject": "a", "value": "b", "_rowid": 1}, {"subject": "c", "value": "d", "_rowid": 2}]
    _write_memory_snapshot(conn, "memory_facts", rows)
    cols = [r[1] for r in conn.execute('PRAGMA table_info("memory_facts")')]
    assert cols == ["subject", "value"]
    assert conn.execute('SELECT COUNT(*) FROM "memory_facts"').fetchone()[0] == 2


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


# ---------------------------------------------------------------------------
# Key derivation
# ---------------------------------------------------------------------------


def test_derive_key_matches_openssl_vector():
    # Vector computed with the exact command provision-customer.sh runs:
    #   printf '%s' 'smd' | openssl dgst -sha256 -hmac 'master-secret'
    assert (
        derive_runtime_read_key("master-secret", "smd")
        == "7e620a81b93f1bca83a054cb00810dad6bf0f0373d198d5c6b5703b62686cbed"
    )


def test_seam_client_rejects_non_https_scheme():
    # urllib follows file:// and ftp://; a poisoned URL must die at
    # construction, never reach urlopen.
    from bin.lib.seam_pull import SeamClient

    for bad in ("file:///etc/passwd", "ftp://x", "http://hermes-smd.fly.dev"):
        with pytest.raises(ValueError, match="https"):
            SeamClient(base_url=bad, slug="smd", key="k" * 64)


def test_seam_client_from_env_requires_both_vars(monkeypatch):
    monkeypatch.delenv("OPERATOR_RUNTIME_READ_SECRET", raising=False)
    monkeypatch.delenv("OPERATOR_RUNTIME_READ_URL", raising=False)
    assert seam_client_from_env("smd") is None
    monkeypatch.setenv("OPERATOR_RUNTIME_READ_SECRET", "master")
    assert seam_client_from_env("smd") is None
    monkeypatch.setenv("OPERATOR_RUNTIME_READ_URL", "https://{app}.fly.dev")
    client = seam_client_from_env("smd")
    assert client is not None
    assert client._base == "https://hermes-smd.fly.dev"


# ---------------------------------------------------------------------------
# Preserver — fake seam client
# ---------------------------------------------------------------------------


class _FakeSeamClient:
    def __init__(self) -> None:
        self.audit_rows = [
            {
                "id": "01A",
                "ts": "2026-06-01T00:00:00Z",
                "action_type": "DRAFT_CREATED",
                "actor": "agent",
                "actor_role": "agent",
                "skill_name": "inbox-triage",
                "matter_ref": "m-1",
                "input_digest": "in1",
                "output_digest": "out1",
                "diff_digest": None,
                "trust_ceiling": "draft_for_review",
                "metadata": json.dumps({"k": "v"}),
            },
            {
                "id": "01B",
                "ts": "2026-06-02T00:00:00Z",
                "action_type": "LLM_TURN_COMPLETED",
                "actor": "agent",
                "actor_role": "agent",
                "skill_name": None,
                "matter_ref": None,
                "input_digest": None,
                "output_digest": None,
                "diff_digest": None,
                "trust_ceiling": None,
                "metadata": None,
            },
        ]
        self.memory_rows = {
            "persona_observations": [
                {"_rowid": 1, "observation_id": "obs-1", "content": "remembered thing"},
            ],
            "persona_observations_archive": [],
            "agent_skills_inventory": [
                {"_rowid": 1, "skill_name": "follow-up-cadence", "status": "persisted"},
            ],
            "peer_preferences": [
                {"_rowid": 1, "peer_id": "chris", "preference": "Wants bullets", "source": "stated"},
            ],
        }

    def read_all(self, kind: str, *, table: Optional[str] = None) -> list[dict]:
        if kind == "audit_export":
            return list(self.audit_rows)
        if kind == "memory_export":
            return list(self.memory_rows[table])
        raise AssertionError(f"unexpected kind {kind}")


def test_preserver_writes_csv_snapshot_and_manifest(tmp_path):
    preserver = SeamAuditLogPreserver(_FakeSeamClient())
    archive_dir = tmp_path / "archive" / "smd"
    result = _run(preserver.preserve("smd", archive_dir, 2555))

    assert result["skipped"] is False
    assert result["stub"] is False
    assert result["rows_preserved"] == 2
    assert result["memory_rows_preserved"]["persona_observations"] == 1
    assert result["memory_rows_preserved"]["agent_skills_inventory"] == 1
    assert result["memory_rows_preserved"]["peer_preferences"] == 1

    # CSV: full canonical columns, both rows.
    with Path(result["csv_path"]).open(encoding="utf-8") as fp:
        rows = list(csv.reader(fp))
    assert rows[0] == list(AUDIT_COLUMNS)
    assert len(rows) == 3
    assert rows[1][0] == "01A"
    assert rows[1][7] == "in1"  # input_digest IS preserved

    # Snapshot sqlite: audit_log + memory tables materialized.
    conn = sqlite3.connect(result["snapshot_path"])
    try:
        n_audit = conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0]
        assert n_audit == 2
        obs = conn.execute("SELECT observation_id, content FROM persona_observations").fetchall()
        assert obs == [("obs-1", "remembered thing")]
        skills = conn.execute("SELECT skill_name FROM agent_skills_inventory").fetchall()
        assert skills == [("follow-up-cadence",)]
    finally:
        conn.close()

    manifest = json.loads(Path(result["archive_path"]).read_text(encoding="utf-8"))
    assert manifest["rows_preserved"] == 2
    assert manifest["source"].startswith("adr-0043")


def test_preserver_idempotent_same_day(tmp_path):
    preserver = SeamAuditLogPreserver(_FakeSeamClient())
    archive_dir = tmp_path / "archive" / "smd"
    first = _run(preserver.preserve("smd", archive_dir, 2555))
    second = _run(preserver.preserve("smd", archive_dir, 2555))
    assert first["skipped"] is False
    assert second["skipped"] is True
    assert second["reason"] == "audit_log_already_preserved_today"


def test_preserver_raises_on_transport_failure(tmp_path):
    # Pull-before-destroy is only safe if a failed pull HALTS the pipeline.
    class _BrokenClient:
        def read_all(self, kind: str, *, table: Optional[str] = None) -> list[dict]:
            raise OSError("connection refused")

    preserver = SeamAuditLogPreserver(_BrokenClient())
    with pytest.raises(OSError):
        _run(preserver.preserve("smd", tmp_path / "archive" / "smd", 2555))
    # No manifest may exist after a failure — a rerun must re-attempt.
    assert not list((tmp_path / "archive" / "smd").glob("audit-log-manifest-*.json"))


def test_memory_export_tables_match_overlay_allowlist():
    # Mirror of the overlay's MEMORY_EXPORT_TABLES (shared/runtime_read.py).
    # If a table is added on the Machine side, the preserver must learn it
    # in the same wave or the snapshot silently loses a table.
    assert set(MEMORY_EXPORT_TABLES) == {
        "persona_observations",
        "persona_observations_archive",
        "agent_skills_inventory",
        "peer_preferences",
    }
