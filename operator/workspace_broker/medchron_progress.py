"""Who a chronology job is for, what kind it is, where it is (2026-11-19).

THE PROBLEM. A firm asked the Operator about a chronology that was running,
and the status read could not say whose it was, whether it was a run or an
update, or how far it had got: a running row carried 0 documents, 0 pages and
0 cents, because the counts move only when the job's state does. The Operator
filled the gap from memory. A second request for the same client, spelled
another way, queued a second job beside the live one, because the duplicate
rule compared the whole piece of work, and a different file list is a
different piece of work. And an update's file list was computed in the
model's prose ("listing minus coverage"), where it went wrong.

What lives here, each read or written by code the model does not steer:

* ``kind_of``: ``run`` or ``update``, kept on the row at submit.
* the stage note: the runner reports the stage a job has reached
  (``jobs.medchron.progress``); it lands only on a running row.
* ``with_clients``: the client names a job is for, from its kept units, never
  a date of birth.
* ``same_person``: an active job on the matter for the same person and kind.
* ``update_files``: an update's new documents, from the firm's listing and the
  coverage record, in code.

Ids, names, states and counts only. Never a date of birth leaves this module,
and never a page of a record.
"""

from __future__ import annotations

import json
import re
from typing import Any

from .medchron_coverage import union_coverage

#: A stage name as the runner's DAG spells it (``jobs/medchron/dag.py``).
_STAGE_RE = re.compile(r"^[a-z][a-z0-9_]{0,59}$")
#: An expected finish time, ISO-8601 UTC to the second or millisecond.
_FINISH_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{1,6})?Z$")
#: The fields a stage note carries. A record with these alone, in ``running``,
#: is progress, not a transition.
NOTE_FIELDS = frozenset({"stage", "expected_finish"})
ACTIVE = ("submitted", "running", "held")


def kind_of(envelope: dict[str, Any]) -> str:
    return "update" if "update" in envelope else "run"


def is_stage_note(state: str, fields: dict[str, Any]) -> bool:
    return state == "running" and "stage" in fields and set(fields) <= NOTE_FIELDS


def check_note(col: str, value: Any) -> str:
    """A stage name or a finish time as stored, or ValueError (never cast)."""
    rx = _STAGE_RE if col == "stage" else _FINISH_RE
    if not isinstance(value, str) or not rx.match(value):
        raise ValueError(f"{col} must match {rx.pattern}")
    return value


_DOC_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


def dob_sources(envelope: dict[str, Any]) -> list[str]:
    """The document ids a submitted date of birth was read from: two labelled
    documents that state the same date (the skill's RUN step 2), kept on the
    SUBMITTED audit row. Optional; when present, two distinct document ids or
    the submission is refused (``EnvelopeError``), never trimmed to fit."""
    raw = envelope.get("dob_sources")
    if raw is None:
        return []
    ids = raw if isinstance(raw, list) else []
    if len(ids) != 2 or len(set(ids)) != 2 or not all(isinstance(x, str) and _DOC_ID_RE.match(x) for x in ids):
        from .medchron_ledger import EnvelopeError

        raise EnvelopeError("dob_sources must name the two distinct document ids the date of birth was read from")
    return list(ids)


def _person(unit: dict[str, Any]) -> tuple[str, str] | None:
    """(surname, dob) with case and every space folded: a middle name, a
    double space or a capital never makes one person two."""
    sur = "".join(str(unit.get("surname") or "").split()).casefold()
    dob = str(unit.get("dob") or "").strip()
    return (sur, dob) if sur and dob else None


def _units(raw: Any) -> list[dict[str, Any]]:
    try:
        units = json.loads(raw) if raw else []
    except ValueError:
        return []
    return [u for u in units if isinstance(u, dict)] if isinstance(units, list) else []


# An active row on the matter, with the duplicate rule's two exemptions for a
# hold (``medchron_ledger._ACTIVE_TWIN_SQL``): superseded by a later delivery,
# or past the held-job retention window. Written out, never composed.
_ACTIVE_ON_MATTER_SQL = (
    "SELECT j.id, j.state, j.kind, u.units_json FROM medchron_jobs j "
    "JOIN medchron_job_units u ON u.job_id = j.id "
    "WHERE j.matter_id = ? AND j.state IN ('submitted', 'running', 'held') "
    "AND NOT (j.state = 'held' AND (j.updated_at < ? OR EXISTS ("
    "SELECT 1 FROM medchron_jobs d WHERE d.matter_id = j.matter_id "
    "AND d.state = 'delivered' AND d.created_at > j.created_at))) "
    "ORDER BY j.created_at DESC"
)


def same_person(ledger: Any, envelope: dict[str, Any], stale_before: str) -> tuple[str, str] | None:
    """``(job_id, state)`` of an active job on this matter, of this kind, for
    a person this envelope names (surname and date of birth), or None.

    A row with no recorded kind (written before 2026-11-19) never matches:
    the work-digest rule still covers it, and guessing its kind could refuse
    the update a held run was rerouted to."""
    from . import medchron_identity

    want = {p for p in (_person(u) for u in envelope.get("units") or []) if p}
    if not want:
        return None
    conn = ledger._connect()
    try:
        medchron_identity.ensure(conn)
        rows = conn.execute(_ACTIVE_ON_MATTER_SQL, (str(envelope["matter"]["id"]), stale_before)).fetchall()
    finally:
        conn.close()
    kind = kind_of(envelope)
    for job_id, state, row_kind, raw in rows:
        if row_kind == kind and want & {p for p in (_person(u) for u in _units(raw)) if p}:
            return str(job_id), str(state)
    return None


_CLIENTS_SQL = (
    "SELECT j.id, j.client_keys, u.units_json FROM medchron_jobs j "
    "LEFT JOIN medchron_job_units u ON u.job_id = j.id WHERE j.id = ?"
)


def with_clients(ledger: Any, jobs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Each projected job with ``clients``: the names its kept units carry,
    or (a job submitted before units were kept) the names its row recorded."""
    from . import medchron_identity

    ids = [str(j["id"]) for j in jobs if j.get("id")]
    if not ids:
        return jobs
    conn = ledger._connect()
    try:
        medchron_identity.ensure(conn)
        # One literal query per job (at most twenty on a status read): no SQL is composed.
        rows = [r for i in ids for r in conn.execute(_CLIENTS_SQL, (i,)).fetchall()]
    finally:
        conn.close()
    names: dict[str, list[str]] = {}
    for job_id, keys, raw in rows:
        kept = [str(u["client_name"]) for u in _units(raw) if u.get("client_name")]
        names[str(job_id)] = kept or _keys(keys)
    return [{**j, "clients": names.get(str(j.get("id")), [])} for j in jobs]


def _keys(raw: Any) -> list[str]:
    try:
        keys = json.loads(raw) if raw else []
    except ValueError:
        return []
    return [str(k) for k in keys] if isinstance(keys, list) else []


def update_files(ledger: Any, request: dict[str, Any]) -> dict[str, Any]:
    """``medchron_update_files``: the documents an update for ONE client reads.

    ``listing_ids`` is the matter's file listing, paged to the end, as the
    agent read it this turn: the broker holds no practice-management session,
    so it cannot see a file that landed after the delivery. The answer is the
    listing minus every id a delivered chronology for this client covered,
    in listing order, and which of those were on the matter at delivery and
    went unread (``previously_uncovered``). ``coverage_record`` false means no
    delivered row for this client carries a record, so nothing is computed.
    Document ids and counts only: the ledger holds no page counts."""
    matter_id = str(request.get("matter_id") or "").strip()
    client = " ".join(str(request.get("client_name") or "").split()).lower()
    listing = request.get("listing_ids")
    if not matter_id or not client:
        raise ValueError("medchron_update_files requires matter_id and client_name")
    if not isinstance(listing, list) or not all(isinstance(x, str) and x.strip() for x in listing):
        raise ValueError("medchron_update_files requires listing_ids: the matter's document ids, paged to the end")
    conn = ledger._connect()
    try:
        rows = [
            dict(r)
            for r in conn.execute(
                "SELECT * FROM medchron_jobs WHERE matter_id=? AND state='delivered' ORDER BY created_at ASC",
                (matter_id,),
            ).fetchall()
        ]
    finally:
        conn.close()
    mine = [r for r in rows if r.get("client_keys") is None or client in _keys(r.get("client_keys"))]
    record = union_coverage(mine)
    if record is None:
        return {"ok": True, "coverage_record": False, "new_document_ids": None, "count": None}
    covered = set(record["covered_document_ids"])
    uncovered = set(record["uncovered_document_ids"])
    new = list(dict.fromkeys(x.strip() for x in listing if x.strip() not in covered))
    return {
        "ok": True,
        "coverage_record": True,
        "new_document_ids": new,
        "count": len(new),
        "previously_uncovered": [x for x in new if x in uncovered],
    }
