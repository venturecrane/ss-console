"""One model of a PI discovery paper (docs/plans/pi-document-model/design.md).

Every paper is the same ordered regions (caption, title, identification,
front, definitions, instructions, numbered items, closing, then attachments:
declaration, verification, index, service, notes); only an item's shape
differs between a set (the request) and a response (request, objections,
answer, cites). Every field records the ids of the Word blocks it came from
(``blocks.Block.id``), and every body block belongs to exactly one field, so a
writer can go back to the very paragraph a value was read from.

Slice 1 is shadow only: nothing on a seat reads this model yet.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Block:
    """One non-empty body paragraph, table row, or text-box paragraph, as Word
    stores it. ``id`` locates the element: ``p<i>`` the i-th body child,
    ``t<i>.r<j>`` row j of the table at body child i, ``p<i>.x<k>`` the k-th
    text-box paragraph anchored in paragraph i."""

    id: str
    text: str
    bold: bool = False
    caps: bool = False
    style: str = "Normal"
    #: The number Word renders for an auto-numbered paragraph ("1.", "(a)",
    #: "SPECIAL INTERROGATORY NO. 3:"), computed from numbering.xml; None when
    #: the paragraph carries no numbering or only a bullet.
    numbered: str | None = None
    num_id: str | None = None
    num_level: int | None = None
    in_table: bool = False
    align: str = "left"
    in_textbox: bool = False
    #: The style's outline level or heading level; 0 for a body paragraph.
    heading: int = 0


@dataclass
class Region:
    """A run of blocks with one role."""

    name: str
    ids: list[str] = field(default_factory=list)


@dataclass
class Definition:
    term: str
    plural: str | None
    #: The definition's number when the firm numbers its definitions (in the
    #: text or by Word), else None.
    number: int | None
    ids: list[str] = field(default_factory=list)
    #: True when the comma or period sat inside the closing quote ("TERM,").
    punct_inside: bool = False
    #: The quote characters around the term: '"' or '“”'.
    quote: str = '"'


@dataclass
class Item:
    number: int
    #: The label words as written ("SPECIAL INTERROGATORY"), or "" for an item
    #: numbered by Word with no label in its text.
    label: str
    ids: list[str] = field(default_factory=list)
    request: list[str] = field(default_factory=list)
    objections: list[str] = field(default_factory=list)
    answer: list[str] = field(default_factory=list)
    cites: list[str] = field(default_factory=list)
    #: The response heading block ("RESPONSE TO ... NO. n:") on a response.
    response_head: str | None = None
    parent: int | None = None


@dataclass
class CountMention:
    value: int
    id: str


@dataclass
class ReasonList:
    """One list of item numbers in a declaration, under a statutory reason
    letter ("(A)") or under none (the whole set)."""

    reason: str | None
    numbers: list[int]
    #: The noun before the list as written: "No.", "Nos.", "Numbers", ...
    noun: str
    id: str


@dataclass
class Declaration:
    count_mentions: list[CountMention] = field(default_factory=list)
    reason_lists: list[ReasonList] = field(default_factory=list)
    #: The numbers of the statutory prior-sets statement ("previously
    #: propounded a total of N ..., of which M were not form"), in order.
    prior_counts: list[CountMention] = field(default_factory=list)
    ids: list[str] = field(default_factory=list)


@dataclass
class Paper:
    type: str
    caption: Region
    title: Region
    ident: Region
    front: Region
    definitions_region: Region
    definitions: list[Definition]
    instructions: Region
    items: list[Item]
    closing: Region
    declaration: Declaration | None
    verification: Region
    index: Region
    service: Region
    notes: Region
    blocks: list[Block]
    #: Which generic detector read each region ({"title": "bold", "items": "label"}).
    detectors: dict[str, str] = field(default_factory=dict)

    def assignment(self) -> list[tuple[str, str]]:
        """Every (block id, region) this paper assigns; an id may appear more
        than once only if the reader is wrong, which the invariants catch."""
        out: list[tuple[str, str]] = []
        for reg in (self.caption, self.title, self.ident, self.front, self.definitions_region, self.instructions):
            out += [(i, reg.name) for i in reg.ids]
        for it in self.items:
            out += [(i, f"item {it.number}") for i in it.ids]
        out += [(i, "closing") for i in self.closing.ids]
        if self.declaration is not None:
            out += [(i, "declaration") for i in self.declaration.ids]
        for reg in (self.verification, self.index, self.service, self.notes):
            out += [(i, reg.name) for i in reg.ids]
        return out


@dataclass(frozen=True)
class Unread:
    """The reader could not read the paper: which region, the block where it
    failed, and why. The job holds for SMD; nothing is filed."""

    region: str
    block_id: str | None
    why: str
