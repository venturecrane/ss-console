"""Work SMD owes a firm person, as the broker recorded it (owner's ruling, 2026-11-19).

THE NO-LOOKBACK READ (shared with ``work_health_proof``'s owed served sets):

    read_open_owed(slug) -> Optional[dict]
        {"owed": [row, ...], "owed_jobs": [job, ...]}, or None when the seat
        could not be read (the caller then falls back to the windowed rows).
        row: {"id", "created_at", "kind", "request_ref", "document_class",
              "matter_number", "refusal_code", "detail"}  (broker smd_owed table)
        job: {"id", "state", "matter_number", "message_ref", "delivery_json"}
             one DELIVERED drafting_jobs row whose delivery_json.served_sets
             has a set with status != "filed" or told != true.
        Source: the seat's ``smd_owed`` runtime kind -> broker ``smd_owed_open``
        (``workspace_broker/smd_owed.open_items``), every open row, no window.
    owed_jobs_kwargs(open_owed) -> dict
        ``{"owed_jobs": open_owed["owed_jobs"]}`` for
        ``work_health_proof.delivery_proof_items``, or ``{}`` when the read
        failed or that function does not take it yet.

The broker records the work in the step it arises (``workspace_broker/smd_owed.py``):
a drafting request refused on our side (the attorney was given the normal
acknowledgment), or a question the help guide does not answer (he was told the
Operator would research it and get back to him), or an addition to a draft whose
job had stopped (he was given the revise acknowledgment; the item names the
job, and delivering it by hand closes the item). Each is listed here until SMD closes it
in ``work-health-closed.yaml`` (``job: <owed id>``), or, for a drafting request
opened as a job and delivered by hand, until that job is delivered (the broker
stops returning it) or a job for the same request is accepted (the broker
closes it then).

Deliberately NOT closed by a later reply (``answered_after``) or a later ok
tool call: the person was told the work is in hand. With the open read there is
no time-based drop-off at all; only when the read fails do the audit rows in
the ledger window stand in for it.
"""

from __future__ import annotations

from datetime import datetime
import inspect
import re
from typing import Any, Callable, Optional

from bin.lib import seam_pull
from bin.lib.work_health import Config, Item, hhmm, item, meta, parse_ts, reason_token, stamped

CODE = "smd_owed"
KIND = "job_hand_step"
#: The ended job a ``drafting_addition`` item's detail names
#: (``workspace_broker.smd_owed.owe_addition``: "...; job <id> had ended failed").
_ENDED_JOB = re.compile(r"\bjob (\S+) had ended\b")

_WHAT = {
    "drafting_refused": (
        "A drafting request was refused on our side at {at} ({detail}); the attorney was given the normal "
        "acknowledgment, so the draft is owed. Request email {ref}."
    ),
    "drafting_addition": (
        "The requester added to a draft whose job had stopped, at {at} ({detail}); they were told we'll make those "
        "changes and reply when the new version is filed, so the change is owed. Addition email {ref}."
    ),
    "help_question": (
        "A firm person asked something the help guide does not answer at {at}, and was told the Operator would "
        "research it and get back to them. Request email {ref}."
    ),
    "rule_stated": (
        "A firm person stated a drafting rule or replied about one at {at} ({detail}), and was told it would be "
        "confirmed in the thread once in place. Request email {ref}."
    ),
    "rule_not_recorded": (
        "A rule a firm person confirmed was not recorded at {at} ({detail}); they were told it is not recorded yet "
        "and that the Operator will reply. Request email {ref}."
    ),
    "rule_install_failed": (
        "A rule a firm person was told is recorded did not install at {at} ({detail}). Request email {ref}."
    ),
    "reply_not_fulfilled": (
        "A firm person's request was answered at {at} without what they asked ({detail}): the reply told them "
        "the Operator could not do it, or that it would look into it and reply in the thread, so the answer is "
        "owed. Request email {ref}."
    ),
    "rule_needs_person": (
        "A rule a firm person stated was read back twice with no plain yes at {at} ({detail}); they were told it "
        "will be confirmed in the thread once in place. Request email {ref}."
    ),
}
_TODO = {
    "rule_needs_person": (
        "Read the thread, confirm the rule with them by note in that thread, record it with the administrator "
        "path, then close it in work-health-closed.yaml (job: {owed})."
    ),
    "rule_not_recorded": (
        "Record the rule, answer in that thread through the SMD note lane, then close it in "
        "work-health-closed.yaml (job: {owed})."
    ),
    "rule_install_failed": (
        "Install the rule (or tell them in that thread through the SMD note lane that it is not in force), then "
        "close it in work-health-closed.yaml (job: {owed})."
    ),
    "drafting_addition": (
        "Make the change by hand: draft job {job} with what that email asks, file the new version in the matter, "
        "then record it with jobs.drafting.deliver_by_smd {job} (runbook drafting-deliver-by-smd; it emails the "
        "attorney only with --notify, once the files are on the matter); recording that delivery closes this. "
        "Close it in work-health-closed.yaml (job: {owed}) if it is answered another way."
    ),
    "drafting_refused": (
        "Fix the cause, or draft and file it by hand: open it as a job (workspace_broker.smd_owed open-job {owed}, "
        "runbook drafting-deliver-by-smd), then record it with jobs.drafting.deliver_by_smd {owed} (it emails the "
        "attorney only with --notify, once the files are on the matter). Close it in "
        "work-health-closed.yaml (job: {owed}) if it is answered another way."
    ),
    "reply_not_fulfilled": (
        "Do what they asked and answer in that thread through the SMD note lane, then close it in "
        "work-health-closed.yaml (job: {owed})."
    ),
    "help_question": (
        "Answer in that thread through the SMD note lane (it sends from the Operator's mailbox), then close it in "
        "work-health-closed.yaml (job: {owed})."
    ),
    "rule_stated": (
        "Read that email, record the rule with the administrator path, and reply in the thread by note. Then close "
        "it in work-health-closed.yaml (job: {owed})."
    ),
}


def read_open_owed(slug: str, client: Any = None) -> Optional[dict[str, list[dict]]]:
    """The seat's open owed items and owed served-set jobs, no lookback; None
    when they could not be read. Never raises."""
    try:
        client = client or seam_pull.seam_client_from_env(slug)
        if client is None:
            return None
        page = client.read_page("smd_owed")
    except Exception:  # noqa: BLE001 - unread is None, never "nothing owed"
        return None
    if not isinstance(page, dict) or page.get("unavailable"):
        return None
    owed = [r for r in page.get("entries") or [] if isinstance(r, dict)]
    jobs = [r for r in page.get("owed_jobs") or [] if isinstance(r, dict)]
    return {"owed": owed, "owed_jobs": jobs}


def owed_refs(open_owed: Optional[dict]) -> frozenset[str]:
    """The request emails of the seat's open owed items: a could-not reply to
    one is listed as that owed item, not a second time as a could-not line."""
    rows = (open_owed or {}).get("owed") or []
    return frozenset(str(r.get("request_ref")) for r in rows if isinstance(r, dict) and r.get("request_ref"))


def owed_jobs_kwargs(open_owed: Optional[dict], fn: Optional[Callable[..., Any]] = None) -> dict[str, Any]:
    """``{"owed_jobs": ...}`` for ``delivery_proof_items`` when the read worked
    and ``fn`` takes it, else ``{}`` (the windowed rows stand in)."""
    if open_owed is None:
        return {}
    if fn is not None and "owed_jobs" not in inspect.signature(fn).parameters:
        return {}
    return {"owed_jobs": open_owed["owed_jobs"]}


def _line(seat: str, owed_id: str, ts: datetime, md: dict) -> Item:
    kind = str(md.get("kind") or "")
    what = _WHAT.get(kind, "SMD owes a firm person an answer, recorded at {at} ({detail}). Request email {ref}.")
    todo = _TODO.get(
        kind, "Answer it through the SMD note lane, then close it in work-health-closed.yaml (job: {owed})."
    )
    detail = str(md.get("detail") or "no detail").rstrip(".")
    ended = _ENDED_JOB.search(detail)
    fill = {
        "at": hhmm(ts),
        "detail": detail,
        "ref": str(md.get("request_ref") or "(none recorded)"),
        "owed": owed_id,
        "job": ended.group(1) if ended else "(the job named in the detail)",
    }
    return item(seat, KIND, f"owed:{owed_id}", ts, what.format(**fill), todo.format(**fill), job=owed_id)


def smd_owed_items(
    seat: str,
    rows: list[dict],
    cfg: Config,
    now: datetime,
    closed: Optional[set[tuple[str, str]]] = None,
    open_owed: Optional[dict] = None,
) -> list[Item]:
    """One line per open owed item (keyed on its id, so the email names it new
    once and then as still open). From the broker's open read when given; else
    from the ledger window's audit rows."""
    proven: dict[str, datetime] = {}
    owed: dict[str, tuple[datetime, dict]] = {}
    for ts, row in stamped(rows):
        md = meta(row)
        owed_id = str(md.get("job_id") or "")
        if row.get("action_type") == "DELIVERY_PROVEN" and owed_id:
            proven[owed_id] = ts
        elif open_owed is None and row.get("action_type") == "DELIVERY_PROOF_FAILED":
            if reason_token(md.get("code")) == CODE and owed_id:
                owed.setdefault(owed_id, (ts, md))
    for row in (open_owed or {}).get("owed") or []:
        ts = parse_ts(row.get("created_at"))
        if row.get("id") and ts is not None:
            owed.setdefault(str(row["id"]), (ts, row))
    return [
        _line(seat, owed_id, ts, md)
        for owed_id, (ts, md) in owed.items()
        if not (closed and (seat, owed_id) in closed) and not (owed_id in proven and proven[owed_id] >= ts)
    ]


__all__ = ["CODE", "owed_jobs_kwargs", "owed_refs", "read_open_owed", "smd_owed_items"]
