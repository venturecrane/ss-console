"""Body construction for Smokeball's full-replace task PUT.

Proven live 2026-08-31 (vfy_01M1CWACT2NSB1WFSZXD3KQK5F): ``PUT /tasks/{id}``
is a FULL REPLACE, not a patch — ``StaffId`` is required on every update,
``isCompleted=true`` additionally requires ``CompletedByStaffId``, and any
omitted field is CLEARED on the tenant (a bare completion PUT nulled the
subject, due date, and matter link). The task read never echoes ``staffId``,
so the caller must supply the owner. This module builds the merged body from
the task's current state plus the requested changes; ``server.update_task``
owns the wire calls and the matter-reference guard.
"""

from __future__ import annotations

from typing import Any, Callable

PROVENANCE_MARK = "[Operator]"

# Rehearsal / self-test artifacts written into a tenant carry this subject
# marker (ss #2403): ``[SMD-PROBE <ISO-8601 creation stamp>]``, at the start of
# the subject (after the ``[Operator]`` provenance stamp create_task adds).
# The 2026-08-14 incident: a rehearsal probe task outlived its test, the digest
# itself flagged it as "a machine-authored probe task; its own note instructs
# deletion after witnessing", and 37 minutes later the verification chase cited
# it as its real tracking anchor. The marker is deliberately subject-visible —
# firm staff reading the task list are the OTHER consumer that can mistake a
# probe for real work; a note-only token would hide it from exactly them.
#
# ``list_tasks`` drops marked rows by default so the agent turn never ingests
# a probe as work (``include_probe_artifacts=True`` is the census/teardown
# opt-in), and the drop is COUNTED on the response envelope — a filter that
# can hide rows silently is a suppression channel, and a deadline watcher that
# goes quiet is the dangerous failure. The match is position-anchored (only a
# subject that STARTS with the marker, provenance stamp aside, is a probe);
# a mid-subject occurrence does not match, so real work cannot be hidden by
# quoting the marker.
_PROBE_MARK = "[SMD-PROBE"


def _is_probe_subject(subject: Any) -> bool:
    """True iff the subject is marked as a rehearsal/self-test probe artifact."""
    if not isinstance(subject, str):
        return False
    text = subject.lstrip()
    if text.upper().startswith(PROVENANCE_MARK.upper()):
        text = text[len(PROVENANCE_MARK) :].lstrip()
    return text.upper().startswith(_PROBE_MARK.upper())


def _task_subject_of(item: Any) -> str:
    if not isinstance(item, dict):
        return ""
    for key in ("subject", "Subject", "name", "Name", "title", "Title"):
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return ""


def drop_probe_tasks(resp: Any) -> Any:
    """Remove probe-marked rows from a /tasks response, loudly.

    Only the dict (HATEOAS ``{"value": [...]}``) envelope gains the
    ``probeArtifactsExcluded`` count key; a bare-list response is filtered in
    place (its shape cannot carry a count without breaking consumers).
    """
    if isinstance(resp, dict):
        for key in ("value", "items", "results", "tasks", "data"):
            rows = resp.get(key)
            if isinstance(rows, list):
                kept = [r for r in rows if not _is_probe_subject(_task_subject_of(r))]
                excluded = len(rows) - len(kept)
                if excluded:
                    resp[key] = kept
                    resp["probeArtifactsExcluded"] = excluded
                return resp
        return resp
    if isinstance(resp, list):
        return [r for r in resp if not _is_probe_subject(_task_subject_of(r))]
    return resp


def merge_task_update(
    current: Any,
    *,
    subject: str | None,
    note: str | None,
    due_date: str | None,
    is_completed: bool | None,
    assignee_ids: list[str] | None,
    staff_id: str | None,
    stamp: Callable[[str | None], str | None],
) -> tuple[dict[str, Any], str | None]:
    """Return ``(put_body, matter_id)`` for a task update.

    Raises ``ValueError`` when no owning staff id can be established — sending
    the PUT without one either 400s or, worse, strips the task.
    """
    if not isinstance(current, dict):
        current = {}
    owner = staff_id or current.get("staffId")
    if not owner:
        raise ValueError(
            "update_task needs staff_id: Smokeball requires StaffId on every task "
            "PUT and the task read does not echo it. Pass the owning staff member "
            "(e.g. the matter's personResponsibleStaffId)."
        )
    cur_matter = current.get("matter")
    matter_id = cur_matter.get("id") if isinstance(cur_matter, dict) else None
    completed = is_completed if is_completed is not None else current.get("isCompleted")
    cur_due = current.get("dueDateOnly")
    if isinstance(cur_due, str) and "T" in cur_due:
        cur_due = cur_due.split("T", 1)[0]  # the read echoes a datetime; PUT wants date-only
    cur_assignees: list[str] = []
    for a in current.get("assignees") or []:
        if isinstance(a, dict) and a.get("id"):
            cur_assignees.append(a["id"])
        elif isinstance(a, str):
            cur_assignees.append(a)
    fields = {
        "staffId": owner,
        "subject": stamp(subject) if subject is not None else current.get("subject"),
        "note": note if note is not None else current.get("note"),
        "dueDateOnly": due_date if due_date is not None else cur_due,
        "matterId": matter_id,
        "isCompleted": completed,
        "completedByStaffId": owner if completed else None,
        "assigneeIds": assignee_ids if assignee_ids is not None else (cur_assignees or None),
    }
    return {k: v for k, v in fields.items() if v is not None}, matter_id
