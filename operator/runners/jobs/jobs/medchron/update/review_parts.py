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

import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Callable

from ..audit import claims as CL
from . import body as B, review_repair as RR

WINDOW_PAGES = 20
#: Delivered entries within this many days of a part's entries are shown with
#: it: a new copy of a delivered visit is often dated a day off.
NEAR_DAYS = 7
#: All the delivered entries one part is shown (copies, near dates, same provider).
CONTEXT_MAX = 24
#: A new document this share of whose pages are copies of ONE delivered
#: exhibit is named as such: its remaining pages very likely are too.
MOSTLY_ALREADY = 0.9
FINISH_MAX_ENTRIES = 6
FINISH_MAX_IMAGES = 24
Images = Callable[[int, int], dict[str, Any] | None]


@dataclass
class Part:
    kind: str  # "pages" | "duplicates" | "text"
    entries: list[RR.Entry]
    exhibit: int = 0
    pages: list[int] = field(default_factory=list)
    note: str = ""


#: exhibit -> {page: the delivered page it is a copy of, "Exhibit 1 p. 37"}
Already = dict[int, dict[int, str]]


def already_note(ex: int, n: int, already: Already) -> str:
    """What the review is told about a new document's pages that are copies
    of delivered pages: they are not shown (the delivered chronology already
    carries them), and when nearly all are copies of one delivered exhibit,
    that the rest very likely are too."""
    got = already.get(ex) or {}
    if not got:
        return ""
    srcs = [v.split(" p.")[0] for v in got.values()]
    top = max(set(srcs), key=srcs.count)
    out = (
        f"Exhibit {ex}: {len(got)} of {n} pages are copies of delivered pages (not shown; the delivered "
        f"chronology already carries them): pp. {_ranges(sorted(got))}."
    )
    if srcs.count(top) >= MOSTLY_ALREADY * n:
        out += (
            f" {srcs.count(top)} of its {n} pages are copies of delivered {top}: its other pages are very "
            "likely copies too, and an added entry composed from them most likely repeats a delivered visit."
        )
    return out


def _ranges(ps: list[int]) -> str:
    out: list[str] = []
    for p in ps:
        if out and out[-1].split("-")[-1] == str(p - 1):
            out[-1] = f"{out[-1].split('-')[0]}-{p}"
        else:
            out.append(str(p))
    return ", ".join(out)


def _owner(es: list[RR.Entry], ex: int, lo: int) -> RR.Entry | None:
    """The entry whose last cited page of `ex` most closely precedes `lo`,
    else the first that cites `ex` after it."""
    last = [(max(p for x, p in e.cites() if x == ex), e) for e in es]
    before = [t for t in last if t[0] < lo]
    if before:
        return max(before, key=lambda t: t[0])[1]
    return min(last, key=lambda t: t[0])[1] if last else None


def plan(
    ents: list[RR.Entry], keep: set[int], npages: Callable[[int], int], already: Already | None = None
) -> list[Part]:
    """The review's parts for the entries `ents` (the targets of a round).
    A page that is a copy of a delivered page (`already`) and that no added
    entry cites is not shown."""
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
        cited = {p for e in es for x, p in e.cites() if x == ex}
        skip = (already or {}).get(ex) or {}
        show = [p for p in range(1, n + 1) if p not in skip or p in cited]
        note = already_note(ex, n, already or {})
        windows = [show[i : i + WINDOW_PAGES] for i in range(0, len(show), WINDOW_PAGES)]
        for w in windows:
            mine = [e for e in es if any(x == ex and p in w for x, p in e.cites())]
            own = _owner(es, ex, w[0]) if not mine else None
            out.append(Part("pages", mine or ([own] if own else []), ex, w, note))
        if len(windows) > 1 and len(es) > 1:
            out.append(Part("duplicates", es, ex, note=note))
    if plain:
        out.append(Part("text", plain))
    return [p for p in out if p.entries]


def _entry_block(e: RR.Entry, tag: str = "ADDED") -> str:
    claims = "\n".join(f"{x.anchor}: {x.text}" for x in e.claims) or "(no cited claim)"
    loose = "".join(f"\n{x.anchor}: {x.head}{x.text}" for x in e.uncited)
    loose = f"\n\nUNCITED PARAGRAPHS:{loose}" if loose else ""
    return f"=== {e.anchor} ({tag}) ===\n{e.text}\n\nCLAIMS:\n{claims}{loose}"


def _day(e: str) -> date | None:
    try:
        return date.fromisoformat(B.iso(e))
    except ValueError:
        return None


_WORD = re.compile(r"[a-z]{4,}")
_GENERIC = {"medical", "center", "clinic", "health", "group", "care", "services", "hospital", "records", "provider",
            "stated", "record", "name", "diagnoses", "information", "other", "imaging", "therapy", "physical"}  # fmt: skip


def _provider_words(e: str) -> set[str]:
    return set(_WORD.findall(B.provider_of(e).lower())) - _GENERIC


def _delivered_cites(e: str) -> set[tuple[int, int]]:
    return {(int(m.group(1)), p) for m in CL.CITE.finditer(e) for p in CL.parse_pages(m.group(2)) or [1]}


def _copied(ents: list[RR.Entry], already: Already) -> set[tuple[int, int]]:
    """The delivered pages that pages of these entries' new documents copy."""
    out: set[tuple[int, int]] = set()
    for ex in {x for e in ents for x, _ in e.cites()}:
        for ref in (already.get(ex) or {}).values():
            m = re.match(r"Exhibit (\d+) p\. (\d+)", ref)
            if m:
                out.add((int(m.group(1)), int(m.group(2))))
    return out


def context(
    ents: list[RR.Entry], all_ents: list[RR.Entry], delivered: list[str], already: Already | None = None
) -> str:
    """Read only, where a duplicate of an added entry may already be: the
    delivered entries that cite a delivered page a page of these entries'
    documents copies (page identity, whatever their date: a pre-operative
    history delivered weeks before the surgery was twice reported "missing"),
    the delivered entries within NEAR_DAYS, the delivered entries of the same
    provider (nearest first), at most CONTEXT_MAX in all, and the other added
    entries on the same dates."""
    days = [d for d in (_day(e.text) for e in ents) if d]
    mine = {e.anchor for e in ents}
    copied = _copied(ents, already or {})
    words = set().union(*(_provider_words(e.text) for e in ents)) if ents else set()

    def gap(t: str) -> int:
        d = _day(t)
        return min((abs((d - x).days) for x in days), default=10**6) if d else 10**6

    picks: list[tuple[int, int, int]] = []  # (priority, gap, index)
    for i, t in enumerate(delivered, 1):
        g = gap(t)
        if _delivered_cites(t) & copied:
            picks.append((0, g, i))
        elif g <= NEAR_DAYS:
            picks.append((1, g, i))
        elif words & _provider_words(t):
            picks.append((2, g, i))
    rows = [(f"D{i}", "DELIVERED", delivered[i - 1]) for _, _, i in sorted(picks)[:CONTEXT_MAX]]
    rows += [(e.anchor, "ADDED", e.text) for e in all_ents if e.anchor not in mine and gap(e.text) == 0]
    if not rows:
        return ""
    return "NEARBY ENTRIES, READ ONLY:\n\n" + "\n\n".join(f"=== {a} ({k}) ===\n{t}" for a, k, t in rows)


def head(client: str) -> dict[str, Any]:
    return {"type": "text", "text": client}


def review_content(
    part: Part,
    client: str,
    images: Images,
    all_ents: list[RR.Entry],
    delivered: list[str],
    already: Already | None = None,
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
    if part.note:
        ask += f"\n{part.note}"
    out.append({"type": "text", "text": f"{ask}\n\nADDED ENTRIES IN THIS PART: {names}"})
    out += [{"type": "text", "text": _entry_block(e)} for e in part.entries]
    for pg in part.pages:
        img = images(part.exhibit, pg)
        if img is None:
            continue
        by = [e.anchor for e in part.entries if (part.exhibit, pg) in e.cites()]
        label = f"cited by {', '.join(by)}" if by else "cited by no added entry"
        out += [{"type": "text", "text": f"Exhibit {part.exhibit} p. {pg} ({label}):"}, img]
    ctx = context(part.entries, all_ents, delivered, already)
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
