"""The matter.id -> matter.number join, importable outside the MCP server.

WHY THIS MODULE EXISTS (ss #2390, and the 2026-08-24 degraded digest). A task
or event carries its matter as a GUID; the human-readable number lives on the
matter record. ``server.py`` performs that join for every MCP read surface —
but the deadline escalator's ``pre_run.py`` pulls through the raw client in a
connector-venv subprocess, bypassing the MCP layer entirely, so every digest it
ever produced could only say "matter number unavailable". ss #2390's contract
is that this join is performed IN THE CONNECTOR for the surfaces skills
consume; this module is the connector-side join for the raw-client surface.

It deliberately resolves NUMBERS ONLY. ``server.py``'s ``_resolve_matter_ref``
also composes captions (party lookups, surname joins) because the MCP surfaces
need them; a digest never renders a caption (the skill contract bans it), and a
pre-run subprocess should not pay N party lookups per matter for a field it
must not use.

ABSENCE IS TYPED, NOT COLLAPSED (2026-08-24 critique). "This matter's record
has no number" and "the lookup failed" are different facts with different
consequences: authored absence renders an explicit "no number on record" line
and ships; resolution failure counts toward a degraded run that is withheld and
paged. A join that returned ``None`` for both would let a transient API error
silently suppress a deadline watch — or worse, let a firm whose matters carry
no numbers lose its escalator forever.

Fail-safe direction, same as the server-side join: an unresolved ref attaches
NOTHING. A record with no ``matterNumber`` gives the model nothing to copy —
it can only fail to supply a number, never supply a wrong one.
"""

from __future__ import annotations

from typing import Any

#: Item annotation key for a typed absence. Never set alongside
#: ``matterNumber``; consumers branch on exactly one of the two.
ABSENT_KEY = "matterNumberAbsent"

#: The typed absences.
NO_NUMBER_ON_RECORD = "no_number_on_record"  # fetched fine; record has no number
LOOKUP_FAILED = "lookup_failed"  # the GET raised
BUDGET_EXHAUSTED = "budget_exhausted"  # lookup cap hit before this matter
NO_MATTER_LINK = "no_matter_link"  # the item names no matter at all


def item_matter_id(item: dict[str, Any]) -> str | None:
    """The matter GUID a record carries about ITSELF, or None.

    Reads the two shapes the API uses (``matter: {id}`` on tasks and events,
    ``matterId`` on memos and document reads) and nothing else — never derived
    from a name, a subject line, or a neighbouring record. (Same contract as
    ``server._item_matter_id``; duplicated here rather than imported so this
    module never imports the MCP server into a pre-run subprocess.)
    """
    matter = item.get("matter")
    if isinstance(matter, dict):
        candidate = matter.get("id")
        if isinstance(candidate, str) and candidate:
            return candidate
    candidate = item.get("matterId")
    if isinstance(candidate, str) and candidate:
        return candidate
    return None


def resolve_matter_number(
    client: Any,
    matter_id: str,
    cache: dict[str, tuple[str, str | None]],
    budget: list[int] | None,
) -> tuple[str, str | None]:
    """``(status, number)`` for one matter, memoized in ``cache``.

    ``status`` is ``"resolved"`` (number is a str), ``NO_NUMBER_ON_RECORD``,
    ``LOOKUP_FAILED``, or ``BUDGET_EXHAUSTED`` (number is None for all three).
    ``budget`` is a 1-element list capping LIVE lookups; cache hits are free.
    A budget miss is not cached — a later call with budget remaining may still
    resolve it.
    """
    if matter_id in cache:
        return cache[matter_id]
    if budget is not None:
        if budget[0] <= 0:
            return (BUDGET_EXHAUSTED, None)
        budget[0] -= 1
    try:
        matter = client.get(f"/matters/{matter_id}")
    except Exception:  # noqa: BLE001 — enrichment must never break the pull
        result: tuple[str, str | None] = (LOOKUP_FAILED, None)
        cache[matter_id] = result
        return result
    number = matter.get("number") if isinstance(matter, dict) else None
    if isinstance(number, str) and number:
        result = ("resolved", number)
    else:
        result = (NO_NUMBER_ON_RECORD, None)
    cache[matter_id] = result
    return result


def attach_matter_numbers(
    client: Any,
    items: list[Any],
    *,
    budget: int = 100,
    cache: dict[str, tuple[str, str | None]] | None = None,
) -> dict[str, int]:
    """Attach ``matterNumber`` (or a typed :data:`ABSENT_KEY`) to each record.

    Mutates in place, shares one cache and one budget across the whole list (a
    fifty-task single-matter pull costs one GET), and returns counts by status
    for the caller's degraded-run judgment: ``{"resolved": …,
    "no_number_on_record": …, "lookup_failed": …, "budget_exhausted": …,
    "no_matter_link": …}``.
    """
    if cache is None:
        cache = {}
    remaining = [max(0, int(budget))]
    counts = {
        "resolved": 0,
        NO_NUMBER_ON_RECORD: 0,
        LOOKUP_FAILED: 0,
        BUDGET_EXHAUSTED: 0,
        NO_MATTER_LINK: 0,
    }
    for item in items:
        if not isinstance(item, dict):
            continue
        matter_id = item_matter_id(item)
        if not matter_id:
            item[ABSENT_KEY] = NO_MATTER_LINK
            counts[NO_MATTER_LINK] += 1
            continue
        status, number = resolve_matter_number(client, matter_id, cache, remaining)
        if status == "resolved" and number:
            item["matterNumber"] = number
            counts["resolved"] += 1
        else:
            item[ABSENT_KEY] = status
            counts[status] += 1
    return counts
