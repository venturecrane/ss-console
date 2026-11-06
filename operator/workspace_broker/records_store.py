"""The records ledger's sqlite half: jobs, their item outcomes, the spend.

Per-operation connections, ``journal_mode=DELETE`` (the broker and the runner
reach this file as different uids), every check-and-write in one ``BEGIN
IMMEDIATE`` transaction. The shapes and rules are ``records_ledger``'s.
"""

from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from typing import Any

from .audit_ledger import _iso_utc, _ulid
from .records_ledger import (
    ALLOWED_NEXT,
    CLOSE_REF,
    CLOSED_REASON,
    CREATE_INDEXES,
    CREATE_LINES_SQL,
    CREATE_SQL,
    KIND,
    LIVE_STATES,
    PROJECTION,
    SubmitRefused,
    digest,
    resolve,
    validate_envelope,
)

QUEUE_DIR_ENV = "SMD_RECORDS_QUEUE_DIR"
DEFAULT_QUEUE_DIR = "/opt/data/jobs/records-queue"
#: The open SMD review cards: a job whose reply row waits in the review dir.
#: The overlay's ``request_cards`` reads the same statement (it cannot import
#: this module); a card closes when the row leaves ``review``.
REVIEW_CARD_SQL = (
    "SELECT job_id, request_ref, reason, created_at, updated_at, matter_id, review_id FROM records_jobs"
    " WHERE state = 'review' ORDER BY created_at"
)
#: The lane-proof holds, the shape ``request_cards._HOLD_SQL`` reads per lane.
HOLD_SQL = (
    "SELECT job_id, request_ref, reason, created_at, updated_at, matter_id FROM records_jobs"
    " WHERE reason LIKE 'lane_unproven:%' AND state IN ('queued', 'failed')"
)


def queue_dir_from_env() -> str:
    return os.environ.get(QUEUE_DIR_ENV, "").strip() or DEFAULT_QUEUE_DIR


class RecordsLedger:
    def __init__(self, db_path: str, queue_dir: str | Path) -> None:
        self._db_path = db_path
        self.queue_dir = Path(queue_dir)
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
            for sql in (CREATE_SQL, CREATE_LINES_SQL, *CREATE_INDEXES):
                conn.execute(sql)
            conn.commit()
        finally:
            conn.close()

    # ---- reads ---------------------------------------------------------------------
    def spend_between(self, start: str, end: str, exclude: str = "") -> int:
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT COALESCE(SUM(cents), 0) AS c FROM records_spend WHERE at >= ? AND at < ? AND job_id != ?",
                (start, end, exclude),
            ).fetchone()
            return int(row["c"])
        finally:
            conn.close()

    def read(self, job_id: str) -> dict[str, Any] | None:
        conn = self._connect()
        try:
            row = conn.execute("SELECT * FROM records_jobs WHERE job_id=?", (job_id,)).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def recent(self, limit: int = 20) -> list[dict[str, Any]]:
        conn = self._connect()
        try:
            rows = conn.execute("SELECT * FROM records_jobs ORDER BY created_at DESC LIMIT ?", (int(limit),))
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def lines(self, job_id: str) -> list[dict[str, Any]]:
        conn = self._connect()
        try:
            rows = conn.execute("SELECT * FROM records_lines WHERE job_id=? ORDER BY n", (job_id,))
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def open_reviews(self) -> list[dict[str, Any]]:
        """Every job whose reply waits for SMD: one open card each."""
        conn = self._connect()
        try:
            return [dict(r) for r in conn.execute(REVIEW_CARD_SQL)]
        finally:
            conn.close()

    @staticmethod
    def project(row: dict[str, Any]) -> dict[str, Any]:
        """The row as the agent sees it: states, counts and ids."""
        return {"job_id": row.get("job_id"), **{k: row.get(k) for k in PROJECTION}}

    # ---- the submit ------------------------------------------------------------------
    def _queue(self, env: dict[str, Any], job_id: str) -> None:
        self.queue_dir.mkdir(parents=True, exist_ok=True)
        tmp = self.queue_dir / f".{job_id}.json.tmp"
        tmp.write_text(json.dumps({"kind": KIND, "job_id": job_id, **env}, indent=1, sort_keys=True), encoding="utf-8")
        tmp.chmod(0o640)
        tmp.replace(self.queue_dir / f"{job_id}.json")

    def submit(self, envelope: dict[str, Any]) -> str:
        """The row, then the queue file. One email, one job, ever: a twin in
        any state is refused inside the same write transaction that inserts."""
        env = validate_envelope({k: v for k, v in envelope.items() if k != "request_ref"})  # request_ref is derived
        job_id, now = _ulid(), _iso_utc()
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            dup = conn.execute(
                "SELECT job_id, state FROM records_jobs WHERE request_ref=? OR message_ref=? LIMIT 1",
                (env["request_ref"], env["message_ref"]),
            ).fetchone()
            if dup is not None:
                conn.rollback()
                live = dup["state"] in LIVE_STATES
                word = "already queued" if live else "already answered"
                raise SubmitRefused(
                    f"that email is {word} as records job {dup['job_id']}",
                    dup["job_id"],
                    "already_queued" if live else "already_done",
                )
            conn.execute(
                "INSERT INTO records_jobs (job_id, request_ref, message_ref, matter_id, requester, state, cents, created_at, "
                "updated_at, trigger, ack, envelope_digest) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (job_id, env["request_ref"], env["message_ref"], env["matter_id"], env["requester"], "queued", 0, now, now,
                 env["trigger"], env["ack"], digest(env)),
            )  # fmt: skip
            conn.commit()
        except sqlite3.IntegrityError:
            # The unique index is the last word if a writer outside this method raced.
            conn.rollback()
            row = self._by_ref(env["request_ref"], env["message_ref"])
            if row is None:
                raise
            live = row["state"] in LIVE_STATES
            raise SubmitRefused(
                f"that email is {'already queued' if live else 'already answered'} as records job {row['job_id']}",
                row["job_id"],
                "already_queued" if live else "already_done",
            ) from None
        finally:
            conn.close()
        self._queue(env, job_id)
        return job_id

    def _by_ref(self, ref: str, message_ref: str) -> dict[str, Any] | None:
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT * FROM records_jobs WHERE request_ref=? OR message_ref=?", (ref, message_ref)
            ).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    # ---- the runner's record -----------------------------------------------------------
    def record(self, job_id: str, report: dict[str, Any]) -> dict[str, Any]:
        """One transition (or a same-state note); the row after, with ``prev_state``."""
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            cur = conn.execute("SELECT * FROM records_jobs WHERE job_id=?", (job_id,)).fetchone()
            if cur is None:
                conn.rollback()
                raise ValueError(f"no such job {job_id}")
            try:
                state, f = resolve(report, cur["state"])
            except ValueError:
                conn.rollback()
                raise
            if state != cur["state"] and state not in ALLOWED_NEXT[cur["state"]]:
                conn.rollback()
                raise ValueError(f"illegal transition {cur['state']} -> {state}")
            if cur["close_ref"] and state != cur["state"]:
                conn.rollback()
                raise ValueError(f"job {job_id} was closed by SMD ({cur['close_ref']}); it never moves again")
            now = _iso_utc()
            self._spend(conn, job_id, now, f.get("cents"), int(cur["cents"] or 0))
            delivery = dict(json.loads(cur["delivery_json"]) if cur["delivery_json"] else {})
            delivery.update(f.get("delivery") or {})
            conn.execute(
                "UPDATE records_jobs SET state=?, updated_at=?, attempt=attempt + ?, "
                "cents=CASE WHEN ? THEN ? ELSE cents END, "
                "reason=CASE WHEN ? THEN ? ELSE reason END, "
                "stage=CASE WHEN ? THEN ? ELSE stage END, "
                "review_id=COALESCE(?, review_id), "
                "delivery_json=CASE WHEN ? THEN ? ELSE delivery_json END WHERE job_id=?",
                (
                    state, now, 1 if cur["state"] == "failed" and state == "running" else 0,
                    "cents" in f, f.get("cents"), "reason" in f, f.get("reason"), "stage" in f, f.get("stage"),
                    delivery.get("review_id"), "delivery" in f, json.dumps(delivery, sort_keys=True), job_id,
                ),
            )  # fmt: skip
            for it in f.get("items") or []:
                conn.execute(
                    "INSERT INTO records_lines (job_id, n, outcome, files, updated_at) VALUES (?,?,?,?,?) "
                    "ON CONFLICT(job_id, n) DO UPDATE SET outcome=excluded.outcome, files=excluded.files, "
                    "updated_at=excluded.updated_at",
                    (job_id, it["n"], it["outcome"], it["files"], now),
                )
            conn.commit()
            row = dict(conn.execute("SELECT * FROM records_jobs WHERE job_id=?", (job_id,)).fetchone())
            row["prev_state"] = cur["state"]
            return row
        finally:
            conn.close()

    @staticmethod
    def _spend(conn: sqlite3.Connection, job_id: str, now: str, cents: Any, before: int) -> None:
        if isinstance(cents, int) and cents > before:
            conn.execute("INSERT INTO records_spend (job_id, at, cents) VALUES (?,?,?)", (job_id, now, cents - before))

    # ---- SMD's close -----------------------------------------------------------------------
    def close(self, job_id: str, reason: str, source_ref: str) -> dict[str, Any]:
        """SMD ends a held job, or one in review it answered another way."""
        if not str(reason or "").strip() or not CLOSE_REF.match(str(source_ref or "")):
            raise ValueError("a close requires a reason and a source_ref (vfy_<ULID> or a ULID)")
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            cur = conn.execute("SELECT state FROM records_jobs WHERE job_id=?", (job_id,)).fetchone()
            if cur is None or cur["state"] not in ("held", "review"):
                conn.rollback()
                raise ValueError(
                    f"job {job_id} is {cur['state'] if cur else 'missing'}; only a held or in-review job is closed"
                )
            conn.execute(
                "UPDATE records_jobs SET state='failed', updated_at=?, reason=?, close_reason=?, close_ref=? "
                "WHERE job_id=?",
                (_iso_utc(), CLOSED_REASON, reason[:500], source_ref, job_id),
            )
            conn.commit()
            row = dict(conn.execute("SELECT * FROM records_jobs WHERE job_id=?", (job_id,)).fetchone())
            row["prev_state"] = cur["state"]
            return row
        finally:
            conn.close()


__all__ = ["DEFAULT_QUEUE_DIR", "HOLD_SQL", "QUEUE_DIR_ENV", "REVIEW_CARD_SQL", "RecordsLedger", "queue_dir_from_env"]
