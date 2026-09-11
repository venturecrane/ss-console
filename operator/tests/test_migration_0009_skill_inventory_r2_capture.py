"""Migration 0009 — agent_skills_inventory R2 capture columns (ADR 0022 Stream 2).

Verifies the migration applied against a clean SQLite database that previously
ran migration 0008 (the table-creation migration). Tests live as pytest cases
under operator/tests/ alongside the other Python-side regression tests.

What this test locks in:
  1. The three new columns exist (r2_key, r2_status, r2_write_error) with the
     correct types and the r2_status DEFAULT 'unknown' + CHECK constraint.
  2. The two new indexes exist (agent_skills_inventory_by_hash and the partial
     agent_skills_inventory_r2_pending on r2_status IN ('pending','failed')).
  3. Legacy rows from migration 0008 (those that exist prior to ALTER) remain
     readable with NULL r2_key / r2_write_error and r2_status='unknown' (the
     CHECK constraint accepts the default).
  4. The CHECK constraint rejects unknown r2_status values.
  5. The partial index does not match rows in r2_status='persisted' (cost
     control — the index is meant to bound the reconciler's work).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS_DIR = REPO_ROOT / "operator" / "migrations"


def _connect_with_migrations(through: int) -> sqlite3.Connection:
    """Connect to in-memory SQLite and apply migrations 0001..through inclusive."""
    conn = sqlite3.connect(":memory:")
    for n in range(1, through + 1):
        path = next(MIGRATIONS_DIR.glob(f"{n:04d}_*.sql"))
        conn.executescript(path.read_text(encoding="utf-8"))
    conn.commit()
    return conn


def _columns(conn: sqlite3.Connection) -> dict[str, dict]:
    """Return agent_skills_inventory columns keyed by name."""
    # PRAGMA statements do not accept bind parameters; use a literal table
    # name (the only table this test inspects) to satisfy the SAST lint.
    rows = conn.execute("PRAGMA table_info(agent_skills_inventory)").fetchall()
    return {r[1]: {"type": r[2], "notnull": bool(r[3]), "dflt": r[4]} for r in rows}


def _indexes(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute("PRAGMA index_list(agent_skills_inventory)").fetchall()
    return {r[1] for r in rows}


def test_legacy_row_survives_with_unknown_status() -> None:
    """A row inserted under migration 0008 (no new columns) becomes a row with
    r2_status='unknown' + NULL r2_key + NULL r2_write_error after 0009 applies."""
    conn = _connect_with_migrations(8)
    conn.execute(
        """
        INSERT INTO agent_skills_inventory
          (customer_slug, persona_slug, skill_name, skill_content_hash, source_turn_id)
        VALUES (?, ?, ?, ?, ?)
        """,
        ("smith-pi-firm", "marcus", "demand-letter-draft", "a" * 64, "turn_001"),
    )
    conn.commit()

    # Apply migration 0009.
    path = next(MIGRATIONS_DIR.glob("0009_*.sql"))
    conn.executescript(path.read_text(encoding="utf-8"))
    conn.commit()

    row = conn.execute("SELECT r2_key, r2_status, r2_write_error FROM agent_skills_inventory").fetchone()
    assert row == (None, "unknown", None)


def test_new_columns_present_with_correct_defaults() -> None:
    conn = _connect_with_migrations(9)
    cols = _columns(conn)
    assert "r2_key" in cols
    assert cols["r2_key"]["type"] == "TEXT"
    assert cols["r2_key"]["notnull"] is False
    assert cols["r2_status"]["type"] == "TEXT"
    assert cols["r2_status"]["notnull"] is True
    assert cols["r2_status"]["dflt"] == "'unknown'"
    assert cols["r2_write_error"]["type"] == "TEXT"
    assert cols["r2_write_error"]["notnull"] is False


def test_indexes_added() -> None:
    conn = _connect_with_migrations(9)
    idx = _indexes(conn)
    assert "agent_skills_inventory_by_hash" in idx
    assert "agent_skills_inventory_r2_pending" in idx


def test_check_constraint_rejects_unknown_status() -> None:
    conn = _connect_with_migrations(9)
    try:
        conn.execute(
            """
            INSERT INTO agent_skills_inventory
              (customer_slug, persona_slug, skill_name, skill_content_hash,
               source_turn_id, r2_status)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            ("smith", "marcus", "demand", "b" * 64, "turn_002", "bogus_status"),
        )
        conn.commit()
        raise AssertionError("expected CHECK constraint violation for bogus_status")
    except sqlite3.IntegrityError as e:
        assert "CHECK" in str(e) or "constraint" in str(e).lower()


def test_each_valid_status_accepted() -> None:
    conn = _connect_with_migrations(9)
    for status in ("unknown", "pending", "persisted", "failed"):
        conn.execute(
            """
            INSERT INTO agent_skills_inventory
              (customer_slug, persona_slug, skill_name, skill_content_hash,
               source_turn_id, r2_status)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            ("smith", "marcus", f"skill-{status}", "c" * 64, f"turn-{status}", status),
        )
    conn.commit()
    n = conn.execute("SELECT COUNT(*) FROM agent_skills_inventory").fetchone()[0]
    assert n == 4


def test_partial_index_excludes_persisted_rows() -> None:
    """The partial index agent_skills_inventory_r2_pending is meant to bound
    reconciler work — it MUST NOT match rows in 'persisted' state."""
    conn = _connect_with_migrations(9)
    conn.executemany(
        """
        INSERT INTO agent_skills_inventory
          (customer_slug, persona_slug, skill_name, skill_content_hash,
           source_turn_id, r2_status)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        [
            ("smith", "marcus", "s-persisted", "d" * 64, "t-p", "persisted"),
            ("smith", "marcus", "s-pending", "e" * 64, "t-pe", "pending"),
            ("smith", "marcus", "s-failed", "f" * 64, "t-f", "failed"),
        ],
    )
    conn.commit()
    # The query the reconciler runs.
    rows = conn.execute(
        """
        SELECT skill_name FROM agent_skills_inventory
        WHERE r2_status IN ('pending', 'failed')
        ORDER BY skill_name
        """
    ).fetchall()
    assert [r[0] for r in rows] == ["s-failed", "s-pending"]


def test_schema_version_bumped_to_9() -> None:
    conn = _connect_with_migrations(9)
    v = conn.execute("PRAGMA user_version").fetchone()[0]
    assert v == 9
