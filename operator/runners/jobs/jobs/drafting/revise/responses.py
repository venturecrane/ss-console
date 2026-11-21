"""A filed discovery response read as its responses, by request number: code
decides where a change may land.

A response document read back as markdown blocks (``inplace.blocks`` over
``prior.docx_to_md``: one block per paragraph or table) is segmented by its own
headings. A block that reads ``RESPONSE TO <LABEL> NO. <n>`` opens response
``n``; it runs to the next request heading (``<LABEL> NO. <m>``, with or without
``RESPONSE TO``), a table, or the closing (``DATED``, a verification, a proof of
service). The block of ``<LABEL> NO. <n>`` before it is the request as restated.

Only a response's own blocks are ever edited (:func:`apply`), and only in three
ways a value names: one exact sentence replaced by the firm's phrase, or the
phrase put at the start of the response's first paragraph or the end of its
last. The words written are always the firm's own (``rules.py``), never a
model's. :func:`changed` says which responses two versions of a document differ
in, and whether anything outside every response differs: the rehearsal's
answer-key comparison and the job's own "nothing else changed" proof.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from ..served import SET_TYPES
from . import inplace

#: The request label a response heading carries -> its set type.
LABELS: dict[str, str] = {label: kind for kind, (_name, label) in SET_TYPES.items()}
_LABEL = r"(SPECIAL INTERROGATORY|FORM INTERROGATORY|REQUEST FOR PRODUCTION(?: OF DOCUMENTS)?|REQUEST FOR ADMISSION)"
_HEAD = re.compile(r"^(RESPONSE\s+TO\s+)?" + _LABEL + r"\s+NO\.?\s*(\d+(?:\.\d+)?)\s*[:.]?\s*(.*)$", re.I | re.S)
_CLOSING = re.compile(r"^(DATED|DATE:|RESPECTFULLY|VERIFICATION|PROOF OF SERVICE|DECLARATION OF SERVICE)\b", re.I)
_MARKS = re.compile(r"[*_`]")
#: Where a value puts the firm's phrase.
ACTIONS = ("replace", "start", "end")


class ResponsesUnread(ValueError):
    """The document does not read as numbered responses code can pin."""


def plain(block: str) -> str:
    """A block's words as a reader sees them: no markdown marks, no heading hashes."""
    return " ".join(_MARKS.sub("", re.sub(r"^#{1,6}\s+", "", block)).split())


def _head(block: str) -> tuple[bool, str, str, str] | None:
    """(is a response heading, set type, number, the text after the heading) or None."""
    if block.startswith("|"):
        return None
    m = _HEAD.match(plain(block))
    if not m:
        return None
    label = re.sub(r"\s+OF DOCUMENTS$", "", " ".join(m.group(2).upper().split()))
    return bool(m.group(1)), LABELS[label], m.group(3), m.group(4).strip()


@dataclass
class Response:
    number: str
    set_type: str
    #: The block index of its ``RESPONSE TO`` heading.
    head: int
    #: The block indexes of its own paragraphs, after the heading.
    body: list[int] = field(default_factory=list)
    #: The block indexes of the request as restated before it.
    request: list[int] = field(default_factory=list)
    #: The heading block carries response words of its own after the label.
    inline: bool = False

    @property
    def editable(self) -> list[int]:
        return ([self.head] if self.inline else []) + self.body


@dataclass
class Document:
    blocks: list[str]
    responses: list[Response]

    @property
    def set_type(self) -> str:
        kinds = {r.set_type for r in self.responses}
        return next(iter(kinds)) if len(kinds) == 1 else ""

    @property
    def numbers(self) -> list[str]:
        return [r.number for r in self.responses]

    def by_number(self) -> dict[str, Response]:
        return {r.number: r for r in self.responses}

    def text_of(self, r: Response) -> str:
        return "\n\n".join(self.blocks[i] for i in r.editable)

    def request_of(self, r: Response) -> str:
        return "\n\n".join(self.blocks[i] for i in r.request)

    @property
    def md(self) -> str:
        return "\n\n".join(self.blocks) + "\n"


def _ends(block: str) -> bool:
    return block.startswith("|") or bool(_CLOSING.match(plain(block)))


def read(md: str) -> Document:
    """The document's responses, in order. ``ResponsesUnread`` when none reads,
    when one number repeats, or when the document mixes set types."""
    blocks = inplace.blocks(md)
    heads = [_head(b) for b in blocks]
    out: list[Response] = []
    for i, h in enumerate(heads):
        if h is None or not h[0]:
            continue
        resp = Response(h[2], h[1], i, inline=bool(h[3]))
        floor = (out[-1].body or [out[-1].head])[-1] if out else -1
        j = i - 1
        while j > floor and heads[j] is None and not blocks[j].startswith("|"):
            j -= 1
        if j > floor and heads[j] is not None and not heads[j][0] and heads[j][2] == h[2]:
            resp.request = list(range(j, i))
        k = i + 1
        while k < len(blocks) and heads[k] is None and not _ends(blocks[k]):
            resp.body.append(k)
            k += 1
        out.append(resp)
    if not out:
        raise ResponsesUnread("no block reads as RESPONSE TO <request> NO. <n>")
    nums = [r.number for r in out]
    dup = sorted({n for n in nums if nums.count(n) > 1})
    if dup:
        raise ResponsesUnread(f"response number(s) {', '.join(dup)} appear more than once")
    if len({r.set_type for r in out}) > 1:
        raise ResponsesUnread("the document answers more than one kind of request")
    return Document(blocks, out)


def numbered_as_served(doc: Document, served_numbers: list[int] | None) -> list[str]:
    """Where the responses do not number as served (or, unserved, 1 to N in
    order for a set numbered by whole numbers); empty is a pin."""
    got = doc.numbers
    if served_numbers is not None:
        want = [str(n) for n in served_numbers]
        return (
            []
            if got == want
            else [f"the responses number {', '.join(got)[:120]}; the served set {', '.join(want)[:120]}"]
        )
    if all(n.isdigit() for n in got) and got != [str(n) for n in range(1, len(got) + 1)]:
        return [f"the responses number {', '.join(got)[:160]}, not 1 to {len(got)} in order"]
    return []


@dataclass(frozen=True)
class Value:
    """Where one rule's phrase goes in one response. ``phrase`` is the firm's
    exact words (``rules.py`` resolves the model's choice to them)."""

    request: str
    rule: str
    action: str
    phrase: str
    sentence: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "request": self.request,
            "rule": self.rule,
            "action": self.action,
            "phrase": self.phrase,
            "sentence": self.sentence,
        }


def holds(doc: Document, v: Value) -> list[int]:
    """The editable blocks of ``v``'s response holding its sentence exactly."""
    r = doc.by_number().get(v.request)
    if r is None:
        return []
    return [i for i in r.editable if v.sentence and v.sentence in doc.blocks[i]]


def apply(doc: Document, values: list[Value]) -> tuple[Document, list[str], list[str]]:
    """``values`` made in ``doc``: (the revised document, what was made, what
    could not be). A response that already carries a phrase is left as filed;
    a replace whose sentence is not in exactly one of its blocks is not made
    (the staff may have rewritten it since)."""
    blocks = list(doc.blocks)
    did: list[str] = []
    skipped: list[str] = []
    by = doc.by_number()
    for v in values:
        r = by.get(v.request)
        if r is None or not r.editable:
            skipped.append(f"{v.request} ({v.rule}): the response has no paragraph of its own to carry it")
            continue
        here = "\n\n".join(blocks[i] for i in r.editable)
        if " ".join(v.phrase.split()) in " ".join(plain(here).split()):
            skipped.append(f"{v.request} ({v.rule}): already reads as the rule says; left as filed")
            continue
        if v.action == "replace":
            at = [i for i in r.editable if v.sentence and blocks[i].count(v.sentence) == 1]
            if len(at) != 1:
                skipped.append(f"{v.request} ({v.rule}): the sentence it replaces is not in the response as filed now")
                continue
            blocks[at[0]] = blocks[at[0]].replace(v.sentence, v.phrase, 1)
        elif v.action == "start":
            i = r.body[0] if r.body else None
            if i is None:
                skipped.append(f"{v.request} ({v.rule}): the response has no paragraph to start")
                continue
            blocks[i] = f"{v.phrase} {blocks[i]}"
        else:
            i = r.editable[-1]
            blocks[i] = f"{blocks[i]} {v.phrase}"
        did.append(f"{v.request} ({v.rule}): {v.action}")
    return Document(blocks, [Response(**vars(x)) for x in doc.responses]), did, skipped


def changed(before: str, after: str) -> tuple[list[str], bool]:
    """(the response numbers whose words differ, whether anything outside every
    response differs) between two versions of one document."""
    a, b = read(before), read(after)
    an, bn = a.by_number(), b.by_number()
    if list(an) != list(bn):
        return sorted(set(an) ^ set(bn)), True
    diff = [n for n in an if plain(a.text_of(an[n])) != plain(b.text_of(bn[n]))]

    def outside(d: Document) -> list[str]:
        mine = {i for r in d.responses for i in [r.head, *r.body]}
        return [plain(x) for i, x in enumerate(d.blocks) if i not in mine]

    return diff, outside(a) != outside(b)
