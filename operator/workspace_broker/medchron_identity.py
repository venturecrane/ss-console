"""Who each chronology job was for, kept so an UPDATE never asks again.

THE PROBLEM (2026-10-29). An update on a matter that already holds a delivered
chronology asked the firm to confirm the client's date of birth: the update
rides the RUN's identity rules, the matter's contact record carries no date,
and the confirmation rule written for a first run (2026-10-27) fired. The firm
had already been through that once, for the delivered chronology. The date the
delivered job ran on is the right one for the update by definition: the
delivered entries were all checked against it.

The broker never kept it. The ledger row records the clients' names
(``client_keys``) and the queue file goes to the runner, whose job directory is
unreadable to this uid and is wiped 72 hours after delivery. So this table
keeps each job's units, written at submit, read back for an update.

WHERE THE DATE GOES, AND WHERE IT DOES NOT. Only into an update envelope this
broker validates and queues, which is where the runner has always received it.
:func:`delivered_clients` (the status read the Operator sees) says only WHETHER
a date is on record, never the date, and nothing here audits or logs a unit.
The model needs no date of birth to ask for an update, so it is never handed
one.

A job submitted before this table existed has no row. Its clients still show
(from ``client_keys``) with ``dob_on_record: false``, so the skill knows to read
the date from the file instead; it is never guessed here. While its job dir is
still on the seat, ``python -m medchron.units_backfill`` (root) keeps its units
from the job's own envelope through :func:`backfill`.
"""

from __future__ import annotations

import json
from typing import Any

CREATE_SQL = (
    "CREATE TABLE IF NOT EXISTS medchron_job_units ("
    "job_id TEXT PRIMARY KEY, "
    "matter_id TEXT NOT NULL, "
    "units_json TEXT NOT NULL"
    ")"
)

#: The unit fields kept. ``dob`` is the reason the table exists; the rest are
#: what lets an update name the same client the delivered job named.
_KEPT = ("unit", "client_name", "name_token", "surname", "dob", "folder_prefix")


def _key(name: Any) -> str:
    """A client name as ``client_keys`` stores it: spaces folded, lowercased."""
    return " ".join(str(name or "").split()).lower()


def _ensure(conn: Any) -> None:
    conn.execute(CREATE_SQL)


def remember(ledger: Any, job_id: str, envelope: dict[str, Any]) -> None:
    """Keep the queued envelope's units against its job id."""
    units = [{k: u[k] for k in _KEPT if k in u} for u in envelope.get("units") or [] if isinstance(u, dict)]
    conn = ledger._connect()
    try:
        _ensure(conn)
        conn.execute(
            "INSERT OR REPLACE INTO medchron_job_units (job_id, matter_id, units_json) VALUES (?,?,?)",
            (str(job_id), str(envelope["matter"]["id"]), json.dumps(units, sort_keys=True)),
        )
        conn.commit()
    finally:
        conn.close()


def _delivered_rows(ledger: Any, matter_id: str) -> list[tuple[Any, ...]]:
    """``(client_keys, units_json)`` per delivered job on the matter, newest first."""
    conn = ledger._connect()
    try:
        _ensure(conn)
        conn.commit()
        return conn.execute(
            "SELECT j.client_keys, u.units_json FROM medchron_jobs j "
            "LEFT JOIN medchron_job_units u ON u.job_id = j.id "
            "WHERE j.matter_id=? AND j.state='delivered' "
            "ORDER BY j.created_at DESC, j.updated_at DESC",
            (str(matter_id),),
        ).fetchall()
    finally:
        conn.close()


def _delivered_units(ledger: Any, matter_id: str) -> dict[str, dict[str, Any]]:
    """Per client key, the unit from the NEWEST delivered job that kept one."""
    out: dict[str, dict[str, Any]] = {}
    for _keys, raw in _delivered_rows(ledger, matter_id):
        if not raw:
            continue
        for unit in json.loads(raw):
            key = _key(unit.get("client_name"))
            if key and key not in out and unit.get("dob"):
                out[key] = unit
    return out


def delivered_clients(ledger: Any, matter_id: str) -> list[dict[str, Any]]:
    """The clients the matter's delivered chronologies were for, without dates.

    One entry per client: the name and surname the delivered job used, the
    folder prefix on a joint matter, and ``dob_on_record``. A client known only
    from ``client_keys`` (a job delivered before units were kept) carries the
    stored key as its name and ``dob_on_record: false``.
    """
    kept = _delivered_units(ledger, matter_id)
    out: dict[str, dict[str, Any]] = {}
    for key, unit in kept.items():
        entry = {"client_name": unit.get("client_name"), "surname": unit.get("surname"), "dob_on_record": True}
        if unit.get("folder_prefix"):
            entry["folder_prefix"] = unit["folder_prefix"]
        out[key] = entry
    for raw_keys, _units in _delivered_rows(ledger, matter_id):
        for key in json.loads(raw_keys) if raw_keys else []:
            out.setdefault(key, {"client_name": key, "dob_on_record": False})
    return [out[k] for k in sorted(out)]


def fill_update_dobs(ledger: Any, envelope: dict[str, Any]) -> dict[str, Any]:
    """An UPDATE envelope with each missing ``dob`` taken from the delivered job.

    Only an envelope carrying the ``update`` block, and only a unit that names
    no date: a date the Operator did pass is never overwritten. A client with no
    kept unit is left as it came, so the envelope check refuses it in the
    ordinary words and the Operator reads the date from the file. The input is
    not mutated.
    """
    if not isinstance(envelope.get("update"), dict):
        return envelope
    units = envelope.get("units")
    matter = envelope.get("matter")
    if not isinstance(units, list) or not isinstance(matter, dict) or not matter.get("id"):
        return envelope
    if all(not isinstance(u, dict) or str(u.get("dob") or "").strip() for u in units):
        return envelope
    kept = _delivered_units(ledger, str(matter["id"]))
    filled = []
    for unit in units:
        if isinstance(unit, dict) and not str(unit.get("dob") or "").strip():
            known = kept.get(_key(unit.get("client_name")))
            if known is not None:
                unit = {**unit, "dob": known["dob"]}
        filled.append(unit)
    return {**envelope, "units": filled}


# ---- the backfill, for jobs submitted before this table existed ----------------
#: What ``backfill`` did with one job. Closed, so the CLI counts by outcome and
#: never prints what a unit holds.
BACKFILL_OUTCOMES = ("recorded", "already_recorded", "unknown_job", "no_dob", "names_differ")


def recorded(ledger: Any, job_id: str) -> bool:
    """Whether this job's units are already kept."""
    conn = ledger._connect()
    try:
        _ensure(conn)
        conn.commit()
        return conn.execute("SELECT 1 FROM medchron_job_units WHERE job_id=?", (str(job_id),)).fetchone() is not None
    finally:
        conn.close()


def backfill(ledger: Any, job_id: str, units: Any, *, dry_run: bool = False) -> str:
    """Keep the units of a job submitted before this table existed.

    The units come from the job's own envelope in the runner's job directory
    (``medchron.units_backfill``, root on the seat), which survives about 72
    hours after delivery. Returns one of ``BACKFILL_OUTCOMES``:

    * ``already_recorded``: a row exists (written at submit, or by an earlier
      backfill). Never replaced, so a second run changes nothing.
    * ``unknown_job``: no ledger row. Nothing is kept for a job this broker
      never queued.
    * ``no_dob``: no unit names a date, so there is nothing an update could
      reuse.
    * ``names_differ``: the units name clients the ledger row does not, so they
      are not that job's units.

    The matter is the ledger row's, never the caller's. ``dry_run`` decides
    the same outcome and writes nothing (``recorded`` then means "would be").
    """
    job_id = str(job_id or "")
    row = ledger.read(job_id) if job_id else None
    if row is None:
        return "unknown_job"
    if recorded(ledger, job_id):
        return "already_recorded"
    clean = [u for u in units if isinstance(u, dict)] if isinstance(units, list) else []
    if not any(str(u.get("dob") or "").strip() for u in clean):
        return "no_dob"
    keys = json.loads(row["client_keys"]) if row.get("client_keys") else None
    if keys is not None and sorted({_key(u.get("client_name")) for u in clean} - {""}) != sorted(keys):
        return "names_differ"
    if not dry_run:
        remember(ledger, job_id, {"matter": {"id": row["matter_id"]}, "units": clean})
    return "recorded"
