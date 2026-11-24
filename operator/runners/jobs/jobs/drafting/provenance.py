"""Whose paper a matter file is: ours, or the other side's served paper (2026-11-24).

WHY. A response job on a matter file holding our own earlier papers failed
``served_other_set`` naming three files: a served "DIS RFP1", an Operator
response draft ("Responses to <party> RFP Set One DRAFT <date> by A&P Operator
Agent") and a corrected response ("<name>_Resp_RFP_CORRECTED_<date>"). Each
carried the party lines a served set carries and no title in the capitals the
finder's title list reads, so all three were "a served set of another kind" and
the whole job was refused.

:func:`whose` is the ONE place that decides, by code and before any read,
whether a file is ours or a paper the other side served. The finder calls it
(``served_page.classify``); the records lane, the reply and the route are meant
to call the same function, so a matter-file inventory with provenance grows
from here rather than from a second copy of these rules.

A paper is ours ONLY on strong evidence (the #4226 review: a real served set
silently dropped is worse than a held job), first match wins:

* ``filed``: our own filing records. A folder the Operator delivered into, a
  file id an earlier Operator job's delivery proof names
  (``Evidence.proven``, the ``DELIVERY_PROVEN`` files), or a document name the
  delivered-drafts ledger names (``Evidence.delivered``).
* ``stamped``: the Operator's own name stamp, "... by A&P Operator Agent"
  (``served_papers.STAMP``).
* ``response``: a response TITLE in the title area (through the RESPONDING
  line on a PDF, the first page of a Word file, the same span
  ``served_page.classify`` reads), on a line of its own, whose RESPONDING
  party is our client. Instruction prose ("serve written responses to Request
  for Production, Set One within 30 days") never matches.

Weak evidence never makes a paper ours on its own: response words in a name
("Resp", "Responses", "Answers to"), a draft name, our firm's attorney in the
attorney block. Each is recorded in ``clues`` (supporting only). A paper
without strong evidence is ``theirs`` (``served``) when its first pages carry
both party lines, else ``unread``; ``kind`` is the set type its title or its
name reads ("RFP1", "SROGs", ``sets.VARIANTS``), else None.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import parties, served_papers as papers, sets
from .served_slice import ASKING, RESPONDING, TITLES

OURS, THEIRS, UNREAD = "ours", "theirs", "unread"
WORD_EXTS = (".docx", ".doc")

_TITLE_ANY = {k: re.compile("(?i)" + v) for k, v in TITLES.items()}
#: A response title on a line of its own: an optional party ("Plaintiff Gamma
#: Example's"), the response words, the set's kind, an optional "Set One", and
#: nothing after. Instruction prose has words before or after and never matches.
_RESPONSE_TITLE = re.compile(
    r"(?im)^[ \t|]*(?:[A-Z][^\n]{0,120}?['’]S?[ \t]+)?(?:(?:VERIFIED|AMENDED|FURTHER|SUPPLEMENTAL|CORRECTED)\s+)*"
    r"(?:RESPONSES?|ANSWERS?)(?:\s+AND\s+OBJECTIONS?)?\s+TO\s+(?:[A-Z0-9.,'’&-]+\s+){0,8}?(?:"
    + "|".join(TITLES.values())
    + r")(?:\s+OF\s+DOCUMENTS)?(?:\s+AND\s+THINGS)?(?:[,\s]+\(?SET\s+(?:NO\.?\s*)?\w+\)?)?[ \t.:|]*$"
)
#: Response words in a NAME: supporting evidence only.
_RESPONSE_NAME = re.compile(r"(?i)\b(?:responses|resps?|(?:response|answers?)(?:\s+and\s+objections?)?\s+to)\b")
_FORM_NAME = re.compile(r"(?i)\bf\s?rogs?\b|\bform\s+interrogator(?:y|ies)\b")
_SBN = re.compile(r"(?i)\((?:SBN|State\s+Bar\s+No\.?|Bar\s+No\.?)\s*:?\s*([A-Za-z0-9-]+)\)")
_ATTORNEYS_FOR = re.compile(r"(?im)^[ \t|]*Attorneys?\s+for\b")
_OF_RECORD = re.compile(r"(?i)^\s*TO\b.*\bATTORNEYS?\s+OF\s+RECORD\b")


@dataclass(frozen=True)
class OurFirm:
    """Our firm's attorneys as a caption's attorney block prints them: names and bar numbers."""

    names: tuple[str, ...] = ()
    bars: tuple[str, ...] = ()


@dataclass(frozen=True)
class Evidence:
    """What the job knows of our side: our client's names (None: not checked),
    our firm's attorneys, and our own filing records (delivered document names,
    delivery-proven file ids)."""

    clients: tuple[str, ...] | None = None
    firm: OurFirm | None = None
    delivered: tuple[str, ...] = ()
    proven: tuple[str, ...] = ()


@dataclass(frozen=True)
class Whose:
    """One file's provenance: ``side`` (OURS, THEIRS or UNREAD), ``what`` it is
    (ours: "filed", "stamped" or "response"; theirs: "served"), the set
    ``kind`` code reads, ``why`` in a few words, and the weak ``clues`` seen."""

    side: str
    what: str
    kind: str | None
    why: str
    clues: tuple[str, ...] = ()


def words(name: str) -> str:
    """A file name as words: no extension, "_", "-" and "." as spaces, letters and digits apart ("RFP1")."""
    stem = re.sub(r"(?i)\.(?:docx?|pdf|rtf|txt|msg)$", "", name or "")
    spaced = re.sub(r"[_\-.]+", " ", stem)
    return " ".join(re.sub(r"(?<=[A-Za-z])(?=\d)|(?<=\d)(?=[A-Za-z])", " ", spaced).split())


def name_kind(name: str) -> str | None:
    """The set type a file name names ("DIS RFP1", "Resp_RFP", "SROGs Set 2"), else None."""
    w = words(name)
    if _FORM_NAME.search(w):
        return "form_interrogatories"
    found = sets.variants(w)
    return found[0].key if found else None


def title_kind(text: str) -> str | None:
    """The set type a title in any case names, first in the text; None when none."""
    hits = [(m.start(), k) for k, p in _TITLE_ANY.items() for m in [p.search(text)] if m]
    return min(hits)[1] if hits else None


def title_area(head: str, ext: str) -> str:
    """Where a paper's title is: a Word file's whole first page (its caption table
    comes last), else the text through the RESPONDING line ("" when none)."""
    if ext in WORD_EXTS:
        return head
    m = RESPONDING.search(head)
    return head[: m.end()] if m else ""


def responding(head: str) -> str:
    m = RESPONDING.search(head)
    if not m:
        return ""
    if m.group(1).strip(" \t|"):
        return m.group(1).strip(" \t|")
    rest = [ln.strip(" \t|") for ln in head[m.end() :].splitlines() if ln.strip(" \t|")]
    return rest[0] if rest else ""


def our_firm(authored: list[dict[str, Any]] | None, roster: Any = None) -> OurFirm:
    """The firm's attorneys from its roster when installed (``name``,
    ``bar_number``) and from the first line of each authored caption block
    (``drafting-firm.yaml`` ``attorneys[].block``: "Jane Q. Attorney, Esq. (SBN
    123456)"). An unreadable roster adds nothing here; the caption stage stops on it."""
    names, bars = [], []
    for p in getattr(roster, "attorneys", ()) if getattr(roster, "status", "") == "ok" else ():
        names.append(str(p.name))
        bars += [str(p.bar_number)] if p.bar_number else []
    for e in authored or []:
        first = str((e.get("block") or [""])[0]) if isinstance(e, dict) else ""
        bars += _SBN.findall(first)
        names.append(re.split(r",|\(", first)[0].strip())
    keep = lambda xs: tuple(dict.fromkeys(x for x in xs if len(x.strip()) >= 4))  # noqa: E731
    return OurFirm(keep(names), keep(bars))


def our_attorney(head: str, ext: str, firm: OurFirm | None) -> str:
    """Our firm's attorney or bar number in a PDF's attorney block (its lines
    before "Attorneys for", a "TO ... ATTORNEYS OF RECORD" line skipped), else "".
    Supporting evidence only: a served paper quotes our attorney too."""
    m = _ATTORNEYS_FOR.search(head)
    if firm is None or ext in WORD_EXTS or not m:
        return ""
    lines = [ln for ln in head[: m.start()].splitlines() if not _OF_RECORD.search(ln)]
    block = "\n".join(lines)
    for b in firm.bars:
        if re.search(rf"(?<![\w-]){re.escape(b)}(?![\w-])", block):
            return b
    for n in firm.names:
        if re.search(r"(?i)(?<!\w)" + r"\s+".join(map(re.escape, n.split())) + r"(?!\w)", block):
            return n
    return ""


def _stem(name: str) -> str:
    return re.sub(r"(?i)\.docx?$", "", " ".join(str(Path(name).name).split())).casefold()


def _clues(name: str, head: str, ext: str, ev: Evidence) -> tuple[str, ...]:
    out = ["a response by its name"] if _RESPONSE_NAME.search(words(name)) else []
    out += ["a draft by its name"] if papers.draft_name(name) else []
    who = our_attorney(head, ext, ev.firm)
    return (*out, *([f"its attorney block names our firm's {who}"] if who else []))


def _ours_title(head: str, ext: str, ev: Evidence) -> bool:
    """A response title on its own line in the title area, our client responding."""
    if not _RESPONSE_TITLE.search(title_area(head, ext)):
        return False
    who = responding(head)
    return bool(who) and ev.clients is not None and any(parties.same(who, c) for c in ev.clients)


def whose(name: str, folder: str = "", head: str = "", *, ev: Evidence | None = None, doc_id: str = "",
          ext: str = "") -> Whose:  # fmt: skip
    """Ours on strong evidence only, else the other side's (``served``) when
    the paper carries both party lines, else ``unread`` (module docstring)."""
    ev = ev or Evidence()
    ext = ext or Path(name).suffix.lower()
    kind = title_kind(title_area(head, ext)) or name_kind(name) or title_kind(head)
    clues = _clues(name, head, ext, ev)
    if papers.operator_folder(folder):
        return Whose(OURS, "filed", kind, "in a folder the Operator delivered into", clues)
    if doc_id and doc_id in ev.proven:
        return Whose(OURS, "filed", kind, "a file an earlier Operator job's delivery proof names", clues)
    if _stem(name) in {_stem(d) for d in ev.delivered}:
        return Whose(OURS, "filed", kind, "a document an earlier Operator job delivered", clues)
    if papers.STAMP.search(" ".join(re.sub(r"_+", " ", name).split())):
        return Whose(OURS, "stamped", kind, "the Operator's own name stamp", clues)
    if _ours_title(head, ext, ev):
        return Whose(OURS, "response", kind, "a response title with our client responding", clues)
    if ASKING.search(head) and RESPONDING.search(head):
        return Whose(THEIRS, "served", kind, "served on our client", clues)
    return Whose(UNREAD, "", kind, "neither ours nor a served paper, by code", clues)
