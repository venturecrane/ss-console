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
entries on the same dates: a copy of a delivered visit is found against them,
and content they already record is not reported missing. They come first in
the request, after the client line, so parts with the same entries (the
windows of one long document an entry continues through) read them from the
prompt cache rather than paying for them again (``cache_blocks``,
2026-11-18).

A page is shown as its own text or as its picture by the one rule both
checkers share (``medchron/page_mode.py``); a text page carries the same
label a picture does (``review_pages.page_blocks``).

THE FINISHER is shown the findings it settles, the entries they are on (with
their claim anchors), the same-date entries a duplicate may survive in, and
the pages the findings name with the page either side (2026-11-18: no longer
every page the entries cite).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from datetime import date
from typing import Any, Callable

from ..audit import claims as CL
from . import body as B, frozen as FZ, review_pages as RV, review_repair as RR

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
#: A review request estimated above this many input tokens is split before it
#: is sent. The model refuses a request over 1,000,000 tokens outright, and a
#: real update's 149th part was refused at 1,187,421 (2026-11-21): a duplicate
#: check carrying all 667 added entries of one exhibit. Measured on that
#: update's 148 parts that ran, the estimate (``request_tokens``) is 0.46 to
#: 0.65 of the real input, so 300,000 estimated is at most about 650,000 real.
REQUEST_TOKENS_MAX = 300_000
Images = Callable[[int, int], dict[str, Any] | None]


@dataclass
class Part:
    kind: str  # "pages" | "duplicates" | "text" | "recheck"
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


def focus(rows: list[dict[str, Any]]) -> dict[int, set[int]] | None:
    """The pages the re-check reads: each page an open frozen finding names,
    with the page either side ({exhibit: pages}). None (read every page, as
    round 1 does) when a part round 1 could not review is among them: its
    entries were never reviewed, so no page of theirs is named yet."""
    out: dict[int, set[int]] = {}
    for r in rows:
        f = r.get("finding") or {}
        if f.get("klass") == FZ.NOT_REVIEWED:
            return None
        ex, pg = f.get("exhibit"), f.get("page")
        if isinstance(ex, int) and isinstance(pg, int) and ex and pg:
            out.setdefault(ex, set()).update(p for p in (pg - 1, pg, pg + 1) if p >= 1)
    return out


def plan_focus(
    ents: list[RR.Entry], keep: set[int], npages: Callable[[int], int], pages: dict[int, set[int]]
) -> list[Part]:
    """The re-check's parts: per exhibit, only `pages` (``focus``), in
    windows of WINDOW_PAGES, each with the targeted entries citing that
    exhibit; a targeted entry citing no focused exhibit is checked as text."""
    out: list[Part] = []
    for ex in sorted(x for x in pages if x in keep):
        n = max(npages(ex), 1)
        show = sorted(p for p in pages[ex] if p <= n)
        es = [e for e in ents if any(x == ex for x, _ in e.cites())]
        for i in range(0, len(show), WINDOW_PAGES) if es else ():
            out.append(Part("recheck", es, ex, show[i : i + WINDOW_PAGES]))
    seen = {e.anchor for p in out for e in p.entries}
    rest = [e for e in ents if e.anchor not in seen]
    return out + ([Part("text", rest)] if rest else [])


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
    modes: RV.Modes | None = None,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = [head(client)]
    ctx = context(part.entries, all_ents, delivered, already)
    out += [{"type": "text", "text": ctx}] if ctx else []  # first: the prefix parts with the same entries share
    names = ", ".join(e.anchor for e in part.entries)
    if part.kind == "pages":
        ask = (
            f"PART: Exhibit {part.exhibit} pages {part.pages[0]} to {part.pages[-1]}, every page of this window. "
            f"Check the added entries {names} against these pages."
        )
    elif part.kind == "recheck":
        ask = (
            f"PART: RE-CHECK, Exhibit {part.exhibit} pp. {_ranges(part.pages)}: the pages earlier findings name, "
            f"with the page either side. Check the added entries {names} against these pages."
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
        by = [e.anchor for e in part.entries if (part.exhibit, pg) in e.cites()]
        label = f"cited by {', '.join(by)}" if by else "cited by no added entry"
        out += RV.page_blocks(part.exhibit, pg, f" ({label})", images, modes)
    return out


def not_reviewed(p: Part) -> RR.Finding:
    """A part whose review never finished, as an open MUST finding on its
    first entry: held for SMD with its pages named, reviewed again next round
    (its entries are targets), never sent to the finisher, and never a failure
    of the whole stage (a real update died on one part's seven tries)."""
    where = f"Exhibit {p.exhibit} pp. {p.pages[0]}-{p.pages[-1]}" if p.pages else "its text"
    why = f"the review of entries {', '.join(e.anchor for e in p.entries)} ({where}) did not finish"
    f = RR.Finding(
        RR.MUST,
        FZ.NOT_REVIEWED,
        p.entries[0].anchor,
        p.exhibit or None,
        p.pages[0] if p.pages else None,
        why,
        "none",
        "",
        "",
    )
    f.outcome = f"{FZ.NOT_REVIEWED}: {why}"
    return f


def request_tokens(content: list[dict[str, Any]]) -> int:
    """A review request's input tokens, estimated: its text at four
    characters a token and each picture at the projection's page figure."""
    text = sum(len(str(b.get("text", ""))) for b in content if b.get("type") == "text")
    return text // RV.CHARS_PER_TOKEN + RV.PAGE_TOKENS * sum(1 for b in content if b.get("type") == "image")


def size_halves(part: Part, content: list[dict[str, Any]]) -> list[Part] | None:
    """A part too large to send, in two: by its pages when its pictures and
    page text are most of the request, else by its entries (a duplicate
    check carries every entry of one long file and no page). None when it is
    one entry and one page: nothing smaller can be sent."""
    reads = RV.page_reads(content)
    pages = reads["image_pages"] * RV.PAGE_TOKENS + reads["page_chars"] // RV.CHARS_PER_TOKEN
    by_pages = len(part.pages) > 1 and (pages * 2 >= request_tokens(content) or len(part.entries) < 2)
    if by_pages:
        h = len(part.pages) // 2
        return [
            replace(part, pages=part.pages[:h], note=part.note),
            replace(part, pages=part.pages[h:], note=part.note),
        ]
    if len(part.entries) > 1:
        h = len(part.entries) // 2
        return [
            replace(part, entries=part.entries[:h], note=part.note),
            replace(part, entries=part.entries[h:], note=part.note),
        ]
    return None


def repeated_contexts(
    parts: list[Part], all_ents: list[RR.Entry], delivered: list[str], already: Already | None = None
) -> set[str]:
    """The nearby-entries blocks more than one of a round's parts carries
    (the windows of one long document an entry continues through): the only
    ones worth a cache write (``llm``: a block unique to one call is never
    marked)."""
    seen: set[str] = set()
    out: set[str] = set()
    for p in parts:
        ctx = context(p.entries, all_ents, delivered, already)
        if ctx and ctx in seen:
            out.add(ctx)
        seen.add(ctx)
    return out


def cache_blocks(content: list[dict[str, Any]], repeats: set[str]) -> tuple[str, ...]:
    """A review request's cache markers: the system prompt, and its nearby
    entries (block 1, right after the client line) when another part of the
    round carries the very same block, so every later part reads them from
    the cache."""
    ctx = content[1].get("text") if len(content) > 1 else None
    return ("system", "user:1") if ctx and ctx in repeats else ("system",)


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


def _finish_pages(ents: list[RR.Entry], found: list[RR.Finding]) -> list[tuple[int, int]]:
    """The pages a finisher request shows: the pages its findings name and one
    either side, and, for a finding that names NO page, the pages its entry
    cites. Without the second, such a finding reached the finisher with
    nothing to read, could only be declined, and stayed open."""
    unnamed = {f.anchor.split(".")[0] for f in found if not (f.exhibit and f.page)}
    cited = sorted({p for e in ents if e.anchor in unnamed for p in e.cites()})
    return list(dict.fromkeys(RV.named_pages(found, FINISH_MAX_IMAGES) + cited))[:FINISH_MAX_IMAGES]


def finish_content(
    ents: list[RR.Entry],
    found: list[RR.Finding],
    client: str,
    images: Images,
    all_ents: list[RR.Entry],
    delivered: list[str],
    modes: RV.Modes | None = None,
) -> list[dict[str, Any]]:
    """The finisher's request: the findings, their entries, the pages the
    findings name with the page either side (``_finish_pages``), each as text
    or picture by the one rule, and the nearby entries."""
    out: list[dict[str, Any]] = [head(client)]
    out.append({"type": "text", "text": "FINDINGS TO SETTLE:\n" + "\n".join(_finding_line(f) for f in found)})
    out += [{"type": "text", "text": _entry_block(e)} for e in ents]
    for ex, pg in _finish_pages(ents, found):
        out += RV.page_blocks(ex, pg, "", images, modes)
    ctx = context(ents, all_ents, delivered)
    return out + ([{"type": "text", "text": ctx}] if ctx else [])
