"""One job ledger for every lane: the connection discipline, the schema run,
the row read, the queue file and the transition every lane's record takes.

Before wave 2 each lane (medchron, demand, drafting, library setup,
litigation, case file, negotiation, records, note) carried its own copy of
all of it, and a change to how a job moves had to be made nine times. Now a
lane declares what is its own and nothing else:

* ``JOBS`` and ``ID``: its jobs table and that table's key column;
* ``_create(conn)``: its DDL, verbatim (no statement text changes here; a
  lane's schema is its own and an aged seat file must migrate exactly as it
  did);
* ``_check`` / ``_guard`` / ``_apply`` / ``_result``: what a record may carry,
  what refuses a move, the UPDATE it writes, and the reply it returns;
* ``SPEND``: the table its paid cents are dated in, when it has one.

THE TRANSITION (``_transition``), the same for every lane: one write
transaction (``BEGIN IMMEDIATE``, unless the lane declares ``BEGIN = None``),
the current row read, ``no such job`` and ``illegal transition`` refused with
the lane's own table of moves, the lane's guard, ONE clock read, the spend
increase dated at that instant, the lane's UPDATE, commit, the row after.
Any refusal inside rolls back, so a refused record writes nothing.

A LANE'S RULES ARE ITS MODULE'S. The clock (``_iso_utc``) and the table of
moves (``_ALLOWED_NEXT``; a lane whose table has another name overrides
``_next``) are read from the lane module's globals at call time, never bound
here, so a test (or the golden kit's fixed clock) that patches the lane
module reaches the transition.

THE OPTIONAL HALVES are mixins, because a lane that lacks one must still lack
it (the golden kit reads ``hasattr(ledger, "mark_replied")``):
``ReplyOnce`` (the reply-once compare-and-set), ``RecentJobs`` and
``SpendMeter`` (``spend_between``).
"""

from __future__ import annotations

import json
import re
import sqlite3
import sys
from collections.abc import Callable, Mapping
from pathlib import Path
from types import ModuleType
from typing import Any, ClassVar

_IDENT = re.compile(r"^[a-z_]+$")


def _statements(jobs: str, key: str, spend: str | None) -> dict[str, str]:
    """Every statement the shared half runs, for one lane's tables. Built once
    per lane class from its constants (checked plain identifiers: a table name
    takes no bound parameter); every value is bound."""
    # S608 below: each f-string formats only jobs/key/spend, plain identifiers
    # checked in LaneLedger.__init_subclass__; every value is a bound '?'.
    once = "AND (reply_key IS NULL OR reply_key != ?) AND NOT (reply_key IS NULL AND reply_sent_at IS NOT NULL)"
    out = {
        "row": f"SELECT * FROM {jobs} WHERE {key}=?",  # noqa: S608 - identifiers checked at class creation
        "recent": f"SELECT * FROM {jobs} ORDER BY created_at DESC LIMIT ?",  # noqa: S608 - identifiers checked at class creation
        "mark": f"UPDATE {jobs} SET reply_sent_at=?, reply_key=? WHERE {key}=? {once}",  # noqa: S608 - identifiers checked at class creation
        "unmark": f"UPDATE {jobs} SET reply_sent_at=NULL, reply_key=NULL WHERE {key}=? AND reply_key=?",  # noqa: S608 - identifiers checked at class creation
    }
    if spend:
        out["spend_insert"] = f"INSERT INTO {spend} (job_id, at, cents) VALUES (?,?,?)"  # noqa: S608 - identifiers checked at class creation
        out["spend_sum"] = f"SELECT COALESCE(SUM(cents), 0) AS c FROM {spend} WHERE at >= ? AND at < ? AND job_id != ?"  # noqa: S608 - identifiers checked at class creation
    return out


def require_cents(fields: Mapping[str, Any]) -> None:
    """A record's ``cents``, when it carries one, is a non-negative int."""
    cents = fields.get("cents")
    if "cents" in fields and (not isinstance(cents, int) or isinstance(cents, bool) or cents < 0):
        raise ValueError("cents must be a non-negative int")


class LaneLedger:
    """Per-operation sqlite connections, ``journal_mode=DELETE``: the broker
    and the runner reach the audit db file as different uids, and the WAL's
    shared-memory file would need both to write it."""

    #: The lane's jobs table and its key column.
    JOBS: ClassVar[str]
    ID: ClassVar[str] = "id"
    #: The table each record's cents increase is dated in (None: the lane meters no spend here).
    SPEND: ClassVar[str | None] = None
    #: The statement that opens a record's transaction (None: the lane takes no write lock first).
    BEGIN: ClassVar[str | None] = "BEGIN IMMEDIATE"
    #: The module holding the lane's rules (set from the class that declares ``JOBS``).
    RULES_MODULE: ClassVar[str]
    #: The shared half's statements for this lane (``_statements``).
    _SQL: ClassVar[dict[str, str]]

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        if "JOBS" in cls.__dict__:
            if "RULES_MODULE" not in cls.__dict__:
                cls.RULES_MODULE = cls.__module__
            # The table names are formatted into SQL (an identifier takes no bound parameter).
            for name in (cls.JOBS, cls.ID, cls.SPEND or "x"):
                if not _IDENT.match(name):
                    raise TypeError(f"{cls.__name__}: {name!r} is not a plain table or column name")
            cls._SQL = _statements(cls.JOBS, cls.ID, cls.SPEND)

    def __init__(self, db_path: str, queue_dir: str | Path | None = None) -> None:
        self._db_path = db_path
        if queue_dir is not None:
            self.queue_dir = Path(queue_dir)
        self.ensure_schema()

    # -- the lane's module -------------------------------------------------
    def _rules(self) -> ModuleType:
        return sys.modules[self.RULES_MODULE]

    def _now(self) -> str:
        return self._rules()._iso_utc()

    def _next(self) -> Mapping[str, Any]:
        """The lane's table of legal moves: state -> the states it may move to."""
        return self._rules()._ALLOWED_NEXT

    # -- connection and schema ---------------------------------------------
    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path, timeout=5.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=DELETE")
        conn.execute("PRAGMA busy_timeout=5000")
        return conn

    def ensure_schema(self) -> None:
        conn = self._connect()
        try:
            self._create(conn)
            conn.commit()
        finally:
            conn.close()

    def _create(self, conn: sqlite3.Connection) -> None:
        """The lane's DDL, in its order, verbatim."""
        raise NotImplementedError

    # -- reads -------------------------------------------------------------
    def _row_sql(self) -> str:
        return self._SQL["row"]

    def read(self, job_id: str) -> dict[str, Any] | None:
        conn = self._connect()
        try:
            row = conn.execute(self._row_sql(), (job_id,)).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    # -- the queue file ----------------------------------------------------
    def _write_queue(self, job_id: str, record: dict[str, Any]) -> None:
        """The runner's job file, written whole or not at all (tmp, then rename)."""
        self.queue_dir.mkdir(parents=True, exist_ok=True)
        tmp = self.queue_dir / f".{job_id}.json.tmp"
        tmp.write_text(json.dumps(record, indent=1, sort_keys=True), encoding="utf-8")
        tmp.chmod(0o640)
        tmp.replace(self.queue_dir / f"{job_id}.json")

    # -- the runner's record: the transition's lane hooks -------------------
    def _guard(self, job_id: str, cur: sqlite3.Row, state: str, fields: dict[str, Any]) -> None:
        """A lane's refusal of a legal move, raised inside the transaction."""

    def _apply(
        self, conn: sqlite3.Connection, job_id: str, cur: sqlite3.Row, state: str, fields: dict[str, Any], now: str
    ) -> Any:
        """The lane's UPDATE (and any rows beside it). Returns what ``_result`` needs."""
        raise NotImplementedError

    def _result(self, row: dict[str, Any], cur: sqlite3.Row, state: str, extra: Any) -> dict[str, Any]:
        """The row after, with ``prev_state``."""
        row["prev_state"] = cur["state"]
        return row

    def _spend(self, conn: sqlite3.Connection, job_id: str, now: str, cents: Any, before: int) -> None:
        """The cents increase, dated when it is WRITTEN, not when the job began."""
        if self.SPEND and isinstance(cents, int) and cents > before:
            conn.execute(self._SQL["spend_insert"], (job_id, now, cents - before))

    def _transition(self, job_id: str, decide: Callable[[sqlite3.Row], tuple[str, dict[str, Any]]]) -> dict[str, Any]:
        """One move, every lane's: ``decide`` names the target state and the
        checked fields from the current row (most lanes know them up front;
        the records lane resolves them from the report against the row)."""
        conn = self._connect()
        try:
            if self.BEGIN:
                conn.execute(self.BEGIN)
            cur = conn.execute(self._row_sql(), (job_id,)).fetchone()
            if cur is None:
                conn.rollback()
                raise ValueError(f"no such job {job_id}")
            try:
                state, f = decide(cur)
                # ss#2616: a same-state re-record is a NOTE (a lost deliver
                # wake, for instance): legal, merges fields. Moves otherwise
                # follow the lane's table.
                if state != cur["state"] and state not in self._next()[cur["state"]]:
                    raise ValueError(f"illegal transition {cur['state']} -> {state}")
                self._guard(job_id, cur, state, f)
                now = self._now()
                self._spend(conn, job_id, now, f.get("cents"), int(cur["cents"] or 0) if self.SPEND else 0)
                extra = self._apply(conn, job_id, cur, state, f, now)
            except Exception:
                conn.rollback()
                raise
            conn.commit()
            row = dict(conn.execute(self._row_sql(), (job_id,)).fetchone())
        finally:
            conn.close()
        return self._result(row, cur, state, extra)

    def _closed_guard(self, job_id: str, cur: sqlite3.Row, state: str) -> None:
        """A job SMD closed never moves again (a same-state note still lands)."""
        if cur["close_ref"] and state != cur["state"]:
            raise ValueError(f"job {job_id} was closed by SMD ({cur['close_ref']}); it never moves again")


class StateRecord(LaneLedger):
    """The runner's record as ``(job_id, state, fields)``: every lane but
    records, whose report names no state (it resolves one from the report
    against the current row, so it calls ``_transition`` itself)."""

    def record(self, job_id: str, state: str, fields: dict[str, Any]) -> dict[str, Any]:
        """Apply one transition; returns the row after. A same-state record is
        a note (merges fields). ValueError on an unknown job or an illegal move."""
        f = self._check(state, fields)
        early = self._early(job_id, f)
        if early is not None:
            return early
        return self._transition(job_id, lambda _cur: (state, f))

    def _check(self, state: str, fields: dict[str, Any]) -> dict[str, Any]:
        """The record's fields as the transition uses them, checked before any
        connection opens. The default refuses an unknown state only."""
        if state not in self._rules().STATES:
            raise ValueError(f"unknown state {state!r}")
        return fields

    def _early(self, job_id: str, fields: dict[str, Any]) -> dict[str, Any] | None:
        """A record answered without a transition (None: there is none)."""
        return None


class ReplyOnce(LaneLedger):
    """The completion reply, once per (job, key): ``mark_replied`` is the
    compare-and-set the reply takes AT its send (the reply binding's key is
    ``<attempt>:<state>``), ``unmark_replied`` undoes a mark whose send Graph
    refused outright (nothing was sent)."""

    def mark_replied(self, job_id: str, key: str = "reply") -> bool:
        conn = self._connect()
        try:
            cur = conn.execute(self._SQL["mark"], (self._now(), key, job_id, key))
            conn.commit()
            return cur.rowcount == 1
        finally:
            conn.close()

    def unmark_replied(self, job_id: str, key: str) -> None:
        conn = self._connect()
        try:
            conn.execute(self._SQL["unmark"], (job_id, key))
            conn.commit()
        finally:
            conn.close()


class RecentJobs(LaneLedger):
    """The newest jobs, newest first."""

    def recent(self, limit: int = 20) -> list[dict[str, Any]]:
        conn = self._connect()
        try:
            rows = conn.execute(self._SQL["recent"], (int(limit),))
            return [dict(r) for r in rows]
        finally:
            conn.close()


class SpendMeter(LaneLedger):
    """A month's spend is the sum of the increases written inside it."""

    def spend_between(self, start: str, end: str, exclude: str = "") -> int:
        """Cents WRITTEN in [start, end), without ``exclude``'s."""
        conn = self._connect()
        try:
            row = conn.execute(self._SQL["spend_sum"], (start, end, exclude)).fetchone()
            return int(row["c"])
        finally:
            conn.close()


__all__ = ["LaneLedger", "RecentJobs", "ReplyOnce", "SpendMeter", "StateRecord", "require_cents"]
