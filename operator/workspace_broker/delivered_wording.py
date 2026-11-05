"""The words a DELIVERED job's reply says, rendered by code from the job's own
ledger row (and, for the chronology, the broker's allowance read), so the model
relays them and never composes them (mechanism 4, ADR 0104).

WHY. Until this module every delivered reply was the model's own composition
from counts under exact-label rules in skill prose. A label shortened by the
model reads as a different measure (2026-10-15: "Cases without a date: 110"
beside a letter's "66 files with no LawToolBox dates", two true counts of
different things), and a figure the model restates is a figure it can restate
wrong. The held replies were already fixed wording (``fixed_wording``); this is
the delivered half of the same rule.

THE RULE. ``SENTENCES[lane][key]`` is every sentence a delivered reply can
carry. ``{field}`` marks a value the renderer fills from the row; nothing is
ever left for the model to fill. A sentence whose value the row does not carry
is OMITTED, never rendered with a gap and never guessed (an empty count is left
out, as the skills always said).

Medical chronology, demand and drafting replies live in ``delivered_matter``.
"""

from __future__ import annotations

import json
import re
from string import Formatter
from typing import Any

OUTSIDE = "Nothing has been sent to anyone outside the firm."

#: (count key, the exact label). The deadlines report's labels are the skill's
#: own (a shortened label reads as a different measure). No other count.
DEADLINE_LABELS = (
    ("matters_read", "Open and Pending matters read"),
    ("litigation", "In litigation"),
    ("dates_next_14", "Dates in the next 14 days"),
    ("dates_last_14", "Dates in the last 14 days"),
    ("tasks_due", "Open tasks due or overdue"),
    ("tasks_overdue", "Of them overdue"),
    ("cases_without_date", "Litigation cases with no date ahead on the calendar"),
    ("statute_upcoming", "Statute date ahead, no filing recorded"),
    ("statute_passed_open", "Open, statute date passed, no filing recorded"),
)

SENTENCES: dict[str, dict[str, str]] = {
    "litigation": {
        "status_lead_folder": (
            'The litigation status list is filed in the firm\'s Operator Library, folder "{folder}", as "{workbook}".'
        ),
        "status_lead": 'The litigation status list is filed in the firm\'s Operator Library as "{workbook}".',
        "status_lead_folder_bare": 'The litigation status list is filed in the firm\'s Operator Library, folder "{folder}".',
        "status_lead_bare": "The litigation status list is filed in the firm's Operator Library.",
        "status_per_case": 'The same list, one sheet per case for printing, is filed beside it as "{per_case}".',
        "status_printout": (
            "A printable PDF of every case's court dates, with the source of each date, is filed beside it as "
            '"{printout}".'
        ),
        "status_total": "Matters covered: {n}.",
        "status_reread": "Re-read because their files changed since the last list: {n}.",
        "status_flags": "New flags for review: {n}.",
        "d12_lead_folder": (
            'The 12-section discovery breakdown is filed in the firm\'s Operator Library, folder "{folder}", as '
            '"{workbook}".'
        ),
        "d12_lead": 'The 12-section discovery breakdown is filed in the firm\'s Operator Library as "{workbook}".',
        "d12_lead_folder_bare": (
            'The 12-section discovery breakdown is filed in the firm\'s Operator Library, folder "{folder}".'
        ),
        "d12_lead_bare": "The 12-section discovery breakdown is filed in the firm's Operator Library.",
        "d12_printout": 'Its print-out is filed beside it as "{printout}".',
        "d12_declined": "Files marked for a hand check in the print-out because the reading service declined them: {n}.",
        "deadlines_lead_folder": 'Filed in the firm\'s Operator Library, folder "{folder}": {files}.',
        "deadlines_lead": "Filed in the firm's Operator Library: {files}.",
        "deadlines_lead_folder_bare": 'Filed in the firm\'s Operator Library, folder "{folder}".',
        "deadlines_lead_bare": "Filed in the firm's Operator Library.",
        "deadlines_count": "{label}: {n}.",
        "deadlines_unfiled": (
            "A case can hold filed papers that were never entered as filed, so the statute report shows each "
            "case's count of court-named documents to check before treating it as unfiled."
        ),
        "reassign_moved": "Cases moved from {from_names} to {to_name}: {n}.",
        "reassign_none": "No case was changed.",
        "reassign_scope": "Scope: {scope}.",
        "reassign_tasks": "Open tasks moved with them: {n}.",
        "reassign_outside": "Cases left as they were because they are outside this request: {n}.",
        "reassign_skipped": "Cases left alone because someone else was already assigned before the move: {n}.",
        "outside": OUTSIDE,
    },
    "casefile": {
        "count_first": "Count on the 1st: {count}.",
        "review_lead": 'The ranked list is filed in the firm\'s Operator Library as "{workbook}".',
        "review_lead_bare": "The ranked list is filed in the firm's Operator Library.",
        "review_total": "Files covered: {n}.",
        "review_read": "Read this time: {n}.",
        "review_current": "Already current, with nothing new: {n}.",
        "review_status": 'Each file read has a current File Status in its "File Status" folder.',
        "review_held": 'Files that could not be reviewed this time, named on the list\'s "Not reviewed this run" sheet: {n}.',
        "review_proposed": 'Changes proposed, numbered on the list\'s "Proposed changes" sheet: {n}.',
        "review_approve": (
            'To make them, reply in this thread with the numbers ("yes to 1, 3 and 4", or "yes to all"); '
            "nothing changes until then."
        ),
        "apply_made": "Changes made and checked in Smokeball: {numbers}.",
        "apply_left": (
            "Changes left as they are, because the file changed since they were proposed or the change did not "
            "check out, so nothing was guessed: {numbers}."
        ),
        "apply_none": "No change was made.",
        "outside": OUTSIDE,
    },
}

#: The case file outcomes that count as made (the runner's DONE_OUTCOMES):
#: a write read back, or a record that already showed it.
CASEFILE_DONE = ("completed", "written", "invoice_added", "created", "already_complete", "already_present")
_STATUS_WORD = re.compile(r"[A-Za-z][A-Za-z ]{0,23}")
_FIELD_NAMES = Formatter()


def fill(table: dict[str, dict[str, str]], lane: str, key: str, **values: Any) -> str | None:
    """The sentence with every field filled, or None when any field is absent.
    A sentence is never rendered with a gap."""
    text = table[lane][key]
    names = [f for _, f, _, _ in _FIELD_NAMES.parse(text) if f]
    if any(values.get(n) is None or str(values.get(n)).strip() == "" for n in names):
        return None
    return text.format(**{n: values[n] for n in names})


def first(*options: str | None) -> str | None:
    return next((o for o in options if o), None)


def positive(value: Any) -> int | None:
    """A count worth stating: a positive int. An empty count is left out."""
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else None


def whole(value: Any) -> int | None:
    """A non-negative int, zero included (a figure stated even at zero)."""
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def load(row: dict[str, Any], column: str) -> Any:
    try:
        return json.loads(row.get(column) or "null")
    except (TypeError, ValueError):
        return None


def obj(row: dict[str, Any], column: str) -> dict[str, Any]:
    """A JSON object column, or {} for anything else (absent, malformed, a list)."""
    got = load(row, column)
    return got if isinstance(got, dict) else {}


def files_of(row: dict[str, Any]) -> list[dict[str, Any]]:
    files = obj(row, "delivery_json").get("files")
    return [f for f in files or [] if isinstance(f, dict)]


def names(files: list[dict[str, Any]], *roles: str) -> list[str]:
    return [str(f["name"]).strip() for f in files if f.get("role") in roles and str(f.get("name") or "").strip()]


def name(files: list[dict[str, Any]], role: str) -> str | None:
    got = names(files, role)
    return got[0] if got else None


def and_list(items: list[str]) -> str:
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1] if items else ""


def paragraph(*sentences: str | None) -> str:
    return " ".join(s for s in sentences if s)


def lines(*items: str | None) -> str:
    return "\n".join(s for s in items if s)


def compose(*paragraphs: str) -> str:
    return "\n\n".join(p for p in paragraphs if p)


# -- litigation -------------------------------------------------------------------


def _lit(key: str, **values: Any) -> str | None:
    return fill(SENTENCES, "litigation", key, **values)


def _status_list(row: dict[str, Any], folder: str | None) -> str:
    files = files_of(row)
    book = name(files, "workbook")
    lead = first(
        _lit("status_lead_folder", folder=folder, workbook=book),
        _lit("status_lead", workbook=book),
        _lit("status_lead_folder_bare", folder=folder),
        _lit("status_lead_bare"),
    )
    return compose(
        paragraph(
            lead,
            _lit("status_per_case", per_case=name(files, "per_case_workbook")),
            _lit("status_printout", printout=name(files, "printout_pdf")),
        ),
        paragraph(
            _lit("status_total", n=positive(row.get("matters_total"))),
            _lit("status_reread", n=positive(row.get("matters_reread"))),
            _lit("status_flags", n=positive(row.get("flags_new"))),
        ),
        _lit("outside") or "",
    )


def _discovery12(row: dict[str, Any], folder: str | None) -> str:
    files = files_of(row)
    book = name(files, "discovery_workbook")
    counts = obj(row, "counts_json")
    lead = first(
        _lit("d12_lead_folder", folder=folder, workbook=book),
        _lit("d12_lead", workbook=book),
        _lit("d12_lead_folder_bare", folder=folder),
        _lit("d12_lead_bare"),
    )
    return compose(
        paragraph(lead, _lit("d12_printout", printout=name(files, "printout_pdf"))),
        paragraph(
            _lit("status_total", n=positive(row.get("matters_total"))),
            _lit("d12_declined", n=positive(counts.get("d12_declined"))),
        ),
        _lit("outside") or "",
    )


def _deadlines(row: dict[str, Any], folder: str | None) -> str:
    filed = [f'"{n}"' for n in names(files_of(row), "workbook", "deadlines", "statute")]
    listed = and_list(filed) or None
    counts = obj(row, "counts_json")
    return compose(
        first(
            _lit("deadlines_lead_folder", folder=folder, files=listed),
            _lit("deadlines_lead", files=listed),
            _lit("deadlines_lead_folder_bare", folder=folder),
            _lit("deadlines_lead_bare"),
        )
        or "",
        lines(*(_lit("deadlines_count", label=label, n=positive(counts.get(key))) for key, label in DEADLINE_LABELS)),
        paragraph(_lit("deadlines_unfiled"), _lit("outside")),
    )


def _scope_words(asked: dict[str, Any]) -> str | None:
    statuses = [s for s in asked.get("statuses") or [] if isinstance(s, str)]
    if not statuses:
        return None
    kind = "litigation cases" if asked.get("litigation_only") is True else "cases"
    attorneys = [a for a in asked.get("attorney_names") or [] if isinstance(a, str) and a.strip()]
    whose = f", responsible attorney {and_list(attorneys)}" if attorneys else ""
    return f"{and_list(statuses)} {kind}{whose}"


def _reassign(row: dict[str, Any]) -> str:
    counts = obj(row, "counts_json")
    asked = obj(row, "reassign_json")
    froms = [n for n in asked.get("from_names") or [] if isinstance(n, str) and n.strip()]
    moved = positive(counts.get("cases_moved"))
    lead = (
        _lit("reassign_moved", from_names=and_list(froms) or None, to_name=asked.get("to_name"), n=moved)
        if moved
        else _lit("reassign_none")
    )
    return paragraph(
        lead,
        _lit("reassign_scope", scope=_scope_words(asked)),
        _lit("reassign_tasks", n=positive(counts.get("tasks_moved"))) if moved else None,
        _lit("reassign_outside", n=positive(counts.get("cases_outside_scope"))),
        _lit("reassign_skipped", n=positive(counts.get("cases_skipped"))),
    )


def litigation(row: dict[str, Any]) -> str:
    """A delivered litigation report's reply. The folder is the one the job
    was submitted to file into (``folder_name`` on the row since 2026-11-04);
    a row from before then omits it."""
    folder = str(row.get("folder_name") or "").strip() or None
    report = str(row.get("report") or "status")
    if report == "deadlines":
        return _deadlines(row, folder)
    if report == "reassign":
        return _reassign(row)
    if report == "discovery12":
        return _discovery12(row, folder)
    return _status_list(row, folder)


# -- case file manager -----------------------------------------------------------


def _cf(key: str, **values: Any) -> str | None:
    return fill(SENTENCES, "casefile", key, **values)


def _billing_count(row: dict[str, Any]) -> str | None:
    count = obj(row, "metrics_json").get("billing_count")
    if not isinstance(count, dict) or not count:
        return None
    parts = [
        f"{int(n)} {s if _STATUS_WORD.fullmatch(str(s)) else 'other'}" for s, n in count.items() if whole(n) is not None
    ]
    return ", ".join(parts) or None


def _casefile_apply(row: dict[str, Any]) -> str:
    raw = load(row, "outcomes_json")
    listed = raw if isinstance(raw, list) else []
    outcomes = [o for o in listed if isinstance(o, dict) and whole(o.get("n")) is not None]
    made = [str(o["n"]) for o in outcomes if o.get("outcome") in CASEFILE_DONE]
    left = [str(o["n"]) for o in outcomes if o.get("outcome") not in CASEFILE_DONE]
    return paragraph(
        _cf("apply_made", numbers=and_list(made) or None) or _cf("apply_none"),
        _cf("apply_left", numbers=and_list(left) or None),
    )


def casefile(row: dict[str, Any]) -> str:
    """A delivered case file review's reply, or an apply's."""
    if row.get("mode") == "apply":
        return _casefile_apply(row)
    proposed = positive(row.get("lines_proposed"))
    read = positive(row.get("matters_read"))
    return compose(
        _cf("count_first", count=_billing_count(row)) or "",
        paragraph(
            first(_cf("review_lead", workbook=name(files_of(row), "workbook")), _cf("review_lead_bare")),
            _cf("review_total", n=positive(row.get("matters_total"))),
            _cf("review_read", n=read),
            _cf("review_current", n=positive(row.get("matters_current"))),
            _cf("review_status") if read else None,
            _cf("review_held", n=positive(row.get("matters_held"))),
        ),
        paragraph(_cf("review_proposed", n=proposed), _cf("review_approve") if proposed else None),
        _cf("outside") or "",
    )


__all__ = [
    "CASEFILE_DONE",
    "DEADLINE_LABELS",
    "OUTSIDE",
    "SENTENCES",
    "and_list",
    "casefile",
    "compose",
    "files_of",
    "fill",
    "first",
    "lines",
    "litigation",
    "load",
    "name",
    "names",
    "obj",
    "paragraph",
    "positive",
    "whole",
]
