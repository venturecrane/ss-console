"""Proven delivery, the broker's half: the alert row and the reply's proof.

Two root-only verbs (the runner daemon is uid 0; nothing on the agent side can
raise or read these):

    delivery_proof_alert    one ``DELIVERY_PROOF_FAILED`` audit row: a job's
                            delivery could not be proven (its review held it,
                            its read-back failed, a dry run reached the ledger,
                            the DELIVER turn could not be started, or the
                            requester's reply was not seen in Sent Items). The
                            overlay heartbeat counts the row as a ``failed``
                            shortfall, which pages team@ within minutes. The
                            wire carries the code token only; the detail stays
                            in this seat's own audit row.
    delivery_reply_status   the bound-reply rows of one job: claimed, sent or
                            unknown, and whether the send's own Sent Items
                            lookup found the message. The daemon's reply watch
                            reads it; "the DELIVER turn ran" was never proof the
                            requester got anything (daemon.py consumed a wake
                            on a timeout after send).

WHY AN AUDIT ROW AND NOT A NEW PAGER. The shortfall pipe already reaches SMD
from every seat (heartbeat -> D1 operator_shortfalls -> fleet-alerts email),
and it is the only alert path a root process can feed without the agent: the
status-tool shims page only when a DELIVER turn runs and asks, which is exactly
the turn a lost wake never starts.
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
from typing import Any

from .broker_context import BrokerContext
from . import bound_replies

AUDIT_TYPE = "DELIVERY_PROOF_FAILED"
PROVEN_TYPE = "DELIVERY_PROVEN"
LANES = frozenset({"medchron", "demand", "drafting", "litigation", "negotiation", "note"})
#: Mirrors ``medchron/proof/gate.py`` CODES: the wire vocabulary.
CODES = frozenset(
    {
        "proof_missing",
        "readback_unproven",
        "readback_mismatch",
        "review_missing",
        "review_held",
        "dry_run_reported",
        "wake_lost",
        "reply_unproven",
        "reply_not_in_sent_items",
        "gate_held",
        "update_held",
        "upload_held",
        "stage_refused",
        "limit_held",
        "identity_held",
    }
)
REPLY_KINDS = frozenset({"demand_job", "drafting_job", "litigation_job", "medchron_job", "negotiation_job", "note_job"})
_IDENT = re.compile(r"^[0-9A-Za-z_-]{1,64}$")
DETAIL_MAX = 300
logger = logging.getLogger(__name__)

VERBS: tuple[str, ...] = ("delivery_proof_alert", "delivery_proof_record", "delivery_reply_status")


def _ident(request: dict[str, Any], field: str) -> str:
    v = request.get(field)
    if not isinstance(v, str) or not _IDENT.match(v):
        raise ValueError(f"{field} must be an id")
    return v


def append_alert(
    broker: BrokerContext, *, lane: str, job_id: str, code: str, attempt: int = 1, detail: str = ""
) -> str:
    """Write one DELIVERY_PROOF_FAILED row; returns its id ("" without a ledger).
    Also the broker's own entry point (a bound reply its send could not find)."""
    if lane not in LANES:
        raise ValueError(f"lane must be one of {sorted(LANES)}")
    if code not in CODES:
        raise ValueError(f"code must be one of {sorted(CODES)}")
    if broker.ledger is None:
        return ""
    meta = {
        "lane": lane,
        "job_id": job_id,
        "attempt": int(attempt),
        "code": code,
        "detail": str(detail or "")[:DETAIL_MAX],
    }
    return broker.ledger.append(
        {
            "action_type": AUDIT_TYPE,
            "actor": "workspace-broker",
            "actor_role": "broker",
            "metadata": json.dumps(meta, sort_keys=True, separators=(",", ":")),
        }
    )


def alert_verb(
    broker: BrokerContext, _action: str, request: dict[str, Any], _pid: int, _uid: int | None
) -> dict[str, Any]:
    attempt = request.get("attempt", 1)
    if not isinstance(attempt, int) or isinstance(attempt, bool) or attempt < 1:
        raise ValueError("attempt must be a positive integer")
    row_id = append_alert(
        broker,
        lane=str(request.get("lane") or ""),
        job_id=_ident(request, "job_id"),
        code=str(request.get("code") or ""),
        attempt=attempt,
        detail=str(request.get("detail") or ""),
    )
    return {"ok": True, "audit_id": row_id}


def _proven_files(raw: Any) -> list[dict[str, Any]]:
    """Ids, sizes and hashes only: a file NAME carries the client's name and
    the audit row is no second copy of the matter."""
    out: list[dict[str, Any]] = []
    for f in raw if isinstance(raw, list) else []:
        if not isinstance(f, dict):
            continue
        sha = str(f.get("sha256") or "")
        out.append(
            {
                "file_id": str(f.get("file_id") or "")[:64],
                "size": int(f.get("size") or 0) if str(f.get("size") or "0").isdigit() else 0,
                "sha256": sha if re.fullmatch(r"[0-9a-f]{64}", sha) else "",
            }
        )
    return out[:200]


def record_verb(
    broker: BrokerContext, _action: str, request: dict[str, Any], _pid: int, _uid: int | None
) -> dict[str, Any]:
    """One ``DELIVERY_PROVEN`` row: the evidence a delivered job's claim rests
    on (the far end re-read by id, size and hash; the review's status) and what
    the review cost. The ledger row says delivered; this row says why that is
    believed."""
    lane = str(request.get("lane") or "")
    if lane not in LANES:
        raise ValueError(f"lane must be one of {sorted(LANES)}")
    review = str(request.get("review") or "")
    if review not in ("passed", "released", "code_only"):
        raise ValueError("review must be passed, released or code_only")
    cents = request.get("review_cents", 0)
    if not isinstance(cents, int) or isinstance(cents, bool) or cents < 0:
        raise ValueError("review_cents must be a non-negative integer")
    attempt = request.get("attempt", 1)
    if not isinstance(attempt, int) or isinstance(attempt, bool) or attempt < 1:
        raise ValueError("attempt must be a positive integer")
    rows = request.get("rows", 0)
    meta = {
        "lane": lane,
        "job_id": _ident(request, "job_id"),
        "attempt": attempt,
        "review": review,
        "review_cents": cents,
        "review_model": str(request.get("review_model") or "")[:64],
        "files": _proven_files(request.get("files")),
        "rows": rows if isinstance(rows, int) and not isinstance(rows, bool) and rows >= 0 else 0,
    }
    if broker.ledger is None:
        return {"ok": True, "audit_id": ""}
    row_id = broker.ledger.append(
        {
            "action_type": PROVEN_TYPE,
            "actor": "workspace-broker",
            "actor_role": "broker",
            "metadata": json.dumps(meta, sort_keys=True, separators=(",", ":")),
        }
    )
    return {"ok": True, "audit_id": row_id}


def resume_extras(request: dict[str, Any]) -> dict[str, Any]:
    """What a root-only ``*_job_resume`` may add to its marker for a job the
    pre-delivery review held: ``release_review`` (SMD read the findings and
    files the work as it stands, the findings in the attorney notes) or
    ``redo_review`` (review afresh after a fix). Both are SMD acts: the resume
    verbs are root only and have no agent tool, so no email can talk a turn
    into releasing a hold."""
    out: dict[str, Any] = {}
    for key in ("release_review", "redo_review"):
        v = request.get(key, False)
        if not isinstance(v, bool):
            raise ValueError(f"{key} must be true or false")
        if v:
            out[key] = True
    if len(out) == 2:
        raise ValueError("release_review and redo_review are two different decisions; name one")
    if out.pop("redo_review", False):
        out["redo"] = ["review"]
    return out


def reply_rows(db_path: str, kind: str, ident: str) -> list[dict[str, Any]]:
    conn = bound_replies._connect(db_path)
    try:
        conn.row_factory = sqlite3.Row
        # A prefix compare, not LIKE: ``_`` in every kind (``medchron_job``) is
        # a LIKE wildcard, and a job id may carry one too.
        prefix = f"{kind}:{ident}:"
        rows = conn.execute(
            "SELECT binding_key, outcome, located, claimed_at FROM bound_replies "
            "WHERE substr(binding_key, 1, ?) = ? ORDER BY claimed_at",
            (len(prefix), prefix),
        ).fetchall()
        return [
            {
                "key": r["binding_key"],
                "outcome": r["outcome"],
                "located": None if r["located"] is None else bool(r["located"]),
                "claimed_at": r["claimed_at"],
            }
            for r in rows
        ]
    finally:
        conn.close()


def reply_status_verb(
    broker: BrokerContext, _action: str, request: dict[str, Any], _pid: int, _uid: int | None
) -> dict[str, Any]:
    kind = str(request.get("kind") or "")
    if kind not in REPLY_KINDS:
        raise ValueError(f"kind must be one of {sorted(REPLY_KINDS)}")
    ident = _ident(request, "job_id")
    out: dict[str, Any] = {
        "ok": True,
        "replies": reply_rows(str(broker.audit_db_path), kind, ident),
        "expected_key": _expected_key(broker, kind, ident),
        "channel": "msgraph" if broker.msgraph is not None else "agentmail",
    }
    if out["channel"] == "agentmail":
        out["sends_to_requester"] = sends_to(
            str(broker.audit_db_path), str(request.get("requester") or ""), str(request.get("since") or "")
        )
    return out


def sends_to(db_path: str, requester: str, since: str) -> int:
    """On an AgentMail seat: this broker's own dispatched sends to the
    requester since ``since`` (a ``CONFIRM_SEND_DISPATCHED`` row, outcome
    ``sent``, whose recipients include the address).

    WHY THIS IS THE EVIDENCE THERE. A bound reply exists only on a Microsoft
    Graph seat (``msgraph_reply_bind``/``msgraph_reply_bound``), and only Graph
    has a Sent Items folder to read the message back from. An AgentMail seat's
    DELIVER turn replies through the AgentMail transmit verbs, which write no
    bound-reply row, so a watch that required one would page every job there
    (2026-10-13 audit). The broker is the only transmit path on the Machine and
    writes this row itself, so the agent can forge none of it; it is the
    strongest proof the channel offers ("the vendor accepted a message to that
    person"), and weaker than Graph's, which is why a Graph seat never uses it.
    0 when either argument is missing: no evidence is no evidence."""
    who = requester.strip().lower()
    if "@" not in who or not since:
        return 0
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=5.0)
    try:
        row = conn.execute(
            "SELECT COUNT(*) FROM audit_log a, json_each(json_extract(a.metadata,'$.recipients')) r "
            "WHERE a.action_type='CONFIRM_SEND_DISPATCHED' "
            "AND json_extract(a.metadata,'$.outcome')='sent' "
            "AND substr(a.ts,1,19) >= substr(?,1,19) AND lower(trim(r.value)) = ?",
            (since, who),
        ).fetchone()
        return int(row[0] if row else 0)
    except sqlite3.Error as exc:
        logger.warning("delivery_reply_status: dispatched sends unreadable: %s", exc)
        return 0
    finally:
        conn.close()


def _expected_key(broker: BrokerContext, kind: str, ident: str) -> str:
    """The key this job's reply is owed under NOW, from the job's own ledger
    row: the same ``reply_binding.reply_key`` the send claims. "" when the row
    is unreadable or the job is not in a replyable state (the watch then falls
    back to its own attempt and state)."""
    from . import reply_binding  # reply_binding imports this module

    try:
        row = reply_binding.job_ledger(broker, kind).read(ident)
    except (OSError, sqlite3.Error, ValueError, KeyError) as exc:
        logger.warning("delivery_reply_status: the %s ledger row for %s is unreadable: %s", kind, ident, exc)
        return ""
    if not row or row.get("state") not in reply_binding.REPLYABLE_DEMAND_STATES:
        return ""
    return reply_binding.reply_key(kind, ident, row)


__all__ = [
    "AUDIT_TYPE",
    "CODES",
    "LANES",
    "PROVEN_TYPE",
    "VERBS",
    "alert_verb",
    "append_alert",
    "record_verb",
    "reply_rows",
    "reply_status_verb",
]
