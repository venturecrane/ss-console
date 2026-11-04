"""CACI instructions for ``authority_fetch.py``, cut from the Judicial
Council's official PDF.

The Judicial Council publishes each edition of the Civil Jury Instructions as
one PDF (about 3,500 pages). The tool fetches it once, converts it to text page
by page, and caches the pages (``caci/<file>.pages.json``); every later
instruction is a cache read.

An instruction is cut from the page its heading begins (``430. Causation:
Substantial Factor`` at the top of a page) to the page before the next
instruction's heading, so it carries the instruction, its Directions for Use
and its Sources and Authority. The running header (``NEGLIGENCE`` / ``CACI No.
430``) is dropped, and each page's printed number is kept in the text as
``[CACI p. 296]`` so a pin cite can be checked. A table-of-contents page, which
also begins with an instruction number, is told apart by carrying many of them.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from authority_common import Ctx, Fetched

#: The current edition. A new edition is a new URL; pass ``--caci-url`` until
#: this constant is updated.
CACI_URL = "https://courts.ca.gov/system/files/file/judicial_council_of_california_civil_jury_instructions_2026.pdf"
_NUMBER = re.compile(r"(?i)\bCACI\s+No\.?\s*((?:VF-)?\d{3,4}[A-Z]?)\b")
_HEADING = re.compile(r"^((?:VF-)?\d{3,4}[A-Z]?)(?:[–-]\d{3,4}[A-Z]?)?\.\s+\S")
_PRINTED = re.compile(r"^\d{1,4}$")
#: More numbered headings than this on one page is a table of contents.
_TOC_HEADINGS = 4
#: No instruction runs longer than this many pages; a longer cut is a parse error.
MAX_PAGES = 40


def number(citation: str) -> str | None:
    m = _NUMBER.search(citation)
    return m.group(1).upper() if m else None


def _lines(page: str) -> list[str]:
    return [ln.strip() for ln in page.splitlines() if ln.strip()]


def _heading(page: str) -> str | None:
    lines = _lines(page)
    if not lines:
        return None
    m = _HEADING.match(lines[0])
    if not m:
        return None
    if sum(1 for ln in lines if _HEADING.match(ln)) > _TOC_HEADINGS:
        return None
    return m.group(1).upper()


def _printed(page: str) -> str:
    for ln in reversed(_lines(page)):
        if _PRINTED.match(ln):
            return ln
    return ""


def _body(page: str, num: str) -> str:
    """The page's text without its running header and printed page number."""
    lines = page.splitlines()
    out, top = [], True
    for ln in lines:
        s = ln.strip()
        if top and (not s or s == f"CACI No. {num}" or (s.isupper() and len(s) < 80 and not _HEADING.match(s))):
            continue
        top = False
        out.append(ln.rstrip())
    while out and (not out[-1].strip() or _PRINTED.match(out[-1].strip())):
        out.pop()
    return "\n".join(out).strip()


def edition(pages: list[str], url: str) -> str:
    for p in pages[:8]:
        m = re.search(r"\b(\d{4}) Edition of CACI\b", p)
        if m:
            return f"CACI {m.group(1)} Edition (Judicial Council of California)"
    m = re.search(r"(\d{4})\.pdf$", url)
    return (
        f"CACI {m.group(1)} Edition (Judicial Council of California)" if m else "CACI (Judicial Council of California)"
    )


def load_pages(ctx: Ctx, url: str = CACI_URL) -> list[str]:
    name = "caci/" + re.sub(r"[^A-Za-z0-9._-]+", "-", url.rsplit("/", 1)[-1]) + ".pages.json"
    p = ctx.cache / name
    if p.is_file():
        return json.loads(p.read_text(encoding="utf-8"))["pages"]
    pdf = ctx.get_ok(url, "the CACI PDF")
    if not pdf.startswith(b"%PDF"):
        raise LookupError("the CACI URL did not answer with a PDF")
    pages = ctx.pdf(pdf)
    body = {"url": url, "sha256": hashlib.sha256(pdf).hexdigest(), "pages": pages}
    ctx.cached(name).write_text(json.dumps(body), encoding="utf-8")
    return pages


def cut(pages: list[str], num: str) -> tuple[str, int, list[str]]:
    """(text, first physical page index, printed page numbers) for instruction ``num``."""
    starts = [i for i, pg in enumerate(pages) if _heading(pg) == num]
    if len(starts) != 1:
        raise LookupError(f"CACI No. {num} heads {len(starts)} pages of the PDF, not exactly one")
    first = starts[0]
    end = first + 1
    while end < len(pages) and _heading(pages[end]) is None:
        end += 1
    if end - first > MAX_PAGES:
        raise LookupError(f"CACI No. {num} would run {end - first} pages: the next heading was not found")
    chunks, printed = [], []
    for pg in pages[first:end]:
        n = _printed(pg)
        printed.append(n)
        b = _body(pg, num)
        if b:
            chunks.append(f"[CACI p. {n}]\n{b}" if n else b)
    return "\n\n".join(chunks), first, printed


def fetch(citation: str, ctx: Ctx, url: str = CACI_URL) -> Fetched:
    num = number(citation)
    if num is None:
        raise LookupError("no CACI instruction number in the citation")
    pages = load_pages(ctx, url)
    text, first, printed = cut(pages, num)
    shown = [p for p in printed if p]
    meta: dict[str, Any] = {"edition": edition(pages, url)}
    if shown:
        meta["printed_pages"] = shown[0] if len(shown) == 1 else f"{shown[0]}-{shown[-1]}"
    return Fetched(f"{url}#page={first + 1}", text, {k: str(v) for k, v in meta.items()})
