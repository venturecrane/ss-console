"""A change request on a discovery response: EVERY document the request filed,
named once by file id (2026-11-21).

A discovery request files one document per served set (form, special,
production), and a later job in the same thread re-files into the same file
ids. So the drafts a reply asks to change are not one job's files: they are
every delivered job of the class on the matter whose request is in this
reply's email thread, deduplicated by file id, the NEWEST proof of each file
kept (the newest job's bytes are what the file held when that job read it
back; the runner revises the file as it is now, ``revise/set_run.py``).

A job in the thread whose delivery cannot be paired with its proof by file id
refuses the whole request (``prior_unidentified``): revising the documents
that can be identified and leaving the rest, silently, is not what was asked.

The newest delivery that filed attorney notes names them once (``notes``): the
runner files a new version of them with the revision's changes on top.

Each unit carries a set type hint from the delivery's own served-set rows, or
the file's name; "" when neither says. The runner reads the file's headings and
refuses a file whose headings disagree with a hint.
"""

from __future__ import annotations

import json
import re
from typing import Any, Callable

#: The class whose delivery files a document per served set.
SET_CLASS = "discovery_response"
#: Every class whose delivery files one document per set, revised whole: the
#: responses to served sets, and the propounded sets (``revise/propounded_run.py``,
#: 2026-11-26), each named by file id.
SET_CLASSES = frozenset({SET_CLASS, "discovery_set"})
#: The most filed sets one revision takes (the runner's ``job.MAX_UNITS``).
MAX_UNITS = 20
#: The jobs read back per matter: every delivery of the class a thread can hold.
MAX_JOBS = 50
#: A served set's label words -> its type, the runner's ``served.SET_TYPES``.
_NAMES = (
    (re.compile(r"form\s+interrogator", re.I), "form_interrogatories"),
    (re.compile(r"special\s+interrogator", re.I), "special_interrogatories"),
    (re.compile(r"production", re.I), "requests_for_production"),
    (re.compile(r"admission", re.I), "requests_for_admission"),
)


def set_type_of(name: str, served_rows: list[Any]) -> str:
    """The set a filed file answers: its served-set row's label, else its own name."""
    stem = name[:-5] if name.lower().endswith(".docx") else name
    labels = [str(r.get("set") or "") for r in served_rows if isinstance(r, dict) and r.get("document") in (name, stem)]
    for text in [*labels, name]:
        hits = {kind for pat, kind in _NAMES if pat.search(text)}
        if len(hits) == 1:
            return hits.pop()
    return ""


def collect(
    db: Any,
    delivered: list[dict[str, Any]],
    in_thread: Callable[[dict[str, Any]], bool],
    notes_from: Callable[[dict[str, Any]], bool] | None = None,
) -> tuple[dict[str, Any] | None, str]:
    """(the runner's ``revise`` block, "") or (None, the job id that cannot be
    identified by file id). ``delivered`` is newest first. ``notes_from``: the
    jobs whose attorney notes may be named (the thread's own, never a job that
    only holds a draft of the same set); None names none."""
    jobs = [r for r in delivered if in_thread(r)]
    units: dict[str, dict[str, Any]] = {}
    notes: dict[str, Any] | None = None
    for row in jobs:
        files = db.proven_files(row["id"])
        if not files or not row.get("folder_id"):
            return None, str(row["id"])
        served = (json.loads(row.get("delivery_json") or "{}") or {}).get("served_sets") or []
        for f in files:
            if notes is None and notes_from is not None and notes_from(row) and f.get("role") == "attorney_notes":
                notes = {k: f[k] for k in ("file_id", "name", "size", "sha256")} | {"folder_id": str(row["folder_id"])}
            if f.get("role") != "draft" or f["file_id"] in units:
                continue  # an older job's copy of a file a newer job re-filed
            units[f["file_id"]] = {
                "file_id": f["file_id"],
                "name": f["name"],
                "size": f["size"],
                "sha256": f["sha256"],
                "set_type": set_type_of(f["name"], served),
                "prior_job_id": str(row["id"]),
                "folder_id": str(row["folder_id"]),
            }
    if not units:
        return None, str(jobs[0]["id"]) if jobs else ""
    block: dict[str, Any] = {
        "prior_job_id": str(jobs[0]["id"]),
        "folder_id": str(jobs[0]["folder_id"]),
        "units": list(units.values()),
    }
    if notes is not None and notes["file_id"] not in units:
        block["notes"] = notes  # the newest delivery's attorney notes: the runner files their new version
    return block, ""


__all__ = ["MAX_JOBS", "MAX_UNITS", "SET_CLASS", "SET_CLASSES", "collect", "set_type_of"]
