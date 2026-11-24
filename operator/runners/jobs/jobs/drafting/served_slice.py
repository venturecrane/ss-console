"""A served set's requests, sliced and numbered by code (the ``find`` stage's half).

Split from ``served_find`` (2026-11-20) when the replay's fixes took that module
past the 500-line ceiling; the finder calls :func:`slice_requests` with a
document's pages. Every shape here was measured on real served sets: labelled
"<label> NO. n" requests, a bare "n." run under the section heading (the number
alone on its line with the text below in 4 of 5), blank "RESPONSE:" lines,
pleading-paper margin numbers, and a running header or footer on every page.
"""

from __future__ import annotations

import re

from .served import SET_TYPES

RESPONDING = re.compile(r"(?im)^[ \t]*RESPONDING[ \t]+PART(?:Y|IES)[ \t]*:[ \t]*(.*)$")
#: The asking party's line (measured 2026-11-20: half the real sets say REQUESTING).
ASKING = re.compile(r"(?im)^[ \t]*(?:PROPOUNDING|REQUESTING|DEMANDING)[ \t]+PART(?:Y|IES)[ \t]*:[ \t]*(.*)$")
#: The closed title list, upper case as a caption prints it (never body prose).
TITLES = {
    "form_interrogatories": r"FORM\s+INTERROGATOR(?:Y|IES)",
    "special_interrogatories": r"SPECIAL(?:LY\s+PREPARED)?\s+INTERROGATOR(?:Y|IES)",
    "requests_for_production": r"(?:REQUESTS?|DEMANDS?)\s+FOR\s+(?:THE\s+)?(?:PRODUCTION|INSPECTION)",
    "requests_for_admission": r"REQUESTS?\s+FOR\s+ADMISSIONS?",
}
TITLE = {k: re.compile(v) for k, v in TITLES.items()}
_LABELLED = re.compile(
    r"(?im)^[\W_]*(?:SPECIAL(?:LY\s+PREPARED)?\s+)?(?:INTERROGATORY|REQUEST\s+FOR\s+(?:PRODUCTION(?:\s+OF\s+DOCUMENTS)?"
    r"|ADMISSION)|DEMAND\s+FOR\s+(?:PRODUCTION|INSPECTION))\s+NO\.?\s*(\d+)\s*[:.)]?"
)
#: A bare request number: "15. Text" or "15." alone on its line with the text
#: below (measured 2026-11-20: 4 of 5 real text sets). A space or the line end
#: must follow the period, so a split citation ("4.1.") never counts.
_BARE = re.compile(r"(?m)^[ \t]*(\d{1,3})[ \t]*[.)](?:[ \t]+(?=\S)|[ \t]*$)")
_END = re.compile(r"(?m)^[ \t]*(?:DATED|Dated)\b|^[ \t]*PROOF\s+OF\s+SERVICE\b")
#: A blank response line some served sets print after each request.
_BLANK_RESPONSE = re.compile(r"(?im)^[\W_]*RESPONSE(?:\s+TO\s+[^\n:]{0,80})?\s*:\s*$\n?")
_MARGIN = re.compile(r"(?m)^[ \t]*\d{1,2}[ \t]*$\n?")


def running_lines(pages: dict[int, str]) -> set[str]:
    """The lines a paper repeats on most of its pages: its running header and
    footer (measured 2026-11-20: the paper's title in capitals, which landed
    inside every request crossing a page). Never a request's own number line."""
    if len(pages) < 3:
        return set()
    seen: dict[str, int] = {}
    for t in pages.values():
        for ln in {x.strip() for x in t.splitlines()}:
            if sum(c.isalpha() for c in ln) >= 4 and not (_LABELLED.match(ln) or _BARE.match(ln)):
                seen[ln] = seen.get(ln, 0) + 1
    return {ln for ln, n in seen.items() if n * 2 > len(pages)}


def _heads_a_run(lines: list[str], i: int) -> bool:
    nxt = next((x for x in lines[i + 1 :] if x.strip()), "")
    m = _BARE.match(nxt)
    return bool(m and m.group(1) == "1")


def _clean(text: str, running: set[str] | None = None) -> str:
    if running:
        lines = text.split("\n")
        text = "\n".join(x for i, x in enumerate(lines) if x.strip() not in running or _heads_a_run(lines, i))
    return _MARGIN.sub("", _BLANK_RESPONSE.sub("", text))


def _bare_start(body: str, kind: str) -> int:
    """Where the bare-numbered run begins: past the last section heading (a
    short title line) that a "1." line follows; else the start of the body."""
    best = 0
    for m in TITLE[kind].finditer(body):
        line_end = body.find("\n", m.end())
        line = body[body.rfind("\n", 0, m.start()) + 1 : line_end if line_end >= 0 else len(body)]
        first = _BARE.search(body, m.end())
        if len(line.strip()) <= 80 and "SET" not in line.upper().split() and first and first.group(1) == "1":
            best = line_end if line_end >= 0 else len(body)
    return best


def slice_requests(pages: dict[int, str], kind: str) -> tuple[str, str]:
    """(the requests as ``served.numbering`` reads them, ``labelled`` | ``bare`` | "").

    Labelled ("<label> NO. n") text is kept as served; a bare "n." run is
    labelled with the set's own request label, each request's words as served.
    A blank "RESPONSE:" line after a request is stripped either way."""
    text = "\n".join(pages[n] for n in sorted(pages))
    head = RESPONDING.search(text)
    body = _clean(text[head.end() if head else 0 :], running_lines(pages))
    end = _END.search(body)
    body = body[: end.start()] if end else body
    lab = list(_LABELLED.finditer(body))
    if lab:
        return body[lab[0].start() :].strip(), "labelled"
    start = _bare_start(body, kind)
    marks = list(_BARE.finditer(body, start))
    if not marks:
        return "", ""
    label = SET_TYPES[kind][1]
    items = [
        f"{label} NO. {m.group(1)}: " + " ".join(body[m.end() : (marks[i + 1].start() if i + 1 < len(marks) else len(body))].split())
        for i, m in enumerate(marks)
    ]  # fmt: skip
    return "\n\n".join(items), "bare"
