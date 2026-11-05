"""The attorney notes for a revision: each change he asked for, whether it was
made and how that was checked, what was not made and why, and the notes of the
filed draft beneath. Written from the checked record (``revision.json``), never
by a model, and held to the notes' own wording rules (``wording.py``)."""

from __future__ import annotations

from typing import Any

from .. import wording
from ..outcome import DraftingFailed
from .checklist import Item

HOW = {
    "code": "measured in the revised brief",
    "reading": "read against your request",
    "not checked": "not checked",
}


def _cell(s: str) -> str:
    return " ".join(str(s or "").replace("|", "/").split())


def write(
    *,
    title: str,
    job_tail: str,
    requester: str,
    items: list[Item],
    revision: dict[str, Any],
    markers: list[dict[str, str]],
    prior_notes: str,
) -> str:
    statuses = {s["id"]: s for s in revision.get("statuses") or []}
    lines = [
        f"# Revision of the filed {title} (Operator {job_tail})",
        "",
        f"Requested by {requester}. The brief and these notes are filed as new versions of the same files; the "
        "earlier versions stay in each file's version history.",
        "",
        "| # | You asked | Made | How it was checked |",
        "| --- | --- | --- | --- |",
    ]
    not_done = []
    for i in items:
        st = statuses.get(i.id) or {"status": "read", "how": "not checked", "detail": ""}
        made = {"met": "Yes", "not_met": "No"}.get(st["status"], "See below")
        lines.append(f"| {i.id} | {_cell(i.quote)} | {made} | {HOW.get(st['how'], st['how'])} |")
        if st["status"] != "met":
            not_done.append(f"- {i.id}: {_cell(st.get('detail') or 'not made')}")
    if not_done:
        lines += ["", "## Not made, and why", "", *not_done]
    lines += [
        "",
        "## Across the brief",
        "",
        "- Every section your email did not name is unchanged, word for word.",
        "- No figure, date, quotation or citation was added that the filed brief or your email does not carry.",
        "- The tables are unchanged unless your email asked about them.",
    ]
    if markers:
        lines += ["", "## Still for you in the brief", ""]
        lines += [f"- {m.get('kind')}: {_cell(m.get('text') or '')}" for m in markers]
    if prior_notes.strip():
        lines += ["", "## Notes from the filed draft", "", prior_notes.strip()]
    md = "\n".join(lines) + "\n"
    problems = wording.notes_findings(md)
    if problems:
        raise DraftingFailed(
            f"notes_unclean: the revision notes carry {len(problems)} problem(s): " + wording.summary(md)
        )
    return md
