"""The evidence ledger's shapes: the contract every other stage builds on.

Frozen and plain. A ``Candidate`` is what the model proposes from one window
of pages; a ``Fact`` is what code verified (or rejected) and gave an id; a
``Ledger`` is every fact of one job; a ``Token`` is one markup token the
drafter wrote; a ``Span`` is one piece of rendered text code produced from a
token; a ``Finding`` is one thing lint refused.

The model owns only which facts a page carries, the words of a claim, the
prose around tokens, and which ids a sentence uses. Everything a reader could
check against a page (the file, the page, the excerpt's characters, a figure,
a date, a transcript page and line) is set by code in ``verify``.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, replace
from typing import Any, Iterable, Iterator, Literal, Mapping

Kind = Literal["fact", "figure", "date", "quote", "testimony", "inference"]
KINDS: tuple[str, ...] = ("fact", "figure", "date", "quote", "testimony", "inference")

#: What a figure IS, read from the page: what a bill charged, what a payer
#: paid, what an adjustment wrote off, a number opposing counsel put in a
#: question, a number an expert stated, or anything else. Lint refuses a
#: sentence whose wording contradicts the role (a billed figure called paid;
#: a question's number stated as fact), the class behind taxonomy item 1.
Role = Literal["billed", "paid", "adjusted", "opposing_question", "expert_stated", "other"]
ROLES: tuple[str, ...] = ("billed", "paid", "adjusted", "opposing_question", "expert_stated", "other")

Status = Literal["ok", "relocated", "rejected"]
STATUSES: tuple[str, ...] = ("ok", "relocated", "rejected")

#: Units a figure's ``value`` is in: dollars (``value`` is a plain decimal
#: string, "8300.00"), a percentage ("40"), or a bare number ("12").
UNITS: tuple[str, ...] = ("usd", "pct", "number", "")

#: The markup token kinds (``markup``). F cite, Q quote, N figure, D date,
#: SUM / COUNT / SPAN aggregates computed in code, T the charges table, A an
#: attorney marker (on a fact id, or ``?`` with free text).
TOKEN_KINDS: tuple[str, ...] = ("F", "Q", "N", "D", "SUM", "COUNT", "SPAN", "T", "A")


@dataclass(frozen=True)
class Candidate:
    """One fact the model proposed from one window. Nothing here is trusted:
    ``verify`` checks the excerpt against ``page`` of ``file``, parses any
    value from the verified characters, and locates testimony in code."""

    file: str
    page: int
    kind: str
    excerpt: str
    claim: str
    role: str | None = None
    value: str | None = None
    tags: tuple[str, ...] = ()
    tpage: int | None = None
    lines: tuple[int, int] | None = None
    window: str = ""  # the window sha it came from, for the extract cache and the audit trail

    @classmethod
    def from_json(cls, d: Mapping[str, Any], window: str = "") -> "Candidate":
        lines = d.get("lines")
        ln: tuple[int, int] | None = None
        if isinstance(lines, (list, tuple)) and len(lines) == 2 and all(_is_int(x) for x in lines):
            ln = (int(lines[0]), int(lines[1]))
        elif _is_int(lines):
            ln = (int(lines), int(lines))  # type: ignore[arg-type]
        tags = d.get("tags") or ()
        return cls(
            file=str(d.get("file") or "").strip(),
            page=int(d["page"]) if _is_int(d.get("page")) else 0,
            kind=str(d.get("kind") or "fact").strip().lower(),
            excerpt=str(d.get("excerpt") or ""),
            claim=str(d.get("claim") or "").strip(),
            role=(str(d["role"]).strip().lower() if d.get("role") else None),
            value=(str(d["value"]).strip() if d.get("value") not in (None, "") else None),
            tags=tuple(str(t) for t in tags) if isinstance(tags, (list, tuple)) else (),
            tpage=int(d["tpage"]) if _is_int(d.get("tpage")) else None,
            lines=ln,
            window=window,
        )

    def to_json(self) -> dict[str, Any]:
        out = asdict(self)
        out["tags"] = list(self.tags)
        out["lines"] = list(self.lines) if self.lines else None
        return out


@dataclass(frozen=True)
class Fact:
    """One verified (or rejected) fact. ``excerpt`` is the PAGE's characters,
    never the model's: whitespace collapsed, transcript gutters removed,
    widened to the sentence it sits in. ``value`` is parsed by code from those
    characters (a figure's decimal, a date's ISO form). ``status`` is ``ok``
    (on the cited page), ``relocated`` (found on another page of the same
    file, which ``page`` now names) or ``rejected`` (not found; never
    rendered, never in the index)."""

    id: str
    file: str
    page: int
    kind: str
    excerpt: str
    claim: str
    role: str | None = None
    tags: tuple[str, ...] = ()
    tpage: int | None = None
    lines: tuple[int, int] | None = None
    status: str = "ok"
    value: str | None = None
    unit: str = ""
    machine: bool = False
    page_sha: str = ""
    image_reading: str | None = None
    note: str = ""

    @property
    def usable(self) -> bool:
        return self.status in ("ok", "relocated")

    def to_json(self) -> dict[str, Any]:
        out = asdict(self)
        out["tags"] = list(self.tags)
        out["lines"] = list(self.lines) if self.lines else None
        return out

    @classmethod
    def from_json(cls, d: Mapping[str, Any]) -> "Fact":
        lines = d.get("lines")
        return cls(
            id=str(d["id"]),
            file=str(d["file"]),
            page=int(d["page"]),
            kind=str(d["kind"]),
            excerpt=str(d.get("excerpt") or ""),
            claim=str(d.get("claim") or ""),
            role=d.get("role"),
            tags=tuple(d.get("tags") or ()),
            tpage=d.get("tpage"),
            lines=(int(lines[0]), int(lines[1])) if lines else None,
            status=str(d.get("status") or "ok"),
            value=d.get("value"),
            unit=str(d.get("unit") or ""),
            machine=bool(d.get("machine")),
            page_sha=str(d.get("page_sha") or ""),
            image_reading=d.get("image_reading"),
            note=str(d.get("note") or ""),
        )

    def with_(self, **kw: Any) -> "Fact":
        return replace(self, **kw)


@dataclass(frozen=True)
class Ledger:
    """Every fact of one job, rejected ones included (so lint can name a
    rejected id as rejected rather than unknown). Ids are unique."""

    facts: tuple[Fact, ...] = ()
    _by_id: dict[str, Fact] = field(default_factory=dict, init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        by: dict[str, Fact] = {}
        for f in self.facts:
            if f.id in by:
                raise ValueError(f"duplicate fact id {f.id}")
            by[f.id] = f
        object.__setattr__(self, "_by_id", by)

    def __iter__(self) -> Iterator[Fact]:
        return iter(self.facts)

    def __len__(self) -> int:
        return len(self.facts)

    def get(self, fid: str) -> Fact | None:
        return self._by_id.get(fid)

    def usable(self) -> list[Fact]:
        return [f for f in self.facts if f.usable]

    def rejected(self) -> list[Fact]:
        return [f for f in self.facts if not f.usable]

    def replace_facts(self, facts: Iterable[Fact]) -> "Ledger":
        return Ledger(tuple(facts))

    def to_jsonl(self) -> str:
        return "".join(json.dumps(f.to_json(), sort_keys=True) + "\n" for f in self.facts)

    @classmethod
    def from_jsonl(cls, text: str) -> "Ledger":
        return cls(tuple(Fact.from_json(json.loads(ln)) for ln in text.splitlines() if ln.strip()))


@dataclass(frozen=True)
class DocPages:
    """One corpus document as page text under its ``[p.N]`` markers."""

    name: str
    pages: Mapping[int, str]
    machine: frozenset[int] = frozenset()
    source_path: str | None = None


@dataclass(frozen=True)
class Token:
    """One markup token: ``{{KIND:args}}`` at ``start``..``end`` of the
    markup text. ``ids`` are fact ids (F/Q/N/D/SUM/COUNT/SPAN/A);
    ``text`` is the free part after ``|`` (a Q fragment, an A:? note, a
    SPAN unit); ``name`` is T's table name."""

    kind: str
    ids: tuple[str, ...]
    start: int
    end: int
    raw: str
    text: str | None = None
    name: str | None = None


@dataclass(frozen=True)
class Span:
    """One piece of rendered text code produced from one token. ``start`` /
    ``end`` index the rendered text; ``text`` is exactly what was written
    there; ``ids`` are the facts it traces to."""

    kind: str
    ids: tuple[str, ...]
    start: int
    end: int
    text: str
    raw: str

    def to_json(self) -> dict[str, Any]:
        out = asdict(self)
        out["ids"] = list(self.ids)
        return out

    @classmethod
    def from_json(cls, d: Mapping[str, Any]) -> "Span":
        return cls(str(d["kind"]), tuple(d["ids"]), int(d["start"]), int(d["end"]), str(d["text"]), str(d["raw"]))


@dataclass(frozen=True)
class Finding:
    """One thing lint (or render's span assertion) refused. ``code`` is the
    class (``hand_quote``, ``hand_money``, ``unknown_id`` ...), ``start`` /
    ``end`` index the text it was found in, ``sentence`` is the sentence
    around it, for a scoped repair."""

    code: str
    message: str
    start: int
    end: int
    snippet: str
    sentence: str = ""

    def line(self) -> str:
        return f"[{self.code}] {self.message}: {self.snippet!r}"


def _is_int(x: Any) -> bool:
    if isinstance(x, bool):
        return False
    if isinstance(x, int):
        return True
    return isinstance(x, str) and x.strip().isdigit()
