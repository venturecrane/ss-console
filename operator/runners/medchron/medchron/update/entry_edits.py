"""The finisher's two edits that change which entries the update has. $0.

WHY (2026-10-14). On a real update the finisher declined findings no action
could settle:

* a procedure at a separate facility, from its own exhibit, folded into an
  office visit's entry ("No available action creates an entry"): ``new_entry``
  builds the procedure's own entry from its cited pages;
* an added entry that repeats a DELIVERED visit but whose new pages carry
  orders found nowhere else ("delivered entries cannot be edited, so dropping
  it would lose content"): ``append_to_delivered`` keeps exactly that content,
  as an "Additional findings from records received <date>" block under the
  delivered entry's own date and provider line. The block stays an added
  entry of the audit document (every sentence audited), and assemble folds
  it into the delivered entry (``fold_into_delivered``); the delivered text
  itself is never changed.

Both are checked here: every paragraph ends with one citation to a new
exhibit; a new entry's date is stated by the text of the page it names; a fold
carries every value of the added entry that the delivered entry lacks.
"""

from __future__ import annotations

import re
import time
from datetime import date

from ..audit import claims as CL
from . import body as B, handwritten as H, review_repair as RR
from .values import numbers

FOLD = "Additional findings from records received"
_FOLD_LINE = re.compile(rf"^{re.escape(FOLD)} \d{{2}}/\d{{2}}/\d{{4}}$")


def _paras(text: str, headings: set[str]) -> list[str]:
    """The paragraphs of `text` that are not a heading standing alone."""
    out = []
    for p in (x.strip() for x in text.split("\n\n")):
        lines = [ln for ln in p.split("\n") if ln.strip()]
        while lines and lines[0].strip() in headings:
            lines = lines[1:]
        if lines:
            out.append("\n".join(lines))
    return out


def _cited(paras: list[str], keep: set[int]) -> str:
    """'' when every paragraph ends with one citation to a new exhibit and is
    long enough for the audit to grade."""
    for p in paras:
        cites = list(CL.CITE.finditer(p))
        if not cites or p[cites[-1].end() :].strip():
            return "a paragraph does not end with its citation"
        if any(int(m.group(1)) not in keep for m in cites):
            return "a paragraph cites no new exhibit"
        if not CL.graded(p[: cites[-1].start()]):
            return "a paragraph is too short to be audited"
    return ""


def check_new_entry(e: RR.Finding, keep: set[int], headings: set[str], evidence: RR.Evidence | None) -> str:
    """'' when the new entry is grounded: a date line, a provider line, cited
    paragraphs, and a date the page it names states in its text."""
    lines = e.corrected.strip().split("\n")
    if len(lines) < 3 or not B.DATE.match(lines[0]) or "|" not in lines[1]:
        return "a new entry opens with its date line and its provider line"
    why = _cited(_paras("\n".join(lines[2:]), headings), keep)
    if why:
        return why
    if (
        e.exhibit not in keep
        or e.page is None
        or not re.search(rf"\(Exhibit {e.exhibit} - p\. [^)]*\b{e.page}\b", e.corrected)
    ):
        return "a new entry names a page of a new exhibit that it cites"
    m = B.DATE.match(lines[0])
    assert m is not None
    try:
        d = date(int(m.group(3)), int(m.group(1)), int(m.group(2)))
    except ValueError:
        return "the new entry's date is not a date"
    text = evidence(e.exhibit, e.page) if evidence else None
    if not H.on_page(d, text, d.year):
        return f"Exhibit {e.exhibit} p. {e.page} does not state the new entry's date {lines[0][:10]}"
    return ""


def check_fold(e: RR.Finding, ents: list[RR.Entry], delivered: list[str], keep: set[int], headings: set[str]) -> str:
    """'' when an added entry may be folded into the delivered entry its
    `survivor` names: the fold's paragraphs are cited, and with the delivered
    entry they carry every value the added entry has."""
    ent = next((x for x in ents if x.anchor == e.anchor), None)
    k = int(e.survivor[1:]) - 1 if re.fullmatch(r"D\d+", e.survivor) else -1
    if ent is None or not 0 <= k < len(delivered):
        return "a fold names an added entry and the delivered entry (D<k>) it goes under"
    paras = _paras(e.corrected, headings)
    why = "the fold carries no paragraph" if not paras else _cited(paras, keep)
    if why:
        return why
    body = ent.text.split("\n", 2)[2] if ent.text.count("\n") >= 2 else ""
    missing = sorted(numbers(body) - numbers(delivered[k]) - numbers(e.corrected))
    if missing:
        return (
            f"the fold and {e.survivor} do not carry {', '.join(missing[:5])}; content that exists nowhere else is kept"
        )
    return ""


def fold_text(delivered_entry: str, corrected: str, headings: set[str], today: str | None = None) -> str:
    """The added entry a fold becomes: the delivered entry's date and provider
    lines, then the fold block."""
    head = "\n".join(delivered_entry.split("\n")[:2])
    when = today or time.strftime("%m/%d/%Y")
    return f"{head}\n\n{FOLD} {when}\n\n" + "\n\n".join(_paras(corrected, headings))


def apply_folds(
    md: str, edits: list[RR.Finding], ents: list[RR.Entry], delivered: list[str], headings: set[str]
) -> str:
    """Each checked fold, in place of the added entry it replaces."""
    out = md
    for e in edits:
        if e.action != "append_to_delivered" or e.outcome:
            continue
        ent = next(x for x in ents if x.anchor == e.anchor)
        if out.count(ent.text) != 1:
            e.outcome = "the added entry is not found once"
            continue
        out = out.replace(ent.text, fold_text(delivered[int(e.survivor[1:]) - 1], e.corrected, headings), 1)
        e.removed = sorted(numbers(ent.text) - numbers(e.corrected))
        e.outcome = "applied"
    return out


def add_entries(md: str, edits: list[RR.Finding]) -> str:
    """Each checked new entry into the Medical Chronology, in date order."""
    new = [e for e in edits if e.action == "new_entry" and not e.outcome]
    if not new:
        return md
    body = B.sections(md)["## Medical Chronology"]
    at = md.index(body, md.index("## Medical Chronology"))
    pre, ents = B.split_body(body)
    for e in new:
        ents.append(e.corrected.strip())
        e.outcome = "applied"
    ents.sort(key=B.iso)
    out = (pre.rstrip("\n") + "\n\n" if pre.strip() else "\n") + "\n\n".join(ents) + "\n\n"
    return md[:at] + out + md[at + len(body) :]


def is_fold(entry: str) -> bool:
    lines = entry.split("\n")
    return len(lines) > 3 and bool(_FOLD_LINE.match(lines[3].strip()))


def without_fold(entry: str) -> str:
    """The entry above its fold block (the delivered text), or the entry."""
    m = re.search(rf"\n\n{re.escape(FOLD)} \d{{2}}/\d{{2}}/\d{{4}}\n", entry)
    return entry[: m.start()] if m else entry


def fold_into_delivered(base_ents: list[str], new_ents: list[str]) -> tuple[list[str], list[str]]:
    """(delivered entries with each fold block appended under the delivered
    entry whose date and provider lines it carries, the other added entries).
    The delivered text is kept byte for byte; the block follows it."""
    base = list(base_ents)
    rest = []
    for e in new_ents:
        head = "\n".join(e.split("\n")[:2])
        k = next((i for i, d in enumerate(base) if is_fold(e) and "\n".join(d.split("\n")[:2]) == head), None)
        if k is None:
            rest.append(e)
            continue
        base[k] = base[k].rstrip("\n") + "\n\n" + e.split("\n", 3)[3]
    return base, rest
