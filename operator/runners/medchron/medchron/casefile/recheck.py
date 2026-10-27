"""The re-check economies: what a changed matter costs is what changed.

A re-check of a large matter once cost as much as most of a full read, because
the pack restated the whole file and the reader re-wrote the whole record. Three
pieces here keep it proportional:

* **Document references are short.** The model names a document ``[doc N]``
  (the number the listing and ``fetch_doc`` use), never its 36-character id.
  :func:`to_refs` turns the document ids inside a stored record into ``[doc N]``
  for the pack; :func:`resolve` turns every ``[doc N]`` the model writes back
  into the id before anything is checked or stored. A number that is not a
  document of this matter is a violation (the record is refused), never a
  guess.
* **A re-check answers with a patch.** :func:`merge` applies the changed
  top-level fields and the changed list items, matched by their natural keys
  (a task by its id, a facility by its name, a Medicals row by provider and
  first service date), onto the prior record. The merged record then goes
  through the unchanged validator. A patch that cannot be applied
  unambiguously raises :class:`PatchRefused`, and the reader is asked for the
  whole record instead: never a best-effort merge.
* **The Operator's own hand is not a change.** :func:`stamped_ids` names the
  tasks, events and memos the Operator wrote on Smokeball, by the provenance
  stamp every Operator write carries. Smokeball authors an Operator write as
  the person who consented the connection and has no endpoint naming the
  calling user, so a creator or owner id would match that person's own work;
  the stamp is what only the Operator writes. Documents carry no stamp: the
  lane's own files are excluded by its write journal (``own_writes``).
"""

from __future__ import annotations

import copy
import re
from typing import Any

#: The provenance stamp the Smokeball connector puts on every Operator write
#: (``smokeball_connector.task_update.PROVENANCE_MARK``), restated so the screen
#: needs no connector import.
PROVENANCE_MARK = "[Operator]"
UUID = re.compile(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b")
DOC_REF = re.compile(r"\[doc (\d+)\]")
BARE_REF = re.compile(r"^\s*\[?\s*doc\s*#?\s*(\d+)\s*\]?\s*$", re.I)

#: Every list in the record and the fields that identify one of its items.
#: ``None``: a list of strings, each item its own key.
LIST_KEYS: dict[str, tuple[str, ...] | None] = {
    "open_tasks": ("task_id",),
    "done_tasks_checked": ("task_id",),
    "facilities": ("facility",),
    "records_to_order": ("facility",),
    "medicals": ("provider", "service_start"),
    "letters": ("type",),
    "injury_flags": ("finding",),
    "time_sensitive": ("finding",),
    "unreadable_docs": ("doc_id",),
    "clients": None,
    "questions": None,
}
PATCH_PARTS = ("set", "upsert", "remove")


class PatchRefused(ValueError):
    """A patch that cannot be merged onto the prior record unambiguously."""


# ---- document references ---------------------------------------------------------


def _is_doc_key(key: Any) -> bool:
    k = str(key)
    return k.endswith(("doc_id", "doc_ids"))


def to_refs(obj: Any, by_id: dict[str, int]) -> Any:
    """Every document id inside ``obj`` replaced by ``[doc N]``; ids that are
    not a listed document (a task, a contact, a document since removed) stay."""
    low = {str(k).lower(): n for k, n in by_id.items()}

    def sub(m: re.Match[str]) -> str:
        n = low.get(m.group(0).lower())
        return f"[doc {n}]" if n is not None else m.group(0)

    if isinstance(obj, str):
        return UUID.sub(sub, obj)
    if isinstance(obj, dict):
        return {k: to_refs(v, by_id) for k, v in obj.items()}
    if isinstance(obj, list):
        return [to_refs(v, by_id) for v in obj]
    return obj


class _Resolver:
    def __init__(self, refs: dict[int, dict[str, Any]]) -> None:
        self.refs, self.unknown = refs, set()

    def doc(self, n: int) -> str | None:
        f = self.refs.get(n)
        if f is None:
            self.unknown.add(n)
            return None
        return str(f["id"])

    def text(self, s: str) -> str:
        def sub(m: re.Match[str]) -> str:
            got = self.doc(int(m.group(1)))
            return got if got is not None else m.group(0)

        return DOC_REF.sub(sub, s)

    def one_id(self, v: Any) -> Any:
        if isinstance(v, int) and not isinstance(v, bool):
            got = self.doc(v)
            return got if got is not None else v
        if isinstance(v, str):
            m = BARE_REF.match(v) or (re.fullmatch(r"\s*(\d+)\s*", v))
            if m:
                got = self.doc(int(m.group(1)))
                return got if got is not None else v
            return self.text(v)
        return self.walk(v)

    def walk(self, obj: Any) -> Any:
        if isinstance(obj, str):
            return self.text(obj)
        if isinstance(obj, dict):
            out = {}
            for k, v in obj.items():
                if _is_doc_key(k):
                    out[k] = [self.one_id(x) for x in v] if isinstance(v, list) else self.one_id(v)
                else:
                    out[k] = self.walk(v)
            return out
        if isinstance(obj, list):
            return [self.walk(v) for v in obj]
        return obj


def resolve(obj: Any, refs: dict[int, dict[str, Any]]) -> tuple[Any, list[str]]:
    """``obj`` with every ``[doc N]`` (and, in a document-id field, a bare
    number) turned into the document's id, plus a violation per number that
    is not a document of this matter."""
    r = _Resolver(refs)
    got = r.walk(obj)
    problems = [
        f"unknown_doc: [doc {n}] is not a document on this matter (use a number from the listing or list_files)"
        for n in sorted(r.unknown)
    ]
    return got, problems


# ---- the patch -------------------------------------------------------------------


def _norm(v: Any) -> str:
    return " ".join(str(v if v is not None else "").split()).casefold()


def _key(field: str, item: Any) -> tuple[str, ...]:
    fields = LIST_KEYS[field]
    if fields is None:
        if not isinstance(item, str) or not item.strip():
            raise PatchRefused(f"{field}: an item is not a non-empty string")
        return (_norm(item),)
    if isinstance(item, str) and len(fields) == 1:
        got: tuple[str, ...] = (_norm(item),)
    elif isinstance(item, dict):
        got = tuple(_norm(item.get(f)) for f in fields)
    else:
        raise PatchRefused(f"{field}: an item is neither an object nor its key")
    if not all(got):
        raise PatchRefused(f"{field}: an item has no {' + '.join(fields)} to match it by")
    return got


def _index(field: str, items: list[Any]) -> dict[tuple[str, ...], list[int]]:
    out: dict[tuple[str, ...], list[int]] = {}
    for i, item in enumerate(items):
        try:
            k = _key(field, item)
        except PatchRefused:
            continue  # a prior item with no key cannot be addressed; untouched it stays
        out.setdefault(k, []).append(i)
    return out


def _list_part(patch: dict[str, Any], part: str) -> dict[str, list[Any]]:
    got = patch.get(part) or {}
    if not isinstance(got, dict):
        raise PatchRefused(f"{part} is not an object")
    for field, items in got.items():
        if field not in LIST_KEYS:
            raise PatchRefused(f"{part}.{field}: not a list the record carries by item (use set)")
        if not isinstance(items, list):
            raise PatchRefused(f"{part}.{field} is not a list")
    return got


def merge(prior: dict[str, Any], patch: Any) -> dict[str, Any]:
    """The prior record with ``patch`` applied, or :class:`PatchRefused`.

    ``patch`` is ``{"set": {field: value}, "upsert": {list: [item]},
    "remove": {list: [item or key]}}``: ``set`` replaces a top-level field
    whole, ``upsert`` replaces the prior item with the same key or appends a
    new one, ``remove`` drops the prior item with that key."""
    from .read import RECORD_KEYS

    if not isinstance(patch, dict) or not isinstance(prior, dict):
        raise PatchRefused("the patch or the prior record is not an object")
    extra = sorted(set(patch) - set(PATCH_PARTS))
    if extra:
        raise PatchRefused(f"unknown patch parts {extra}; a patch has only {list(PATCH_PARTS)}")
    sets = patch.get("set") or {}
    if not isinstance(sets, dict):
        raise PatchRefused("set is not an object")
    unknown = sorted(set(sets) - set(RECORD_KEYS))
    if unknown:
        raise PatchRefused(f"set names fields the record does not carry: {unknown}")
    ups, rms = _list_part(patch, "upsert"), _list_part(patch, "remove")
    clash = sorted(set(sets) & (set(ups) | set(rms)))
    if clash:
        raise PatchRefused(f"{clash} are both replaced whole and patched by item")
    out = copy.deepcopy(prior)
    out.update(copy.deepcopy(sets))
    for field in sorted(set(ups) | set(rms)):
        out[field] = _merge_list(field, out.get(field), ups.get(field) or [], rms.get(field) or [])
    return out


def _merge_list(field: str, prior: Any, ups: list[Any], rms: list[Any]) -> list[Any]:
    if prior is None:
        prior = []
    if not isinstance(prior, list):
        raise PatchRefused(f"the prior record's {field} is not a list")
    items = copy.deepcopy(prior)
    where = _index(field, items)
    up_keys = [_key(field, x) for x in ups]
    rm_keys = [_key(field, x) for x in rms]
    for keys, what in ((up_keys, "upsert"), (rm_keys, "remove")):
        if len(set(keys)) != len(keys):
            raise PatchRefused(f"{what}.{field} names the same item twice")
    if set(up_keys) & set(rm_keys):
        raise PatchRefused(f"{field}: an item is both upserted and removed")
    for k in [*up_keys, *rm_keys]:
        if len(where.get(k, [])) > 1:
            raise PatchRefused(f"{field}: the prior record holds more than one item matching {list(k)}")
    for k in rm_keys:
        if k not in where:
            raise PatchRefused(f"remove.{field}: no prior item matches {list(k)}")
    drop = {where[k][0] for k in rm_keys}
    for k, item in zip(up_keys, ups):
        if k in where:
            items[where[k][0]] = copy.deepcopy(item)
        else:
            items.append(copy.deepcopy(item))
    return [x for i, x in enumerate(items) if i not in drop]


# ---- the Operator's own hand ------------------------------------------------------


def _stamped(*texts: Any) -> bool:
    return any(isinstance(t, str) and t.lstrip().upper().startswith(PROVENANCE_MARK.upper()) for t in texts)


def stamped_ids(inv: dict[str, Any]) -> set[str]:
    """The ids of the tasks, events and memos on this matter the Operator wrote
    (subject, note or text opens with the provenance stamp)."""
    out: set[str] = set()
    for t in [*(inv.get("tasks_open") or []), *(inv.get("tasks_done") or [])]:
        if t.get("id") and _stamped(t.get("subject"), t.get("note")):
            out.add(str(t["id"]))
    for e in inv.get("events") or []:
        if e.get("id") and _stamped(e.get("subject"), e.get("description")):
            out.add(str(e["id"]))
    for m in inv.get("memos") or []:
        if m.get("id") and _stamped(m.get("title"), m.get("text"), m.get("plainText")):
            out.add(str(m["id"]))
    return out


__all__ = ["LIST_KEYS", "PROVENANCE_MARK", "PatchRefused", "merge", "resolve", "stamped_ids", "to_refs"]
