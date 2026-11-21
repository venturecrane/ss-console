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


_NOTE_SQL = "UPDATE medchron_jobs SET stage=?, updated_at=? WHERE id=? AND state='running'"
_NOTE_FINISH_SQL = "UPDATE medchron_jobs SET stage=?, expected_finish=?, updated_at=? WHERE id=? AND state='running'"


def apply_note(ledger: Any, job_id: str, fields: dict[str, Any]) -> dict[str, Any]:
    """A stage note in ONE statement that cannot move a row's state: it lands
    only while the row is running, and a row that is not refuses it."""
    stage = check_note("stage", fields["stage"])
    now = ledger._now()
    conn = ledger._connect()
    try:
        if "expected_finish" in fields:
            finish = check_note("expected_finish", fields["expected_finish"])
            cur = conn.execute(_NOTE_FINISH_SQL, (stage, finish, now, job_id))
        else:
            cur = conn.execute(_NOTE_SQL, (stage, now, job_id))
        conn.commit()
        landed = cur.rowcount == 1
        row = conn.execute("SELECT * FROM medchron_jobs WHERE id=?", (job_id,)).fetchone()
    finally:
        conn.close()
    if row is None:
        raise ValueError(f"no such job {job_id}")
    if not landed:
        raise ValueError(f"job {job_id} is {row['state']}; a stage note lands only on a running job")
    return dict(row)


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
    """(surname, dob), case and spaces folded: a middle name, a nickname, a
    title, "Surname, First" or a double space never makes one person two.
    Twins share the key, so the refusal never claims the live job is this
    client's: it names the job's own clients (``person_cover``)."""
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


def person_cover(ledger: Any, envelope: dict[str, Any], stale_before: str) -> list[dict[str, Any]]:
    """Per client of the envelope, in its order: ``client_name`` and the
    active job on this matter, of this kind, for a client with the same
    surname and date of birth, as ``job_id`` / ``job_state`` with
    ``job_clients``, the names that job was submitted for (None for all three
    when no active job matches). The match is by surname and birth date, so
    whose the job is is said from ``job_clients``, never assumed.

    A row with no recorded kind (written before 2026-11-19) never matches:
    the work-digest rule still covers it, and guessing its kind could refuse
    the update a held run was rerouted to."""
    from . import medchron_identity

    units = [u for u in envelope.get("units") or [] if isinstance(u, dict)]
    conn = ledger._connect()
    try:
        medchron_identity.ensure(conn)
        rows = conn.execute(_ACTIVE_ON_MATTER_SQL, (str(envelope["matter"]["id"]), stale_before)).fetchall()
    finally:
        conn.close()
    kind = kind_of(envelope)
    live = [(str(j), str(s), _units(raw)) for j, s, k, raw in rows if k == kind]
    out = []
    for u in units:
        me = _person(u)
        hit = next((x for x in live if me is not None and me in {_person(v) for v in x[2]}), None)
        names = [str(v.get("client_name") or "") for v in hit[2]] if hit else None
        out.append(
            {
                "client_name": str(u.get("client_name") or ""),
                "job_id": hit[0] if hit else None,
                "job_state": hit[1] if hit else None,
                "job_clients": names,
            }
        )
    return out


def same_person(ledger: Any, envelope: dict[str, Any], stale_before: str) -> tuple[str, str] | None:
    """``(job_id, state)`` when EVERY client the envelope names already has an
    active job (the first one's), or None. A request in which only some are
    covered is not a duplicate: ``medchron_verbs`` refuses it naming which
    client matched and which did not, so the rest can be submitted alone."""
    cover = person_cover(ledger, envelope, stale_before)
    if cover and all(c["job_id"] for c in cover):
        return str(cover[0]["job_id"]), str(cover[0]["job_state"])
    return None


def _where(job_id: Any, state: Any) -> str:
    return {
        "submitted": f"already queued as job {job_id} and has not started yet",
        "running": f"already running as job {job_id}",
        "held": f"already submitted as job {job_id}, which is HELD and waiting on a decision",
    }.get(str(state), f"already submitted as job {job_id} (state {state})")


def duplicate_refusal(twin: tuple[str, str] | None, cover: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The submit refusal for work already in flight, or None to go on.

    ``twin`` is the ledger's whole-request duplicate (the same work, or every
    client already covered). ``cover`` says, per client, which active job is
    theirs. When only SOME clients are covered nothing is queued either, and
    the refusal names exactly which client matched which job and which did
    not (``uncovered_clients``), so the Operator submits those alone and says
    the truth about each: a covered client's chronology is under way, an
    uncovered client's is not until it is submitted."""
    covered = [c for c in cover if c["job_id"]]
    uncovered = [c["client_name"] for c in cover if not c["job_id"]]
    # Matched by surname and birth date only, so the job is named with the
    # clients IT was submitted for, never asserted to be this request's client.
    each = "; ".join(
        f"a chronology for a client with the surname and date of birth of {c['client_name']} is "
        f"{_where(c['job_id'], c['job_state'])} (submitted for {', '.join(c['job_clients'] or ['a client not recorded'])})"
        for c in covered
    )
    if twin is None:
        if not covered:
            return None
        return {
            "ok": True,
            "accepted": False,
            "reason": f"on this matter {each}; nothing was queued and nothing "
            f"was charged. Not covered by any job: {', '.join(uncovered)}. Resubmit with only "
            f"{'that client' if len(uncovered) == 1 else 'those clients'}",
            "clients": cover,
            "uncovered_clients": uncovered,
        }
    twin_id, twin_state = twin
    reason = f"on this matter {each}" if covered else f"this chronology package is {_where(*twin)}"
    out: dict[str, Any] = {
        "ok": True,
        "accepted": False,
        "reason": f"{reason}; nothing new was queued and the allowance was not charged twice",
        "job_id": twin_id,
        "job_state": twin_state,
    }
    if covered:
        out["clients"] = cover
    return out


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
    from . import medchron_identity

    conn = ledger._connect()
    try:
        medchron_identity.ensure(conn)
        rows = [dict(r) for r in conn.execute(_DELIVERED_WITH_UNITS_SQL, (matter_id,)).fetchall()]
    finally:
        conn.close()
    # A delivered row is this client's when its recorded clients, or its kept
    # units, name them. A row that names no client at all (written before
    # 2026-10-17) is NEVER subtracted: it may be another client's delivery,
    # and its coverage would drop this client's documents from the update
    # unread. Whenever such a row exists the answer says so.
    for r in rows:
        kept = {" ".join(str(u.get("client_name") or "").split()).lower() for u in _units(r.get("units_json"))}
        r["_names"] = (set(_keys(r.get("client_keys"))) | kept) - {""}
    unknown = [r for r in rows if not r["_names"]]
    mine = [r for r in rows if client in r["_names"]]
    out: dict[str, Any] = {"ok": True, "coverage_complete": not unknown}
    if unknown:
        out["coverage_note"] = (
            f"{len(unknown)} delivered chronology record(s) on this matter name no client, so what they "
            "covered is not subtracted; name this client's medical records from the listing"
        )
    record = union_coverage(mine)
    if record is None:
        return {**out, "coverage_record": False, "new_document_ids": None, "count": None}
    covered = set(record["covered_document_ids"])
    uncovered = set(record["uncovered_document_ids"])
    new = list(dict.fromkeys(x.strip() for x in listing if x.strip() not in covered))
    return {
        **out,
        "coverage_record": True,
        "new_document_ids": new,
        "count": len(new),
        "previously_uncovered": [x for x in new if x in uncovered],
    }


#: The matter's delivered rows, oldest first, with the units each kept.
_DELIVERED_WITH_UNITS_SQL = (
    "SELECT j.*, u.units_json FROM medchron_jobs j LEFT JOIN medchron_job_units u ON u.job_id = j.id "
    "WHERE j.matter_id=? AND j.state='delivered' ORDER BY j.created_at ASC"
)
