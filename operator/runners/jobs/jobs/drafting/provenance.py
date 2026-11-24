"""Whose paper a matter file is: ours, or the other side's served paper (2026-11-24).

WHY. A response job on matter files holding our own earlier papers failed
``served_other_set`` naming three files: a served "DIS RFP1", an Operator
response draft ("Responses to <party> RFP Set One DRAFT ... by A&P Operator
Agent") and a corrected response ("<name>_Resp_RFP_CORRECTED_<date>"). Each
carried the party lines a served set carries and no title in the capitals the
finder's title list reads, so all three were "a served set of another kind" and
the whole job was refused. Our own response papers were counted as discovery
served on us.

:func:`whose` is the ONE place that decides, by code and before any read,
whether a file is ours (a response of our client, a draft, a paper the
Operator or SMD filed) or a paper the other side served. The finder calls it
today (``served_page.classify``); the records lane, the reply and the route are
meant to call the same function, so a single matter-file inventory with
provenance grows from here rather than from a second copy of these rules.

What reads as ours, first match wins:

* ``filed``: a file in a folder the Operator delivered into, or a document an
  earlier Operator job delivered (``delivered``, the delivered-drafts ledger's
  names).
* ``draft``: a file named as a draft ("DRAFT" anywhere in its name, "for
  attorney review", "Operator Agent": ``served_papers.draft_name``).
* ``response``: a file named as a response ("Responses ...", "Resp", "Response
  to", "Answers to") or titled one on its first pages in any case ("Responses to
  Requests for Production"). A response whose RESPONDING PARTY names someone
  other than our client is the other side's answer to OUR discovery:
  ``theirs`` with ``what`` "response", never a served set either.
* ``attorney``: its attorney block (the lines above the court's name) prints one
  of our firm's attorneys or bar numbers (``our_firm``: the roster's ``name`` and
  ``bar_number``, else the first line of each authored ``attorneys[].block``).
  A paper our side wrote is never served on our client.

Anything else is ``theirs`` (``what`` "served") when its first pages carry the
asking and responding party lines a served set carries, else ``unread``.
``kind`` is the set type the title names in any case, else the one its name
names ("RFP1", "SROGs", "RFA", ``sets.VARIANTS``), else None.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from . import parties, served_papers as papers, sets
from .served_slice import ASKING, RESPONDING, TITLES

OURS, THEIRS, UNREAD = "ours", "theirs", "unread"

_TITLE_ANY = {k: re.compile("(?i)" + v) for k, v in TITLES.items()}
#: A response TITLE in any case: a party's ("Plaintiff Gamma's Responses to ...")
#: or one naming its set ("... Requests for Production, Set One"). Instruction prose
#: in a served set ("your responses to these requests for production must be
#: served") is neither, so it never reads as a title.
_RESPONSE_TITLE = re.compile(
    r"(?i)(?:(?<=['’]s\s)|(?<=['’]\s))(?:(?:verified|amended|further|supplemental|corrected)\s+)*"
    r"(?:RESPONSES?|ANSWERS?)(?:\s+AND\s+OBJECTIONS?)?\s+TO\s+(?:[A-Z0-9.,'’&-]+\s+){0,8}?(?:"
    + "|".join(TITLES.values())
    + r")(?!\s+NO\.)"
    + r"|\b(?:RESPONSES?|ANSWERS?)(?:\s+AND\s+OBJECTIONS?)?\s+TO\s+(?:[A-Z0-9.,'’&-]+\s+){0,8}?(?:"
    + "|".join(TITLES.values())
    + r")(?!\s+NO\.)[^\n]{0,60}?\bSET\b"
)
#: A response by its name, in the words the name is split into (``_words``).
_RESPONSE_NAME = re.compile(r"(?i)\b(?:responses|resps?|(?:response|answers?)(?:\s+and\s+objections?)?\s+to)\b")
_FORM_NAME = re.compile(r"(?i)\bf\s?rogs?\b|\bform\s+interrogator(?:y|ies)\b")


@dataclass(frozen=True)
class Whose:
    """One file's provenance: ``side`` (OURS, THEIRS or UNREAD), ``what`` it is
    (ours: "filed", "draft" or "response"; theirs: "served" or "response"), the
    set ``kind`` it names when code reads one, and ``why`` in a few words."""

    side: str
    what: str
    kind: str | None
    why: str


def _words(name: str) -> str:
    """A file name as words: no extension, "_", "-" and "." as spaces, letters and digits apart ("RFP1")."""
    stem = re.sub(r"(?i)\.(?:docx?|pdf|rtf|txt|msg)$", "", name or "")
    spaced = re.sub(r"[_\-.]+", " ", stem)
    return " ".join(re.sub(r"(?<=[A-Za-z])(?=\d)|(?<=\d)(?=[A-Za-z])", " ", spaced).split())


def name_kind(name: str) -> str | None:
    """The set type a file name names ("DIS RFP1", "Resp_RFP", "SROGs Set 2"), else None."""
    words = _words(name)
    if _FORM_NAME.search(words):
        return "form_interrogatories"
    found = sets.variants(words)
    return found[0].key if found else None


def _title_kind(head: str, rx: dict[str, re.Pattern[str]]) -> str | None:
    hits = [(m.start(), k) for k, p in rx.items() for m in [p.search(head)] if m]
    return min(hits)[1] if hits else None


def _response_kind(head: str) -> str | None:
    m = _RESPONSE_TITLE.search(head)
    return _title_kind(m.group(0), _TITLE_ANY) if m else None


def _responding(head: str) -> str:
    m = RESPONDING.search(head)
    if not m:
        return ""
    if m.group(1).strip(" \t|"):
        return m.group(1).strip(" \t|")
    rest = [ln.strip(" \t|") for ln in head[m.end() :].splitlines() if ln.strip(" \t|")]
    return rest[0] if rest else ""


@dataclass(frozen=True)
class OurFirm:
    """Our firm's attorneys as a caption's attorney block prints them: names and bar numbers."""

    names: tuple[str, ...] = ()
    bars: tuple[str, ...] = ()


_SBN = re.compile(r"(?i)\((?:SBN|State\s+Bar\s+No\.?|Bar\s+No\.?)\s*:?\s*([A-Za-z0-9-]+)\)")
_COURT = re.compile(r"(?i)\bcourt\b")


def our_firm(authored: list[dict[str, Any]], roster: Any = None) -> OurFirm:
    """The firm's attorneys from its roster when installed (``roster.attorneys``:
    ``name``, ``bar_number``), and from the first line of each authored caption
    block (``drafting-firm.yaml`` ``attorneys[].block``: "Jane Q. Attorney, Esq.
    (SBN 123456)"). An unreadable roster adds nothing here; the caption stage
    stops on it."""
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


def _our_attorney(head: str, firm: OurFirm | None) -> str:
    """The firm attorney or bar number the paper's attorney block (above the court's name) prints, else ""."""
    if firm is None:
        return ""
    m = _COURT.search(head)
    block = head[: m.start() if m else 1500][:1500]
    bar = next((b for b in firm.bars if re.search(rf"(?<![A-Za-z0-9]){re.escape(b)}(?![A-Za-z0-9])", block)), "")
    flat = " ".join(block.split()).casefold()
    return bar or next((n for n in firm.names if " ".join(n.split()).casefold() in flat), "")


def _stem(name: str) -> str:
    return re.sub(r"(?i)\.docx?$", "", " ".join(str(name).split())).casefold()


def whose(
    name: str,
    folder: str = "",
    head: str = "",
    *,
    clients: Iterable[str] | None = None,
    delivered: Iterable[str] = (),
    firm: OurFirm | None = None,
) -> Whose:
    """Ours or the other side's, by code: the file's name and folder, its first
    pages (``head``), our client's names on the matter (``clients``; None: not
    checked), the documents earlier Operator jobs delivered (``delivered``)
    and our firm's attorneys (``firm``, ``our_firm``)."""
    kind = _response_kind(head) or name_kind(name) or _title_kind(head, _TITLE_ANY)
    if papers.ours_written(name, folder) and not papers.draft_name(name):
        return Whose(OURS, "filed", kind, "in a folder the Operator delivered into")
    if _stem(Path(name).name) in {_stem(Path(d).name) for d in delivered}:
        return Whose(OURS, "filed", kind, "a document an earlier Operator job delivered")
    if papers.draft_name(name):
        return Whose(OURS, "draft", kind, "named as a draft")
    by_name, by_title = _RESPONSE_NAME.search(_words(name)), _RESPONSE_TITLE.search(head)
    if by_name or by_title:
        responding = _responding(head)
        if clients is not None and responding and not any(parties.same(responding, c) for c in clients):
            return Whose(THEIRS, "response", kind, "the other side's response to our discovery")
        return Whose(OURS, "response", kind, "a response paper by its " + ("title" if by_title else "name"))
    who = _our_attorney(head, firm)
    if who:
        return Whose(OURS, "attorney", kind, f"its attorney block names our firm's {who}")
    if ASKING.search(head) and RESPONDING.search(head):
        return Whose(THEIRS, "served", _title_kind(head, _TITLE_ANY) or name_kind(name), "served on our client")
    return Whose(UNREAD, "", kind, "neither ours nor a served paper, by code")
