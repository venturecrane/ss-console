"""Broker-owned job ledger writer for the durable task-execution substrate (B1).

The job ledger is the *control plane* for long-running background jobs: one
mutable `jobs` row per job plus an `idempotency_keys` table that guards
side-effecting steps against replay. The agent's *conversation* lives in
Hermes `state.db` (append-only lineage); this ledger holds only the control
facts `state.db` cannot — status, the rotating session tip, lease/fencing,
cost, delivery target, and the result reference. See ADR 0051 +
docs/design/operator/durable-task-execution-substrate.md.

WHY broker-owned, like the audit ledger: only the broker uid
(``workspace-broker``) holds a read-write handle; the agent uid can read (via
the ``audit-readers`` group) but the ONLY write path is a gateway-PID-gated
verb on the broker socket (``server.py`` checks ``peer_pid == gateway_pid``
before any job verb). So an ``execute_code`` child — a different peer PID —
cannot claim a lease, raise a budget, mark a step done, or flip a status. The
agent cannot rewrite its own job control state any more than it can rewrite
its own audit log.

WHY this differs from ``audit_ledger.LedgerWriter``: the audit log is
append-only (the immutability guarantee is the *absence* of any update/delete
verb). The job ledger is intrinsically MUTABLE — leases are claimed, spend
accrues, status advances. We keep that mutation safe two ways: (1) every
privileged mutation is **epoch-fenced** — it carries the ``lease_epoch`` minted
at claim time and the UPDATE's WHERE clause rejects a stale epoch, so a
respawned-but-not-dead predecessor's writes become no-ops; (2) every transition
is *also* mirrored to the append-only audit log by the caller (mirror, don't
gate — ADR 0016), so the tamper-evident record is independent of this mutable
table.

The job tables share the audit DB *file* (one bind-mounted store, one uid
boundary — "one less thing to own", per the design's build-vs-leverage pass).
The audit_log append-only guarantee is unaffected: no verb here touches
``audit_log``; these verbs touch only ``jobs`` / ``idempotency_keys``.
"""

from __future__ import annotations

import logging
import secrets
import sqlite3
import time
from datetime import UTC, datetime, timedelta

from .audit_ledger import _encode_crockford, _iso_utc  # format in lockstep


def _mint_id_and_stamp() -> tuple[str, str]:
    """A job's ULID and its ``created_at``, from ONE clock read.

    ``list_all`` orders ``created_at DESC, id DESC``, so the two values form a
    composite sort key and must agree about when the row was made. Minting them
    from separate clock reads (``_ulid()`` then ``_iso_utc()``) lets the pair
    straddle a millisecond boundary: ``created_at`` says this row came second
    while its ULID timestamp says it came first, and the composite order stops
    matching creation order.

    Not theoretical. It flaked the ``substrate`` merge gate on PR #2484
    (ss#2486) with a failure in a file that PR did not touch, and it passed on
    re-run: the shape that teaches everyone to hit re-run without reading, which
    is how a real failure gets waved through.

    One read, both values derived from it, so the pair cannot disagree. The
    encoder and the timestamp format stay the audit ledger's on purpose, since
    the two ledgers share a DB file and their id/timestamp formats travel in
    lockstep.
    """
    ms = int(time.time() * 1000)
    dt = datetime.fromtimestamp(ms / 1000, UTC)
    job_id = _encode_crockford(ms, 10) + _encode_crockford(secrets.randbits(80), 16)
    stamp = dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"
    return job_id, stamp


logger = logging.getLogger(__name__)

# A lease older than this (relative to the broker's clock) is reclaimable. The
# broker — one process per Machine — is the single clock of record for lease
# timing, so callers never supply time over the wire (a worker can't lie its
# lease alive). Set generously above a worst-case segment's wall-clock so a
# live-but-busy worker is not stolen from; the worker heartbeats well inside it.
LEASE_TTL_SECONDS = 900


def _fmt_iso(dt: datetime) -> str:
    """Format a datetime exactly like ``audit_ledger._iso_utc`` (ms + Z) so
    job timestamps sort against each other and against audit rows."""
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"


def now_and_lease_cutoff(ttl_seconds: int = LEASE_TTL_SECONDS) -> tuple[str, str]:
    """Return ``(now_iso, expiry_cutoff_iso)`` from the broker's clock. A job
    whose ``lease_ts < expiry_cutoff_iso`` is reclaimable."""
    now = datetime.now(UTC)
    return _fmt_iso(now), _fmt_iso(now - timedelta(seconds=ttl_seconds))


# Terminal statuses never re-claimed by the boot-sweep or a claim attempt.
TERMINAL_STATUSES: frozenset[str] = frozenset({"delivered", "done", "needs_review", "cancelled"})
# Non-terminal statuses the boot-sweep / claim path may pick up.
CLAIMABLE_STATUSES: tuple[str, ...] = ("queued", "running", "complete", "delivering")

# Columns a caller may set at job creation. id/created_at/updated_at/lease_*/
# spent_cents/attempts/cancel_requested/result_ref/error are stamped or owned
# by the ledger, never supplied at create.
_CREATE_COLUMNS: tuple[str, ...] = (
    "customer_slug",
    "persona_id",
    "model",
    "brief",
    "brief_digest",
    "deliver_to",
    "budget_cents",
    "root_session_id",
)
# Fields an epoch-fenced ``record`` may mutate. Deliberately NOT lease_*,
# budget_cents, attempts, or the immutable identity columns.
_RECORD_COLUMNS: frozenset[str] = frozenset({"status", "current_tip_session_id", "spent_cents", "result_ref", "error"})

CREATE_JOBS_SQL = (
    "CREATE TABLE IF NOT EXISTS jobs ("
    "id TEXT PRIMARY KEY, "
    "created_at TEXT NOT NULL, "
    "updated_at TEXT NOT NULL, "
    "customer_slug TEXT NOT NULL, "
    "persona_id TEXT NOT NULL, "
    "model TEXT, "
    "brief TEXT NOT NULL, "
    "brief_digest TEXT, "
    "status TEXT NOT NULL DEFAULT 'queued', "
    "root_session_id TEXT, "
    "current_tip_session_id TEXT, "
    "deliver_to TEXT, "
    "lease_owner TEXT, "
    "lease_epoch INTEGER NOT NULL DEFAULT 0, "
    "lease_ts TEXT, "
    "attempts INTEGER NOT NULL DEFAULT 0, "
    "budget_cents INTEGER NOT NULL, "
    "spent_cents INTEGER NOT NULL DEFAULT 0, "
    "cancel_requested INTEGER NOT NULL DEFAULT 0, "
    "result_ref TEXT, "
    "error TEXT"
    ")"
)
# step_key is the LOGICAL effect (action+target+stable content id), not a
# payload hash — so a re-run that regenerates a different payload still dedupes.
CREATE_IDEMPOTENCY_SQL = (
    "CREATE TABLE IF NOT EXISTS idempotency_keys ("
    "job_id TEXT NOT NULL, "
    "step_key TEXT NOT NULL, "
    "state TEXT NOT NULL, "  # 'in_progress' | 'done'
    "lease_epoch INTEGER NOT NULL, "
    "created_at TEXT NOT NULL, "
    "updated_at TEXT NOT NULL, "
    "PRIMARY KEY (job_id, step_key)"
    ")"
)
CREATE_INDEX_SQL: tuple[str, ...] = (
    # Boot-sweep / claim scan: non-terminal jobs, oldest first.
    "CREATE INDEX IF NOT EXISTS idx_jobs_claimable ON jobs(updated_at) "
    "WHERE status NOT IN ('delivered','done','needs_review','cancelled')",
    "CREATE INDEX IF NOT EXISTS idx_jobs_customer ON jobs(customer_slug, created_at)",
)

_ALL_JOB_COLUMNS: tuple[str, ...] = (
    "id",
    "created_at",
    "updated_at",
    "customer_slug",
    "persona_id",
    "model",
    "brief",
    "brief_digest",
    "status",
    "root_session_id",
    "current_tip_session_id",
    "deliver_to",
    "lease_owner",
    "lease_epoch",
    "lease_ts",
    "attempts",
    "budget_cents",
    "spent_cents",
    "cancel_requested",
    "result_ref",
    "error",
)


class JobLedgerWriter:
    """Mutable, epoch-fenced control-plane writer over a per-operation sqlite
    connection.

    Connection discipline matches ``audit_ledger.LedgerWriter`` deliberately:
    a FRESH connection per operation in the calling thread, ``journal_mode=
    DELETE`` (keeps the agent-uid mode=ro read seam off the cross-uid -wal/-shm
    surface), ``busy_timeout`` to serialize concurrent writers. The job write
    rate is low (a handful per segment), so per-op connections cost nothing
    material and sidestep the stale-pager SQLITE_READONLY failure mode observed
    with a long-lived broker connection.
    """

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        self.ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path, timeout=5.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=DELETE")
        conn.execute("PRAGMA busy_timeout=5000")
        return conn

    def ensure_schema(self) -> None:
        conn = self._connect()
        try:
            conn.execute(CREATE_JOBS_SQL)
            conn.execute(CREATE_IDEMPOTENCY_SQL)
            for index_sql in CREATE_INDEX_SQL:
                conn.execute(index_sql)
            conn.commit()
        finally:
            conn.close()

    # -- intake ------------------------------------------------------------

    def create(self, row: dict) -> str:
        """Create a queued job from caller-supplied create columns. Returns the
        broker-stamped ULID. Identity/lease/cost-progress columns are owned by
        the ledger and cannot be supplied here.

        Raises ValueError on unknown/missing columns.
        """
        unknown = set(row) - set(_CREATE_COLUMNS)
        if unknown:
            raise ValueError(f"job create: unknown column(s) {sorted(unknown)}")
        for required in ("customer_slug", "persona_id", "brief", "budget_cents"):
            if row.get(required) in (None, ""):
                raise ValueError(f"job create: '{required}' is required")
        job_id, now = _mint_id_and_stamp()
        cols = ("id", "created_at", "updated_at", *_CREATE_COLUMNS)
        vals = [job_id, now, now, *(row.get(c) for c in _CREATE_COLUMNS)]
        sql = "INSERT INTO jobs (" + ", ".join(cols) + ") VALUES (" + ", ".join("?" for _ in cols) + ")"  # noqa: S608 - cols is a tuple of _CREATE_COLUMNS constants, checked above; values are bound
        conn = self._connect()
        try:
            conn.execute(sql, vals)
            conn.commit()
        finally:
            conn.close()
        return job_id

    # -- read --------------------------------------------------------------
    def read(self, job_id: str) -> dict | None:
        conn = self._connect()
        try:
            r = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
            return dict(r) if r is not None else None
        finally:
            conn.close()

    def list_all(self) -> list[dict]:
        """Every job row, newest-created first. Powers the observability seam
        (the console's ``jobs`` runtime-read kind) — unlike ``list_claimable``,
        it includes terminal and live-leased jobs and applies no lease filter,
        so an operator surface can show the full job history. Read-only: opens
        the same per-op connection as every other read."""
        conn = self._connect()
        try:
            # Tiebreak on id (a ULID) so jobs created in the same millisecond
            # have a stable, deterministic newest-first order on the seam.
            rows = conn.execute("SELECT * FROM jobs ORDER BY created_at DESC, id DESC").fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def list_claimable(self, now: str, lease_expiry_cutoff: str) -> list[dict]:
        """Non-terminal jobs that are unleased or whose lease has expired
        (``lease_ts < lease_expiry_cutoff``). Used by the boot-sweep and the
        claim scan. Oldest-updated first for fairness.
        """
        placeholders = ", ".join("?" for _ in CLAIMABLE_STATUSES)
        conn = self._connect()
        try:
            rows = conn.execute(
                f"SELECT * FROM jobs WHERE status IN ({placeholders}) "  # noqa: S608 - placeholders is one '?' per CLAIMABLE_STATUSES entry; every value is bound
                "AND (lease_ts IS NULL OR lease_ts < ?) ORDER BY updated_at ASC",
                (*CLAIMABLE_STATUSES, lease_expiry_cutoff),
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    # -- lease / fencing ---------------------------------------------------
    def claim(self, job_id: str, worker_id: str, now: str, lease_expiry_cutoff: str) -> int | None:
        """Atomically claim a job: bump ``lease_epoch``, set owner+heartbeat,
        increment ``attempts``, move to 'running'. Succeeds only if the job is
        non-terminal AND (unleased OR its lease has expired). Returns the NEW
        ``lease_epoch`` the worker must carry on every privileged write, or
        None if the job was not claimable (already owned by a live lease, or
        terminal). The UPDATE+SELECT run in one transaction so two racing
        claimants cannot both win.
        """
        placeholders = ", ".join("?" for _ in CLAIMABLE_STATUSES)
        conn = self._connect()
        try:
            cur = conn.execute(
                "UPDATE jobs SET lease_owner=?, lease_epoch=lease_epoch+1, "  # noqa: S608 - the only interpolation is a '?' per CLAIMABLE_STATUSES entry; values are bound
                "lease_ts=?, attempts=attempts+1, status='running', updated_at=? "
                f"WHERE id=? AND status IN ({placeholders}) "
                "AND (lease_ts IS NULL OR lease_ts < ?)",
                (worker_id, now, now, job_id, *CLAIMABLE_STATUSES, lease_expiry_cutoff),
            )
            if cur.rowcount != 1:
                conn.rollback()
                return None
            epoch = conn.execute("SELECT lease_epoch FROM jobs WHERE id=?", (job_id,)).fetchone()[0]
            conn.commit()
            return int(epoch)
        finally:
            conn.close()

    def heartbeat(self, job_id: str, lease_epoch: int, now: str) -> bool:
        """Refresh the lease heartbeat. Epoch-fenced: a stale worker can't
        keep a lease it no longer owns alive. Returns False if fenced out.
        """
        conn = self._connect()
        try:
            cur = conn.execute(
                "UPDATE jobs SET lease_ts=?, updated_at=? WHERE id=? AND lease_epoch=?",
                (now, now, job_id, lease_epoch),
            )
            conn.commit()
            return cur.rowcount == 1
        finally:
            conn.close()

    def record(self, job_id: str, lease_epoch: int, fields: dict) -> bool:
        """Epoch-fenced mutation of progress fields (status, tip, spent_cents,
        result_ref, error). Returns False (no rows) if the caller's epoch is
        stale — its write is a no-op and it should stop. Rejects any field not
        in the allowed mutable set.
        """
        unknown = set(fields) - _RECORD_COLUMNS
        if unknown:
            raise ValueError(f"job record: non-mutable field(s) {sorted(unknown)}")
        if not fields:
            return False
        now = _iso_utc()
        assignments = ", ".join(f"{c}=?" for c in fields) + ", updated_at=?"
        vals = [*fields.values(), now, job_id, lease_epoch]
        conn = self._connect()
        try:
            cur = conn.execute(f"UPDATE jobs SET {assignments} WHERE id=? AND lease_epoch=?", vals)  # noqa: S608 - fields is a subset of _RECORD_COLUMNS, checked above; values are bound
            conn.commit()
            return cur.rowcount == 1
        finally:
            conn.close()

    def request_cancel(self, job_id: str) -> bool:
        """Set the cancel flag. NOT epoch-fenced: anyone holding the ticket may
        request a cancel; the worker observes the flag at its next per-iteration
        check and dead-letters as 'cancelled'. Returns True if the job exists
        and is non-terminal.
        """
        now = _iso_utc()
        placeholders = ", ".join("?" for _ in CLAIMABLE_STATUSES)
        conn = self._connect()
        try:
            cur = conn.execute(
                f"UPDATE jobs SET cancel_requested=1, updated_at=? WHERE id=? AND status IN ({placeholders})",  # noqa: S608 - placeholders is one '?' per CLAIMABLE_STATUSES entry; every value is bound
                (now, job_id, *CLAIMABLE_STATUSES),
            )
            conn.commit()
            return cur.rowcount == 1
        finally:
            conn.close()

    # -- idempotency (record-key-before-effect) ----------------------------
    def idempotency_begin(self, job_id: str, step_key: str, lease_epoch: int) -> str:
        """Claim a side-effecting step BEFORE performing it. Returns:
          - 'proceed': key newly inserted — the caller owns this effect, do it.
          - 'skip': key already 'done' — the effect already happened, skip it.
          - 'review': key is 'in_progress' from a prior (crashed) attempt — we
            cannot know whether the effect landed, so fail closed: the caller
            must NOT re-fire and should park the job to needs_review.
        The insert is the journal: it survives the crash that would lose an
        in-context decision, so a resume sees 'done'/'in_progress' and does not
        double-fire.
        """
        now = _iso_utc()
        conn = self._connect()
        try:
            cur = conn.execute(
                "INSERT OR IGNORE INTO idempotency_keys "
                "(job_id, step_key, state, lease_epoch, created_at, updated_at) "
                "VALUES (?,?,'in_progress',?,?,?)",
                (job_id, step_key, lease_epoch, now, now),
            )
            conn.commit()
            if cur.rowcount == 1:
                return "proceed"
            state = conn.execute(
                "SELECT state FROM idempotency_keys WHERE job_id=? AND step_key=?",
                (job_id, step_key),
            ).fetchone()[0]
            return "skip" if state == "done" else "review"
        finally:
            conn.close()

    def idempotency_complete(self, job_id: str, step_key: str, lease_epoch: int) -> bool:
        """Mark a side-effecting step done AFTER it succeeded. Epoch-fenced so a
        stale worker cannot retroactively mark a step it didn't own. Returns
        False if fenced out or the key is absent.
        """
        now = _iso_utc()
        conn = self._connect()
        try:
            cur = conn.execute(
                "UPDATE idempotency_keys SET state='done', updated_at=? "
                "WHERE job_id=? AND step_key=? AND lease_epoch=?",
                (now, job_id, step_key, lease_epoch),
            )
            conn.commit()
            return cur.rowcount == 1
        finally:
            conn.close()
