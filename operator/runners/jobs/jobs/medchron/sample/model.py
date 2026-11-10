"""The expanded page plan: what every page says and what every chronology
paragraph cites. Shared by the generator, the answer key and the tests, so the
three can never disagree about which page a fact is on."""

from __future__ import annotations

import random
import re
from dataclasses import dataclass, field
from typing import Any

#: On every page of every generated record (the fixture convention in
#: ``bin/lib/seed_fixtures``): nothing here can be mistaken for a real record.
SEED_MARK = "[SEED] Fictional record for demonstration. Not a real patient."


@dataclass(frozen=True)
class Provider:
    key: str
    facility: str
    address: str
    phone: str
    fax: str
    #: Regex the demo firm config aliases to ``facility`` (clinician surnames).
    match: str
    custodian: str
    #: How the facility prints its name on a bill, when it differs.
    bill_name: str
    style: dict[str, Any] = field(default_factory=dict)


@dataclass
class Page:
    doc_id: str
    kind: str
    title: str
    lines: list[str]
    number: int = 0
    pid: str = ""
    date: str = ""  # ISO, printed in the header when set
    scan: bool | None = None  # None: decided by the matter's scan rate
    #: Indices into ``lines`` written by hand (rendered as handwriting).
    hand: list[int] = field(default_factory=list)
    illegible: bool = False
    #: The patient the page is about, when it is NOT the matter's patient.
    other_patient: str = ""
    #: The billing transcription of this page, when it is a billing page.
    bill: dict[str, Any] | None = None


@dataclass
class Document:
    id: str
    name: str
    provider: str
    pages: list[Page]
    fax: dict[str, Any] | None = None
    resend_of: str = ""
    seq: int = 0

    @property
    def file_name(self) -> str:
        return self.name + ".pdf"

    @property
    def file_id(self) -> str:
        """Stable, uuid-shaped, and obviously synthetic."""
        return f"5eed0000-0000-4000-8000-{self.seq:012d}"


@dataclass(frozen=True)
class Para:
    heading: str
    text: str
    doc: str
    page: int
    evidence: str


@dataclass
class Encounter:
    date: str  # ISO
    provider: str
    signer: str
    icd: list[str]
    paras: list[Para]


@dataclass
class Plan:
    matter: dict[str, Any]
    patient: dict[str, Any]
    providers: dict[str, Provider]
    documents: list[Document]
    encounters: list[Encounter]
    conflicts: list[str]
    icd: dict[str, str]
    defects: dict[str, Any]
    headings: list[str]

    def doc(self, doc_id: str) -> Document:
        for d in self.documents:
            if d.id == doc_id:
                return d
        raise KeyError(doc_id)

    def total_pages(self) -> int:
        return sum(len(d.pages) for d in self.documents)


def rng(seed: Any, *parts: Any) -> random.Random:
    """A generator keyed by the matter seed and a purpose. String seeds hash
    through sha512 in CPython, so the stream is stable across runs."""
    return random.Random(":".join(str(p) for p in (seed, *parts)))


def us_date(iso: str) -> str:
    return f"{iso[5:7]}/{iso[8:10]}/{iso[:4]}" if iso else ""


def norm(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().lower()
