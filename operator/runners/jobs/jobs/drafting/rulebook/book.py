"""The firm's phrase book: the standard wording code writes into a response.

Authored as an optional top-level section of ``drafting-firm.yaml``::

    phrase_book: {path: <a JSON file pinned in inputs>, stages: [revise | first_draft, ...]}

Absent, nothing reads it and every job runs as it did before the section
existed. ``stages`` switches the rules stage on per mode, so revisions can go on
before first drafts. The file is a list of entries ``{key, kind, text, names,
sets, source}`` (design C); ``load`` reads it, the stage that uses it is built
in its own change.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from .types import MODES

KEY = "phrase_book"
KEYS = ("path", "stages")
KINDS = ("objection", "disposition", "transition", "statement")
ENTRY_KEYS = ("key", "kind", "text", "names", "sets", "source")


def validate(body: object) -> list[str]:
    """The section's closed shape: ``{path, stages: [one or more of MODES, no repeats]}``."""
    if not isinstance(body, dict) or set(body) != set(KEYS):
        return [f"{KEY}: expected exactly {{path, stages: [one or more of {list(MODES)}]}}"]
    out = []
    if not isinstance(body["path"], str) or not body["path"].strip():
        out.append(f"{KEY}.path: expected a path in inputs")
    stages = body["stages"]
    if not isinstance(stages, list) or not stages or not all(isinstance(s, str) for s in stages):
        return [*out, f"{KEY}.stages: expected a list of one or more of {list(MODES)}"]
    out += [f"{KEY}.stages: {s!r} is not one of {list(MODES)}" for s in stages if s not in MODES]
    if len(set(stages)) != len(stages):
        out.append(f"{KEY}.stages: a stage is named twice")
    return out


def referenced_paths(data: dict[str, Any]) -> list[tuple[str, str]]:
    """(where, path) of the book's file, for the inputs pin check."""
    body = data.get(KEY)
    return [(f"{KEY}.path", body["path"])] if isinstance(body, dict) and isinstance(body.get("path"), str) else []


def stages(data: dict[str, Any] | None) -> tuple[str, ...]:
    """The modes the firm switched the rules stage on for; () when unauthored."""
    body = (data or {}).get(KEY)
    if body is None or validate(body):
        return ()
    return tuple(body["stages"])


@dataclass(frozen=True)
class Entry:
    key: str
    kind: str
    text: str
    names: tuple[str, ...] = ()
    sets: tuple[str, ...] = ()
    source: str = ""


@dataclass(frozen=True)
class Book:
    entries: dict[str, Entry] = field(default_factory=dict)

    def text(self, key: str, item: str = "", exhibits: str = "") -> str:
        """An entry's words with its ``{item}`` and ``{exhibit}`` slots filled."""
        return self.entries[key].text.replace("{item}", item).replace("{exhibit}", exhibits)

    def find(self, sentence: str) -> str | None:
        """The key of the entry whose words are this sentence (spacing and case
        folded), else None."""
        want = " ".join(sentence.split()).casefold()
        for k, e in self.entries.items():
            if " ".join(e.text.split()).casefold() == want:
                return k
        return None


class BookError(ValueError):
    """The book file is not the closed shape."""


def parse(raw: str) -> Book:
    try:
        rows = json.loads(raw)
    except ValueError as exc:
        raise BookError(f"not JSON: {exc}") from None
    if not isinstance(rows, list):
        raise BookError("expected a list of entries")
    out: dict[str, Entry] = {}
    for i, r in enumerate(rows):
        if not isinstance(r, dict) or set(r) != set(ENTRY_KEYS):
            raise BookError(f"entry {i}: expected exactly {list(ENTRY_KEYS)}")
        if r["kind"] not in KINDS:
            raise BookError(f"entry {i}: kind {r['kind']!r} is not one of {list(KINDS)}")
        if not all(isinstance(r[k], str) and r[k].strip() for k in ("key", "text")) or r["key"] in out:
            raise BookError(f"entry {i}: a non-empty key, not repeated, and non-empty text")
        out[r["key"]] = Entry(
            r["key"], r["kind"], r["text"], tuple(r["names"] or ()), tuple(r["sets"] or ()), str(r["source"] or "")
        )
    return Book(out)


def load(firm: Any) -> Book | None:
    """The firm's book, or None when the firm authored no ``phrase_book``."""
    data = getattr(firm, "data", None) or {}
    if not stages(data):
        return None
    return parse(firm.read(data[KEY]["path"]))
