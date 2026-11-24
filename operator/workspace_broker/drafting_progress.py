"""Where a running drafting job is, on its ledger row (2026-11-24).

A drafting job pulling about 500 files read cents 0 with ``updated_at`` frozen
for 26 minutes: the row moved only when the job's state did, so ``ops-requests``
and the hourly triage could not tell a long pull from a hung one. The runner's
daemon now sends a PROGRESS NOTE while the child runs (``jobs.drafting.progress``):
``drafting_job_record`` in ``running`` carrying only ``NOTE_FIELDS``.

* A note lands in ONE statement conditioned on ``state='running'``: it never
  moves a row's state, and one in flight as the job ends cannot move a finished
  row back (the medchron stage note's rule, ``medchron_progress``).
* ``stage`` and ``progress`` ("n/N" files pulled) say where it is; the note
  bumps ``updated_at`` every time, at least every two minutes.
* ``expected_finish`` and ``stage_seconds`` (each finished stage's seconds) stay
  on the row after the job's run dir is wiped; an ending record may carry them
  too (``kept``), so the final timings survive.
* ``moved``: whether the stage or the count changed. Only then does the broker
  write a ``DRAFTING_JOB_PROGRESS`` audit row, so the triage reads a frozen
  count as a stall and never a refresh as work.

Stage names, counts, times and seconds only: never a file name or client text.
"""

from __future__ import annotations

import json
import re
import sqlite3
from typing import Any

AUDIT_TYPE = "DRAFTING_JOB_PROGRESS"
NOTE_FIELDS = frozenset({"stage", "progress", "expected_finish", "stage_seconds"})
#: What an ending record may carry onto the row.
KEPT_FIELDS = ("expected_finish", "stage_seconds")
_STAGE_RE = re.compile(r"^[a-z][a-z0-9_-]{0,59}$")
_PROGRESS_RE = re.compile(r"^\d{1,7}/\d{1,7}$")
_FINISH_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{1,6})?Z$")
MAX_STAGES = 80

COLUMNS = (
    ("stage", "ALTER TABLE drafting_jobs ADD COLUMN stage TEXT"),
    ("progress", "ALTER TABLE drafting_jobs ADD COLUMN progress TEXT"),
    ("expected_finish", "ALTER TABLE drafting_jobs ADD COLUMN expected_finish TEXT"),
    ("stage_seconds_json", "ALTER TABLE drafting_jobs ADD COLUMN stage_seconds_json TEXT"),
)


def ensure(conn: sqlite3.Connection) -> None:
    """The note's columns on a ledger created before them."""
    cols = {r[1] for r in conn.execute("PRAGMA table_info(drafting_jobs)")}
    for col, sql in COLUMNS:
        if col not in cols:
            conn.execute(sql)


def is_note(state: str, fields: dict[str, Any]) -> bool:
    return state == "running" and "stage" in fields and set(fields) <= NOTE_FIELDS


def _text(rx: re.Pattern[str], name: str, value: Any) -> str:
    if not isinstance(value, str) or not rx.match(value):
        raise ValueError(f"{name} must match {rx.pattern}")
    return value


def _seconds(value: Any) -> str:
    """``stage_seconds`` as stored: {stage: seconds}, or ValueError (never cast)."""
    if not isinstance(value, dict) or len(value) > MAX_STAGES:
        raise ValueError(f"stage_seconds must be an object of at most {MAX_STAGES} stages")
    out: dict[str, float] = {}
    for name, secs in value.items():
        _text(_STAGE_RE, "a stage_seconds key", name)
        if not isinstance(secs, (int, float)) or isinstance(secs, bool) or secs < 0:
            raise ValueError("stage_seconds values must be seconds")
        out[name] = round(float(secs), 1)
    return json.dumps(out, sort_keys=True)


def kept(fields: dict[str, Any]) -> list[tuple[str, Any]]:
    """(column, stored value) for each kept field the record carries."""
    out: list[tuple[str, Any]] = []
    if "expected_finish" in fields:
        out.append(("expected_finish", _text(_FINISH_RE, "expected_finish", fields["expected_finish"])))
    if "stage_seconds" in fields:
        out.append(("stage_seconds_json", _seconds(fields["stage_seconds"])))
    return out


_KEEP_SQL = {
    "expected_finish": "UPDATE drafting_jobs SET expected_finish=? WHERE id=?",
    "stage_seconds_json": "UPDATE drafting_jobs SET stage_seconds_json=? WHERE id=?",
}


def keep(conn: sqlite3.Connection, job_id: str, fields: dict[str, Any]) -> None:
    """An ending record's timings and expected finish, on the row, in the same
    transaction as the record."""
    for col, value in kept(fields):
        conn.execute(_KEEP_SQL[col], (value, job_id))


_NOTE_SQL = (
    "UPDATE drafting_jobs SET stage=?, progress=?, updated_at=?, "
    "expected_finish=COALESCE(?, expected_finish), stage_seconds_json=COALESCE(?, stage_seconds_json) "
    "WHERE id=? AND state='running'"
)


def apply_note(ledger: Any, job_id: str, fields: dict[str, Any]) -> dict[str, Any]:
    """A note in ONE statement that cannot move a row's state. The row after,
    with ``prev_state`` (a note is not a transition) and ``moved``."""
    stage = _text(_STAGE_RE, "stage", fields["stage"])
    progress = _text(_PROGRESS_RE, "progress", fields["progress"]) if fields.get("progress") is not None else None
    extra = dict(kept(fields))
    conn = ledger._connect()
    try:
        before = conn.execute("SELECT stage, progress FROM drafting_jobs WHERE id=?", (job_id,)).fetchone()
        cur = conn.execute(
            _NOTE_SQL,
            (stage, progress, ledger._now(), extra.get("expected_finish"), extra.get("stage_seconds_json"), job_id),
        )
        conn.commit()
        landed = cur.rowcount == 1
        row = conn.execute("SELECT * FROM drafting_jobs WHERE id=?", (job_id,)).fetchone()
    finally:
        conn.close()
    if row is None:
        raise ValueError(f"no such job {job_id}")
    if not landed:
        raise ValueError(f"job {job_id} is {row['state']}; a progress note lands only on a running job")
    moved = before is None or (before["stage"], before["progress"]) != (stage, progress)
    return {**dict(row), "prev_state": "running", "moved": moved}


__all__ = ["AUDIT_TYPE", "NOTE_FIELDS", "apply_note", "ensure", "is_note", "keep", "kept"]
