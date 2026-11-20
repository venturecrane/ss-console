"""The uncited-page reader's request and the parse of its reply. $0 here; the
paid call is ``stages/update_uncited``'s.

ONE REQUEST per run of adjacent uncited pages of one document
(``stages/update_uncited_plan.RUN_PAGES`` at most): the pages as the entry
reader shows a page (``review_pages.page_blocks``: its text layer where the
page rule trusts one, else its picture), and for context the date and
provider lines of the entries that cite the same document.

THE REPLY is one JSON object, ``{"pages": [...], "complete": true}``, with
exactly one row per page shown, each one of CLASSES:

    {"page": 5, "class": "continuation", "entry": "03/14/2019 | Example Clinic"}
    {"page": 6, "class": "non_record"}
    {"page": 7, "class": "duplicate", "of": 6}
    {"page": 4, "class": "dated_record", "dates": ["03/14/2019"], "kinds": ["test"]}
    {"page": 8, "class": "unsure"}

A ``dated_record`` names at least one date and at least one of KINDS (the
measured misses: a vaccine or injection given, a test placed, read or
resulted, a visit listed by date on a log or statement). Anything else (a page missing, named twice or not shown, a class not listed,
``complete`` not true) is UNPARSED, and the stage asks once more. Code acts
only on the class: a ``dated_record`` page goes to the extra writing pass
(``stages/update_uncited_write``), shown the dates and kinds read here; an
``unsure`` page is recorded and goes nowhere.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from ..prompts import load
from . import review_pages as RV

PROMPT = "uncited-read-system"
CONTINUATION, NON_RECORD, DUPLICATE, DATED, UNSURE = (
    "continuation",
    "non_record",
    "duplicate",
    "dated_record",
    "unsure",
)
CLASSES = (CONTINUATION, NON_RECORD, DUPLICATE, DATED, UNSURE)
KINDS = ("vaccine_or_injection", "test", "visit_on_log")
_DATE = re.compile(r"\d{2}/\d{2}/\d{4}")


class Unparsed(ValueError):
    """A reply the stage cannot act on (asked once more, then held)."""


@dataclass(frozen=True)
class Run:
    exhibit: int
    pages: tuple[int, ...]

    @property
    def name(self) -> str:
        """The answer files' name (``entry_audit_io``): ``x7-p4-9``."""
        return f"x{self.exhibit}-p{self.pages[0]}-{self.pages[-1]}"


@dataclass(frozen=True)
class Answer:
    page: int
    klass: str
    entry: str = ""
    of: int | None = None
    dates: tuple[str, ...] = ()
    kinds: tuple[str, ...] = ()


def request(run: Run, context: list[str], cfg: Any, images: RV.Images, modes: Any) -> tuple[str, list[dict[str, Any]]]:
    """(system, content) for one run. A page with neither text nor picture is
    shown by its label alone and still needs its class."""
    lines = "\n".join(f"- {c}" for c in context) or "- none: no entry cites this document yet"
    head = f"DOCUMENT: Exhibit {run.exhibit}\nENTRIES THAT CITE OTHER PAGES OF IT:\n{lines}\n"
    content: list[dict[str, Any]] = [{"type": "text", "text": head}]
    for p in run.pages:
        blocks = RV.page_blocks(run.exhibit, p, "", images, modes)
        content += blocks or [{"type": "text", "text": f"Exhibit {run.exhibit} p. {p}: (no text and no picture)"}]
    shown = ", ".join(str(p) for p in run.pages)
    content.append({"type": "text", "text": f"PAGES SHOWN: Exhibit {run.exhibit} pp. {shown}. Class every one."})
    return load(PROMPT, cfg), content


def _json(text: str) -> dict[str, Any]:
    body = text.strip()
    if body.startswith("```"):
        body = re.sub(r"^```\w*\s*|\s*```$", "", body)
    start, end = body.find("{"), body.rfind("}")
    if start < 0 or end <= start:
        raise Unparsed("no JSON object")
    try:
        got = json.loads(body[start : end + 1])
    except ValueError as exc:
        raise Unparsed(f"not JSON ({exc})") from exc
    if not isinstance(got, dict):
        raise Unparsed("not a JSON object")
    return got


def _answer(row: Any, shown: set[int]) -> Answer:
    if not isinstance(row, dict):
        raise Unparsed("a page row is not an object")
    page, klass = row.get("page"), row.get("class")
    if not isinstance(page, int) or isinstance(page, bool) or page not in shown:
        raise Unparsed(f"page {page!r} was not shown")
    if klass not in CLASSES:
        raise Unparsed(f"page {page}: class {klass!r} is not one of {CLASSES}")
    if klass == CONTINUATION:
        entry = row.get("entry")
        if not isinstance(entry, str) or not entry.strip():
            raise Unparsed(f"page {page}: a continuation names no entry")
        return Answer(page, klass, entry=entry.strip())
    if klass == DUPLICATE:
        of = row.get("of")
        if not isinstance(of, int) or isinstance(of, bool) or of == page:
            raise Unparsed(f"page {page}: a duplicate names no other page")
        return Answer(page, klass, of=of)
    if klass == DATED:
        return _dated(row, page)
    return Answer(page, klass)


def _dated(row: dict[str, Any], page: int) -> Answer:
    dates, kinds = row.get("dates"), row.get("kinds")
    if not isinstance(dates, list) or not dates or not all(isinstance(d, str) and _DATE.fullmatch(d) for d in dates):
        raise Unparsed(f"page {page}: a dated record names no MM/DD/YYYY date")
    if not isinstance(kinds, list) or not kinds or not all(k in KINDS for k in kinds):
        raise Unparsed(f"page {page}: a dated record names no kind of {KINDS}")
    return Answer(page, DATED, dates=tuple(dates), kinds=tuple(dict.fromkeys(kinds)))


def parse(text: str, run: Run) -> dict[int, Answer]:
    """{page: its answer}, one for every page of the run, or ``Unparsed``."""
    got = _json(text)
    if got.get("complete") is not True:
        raise Unparsed("complete is not true")
    rows = got.get("pages")
    if not isinstance(rows, list):
        raise Unparsed("no pages list")
    shown = set(run.pages)
    out: dict[int, Answer] = {}
    for row in rows:
        a = _answer(row, shown)
        if a.page in out:
            raise Unparsed(f"page {a.page} is answered twice")
        out[a.page] = a
    missing = sorted(shown - set(out))
    if missing:
        raise Unparsed(f"page(s) {missing} have no class")
    return out


def parses(run: Run) -> Any:
    def ok(text: str) -> bool:
        try:
            parse(text, run)
        except Unparsed:
            return False
        return True

    return ok
