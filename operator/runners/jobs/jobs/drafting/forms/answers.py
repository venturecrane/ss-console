"""What a model returns for one section of form interrogatories, and its check.

One call per checked SECTION (``work_units``) returns values only, never
layout. The shape, as JSON::

    {"answers": [
      {"number": "6.4", "kind": "answered", "items": [
         {"name": "<who or what this item is>",
          "parts": {"a": {"text": "...", "cites": [{"file": "...", "page": 3, "excerpt": "..."}]},
                    "b": {...}}}]},
      {"number": "2.2", "kind": "answered", "items": [
         {"text": "...", "cites": [...]}]},
      {"number": "6.7", "kind": "none"}
    ]}

``kind`` is ``answered``, ``none`` (the answer is no), ``no_information`` or
``not_applicable``; only ``answered`` carries items. An interrogatory with
lettered subparts answers per letter in ``parts`` (a letter left out renders
the firm's no-information sentence); one without answers in ``text``. Every
value carries at least one cite, which code verifies against the page
elsewhere (``evidence/verify.py``). Nothing outside the shape is accepted: no
extra keys, no letter the form does not print for that number, no number the
call was not given, and no text that could carry layout (a line break, a
markdown mark, a lettered label of its own).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Mapping

from .catalog import Catalogue, load

KINDS = ("answered", "none", "no_information", "not_applicable")
EXCERPT_MAX = 400
TEXT_MAX = 2000
#: Text that would change the layout code owns, or smuggle a marker through.
_UNSAFE = re.compile(r"[\r\n*`|]|\{\{|\}\}|^\s*(?:#|>|-\s|\d+[.)]\s)|\([a-z]\)\s")


class AnswerError(ValueError):
    def __init__(self, problems: list[str]) -> None:
        super().__init__("; ".join(problems))
        self.problems = problems


@dataclass(frozen=True)
class Cite:
    file: str
    page: int
    excerpt: str


@dataclass(frozen=True)
class Value:
    text: str
    cites: tuple[Cite, ...]


@dataclass(frozen=True)
class Item:
    name: str | None
    parts: Mapping[str, Value]  # letter -> value; "" for an interrogatory with no subparts


@dataclass(frozen=True)
class Answer:
    number: str
    kind: str
    items: tuple[Item, ...] = ()


@dataclass(frozen=True)
class WorkUnit:
    """One model call: a section's checked numbers, in form order."""

    section: str
    title: str
    numbers: tuple[str, ...]


def work_units(checked: list[str] | tuple[str, ...], cat: Catalogue | None = None) -> list[WorkUnit]:
    cat = cat or load()
    out: list[WorkUnit] = []
    for n in sorted(checked, key=cat.position):
        sec = cat.section_of(n)
        if out and out[-1].section == sec.number:
            out[-1] = WorkUnit(sec.number, sec.title, (*out[-1].numbers, n))
        else:
            out.append(WorkUnit(sec.number, sec.title, (n,)))
    return out


def _keys(raw: Any, allowed: set[str], required: set[str], where: str, problems: list[str]) -> bool:
    if not isinstance(raw, dict):
        problems.append(f"{where}: must be an object")
        return False
    extra = sorted(set(raw) - allowed)
    missing = sorted(required - set(raw))
    if extra:
        problems.append(f"{where}: keys not in the shape: {extra}")
    if missing:
        problems.append(f"{where}: missing {missing}")
    return not extra and not missing


def _text(raw: Any, where: str, problems: list[str], limit: int = TEXT_MAX) -> str | None:
    if not isinstance(raw, str) or not raw.strip():
        problems.append(f"{where}: must be non-empty text")
        return None
    if len(raw) > limit:
        problems.append(f"{where}: longer than {limit} characters")
        return None
    if _UNSAFE.search(raw):
        problems.append(f"{where}: carries a line break, a markdown mark or a lettered label")
        return None
    return " ".join(raw.split())


def _cite(raw: Any, where: str, problems: list[str]) -> Cite | None:
    if not _keys(raw, {"file", "page", "excerpt"}, {"file", "page", "excerpt"}, where, problems):
        return None
    page = raw["page"]
    if isinstance(page, bool) or not isinstance(page, int) or page < 1:
        problems.append(f"{where}: page must be a whole number, 1 or more")
        return None
    f = raw["file"]
    excerpt = raw["excerpt"]
    if not isinstance(f, str) or not f.strip() or not isinstance(excerpt, str) or not excerpt.strip():
        problems.append(f"{where}: file and excerpt must be non-empty text")
        return None
    if len(excerpt) > EXCERPT_MAX:
        problems.append(f"{where}: excerpt longer than {EXCERPT_MAX} characters")
        return None
    return Cite(f.strip(), page, " ".join(excerpt.split()))


def _value(raw: Any, where: str, problems: list[str]) -> Value | None:
    if not _keys(raw, {"text", "cites"}, {"text", "cites"}, where, problems):
        return None
    text = _text(raw["text"], f"{where}.text", problems)
    cites_raw = raw["cites"]
    if not isinstance(cites_raw, list) or not cites_raw:
        problems.append(f"{where}: every answered value needs at least one cite")
        return None
    cites = [_cite(c, f"{where}.cites[{i}]", problems) for i, c in enumerate(cites_raw)]
    if text is None or any(c is None for c in cites):
        return None
    return Value(text, tuple(c for c in cites if c is not None))


def _item(raw: Any, letters: tuple[str, ...], where: str, problems: list[str]) -> Item | None:
    if letters:
        if not _keys(raw, {"name", "parts"}, {"parts"}, where, problems):
            return None
        parts_raw = raw["parts"]
        if not isinstance(parts_raw, dict) or not parts_raw:
            problems.append(f"{where}.parts: must name at least one lettered subpart")
            return None
        wrong = sorted(k for k in parts_raw if k not in letters)
        if wrong:
            problems.append(f"{where}.parts: {wrong} are not subparts of this interrogatory (it has {list(letters)})")
            return None
        parts = {k: _value(parts_raw[k], f"{where}.parts.{k}", problems) for k in letters if k in parts_raw}
    else:
        if not _keys(raw, {"name", "text", "cites"}, {"text", "cites"}, where, problems):
            return None
        parts = {"": _value({"text": raw["text"], "cites": raw["cites"]}, where, problems)}
    name = None
    if raw.get("name") is not None:
        name = _text(raw["name"], f"{where}.name", problems, 300)
        if name is None:
            return None
    if any(v is None for v in parts.values()):
        return None
    return Item(name, {k: v for k, v in parts.items() if v is not None})


def _answer(raw: Any, numbers: tuple[str, ...], cat: Catalogue, problems: list[str]) -> Answer | None:
    if not isinstance(raw, dict) or not isinstance(raw.get("number"), str):
        problems.append("an answer must be an object with a number")
        return None
    n = raw["number"]
    where = f"answer {n}"
    if n not in numbers:
        problems.append(f"{where}: not a number this call was given ({list(numbers)})")
        return None
    if raw.get("kind") not in KINDS:
        problems.append(f"{where}: kind must be one of {list(KINDS)}")
        return None
    kind = raw["kind"]
    if kind != "answered":
        return Answer(n, kind) if _keys(raw, {"number", "kind"}, {"number", "kind"}, where, problems) else None
    if not _keys(raw, {"number", "kind", "items"}, {"number", "kind", "items"}, where, problems):
        return None
    if not isinstance(raw["items"], list) or not raw["items"]:
        problems.append(f"{where}: an answered interrogatory needs at least one item")
        return None
    letters = cat.get(n).letters
    items = [_item(it, letters, f"{where}.items[{i}]", problems) for i, it in enumerate(raw["items"])]
    if len(items) > 1 and any(it is not None and not it.name for it in items):
        problems.append(f"{where}: with more than one item, every item needs a name")
        return None
    if any(it is None for it in items):
        return None
    return Answer(n, kind, tuple(it for it in items if it is not None))


def parse_section(raw: Any, numbers: list[str] | tuple[str, ...], cat: Catalogue | None = None) -> list[Answer]:
    """One call's reply, validated: exactly one answer per number given, in
    form order on return. Raises ``AnswerError`` with every problem."""
    cat = cat or load()
    nums = tuple(numbers)
    problems: list[str] = []
    if not _keys(raw, {"answers"}, {"answers"}, "the reply", problems) or not isinstance(raw["answers"], list):
        raise AnswerError(problems or ["the reply: answers must be a list"])
    answers = [_answer(a, nums, cat, problems) for a in raw["answers"]]
    got = [a.number for a in answers if a is not None]
    problems.extend(f"answer {n}: given more than once" for n in sorted({n for n in got if got.count(n) > 1}))
    problems.extend(f"answer {n}: missing" for n in nums if n not in got and n not in _named(raw["answers"]))
    if problems:
        raise AnswerError(problems)
    return sorted((a for a in answers if a is not None), key=lambda a: cat.position(a.number))


def _named(raw: list[Any]) -> set[str]:
    return {a["number"] for a in raw if isinstance(a, dict) and isinstance(a.get("number"), str)}
