"""What the independent review and the finisher are shown, in parts sized to
finish. $0.

THE REVIEW reads every page of every new document an added entry cites, not
only the cited pages: a report that reached compose as its first pages, a
visit composed twice from one file, a value the entry drops, all sit on pages
no citation names. A document is shown in windows of WINDOW_PAGES pages; each
window comes with every added entry citing a page in it, and a window no entry
cites comes with the entry whose last cited page precedes it (the entry that
composes that document's continuation), so every page is read beside the text
that should carry it. A document whose entries span more than one window also
gets a TEXT-ONLY duplicate part with all of its entries: two entries composed
from distant pages of one long file (the same visit printed twice inside it)
are compared there, since no window holds both.

Every part also carries, read only, the delivered entries and the other added
entries on the same dates: a copy of a delivered visit is found against them.

THE FINISHER is shown the findings it settles, the entries they are on (with
their claim anchors), the same-date entries a duplicate may survive in, and
the page images the findings rely on, then the pages the entries cite.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from . import body as B, review_repair as RR

WINDOW_PAGES = 20
FINISH_MAX_ENTRIES = 6
FINISH_MAX_IMAGES = 24
Images = Callable[[int, int], dict[str, Any] | None]


@dataclass
class Part:
    kind: str  # "pages" | "duplicates" | "text"
    entries: list[RR.Entry]
    exhibit: int = 0
    pages: list[int] = field(default_factory=list)


def _owner(es: list[RR.Entry], ex: int, lo: int) -> RR.Entry | None:
    """The entry whose last cited page of `ex` most closely precedes `lo`,
    else the first that cites `ex` after it."""
    last = [(max(p for x, p in e.cites() if x == ex), e) for e in es]
    before = [t for t in last if t[0] < lo]
    if before:
        return max(before, key=lambda t: t[0])[1]
    return min(last, key=lambda t: t[0])[1] if last else None


def plan(ents: list[RR.Entry], keep: set[int], npages: Callable[[int], int]) -> list[Part]:
    """The review's parts for the entries `ents` (the targets of a round)."""
    by_ex: dict[int, list[RR.Entry]] = {}
    plain: list[RR.Entry] = []
    for e in ents:
        exs = sorted({x for x, _ in e.cites() if x in keep})
        for x in exs:
            by_ex.setdefault(x, []).append(e)
        if not exs:
            plain.append(e)
    out: list[Part] = []
    for ex in sorted(by_ex):
        es, n = by_ex[ex], max(npages(ex), 1)
        windows = [list(range(a, min(a + WINDOW_PAGES, n + 1))) for a in range(1, n + 1, WINDOW_PAGES)]
        for w in windows:
            mine = [e for e in es if any(x == ex and p in w for x, p in e.cites())]
            own = _owner(es, ex, w[0]) if not mine else None
            out.append(Part("pages", mine or ([own] if own else []), ex, w))
        if len(windows) > 1 and len(es) > 1:
            out.append(Part("duplicates", es, ex))
    if plain:
        out.append(Part("text", plain))
    return [p for p in out if p.entries]


def _entry_block(e: RR.Entry, tag: str = "ADDED") -> str:
    claims = "\n".join(f"{x.anchor}: {x.text}" for x in e.claims) or "(no cited claim)"
    loose = "".join(f"\n{x.anchor}: {x.head}{x.text}" for x in e.uncited)
    loose = f"\n\nUNCITED PARAGRAPHS:{loose}" if loose else ""
    return f"=== {e.anchor} ({tag}) ===\n{e.text}\n\nCLAIMS:\n{claims}{loose}"


def context(ents: list[RR.Entry], all_ents: list[RR.Entry], delivered: list[str]) -> str:
    """The same-date entries not in this part, read only: where a duplicate
    of an added entry may already be."""
    want = {B.iso(e.text) for e in ents}
    mine = {e.anchor for e in ents}
    rows = [(f"D{i}", "DELIVERED", t) for i, t in enumerate(delivered, 1) if B.iso(t) in want]
    rows += [(e.anchor, "ADDED", e.text) for e in all_ents if e.anchor not in mine and B.iso(e.text) in want]
    if not rows:
        return ""
    return "SAME-DATE ENTRIES, READ ONLY:\n\n" + "\n\n".join(f"=== {a} ({k}) ===\n{t}" for a, k, t in rows)


def head(client: str) -> dict[str, Any]:
    return {"type": "text", "text": client}


def review_content(
    part: Part, client: str, images: Images, all_ents: list[RR.Entry], delivered: list[str]
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = [head(client)]
    names = ", ".join(e.anchor for e in part.entries)
    if part.kind == "pages":
        ask = (
            f"PART: Exhibit {part.exhibit} pages {part.pages[0]} to {part.pages[-1]}, every page of this window. "
            f"Check the added entries {names} against these pages."
        )
    elif part.kind == "duplicates":
        ask = (
            f"PART: DUPLICATE CHECK, text only. Every added entry composed from Exhibit {part.exhibit}: {names}. "
            "Report only entries that repeat another entry's visit or content (class duplicate); the pages are "
            "checked in the other parts."
        )
    else:
        ask = f"PART: added entries that cite no new page: {names}. Check their text."
    out.append({"type": "text", "text": f"{ask}\n\nADDED ENTRIES IN THIS PART: {names}"})
    out += [{"type": "text", "text": _entry_block(e)} for e in part.entries]
    for pg in part.pages:
        img = images(part.exhibit, pg)
        if img is None:
            continue
        by = [e.anchor for e in part.entries if (part.exhibit, pg) in e.cites()]
        label = f"cited by {', '.join(by)}" if by else "cited by no added entry"
        out += [{"type": "text", "text": f"Exhibit {part.exhibit} p. {pg} ({label}):"}, img]
    ctx = context(part.entries, all_ents, delivered)
    return out + ([{"type": "text", "text": ctx}] if ctx else [])


def entry_index(anchor: str) -> int | None:
    a = anchor.split(".", 1)[0]
    return int(a[1:]) - 1 if a[:1] == "N" and a[1:].isdigit() else None


def finish_parts(todo: list[RR.Finding], ents: list[RR.Entry]) -> list[tuple[list[RR.Entry], list[RR.Finding]]]:
    """Findings grouped by the entries they are on, at most
    FINISH_MAX_ENTRIES entries per part."""
    by: dict[int, list[RR.Finding]] = {}
    for f in todo:
        k = entry_index(f.anchor)
        if k is not None and 0 <= k < len(ents):
            by.setdefault(k, []).append(f)
    keys = sorted(by)
    out = []
    for i in range(0, len(keys), FINISH_MAX_ENTRIES):
        ks = keys[i : i + FINISH_MAX_ENTRIES]
        out.append(([ents[k] for k in ks], [f for k in ks for f in by[k]]))
    return out


def _finding_line(f: RR.Finding) -> str:
    where = f"Exhibit {f.exhibit} p. {f.page}" if f.exhibit and f.page else "no page named"
    says = f" The page says: {f.page_says}" if f.page_says else ""
    return f"{f.fid} [{f.severity}] [{f.klass}] {f.anchor} ({where}): {f.problem}{says}"


def finish_content(
    ents: list[RR.Entry],
    found: list[RR.Finding],
    client: str,
    images: Images,
    all_ents: list[RR.Entry],
    delivered: list[str],
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = [head(client)]
    out.append({"type": "text", "text": "FINDINGS TO SETTLE:\n" + "\n".join(_finding_line(f) for f in found)})
    out += [{"type": "text", "text": _entry_block(e)} for e in ents]
    pages = [(f.exhibit, f.page) for f in found if f.exhibit and f.page]
    pages += sorted({p for e in ents for p in e.cites()})
    shown = 0
    for ex, pg in dict.fromkeys(pages):
        img = images(ex, pg) if shown < FINISH_MAX_IMAGES else None
        if img is not None:
            out += [{"type": "text", "text": f"Exhibit {ex} p. {pg}:"}, img]
            shown += 1
    ctx = context(ents, all_ents, delivered)
    return out + ([{"type": "text", "text": ctx}] if ctx else [])
