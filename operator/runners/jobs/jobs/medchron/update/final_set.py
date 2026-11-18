"""The update's FINAL SET of added entries: one step after the update review
and before assemble, where the rules about what the firm reads live. $0.

WHY (2026-11-17). On a real 799-page update about 34 repeated visit rows and
64 lines that were only a citation reached the filed document. The same-visit
rule ran at collect, over the composed entries, and the update review and
its finisher add entries afterwards that it never saw; the stray lines were
joined at assemble, which had to leave a line under a heading that kept the
heading out of an audited claim. And a repair that once wrote AFTER the
audit invented a name and a date that reached the firm. The firm was told:
the entries already delivered and the exhibits are unchanged, new records
become exhibits numbered after the delivered ones, and every statement is
checked against the page it cites.

The rules here are DELETE-ONLY, so no graded claim changes: the audit's claim
keys are taken before and after (``audit/claims.extract_claims``) and an
operation that would add a key is not applied, but recorded. In order:

1. STRAY LINES (``uncited.join_stray``, moved here from assemble). A line
   that is only a citation joins the text it cites. One the join must leave
   (directly under a subsection heading, closing that heading's span) is
   deleted only when that turns exactly one claim into the heading and the
   same claim; that claim's verdict is carried to it (an audit row,
   ``mode: carry``, as ``proofs._as_audited`` carries a respelling). A line
   whose deletion would uncite an exhibit stays.
2. HEADINGS (``update/heading``). Every added entry's date and provider are
   looked for on its cited pages, else on its note's heading. RECORD-ONLY:
   the check records what it finds and NEVER changes the document. (It once
   reverted a heading it judged unprinted; the entry it took for the
   heading's "audited predecessor" could be a different encounter sharing a
   page, and five successive real runs found false failures, so it acts on
   nothing.)
3. REPEATS (``update/encounter``). An added entry that is one encounter with
   a delivered entry or another added entry is DROPPED WHOLE only when the
   other covers it: every value and finding term (``repeats._covered``) and
   either every run of four words of its body (``encounter.held``) or every
   page it cites, by picture (``encounter.page_identical``). Otherwise both
   stay whole and the pair is recorded. A drop that would leave an exhibit
   uncited is not made (assemble refuses a gap). Nothing is recut: recut
   text is ungraded.

Outputs, besides the document: ``update/final_rows.jsonl``, one row per
added claim (or per added entry that has none) with its entry's date, raw
and normalised provider, heading origin, exhibit, pages, claim key, verdict
and mode, page identities and text source; ``update/corrections.json``,
every fold under a DELIVERED entry (a record only: no firm reads it); and
``update/final_set.json``, the counts ``report`` prints.

``python -m jobs.medchron.update.final_set report <job_dir>`` runs the step
on a COPY of a job directory in memory: no model call, no write, counts only.
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..audit import claims as CL
from . import body as B, encounter as E, entry_edits as EE, heading as HD, repeats as R, uncited as U
from .values import content_numbers, terms, words

ROWS, CORRECTIONS, REPORT = "final_rows.jsonl", "corrections.json", "final_set.json"
CARRY = "carry"
#: Where an added entry's heading comes from. Nothing on disk tells an entry
#: the update review's finisher ADDED from one it EDITED (``update_review``
#: persists its reviewer's findings, never the finisher's edits), so both are
#: one honest label.
COMPOSED, FOLD, AFTER_COMPOSE = "composed", "fold", "added after compose"
BORN_DIGITAL, TRANSCRIPT = "born-digital", "transcript"


@dataclass
class Inputs:
    md: str  # the reviewed audit document (``runs/<unit>/final-chronology.md``)
    base_md: str
    composed: list[str]  # ``update/new_entries.md``: the audit document's entries as collect wrote them
    headings: set[str]
    audit_rows: list[dict[str, Any]] = field(default_factory=list)
    pages: HD.Pages = field(default_factory=lambda: HD.Pages(lambda _e, _p: None))
    page_id: E.PageId | None = None
    canon: Callable[[str], str] = str


@dataclass
class Outcome:
    md: str
    rows: list[dict[str, Any]] = field(default_factory=list)
    corrections: list[dict[str, Any]] = field(default_factory=list)
    carries: list[dict[str, Any]] = field(default_factory=list)
    counts: Counter[str] = field(default_factory=Counter)
    ops: list[dict[str, Any]] = field(default_factory=list)
    #: claim keys this step removed on purpose: a dropped entry's, a stray
    #: line's claim whose verdict was carried to its heading and words
    removed: set[str] = field(default_factory=set)


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _all_ex(text: str) -> set[int]:
    return B.cited(text)


def keys_of(text: str) -> set[str]:
    """The audit's claim keys of a document or an entry."""
    body = CL.body_of(text) if CL.BODY_START in text else text
    return {c["key"] for c in CL.extract_claims(body, _all_ex(body))}


def _claims(text: str) -> list[dict[str, Any]]:
    return CL.extract_claims(text, _all_ex(text))


def _head(e: str) -> str:
    return "\n".join(e.split("\n")[:2])


def with_entries(md: str, ents: list[str]) -> str:
    """`md` with its Medical Chronology entries replaced by `ents` (its
    preamble kept), as ``entry_edits.add_entries`` writes them."""
    body = B.sections(md)["## Medical Chronology"]
    at = md.index(body, md.index("## Medical Chronology"))
    pre, _ = B.split_body(body)
    out = (pre.rstrip("\n") + "\n\n" if pre.strip() else "\n") + "\n\n".join(ents) + "\n\n"
    return md[:at] + out + md[at + len(body) :]


def _latest(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    latest: dict[str, dict[str, Any]] = {}
    for r in rows:
        if r.get("kind") == "real" and r.get("key"):
            latest[str(r["key"])] = r
    return latest


# ---- 1. stray lines ---------------------------------------------------------------------------


def _carry_row(new: dict[str, Any], old_row: dict[str, Any], old_key: str) -> dict[str, Any]:
    return {
        "key": new["key"],
        "kind": "real",
        "exhibit": new["exhibit"],
        "page_spec": new["page_spec"],
        "pages": CL.parse_pages(new["page_spec"]) or [1],
        "claim": new["claim"][:500],
        "verdict": old_row.get("verdict"),
        "mode": CARRY,
        "carried_from": old_key,
        "note": "the audited claim under its subsection heading; a stray citation line between them was deleted",
    }


def _flat(s: str) -> str:
    return " ".join(s.split())


def _drop_line(e: str, k: int, latest: dict[str, dict[str, Any]]) -> tuple[str, dict[str, Any] | None, str]:
    """(entry without line `k`, the verdict-carry row or None, why not when
    the line stays). Applied only when no exhibit loses its citation and the
    claims are the same, or exactly one became its heading and itself."""
    lines = e.split("\n")
    out = "\n".join(lines[:k] + lines[k + 1 :])
    if B.cited(out) != B.cited(e):
        return e, None, "its exhibit is cited nowhere else in the entry"
    before = {c["key"]: c for c in _claims(e)}
    after = {c["key"]: c for c in _claims(out)}
    gone, new = set(before) - set(after), set(after) - set(before)
    if not gone and not new:
        return out, None, ""
    if len(gone) != 1 or len(new) != 1:
        return e, None, "it would change more than one claim"
    (g,), (n,) = gone, new
    old, now = before[g], after[n]
    heading = lines[k - 1].strip() if k else ""
    same_cite = (old["exhibit"], old["page_spec"]) == (now["exhibit"], now["page_spec"])
    if not same_cite or _flat(now["claim"]) != _flat(f"{heading} {old['claim']}"):
        return e, None, "the claim it closes would read other than its heading and its audited words"
    row = latest.get(g)
    if row is None:
        return e, None, "the claim it would join has no audit verdict to carry"
    return out, _carry_row(now, row, g), ""


def strays(ents: list[str], headings: set[str], rows: list[dict[str, Any]], o: Outcome) -> list[str]:
    """Rule 1, over the added entries (a fold's delivered part is not in them)."""
    latest = _latest(rows)
    out = []
    for e in ents:
        before = sum(U.cite_only(ln) for ln in e.split("\n")[2:])
        o.counts["stray lines before"] += before
        j = U.join_stray(e, headings, audited=True)
        if keys_of(j) != keys_of(e):
            j = e
            o.ops.append({"op": "stray join", "entry": B.iso(e), "applied": False, "why": "the join changed a claim"})
        k = 2
        while k < len(j.split("\n")):
            if not U.cite_only(j.split("\n")[k]):
                k += 1
                continue
            got, carry, why = _drop_line(j, k, latest)
            if got == j:
                o.ops.append({"op": "stray line", "entry": B.iso(e), "applied": False, "why": why})
                k += 1
                continue
            j = got
            if carry is not None:
                o.removed.add(carry["carried_from"])
                o.carries.append(carry)
                latest[carry["key"]] = carry
                o.counts["verdicts carried"] += 1
        j = re.sub(r"\n{3,}", "\n\n", j)
        o.counts["stray lines after"] += sum(U.cite_only(ln) for ln in j.split("\n")[2:])
        out.append(j)
    return out


# ---- 2. headings ------------------------------------------------------------------------------


def origin(e: str, composed: list[str]) -> str:
    """Where an added entry's HEADING comes from: a fold under a delivered
    entry, collect (a heading of ``new_entries.md``, the audit document's
    own), or written after compose (the update review's finisher)."""
    if EE.is_fold(e):
        return FOLD
    if e in composed or _head(e) in {_head(c) for c in composed}:
        return COMPOSED
    return AFTER_COMPOSE


def headings_check(ents: list[str], x: Inputs, o: Outcome) -> dict[str, HD.Check]:
    """Rule 2, RECORD-ONLY: every added entry's heading checked, keyed by the
    entry. The entries are never touched."""
    checks: dict[str, HD.Check] = {}
    for e in ents:
        if EE.is_fold(e):
            continue
        c = HD.check(e, x.pages)
        checks[e] = c
        o.counts[f"headings {c.where}"] += 1
        o.counts["headings read on a transcript"] += int(c.weaker == HD.WEAKER)
    return checks


# ---- 3. repeats -------------------------------------------------------------------------------


_MONTHS: tuple[str, ...] = ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec")
_NUM_DATE = re.compile(r"(?<![\d/.-])(\d{1,2})[/.-](\d{1,2})[/.-](\d{4}|\d{2})(?!\d|[/.-]\d)")
_ISO_DATE = re.compile(r"(?<!\d)(\d{4})-(\d{2})-(\d{2})(?!\d)")
_WORD_DATE = re.compile(r"(?i)\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+(\d{1,2}),?\s+(\d{4})\b")
_RANGE = re.compile(r"\s*(?:-|–|to|through|thru|until)\s*", re.I)


def _dates_at(text: str) -> list[tuple[int, int, str]]:
    """(start, end, normalised YYYY-MM-DD) of every date written in `text`."""
    out = []
    for m in _NUM_DATE.finditer(text):
        mo, d, y = int(m.group(1)), int(m.group(2)), m.group(3)
        out.append((m.start(), m.end(), f"{2000 + int(y) if len(y) == 2 else int(y):04d}-{mo:02d}-{d:02d}"))
    for m in _ISO_DATE.finditer(text):
        out.append((m.start(), m.end(), m.group(0)))
    for m in _WORD_DATE.finditer(text):
        mo = _MONTHS.index(str(m.group(1)).lower()[:3]) + 1
        out.append((m.start(), m.end(), f"{int(m.group(3)):04d}-{mo:02d}-{int(m.group(2)):02d}"))
    return sorted(out)


def dates_written(e: str) -> tuple[set[str], set[tuple[str, str]]]:
    """(every date, every date range) written in an entry's BODY (its date
    line aside), citations aside."""
    body = B.CITE.sub(" ", "\n".join(e.split("\n")[2:]))
    at = _dates_at(body)
    ranges = {(a[2], b[2]) for a, b in zip(at, at[1:]) if _RANGE.fullmatch(body[a[1] : b[0]])}
    return {d for _s, _e, d in at}, ranges


def dates_present(e: str, keeper: str) -> bool:
    """THE SECOND CONDITION of a safe drop, in its one place: every date and
    every date range written in `e`'s body is written in `keeper`'s body too.
    2026-11-17, on a real update: of 9 covered pairs of two exports, 2 held an
    order date range the kept entry only implied through other dates; this
    keeps them whole. (A run-of-words or a pixel-identity test dropped none:
    two exports are different prints, worded differently.)"""
    mine, my_ranges = dates_written(e)
    have, have_ranges = dates_written(keeper)
    return mine <= have and my_ranges <= have_ranges


def covers(keeper: str, e: str, page_id: E.PageId | None = None) -> bool:
    """`keeper` says everything `e` says: every value and finding term
    (``repeats._covered``) and every date and range (``dates_present``).
    `e`, an added entry no longer than `keeper`, may then go whole."""
    return R._covered(e, keeper) and dates_present(e, keeper)


def _yn(b: bool) -> str:
    return "yes" if b else "no"


def _kept_why(e: str, cond: list[tuple[str, str, bool, bool]]) -> str:
    """Why an added entry matched by key stays whole (the first rule it fails)."""
    if not any(c for _k, _x, c, _d in cond):
        return "not covered (it states a value or finding the other lacks)"
    if not any(c and d for _k, _x, c, d in cond):
        return "a date or date range the other does not write"
    return "the entry that covers it is the shorter one"


def _lost(e: str, keeper: str) -> bool:
    """The falsifier of a drop, read independently of the rule: a content
    value or a finding term of `e` that `keeper` does not state."""
    return bool(content_numbers(e) - content_numbers(keeper)) or bool(terms(e) - words(keeper))


def repeats(ents: list[str], base_ents: list[str], x: Inputs, o: Outcome) -> list[str]:
    """Rule 3. Longer entries are kept first; a fold is never dropped."""
    order = sorted(range(len(ents)), key=lambda i: -len(ents[i]))
    dropped: set[int] = set()
    cited_before = set().union(*(B.cited(e) for e in ents)) if ents else set()
    for i in order:
        e = ents[i]
        if EE.is_fold(e):
            continue
        keep_by = [("delivered", d) for d in base_ents if E.same_encounter(d, e, x.canon, x.page_id)]
        keep_by += [
            ("added", ents[j])
            for j in order
            if j != i and j not in dropped and not EE.is_fold(ents[j]) and len(ents[j]) >= len(e)
            if E.same_encounter(ents[j], e, x.canon, x.page_id) and (len(ents[j]) > len(e) or j < i)
        ]
        if not keep_by:
            continue
        cond = [(kind, k, R._covered(e, k), dates_present(e, k)) for kind, k in keep_by]
        for kind, _k, _c, _d in cond:
            o.counts[f"repeats: key matches ({kind})"] += 1
        hit = next(((kind, k) for kind, k, c, d in cond if c and d and len(k) >= len(e)), None)
        why = "" if hit else _kept_why(e, cond)
        if hit is not None:
            rest = [ents[j] for j in range(len(ents)) if j != i and j not in dropped]
            still = set().union(*(B.cited(r) for r in rest)) if rest else set()
            why = "an exhibit would be uncited" if cited_before - still else ""
        for kind, k, c, d in cond:
            gone = not why and hit is not None and k is hit[1]
            o.counts[f"pair: covered {_yn(c)}, dates present {_yn(d)}, dropped {_yn(gone)}"] += 1
        if why:
            o.counts[f"repeats: kept whole, {why}"] += 1
            o.ops.append({"op": "repeat", "entry": E.key(e), "applied": False, "why": why})
            continue
        assert hit is not None
        dropped.add(i)
        o.removed |= keys_of(e)
        o.counts[f"repeats: safe drops ({hit[0]})"] += 1
        o.counts["distinct visits merged"] += int(_lost(e, hit[1]))
        o.ops.append({"op": "repeat", "entry": E.key(e), "applied": True, "by": hit[0]})
    return [e for i, e in enumerate(ents) if i not in dropped]


# ---- rows and corrections ---------------------------------------------------------------------


def _source(pages: list[tuple[int, int]], x: Inputs) -> str:
    return TRANSCRIPT if any(x.pages.transcribed(ex, p) for ex, p in pages) else BORN_DIGITAL


def rows_of(ents: list[str], x: Inputs, checks: dict[str, HD.Check], latest: dict[str, dict[str, Any]]) -> list[dict]:
    """One row per added claim, or per added entry with none."""
    out: list[dict[str, Any]] = []
    for i, e in enumerate(ents):
        prov = B.provider_of(e)
        common = {
            "entry": i + 1,
            "date": B.iso(e),
            "service": B.is_service(e),
            "provider": prov,
            "provider_norm": "+".join(sorted(E.name_tokens(prov))),
            "encounter": E.key(e),
            "origin": origin(e, x.composed),
            "heading": (checks.get(e) or HD.Check("not checked")).as_dict(),
        }
        cl = _claims(e)
        for c in cl or [None]:
            pages = [(c["exhibit"], p) for p in CL.parse_pages(c["page_spec"]) or [1]] if c else []
            row = latest.get(c["key"], {}) if c else {}
            out.append(
                common
                | {
                    "exhibit": c["exhibit"] if c else None,
                    "pages": [p for _ex, p in pages],
                    "claim_key": c["key"] if c else None,
                    "verdict": row.get("verdict") if c else None,
                    "mode": (row.get("mode") or "model") if row else None,
                    "page_ids": sorted(x.page_id(ex, p) for ex, p in pages) if x.page_id else [],
                    "text_source": _source(pages, x),
                }
            )
    return out


def corrections_of(ents: list[str], base_ents: list[str]) -> list[dict[str, Any]]:
    """Every fold under a DELIVERED entry: which, its bytes before, what is
    added and why. A record only. The review finding a fold settles is not
    named: the update review persists its reviewer's findings, never the
    finisher's edits, so no record on disk says which finding a fold came from."""
    out = []
    for e in ents:
        if not EE.is_fold(e):
            continue
        k = next((n for n, d in enumerate(base_ents) if _head(d) == _head(e)), None)
        if k is None:
            continue
        block = e.split("\n", 3)[3] if e.count("\n") >= 3 else ""  # the fold's heading line and its paragraphs
        out.append(
            {
                "entry": f"D{k + 1}",
                "before_sha": _sha(base_ents[k]),
                "added_sha": _sha(block),
                "added_paragraphs": len([p for p in block.split("\n\n")[1:] if p.strip()]),
                "why": "an added visit's content the delivered entry lacks, under it (``entry_edits``)",
            }
        )
    return out


# ---- the step ---------------------------------------------------------------------------------


def run(x: Inputs) -> Outcome:
    """The final set of the added entries. Raises AssertionError, and nothing
    is written, if any rule created a claim key (each checks it cannot) or if
    a claim key is LOST that no drop of this step accounts for: a document
    that lost an entry this step did not drop is refused, never filed."""
    _, ents = B.entries_of(x.md)
    _, base_ents = B.entries_of(x.base_md)
    o = Outcome(x.md)
    keys0 = keys_of(x.md)
    ents = strays(ents, x.headings, x.audit_rows, o)
    checks = headings_check(ents, x, o)
    ents = repeats(ents, base_ents, x, o)
    md = with_entries(x.md, ents) if ents != B.entries_of(x.md)[1] else x.md
    carried = {c["key"] for c in o.carries}
    after = keys_of(md)
    created, lost = after - keys0 - carried, keys0 - after - o.removed
    o.counts["new claim keys created"] = len(created)
    o.counts["claim keys lost unaccounted"] = len(lost)
    if created:
        raise AssertionError(f"final set created {len(created)} claim key(s); nothing is written")
    if lost:
        raise AssertionError(f"final set lost {len(lost)} claim key(s) no drop of its own removed; nothing is written")
    o.md = md
    latest = _latest(x.audit_rows + o.carries)
    o.rows = rows_of(ents, x, checks, latest)
    o.corrections = corrections_of(ents, base_ents)
    o.counts["added entries"] = len(ents)
    o.counts["corrections recorded"] = len(o.corrections)
    return o


def main(argv: list[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(prog="python -m jobs.medchron.update.final_set")
    sub = ap.add_subparsers(dest="cmd", required=True)
    rp = sub.add_parser("report", help="the final set's counts on a COPY of a job directory; no model, no write")
    rp.add_argument("job_dir", type=Path)
    rp.add_argument("--unit")
    a = ap.parse_args(argv)
    try:
        from .final_set_io import report

        out = report(a.job_dir, a.unit)
    except AssertionError as exc:
        print(json.dumps({"refused": str(exc)}))
        return 1
    print(json.dumps(out, indent=1))
    return 1 if out["distinct_visits_merged"] or out["new_claim_keys_created"] else 0


if __name__ == "__main__":
    sys.exit(main())
