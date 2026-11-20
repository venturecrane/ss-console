"""Work SMD owes a firm person, recorded by the broker in the step it arises
(owner's ruling, 2026-11-19).

A failure on SMD's side is not the attorney's problem, so the seat does not
tell him about it: the drafting lane answers an our-side refusal with its
normal acknowledgment, and the help guide answers a question it does not cover
with "Let me research that and get back to you in this thread." Those sentences
are true only if SMD is certain to learn of the request, so the record never
depends on the reply's wording or on a row the overlay writes after the tool
returns. In the same call, the broker writes:

* one ``smd_owed`` row (every kind; the drafting kind keeps its whole envelope
  so staff can open the request as a job, :func:`open_job`), read with NO
  lookback by the hourly work-health triage through ``smd_owed_open`` (the
  seat's ``smd_owed`` runtime read; ``bin/lib/work_health_owed.py``) and
  listed until SMD closes it in work-health-closed.yaml or it is proven
  delivered, and
* one ``DELIVERY_PROOF_FAILED`` audit row with code ``smd_owed``, which the
  seat's heartbeat sends up the shortfall pipe as ``proof:smd_owed``; the
  fleet-alerts worker emails team@ once per item
  (``workers/fleet-alerts/src/shortfalls.ts`` ``emailsAPerson``).

The caller is told whether the record was written (``smd_told``). When it was
not, the overlay says nothing about that point, and the unanswered request is
listed as no reply.

Kinds: ``drafting_refused`` (the broker's own, from ``drafting_job_submit``)
and ``help_question`` (the agent's, through ``smd_owed_record``, keyed on the
email that asked).
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sqlite3
import sys
from pathlib import Path
from typing import Any, Callable

from .audit_ledger import _iso_utc, _ulid
from .broker_context import BrokerContext

logger = logging.getLogger(__name__)

AUDIT_TYPE = "DELIVERY_PROOF_FAILED"
CODE = "smd_owed"
KIND_DRAFTING = "drafting_refused"
KIND_HELP = "help_question"
#: The kinds the agent may record (the drafting kind is the broker's own).
AGENT_KINDS = frozenset({KIND_HELP})
_LANE = {KIND_DRAFTING: "drafting", KIND_HELP: "operator"}
DETAIL_MAX = 300
_MESSAGE_ID = re.compile(r"^<?[^\s<>]{3,500}@[^\s<>]{1,250}>?$")

CREATE_SQL = (
    "CREATE TABLE IF NOT EXISTS smd_owed ("
    "id TEXT PRIMARY KEY, created_at TEXT NOT NULL, kind TEXT NOT NULL, request_ref TEXT NOT NULL, "
    "document_class TEXT NOT NULL DEFAULT '', matter_number TEXT NOT NULL DEFAULT '', "
    "refusal_code TEXT NOT NULL DEFAULT '', detail TEXT NOT NULL DEFAULT '', envelope_json TEXT, "
    "closed_at TEXT, closed_why TEXT)"
)
#: A drafting request whose envelope failed validation once: the model is told
#: to correct its call, and only a second failure of the same email is owed.
CREATE_ATTEMPTS_SQL = (
    "CREATE TABLE IF NOT EXISTS drafting_invalid_attempts (request_ref TEXT PRIMARY KEY, at TEXT NOT NULL)"
)
#: What the open read returns for an owed item (no envelope, no request text).
OWED_COLUMNS = ("id", "created_at", "kind", "request_ref", "document_class", "matter_number", "refusal_code", "detail")

_OPEN_SQL_NO_JOBS = (
    "SELECT id, created_at, kind, request_ref, document_class, matter_number, refusal_code, detail "
    "FROM smd_owed WHERE closed_at IS NULL ORDER BY created_at"
)
_OPEN_SQL = (
    "SELECT id, created_at, kind, request_ref, document_class, matter_number, refusal_code, detail "
    "FROM smd_owed WHERE closed_at IS NULL "
    "AND id NOT IN (SELECT id FROM drafting_jobs WHERE state='delivered') ORDER BY created_at"
)

VERBS: tuple[str, ...] = ("smd_owed_record", "smd_owed_open")


def alert_row(kind: str, owed_id: str, request_ref: str, detail: str) -> dict[str, Any]:
    """The audit row that pages SMD."""
    meta = {
        "lane": _LANE[kind],
        "job_id": owed_id,
        "attempt": 1,
        "code": CODE,
        "kind": kind,
        "request_ref": request_ref,
        "detail": str(detail or "")[:DETAIL_MAX],
    }
    return {
        "action_type": AUDIT_TYPE,
        "actor": "workspace-broker",
        "actor_role": "broker",
        "metadata": json.dumps(meta, sort_keys=True, separators=(",", ":")),
    }


def _connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, timeout=5.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute(CREATE_SQL)
    return conn


def _text(value: Any, limit: int = 200) -> str:
    return str(value or "").strip()[:limit] if isinstance(value, (str, int)) else ""


def record(
    db_path: str | None,
    append: Callable[[dict[str, Any]], Any] | None,
    kind: str,
    request_ref: str,
    detail: str,
    **fields: Any,
) -> bool:
    """Write the owed row and its audit row. True only when both landed; never raises."""
    if not db_path or append is None:
        logger.warning("smd_owed: no database or ledger, %s for %s not recorded", kind, request_ref[-16:])
        return False
    owed_id = _ulid()
    try:
        conn = _connect(db_path)
        try:
            with conn:
                conn.execute(
                    "INSERT INTO smd_owed (id, created_at, kind, request_ref, document_class, matter_number, "
                    "refusal_code, detail, envelope_json) VALUES (?,?,?,?,?,?,?,?,?)",
                    (
                        owed_id,
                        _iso_utc(),
                        kind,
                        request_ref,
                        _text(fields.get("document_class")),
                        _text(fields.get("matter_number")),
                        _text(fields.get("refusal_code")),
                        str(detail or "")[:DETAIL_MAX],
                        json.dumps(fields["envelope"], sort_keys=True) if "envelope" in fields else None,
                    ),
                )
        finally:
            conn.close()
        row_id = append(alert_row(kind, owed_id, request_ref, detail))
    except Exception:  # not recorded means smd_told false, never a crash
        logger.exception("smd_owed: %s for %s could not be recorded", kind, request_ref[-16:])
        return False
    return bool(row_id)


def owe_drafting(db_path: str | None, append: Callable[[dict[str, Any]], Any] | None, raw: Any, code: str) -> bool:
    """Record a drafting request refused on our side, with its envelope."""
    env = raw if isinstance(raw, dict) else {}
    found = env.get("matter")
    matter: dict[str, Any] = found if isinstance(found, dict) else {}
    klass, number = _text(env.get("document_class")), _text(matter.get("number"))
    detail = f"{klass or 'a document'} for matter {number or '(none named)'} refused on our side: {code}"
    return record(
        db_path,
        append,
        KIND_DRAFTING,
        _text(env.get("request_ref"), 600),
        detail,
        document_class=klass,
        matter_number=number,
        refusal_code=code,
        envelope=env,
    )


def first_invalid(db_path: str | None, request_ref: str) -> bool:
    """True the first time this email's envelope fails validation (the model
    corrects its own call; nothing is owed yet). Never raises: unreadable is
    treated as a repeat, so the request is owed rather than lost."""
    if not request_ref:
        # No email id to key on (the broker sees no other id of the turn's
        # email): always the model's call to correct, and nothing can be owed
        # without a ref. Keyed on "" every such envelope after the first was owed.
        return True
    if not db_path:
        return False
    try:
        conn = sqlite3.connect(db_path, timeout=5.0)
        try:
            with conn:
                conn.execute(CREATE_ATTEMPTS_SQL)
                cur = conn.execute(
                    "INSERT OR IGNORE INTO drafting_invalid_attempts (request_ref, at) VALUES (?, ?)",
                    (request_ref, _iso_utc()),
                )
            return cur.rowcount == 1
        finally:
            conn.close()
    except sqlite3.Error:
        logger.exception("smd_owed: the invalid-attempt mark could not be read")
        return False


def close_for_job(db_path: str | None, request_ref: str, document_class: str, matter_number: str, job_id: str) -> int:
    """A job was accepted for the same email, class AND matter: its open owed
    item is answered by that job, closed in the same step. One email asking for
    the same kind of document on two matters still owes the other one (review
    2026-11-19). Returns how many closed."""
    if not db_path:
        return 0
    try:
        conn = _connect(db_path)
        try:
            with conn:
                return conn.execute(
                    "UPDATE smd_owed SET closed_at=?, closed_why=? WHERE kind=? AND request_ref=? "
                    "AND document_class=? AND matter_number=? AND closed_at IS NULL",
                    (
                        _iso_utc(),
                        f"job {job_id} accepted for the same request",
                        KIND_DRAFTING,
                        request_ref,
                        document_class,
                        matter_number,
                    ),
                ).rowcount
        finally:
            conn.close()
    except sqlite3.Error:
        logger.exception("smd_owed: the owed item for job %s could not be closed", job_id)
        return 0


def open_items(db_path: str) -> dict[str, Any]:
    """Every open owed item, and every delivered drafting job with a served set
    still unfiled or untold, with NO lookback: the triage lists each until it
    is closed. An owed item whose id is a delivered job (opened by
    :func:`open_job` and delivered by hand) is closed."""
    conn = _connect(db_path)
    try:
        jobs = {
            r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='drafting_jobs'")
        }
        owed = conn.execute(_OPEN_SQL if jobs else _OPEN_SQL_NO_JOBS).fetchall()
        delivered = (
            conn.execute(
                "SELECT id, state, matter_number, request_ref, delivery_json FROM drafting_jobs "
                "WHERE state='delivered' AND delivery_json LIKE '%served_sets%'"
            ).fetchall()
            if jobs
            else []
        )
    finally:
        conn.close()
    return {"owed": [dict(r) for r in owed], "owed_jobs": [_job(r) for r in delivered if _owes_a_set(r)]}


def _job(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "state": row["state"],
        "matter_number": row["matter_number"],
        "message_ref": row["request_ref"],
        "delivery_json": row["delivery_json"],
    }


def _owes_a_set(row: sqlite3.Row) -> bool:
    try:
        sets = (json.loads(row["delivery_json"] or "{}") or {}).get("served_sets") or []
    except ValueError:
        return True  # unreadable: listed, never silently dropped
    return any(isinstance(s, dict) and (s.get("status") != "filed" or s.get("told") is not True) for s in sets)


def record_verb(
    broker: BrokerContext, _action: str, request: dict[str, Any], _pid: int, _uid: int | None
) -> dict[str, Any]:
    """``smd_owed_record``: the agent records a help question, keyed on the
    email that asked. ``smd_told`` says whether it was."""
    kind = str(request.get("kind") or "")
    if kind not in AGENT_KINDS:
        raise ValueError(f"kind must be one of {sorted(AGENT_KINDS)}")
    ref = str(request.get("request_ref") or "")
    if not _MESSAGE_ID.match(ref):
        raise ValueError("request_ref must be the asking email's message id")
    ledger = broker.ledger
    append = ledger.append if ledger is not None else None
    told = record(broker.audit_db_path, append, kind, ref, str(request.get("detail") or ""))
    return {"ok": True, "smd_told": told}


def open_verb(
    broker: BrokerContext, _action: str, _request: dict[str, Any], _pid: int, _uid: int | None
) -> dict[str, Any]:
    """``smd_owed_open``: the no-lookback read the seat's ``smd_owed`` runtime
    kind serves to the hourly triage."""
    if not broker.audit_db_path:
        raise ValueError("no audit database on this broker")
    return {"ok": True, **open_items(broker.audit_db_path)}


HANDLERS = {"smd_owed_record": record_verb, "smd_owed_open": open_verb}


# -- staff: open a refused request as a job, so the hand delivery can be recorded


def open_job(db_path: str, jobs_dir: str, owed_id: str) -> str:
    """Create the drafting job a refused request never became: its ledger row in
    ``failed``, and its job dir (the envelope the runner would have claimed, and
    a daemon state of ``failed``), under the owed item's own id. Then
    ``python -m jobs.drafting.deliver_by_smd <owed_id> <record.json>`` records
    the hand delivery, and a change request in the same thread revises it.
    A rerun after a part-way failure finishes it; a job that has moved past
    ``failed`` is refused. Raises ValueError on anything it cannot do."""
    from .drafting_ledger import CREATE_SQL as JOBS_SQL
    from .drafting_ledger import digest, queue_record, validate_envelope

    conn = _connect(db_path)
    try:
        conn.execute(JOBS_SQL)
        row = conn.execute("SELECT * FROM smd_owed WHERE id = ? AND kind = ?", (owed_id, KIND_DRAFTING)).fetchone()
        if row is None or not row["envelope_json"]:
            raise ValueError(f"no refused drafting request {owed_id} is recorded")
        have = conn.execute("SELECT state FROM drafting_jobs WHERE id = ?", (owed_id,)).fetchone()
        if have is not None and have[0] != "failed":
            raise ValueError(f"job {owed_id} is already {have[0]}; nothing was written")
        env = validate_envelope({k: v for k, v in json.loads(row["envelope_json"]).items() if k != "revise_of"})
        now = _iso_utc()
        conn.execute(
            "INSERT OR IGNORE INTO drafting_jobs (id, created_at, updated_at, state, matter_id, matter_number, "
            "file_to_matter_id, document_class, requester, request_ref, envelope_digest, reason) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                owed_id,
                now,
                now,
                "failed",
                env["matter"]["id"],
                env["matter"]["number"],
                (env["file_to"] or env["matter"])["id"],
                env["document_class"],
                env["requested_by"],
                env["request_ref"],
                digest(env),
                f"refused on our side ({row['refusal_code']}); opened for a hand delivery",
            ),
        )
        jd = Path(jobs_dir) / owed_id
        jd.mkdir(parents=True, mode=0o700, exist_ok=True)
        _write(jd / "envelope.json", json.dumps(queue_record(env, owed_id), indent=1, sort_keys=True))
        _write(jd / "daemon.json", json.dumps({"state": "failed", "claimed": True}, indent=1))
        conn.commit()
    finally:
        conn.close()
    return owed_id


def _write(path: Path, text: str) -> None:
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m workspace_broker.smd_owed")
    sub = ap.add_subparsers(dest="cmd", required=True)
    op = sub.add_parser("open-job", help="open a refused drafting request as a failed job")
    op.add_argument("owed_id")
    op.add_argument("--db", required=True, help="the broker's audit database (the drafting ledger's)")
    op.add_argument("--jobs-dir", required=True, help="the jobs daemon's drafting job directory root")
    args = ap.parse_args(argv)
    try:
        print(open_job(args.db, args.jobs_dir, args.owed_id))
    except (ValueError, sqlite3.Error, OSError) as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
