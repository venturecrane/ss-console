"""`people`: who a people-scoped job is about, resolved against Smokeball's
staff list, and the one scope rule the status list and the deadlines report
share.

WHY (2026-10-24). An attorney asked for "every litigated file I'm tagged on"
and the request ran firm-wide, because "my cases" had no scope to become.
The Captain's rule: one person's files are every matter that person holds ANY
staff role on (responsible, assisting, originating; the matter list carries no
supervisor key), and every matter they are tagged on as Staff. A Staff tag is
not on the matter list row; ``staff_tags`` reads it per matter.

RESOLUTION, against ``GET /staff``, each person found ONCE and then expanded:

* the requester (``mine``) by the email on a staff record (no two records
  share one; a match on none holds ``requester_unresolved``, the firm's
  answer: whose files to list is never guessed);
* a name (the firm roster's name the broker placed) by its full name, else by
  the roster's staff id for that name, else by ``reassign.spec.resolve`` (a
  first or last name, never two people). A name none of these settles holds
  ``people_unresolved`` (ours: the broker had placed it);
* each match expands to EVERY record with the same full name, enabled or not
  (one person can carry several records, one of them disabled): the
  reassignment's same-name rule.

THE LOUD CHECK. A people list counts originating staff, so a matter list in
which no row carries an originating key at all would under-count every
person's files in silence; it holds ``roles_unlisted`` instead. A matter
whose Staff tags could not be read after every retry, or a read in which no
matter carries a tags list at all, holds ``staff_tags_unlisted`` the same way.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable

from .. import arrivals
from ..arrivals import ASSISTING, ORIGINATING, RESPONSIBLE, STAFF
from .outcome import LitigationHold
from .reassign import spec

ROLE_ORDER = (RESPONSIBLE, ASSISTING, ORIGINATING, STAFF)
#: Where a job keeps the Staff tags it has read, so a resume reads only the rest.
TAGS_FILE = "staff_tags.json"


@dataclass(frozen=True)
class Person:
    """One person: the staff list's full name and every record id they hold."""

    name: str
    ids: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {"name": self.name, "ids": list(self.ids)}


def _staff(rows: Iterable[Any]) -> list[dict[str, Any]]:
    return [r for r in rows if isinstance(r, dict) and isinstance(r.get("id"), str) and r["id"].strip()]


def _same_name(row: dict[str, Any], staff: list[dict[str, Any]]) -> Person:
    full = spec.norm(spec.full_name(row))
    ids = sorted({r["id"].strip().lower() for r in staff if spec.norm(spec.full_name(r)) == full})
    return Person(spec.full_name(row), tuple(ids))


def by_email(email: str, staff: list[dict[str, Any]]) -> Person | None:
    want = email.strip().lower()
    hits = [r for r in staff if str(r.get("email") or "").strip().lower() == want]
    if len({spec.norm(spec.full_name(r)) for r in hits}) != 1:
        return None
    return _same_name(hits[0], staff)


def by_name(name: str, staff: list[dict[str, Any]], roster: dict[str, str]) -> Person | None:
    q = spec.norm(name)
    exact = [r for r in staff if spec.norm(spec.full_name(r)) == q]
    if exact:
        return _same_name(exact[0], staff)
    sid = next((str(v).strip().lower() for k, v in roster.items() if spec.norm(k) == q), "")
    on_roster = [r for r in staff if r["id"].strip().lower() == sid] if sid else []
    if on_roster:
        return _same_name(on_roster[0], staff)
    try:
        found = spec.resolve(name, staff, "person")
    except spec.Unresolved:
        return None
    row = next(r for r in staff if r["id"].strip().lower() == found.ids[0])
    return _same_name(row, staff)


def firm_roster(firm: Any) -> dict[str, str]:
    """The firm config's ``attorneys`` and ``paralegals`` maps, merged (name ->
    staff id). The broker placed every name against these."""
    data = getattr(firm, "data", firm) or {}
    out: dict[str, str] = {}
    for key in ("attorneys", "paralegals"):
        raw = data.get(key) if isinstance(data, dict) else None
        if isinstance(raw, dict):
            out.update({str(k): str(v) for k, v in raw.items() if isinstance(v, str)})
    return out


def resolve(people: Any, staff_rows: Iterable[Any], roster: dict[str, str]) -> tuple[Person, ...]:
    """Every person the scope names, the requester first. Raises
    ``LitigationHold`` (``requester_unresolved`` / ``people_unresolved``)."""
    staff = _staff(staff_rows)
    out: list[Person] = []
    if people.requester:
        me = by_email(people.requester, staff)
        if me is None:
            raise LitigationHold(
                "requester_unresolved: the requester's address matches no staff record in Smokeball, "
                "so whose files to list was not guessed"
            )
        out.append(me)
    for name in people.names:
        p = by_name(name, staff, roster)
        if p is None:
            raise LitigationHold(f"people_unresolved: the staff list has no one person for the roster name {name!r}")
        out.append(p)
    return tuple(dict.fromkeys(out))


def ids_of(people: Iterable[Person]) -> frozenset[str]:
    return frozenset(i for p in people for i in p.ids)


def in_scope(view: dict[str, Any], job: Any, people_ids: frozenset[str] = frozenset()) -> bool:
    """THE scope rule, for the status list and the deadlines report alike.
    ``view``: ``staff_id``, ``assisting_ids`` and ``roles`` (``arrivals``).

    Every matter; or, for a people scope, any staff role held by one of the
    people; or one matching EACH id side the job names (the responsible
    attorney among the attorney ids, one Person Assisting among the paralegal
    ids)."""
    if job.scope_all:
        return True
    if getattr(job, "people", None) is not None:
        return bool(set(view.get("roles") or {}) & people_ids)
    staff_ids, paralegal_ids = job.attorney_staff_ids, job.paralegal_staff_ids
    if not staff_ids and not paralegal_ids:
        return False
    if staff_ids and view.get("staff_id") not in staff_ids:
        return False
    return not paralegal_ids or bool(set(view.get("assisting_ids") or ()) & set(paralegal_ids))


def check_roles_listed(job: Any, originating: Iterable[bool], listed: int) -> None:
    """The loud check: a people job over a matter list where NO row carries an
    originating key holds rather than under-counting every person's files."""
    if getattr(job, "people", None) is None or listed == 0:
        return
    if not any(originating):
        raise LitigationHold(
            f"roles_unlisted: none of the {listed} listed matters carries an originating staff key; "
            "a person's files would be under-counted"
        )


def _load_tags(path: Path | None) -> dict[str, Any]:
    if path is None or not path.is_file():
        return {"read": [], "tags": {}, "listed": False}
    got = json.loads(path.read_text(encoding="utf-8"))
    return {"read": list(got["read"]), "tags": dict(got["tags"]), "listed": bool(got["listed"])}


def _save_tags(path: Path | None, kept: dict[str, Any]) -> None:
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(json.dumps(kept, indent=1), encoding="utf-8")
    tmp.replace(path)


def staff_tags(job: Any, get: Callable[..., Any], matter_ids: Iterable[str], path: Path | None) -> dict[str, list[str]]:
    """``{matter id: [staff ids tagged as Staff]}`` for a people scope over
    ``matter_ids`` (every open matter: a matter a person is only tagged on is
    otherwise invisible); ``{}`` for any other scope, with no call made.

    What was read is kept at ``path``: a resumed job reads only the matters it
    has not read yet. Holds ``staff_tags_unlisted`` when a matter's tags stay
    unread after every retry, or when no matter read carries a tags list."""
    if getattr(job, "people", None) is None:
        return {}
    ids = list(dict.fromkeys(str(m) for m in matter_ids))
    kept = _load_tags(path)
    todo = [m for m in ids if m not in set(kept["read"])]
    if todo:
        try:
            got = arrivals.staff_tags(get, todo)
        except arrivals.StaffTagsError as exc:
            raise LitigationHold(f"staff_tags_unlisted: {exc}; a person's files would be under-counted") from exc
        kept["read"] = list(dict.fromkeys(kept["read"] + todo))
        kept["tags"].update({m: v for m, v in got.items() if v})
        kept["listed"] = kept["listed"] or any(v is not None for v in got.values())
        _save_tags(path, kept)
    if ids and not kept["listed"]:
        raise LitigationHold(
            f"staff_tags_unlisted: none of the {len(ids)} matters read carries a tags list; "
            "a person's files would be under-counted"
        )
    return {m: list(kept["tags"][m]) for m in ids if m in kept["tags"]}


def with_staff_tags(roles: dict[str, list[str]] | None, tagged: Iterable[str]) -> dict[str, list[str]]:
    """``roles`` with ``Staff`` added for every tagged staff id (a copy)."""
    out = {sid: list(labels) for sid, labels in (roles or {}).items()}
    for sid in tagged:
        if STAFF not in out.setdefault(sid, []):
            out[sid].append(STAFF)
    return out


def role_labels(roles: dict[str, list[str]] | None, ids: Iterable[str]) -> list[str]:
    """The roles one person (``ids``) holds on a matter, in a fixed order."""
    held = {r for sid in ids for r in (roles or {}).get(sid, ())}
    return [r for r in ROLE_ORDER if r in held]


__all__ = [
    "Person",
    "by_email",
    "by_name",
    "check_roles_listed",
    "firm_roster",
    "ids_of",
    "in_scope",
    "resolve",
    "role_labels",
    "staff_tags",
    "with_staff_tags",
]
