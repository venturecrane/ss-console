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
    numbers: tuple[int, ...] = ()
    doc_type: str = ""
    act: str = ""
    keys: tuple[str, ...] = ()


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


@dataclass(frozen=True)
class Part:
    """One sentence of a response: its role (transition, objection, disposition,
    other), the phrase-book key it matches, and the ground it states."""

    text: str
    role: str
    key: str = ""
    ground: str = ""


@dataclass(frozen=True)
class PartsDoc:
    """A response document read into its sentences, per item label."""

    items: dict[str, tuple[Part, ...]] = field(default_factory=dict)
