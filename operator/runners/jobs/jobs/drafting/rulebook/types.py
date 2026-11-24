"""The values the firm-rules stage takes and returns (design C, "Interfaces").

Every field is a value code can check: a model may fill some of them, but code
verifies each against the rule's own words or the document before it is used.
Nothing here reads a file or calls a model.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Literal

#: The two places the stage runs: a document drafted fresh, or a filed one revised.
Mode = Literal["first_draft", "revise"]
MODES: tuple[str, ...] = ("first_draft", "revise")

#: An ``ask(stage, system, user, max_tokens) -> text`` call through the job's doorway.
Ask = Callable[[str, str, str, int], str]


@dataclass(frozen=True)
class Instruction:
    """One thing a document must do, and where it was stated: a recorded rule
    (``rule:<id>``), the request email, or the other side's letter. ``quote`` is
    a span of that source; ``numbers`` the request numbers it names, if any."""

    source: str
    quote: str
    numbers: tuple[str, ...] = ()
    doc_type: str = ""
    act: str = ""
    keys: tuple[str, ...] = ()
    #: Objection grounds named in the quote that the book has no sentence for
    #: (written for the request, ``written.py``), folded.
    grounds: tuple[str, ...] = ()


@dataclass(frozen=True)
class StageJob:
    """The job around the documents: its data directory, the firm's inputs,
    the model call, the log, and the request's own words."""

    data: Path
    firm: Any
    ask: Ask | None
    log: Callable[[str], None]
    request_text: str
    mode: Mode


@dataclass(frozen=True)
class StageDoc:
    """One document the stage checks: its markdown, its type (the classes'
    document types, ``rfp_response`` and the rest), whether it is an amended
    response, the kind hint a bare heading needs, and the instructions that
    apply to it alone. ``fix``: code may write into it; else check only."""

    key: str
    md: str
    doc_type: str
    amended: bool = False
    hint: str | None = None
    instructions: tuple[Instruction, ...] = ()
    fix: bool = True


@dataclass(frozen=True)
class Applied:
    """A clause applied to one item, by the rule (or instruction) it came from."""

    source: str
    item: str
    key: str = ""


@dataclass(frozen=True)
class Left:
    """A clause not applied to one item, and the reason code from the endings
    table (``drafting/endings.py``) the notes line is written from."""

    source: str
    item: str
    reason: str


@dataclass(frozen=True)
class Finding:
    """What a check-only document (a propounded set) must change, as a closed
    code: the caller's repair acts on it; this stage writes no request."""

    source: str
    code: str
    item: str = ""
    detail: str = ""


@dataclass(frozen=True)
class StageResult:
    md: str
    applied: tuple[Applied, ...] = ()
    left: tuple[Left, ...] = ()
    findings: tuple[Finding, ...] = ()
    notes: tuple[str, ...] = ()


@dataclass(frozen=True)
class Fresh:
    """Whether the rule store moved since the job froze it: the rule ids changed,
    added and removed."""

    changed: tuple[str, ...] = ()
    added: tuple[str, ...] = ()
    removed: tuple[str, ...] = ()

    @property
    def moved(self) -> bool:
        return bool(self.changed or self.added or self.removed)


#: The document types a clause names (design C), and the set type each response
#: document answers.
DOC_TYPES: tuple[str, ...] = (
    "rfp_response", "srog_response", "frog_response", "rfa_response", "index", "rfp_set", "srog_set", "rfa_set",
)  # fmt: skip
#: The propounded set each set type is, for clauses that check a set (``sets_check``).
SET_OF: dict[str, str] = {
    "requests_for_production": "rfp_set",
    "special_interrogatories": "srog_set",
    "requests_for_admission": "rfa_set",
}
SET_DOC: dict[str, str] = {
    "requests_for_production": "rfp_response",
    "special_interrogatories": "srog_response",
    "form_interrogatories": "frog_response",
    "requests_for_admission": "rfa_response",
}
#: The word a phrase-book ``{item}`` slot takes in each response document.
ITEM_WORD: dict[str, str] = {
    "rfp_response": "Request",
    "srog_response": "Interrogatory",
    "frog_response": "Interrogatory",
    "rfa_response": "Request",
}
#: What a clause asks of a response. ``other`` is composed under the rule's own
#: words and never checked or made by code.
ACTS: tuple[str, ...] = ("add_objection", "use_phrase", "remove_part", "count", "forbid_topic", "other")
#: The acts code makes.
MADE: tuple[str, ...] = ("add_objection", "use_phrase", "remove_part")
#: The acts code checks a propounded set for, never writes: a finding goes to the set lane's repair.
CHECKED: tuple[str, ...] = ("count", "forbid_topic")
#: Sentence roles.
ROLES: tuple[str, ...] = ("transition", "objection", "disposition", "other")


@dataclass(frozen=True)
class Clause:
    """One thing a rule asks, read from its words and verified against them by
    code (``reading.check``). ``standing`` false: said of one matter's response
    (a segment the rule labels "Response #9:"), never applied by this stage.
    ``when``: ``judged`` per item by the grid, ``always`` for every item of its
    document types, ``numbers`` for the items in ``numbers`` only."""

    rule: str
    n: int
    quote: str
    standing: bool
    doc_types: tuple[str, ...]
    act: str
    keys: tuple[str, ...] = ()
    grounds: tuple[str, ...] = ()
    when: str = "judged"
    numbers: tuple[str, ...] = ()

    @property
    def id(self) -> str:
        return f"{self.rule}#{self.n}"

    def to_dict(self) -> dict[str, Any]:
        return {**vars(self), "keys": list(self.keys), "grounds": list(self.grounds),
                "doc_types": list(self.doc_types), "numbers": list(self.numbers)}  # fmt: skip

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Clause":
        return cls(**{**d, **{k: tuple(d.get(k) or ()) for k in ("keys", "grounds", "doc_types", "numbers")}})


@dataclass(frozen=True)
class Cell:
    """The grid's answer for one item and one judged clause, checked by code:
    ``apply`` with the book keys (or, with no sentence in the book, the
    grounds), the response's own sentences it replaces or removes, and the
    exhibits by title; ``complies``; ``conflict``."""

    item: str
    clause: str
    state: str
    keys: tuple[str, ...] = ()
    grounds: tuple[str, ...] = ()
    sentences: tuple[str, ...] = ()
    exhibits: tuple[str, ...] = ()


@dataclass(frozen=True)
class Part:
    """One sentence of a response: its role (transition, objection, disposition,
    other), the phrase-book key it matches, and the ground it states. ``block``
    and ``at``: where it sits in the document's blocks (``responses.read``)."""

    text: str
    role: str
    key: str = ""
    ground: str = ""
    block: int = -1
    at: int = -1


@dataclass(frozen=True)
class PartsDoc:
    """A response document read into its sentences, per item label."""

    items: dict[str, tuple[Part, ...]] = field(default_factory=dict)
