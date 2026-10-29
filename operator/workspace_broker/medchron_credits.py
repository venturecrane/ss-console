"""Page credits against the chronology page allowance (2026-10-29).

WHY. SMD delivered chronologies on one A&P matter without reading a hospital's
files. Re-reading those pages is a correction of SMD's own miss, and the firm
must not pay for it out of its cycle allowance (Captain, 2026-10-29). Before
this there was no mechanism: every job that recorded cents debited its pages
(`medchron_ledger._DEBITS_SQL`). The correcting job can MIX waived pages with
pages the firm asked for and pays for, so a whole-job "not billed" flag is the
wrong shape. A credit is a count of pages, written by a person, that one cycle
does not charge. Credits are only ever a person's act (the root verb below);
nothing in the runner writes one.

ONE rule, shared with the console (`src/lib/admin/medchron-jobs-read.ts`):

* A credit lands in the cycle of its ``applies_at``. For a credit linked to a
  job that is the JOB's ``created_at`` (the cycle the job's pages debit), so a
  credit written on Nov 1 for an October job lightens October, and the job's
  delivery "consumed" and the cycle's "remains" can never disagree. A credit
  with no job applies at the moment it was written.
* A cycle's pages used = debited pages in the window minus credited pages in
  the window, floored at 0.
* Pages only. Documents and cents are untouched: the cents are SMD's real
  vendor cost and stay in its cost telemetry whoever pays for the pages.
* When the runner meters a job (the ``exclude_job_id`` read) it leaves the
  job's own pages out of "used", so that job's own credits come back as
  headroom instead, or a correction larger than the cycle's remainder would
  hold on pages the firm is not paying for.

Written only through the root-only ``medchron_allowance_credit`` broker verb
(`python -m medchron.allowance credit`), which also writes a
``MEDCHRON_ALLOWANCE_CREDITED`` audit row: the record billing reads.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from .audit_ledger import _iso_utc, _ulid

from .cycle_window import Window

CREATE_CREDITS_SQL = (
    "CREATE TABLE IF NOT EXISTS medchron_page_credits ("
    "id TEXT PRIMARY KEY, "
    "created_at TEXT NOT NULL, "
    "applies_at TEXT NOT NULL, "
    "pages INTEGER NOT NULL CHECK (pages > 0), "
    "by_name TEXT NOT NULL, "
    "why TEXT NOT NULL, "
    "job_id TEXT"
    ")"
)
CREATE_CREDITS_INDEX_SQL = (
    "CREATE INDEX IF NOT EXISTS idx_medchron_page_credits_applies ON medchron_page_credits(applies_at)"
)

#: What the console sees of a credit (the ``medchron_jobs`` runtime-read
#: payload's ``credits`` list). Counts, times and ids only: ``why`` is free text
#: a person typed and ``by_name`` is a name, so neither crosses the management
#: seam (ADR 0052). Byte-pinned to the overlay's ``_MEDCHRON_CREDIT_COLUMNS``.
CREDIT_PROJECTION = ("id", "created_at", "applies_at", "pages", "job_id")

_IN_WINDOW_SQL = (
    "SELECT COALESCE(SUM(pages), 0) AS pages FROM medchron_page_credits WHERE applies_at >= ? AND applies_at < ?"
)
# The named job's work group (the group `_DEBITS_SQL_EXCLUDING` leaves out), so
# a resume or a relaunch of the credited work still gets its credit back.
_GROUP_IN_WINDOW_SQL = (
    "SELECT COALESCE(SUM(c.pages), 0) AS pages FROM medchron_page_credits c "
    "WHERE c.applies_at >= ? AND c.applies_at < ? AND c.job_id IS NOT NULL AND c.job_id IN ("
    "SELECT g.id FROM medchron_jobs g WHERE COALESCE(g.work_digest, g.id) IN "
    "(SELECT COALESCE(j.work_digest, j.id) FROM medchron_jobs j WHERE j.id = ?))"
)
_GROUP_TOTAL_SQL = (
    "SELECT COALESCE(SUM(c.pages), 0) AS pages FROM medchron_page_credits c "
    "WHERE c.job_id IS NOT NULL AND c.job_id IN ("
    "SELECT g.id FROM medchron_jobs g WHERE COALESCE(g.work_digest, g.id) IN "
    "(SELECT COALESCE(j.work_digest, j.id) FROM medchron_jobs j WHERE j.id = ?))"
)
_LIST_SQL = (
    "SELECT id, created_at, applies_at, pages, job_id FROM medchron_page_credits ORDER BY created_at DESC LIMIT ?"
)
_INSERT_SQL = (
    "INSERT INTO medchron_page_credits (id, created_at, applies_at, pages, by_name, why, job_id) "
    "VALUES (?, ?, ?, ?, ?, ?, ?)"
)


def ensure_schema(conn: sqlite3.Connection) -> None:
    """Idempotent on a seat whose ledger predates credits: the table and index
    are created if absent, nothing else is touched."""
    conn.execute(CREATE_CREDITS_SQL)
    conn.execute(CREATE_CREDITS_INDEX_SQL)


def credited(conn: sqlite3.Connection, window: Window, exclude_job_id: str | None = None) -> tuple[int, int]:
    """(credits against the window's usage, credits that are the excluded job's
    own headroom). Without ``exclude_job_id`` the second figure is 0 and every
    credit in the window reduces usage."""
    total = int(conn.execute(_IN_WINDOW_SQL, (window.start, window.end)).fetchone()["pages"])
    if not exclude_job_id:
        return total, 0
    own = int(conn.execute(_GROUP_IN_WINDOW_SQL, (window.start, window.end, exclude_job_id)).fetchone()["pages"])
    return total - own, own


def job_credited(conn: sqlite3.Connection, job_id: str) -> int:
    """Every page credited to the job's work group, whatever window: the
    figure the delivery reply nets out of what the job consumed."""
    return int(conn.execute(_GROUP_TOTAL_SQL, (job_id,)).fetchone()["pages"])


def insert(
    conn: sqlite3.Connection,
    *,
    credit_id: str,
    created_at: str,
    applies_at: str,
    pages: int,
    by: str,
    why: str,
    job_id: str | None,
) -> None:
    conn.execute(_INSERT_SQL, (credit_id, created_at, applies_at, pages, by, why, job_id))


def list_recent(conn: sqlite3.Connection, limit: int = 200) -> list[dict[str, Any]]:
    return [{k: r[k] for k in CREDIT_PROJECTION} for r in conn.execute(_LIST_SQL, (limit,)).fetchall()]


def validate(request: dict[str, Any]) -> tuple[int, str, str, str | None]:
    """The credit request's fields, or ValueError naming what is wrong. Never
    filled in: a credit with no author or no reason is not a billing record."""
    pages = request.get("pages")
    if not isinstance(pages, int) or isinstance(pages, bool) or pages <= 0:
        raise ValueError("medchron_allowance_credit requires pages: a whole number greater than 0")
    by = str(request.get("by") or "").strip()
    if not by:
        raise ValueError("medchron_allowance_credit requires by: who waived the pages")
    why = str(request.get("why") or "").strip()
    if not why:
        raise ValueError("medchron_allowance_credit requires why: what SMD is not charging for, and why")
    job_id = str(request.get("job_id") or "").strip() or None
    return pages, by[:120], why[:500], job_id


class CreditLedger:
    """The credit half of ``MedchronLedger`` (a mixin: it uses the ledger's
    ``_connect`` and ``read``), kept here so the ledger module stays under
    the size ceiling and every credit rule lives in one file."""

    def _connect(self) -> sqlite3.Connection:  # pragma: no cover - provided by MedchronLedger
        raise NotImplementedError

    def read(self, job_id: str) -> dict[str, Any] | None:  # pragma: no cover - provided by MedchronLedger
        raise NotImplementedError

    def credit_figures(
        self, window: Window, debited: int, exclude_job_id: str | None = None
    ) -> tuple[int, int, dict[str, int]]:
        """(pages used, the excluded job's own headroom, the credit keys the
        allowance answer carries). Used is debited minus credited in the
        window, floored at 0. A credit on the excluded job's own work comes
        back as that job's headroom instead, because its pages are not in
        ``debited`` to be reduced. ``job_pages_credited`` is every page
        credited to that job's work (0 without ``exclude_job_id``): credits
        land in the job's own window, so the delivery's "consumed" (job pages
        minus these) and the window's "remains" come from one rule."""
        others, own = self.credited_pages(window, exclude_job_id)
        keys = {
            "pages_debited": debited,
            "pages_credited": others + own,
            "job_pages_credited": self.job_credited_pages(exclude_job_id) if exclude_job_id else 0,
        }
        return max(0, debited - others), own, keys

    def credited_pages(self, window: Window, exclude_job_id: str | None = None) -> tuple[int, int]:
        conn = self._connect()
        try:
            return credited(conn, window, exclude_job_id)
        finally:
            conn.close()

    def job_credited_pages(self, job_id: str) -> int:
        conn = self._connect()
        try:
            return job_credited(conn, job_id)
        finally:
            conn.close()

    def add_credit(
        self, *, pages: int, by: str, why: str, job_id: str | None, now: str | None = None
    ) -> dict[str, Any]:
        """A credit linked to a job applies in the JOB's window (its
        ``created_at``), the window its pages debit; one with no job applies
        when written. Raises ValueError for a job the ledger does not hold."""
        credit_id = _ulid()
        created_at = now or _iso_utc()
        applies_at = created_at
        if job_id:
            row = self.read(job_id)
            if row is None:
                raise ValueError(f"no such job {job_id}; nothing was credited")
            applies_at = str(row["created_at"])
        conn = self._connect()
        try:
            insert(
                conn,
                credit_id=credit_id,
                created_at=created_at,
                applies_at=applies_at,
                pages=pages,
                by=by,
                why=why,
                job_id=job_id,
            )
            conn.commit()
        finally:
            conn.close()
        return {"id": credit_id, "created_at": created_at, "applies_at": applies_at, "pages": pages, "job_id": job_id}

    def list_credits(self, limit: int = 200) -> list[dict[str, Any]]:
        conn = self._connect()
        try:
            return list_recent(conn, limit)
        finally:
            conn.close()
