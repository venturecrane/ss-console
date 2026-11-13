"""Which job a reply SMD releases from review answers (2026-11-06).

THE DEFECT (A&P). A drafting job's completion reply went out as an SMD hand
release (``shared.matter_file_review``: a row held with ``author="smd"``, then
root's ``approve``). That is a plain reply, outside the broker's bound-reply
claim, so the job's ``reply_sent_at`` stayed empty and the broker held no
claim for its key. A ``jobs rewake`` would then have sent the code-written
completion a SECOND time.

So an approve says which job the reply is the reply of, and the release
records it as that job's reply atomically with the send (the broker's
``workspace_broker/hand_reply.py``). This module answers the two questions the
approve asks before it writes a decision:

* :func:`resolve_job`: what ``--job <id>`` names (``<kind>:<id>``, or a bare
  id looked up in the seat's job ledgers);
* :func:`unreplied_jobs`: the jobs in the held reply's thread that ended and
  have no reply recorded for that ending. An approve that names no job in such
  a thread is refused: it is exactly the reply that went out unrecorded.

READ-ONLY, as root on the seat: the broker's audit db (its job ledgers and its
``REPLY_BINDING`` rows) and the agent's request index (which emails share a
thread). A file that exists and cannot be read RAISES (:class:`ReviewError`):
"could not look" never reads as "no job here".
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from typing import Any

from records_contract.review_row import JOB_ID_RE, JOB_KINDS, ReviewError

from shared import request_index, seat_layout

AUDIT_DB_ENV = "SMD_AUDIT_DB_PATH"
# The live db. Until 2026-11-10 this read /opt/data/audit.db, the pre-move path
# every boot renames away (entrypoint.sh), so a root `approve` run without
# SMD_AUDIT_DB_PATH was refused with "the seat's audit db is not at ...".
DEFAULT_AUDIT_DB = seat_layout.AUDIT_VOLUME_DB
#: The ledgers a requested job's reply is marked on (``reply_sent_at`` /
#: ``reply_key``), by kind, whose "ended and owes a reply" is delivered or held.
#: A medchron job carries the mark too (2026-11-13) but is replied on only when
#: delivered, never held, so this rule does not fit it; a negotiation or note
#: job has no request thread of the kind a hand reply answers. None of the three
#: is looked for here; ``--job`` still names them.
_TABLES = {
    "casefile_job": "casefile_jobs",
    "demand_job": "demand_jobs",
    "drafting_job": "drafting_jobs",
    "litigation_job": "litigation_jobs",
}
_ID_TABLES = {
    **_TABLES,
    "medchron_job": "medchron_jobs",
    "negotiation_job": "negotiation_jobs",
    "note_job": "note_jobs",
}
#: Literal queries, one per ledger (no SQL is composed from input): a job that
#: ended (delivered or held, the broker's REPLYABLE_DEMAND_STATES) with no reply
#: marked for that ending (a resumed job's earlier held reply carries an older
#: ``attempt:state`` key and owes nothing now).
_OWED_SQL = {
    kind: (
        "SELECT id, state, request_ref FROM " + table + " WHERE state IN ('delivered', 'held') "
        "AND (reply_key IS NULL OR reply_key != attempt || ':' || state)"
    )
    for kind, table in _TABLES.items()
}
_ID_SQL = {kind: "SELECT 1 FROM " + table + " WHERE id=?" for kind, table in _ID_TABLES.items()}


def audit_db() -> Path:
    return Path(os.environ.get(AUDIT_DB_ENV, "").strip() or DEFAULT_AUDIT_DB)


def _open(path: Path) -> sqlite3.Connection:
    if not path.is_file():
        raise ReviewError(f"the seat's audit db is not at {path}, so the job cannot be checked")
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5)
        conn.row_factory = sqlite3.Row
        return conn
    except sqlite3.Error as exc:
        raise ReviewError(f"the seat's audit db could not be read: {exc}") from exc


def _tables(conn: sqlite3.Connection) -> set[str]:
    return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def resolve_job(text: str, *, path: Path | None = None) -> dict[str, str]:
    """``{"kind", "job_id"}`` for ``--job``'s value: ``<kind>:<id>`` as given, or
    a bare id found in exactly one of the seat's job ledgers."""
    raw = str(text or "").strip()
    kind, _, ident = raw.rpartition(":")
    if kind and kind not in JOB_KINDS:
        raise ReviewError(f"--job names a kind from {sorted(JOB_KINDS)}, or a bare job id")
    if not JOB_ID_RE.fullmatch(ident):
        raise ReviewError("--job names a job id exactly as the job's ledger shows it")
    conn = _open(path or audit_db())
    try:
        have = _tables(conn)
        kinds = [kind] if kind else sorted(_ID_TABLES)
        found = [
            k
            for k in kinds
            if _ID_TABLES[k] in have and conn.execute(_ID_SQL[k], (ident,)).fetchone()
        ]
    except sqlite3.Error as exc:
        raise ReviewError(f"the seat's job ledgers could not be read: {exc}") from exc
    finally:
        conn.close()
    if len(found) != 1:
        raise ReviewError(
            f"job {ident} is {'in more than one ledger; name it as <kind>:<id>' if found else 'on no ledger of this seat'}"
        )
    return {"kind": found[0], "job_id": ident}


def _thread_refs(message_id: str, requests_path: str | None) -> set[str]:
    """Every id of every indexed email in ``message_id``'s thread (Graph ids
    and RFC ids, bracketed and bare), including ``message_id`` itself."""
    ids = {message_id}
    try:
        own = request_index.read_request(message_id, path=requests_path)
        thread = str((own or {}).get("thread_ref") or "")
        if own and own.get("internet_message_id"):
            ids.add(str(own["internet_message_id"]))
        if thread:
            target = requests_path or request_index.db_path()
            for row in request_index._read_rows(  # noqa: SLF001 - the index's own read contract
                target, ("vendor_message_id", "internet_message_id"), "thread_ref = ?", (thread,)
            ):
                ids.update(str(v) for v in row.values() if v)
    except (OSError, sqlite3.Error) as exc:
        raise ReviewError(
            f"the request index could not be read, so the reply's thread is unknown: {exc}"
        ) from exc
    return ids | {i.strip("<>") for i in ids} | {f"<{i.strip('<>')}>" for i in ids}


def unreplied_jobs(
    message_id: str, *, path: Path | None = None, requests_path: str | None = None
) -> list[dict[str, str]]:
    """The jobs in ``message_id``'s thread that ended (delivered or held) with
    no reply recorded for that ending: each ``{"kind", "job_id", "state"}``.

    A job is in the thread when its request email is one of the thread's
    indexed emails, or when the broker bound a reply for it to ``message_id``
    (a ``REPLY_BINDING`` row: the job's own completion turn got that far)."""
    refs = _thread_refs(message_id, requests_path)
    conn = _open(path or audit_db())
    try:
        have = _tables(conn)
        bound: set[str] = set()
        if "audit_log" in have:
            bound = {
                str(r[0])
                for r in conn.execute(
                    "SELECT json_extract(metadata,'$.job_id') FROM audit_log WHERE action_type='REPLY_BINDING' "
                    "AND json_extract(metadata,'$.graph_message_id') = ? AND json_extract(metadata,'$.job_id') IS NOT NULL",
                    (message_id,),
                )
            }
        out: list[dict[str, str]] = []
        for kind, table in sorted(_TABLES.items()):
            if table not in have:
                continue  # that lane is not on this seat
            for r in conn.execute(_OWED_SQL[kind]):
                if str(r["request_ref"] or "") in refs or str(r["id"]) in bound:
                    out.append({"kind": kind, "job_id": str(r["id"]), "state": str(r["state"])})
        return out
    except sqlite3.Error as exc:
        raise ReviewError(f"the seat's job ledgers could not be read: {exc}") from exc
    finally:
        conn.close()


def named(job: dict[str, Any]) -> str:
    return f"{job.get('kind')}:{job.get('job_id')}"


__all__ = ["AUDIT_DB_ENV", "DEFAULT_AUDIT_DB", "audit_db", "named", "resolve_job", "unreplied_jobs"]
