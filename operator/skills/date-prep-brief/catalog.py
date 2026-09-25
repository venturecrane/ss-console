"""date-prep-brief: the CLOSED decision catalog, derived from file-status facts.

Pure (no I/O). Given one matter's file status (``file_status.pull_matter_status``),
the firm's authored step levels (``case_manager.date_prep.steps``) and the
records chaser's ledger state, it returns the only steps a brief may offer.
The turn chooses at most two of them to ask about; ``casework_brief``
refuses any ``catalog_id`` not listed here, so a step nobody derived cannot be
offered, and a step the firm did not turn on is never derived.

Levels (case-manager spec §4): a step at ``surfaces`` is never offered (the
brief can only SAY it); ``prepares`` offers a draft for a person; ``handles``
means the turn runs the step before the brief and reports it as done.

Every rule reads facts, never document content, and each entry carries its
``basis``: the facts that made code offer it, so a reader can check the offer
without trusting the turn. The catalog in ``references/decision-catalog.md`` is
this file in prose; the two change together.
"""

from __future__ import annotations

import re
from datetime import date

#: Which routine runs which step. The router runs that skill on the matter
#: when a person says yes (matter-inbox-router, its existing skill path).
STEP_SKILLS: dict[str, str] = {
    "binder_assemble": "trial-binder-assembler",
    "witness_list_finalize": "trial-binder-assembler",
    "exhibit_list_finalize": "trial-binder-assembler",
    "records_refresh": "medical-records-chaser",
    "motion_calendar_refresh": "motion-calendar-tracker",
    "discovery_status_refresh": "discovery-response-tracker",
}

#: Offered levels. ``surfaces`` is authored but never offered.
OFFERED_LEVELS = ("prepares", "handles")

#: Catalog size bound. A brief asks at most two questions; the rest is choice.
MAX_ENTRIES = 10
MAX_RECORDS_ENTRIES = 5

_LISTS = {
    "witness_list_finalize": re.compile(r"witness\s*list", re.IGNORECASE),
    "exhibit_list_finalize": re.compile(r"exhibit\s*list", re.IGNORECASE),
}
_DRAFT = re.compile(r"\bdraft\b", re.IGNORECASE)

#: Which memo marker says a status step already ran (file_status.MEMO_MARKERS).
_STATUS_STEPS = {
    "binder_assemble": "binder_assembled",
    "motion_calendar_refresh": "motion_calendar",
    "discovery_status_refresh": "discovery_status",
}


def _entry(step: str, level: str, params: dict, basis: list[str], suffix: str = "") -> dict:
    return {
        "catalog_id": step + (":" + suffix if suffix else ""),
        "skill": STEP_SKILLS[step],
        "level": level,
        "params": params,
        "basis": basis,
    }


def _newest_file_day(files: list[dict]) -> str | None:
    days = [f["date"] for f in files if f.get("date")]
    return max(days) if days else None


def list_entries(step: str, level: str, files: list[dict]) -> list[dict]:
    """A witness or exhibit list that exists only as a draft: offer to finalize
    the newest draft. A non-draft list on file means it is done; nothing offered."""
    matching = [f for f in files if _LISTS[step].search(f.get("name") or "")]
    if not matching or any(not _DRAFT.search(f["name"]) for f in matching):
        return []
    newest = max(matching, key=lambda f: f.get("date") or "")
    basis = ["file " + newest["file_id"] + " draft dated " + (newest.get("date") or "unknown")]
    return [_entry(step, level, {"file_id": newest["file_id"]}, basis)]


def status_entries(step: str, level: str, markers: dict, files: list[dict]) -> list[dict]:
    """A prep routine's status is stale when it never ran on this matter, or its
    last run predates the newest file (the file set changed since)."""
    last = markers.get(_STATUS_STEPS[step])
    newest = _newest_file_day(files)
    if last is not None and (newest is None or last >= newest):
        return []
    basis = ["last run " + (last or "never") + ", newest file " + (newest or "none")]
    return [_entry(step, level, {}, basis)]


def records_entries(level: str, records: list[dict], chases: dict, cadence_days: int | None, today: date) -> list[dict]:
    """An OUTSTANDING provider on the records roster whose chase is due: never
    chased, or last chased at least ``cadence_days`` ago. With no authored
    cadence only a never-chased provider is offered (no invented interval).

    Received providers are listed in the file status but never offered: the
    chaser works its open roster only, so "request an update of records already
    received" is not a step any routine can run today, and offering it would
    promise one."""
    out = []
    for record in records:
        if record.get("state") != "outstanding":
            continue
        chase = chases.get(record["roster_task_id"]) or {}
        last = chase.get("last_chased")
        if last is not None:
            if cadence_days is None or (today - date.fromisoformat(last)).days < cadence_days:
                continue
        basis = ["outstanding, chased " + str(chase.get("attempts", 0)) + "x, last " + (last or "never")]
        params = {"roster_task_id": record["roster_task_id"], "provider": record["provider"]}
        out.append(_entry("records_refresh", level, params, basis, suffix=record["roster_task_id"]))
    return out[:MAX_RECORDS_ENTRIES]


def build_catalog(
    status: dict,
    steps: dict,
    chases: dict,
    *,
    cadence_days: int | None,
    today: date,
) -> list[dict]:
    """The closed catalog for one matter. ``steps`` is the authored
    ``case_manager.date_prep.steps`` map; an unlisted step is never offered."""
    files = status.get("files") or []
    markers = status.get("memo_markers") or {}
    unread = set(status.get("unread") or [])
    catalog: list[dict] = []
    for step in STEP_SKILLS:
        level = steps.get(step)
        if level not in OFFERED_LEVELS:
            continue
        # An unread part is unknown, not empty: never offer a step on a gap
        # that is really a failed read.
        if step in _LISTS and "files" not in unread:
            catalog.extend(list_entries(step, level, files))
        elif step in _STATUS_STEPS and not unread & {"files", "memos"}:
            catalog.extend(status_entries(step, level, markers, files))
        elif step == "records_refresh" and "tasks" not in unread:
            catalog.extend(records_entries(level, status.get("records") or [], chases, cadence_days, today))
    return catalog[:MAX_ENTRIES]


def envelope_catalog(catalog: list[dict]) -> list[dict]:
    """The catalog as ``casework_brief`` reads it: no ``basis``."""
    return [{k: v for k, v in entry.items() if k != "basis"} for entry in catalog]
