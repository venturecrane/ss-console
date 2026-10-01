"""Deterministic renderer for the monthly statute report (``references/output-format.md``).

The pre_run renders the whole message here, in code; the woken turn composes
nothing. Every phrase below is authored template text, and every value comes
from the firm's own Smokeball records through ``report.Row`` and
``changes.Change``: the file number, the client's last name (from the matter
title), the statute date, the days between today and that date, the
responsible attorney's last name, a count of documents whose file names read
as court papers, and what the record shows for a case that left the list. An
absent value renders as an explicit absence, never a guess.

The overlay renders this markdown to html (``shared/report_render.py``):
``##`` headings, ``- `` bulleted items, ``**bold**``, and an indented line
under an item as that item's detail line. No tables.

WHAT THE SEND GATE WILL SEE, and why each rule here exists:

* The only dates printed are statute dates: each case's current one, and for
  a case whose statute date changed, last month's one beside it. The handoff
  seeds every (file number, date) pair either can print. The subject names a
  month and a year, which the gate does not read as a date. The date of last
  month's run is never printed.
* Names are last names only (``clients.body_name``): a middle initial "V."
  turns a full name into a case caption the citation gate refuses.
* No em dashes, no citations, no field names.

The SKELETON is the fallback the overlay sends when the full body is refused:
counts only, no file number, name or date, so it cannot be refused on an
identifier and still tells the reader the report did not arrive.
"""

from __future__ import annotations

from datetime import date

MONTHS = (
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
)

SUBJECT_TEMPLATE = "Statute watch, {month} {year}: {cases}, {week} due this week"
SUBJECT_EMPTY_TEMPLATE = "Statute watch, {month} {year}: no cases"

HEADLINE_TEMPLATE = (
    "{count} open {cases} a statute date in the next three months with no Filed date and no Case number "
    "in Smokeball, {week} due within 7 days"
)
SINCE_TEMPLATE = "; since last month, {new} new and {passed} passed with no filing recorded"

EMPTY_LINE = "No open cases have a statute date in the next three months without a filing."

#: Section headings and the days-left ceiling of each (Later has none).
WEEK_DAYS = 7
MONTH_DAYS = 30
SECTION_WEEK = "## Due this week"
SECTION_MONTH = "## Due this month"
SECTION_LATER = "## Later"
SECTION_SINCE = "## Since last month"

NO_PREVIOUS_LINE = "Changes since last month start with next month's report."
NO_CHANGES_LINE = "No changes since last month."
ATTACHED_LINE = "The full list is attached as a spreadsheet."
NOT_ATTACHED_LINE = "The workbook could not be attached this month."

#: At most this many "since last month" items in the body; the workbook has all.
CHANGES_CAP = 50

#: The one template the woken turn may send in this session (in_turn, enforced).
SKELETON_TEMPLATE = (
    "{count} open {cases} a statute date in the next three months with nothing filed; "
    "the case list could not be sent this month."
)

#: What the record shows, one sentence per kind (``changes`` kinds). The
#: ``{date}``, ``{old}`` and ``{new}`` slots are statute dates only.
CHANGE_SENTENCES = {
    "new": "New on the list. Statute date {date}.",
    "filed": "Filed per Smokeball (Filed date or Case number now recorded).",
    "closed": "Closed in Smokeball.",
    "not_open": "No longer Open or Pending in Smokeball.",
    "passed": "Statute date {date} has passed. Smokeball shows no Filed date and no Case number.",
    "changed": "Statute date changed from {old} to {new}.",
    "removed": "Statute date removed in Smokeball.",
    "unchecked": "Could not be checked this month.",
}

#: The workbook's "Change" column, one short label per kind.
CHANGE_LABELS = {
    "new": "New",
    "filed": "Filed per Smokeball",
    "closed": "Closed",
    "not_open": "No longer open",
    "passed": "Statute date passed",
    "changed": "Statute date changed",
    "removed": "Statute date removed",
    "unchecked": "Not checked",
}


def long_date(day: date) -> str:
    """``October 15, 2026``: the form the reader reads and the gate folds."""
    return MONTHS[day.month - 1] + " " + str(day.day) + ", " + str(day.year)


def _plural(count: int, one: str, many: str) -> str:
    return one if count == 1 else many


def due_this_week(rows: list) -> int:
    return sum(1 for r in rows if r.days_left <= WEEK_DAYS)


def subject(today: date, total: int, week: int) -> str:
    month, year = MONTHS[today.month - 1], today.year
    if total == 0:
        return SUBJECT_EMPTY_TEMPLATE.format(month=month, year=year)
    cases = str(total) + " " + _plural(total, "case", "cases")
    return SUBJECT_TEMPLATE.format(month=month, year=year, cases=cases, week=week)


def headline(total: int, week: int, new: int, passed: int, has_previous: bool) -> str:
    """The one sentence of counts the body opens with."""
    if total == 0:
        line = EMPTY_LINE.rstrip(".")
    else:
        line = HEADLINE_TEMPLATE.format(count=total, cases=_plural(total, "case has", "cases have"), week=week)
    if has_previous:
        line += SINCE_TEMPLATE.format(new=new, passed=passed)
    return line + "."


def _days_phrase(days_left: int) -> str:
    if days_left == 0:
        return "due today"
    return str(days_left) + " " + _plural(days_left, "day", "days") + " left"


def attorney_phrase(row) -> str:
    if row.attorney:
        return "Attorney " + row.attorney + "."
    if row.attorney_assigned:
        return "Attorney not available."
    return "No responsible attorney on file."


def attorney_cell(row) -> str:
    """The workbook's Attorney column: the full name, or the same absence."""
    if row.attorney_full:
        return row.attorney_full
    return attorney_phrase(row).rstrip(".")


def _documents_phrase(row) -> str:
    if row.court_documents is None:
        return "Court-named documents: not checked."
    return "Court-named documents: " + str(row.court_documents) + "."


def _label(number: str | None, client: str | None) -> str:
    file_part = "File " + number if number else "No file number"
    return "**" + file_part + ", " + (client or "client name not available") + "**"


def case_item(row) -> str:
    """One case: the bold file and client, then an indented detail line."""
    return (
        "- "
        + _label(row.matter_number, row.client)
        + "\n   Statute date "
        + long_date(row.statute)
        + ", "
        + _days_phrase(row.days_left)
        + ". "
        + attorney_phrase(row)
        + " "
        + _documents_phrase(row)
    )


def change_detail(change) -> str:
    """What the record shows for one change, the same words in body and workbook."""
    template = CHANGE_SENTENCES[change.kind]
    slots = {}
    if change.statute is not None:
        slots["date"] = slots["new"] = long_date(change.statute)
    if change.previous is not None:
        slots["old"] = long_date(change.previous)
    try:
        return template.format(**slots)
    except KeyError:
        return CHANGE_SENTENCES["unchecked"]


def change_item(change) -> str:
    return "- " + _label(change.matter_number, change.client) + ": " + change_detail(change)


def more_line(count: int) -> str:
    return "And " + str(count) + " more " + _plural(count, "case", "cases") + "."


def more_changes_line(count: int) -> str:
    return "And " + str(count) + " more " + _plural(count, "change", "changes") + "."


def unreadable_line(count: int) -> str:
    return str(count) + " open " + _plural(count, "case", "cases") + " could not be checked this month."


def _sections(rows: list) -> list[str]:
    buckets = (
        (SECTION_WEEK, [r for r in rows if r.days_left <= WEEK_DAYS]),
        (SECTION_MONTH, [r for r in rows if WEEK_DAYS < r.days_left <= MONTH_DAYS]),
        (SECTION_LATER, [r for r in rows if r.days_left > MONTH_DAYS]),
    )
    return [heading + "\n\n" + "\n".join(case_item(r) for r in items) for heading, items in buckets if items]


def _since(changes: list | None) -> str:
    if changes is None:
        return SECTION_SINCE + "\n\n" + NO_PREVIOUS_LINE
    if not changes:
        return SECTION_SINCE + "\n\n" + NO_CHANGES_LINE
    shown = changes[:CHANGES_CAP]
    text = SECTION_SINCE + "\n\n" + "\n".join(change_item(c) for c in shown)
    if len(changes) > len(shown):
        text += "\n\n" + more_changes_line(len(changes) - len(shown))
    return text


def render_full(
    rows: list, *, total: int, unreadable: int, week: int, changes: list | None = None, attached: bool = True
) -> str:
    """The whole report body. ``rows`` are the listed cases (at most the cap);
    ``total`` counts every selected case, so the remainder is stated.
    ``changes`` is None when there is no last run to compare with."""
    new = sum(1 for c in changes or [] if c.kind == "new")
    passed = sum(1 for c in changes or [] if c.kind == "passed")
    parts = [headline(total, week, new, passed, changes is not None)]
    parts.extend(_sections(rows))
    if total > len(rows):
        parts.append(more_line(total - len(rows)))
    if unreadable > 0:
        parts.append(unreadable_line(unreadable))
    parts.append(_since(changes))
    parts.append(ATTACHED_LINE if attached else NOT_ATTACHED_LINE)
    return "\n\n".join(parts) + "\n"


def without_attachment(full_body: str) -> str:
    """The same body with the attached line replaced (the overlay's middle rung)."""
    head, sep, tail = full_body.rpartition(ATTACHED_LINE)
    return head + NOT_ATTACHED_LINE + tail if sep else full_body


def render_skeleton(total: int) -> str:
    return SKELETON_TEMPLATE.format(count=total, cases=_plural(total, "case has", "cases have")) + "\n"
