"""Gate 2c: a quotation's page cite names the page its words are on.

The gate's 2a test asks only whether a quotation's words exist somewhere in
the corpus, and 2b tests a deposition's page:line. Neither tests a document
pin: a first mediation brief (2026-11-04) carried ten wrong page cites, seven
of them a quotation pinned to the first page of a whole encounter the digest
had cited as a range. The extracted text already carries ``[p.N]`` markers
(``demand/preflight.extract_one``, and one per transcribed page), so for every
quotation followed by ``(FILE: <name>, p. N)`` (or ``pp. N-M``, ``p. 3, p. 5``)
this module reads the cited page(s) of that file and tests the quotation's
words there, with no model call.

Outcomes, per quotation:

* ``ok``: the words are on the cited page(s);
* ``moved``: not there, but on exactly ONE other page (or one pair of
  consecutive pages, for a quotation that runs over a page break) of the same
  file. ``repair`` rewrites the pin to that page, said in the notes;
* ``wrong_page``: not on the cited page(s) and on more than one other page, or
  on none (a refusal: which page was meant is not code's to guess);
* ``wrong_file``: the cited file does not carry the words anywhere (a refusal);
* ``unresolved``: the cited name matches no corpus document, or more than one
  (a refusal);
* ``untestable``: fail-open, explicit and counted: the file has no page
  markers (an email body, a Word file) and the cite names a page past 1, or
  the quotation is one 2a already refuses (2a owns it).

Every outcome is counted in the gate record (``pins``), so a fail-open run is
visible, never silent.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import grounding, render

GATE = "2c"
#: A document cite: FILE: <name>, then its pages. The name runs lazily to the
#: first ", p." so a name carrying commas or parentheses still resolves.
_CITE = re.compile(
    r"FILE:\s*(?P<name>[^;\n]+?),\s*(?P<pages>pp?\.\s*\d+(?:\s*[-–]\s*\d+)?"
    r"(?:\s*,\s*(?:pp?\.\s*)?\d+(?:\s*[-–]\s*\d+)?)*)"
)
_RANGE = re.compile(r"(\d+)(?:\s*[-–]\s*(\d+))?")
#: A deposition page:line cite, which 2b owns. One that comes first after a
#: quotation means the quotation is not this gate's.
_PAGE_LINE = re.compile(r"\b\d{1,4}:\d{1,3}\b")
_MARK = re.compile(r"^\s*\[p\.(\d+)\]")
#: How far after a quotation its cite may sit (the paragraph caps it too).
WINDOW = 400
_ELLIPSIS = re.compile(r"\.\s*\.\s*\.")


@dataclass
class Pin:
    quote: str
    line: int
    name: str
    cited: list[tuple[int, int]]
    #: Offsets of the pages text ("p. 4") in the document, for the repair.
    span: tuple[int, int]
    status: str = "ok"
    resolved: str | None = None
    found: list[tuple[int, int]] = field(default_factory=list)
    why: str = ""

    def pages_text(self, ranges: list[tuple[int, int]]) -> str:
        return ", ".join(f"p. {a}" if a == b else f"pp. {a}-{b}" for a, b in ranges)


def pages(text: str) -> dict[int, str]:
    """A source's text by page, from its ``[p.N]`` markers; one with none is
    page 1 (an email body, a Word file)."""
    out: dict[int, list[str]] = {}
    cur: int | None = None
    for ln in text.splitlines():
        m = _MARK.match(ln)
        if m:
            cur = int(m.group(1))
            out.setdefault(cur, [])
            rest = ln[m.end() :].strip()
            if rest and not rest.startswith("(machine transcription)"):
                out[cur].append(rest)
            continue
        if cur is not None:
            out[cur].append(ln)
    if not out:
        return {1: text}
    return {n: "\n".join(v) for n, v in out.items()}


def has_markers(text: str) -> bool:
    return any(_MARK.match(ln) for ln in text.splitlines())


def parse_pages(s: str) -> list[tuple[int, int]]:
    out = []
    for m in _RANGE.finditer(s):
        a = int(m.group(1))
        b = int(m.group(2)) if m.group(2) else a
        out.append((min(a, b), max(a, b)))
    return out


def _segments(normalized: str) -> list[str]:
    segs = [s.strip() for s in _ELLIPSIS.split(normalized)]
    return [s for s in segs if len(s.split()) >= 3] or [normalized]


def _on(ck: Any, quote: Any, text: str, cache: dict[str, Any] | None = None) -> bool:
    """The quotation's words (each elided segment, in order) in ``text``, by
    the gate's own normalization and its tolerated variants."""
    cache = {} if cache is None else cache  # a page is read once per check
    doc = cache.get(text)
    if doc is None:
        doc = cache[text] = ck.SourceDoc(Path("page"), text)
    segs = _segments(quote.normalized)
    if len(segs) == 1:
        return ck.locate_quote(quote, [doc]) is not None
    for view in doc.views:
        cursor, ok = 0, True
        for seg in segs:
            hit = next(
                (view.text.find(v, cursor) for v in ck.quote_variants(seg) if view.text.find(v, cursor) >= 0), -1
            )
            if hit < 0:
                ok = False
                break
            cursor = hit
        if ok:
            return True
    return False


def _join(by_page: dict[int, str], a: int, b: int) -> str | None:
    if any(n not in by_page for n in range(a, b + 1)):
        return None
    return "\n".join(by_page[n] for n in range(a, b + 1))


def _cite_after(body: str, quote: Any, next_start: int) -> tuple[str, str, tuple[int, int]] | None:
    """(name, pages text, pages span) of the FILE cite that follows a
    quotation in its paragraph, before the next quotation; None when the
    first cite there is a page:line one, or there is none."""
    start = quote.start + len(quote.raw)
    end = min(start + WINDOW, next_start if next_start > start else len(body), len(body))
    window = body[start:end]
    para = window.find("\n\n")
    if para >= 0:
        window = window[:para]
    m = _CITE.search(window)
    if not m:
        return None
    pl = _PAGE_LINE.search(window)
    if pl and pl.start() < m.start():
        return None
    return m.group("name").strip(), m.group("pages"), (start + m.start("pages"), start + m.end("pages"))


def _resolve(name: str, corpus: dict[str, str]) -> str | None:
    if name in corpus:
        return name
    hit = grounding.find_file(name, corpus)
    if hit:
        return hit
    flat = re.sub(r"[\W_]+", " ", name).strip().casefold()
    hits = [n for n in corpus if re.sub(r"[\W_]+", " ", render._stem(n)).strip().casefold() == flat]
    return hits[0] if len(hits) == 1 else None


def _checker() -> Any:
    from ..demand import quotefix

    return quotefix.checker()


def check(md: str, named: list[tuple[str, str]]) -> tuple[list[Pin], dict[str, int]]:
    """Every FILE-cited quotation's pin, tested; and the counts."""
    ck = _checker()
    seen: dict[str, Any] = {}
    corpus = {n: t for n, t in named}
    body = ck.Draft(Path("draft"), md).body_text
    all_docs = [ck.SourceDoc(Path(n), t) for n, t in named]
    quotes = ck.extract_quotes(body)
    out: list[Pin] = []
    for i, q in enumerate(quotes):
        nxt = quotes[i + 1].start if i + 1 < len(quotes) else len(body)
        c = _cite_after(body, q, nxt)
        if c is None:
            continue
        name, ptxt, span = c
        pin = Pin(q.normalized, q.line, name, parse_pages(ptxt), span)
        out.append(pin)
        if ck.locate_quote(q, all_docs) is None and ck.check_elision(q, all_docs) is None:
            pin.status, pin.why = (
                "untestable",
                "2a refuses this quotation; its pin is tested once it is the record's words",
            )
            continue
        got = _resolve(name, corpus)
        if got is None:
            pin.status, pin.why = "unresolved", "the cited name matches no document in the file, or more than one"
            continue
        pin.resolved = got
        text = corpus[got]
        by_page = pages(text)
        marked = has_markers(text)
        if not marked and any(b > 1 for _a, b in pin.cited):
            pin.status, pin.why = "untestable", "the document carries no page markers"
            continue
        if any((j := _join(by_page, a, b)) is not None and _on(ck, q, j, seen) for a, b in pin.cited):
            continue
        singles = [(n, n) for n in sorted(by_page) if _on(ck, q, by_page[n], seen)]
        pairs = []
        if not singles:
            pairs = [
                (n, n + 1)
                for n in sorted(by_page)
                if n + 1 in by_page and _on(ck, q, _join(by_page, n, n + 1) or "", seen)
            ]
        pin.found = singles or pairs
        if len(pin.found) == 1:
            pin.status = "moved"
        elif pin.found:
            pin.status = "wrong_page"
            pin.why = "the words are on more than one other page of that document"
        elif _on(ck, q, text, seen):
            pin.status, pin.why = "wrong_page", "the words are in that document but not on any page or page pair"
        else:
            pin.status, pin.why = "wrong_file", "the cited document does not carry these words"
    counts: dict[str, int] = {"checked": len(out)}
    for p in out:
        counts[p.status] = counts.get(p.status, 0) + 1
    return out, counts


REFUSE = ("wrong_page", "wrong_file", "unresolved")


def findings(pins: list[Pin]) -> tuple[list[str], list[str]]:
    """(refusals, infos) in the record check's line shape. A ``moved`` pin is a
    refusal until ``repair`` moves it: the document as it stands is wrong."""
    refusals, infos = [], []
    for p in pins:
        cited = p.pages_text(p.cited)
        if p.status in REFUSE:
            on = f"; found on {p.pages_text(p.found)}" if p.found else ""
            refusals.append(
                f'[{GATE}] pin {cited} of "{p.name}" does not carry the quoted words ({p.why}{on}): "{p.quote}"'
            )
        elif p.status == "moved":
            refusals.append(
                f'[{GATE}] pin {cited} of "{p.name}" does not carry the quoted words; they are on '
                f'{p.pages_text(p.found)}: "{p.quote}"'
            )
        elif p.status == "untestable":
            infos.append(f'[{GATE}] pin {cited} of "{p.name}" not tested ({p.why}): "{p.quote[:120]}"')
    return refusals, infos


def repair(md: str, named: list[tuple[str, str]]) -> tuple[str, list[str]]:
    """The document with every ``moved`` pin rewritten to its one page, and a
    line per repair for the attorney notes. Nothing else is touched."""
    pins, _counts = check(md, named)
    log = []
    for p in sorted((p for p in pins if p.status == "moved"), key=lambda p: -p.span[0]):
        new = p.pages_text(p.found)
        old = md[p.span[0] : p.span[1]]
        md = md[: p.span[0]] + new + md[p.span[1] :]
        log.append(f'pin {old} of "{p.name}" moved to {new}, where the quoted words are: "{p.quote[:100]}"')
    return md, list(reversed(log))


def cited_text(named: list[tuple[str, str]]) -> Any:
    """For quotefix: ``f(md, quote)`` is the text of the document the
    quotation's FILE cite names (its cited pages when they exist, else the
    whole document), or None."""
    corpus = {n: t for n, t in named}

    def f(md: str, q: Any) -> str | None:
        ck = _checker()
        body = ck.Draft(Path("draft"), md).body_text
        quotes = ck.extract_quotes(body)
        nxt = next((x.start for x in quotes if x.start > q.start), len(body))
        c = _cite_after(body, q, nxt)
        got = _resolve(c[0], corpus) if c else None
        if c is None or got is None:
            return None
        text = corpus[got]
        if not has_markers(text):
            return text
        by_page = pages(text)
        parts = [j for a, b in parse_pages(c[1]) if (j := _join(by_page, a, b)) is not None]
        return "\n".join(parts) if parts else text

    return f
