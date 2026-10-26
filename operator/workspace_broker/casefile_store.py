"""The case file ledger's sqlite half: jobs, their numbered lines, the spend.

Per-operation connections, ``journal_mode=DELETE`` (the broker and the runner
reach this file as different uids), every check-and-write in one ``BEGIN
IMMEDIATE`` transaction. The shapes and rules are ``casefile_ledger``'s.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from .audit_ledger import _iso_utc, _ulid
from .casefile_ledger import (
    ALLOWED_NEXT,
    APPLIED_OUTCOMES,
    CLOSE_REF,
    CLOSED_REASON,
    COUNT_FIELDS,
    CREATE_INDEXES,
    CREATE_LINES_SQL,
    CREATE_SQL,
    KIND,
    NOTHING_TO_DO,
    PROJECTION,
    SubmitRefused,
    checked,
    digest,
    scope_key,
    validate_envelope,
)


class CasefileLedger:
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
                "SELECT COALESCE(SUM(cents), 0) AS c FROM casefile_spend WHERE at >= ? AND at < ? AND job_id != ?",
                (start, end, exclude),
            ).fetchone()
            return int(row["c"])
        finally:
            conn.close()

    def read(self, job_id: str) -> dict[str, Any] | None:
        conn = self._connect()
        try:
            row = conn.execute("SELECT * FROM casefile_jobs WHERE id=?", (job_id,)).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def recent(self, limit: int = 20) -> list[dict[str, Any]]:
        conn = self._connect()
        try:
            rows = conn.execute("SELECT * FROM casefile_jobs ORDER BY created_at DESC LIMIT ?", (int(limit),))
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def lines(self, job_id: str) -> list[dict[str, Any]]:
        conn = self._connect()
        try:
            rows = conn.execute("SELECT * FROM casefile_lines WHERE job_id=? ORDER BY n", (job_id,))
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def lines_on_thread(self, thread_ref: str) -> list[dict[str, Any]]:
        conn = self._connect()
        try:
            rows = conn.execute("SELECT * FROM casefile_lines WHERE thread_ref=? ORDER BY job_id, n", (thread_ref,))
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def unfinished_for_scope(self, key: str) -> str | None:
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT id FROM casefile_jobs WHERE scope_key=? AND state IN ('queued','running') LIMIT 1", (key,)
            ).fetchone()
            return row["id"] if row else None
        finally:
            conn.close()

    @staticmethod
    def project(row: dict[str, Any]) -> dict[str, Any]:
        """The row as the agent sees it: states, counts and file metadata."""
        out: dict[str, Any] = {"job_id": row.get("id"), **{k: row.get(k) for k in PROJECTION}}
        delivery = json.loads(row["delivery_json"]) if row.get("delivery_json") else {}
        files = delivery.get("files") if isinstance(delivery, dict) else None
        out["files"] = [{"role": f.get("role"), "name": f.get("name"), "size": f.get("size")} for f in files or []]
        book = next((f for f in files or [] if f.get("role") == "workbook"), None)
        out["file"] = {"name": book.get("name"), "size": book.get("size")} if book else None
        return out

    # ---- the submit ------------------------------------------------------------------
    def _queue(self, env: dict[str, Any], job_id: str) -> None:
        self.queue_dir.mkdir(parents=True, exist_ok=True)
        tmp = self.queue_dir / f".{job_id}.json.tmp"
        tmp.write_text(json.dumps({"kind": KIND, "job_id": job_id, **env}, indent=1, sort_keys=True), encoding="utf-8")
        tmp.chmod(0o640)
        tmp.replace(self.queue_dir / f"{job_id}.json")

    def submit(self, envelope: dict[str, Any]) -> str:
        """Row (and, for an apply, the lines it takes), then the queue file.
        The duplicate checks and the line claim run in one write transaction."""
        env = validate_envelope(envelope)
        apply = env.get("apply")
        job_id, now = _ulid(), _iso_utc()
        # An apply is keyed to itself: a second approval while one is queued
        # is a new job, and the line claim below decides what each one takes.
        key = f"apply:{job_id}" if apply else scope_key(env["scope"])
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            dup = conn.execute(
                "SELECT id FROM casefile_jobs WHERE request_ref=? AND NOT (state='failed' AND trigger='request') LIMIT 1",
                (env["message_ref"],),
            ).fetchone()
            twin = conn.execute(
                "SELECT id FROM casefile_jobs WHERE scope_key=? AND state IN ('queued','running') LIMIT 1", (key,)
            ).fetchone()
            if dup is not None or twin is not None:
                conn.rollback()
                got = (dup or twin)["id"]
                what = "that email already queued" if dup is not None else "the same caseload is already underway as"
                raise SubmitRefused(f"{what} case file job {got}", got)
            if apply:
                self._claim(conn, apply, job_id, now)
            conn.execute(
                "INSERT INTO casefile_jobs (id, created_at, updated_at, state, trigger, mode, requester, request_ref, "
                "scope_key, scope_json, file_to_matter_id, envelope_digest, source_job_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    job_id, now, now, "queued", env["trigger"], env["mode"], env["requester"], env["message_ref"], key,
                    json.dumps(env.get("scope") or {"lines": [ln["n"] for ln in (apply or {}).get("lines") or []]}, sort_keys=True),
                    env.get("file_to_matter_id") or None,
                    digest(env), apply["source_job_id"] if apply else None,
                ),
            )  # fmt: skip
            conn.commit()
        finally:
            conn.close()
        self._queue(env, job_id)
        return job_id

    @staticmethod
    def _claim(conn: sqlite3.Connection, apply: dict[str, Any], job_id: str, now: str) -> None:
        """Every line still ``raised`` moves to ``applying`` under this job, or
        nothing is queued (another apply took one first). This compare-and-set
        is the one arbiter between two approvals of the same job's lines."""
        for ln in apply["lines"]:
            cur = conn.execute(
                "UPDATE casefile_lines SET state='applying', apply_job_id=?, updated_at=? "
                "WHERE job_id=? AND n=? AND state='raised' AND sha256=? AND thread_ref=?",
                (job_id, now, apply["source_job_id"], ln["n"], ln["sha256"], apply["thread_ref"]),
            )
            if cur.rowcount != 1:
                conn.rollback()
                raise SubmitRefused(f"change {ln['n']} is not open to apply (already applied or underway)")

    # ---- the runner's record -----------------------------------------------------------
    def record(self, job_id: str, state: str, fields: dict[str, Any]) -> dict[str, Any]:
        """One transition (or a same-state note); the row after, with ``prev_state``."""
        f = checked(state, fields)
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            cur = conn.execute("SELECT * FROM casefile_jobs WHERE id=?", (job_id,)).fetchone()
            if cur is None:
                conn.rollback()
                raise ValueError(f"no such job {job_id}")
            if state != cur["state"] and state not in ALLOWED_NEXT[cur["state"]]:
                conn.rollback()
                raise ValueError(f"illegal transition {cur['state']} -> {state}")
            if cur["close_ref"] and state != cur["state"]:
                conn.rollback()
                raise ValueError(f"job {job_id} was closed by SMD ({cur['close_ref']}); it never moves again")
            now = _iso_utc()
            self._spend(conn, job_id, now, f.get("cents"), int(cur["cents"] or 0))
            self._update(conn, job_id, now, state, cur, f)
            if state == "delivered" and cur["mode"] == "full" and f.get("lines"):
                self._lines(conn, job_id, now, f["lines"])
            if state == "delivered" and cur["mode"] == "apply" and f.get("line_outcomes"):
                self._outcomes(conn, cur, now, f["line_outcomes"])
            if state in ("held", "failed") and cur["mode"] == "apply" and state != cur["state"]:
                self._release(conn, cur, now, f)
            conn.commit()
            row = dict(conn.execute("SELECT * FROM casefile_jobs WHERE id=?", (job_id,)).fetchone())
            row["prev_state"] = cur["state"]
            return row
        finally:
            conn.close()

    def _release(self, conn: sqlite3.Connection, cur: Any, now: str, f: dict[str, Any]) -> None:
        """A held or failed apply: each line it reported an outcome for takes
        that outcome; a line with a journaled write intent and no outcome stays
        ``applying`` under this job (its write may have landed; only this job's
        resume or SMD decides it); every other line goes back to ``raised`` for
        a new approval."""
        if f.get("line_outcomes"):
            self._outcomes(conn, cur, now, f["line_outcomes"])
        keep = {int(n) for n in f.get("intents") or [] if isinstance(n, int)}
        for (n,) in conn.execute(
            "SELECT n FROM casefile_lines WHERE apply_job_id=? AND state='applying'", (cur["id"],)
        ).fetchall():
            if n not in keep:
                conn.execute(
                    "UPDATE casefile_lines SET state='raised', apply_job_id=NULL, updated_at=? "
                    "WHERE apply_job_id=? AND n=? AND state='applying'",
                    (now, cur["id"], n),
                )

    def reclaim(self, job_id: str) -> list[int]:
        """A failed apply resumed: the lines it released and no other apply has
        taken since come back to it; returns every line it now holds."""
        row = self.read(job_id)
        if row is None:
            return []
        env = json.loads(row["scope_json"] or "{}")
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            for n in env.get("lines") or []:
                conn.execute(
                    "UPDATE casefile_lines SET state='applying', apply_job_id=?, updated_at=? "
                    "WHERE job_id=? AND n=? AND state='raised'",
                    (job_id, _iso_utc(), row["source_job_id"], n),
                )
            conn.commit()
        finally:
            conn.close()
        return self.owned_lines(job_id)

    def owned_lines(self, job_id: str) -> list[int]:
        """The line numbers this apply job holds (applying, or already ended under it)."""
        conn = self._connect()
        try:
            rows = conn.execute("SELECT n FROM casefile_lines WHERE apply_job_id=? ORDER BY n", (job_id,)).fetchall()
            return [int(r["n"]) for r in rows]
        finally:
            conn.close()

    @staticmethod
    def _spend(conn: sqlite3.Connection, job_id: str, now: str, cents: Any, before: int) -> None:
        if isinstance(cents, int) and cents > before:
            conn.execute("INSERT INTO casefile_spend (job_id, at, cents) VALUES (?,?,?)", (job_id, now, cents - before))

    @staticmethod
    def _update(conn: sqlite3.Connection, job_id: str, now: str, state: str, cur: Any, f: dict[str, Any]) -> None:
        def text(key: str, limit: int) -> str | None:
            return str(f.get(key) or "")[:limit] or None

        blobs = {
            "files": json.dumps({"files": f["files"]}, sort_keys=True) if "files" in f else None,
            "metrics": json.dumps(f["metrics"], sort_keys=True) if "metrics" in f else None,
            "line_outcomes": json.dumps(f["line_outcomes"], sort_keys=True) if "line_outcomes" in f else None,
        }
        pairs: list[Any] = []
        for key in ("cents", *COUNT_FIELDS):
            pairs += [key in f, f.get(key)]
        for key, limit in (("reason", 500), ("stage", 40), ("folder_id", 120)):
            pairs += [key in f, text(key, limit)]
        for key in ("files", "metrics", "line_outcomes"):
            pairs += [key in f, blobs[key]]
        conn.execute(
            "UPDATE casefile_jobs SET state=?, updated_at=?, attempt=attempt + ?, "
            "cents=CASE WHEN ? THEN ? ELSE cents END, "
            "matters_total=CASE WHEN ? THEN ? ELSE matters_total END, "
            "matters_read=CASE WHEN ? THEN ? ELSE matters_read END, "
            "matters_current=CASE WHEN ? THEN ? ELSE matters_current END, "
            "matters_held=CASE WHEN ? THEN ? ELSE matters_held END, "
            "lines_proposed=CASE WHEN ? THEN ? ELSE lines_proposed END, "
            "reason=CASE WHEN ? THEN ? ELSE reason END, "
            "stage=CASE WHEN ? THEN ? ELSE stage END, "
            "folder_id=CASE WHEN ? THEN ? ELSE folder_id END, "
            "delivery_json=CASE WHEN ? THEN ? ELSE delivery_json END, "
            "metrics_json=CASE WHEN ? THEN ? ELSE metrics_json END, "
            "outcomes_json=CASE WHEN ? THEN ? ELSE outcomes_json END "
            "WHERE id=?",
            (state, now, 1 if cur["state"] == "failed" and state == "running" else 0, *pairs, job_id),
        )

    @staticmethod
    def _lines(conn: sqlite3.Connection, job_id: str, now: str, lines: list[dict[str, Any]]) -> None:
        for ln in lines:
            conn.execute(
                "INSERT OR IGNORE INTO casefile_lines (job_id, n, kind, matter_id, source_id, sha256, state, updated_at) "
                "VALUES (?,?,?,?,?,?,'proposed',?)",
                (job_id, ln["n"], ln["kind"], ln["matter_id"], ln["source_id"], ln["sha256"], now),
            )

    @staticmethod
    def _outcomes(conn: sqlite3.Connection, cur: Any, now: str, outcomes: list[dict[str, Any]]) -> None:
        for o in outcomes:
            if o["outcome"] in ("error", "readback_mismatch", "unknown_after_stop", "link_not_visible"):
                continue  # undecided: the line stays applying under this job until its resume or SMD
            state = "applied" if o["outcome"] in (*APPLIED_OUTCOMES, *NOTHING_TO_DO) else "refused"
            conn.execute(
                "UPDATE casefile_lines SET state=?, outcome=?, updated_at=? WHERE job_id=? AND n=? AND apply_job_id=?",
                (state, o["outcome"], now, cur["source_job_id"], o["n"], cur["id"]),
            )

    def mark_raised(self, job_id: str, thread_ref: str, dispatch_ref: str) -> list[dict[str, Any]]:
        """Every ``proposed`` line of the job, now ``raised`` on the thread its
        reply went out on; returns them (an empty list when none were open)."""
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            rows = [
                dict(r)
                for r in conn.execute(
                    "SELECT * FROM casefile_lines WHERE job_id=? AND state='proposed' ORDER BY n", (job_id,)
                )
            ]
            conn.execute(
                "UPDATE casefile_lines SET state='raised', thread_ref=?, dispatch_ref=?, updated_at=? "
                "WHERE job_id=? AND state='proposed'",
                (thread_ref, dispatch_ref, _iso_utc(), job_id),
            )
            conn.commit()
            return rows
        finally:
            conn.close()

    # ---- SMD's close and the reply mark ----------------------------------------------------
    def close(self, job_id: str, reason: str, source_ref: str) -> dict[str, Any]:
        if not str(reason or "").strip() or not CLOSE_REF.match(str(source_ref or "")):
            raise ValueError("a close requires a reason and a source_ref (vfy_<ULID> or a ULID)")
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            cur = conn.execute("SELECT state FROM casefile_jobs WHERE id=?", (job_id,)).fetchone()
            if cur is None or cur["state"] != "held":
                conn.rollback()
                raise ValueError(f"job {job_id} is {cur['state'] if cur else 'missing'}; only a held job is closed")
            conn.execute(
                "UPDATE casefile_jobs SET state='failed', updated_at=?, reason=?, close_reason=?, close_ref=? WHERE id=?",
                (_iso_utc(), CLOSED_REASON, reason[:500], source_ref, job_id),
            )
            conn.commit()
            row = dict(conn.execute("SELECT * FROM casefile_jobs WHERE id=?", (job_id,)).fetchone())
            row["prev_state"] = "held"
            return row
        finally:
            conn.close()

    def mark_replied(self, job_id: str, key: str = "reply") -> bool:
        conn = self._connect()
        try:
            cur = conn.execute(
                "UPDATE casefile_jobs SET reply_sent_at=?, reply_key=? WHERE id=? "
                "AND (reply_key IS NULL OR reply_key != ?) AND NOT (reply_key IS NULL AND reply_sent_at IS NOT NULL)",
                (_iso_utc(), key, job_id, key),
            )
            conn.commit()
            return cur.rowcount == 1
        finally:
            conn.close()

    def unmark_replied(self, job_id: str, key: str) -> None:
        conn = self._connect()
        try:
            conn.execute(
                "UPDATE casefile_jobs SET reply_sent_at=NULL, reply_key=NULL WHERE id=? AND reply_key=?", (job_id, key)
            )
            conn.commit()
        finally:
            conn.close()


__all__ = ["CasefileLedger"]
