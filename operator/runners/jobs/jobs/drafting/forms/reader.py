"""The checked-box protocol: two model readers and the pixel read, reconciled.

The model call is made elsewhere (the lane, around this module); this module
owns what the reader is told, how its reply is parsed, and what three reads
must agree on before the checked list is used:

* ``READER_INSTRUCTION``: the instruction each reader gets with the rendered
  pages (the one measured 2026-11-20: 163 of 163 boxes right for both readers),
  followed by the exact reply shape ``parse_reply`` accepts;
* ``parse_reply``: strict. A number the form does not carry, a number twice, a
  number out of the form's order, or a reply outside the shape is a failed
  read (``ReplyError``), never a repaired one;
* ``reconcile``: the list is ``agreed`` only when both readers list the same
  numbers and the pixel read, on every page that registered, finds exactly
  those boxes checked. Otherwise the record names each number that differs and
  which source saw it checked, for SMD to confirm. Handwritten or attached
  extra interrogatories, a reader's unreadable notes, and a page the pixel read
  could not register also go to SMD, even when the lists agree.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Mapping

from .catalog import Catalogue, load
from .checkbox import FILLED_MIN, PixelRead

READER_INSTRUCTION = (
    "List every interrogatory number whose box is checked on this form, in order, one per line, exactly as "
    "numbered on the form. List nothing that is not checked. Then list separately anything handwritten or "
    "attached that adds an interrogatory, and anything you could not read with the page number.\n\n"
    "Also say which Section 4(a) definition box is checked: 4(a)(1), 4(a)(2), or neither.\n\n"
    "Reply in exactly this shape and nothing else (write NONE under a heading with nothing to list):\n"
    "CHECKED:\n<one interrogatory number per line>\n"
    "DEFINITION:\n<4(a)(1) or 4(a)(2) or NONE>\n"
    "ADDITIONS:\n<one line for each handwritten or attached interrogatory, or NONE>\n"
    "UNREADABLE:\n<page N: what you could not read, one per line, or NONE>"
)
HEADINGS = ("CHECKED", "DEFINITION", "ADDITIONS", "UNREADABLE")
DEFINITION_BOXES = ("4(a)(1)", "4(a)(2)")
_NONE = "NONE"
_PAGE_NOTE = re.compile(r"^page\s+\d+\s*:\s*\S", re.I)


class ReplyError(ValueError):
    """A reader's reply that is not in the shape, or lists what the form does not carry."""


@dataclass(frozen=True)
class ReaderRead:
    checked: tuple[str, ...]
    definition: tuple[str, ...]
    additions: tuple[str, ...]
    unreadable: tuple[str, ...]


def _blocks(reply: str) -> dict[str, list[str]]:
    blocks: dict[str, list[str]] = {}
    current: str | None = None
    for raw in reply.strip().splitlines():
        line = raw.strip()
        if not line:
            continue
        head = line.rstrip(":").upper()
        if line.endswith(":") and head in HEADINGS:
            if head in blocks:
                raise ReplyError(f"the heading {head} appears twice")
            current = head
            blocks[head] = []
        elif current is None:
            raise ReplyError(f"text before the first heading: {line[:80]!r}")
        else:
            blocks[current].append(line)
    if list(blocks) != list(HEADINGS):
        raise ReplyError(f"the reply's headings are {list(blocks)}, not {list(HEADINGS)} in that order")
    return blocks


def _listed(lines: list[str], head: str) -> list[str]:
    if lines == [_NONE]:
        return []
    if _NONE in lines or (not lines and head != "CHECKED"):
        raise ReplyError(f"{head}: NONE must stand alone, and an empty heading must say NONE")
    return lines


def parse_reply(reply: str, cat: Catalogue | None = None) -> ReaderRead:
    cat = cat or load()
    b = _blocks(reply)
    checked = _listed(b["CHECKED"], "CHECKED")
    for n in checked:
        if not cat.has(n):
            raise ReplyError(f"{n!r} is not an interrogatory on {cat.form} [{cat.revision}]")
    if len(set(checked)) != len(checked):
        raise ReplyError("an interrogatory number is listed twice")
    if checked != sorted(checked, key=cat.position):
        raise ReplyError("the checked numbers are not in the form's order")
    definition = _listed(b["DEFINITION"], "DEFINITION")
    if len(definition) > 1 or any(d not in DEFINITION_BOXES for d in definition):
        raise ReplyError(f"DEFINITION must be one of {list(DEFINITION_BOXES)} or NONE, not {definition}")
    unreadable = _listed(b["UNREADABLE"], "UNREADABLE")
    bad = [u for u in unreadable if not _PAGE_NOTE.match(u)]
    if bad:
        raise ReplyError(f"an UNREADABLE line must start 'page N:': {bad[0][:80]!r}")
    return ReaderRead(tuple(checked), tuple(definition), tuple(_listed(b["ADDITIONS"], "ADDITIONS")), tuple(unreadable))


@dataclass(frozen=True)
class Difference:
    """One box the sources disagree on, and which of them saw it checked."""

    number: str
    checked_by: tuple[str, ...]
    not_checked_by: tuple[str, ...]


@dataclass(frozen=True)
class Reconciled:
    status: str  # "agreed" or "disagreement"
    checked: tuple[str, ...]  # the agreed list (empty unless agreed)
    differences: tuple[Difference, ...]
    definition_boxes: tuple[str, ...]
    additions: tuple[str, ...]
    to_smd: tuple[str, ...]  # every reason SMD confirms before the list is used

    @property
    def agreed(self) -> bool:
        return self.status == "agreed"


def _sources(
    read_a: ReaderRead | None, read_b: ReaderRead | None, pixel: PixelRead | None, threshold: float
) -> dict[str, tuple[frozenset[str], frozenset[str] | None]]:
    """source name -> (keys it saw checked, keys it could see; None = all)."""
    out: dict[str, tuple[frozenset[str], frozenset[str] | None]] = {}
    for name, r in (("reader A", read_a), ("reader B", read_b)):
        if r is not None:
            out[name] = (frozenset(r.checked) | frozenset(r.definition), None)
    if pixel is not None:
        out["pixel read"] = (pixel.checked(threshold), pixel.registered)
    return out


def _differences(
    sources: Mapping[str, tuple[frozenset[str], frozenset[str] | None]], cat: Catalogue
) -> list[Difference]:
    keys = set().union(*(s[0] for s in sources.values())) if sources else set()
    order = {k: i for i, k in enumerate((*DEFINITION_BOXES, *cat.numbers))}
    out: list[Difference] = []
    for k in sorted(keys, key=lambda x: order.get(x, len(order))):
        yes = tuple(n for n, (got, _) in sources.items() if k in got)
        no = tuple(n for n, (got, seen) in sources.items() if k not in got and (seen is None or k in seen))
        if no:
            out.append(Difference(k, yes, no))
    return out


def reconcile(
    read_a: ReaderRead | None,
    read_b: ReaderRead | None,
    pixel: PixelRead | None,
    cat: Catalogue | None = None,
    threshold: float = FILLED_MIN,
) -> Reconciled:
    """Three sources, each a read or None (failed or unreadable)."""
    cat = cat or load()
    reasons: list[str] = []
    for name, r in (("reader A", read_a), ("reader B", read_b)):
        if r is None:
            reasons.append(f"{name} gave no usable read")
        elif r.unreadable:
            reasons.extend(f"{name} could not read {u}" for u in r.unreadable)
    if pixel is None:
        reasons.append("the pixel read did not run")
    elif pixel.unreadable_pages:
        pages = ", ".join(str(p + 1) for p in pixel.unreadable_pages)
        reasons.append(f"the pixel read could not register form page(s) {pages} (a scan, another revision, or rotated)")
    diffs = _differences(_sources(read_a, read_b, pixel, threshold), cat)
    reasons.extend(
        f"{d.number}: checked by {', '.join(d.checked_by)}; not by {', '.join(d.not_checked_by)}" for d in diffs
    )
    additions = tuple(dict.fromkeys((*(read_a.additions if read_a else ()), *(read_b.additions if read_b else ()))))
    reasons.extend(f"handwritten or attached: {a}" for a in additions)
    defs = tuple(sorted({d for r in (read_a, read_b) if r for d in r.definition}))
    ok = read_a is not None and read_b is not None and not diffs
    return Reconciled(
        status="agreed" if ok else "disagreement",
        checked=read_a.checked if ok and read_a else (),
        differences=tuple(diffs),
        definition_boxes=defs,
        additions=additions,
        to_smd=tuple(reasons),
    )
