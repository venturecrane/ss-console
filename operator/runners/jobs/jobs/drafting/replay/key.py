"""The answer key a replay compares against, read tolerantly.

The ops workspace writes one per saved matter from the hand drafts::

    {"matter": "...",
     "served_sets": [{"type", "set_number", "sha256", "request_count"}],
     "form": {"checked": [...], "kinds": {"<number>": "<kind>"}, "names": {"<number>": [...]}},
     "special": [{"n", "kind", "request_head"}],
     "production": [{"n", "kind", "request_head"}],
     "exhibits": [{"letter", "title", "files": [{"name", "requests"} | "1. <name> (Request Nos. 2, 6)"]}]}

The workspace's own hand-built keys use a second, shorter shape, read the
same (2026-11-20: the loader read them as an empty key and the replay said
nothing)::

    {"served_sets": [{"type": "si" | "frog" | "rfp" | "rfa" | <a SET_TYPES key>,
                      "set", "sha256", "requests"}],
     "special": [{"n", "kind", "head"}], ...}

with ``set`` in words ("One") and the per-request answers under ``responses``
(``from_responses``).

Every key but ``served_sets`` is optional, and a malformed entry is dropped
and counted in ``dropped`` rather than failing the replay: the key is an
instrument, and a replay that refuses to run on a half-written key tells SMD
nothing. A key that loads with NO served set is the exception
(:class:`EmptyKey`): every comparison would be vacuous, so it fails loudly,
naming the field each entry lacked.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

#: A form answer's kind, as the key and the answer stage name it.
KINDS = frozenset(
    {"answered_from_file", "none", "no_information", "not_applicable", "objection_only", "objection_plus_answer"}
)
#: The key's per-request lists, by the set type they belong to.
ITEM_LISTS = {"special": "special_interrogatories", "production": "requests_for_production"}
#: The short set names the workspace's keys use, and their spellings.
TYPE_ALIASES = {
    "si": "special_interrogatories",
    "srog": "special_interrogatories",
    "srogs": "special_interrogatories",
    "fi": "form_interrogatories",
    "frog": "form_interrogatories",
    "frogs": "form_interrogatories",
    "rfp": "requests_for_production",
    "rfps": "requests_for_production",
    "rpd": "requests_for_production",
    "rfa": "requests_for_admission",
    "rfas": "requests_for_admission",
}


class EmptyKey(ValueError):
    """A key with no usable served set: the replay exits 2, naming what is missing."""


def _first(d: dict[str, Any], *names: str) -> Any:
    return next((d[n] for n in names if d.get(n) is not None), None)


def set_type(v: Any) -> str | None:
    if not isinstance(v, str) or not v.strip():
        return None
    t = v.strip().lower()
    return TYPE_ALIASES.get(t, t)


@dataclass(frozen=True)
class KeySet:
    type: str
    set_number: int
    sha256: str
    request_count: int | None


@dataclass(frozen=True)
class KeyItem:
    n: int
    kind: str
    request_head: str


@dataclass
class Key:
    matter: str
    served_sets: list[KeySet]
    checked: list[str] | None
    kinds: dict[str, str]
    names: dict[str, list[str]]
    items: dict[str, list[KeyItem]]
    dropped: list[str] = field(default_factory=list)
    #: The hand Index of Exhibits, one line per file (``exhibit_lines``).
    exhibits: list[KeyLine] = field(default_factory=list)


@dataclass(frozen=True)
class KeyLine:
    """One line of the key's Index of Exhibits: the file, its exhibit, and the
    request numbers it lists (None when the key line names none)."""

    letter: str
    name: str
    requests: tuple[int, ...] | None


#: "1. Valley Ortho ledger.pdf (Request Nos. 2, 6)", as the hand index prints a line.
_INDEX_LINE = re.compile(r"^\s*\d+\.\s*(.+?)\s*(?:\(Request Nos?\.\s*([^)]*)\))?\s*$", re.I)


def _requests(v: Any) -> tuple[int, ...] | None:
    if isinstance(v, str):
        return tuple(int(x) for x in re.findall(r"\d+", v))
    if isinstance(v, list):
        return tuple(n for n in (_int(x) for x in v) if n is not None)
    return None


def exhibit_lines(raw: Any, dropped: list[str]) -> list[KeyLine]:
    """``exhibits``: ``[{"letter", "title", "files": [{"name", "requests": [..]} | "<index line>"]}]``."""
    out: list[KeyLine] = []
    for i, e in enumerate(raw if isinstance(raw, list) else []):
        letter = str(e.get("letter") or "").strip().upper() if isinstance(e, dict) else ""
        if not letter or not isinstance(e.get("files"), list):
            dropped.append(f"exhibits[{i}]")
            continue
        for f in e["files"]:
            if isinstance(f, dict) and str(f.get("name") or "").strip():
                out.append(KeyLine(letter, str(f["name"]).strip(), _requests(f.get("requests"))))
                continue
            m = _INDEX_LINE.match(f) if isinstance(f, str) else None
            if m:
                out.append(KeyLine(letter, m.group(1), _requests(m.group(2)) if m.group(2) else None))
            else:
                dropped.append(f"exhibits[{i}] line {str(f)[:40]!r}")
    return out


#: A set number as the keys spell it in words ("set": "One").
_WORDS = {w: i for i, w in enumerate("one two three four five six seven eight nine ten".split(), 1)}


def _int(v: Any) -> int | None:
    if isinstance(v, bool):
        return None
    if isinstance(v, int):
        return v
    if isinstance(v, str) and v.strip().isdigit():
        return int(v.strip())
    if isinstance(v, str):
        return _WORDS.get(v.strip().lower())
    return None


def number(v: Any) -> str:
    """A form number as one spelling: ``6.4``, never ``6.40`` or `` 6.4 ``."""
    s = str(v).strip().rstrip(".")
    head, dot, tail = s.partition(".")
    if dot and head.isdigit() and tail.isdigit():
        return f"{int(head)}.{int(tail)}"
    return s


def _sets(raw: Any, dropped: list[str]) -> list[KeySet]:
    out: list[KeySet] = []
    for i, s in enumerate(raw if isinstance(raw, list) else []):
        if not isinstance(s, dict):
            dropped.append(f"served_sets[{i}] (not an object)")
            continue
        kind, n = set_type(_first(s, "type", "set_type")), _int(_first(s, "set_number", "set"))
        if kind is None or n is None:
            lacks = [name for name, v in (("type", kind), ("set_number or set", n)) if v is None]
            dropped.append(f"served_sets[{i}] (no {', '.join(lacks)})")
            continue
        count = _int(_first(s, "request_count", "requests"))
        out.append(KeySet(kind, n, str(s.get("sha256") or "").lower(), count))
    return out


def _items(raw: Any, name: str, dropped: list[str]) -> list[KeyItem]:
    out: list[KeyItem] = []
    for i, it in enumerate(raw if isinstance(raw, list) else []):
        n = _int(it.get("n")) if isinstance(it, dict) else None
        if not isinstance(it, dict) or n is None:
            dropped.append(f"{name}[{i}]")
            continue
        out.append(KeyItem(n, str(it.get("kind") or ""), str(_first(it, "request_head", "head") or "")))
    return out


def _form(raw: Any, dropped: list[str]) -> tuple[list[str] | None, dict[str, str], dict[str, list[str]]]:
    form = raw if isinstance(raw, dict) else {}
    checked = form.get("checked")
    nums = [number(x) for x in checked] if isinstance(checked, list) else None
    kinds: dict[str, str] = {}
    for k, v in (form.get("kinds") or {}).items() if isinstance(form.get("kinds"), dict) else []:
        if v in KINDS:
            kinds[number(k)] = v
        else:
            dropped.append(f"form.kinds[{k}]")
    names: dict[str, list[str]] = {}
    for k, v in (form.get("names") or {}).items() if isinstance(form.get("names"), dict) else []:
        if isinstance(v, list):
            names[number(k)] = [str(x) for x in v if str(x).strip()]
    return nums, kinds, names


def _entries(raw: Any) -> list[tuple[Any, dict[str, Any]]]:
    """``{number: {...}}`` or ``[{"n": number, ...}]``, as (number, entry) pairs."""
    if isinstance(raw, dict):
        return [(k, v) for k, v in raw.items() if isinstance(v, dict)]
    if isinstance(raw, list):
        return [(_first(v, "n", "number"), v) for v in raw if isinstance(v, dict)]
    return []


def from_responses(doc: dict[str, Any]) -> dict[str, Any]:
    """The workspace's ``responses`` shape, as the top-level keys ``parse`` reads::

        "responses": {"FI": {"checked_numbers": [...], "per_number": {"6.4": {"kind"}}},
                      "SI" | "RFP": {"per_request": {"1": {"kind", "request_first80"}}}}

    A top-level ``form``, ``special`` or ``production`` the key already has wins."""
    out = dict(doc)
    lists = {t: name for name, t in ITEM_LISTS.items()}
    for code, block in (doc.get("responses") or {}).items() if isinstance(doc.get("responses"), dict) else []:
        kind = set_type(code)
        if not isinstance(block, dict):
            continue
        if kind == "form_interrogatories" and "form" not in doc:
            per = _entries(block.get("per_number"))
            out["form"] = {"checked": block.get("checked_numbers", block.get("checked")),
                           "kinds": {str(n): e.get("kind") for n, e in per if n is not None and e.get("kind")}}  # fmt: skip
        elif kind in lists and lists[kind] not in doc:
            out[lists[kind]] = [
                {"n": n, "kind": e.get("kind"), "head": _first(e, "request_first80", "request_head", "head")}
                for n, e in _entries(block.get("per_request"))
            ]
    return out


def parse(doc: Any) -> Key:
    if not isinstance(doc, dict):
        raise ValueError("the answer key is not a JSON object")
    doc = from_responses(doc)
    dropped: list[str] = []
    checked, kinds, names = _form(doc.get("form"), dropped)
    served = _sets(doc.get("served_sets"), dropped)
    if not served:
        why = "; ".join(d for d in dropped if d.startswith("served_sets")) or "no served_sets list"
        raise EmptyKey(f"the answer key has no served set the replay can compare against ({why})")
    return Key(
        matter=str(doc.get("matter") or ""),
        served_sets=served,
        checked=checked,
        kinds=kinds,
        names=names,
        items={name: _items(doc.get(name), name, dropped) for name in ITEM_LISTS},
        dropped=dropped,
        exhibits=exhibit_lines(doc.get("exhibits"), dropped),
    )


def load(path: Path) -> Key:
    return parse(json.loads(path.read_text(encoding="utf-8")))
