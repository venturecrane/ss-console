"""records-order-chase: select what has not come in, and render the weekly email.

Pure functions, no I/O: ``pre_run.py`` hands in the committed orders (the
broker's ``records_orders_placed``) and the per-matter read (``pull.py``), and
gets back the report and its text. The shape is ``references/output-format.md``.

WHAT COUNTS AS IN. Only a location whose vendor status, trimmed and compared
without case, is in :data:`COMPLETE_STATUSES`. The vendor's status vocabulary is
not published to us: the one value this repo has seen is ``Not Started`` (the
connector's test vendor, ``operator/connectors/smokeball/tests/
test_records_orders.py``). So the set is deliberately the narrowest reading of
"done", and EVERY other value, including one we have never seen, an empty one,
or one that sounds final, is listed as not in with the vendor's own word for
it. A status we do not know is shown, never interpreted: a week with a stale
line costs a person a glance, a request marked received that was not costs the
firm the records.

WHAT IS NEVER DROPPED. A matter the vendor would not answer for (an HTTP error,
a 404 on a matter the Operator ordered on, an answer with no locations), and an
order whose matter is not known, are each listed under "could not be checked".
A failed read never looks like an empty result.

MATCHING IS PER MATTER. The vendor's matter read lists locations, not orders
(``records_order_tools.records_orders_for_matter``), so a matter with two orders
shows both order dates and counts days from the first. The read shows orders
placed through the API only (``medical-records-chaser/SKILL.md``, the section
on why that skill calls no vendor tool), which on these seats means placed through the
Operator; that claim is the connector's, not proven against a live account.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

#: The statuses read as "the records came in". See the module docstring.
COMPLETE_STATUSES = frozenset({"completed", "complete"})
NO_STATUS = "no status given"


@dataclass
class Outstanding:
    matter_number: str | None
    client: str | None
    facility: str
    request_id: str | None
    status: str
    ordered: list[date]
    days: int


@dataclass
class Unchecked:
    matter_number: str | None
    client: str | None
    ordered: list[date]
    reason: str


@dataclass
class Report:
    vendor: str
    orders: int
    matters: int
    outstanding: list[Outstanding] = field(default_factory=list)
    unchecked: list[Unchecked] = field(default_factory=list)
    complete: int = 0
    not_read: int = 0


def is_complete(status: object) -> bool:
    return isinstance(status, str) and status.strip().casefold() in COMPLETE_STATUSES


def _reason(code: object) -> str:
    text = str(code or "")
    if text == "http_404":
        return "the vendor says it holds no order for this matter"
    if text.startswith("http_"):
        return "the vendor did not answer (HTTP " + text[5:] + ")"
    if text == "no_locations":
        return "the vendor's answer listed no requests"
    return "the vendor could not be reached"


def _text(value: object) -> str | None:
    text = str(value).strip() if value is not None else ""
    return text or None


def build(
    orders_by_matter: dict[str, list[date]],
    unknown_matter: list[date],
    reads: dict,
    vendor: str,
    today: date,
    *,
    not_read: int = 0,
) -> Report:
    """The report: every location not in, every matter or order that could not be checked."""
    total = sum(len(v) for v in orders_by_matter.values()) + len(unknown_matter) + not_read
    report = Report(vendor=vendor, orders=total, matters=len(orders_by_matter), not_read=not_read)
    for matter_id, ordered in sorted(orders_by_matter.items(), key=lambda kv: (min(kv[1]), kv[0])):
        raw = reads.get(matter_id)
        facts: dict = raw if isinstance(raw, dict) else {}
        number = None if facts.get("matterError") else _text(facts.get("number"))
        client = None if facts.get("matterError") else _text(facts.get("client"))
        dates = sorted(ordered)
        locations = facts.get("locations")
        if "vendorError" in facts or not isinstance(locations, list):
            report.unchecked.append(Unchecked(number, client, dates, _reason(facts.get("vendorError"))))
            continue
        if not locations:
            report.unchecked.append(Unchecked(number, client, dates, _reason("no_locations")))
            continue
        for loc in locations:
            if not isinstance(loc, dict):
                continue
            if is_complete(loc.get("status")):
                report.complete += 1
                continue
            report.outstanding.append(
                Outstanding(
                    matter_number=number,
                    client=client,
                    facility=_text(loc.get("name")) or "a facility the vendor did not name",
                    request_id=_text(loc.get("request_id")),
                    status=_text(loc.get("status")) or NO_STATUS,
                    ordered=dates,
                    days=(today - dates[0]).days,
                )
            )
    for ordered in sorted(unknown_matter):
        report.unchecked.append(Unchecked(None, None, [ordered], "the order's matter is not on record"))
    return report


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def _matter(number: str | None, client: str | None) -> str:
    who = "Matter " + number if number else "A matter whose number could not be read from Smokeball"
    return who + (", " + client if client else "")


def _dates(values: list[date]) -> str:
    return " and ".join(d.isoformat() for d in values)


def _days(n: int) -> str:
    return "today" if n <= 0 else ("1 day ago" if n == 1 else f"{n} days ago")


def follow_up(item: Outstanding) -> str:
    """The note a person can copy and send to the vendor. Facts from the read only."""
    client = f"our client {item.client}" if item.client else "our client"
    matter = f" (our matter {item.matter_number})" if item.matter_number else ""
    when = _dates(item.ordered)
    if item.request_id:
        ask = f"Could you tell us where request {item.request_id} stands? It is for records from {item.facility}"
        shown = f" Your system shows it as {item.status}." if item.status != NO_STATUS else ""
    else:
        ask = f"Could you tell us where our request for records from {item.facility} stands? It is"
        shown = " It does not have a request number yet."
    return (
        "Hello,\n\n"
        f"{ask} for {client}{matter}, ordered on {when}.{shown} "
        "If anything is needed from us to move it forward, please let us know.\n\n"
        "Thank you,"
    )


def _item_lines(n: int, item: Outstanding, vendor: str) -> list[str]:
    lines = [
        f"{n}. {_matter(item.matter_number, item.client)}",
        f"   Facility: {item.facility}",
        f"   Request number: {item.request_id or 'not assigned yet'}",
        f"   Ordered: {_dates(item.ordered)} ({_days(item.days)})",
        f"   Status at {vendor}: {item.status}",
        f"   Follow-up to send to {vendor}:",
    ]
    lines += ["   " + line if line else "" for line in follow_up(item).split("\n")]
    return lines


def _unchecked_line(item: Unchecked) -> str:
    count = len(item.ordered)
    noun = "order" if count == 1 else "orders"
    when = _dates(item.ordered)
    if not (item.matter_number or item.client):
        return f"- An order placed {when}: {item.reason}, so it was not checked this week."
    head = _matter(item.matter_number, item.client)
    return f"- {head}, {noun} placed {when}: {item.reason}, so it was not checked this week."


def render(report: Report) -> str:
    vendor = report.vendor
    n_out = len(report.outstanding)
    noun = "order" if report.orders == 1 else "orders"
    lines = ["Hello,", ""]
    if n_out:
        lines.append(
            f"We checked with {vendor} on the {report.orders} medical-records {noun} placed through the Operator. "
            f"{n_out} request{' has' if n_out == 1 else 's have'} not come in. Each has a follow-up below, ready "
            f"for a person to send to {vendor}. Nothing has been sent to {vendor} or to any provider."
        )
    else:
        lines.append(
            f"We checked with {vendor} on the {report.orders} medical-records {noun} placed through the Operator. "
            f"{vendor} shows every request it could be asked about as completed, so there is nothing to follow up "
            "this week."
        )
    for n, item in enumerate(report.outstanding, start=1):
        lines += [""] + _item_lines(n, item, vendor)
    if report.unchecked:
        lines += ["", "Could not be checked this week:"]
        lines += [_unchecked_line(item) for item in report.unchecked]
    if report.not_read:
        lines += [
            "",
            f"{report.not_read} more order{' was' if report.not_read == 1 else 's were'} not checked this week, "
            "past the number one run reads; they come first next week.",
        ]
    lines += [
        "",
        "This list covers only orders placed through the Operator, not orders placed on "
        f"{vendor}'s own site, and it does not judge whether records that came in are complete.",
    ]
    return "\n".join(lines)


def subject(report: Report) -> str:
    n_out, n_unchecked = len(report.outstanding), len(report.unchecked)
    head = (
        f"Records orders: {n_out} request{'' if n_out == 1 else 's'} not yet in"
        if n_out
        else "Records orders: nothing outstanding"
    )
    if n_unchecked:
        head += f", {n_unchecked} could not be checked"
    return head


def skeleton(report: Report) -> str:
    n_out, n_unchecked = len(report.outstanding), len(report.unchecked)
    return (
        "Hello,\n\n"
        f"This week's records-order check found {n_out} request{'' if n_out == 1 else 's'} not yet in and "
        f"{n_unchecked} that could not be checked. The list itself could not be included in this email."
    )


def handoff_records(report: Report, today: date) -> dict:
    """What the send gate may treat as read: each printed matter number paired with
    the order dates printed beside it, and every date the body prints."""
    grouped: dict[str, list[str]] = {}
    days: list[str] = [today.isoformat()]
    for item in [*report.outstanding, *report.unchecked]:
        for d in item.ordered:
            if d.isoformat() not in days:
                days.append(d.isoformat())
            if item.matter_number:
                dates = grouped.setdefault(item.matter_number, [])
                if d.isoformat() not in dates:
                    dates.append(d.isoformat())
    return {"dates": days, "records": [{"matterNumber": k, "dates": v} for k, v in grouped.items()]}
