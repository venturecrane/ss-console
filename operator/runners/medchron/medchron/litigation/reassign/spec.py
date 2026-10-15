"""The reassignment's request, re-validated on the runner (the broker's
package is not on the runner's venv; ``workspace_broker/litigation_reassign.py``
validates the same shape before it queues), and the staff resolution.

RESOLUTION, against ``GET /staff``. A name matches a staff record by its full
name (whitespace and case normalized: one record on the first client reads
"Maricela  Mendoza"), else by one of its words (a first or a last name), else,
for a single word, as the start of a first name ("Ed" is Edward). Records that
share one full name are ONE person (the first client carries two "Edward
Schade" records, one disabled): all their ids are that person's. Two different
full names is ambiguity, and the job refuses rather than guesses. The person
the cases move TO must have exactly one active record (enabled, not former).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

STATUSES = ("Open", "Pending", "Closed")
MAX_FROM = 6
MAX_ATTORNEYS = 6
MAX_NAME = 100
ROLES = ("from", "to", "attorney")


class SpecError(ValueError):
    pass


def _names(raw: Any, key: str, cap: int, allow_empty: bool) -> list[str]:
    if not isinstance(raw, list) or not all(isinstance(n, str) for n in raw):
        raise SpecError(f"reassign.{key} must be a list of names")
    names = [" ".join(n.split()) for n in raw]
    if (not names and not allow_empty) or len(names) > cap or any(not n or len(n) > MAX_NAME for n in names):
        raise SpecError(f"reassign.{key} must hold up to {cap} names of at most {MAX_NAME} characters")
    return names


def validate(raw: Any) -> dict[str, Any]:
    keys = {"from_names", "to_name", "attorney_names", "litigation_only", "statuses"}
    if not isinstance(raw, dict) or set(raw) != keys:
        raise SpecError(f"reassign carries exactly {sorted(keys)}")
    to = raw["to_name"]
    if not isinstance(to, str) or not to.strip() or len(to) > MAX_NAME:
        raise SpecError("reassign.to_name must be one name")
    if not isinstance(raw["litigation_only"], bool):
        raise SpecError("reassign.litigation_only must be true or false")
    st = raw["statuses"]
    if not (isinstance(st, list) and st and set(st) <= set(STATUSES) and len(set(st)) == len(st)):
        raise SpecError(f"reassign.statuses must be a non-empty subset of {list(STATUSES)}")
    return {
        "from_names": _names(raw["from_names"], "from_names", MAX_FROM, False),
        "to_name": " ".join(to.split()),
        "attorney_names": _names(raw["attorney_names"], "attorney_names", MAX_ATTORNEYS, True),
        "litigation_only": raw["litigation_only"],
        "statuses": [s for s in STATUSES if s in st],
    }


# ---- resolution -------------------------------------------------------------------
def norm(text: Any) -> str:
    return " ".join(str(text or "").split()).lower()


def full_name(row: dict[str, Any]) -> str:
    return " ".join(f"{row.get('firstName') or ''} {row.get('lastName') or ''}".split())


def active(row: dict[str, Any]) -> bool:
    """Enabled and not former. A record that does not say so is not active
    (the person cases move TO must be provably able to work them)."""
    return row.get("enabled") is True and row.get("former") is not True


@dataclass(frozen=True)
class Person:
    name: str
    ids: tuple[str, ...]
    active_ids: tuple[str, ...]


class Unresolved(Exception):
    """A name the staff list cannot settle. ``kind``: unknown, ambiguous,
    inactive (TO only), same (TO is also a FROM person)."""

    def __init__(self, role: str, kind: str, name: str, matches: int = 0) -> None:
        super().__init__(f"staff_unresolved: {role} {kind}")
        self.role, self.kind, self.name, self.matches = role, kind, name, matches


def _hits(q: str, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    exact = [r for r in rows if norm(full_name(r)) == q]
    if exact:
        return exact
    words = [r for r in rows if q in norm(full_name(r)).split(" ")]
    if words or " " in q or len(q) < 2:
        return words
    return [r for r in rows if norm(r.get("firstName")).startswith(q)]


def resolve(name: str, rows: list[dict[str, Any]], role: str) -> Person:
    q = norm(re.sub(r"['’]s$", "", name.strip()))
    staff = [r for r in rows if isinstance(r, dict) and isinstance(r.get("id"), str) and r["id"].strip()]
    hits = _hits(q, staff) if q else []
    if not hits:
        raise Unresolved(role, "unknown", name)
    people = {norm(full_name(r)) for r in hits}
    if len(people) > 1:
        raise Unresolved(role, "ambiguous", name, len(people))
    ids = tuple(sorted({r["id"].strip().lower() for r in hits}))
    act = tuple(sorted({r["id"].strip().lower() for r in hits if active(r)}))
    return Person(full_name(hits[0]), ids, act)


def resolve_all(spec: dict[str, Any], rows: list[dict[str, Any]]) -> dict[str, Any]:
    """{"from": [Person], "to": Person, "to_id": str, "attorneys": [Person]}
    or ``Unresolved``."""
    frm = [resolve(n, rows, "from") for n in spec["from_names"]]
    to = resolve(spec["to_name"], rows, "to")
    if len(to.active_ids) != 1:
        raise Unresolved("to", "inactive" if not to.active_ids else "ambiguous", spec["to_name"], len(to.active_ids))
    if set(to.ids) & {i for p in frm for i in p.ids}:
        raise Unresolved("to", "same", spec["to_name"])
    attorneys = [resolve(n, rows, "attorney") for n in spec["attorney_names"]]
    return {"from": frm, "to": to, "to_id": to.active_ids[0], "attorneys": attorneys}


__all__ = ["Person", "SpecError", "Unresolved", "active", "full_name", "norm", "resolve", "resolve_all", "validate"]
