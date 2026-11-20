"""The answer key a replay compares against, read tolerantly.

The ops workspace writes one per saved matter from the hand drafts::

    {"matter": "...",
     "served_sets": [{"type", "set_number", "sha256", "request_count"}],
     "form": {"checked": [...], "kinds": {"<number>": "<kind>"}, "names": {"<number>": [...]}},
     "special": [{"n", "kind", "request_head"}],
     "production": [{"n", "kind", "request_head"}]}

Every key but ``served_sets`` is optional, and a malformed entry is dropped
and counted in ``dropped`` rather than failing the replay: the key is an
instrument, and a replay that refuses to run on a half-written key tells SMD
nothing.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

#: A form answer's kind, as the key and the answer stage name it.
KINDS = frozenset(
    {"answered_from_file", "none", "no_information", "not_applicable", "objection_only", "objection_plus_answer"}
)
#: The key's per-request lists, by the set type they belong to.
ITEM_LISTS = {"special": "special_interrogatories", "production": "requests_for_production"}


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


def _int(v: Any) -> int | None:
    if isinstance(v, bool):
        return None
    if isinstance(v, int):
        return v
    if isinstance(v, str) and v.strip().isdigit():
        return int(v.strip())
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
        n = _int(s.get("set_number")) if isinstance(s, dict) else None
        if not isinstance(s, dict) or not isinstance(s.get("type"), str) or n is None:
            dropped.append(f"served_sets[{i}]")
            continue
        out.append(KeySet(s["type"], n, str(s.get("sha256") or "").lower(), _int(s.get("request_count"))))
    return out


def _items(raw: Any, name: str, dropped: list[str]) -> list[KeyItem]:
    out: list[KeyItem] = []
    for i, it in enumerate(raw if isinstance(raw, list) else []):
        n = _int(it.get("n")) if isinstance(it, dict) else None
        if not isinstance(it, dict) or n is None:
            dropped.append(f"{name}[{i}]")
            continue
        out.append(KeyItem(n, str(it.get("kind") or ""), str(it.get("request_head") or "")))
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


def parse(doc: Any) -> Key:
    if not isinstance(doc, dict):
        raise ValueError("the answer key is not a JSON object")
    dropped: list[str] = []
    checked, kinds, names = _form(doc.get("form"), dropped)
    return Key(
        matter=str(doc.get("matter") or ""),
        served_sets=_sets(doc.get("served_sets"), dropped),
        checked=checked,
        kinds=kinds,
        names=names,
        items={name: _items(doc.get(name), name, dropped) for name in ITEM_LISTS},
        dropped=dropped,
    )


def load(path: Path) -> Key:
    return parse(json.loads(path.read_text(encoding="utf-8")))
