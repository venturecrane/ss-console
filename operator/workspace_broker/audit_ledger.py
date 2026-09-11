"""Append-only audit ledger writer for the capability broker (OP-P1-4).

The broker process (uid ``workspace-broker``) holds the ONLY read-write handle
on the per-customer audit ledger file. The agent uid can read the file (via the
``audit-readers`` group) but cannot open it for write, so the only path a row
reaches the ledger is an ``audit_append`` request to the broker. This module is
that writer.

Append-only is structural, not a SQL filter: this writer exposes ``append`` and
nothing else. There is no update/delete/drop method, and the broker's IPC
surface has no verb that reaches one. The broker stamps ``id``/``ts``
server-side so the agent cannot backdate or collide rows.

CONTRACT: the column set and CREATE below MUST stay in lockstep with the
overlay's ``shared/audit_contract.py`` (COLUMNS / CREATE_TABLE_SQL). The
overlay's ``BrokerAuditClient`` sends a ``row`` keyed by the agent-supplied
columns (COLUMNS minus id/ts). A mismatch surfaces immediately at the boot-time
probe-append + read-back gate (bootstrap.sh), not silently in production.
"""

from __future__ import annotations

import logging
import os
import secrets
import sqlite3
import time
from datetime import UTC, datetime

from workspace_broker.chain import GENESIS, compute_row_hash, legacy_anchor

logger = logging.getLogger(__name__)


def _current_umask() -> int:
    """Read the process umask without permanently changing it."""
    value = os.umask(0o022)
    os.umask(value)
    return value


# Agent-supplied columns: the overlay COLUMNS tuple minus the leading id/ts,
# which the broker stamps. Order here only governs the INSERT this module
# builds; the wire payload is a name-keyed dict, so column *ordering* cannot
# drift the two repos — only the column *set* is the contract.
_AGENT_COLUMNS: tuple[str, ...] = (
    "action_type",
    "actor",
    "actor_role",
    "skill_name",
    "matter_ref",
    "input_digest",
    "output_digest",
    "diff_digest",
    "trust_ceiling",
    "metadata",
)
_ALL_COLUMNS: tuple[str, ...] = ("id", "ts", *_AGENT_COLUMNS)

# Exact copy of overlay shared/audit_contract.py CREATE_TABLE_SQL. Immutability
# is enforced here structurally (no update/delete verb) rather than by triggers.
CREATE_TABLE_SQL = (
    "CREATE TABLE IF NOT EXISTS audit_log ("
    "id TEXT PRIMARY KEY, "
    "ts TEXT NOT NULL, "
    "action_type TEXT NOT NULL, "
    "actor TEXT NOT NULL, "
    "actor_role TEXT, "
    "skill_name TEXT, "
    "matter_ref TEXT, "
    "input_digest TEXT, "
    "output_digest TEXT, "
    "diff_digest TEXT, "
    "trust_ceiling TEXT, "
    "metadata TEXT, "
    "prev_hash TEXT, "
    "row_hash TEXT"
    ")"
)

# Hash-chain upgrade for pre-#1686 ledgers: applied at ensure_schema, each
# tolerated when the column already exists. Chain semantics live in chain.py
# (a byte-identical twin of the overlay's shared/audit_chain.py, tracked in
# operator/contracts/overlay-pairs.json).
CHAIN_COLUMN_ALTERS: tuple[str, ...] = (
    "ALTER TABLE audit_log ADD COLUMN prev_hash TEXT",
    "ALTER TABLE audit_log ADD COLUMN row_hash TEXT",
)
CREATE_INDEX_SQL: tuple[str, ...] = (
    "CREATE INDEX IF NOT EXISTS idx_audit_ts ON audit_log(ts)",
    "CREATE INDEX IF NOT EXISTS idx_audit_action_type ON audit_log(action_type, ts)",
    "CREATE INDEX IF NOT EXISTS idx_audit_actor ON audit_log(actor, ts)",
)

_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def _encode_crockford(value: int, length: int) -> str:
    out: list[str] = []
    for _ in range(length):
        value, rem = divmod(value, 32)
        out.append(_CROCKFORD[rem])
    return "".join(reversed(out))


def _ulid() -> str:
    """26-char Crockford ULID — matches overlay shared/ids.ulid()."""
    ts = int(time.time() * 1000)
    return _encode_crockford(ts, 10) + _encode_crockford(secrets.randbits(80), 16)


def new_row_token() -> str:
    """A ULID minted for a row that does not exist yet (ss#2499).

    ``append`` mints a row's own id at write time, and a transmit row is written
    AFTER the send it records. So a header that has to travel WITH the message —
    ``X-SMD-Audit-Row``, the exact key the console-side reconciler joins on —
    cannot carry the row id: the row does not have one yet. Pre-minting the id
    instead would mean teaching ``append`` to accept a caller-supplied id, which
    is the hash-chain seam (``chain.py`` is a byte-identical overlay twin) and
    not a place to spend risk for a naming convenience.

    This is that id in every way that matters to the join: the same generator,
    the same alphabet, minted once per transmit, and written onto the row it
    belongs to as ``audit_row_token``. One token, one row, both directions.
    """
    return _ulid()


def _iso_utc() -> str:
    dt = datetime.now(UTC)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"


class LedgerWriter:
    """Append-only writer over a per-operation sqlite connection.

    Each append/count opens a FRESH connection in the calling thread and closes
    it. This is deliberate: a single long-lived connection opened at broker
    startup and written from a ``ThreadingUnixStreamServer`` worker thread ~50s
    later was observed to fail ``SQLITE_READONLY`` on customer-zero staging even
    though the file/dir were owner-writable and a fresh connection wrote fine.
    Opening at write-time, in the writing thread, matches the proven path and
    sidesteps any stale-pager state. Rollback-journal mode (``journal_mode=
    DELETE``, NOT WAL) keeps the agent-uid mode=ro read seam off the cross-uid
    ``-wal``/``-shm`` surface; ``busy_timeout`` serializes concurrent writers.
    Audit write rate is ~1/turn, so per-op connections cost nothing material.
    """

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        # Startup diagnostic — the broker's OWN view of ledger writability, so a
        # future failure is debuggable from `fly logs` without root-SSH.
        ledger_dir = os.path.dirname(db_path) or "."
        logger.info(
            "audit ledger init: path=%s dir_w_ok=%s file_w_ok=%s umask=%03o",
            db_path,
            os.access(ledger_dir, os.W_OK),
            os.access(db_path, os.W_OK) if os.path.exists(db_path) else "absent",
            _current_umask(),
        )
        self.ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path, timeout=5.0)
        conn.execute("PRAGMA journal_mode=DELETE")
        conn.execute("PRAGMA busy_timeout=5000")
        return conn

    def ensure_schema(self) -> None:
        conn = self._connect()
        try:
            conn.execute(CREATE_TABLE_SQL)
            for index_sql in CREATE_INDEX_SQL:
                conn.execute(index_sql)
            for alter_sql in CHAIN_COLUMN_ALTERS:
                try:
                    conn.execute(alter_sql)
                except sqlite3.OperationalError as err:
                    if "duplicate column" not in str(err):
                        raise
            conn.commit()
        finally:
            conn.close()

    def append(self, row: dict) -> str:
        """Insert one audit row. Returns the broker-stamped ULID.

        Raises:
            ValueError: row carries an unknown column, or no action_type.
        """
        unknown = set(row) - set(_AGENT_COLUMNS)
        if unknown:
            raise ValueError(f"audit_append: unknown column(s) {sorted(unknown)}")
        action_type = row.get("action_type")
        if not isinstance(action_type, str) or not action_type.strip():
            raise ValueError("audit_append: a non-empty action_type is required")
        row_id = _ulid()
        contract_values = [row_id, _iso_utc(), *(row.get(col) for col in _AGENT_COLUMNS)]
        sql = (
            "INSERT INTO audit_log (" + ", ".join(_ALL_COLUMNS) + ", prev_hash, row_hash) "
            "VALUES (" + ", ".join("?" for _ in _ALL_COLUMNS) + ", ?, ?)"
        )
        conn = self._connect()
        try:
            # BEGIN IMMEDIATE serializes tail-read + insert against any other
            # writer (there is only this broker, but the lock makes the chain
            # correct by construction, not by deployment assumption).
            conn.execute("BEGIN IMMEDIATE")
            tail = conn.execute("SELECT id, row_hash FROM audit_log ORDER BY rowid DESC LIMIT 1").fetchone()
            if tail is None:
                prev_hash = GENESIS
            elif tail[1] is not None:
                prev_hash = str(tail[1])
            else:
                # Ledger predates the chain: anchor to the legacy tail so
                # deleting pre-chain rows after the upgrade is detectable.
                prev_hash = legacy_anchor(str(tail[0]))
            row_hash = compute_row_hash(prev_hash, contract_values)
            conn.execute(sql, [*contract_values, prev_hash, row_hash])
            conn.commit()
        finally:
            conn.close()
        return row_id

    def count(self) -> int:
        conn = self._connect()
        try:
            return int(conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0])
        finally:
            conn.close()
