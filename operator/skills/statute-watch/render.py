"""Deterministic renderer for the monthly statute report (``references/output-format.md``).

The pre_run renders the whole message here, in code; the woken turn composes
nothing. Every phrase below is authored template text, and every value comes
from the firm's own Smokeball records through ``report.Row``: the matter
number, the client's last name, the statute date, the days between today and
that date, the responsible attorney's last name, and a count of documents
whose file names read as court papers. An absent value renders as an explicit
absence, never a guess.

WHAT THE SEND GATE WILL SEE, and why each rule here exists:

* The only dates printed are the statute dates themselves, each on the same
  line as its own matter number. The handoff seeds exactly those pairs, so the
  identifier gate verifies every line. The subject names a month and a year,
  which the gate does not read as a date.
* Names are last names only (``report.surname``): a middle initial "V." turns
  a full name into a case caption the citation gate refuses.
* No em dashes, no citations, no field names.

The SKELETON is the fallback the overlay sends when the full body is refused:
counts only, no matter number, name or date, so it cannot be refused on an
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

SUBJECT_TEMPLATE = "Statute report, {month} {year}"

INTRO = (
    "Open cases whose statute of limitations date falls in the next three months, "
    "with no Filed date and no Case number in Smokeball. Soonest first."
)

EMPTY_LINE = "No open cases have a statute date in the next three months without a filing."

#: The one template the woken turn may send in this session (in_turn, enforced).
SKELETON_TEMPLATE = (
    "{count} open {cases} a statute date in the next three months with nothing filed; "
    "the case list could not be sent this month."
)


def long_date(day: date) -> str:
    """``October 15, 2026``: the form the reader reads and the gate folds."""
    return MONTHS[day.month - 1] + " " + str(day.day) + ", " + str(day.year)


def subject(today: date) -> str:
    return SUBJECT_TEMPLATE.format(month=MONTHS[today.month - 1], year=today.year)


def _plural(count: int, one: str, many: str) -> str:
    return one if count == 1 else many


def _days_phrase(days_left: int) -> str:
    if days_left == 0:
        return "due today"
    return str(days_left) + " " + _plural(days_left, "day", "days") + " left"


def _attorney_phrase(row) -> str:
    if row.attorney:
        return "Attorney " + row.attorney + "."
    if row.attorney_assigned:
        return "Attorney not available."
    return "No responsible attorney on file."


def _documents_phrase(row) -> str:
    if row.court_documents is None:
        return "Court-named documents: not checked."
    return "Court-named documents: " + str(row.court_documents) + "."


def case_line(index: int, row) -> str:
    """One numbered line per case: every value on it read from the record."""
    matter = "Matter " + row.matter_number if row.matter_number else "Matter with no number on file"
    client = "client " + row.client if row.client else "client name not available"
    return (
        str(index)
        + ". "
        + matter
        + ", "
        + client
        + ": statute date "
        + long_date(row.statute)
        + ", "
        + _days_phrase(row.days_left)
        + ". "
        + _attorney_phrase(row)
        + " "
        + _documents_phrase(row)
    )


def more_line(count: int) -> str:
    return "And " + str(count) + " more " + _plural(count, "case", "cases") + "."


def unreadable_line(count: int) -> str:
    return str(count) + " open " + _plural(count, "case", "cases") + " could not be checked this month."


def render_full(rows: list, *, total: int, unreadable: int) -> str:
    """The whole report body. ``rows`` are the listed cases (at most the cap);
    ``total`` counts every selected case, so the remainder is stated."""
    parts: list[str] = []
    if total == 0:
        parts.append(EMPTY_LINE)
    else:
        parts.append(INTRO)
        parts.append("\n".join(case_line(i, row) for i, row in enumerate(rows, start=1)))
        if total > len(rows):
            parts.append(more_line(total - len(rows)))
    if unreadable > 0:
        parts.append(unreadable_line(unreadable))
    return "\n\n".join(parts) + "\n"


def render_skeleton(total: int) -> str:
    return SKELETON_TEMPLATE.format(count=total, cases=_plural(total, "case has", "cases have")) + "\n"
