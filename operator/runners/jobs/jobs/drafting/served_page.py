"""The served-set finder's reading of a page by code alone: what a paper is
(a served set, our response, a form, a scan), its SET numbers, its titles, and
whether a value or a date is on it. No model, no I/O. Split from
``served_find.py`` at the module-size ceiling; ``served_find`` re-exports every
name it had.
"""

from __future__ import annotations

import datetime as dt
import re

from . import provenance, served as served_mod, served_papers as papers
from .served_docs import Doc
from .served_slice import ASKING as _ASKING, RESPONDING as _RESPONDING, TITLE as _TITLE, TITLES as _TITLES

#: A Word file's extracted text: paragraphs, then tables (``matter/word.record_docx_text``).
WORD_EXTS = provenance.WORD_EXTS
#: A SET line: "SET ONE", "SET NO.: 2", or "SET NO.:" with its value on the next
#: non-blank line (measured 2026-11-20: every real text set prints it below).
_SET_LINE = re.compile(r"(?im)^[ \t]*SET\b(?:[ \t]*(NO\b\.?|NUMBER\b))?[ \t]*(:)?[ \t|]*(.*)$")
_SET_VALUE = re.compile(r"^[ \t|]*([A-Za-z]+|\d+)\b")
_FORM = (re.compile(r"DISC-001"), re.compile(r"(?i)\bPage\s+\d+\s+of\s+8\b"))
#: A response paper's title. "RESPONSE TO REQUEST FOR PRODUCTION NO. 3:" is a
#: served set's blank response line, not a title (``(?!\s+NO\.)``).
_RESPONSE = {
    k: re.compile(
        r"\b(?:RESPONSES?|ANSWERS?)(?:\s+AND\s+OBJECTIONS?)?\s+TO\s+(?:[A-Z0-9.,'’&-]+\s+){0,8}?" + v + r"(?!\s+NO\.)"
    )
    for k, v in _TITLES.items()
}
_NAME_VOCAB = re.compile(
    r"(?i)\b(?:interrogator(?:y|ies)|rogs?|srogs?|frogs?|requests?\s+for\s+(?:production|admissions?)|"
    r"demands?\s+for\s+(?:production|inspection)|rf[ap]s?|rpds?|discovery)\b"
)
_WORDS = {w.lower(): i for i, w in enumerate("One Two Three Four Five Six Seven Eight Nine Ten".split(), 1)}


# ---- code's own reading of a page ------------------------------------------------------------


def norm(s: str) -> str:
    return " ".join(str(s or "").split()).casefold()


def _value(rx: re.Pattern[str], text: str) -> str:
    m = rx.search(text)
    if not m:
        return ""
    if m.group(1).strip(" \t|"):
        return m.group(1).strip(" \t|")
    rest = [ln.strip(" \t|") for ln in text[m.end() :].splitlines() if ln.strip(" \t|")]
    return rest[0] if rest else ""


def set_numbers(text: str) -> list[int]:
    """The numbers the page's SET lines carry ("SET ONE", "SET NO.: 2")."""
    out = []
    for m in _SET_LINE.finditer(text):
        rest = m.group(3).strip(" \t|")
        if not rest and (m.group(1) or m.group(2)):  # the value is on the next non-blank line
            rest = next((ln.strip(" \t|") for ln in text[m.end() :].splitlines() if ln.strip(" \t|")), "")
        v = _SET_VALUE.match(rest)
        w = v.group(1).lower() if v else ""
        n = int(w) if w.isdigit() else _WORDS.get(w)
        out += [n] if n else []
    return out


def is_form(d: Doc) -> bool:
    return any(all(rx.search(t) for rx in _FORM) for t in d.pages.values())


def title_type(text: str) -> str | None:
    hits = [(m.start(), k) for k, rx in _TITLE.items() for m in [rx.search(text)] if m]
    return min(hits)[1] if hits else None


def response_type(text: str) -> str | None:
    hits = [(m.start(), k) for k, rx in _RESPONSE.items() for m in [rx.search(text)] if m]
    return min(hits)[1] if hits else None


def classify(d: Doc, ev: provenance.Evidence | None = None) -> tuple[str, str | None]:
    """(``served`` | ``response`` | ``other_set`` | ``scan`` | ``none``, set type), by code only."""
    head = d.first
    responding = _RESPONDING.search(head)
    lined = bool(_ASKING.search(head) and responding)
    # The caption and its party block. A Word file's text puts its tables (the
    # caption, and the title in it) AFTER the body paragraphs, so a Word paper's
    # title is read anywhere on its first page (measured 2026-11-20: 12 of 12
    # of our own .docx response papers were typed as served sets without this).
    title_area = provenance.title_area(head, d.ext)
    if lined and response_type(title_area):
        return "response", response_type(title_area)
    if is_form(d):
        return "served", "form_interrogatories"
    if lined:
        t = title_type(head)
        if t:
            return "served", t
        # No title in the capitals a caption prints: ours only on strong evidence
        # (``provenance.whose``, 2026-11-24); else a served set of the kind its
        # title or name reads, or a kind code cannot read (``other_set``).
        w = provenance.whose(d.name, d.folder, head, ev=ev, doc_id=d.id, ext=d.ext, sha=d.sha)
        if w.side == provenance.OURS:
            return "response", w.kind
        return ("served", w.kind) if w.kind else ("other_set", None)
    if len(head.strip()) < 40 and _NAME_VOCAB.search(provenance.words(d.name)):
        return ("none" if papers.ours_written(d.name, d.folder) else "scan"), None
    return "none", None


def ours(name: str, clients: list[str]) -> bool:
    problems: list[str] = []
    served_mod._ours(0, name, clients, problems)
    return bool(name.strip()) and not problems


def on_page(value: str, text: str) -> bool:
    return bool(norm(value)) and norm(value) in norm(text)


def date_on_page(iso: str, text: str) -> bool:
    try:
        d = dt.date.fromisoformat(iso)
    except (TypeError, ValueError):
        return False
    month = d.strftime("%B")
    forms = [iso, f"{d.month}/{d.day}/{d.year}", f"{d.month:02d}/{d.day:02d}/{d.year}", f"{month} {d.day}, {d.year}",
             f"{month[:3]}. {d.day}, {d.year}", f"{d.day} {month} {d.year}"]  # fmt: skip
    t = norm(text)
    return any(norm(f) in t for f in forms)
