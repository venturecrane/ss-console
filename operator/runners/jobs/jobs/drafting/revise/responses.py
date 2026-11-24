"""A filed discovery response read as its responses, by request number: code
decides where a change may land.

A response document read back as markdown blocks (``inplace.blocks`` over
``prior.docx_to_md``: one block per paragraph or table) is segmented by its own
headings. A block that reads ``RESPONSE TO <LABEL> NO. <n>`` opens response
``n``; it runs to the next request heading (``<LABEL> NO. <m>``, with or without
``RESPONSE TO``), a table, the closing (``DATED``, a verification, a proof of
service), the Index of Exhibits, or the attorney's notes. The block of
``<LABEL> NO. <n>`` before it is the request as restated.

A bare ``INTERROGATORY NO. <n>`` heading (2026-11-23: a special interrogatory
document headed that way failed the whole job) takes its kind from the
document's own title ("SPECIAL INTERROGATORIES" or "FORM INTERROGATORIES",
whichever its front matter names first), else from the kind the document was
delivered as, else a dotted number is a form interrogatory; otherwise the
document does not read (:class:`ResponsesUnread`), never a guess.

The Index of Exhibits is its own region (:class:`Index`: each exhibit's letter,
title and lines), never part of the last response. The attorney's notes are no
response either: a heading quoted in them opens nothing.

Only a response's own blocks are ever edited (:func:`apply`), and only in three
ways a value names: one exact sentence replaced by the firm's phrase, or the
phrase put at the start of the response's first paragraph or the end of its
last. The words written are always the firm's own (``rules.py``), never a
model's; where the firm's phrase ends at "Exhibit", the letter is filled by
code from the document's own index, by the exhibit's title (:func:`fill`), and a
title the index does not hold exactly once leaves the response as filed.
:func:`changed` says which responses two versions of a document differ in, and
whether anything outside every response differs: the rehearsal's answer-key
comparison and the job's own "nothing else changed" proof.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from ..served import SET_TYPES
from . import inplace

#: The request label a response heading carries -> its set type.
LABELS: dict[str, str] = {label: kind for kind, (_name, label) in SET_TYPES.items()}
_LABEL = (
    r"(SPECIAL INTERROGATORY|FORM INTERROGATORY|REQUEST FOR PRODUCTION(?: OF DOCUMENTS)?|REQUEST FOR ADMISSION"
    r"|INTERROGATORY)"
)
#: A heading that names no kind of interrogatory.
BARE = "INTERROGATORY"
_HEAD = re.compile(r"^(RESPONSE\s+TO\s+)?" + _LABEL + r"\s+NO\.?\s*(\d+(?:\.\d+)?)\s*[:.]?\s*(.*)$", re.I | re.S)
_CLOSING = re.compile(r"^(DATED|DATE:|RESPECTFULLY|VERIFICATION|PROOF OF SERVICE|DECLARATION OF SERVICE)\b", re.I)
_MARKS = re.compile(r"[*_`]")
#: The index and notes headings: the WHOLE paragraph is the heading. A body
#: sentence that starts with the same words ("Attorney's notes are protected
#: ...") is a response's words, never the end of the responses.
_INDEX = re.compile(r"^INDEX\s+OF\s+EXHIBITS\s*:?\s*$", re.I)
_NOTES = re.compile(
    r"^(?:=+\s*)?(?:NOTES\s+FOR\s+THE\s+ATTORNEY|ATTORNEY(?:'S|\u2019S)?\s+NOTES)\s*:?\s*(?:=+)?\s*$", re.I
)
#: An exhibit's line: "EXHIBIT B: TITLE", "EXHIBIT B - TITLE", "EXHIBIT B TITLE",
#: "EXHIBIT AA: TITLE". The letters are capitals as written.
_EXHIBIT = re.compile(r"^(?i:EXHIBIT)\s+([A-Z]{1,2})\b\s*[:.\-\u2013\u2014]?\s*(.*)$")
#: Where the firm's phrase leaves the exhibit to be named: "Exhibit" at the
#: phrase's end, or before a blank ("Exhibit ___", "Exhibit [letter]").
_SLOT = re.compile(r"\b(Exhibits?)(?:\s+(?:_+|\[[^\]]*\]|\{[^}]*\}|<[^>]*>))?(?=\s*[.;,]?\s*$)")
_TITLE_FORM = re.compile(r"\b(SPECIAL(?:LY\s+PREPARED)?|FORM)\s+INTERROGATOR", re.I)
#: A bare interrogatory heading is the whole paragraph: a sentence that starts
#: "Interrogatory No. 4 asks the same" is a response's words.
_BARE_ALONE = re.compile(r"^INTERROGATORY\s+NO\.?\s*\d+(?:\.\d+)?\s*[:.]?\s*$", re.I)
#: Where a value puts the firm's phrase.
ACTIONS = ("replace", "start", "end")


class ResponsesUnread(ValueError):
    """The document does not read as numbered responses code can pin."""


def plain(block: str) -> str:
    """A block's words as a reader sees them: no markdown marks, no heading hashes."""
    return " ".join(_MARKS.sub("", re.sub(r"^#{1,6}\s+", "", block)).split())


def _head(block: str) -> tuple[bool, str, str, str] | None:
    """(is a response heading, set type or ``BARE``, number, the text after the heading) or None."""
    if block.startswith("|"):
        return None
    m = _HEAD.match(plain(block))
    if not m:
        return None
    label = re.sub(r"\s+OF DOCUMENTS$", "", " ".join(m.group(2).upper().split()))
    if label == BARE and not m.group(1) and not _BARE_ALONE.match(plain(block)):
        return None
    return bool(m.group(1)), BARE if label == BARE else LABELS.get(label, ""), m.group(3), m.group(4).strip()


def _title_kind(blocks: list[str], heads: list[Any]) -> str:
    """The interrogatory kind the document's TITLE names, or "": the first
    front-matter paragraph written in capitals that names one. Prose in the
    front matter ("like Plaintiff's responses to Form Interrogatories") is not
    the title and is not read."""
    for b, h in zip(blocks, heads):
        if h is not None:
            return ""
        p = plain(b)
        m = _TITLE_FORM.search(p)
        if m and p == p.upper():
            return LABELS["FORM INTERROGATORY" if m.group(1).upper() == "FORM" else "SPECIAL INTERROGATORY"]
    return ""


def _resolve(blocks: list[str], heads: list[Any], hint: str | None) -> list[Any]:
    """Every bare interrogatory heading given its kind: the kind it was
    delivered as, then the title, then a dotted number means form.
    ``ResponsesUnread`` when none says."""
    if not any(h is not None and h[1] in (BARE, "") for h in heads):
        return heads
    kinds = {LABELS["SPECIAL INTERROGATORY"], LABELS["FORM INTERROGATORY"]}
    kind = (hint if hint in kinds else "") or _title_kind(blocks, heads)
    out: list[Any] = []
    for h in heads:
        if h is not None and h[1] == "":
            raise ResponsesUnread(f"a heading names a kind of request this reader does not know (no. {h[2]})")
        if h is not None and h[1] == BARE:
            got = kind or (LABELS["FORM INTERROGATORY"] if "." in h[2] else "")
            if not got:
                raise ResponsesUnread(
                    f"INTERROGATORY NO. {h[2]} does not say special or form, and neither does the document's title"
                )
            h = (h[0], got, h[2], h[3])
        out.append(h)
    return out


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
class Exhibit:
    letter: str
    title: str
    #: The block index of its ``EXHIBIT <letter>:`` line.
    head: int
    #: The block indexes of the lines listed under it.
    lines: list[int] = field(default_factory=list)


@dataclass
class Index:
    #: The block index of the ``INDEX OF EXHIBITS`` heading (-1: an index file
    #: whose first block is already an exhibit line).
    head: int
    #: Every block of the index after its heading.
    blocks: list[int] = field(default_factory=list)
    exhibits: list[Exhibit] = field(default_factory=list)


@dataclass
class Document:
    blocks: list[str]
    responses: list[Response]
    index: Index | None = None
    #: The block index of the attorney's notes heading, if the document has one.
    notes: int | None = None

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

    def index_lines(self) -> list[str]:
        """The index's lines as a reader sees them, in order; [] without an index."""
        return [plain(self.blocks[i]) for i in self.index.blocks] if self.index else []

    def exhibit_titles(self) -> list[str]:
        return [e.title for e in self.index.exhibits] if self.index else []

    @property
    def is_index(self) -> bool:
        """A file that is only an Index of Exhibits: no response, an index region."""
        return not self.responses and self.index is not None


def _ends(block: str) -> bool:
    return block.startswith("|") or bool(_CLOSING.match(plain(block)))


def _after_last(blocks: list[str], heads: list[Any], pat: re.Pattern[str]) -> int | None:
    """The first heading ``pat`` names after the last response heading: an index
    or notes heading before a response is never the end of the responses."""
    last = max((i for i, h in enumerate(heads) if h is not None and h[0]), default=-1)
    return next((i for i in range(last + 1, len(blocks)) if pat.match(plain(blocks[i]))), None)


def _index(blocks: list[str], start: int | None) -> Index | None:
    """The Index of Exhibits: from its heading to the closing or the notes."""
    if start is None:
        return None
    idx = Index(start)
    for i in range(start + 1, len(blocks)):
        p = plain(blocks[i])
        if _CLOSING.match(p) or _NOTES.match(p):
            break
        idx.blocks.append(i)
        m = _EXHIBIT.match(p)
        if m:
            idx.exhibits.append(Exhibit(m.group(1), " ".join(m.group(2).split()), i))
        elif idx.exhibits:
            idx.exhibits[-1].lines.append(i)
    return idx


def _index_file_start(blocks: list[str]) -> int | None:
    """Where an index-only file's index begins: its heading as a whole paragraph,
    else a title in capitals that names it ("PLAINTIFF'S INDEX OF EXHIBITS"),
    else just before its first ``EXHIBIT <letter>`` line (a file headed only by
    the caption). None when the file lists no exhibit."""
    first = next((i for i, b in enumerate(blocks) if _EXHIBIT.match(plain(b))), None)
    if first is None:
        return None
    for i in range(first):
        p = plain(blocks[i])
        if _INDEX.match(p) or (p == p.upper() and re.search(r"\bINDEX\s+OF\s+EXHIBITS\b", p)):
            return i
    return first - 1


def _quiet(heads: list[Any], index: Index | None) -> list[Any]:
    """The headings with the request headings listed in the index removed. Both
    regions start after the last response heading, so none is ever removed."""
    out = list(heads)
    for i in index.blocks if index else ():
        if out[i] is not None and not out[i][0]:
            out[i] = None
    return out


def read(md: str, set_type: str | None = None) -> Document:
    """The document's responses, in order, its index and its notes. ``set_type``
    is the kind it was delivered as, read only for a bare interrogatory heading
    its title does not settle. A file with no response heading that lists
    exhibits is an index file (``Document.is_index``: no responses, its index).
    ``ResponsesUnread`` when nothing reads, when one number repeats, or when the
    document mixes set types."""
    blocks = inplace.blocks(md)
    heads = [_head(b) for b in blocks]
    notes = _after_last(blocks, heads, _NOTES)
    index = _index(blocks, _after_last(blocks, heads, _INDEX))
    heads = _resolve(blocks, _quiet(heads, index), set_type)
    stops = {i for i in (notes, index.head if index else None) if i is not None}
    out: list[Response] = []
    for i, h in enumerate(heads):
        if h is None or not h[0]:
            continue
        resp = Response(h[2], h[1], i, inline=bool(h[3]))
        floor = (out[-1].body or [out[-1].head])[-1] if out else -1
        j = i - 1
        while j > floor and heads[j] is None and not blocks[j].startswith("|"):
            j -= 1
        prev = heads[j] if j > floor else None
        if prev is not None and not prev[0] and prev[2] == h[2]:
            resp.request = list(range(j, i))
        k = i + 1
        while k < len(blocks) and heads[k] is None and k not in stops and not _ends(blocks[k]):
            resp.body.append(k)
            k += 1
        out.append(resp)
    if not out:
        # 2026-11-23: a request delivered its Index of Exhibits as a file of its
        # own, and reading it as a response document failed the whole request.
        index = index or _index(blocks, _index_file_start(blocks))
        if index is not None and index.exhibits:
            return Document(blocks, [], index, notes)
        raise ResponsesUnread("no block reads as RESPONSE TO <request> NO. <n>")
    nums = [r.number for r in out]
    dup = sorted({n for n in nums if nums.count(n) > 1})
    if dup:
        raise ResponsesUnread(f"response number(s) {', '.join(dup)} appear more than once")
    if len({r.set_type for r in out}) > 1:
        raise ResponsesUnread("the document answers more than one kind of request")
    return Document(blocks, out, index, notes)


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
    exact words (``rules.py`` resolves the model's choice to them). ``exhibits``
    are the titles, as the document's index lists them, of the exhibits a
    phrase ending at "Exhibit" points to: code turns them into letters (:func:`fill`)."""

    request: str
    rule: str
    action: str
    phrase: str
    sentence: str = ""
    exhibits: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "exhibits", tuple(str(t) for t in self.exhibits))

    def to_dict(self) -> dict[str, Any]:
        return {
            "request": self.request,
            "rule": self.rule,
            "action": self.action,
            "phrase": self.phrase,
            "sentence": self.sentence,
            "exhibits": list(self.exhibits),
        }


#: Why a value was not made because its exhibit could not be named by code.
EXHIBIT_UNRESOLVED = "exhibit_unresolved"


def has_slot(phrase: str) -> bool:
    """The firm's phrase leaves the exhibit letter to be filled."""
    return bool(_SLOT.search(phrase))


def title_key(title: str) -> str:
    """An exhibit title as two are compared: case, spacing and end punctuation folded."""
    return " ".join(re.sub(r"[^\w\s]", " ", plain(title)).split()).casefold()


def _letters(letters: list[str]) -> str:
    """ "A", "A and B", "A, B and C": in index order (A..Z, then AA..)."""
    letters = sorted(letters, key=lambda x: (len(x), x))
    return letters[0] if len(letters) == 1 else ", ".join(letters[:-1]) + " and " + letters[-1]


def _word(written: str, many: bool) -> str:
    """The phrase's own word for "Exhibit", made plural for two or more."""
    stem = written[:-1] if written.lower().endswith("s") else written
    return stem + ("S" if stem.isupper() else "s") if many else stem


def fill(doc: Document, phrase: str, titles: tuple[str, ...]) -> tuple[str, str]:
    """(the phrase with its exhibit letters, "") or ("", why not). Each title must
    name exactly one exhibit in THIS document's index; a letter is never taken
    from anywhere but that index. A phrase with no slot is returned as is."""
    m = _SLOT.search(phrase)
    if not m:
        return phrase, ""
    if not titles:
        return "", "the firm's phrase names an exhibit and no exhibit was named for it"
    exhibits = doc.index.exhibits if doc.index else []
    letters: list[str] = []
    for t in titles:
        hits = [e.letter for e in exhibits if title_key(e.title) == title_key(t)]
        if len(hits) != 1:
            where = "is not in" if not hits else "is in more than once in"
            return "", f"the exhibit {t[:60]!r} {where} the document's index"
        letters += [x for x in hits if x not in letters]
    rest = phrase[m.end() :]
    out = f"{phrase[: m.start()]}{_word(m.group(1), len(letters) > 1)} {_letters(letters)}{rest}"
    return (out if rest.strip() else out.rstrip() + "."), ""


def _bare(phrase: str) -> str:
    """The phrase as the already-says-this check reads it: end punctuation off,
    so "... Exhibit B" in a response is the phrase "... Exhibit B." already."""
    return " ".join(phrase.split()).rstrip(" .;,")


def holds(doc: Document, v: Value) -> list[int]:
    """The editable blocks of ``v``'s response holding its sentence exactly."""
    r = doc.by_number().get(v.request)
    if r is None:
        return []
    return [i for i in r.editable if v.sentence and v.sentence in doc.blocks[i]]


def _place(blocks: list[str], r: Response, v: Value, phrase: str) -> str:
    """``phrase`` placed in ``blocks`` as ``v`` says; "" when placed, else why not."""
    if v.action == "replace":
        at = [i for i in r.editable if v.sentence and blocks[i].count(v.sentence) == 1]
        if len(at) != 1:
            return "the sentence it replaces is not in the response as filed now"
        after = blocks[at[0]].split(v.sentence, 1)[1][:1]
        if after and after in ".;," and phrase.endswith("."):
            phrase = phrase[:-1]  # the sentence's own end punctuation stays
        blocks[at[0]] = blocks[at[0]].replace(v.sentence, phrase, 1)
    elif v.action == "start":
        if not r.body:
            return "the response has no paragraph to start"
        blocks[r.body[0]] = f"{phrase} {blocks[r.body[0]]}"
    else:
        i = r.editable[-1]
        blocks[i] = f"{blocks[i]} {phrase}"
    return ""


def apply(doc: Document, values: list[Value]) -> tuple[Document, list[str], list[str]]:
    """``values`` made in ``doc``: (the revised document, what was made, what
    could not be). A response that already carries a phrase is left as filed;
    a replace whose sentence is not in exactly one of its blocks is not made
    (the staff may have rewritten it since); a phrase whose exhibit the index
    does not name exactly once is not made (``exhibit_unresolved``)."""
    blocks = list(doc.blocks)
    did: list[str] = []
    skipped: list[str] = []
    by = doc.by_number()
    for v in values:
        r = by.get(v.request)
        if r is None or not r.editable:
            skipped.append(f"{v.request} ({v.rule}): the response has no paragraph of its own to carry it")
            continue
        phrase, why = fill(doc, v.phrase, v.exhibits)
        if why:
            skipped.append(f"{v.request} ({v.rule}): {EXHIBIT_UNRESOLVED}: {why}; left as filed")
            continue
        here = "\n\n".join(blocks[i] for i in r.editable)
        if _bare(phrase) in " ".join(plain(here).split()):
            skipped.append(f"{v.request} ({v.rule}): already reads as the rule says; left as filed")
            continue
        why = _place(blocks, r, v, phrase)
        if why:
            skipped.append(f"{v.request} ({v.rule}): {why}")
            continue
        did.append(f"{v.request} ({v.rule}): {v.action}")
    new = Document(blocks, [Response(**vars(x)) for x in doc.responses], doc.index, doc.notes)
    return new, did, skipped


def changed(before: str, after: str, set_type: str | None = None) -> tuple[list[str], bool]:
    """(the response numbers whose words differ, whether anything outside every
    response differs) between two versions of one document."""
    a, b = read(before, set_type), read(after, set_type)
    an, bn = a.by_number(), b.by_number()
    if list(an) != list(bn):
        return sorted(set(an) ^ set(bn)), True
    diff = [n for n in an if plain(a.text_of(an[n])) != plain(b.text_of(bn[n]))]

    def outside(d: Document) -> list[str]:
        mine = {i for r in d.responses for i in [r.head, *r.body]}
        return [plain(x) for i, x in enumerate(d.blocks) if i not in mine]

    return diff, outside(a) != outside(b)
