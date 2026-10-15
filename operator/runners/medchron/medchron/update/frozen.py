"""The update review's FROZEN must-fix list: keys, where each sits now, and
what the narrow re-check says about it. $0, no model
(``stages/update_review.py`` makes the paid calls).

WHY (2026-10-15). The review loop re-reviewed after every finish and held
until a fresh review found nothing must-fix. On two real matters it never
converged: each round raised mostly NEW objections (one round 2: 77 of 85
must-fix were new), so the stopping rule chased a moving target. Round 1's
MUST findings on added entries are now the whole of what can hold the
update; a later review only confirms or settles them.

THE KEY. A finding's anchor (``N<k>``) moves whenever an edit drops, adds or
redates an entry, so a frozen finding is identified by what does not: its
class (lower case, the review's leading number dropped), the exhibit and page
it relies on, and the date line of the entry it sits on. The page a reviewer
relies on is the evidence it cites, and the date line is the visit; together
they name one defect on one visit without the anchor. The cost of the
choice: the same defect cited on another page, or a finding on an entry whose
date a redate changed, does not match, and is then settled as "not raised
again" (the claim audit still grades every sentence either way).
"""

from __future__ import annotations

import re
from typing import Any

from . import review_repair as RR

NOT_REVIEWED = "not reviewed"
ADVISORY = "advisory: not on the round-1 list"
SETTLED = "settled: not raised again on re-check"
_ANCHOR = re.compile(r"\bN\d+\b")


def _klass(k: str) -> str:
    return " ".join(re.sub(r"^[\d\W_]+", "", k.lower()).split())


def date_line(text: str) -> str:
    return text.split("\n", 1)[0].strip()


def _entry(f: RR.Finding, ents: list[RR.Entry]) -> RR.Entry | None:
    a = f.anchor.split(".")[0]
    return next((e for e in ents if e.anchor == a), None)


def key(f: RR.Finding, ents: list[RR.Entry]) -> str:
    """class | exhibit | page | the entry's date line (``ents``: the anchored
    added entries of the document the finding was raised on)."""
    e = _entry(f, ents)
    return "|".join((_klass(f.klass), str(f.exhibit or ""), str(f.page or ""), date_line(e.text) if e else ""))


def dates(f: RR.Finding, ents: list[RR.Entry]) -> list[str]:
    """The date lines of the entries the finding sits on: its own, and for a
    part the review did not finish, every entry its problem names."""
    names = {f.anchor.split(".")[0]}
    if f.klass == NOT_REVIEWED:
        names |= set(_ANCHOR.findall(f.problem))
    return sorted({date_line(e.text) for e in ents if e.anchor in names})


def freeze(must: list[RR.Finding], ents: list[RR.Entry]) -> list[dict[str, Any]]:
    """The record rows of round 1's MUST findings on added entries."""
    return [{"key": key(f, ents), "dates": dates(f, ents), "finding": f.as_dict()} for f in must]


def reanchor(must: list[RR.Finding], ents: list[RR.Entry]) -> list[dict[str, Any]]:
    """The record rows of findings whose anchors name ANOTHER document (an
    old checkpoint's last review, made before its finish renumbered the
    entries: on a real resume 34 of 34 keys took the wrong date line). Each
    is moved to the entry of ``ents`` citing its exhibit and page (of
    several, the one stating a date the finding's page_says or problem
    names, else the first); one no entry cites keeps its page and no date,
    and ``locate`` finds it by the page."""
    rows = []
    for f in must:
        cites = [e for e in ents if f.exhibit and f.page and (f.exhibit, f.page) in e.cites()]
        said = set(re.findall(r"\d{2}/\d{2}/\d{4}", f"{f.page_says} {f.problem}"))
        pick = next((e for e in cites if any(d in e.text for d in said)), cites[0] if cites else None)
        if pick is not None:
            f.anchor = pick.anchor
            rows.append({"key": key(f, ents), "dates": dates(f, ents), "finding": f.as_dict()})
        else:
            k = "|".join((_klass(f.klass), str(f.exhibit or ""), str(f.page or ""), ""))
            rows.append({"key": k, "dates": [], "finding": f.as_dict()})
    return rows


def finding(row: dict[str, Any]) -> RR.Finding:
    return RR.Finding(**row["finding"])


def locate(rows: list[dict[str, Any]], ents: list[RR.Entry]) -> set[int]:
    """The 0-based indexes of the entries the frozen rows sit on now: the
    entries on their date (those citing their exhibit, when any does; the
    exhibit may be a page the entry omits, so never only those); an entry no
    longer on that date, by the page it cites."""
    out: set[int] = set()
    for row in rows:
        f, on = finding(row), set(row.get("dates") or [])
        same_day = {i for i, e in enumerate(ents) if date_line(e.text) in on}
        hit = {i for i in same_day if any(x == f.exhibit for x, _ in ents[i].cites())}
        if not same_day and f.exhibit and f.page:
            hit = {i for i, e in enumerate(ents) if (f.exhibit, f.page) in e.cites()}
        out |= hit or same_day
    return out


def triage(
    rows: list[dict[str, Any]], open_: list[int], found: list[RR.Finding], ents: list[RR.Entry]
) -> tuple[list[RR.Finding], list[RR.Finding], dict[int, list[RR.Finding]]]:
    """The narrow re-check's findings sorted against the open frozen rows
    (``open_``: their indexes): (the MUST findings that go to the finisher,
    the parts still not reviewed, {frozen row: the findings that confirm it}).

    * a MUST finding whose key an open frozen row has CONFIRMS that row;
    * a MUST finding on an entry round 1 could not review is that entry's
      first review: it goes to the finisher as if it were on the list;
    * every other finding is recorded ``advisory`` and never blocks."""
    keys: dict[str, list[int]] = {}
    unread: set[str] = set()
    for i in open_:
        if rows[i]["finding"]["klass"] == NOT_REVIEWED:
            unread |= set(rows[i].get("dates") or [])
        else:
            keys.setdefault(rows[i]["key"], []).append(i)
    todo: list[RR.Finding] = []
    unreviewed: list[RR.Finding] = []
    confirms: dict[int, list[RR.Finding]] = {}
    for f in found:
        if f.anchor.startswith("D"):
            f.outcome = "carried: a delivered entry is never edited by an update"
        elif f.klass == NOT_REVIEWED:
            unreviewed.append(f)
        elif f.severity != RR.MUST:
            f.outcome = ADVISORY
        elif key(f, ents) in keys:
            for i in keys[key(f, ents)]:
                confirms.setdefault(i, []).append(f)
            todo.append(f)
        elif (e := _entry(f, ents)) is not None and date_line(e.text) in unread:
            todo.append(f)
        else:
            f.outcome = ADVISORY
    return todo, unreviewed, confirms
