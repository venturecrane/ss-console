"""A POSITIVE result whose record may say otherwise is re-checked on the
page images, with the neighbouring page shown. $0 to decide.

A negative serology was composed POSITIVE: its "No" sat at the foot of one
page and the result at the head of the next. The text audit, shown the
cited page, can agree with the claim. So a claim that states a test
"positive", "reactive" or "detected" is checked here deterministically: when
the cited pages, or the page on either side, put "No", "Negative", "Not
detected" or "Non-reactive" within ``NEAR`` characters of a word the claim
names beside its result (page breaks joined, so a negation across the break
counts), the claim is routed to the IMAGE audit with the neighbouring page
images and a note saying where the negation is. It is never answered here:
the auditor reads the images and decides, so a flag costs one image check
and never a wrong verdict.

Best effort by design, like ``handwritten_evidence``: no page text, no flag.
"""

from __future__ import annotations

import re
from typing import Any, Callable

#: Characters between a test word and a negation that count as beside it.
NEAR = 60
POSITIVE = re.compile(r"\b(positive|reactive|detected)\b", re.I)
_DENIED = re.compile(r"(?:\bnot|\bnon|\bno|\bnegative)[\s-]*$", re.I)
NEGATION = re.compile(r"\b(?:no|negative|neg|not\s+detected|non-?\s?reactive|none\s+detected)\b", re.I)
_WORD = re.compile(r"[A-Za-z][A-Za-z0-9-]{1,}")
_STOP = frozenset(
    """was were is are be been the and for with result results resulted test tested testing tests came back
    returned showed shows show revealed reveals noted lab labs laboratory which that this patient her his
    also screen screening value values level levels positive reactive detected found finding findings
    on of in at to as a an by from per recorded record records reported report reports""".split()
)
CITE_TAIL = re.compile(r"\((?:Exhibit|FILE:)[^()]*\)")

PageText = Callable[[int, int], "str | None"]


def tested_words(claim: str) -> list[str]:
    """The words a claim names beside each affirmative result: up to four
    words before "positive" and, for "positive for X", up to four after."""
    text = CITE_TAIL.sub("", claim)
    out: list[str] = []
    for m in POSITIVE.finditer(text):
        if _DENIED.search(text[: m.start()]):
            continue
        before = _WORD.findall(text[max(0, m.start() - 60) : m.start()])[-4:]
        after = _WORD.findall(text[m.end() : m.end() + 60])
        after = after[1:5] if after and after[0].lower() == "for" else []
        out += [w for w in before + after if w.lower() not in _STOP and len(w) >= 2]
    return list(dict.fromkeys(out))


def negated_beside(words: list[str], text: str) -> str | None:
    """The first test word in `text` with a negation within NEAR characters
    of it (whitespace collapsed), or None."""
    flat = re.sub(r"\s+", " ", text)
    for w in words:
        for m in re.finditer(rf"\b{re.escape(w)}\b", flat, re.I):
            window = flat[max(0, m.start() - NEAR) : m.end() + NEAR]
            if NEGATION.search(window):
                return w
    return None


def flag(c: dict[str, Any], page_text: PageText) -> dict[str, Any] | None:
    """{word, pages, adjacent} when claim `c` states a positive result that
    its cited pages or their neighbours put a negation beside, else None."""
    words = tested_words(str(c.get("claim") or ""))
    if not words:
        return None
    ex = int(c["exhibit"])
    pages = [int(p) for p in c.get("pages") or []]
    if not pages:
        return None
    npages = int(c.get("npages") or max(pages) + 1)
    span = [p for p in range(min(pages) - 1, max(pages) + 2) if 1 <= p <= npages]
    texts = [page_text(ex, p) or "" for p in span]
    word = negated_beside(words, "\n".join(texts))
    if word is None:
        return None
    return {"word": word, "pages": pages, "adjacent": [p for p in span if p not in pages]}


def note(f: dict[str, Any]) -> str:
    """The instruction sent beside the images of a flagged claim."""
    adj = ", ".join(f"p.{p}" for p in f["adjacent"]) or "none"
    return (
        f"RE-CHECK A POSITIVE RESULT. The record text puts a negation (No, Negative, Not detected, Non-reactive) "
        f"beside '{f['word']}', possibly across a page break. The images after this note are the neighbouring "
        f"page(s) of the same exhibit ({adj}), shown for context only. Read the result for '{f['word']}' as the "
        f"record prints it, across the break; the claim's positive result is SUPPORTED only if the record states "
        f"that result positive."
    )


def evidence(rnd: Any, c: dict[str, Any], pdfs: dict[int, Any], render_page: Callable[[int, int], Any]) -> list:
    """The content blocks to add to claim `c`'s image audit: the note and the
    neighbouring page images when the claim is flagged, else none."""
    from .handwritten_evidence import Evidence
    from .claims import body_of

    with rnd._lock:
        ev = rnd.__dict__.get("neg_evidence")
        if ev is None:
            body = body_of(rnd.paths.doc.read_text(encoding="utf-8"))
            ev = rnd.__dict__["neg_evidence"] = Evidence(rnd.paths.slug_dir, rnd.paths.unit, body)
    f = flag(c, ev.page)
    if f is None or int(c["exhibit"]) not in pdfs:
        return []
    imgs = [b for b in (render_page(int(c["exhibit"]), p) for p in f["adjacent"]) if b]
    return [{"type": "text", "text": note(f)}, *imgs]


def index_page_text(index: Any) -> PageText:
    """A PageText over a text-mode PageIndex (the plan's own index)."""

    def page(exhibit: int, p: int) -> str | None:
        try:
            hit = index.resolve(exhibit, p)
            seg = index.segments(hit[0]).get(hit[1]) if hit else None
        except Exception:  # noqa: BLE001 - an unreadable transcript is no flag, never a failed plan
            return None
        return seg[0] if seg else None

    return page
