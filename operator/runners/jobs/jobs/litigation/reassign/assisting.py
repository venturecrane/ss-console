"""A matter's assisting staff: the MAIN person and the ADDITIONAL list.

Proven live on the first client, 2026-10-15 (the reassignment's dry run, job
01M5131A03YE5TPFG6GVM7J02W): a matter carries a main assisting staff member,
``personAssistingStaffId``, and a separate list of additional ones, read back
as ``personAssistingStaffs`` (``[{id, href, rel}]``). The list does NOT include
the main. On four matters the main was already the new paralegal and the old
one sat only in the list; the first version of this job wrote and read back
only the main, so it would have reported those four "moved" with the old
paralegal still on them.

The list is WRITTEN as ``personAssistingStaffIds`` (plural, a string array;
the vendor's update-a-matter reference). ``PATCH {"personAssistingStaffIds":
[]}`` removed a person from a list within ten seconds; ``personAssistingStaffs``
in either shape is answered 202 and silently ignored, as is a main-only PATCH.
So every write sends both fields, and every read-back checks both.
"""

from __future__ import annotations

from typing import Any

MAIN = "personAssistingStaffId"
LIST_READ = "personAssistingStaffs"
LIST_WRITE = "personAssistingStaffIds"


def _id(raw: Any) -> str:
    sid = raw.get("id") if isinstance(raw, dict) else raw
    return sid.strip().lower() if isinstance(sid, str) and sid.strip() else ""


def state(row: dict[str, Any]) -> tuple[str, list[str]]:
    """(main, additional list) as the matter read shows them, lowercased, the
    list in order, once each, never holding the main."""
    main = _id(row.get(MAIN))
    raw = row.get(LIST_READ)
    if raw is None:
        raw = row.get(LIST_WRITE)
    extra = [i for i in (_id(x) for x in (raw if isinstance(raw, list) else [])) if i and i != main]
    return main, list(dict.fromkeys(extra))


def desired(main: str, extra: list[str], from_ids: set[str], to: str) -> tuple[str, list[str]]:
    """Main in FROM becomes TO; each FROM id in the list becomes TO, the others
    kept, once each; TO leaves the list when it is the main."""
    new_main = to if main in from_ids else main
    out: list[str] = []
    for i in extra:
        j = to if i in from_ids else i
        if j != new_main and j not in out:
            out.append(j)
    return new_main, out


def body(main: str, extra: list[str], from_ids: set[str], to: str) -> dict[str, Any]:
    """The full desired state in one PATCH (the list is a full replace). A
    matter with no main keeps none: an empty main is never sent."""
    new_main, new_list = desired(main, extra, from_ids, to)
    return {**({MAIN: new_main} if new_main else {}), LIST_WRITE: new_list}


def moved(row: dict[str, Any], from_ids: set[str], to: str) -> bool:
    """Moved only when no FROM id remains in the main OR the list, and TO is
    on the matter."""
    main, extra = state(row)
    on = {main, *extra}
    return not (on & from_ids) and to in on


def names_from(row: dict[str, Any], from_ids: set[str]) -> bool:
    main, extra = state(row)
    return bool({main, *extra} & from_ids)


__all__ = ["LIST_READ", "LIST_WRITE", "MAIN", "body", "desired", "moved", "names_from", "state"]
