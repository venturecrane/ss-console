"""The firm's phrase book: the standard wording code writes into a response.

Authored as an optional top-level section of ``drafting-firm.yaml``::

    phrase_book: {path: <a JSON file pinned in inputs>, stages: [revise | first_draft, ...]}

Absent, nothing reads it and every job runs as it did before the section
existed. ``stages`` switches the rules stage on per mode, so revisions can go on
before first drafts. The file is a list of entries ``{key, kind, text, names,
sets, source}`` (design C). ``{item}`` in an entry's text is the response's own
word for what it answers ("Request", "Interrogatory"); ``{exhibit}`` is the
exhibit letters code fills from the document's own index.

A rule whose own words are the firm's wording (quoted, or stated: "state that
the responding party will not respond further") adds an entry of its own at the
job's rule read (``reading.py``), keyed ``<rule id>#<clause>``, so such a rule
needs no change to the book file.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any

from .types import MODES

KEY = "phrase_book"
KEYS = ("path", "stages")
KINDS = ("objection", "disposition", "transition", "statement")
ENTRY_KEYS = ("key", "kind", "text", "names", "sets", "source")
#: The fewest words a long-form objection's shared lead-in has (``Book.objection_lead``).
LEAD_WORDS = 4

_FOLD = str.maketrans({"‘": "'", "’": "'", "“": '"', "”": '"', " ": " "})
#: Exhibit letters as they read once folded: "a", "a and b", "a, b and c".
_LETTERS = r"[a-z]{1,2}(?:(?:,\s*(?:and\s+)?|\s+and\s+)[a-z]{1,2})*"
_CAPS = re.compile(r"\b[A-Z][a-z]+(?: [A-Z][a-z]+)*\b")
_SLOT = re.compile(r"(\{item\}|\{exhibit\})")


def norm(text: str) -> str:
    """Curly quotes and spacing folded, case kept."""
    return " ".join(str(text or "").translate(_FOLD).split())


def fold(text: str) -> str:
    """Words as two are compared: curly quotes, spacing and case folded."""
    return norm(text).casefold()


def bare(text: str) -> str:
    """``fold`` with end punctuation off: "... Exhibit B" and "... Exhibit B." are one sentence."""
    return fold(text).rstrip(" .;,:")


@lru_cache(maxsize=512)
def _pattern(text: str) -> re.Pattern[str]:
    """An entry's words as a pattern over a ``bare`` sentence: ``{item}`` any
    short run of words, ``{exhibit}`` exhibit letters (after "Exhibit" or
    "Exhibits")."""
    out = []
    for piece in _SLOT.split(bare(text)):
        if piece == "{item}":
            out.append(r".{1,40}?")
        elif piece == "{exhibit}":
            out.append(_LETTERS)
        else:
            out.append(re.sub(r"exhibit\\ $", r"exhibits?\\ ", re.escape(piece)))
    return re.compile("".join(out))


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

    def to_dict(self) -> dict[str, Any]:
        return {**vars(self), "names": list(self.names), "sets": list(self.sets)}


@dataclass(frozen=True)
class Book:
    entries: dict[str, Entry] = field(default_factory=dict)

    def text(self, key: str, item: str = "", exhibits: str = "") -> str:
        """An entry's words with its ``{item}`` and ``{exhibit}`` slots filled."""
        return self.entries[key].text.replace("{item}", item).replace("{exhibit}", exhibits)

    def find(self, sentence: str) -> str | None:
        """The key of the entry whose words are this sentence (quotes, spacing,
        case and end punctuation folded; a slot matches what fills it), else None."""
        want = bare(sentence)
        for k, e in self.entries.items():
            if bare(e.text) == want:
                return k
        for k, e in self.entries.items():
            if _pattern(e.text).fullmatch(want):
                return k
        return None

    def of_kind(self, *kinds: str) -> list[Entry]:
        return [e for e in self.entries.values() if e.kind in kinds]

    def with_entries(self, entries: list[Entry]) -> Book:
        """This book and ``entries``; an entry already here keeps its words."""
        return Book({**{e.key: e for e in entries}, **self.entries})

    def objection_lead(self) -> str:
        """The words every objection entry starts with, up to and past
        ``{item}`` ("Responding Party objects to this {item} on the"): the shape
        a sentence written for one request must take. "" when fewer than two
        objection entries share ``LEAD_WORDS`` or more words with the slot."""
        texts = [e.text.split() for e in self.of_kind("objection")]
        if len(texts) < 2:
            return ""
        lead: list[str] = []
        for words in zip(*texts):
            if len({w.casefold() for w in words}) != 1:
                break
            lead.append(words[0])
        return " ".join(lead) if len(lead) >= LEAD_WORDS and "{item}" in lead else ""

    def cased(self, words: str) -> str:
        """``words`` with each run of capitalised words the book writes
        ("Responding Party", "Request") in the book's casing, a capital first
        letter and a full stop: a rule's stated words made the firm's sentence
        without adding or changing a word."""
        runs = sorted({m.group(0) for e in self.entries.values() for m in _CAPS.finditer(e.text)}, key=len)
        out = norm(words).strip(" \"'")
        for run in reversed(runs):
            out = re.sub(rf"\b{re.escape(run)}\b", run, out, flags=re.I)
        return (out[:1].upper() + out[1:]).rstrip(" .;,:") + "."


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
