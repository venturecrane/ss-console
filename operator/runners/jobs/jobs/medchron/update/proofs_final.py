"""The update's proofs of what the firm was told, over the filed document. $0.

The firm was told: on an update the entries already delivered and the
exhibits are unchanged; new records become exhibits numbered after the
delivered ones; every statement is checked against the page it cites. These
proofs read the RENDERED document and the final set's rows
(``update/final_set``) and hold each of those words:

11. every delivered entry's text, as the rendered entry table shows it, is in
    the updated document in its delivered order, the same text, differing
    only where ``update/corrections.json`` records a fold under it (and then
    only by what follows its delivered text);
12. every delivered Exhibit List row is in the updated list as delivered,
    every new exhibit's number is above the delivered highest, and no
    delivered exhibit is in the upload manifest (it is on the matter already);
13. no two added entries are one encounter (``encounter.same_encounter``)
    with the longer covering the shorter (``final_set.covers``) and nothing
    keeping it: a repeat the final set should have dropped (``p13`` also
    counts, apart, the pairs kept for an exhibit and the pairs only the
    shorter covers);
14. each new exhibit's title names a provider and dates that a final row
    citing that exhibit carries;
15. every paragraph of the filed body is graded, delivered, or a listed label
    (``medchron/graded_proof``).

WHICH FAIL A RUN is one table (``ENFORCE``, ``RECORD``), pinned by a test, so
turning a record-only proof on is a one-line reviewed change. An enforced
proof's failure joins the update proofs' own (``stages/update_finish``,
exit 1: nothing is filed). A record-only one returns its findings as codes
and counts under ``recorded`` in ``proofs.json``, and never fails a run.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from .. import graded_proof as GP
from . import body as B, encounter as E, entry_edits as EE

#: Proofs whose failure fails the run.
ENFORCE = frozenset({"11"})
#: Proofs that record their findings and never fail the run.
RECORD = frozenset({"12", "13", "14", "15"})
_MDY = re.compile(r"\d{2}-\d{2}-\d{4}")
_TITLE_HEAD = re.compile(r" - \d{2}-\d{2}-\d{4}| -\s+\(| \(")


@dataclass
class Inputs:
    delivered_rows: list[str]  # ``docx_md.entry_rows`` of the delivered document
    updated_rows: list[str]  # the same of the updated document
    corrections: list[dict[str, Any]]  # ``update/corrections.json``
    delivered_list: list[tuple[str, ...]]  # the delivered Exhibit List's rows
    updated_list: list[tuple[str, ...]]
    delivered_max: int
    new_numbers: list[int]  # the new exhibits the update files
    titles: dict[int, str]  # {new exhibit: its title in the updated document}
    final_rows: list[dict[str, Any]]
    added: list[str]  # the final set's added entries
    manifest_names: list[str] | None = None  # None before the manifest is built
    delivered_pdfs: set[str] = field(default_factory=set)
    graded: GP.Result | None = None
    canon: Callable[[str], str] = str
    page_id: E.PageId | None = None


@dataclass
class Result:
    lines: list[str] = field(default_factory=list)
    fails: list[str] = field(default_factory=list)
    recorded: dict[str, int] = field(default_factory=dict)
    #: {name, sha256} of the chronology the proofs read (the file filed must be it)
    filed: dict[str, Any] | None = None


def p11(x: Inputs) -> Counter[str]:
    """Delivered entries in order, unchanged but for a recorded fold."""
    folded = {str(c.get("entry")) for c in x.corrections}
    out: Counter[str] = Counter()
    j = 0
    for i, d in enumerate(x.delivered_rows, 1):
        k = next(
            (
                n
                for n in range(j, len(x.updated_rows))
                if x.updated_rows[n] == d or (f"D{i}" in folded and x.updated_rows[n].startswith(d + " "))
            ),
            None,
        )
        if k is None:
            out["P11_delivered_entry_changed_or_moved"] += 1
            continue
        j = k + 1
    return out


def p12(x: Inputs) -> Counter[str]:
    out: Counter[str] = Counter()
    have = Counter(x.updated_list)
    for row in x.delivered_list:
        if have[row] <= 0:
            out["P12_delivered_exhibit_row_changed"] += 1
        else:
            have[row] -= 1
    out["P12_new_number_not_above_delivered"] = sum(1 for n in x.new_numbers if n <= x.delivered_max)
    if x.manifest_names is not None:
        out["P12_delivered_pdf_in_manifest"] = len(set(x.manifest_names) & x.delivered_pdfs)
    return out


def p13(x: Inputs) -> Counter[str]:
    """Pairs of added entries that are one encounter, by the drop rule's own
    direction (``final_set.covers``: the LONGER covers the shorter, values,
    findings, dates and ranges): ``P13_covered_repeat_left`` when nothing kept
    the shorter (a repeat the final set should have dropped: 0 when it ran),
    ``P13_kept_for_an_exhibit`` when dropping it would uncite an exhibit.
    ``P13_shorter_covers_longer`` counts pairs only the shorter covers, which
    the rule never drops (the longer one stays). A pair a date or range keeps
    whole is in none of them: ``covers`` requires ``dates_present``."""
    from .final_set import covers

    ents = [e for e in x.added if not EE.is_fold(e)]
    out = Counter({"P13_covered_repeat_left": 0, "P13_kept_for_an_exhibit": 0, "P13_shorter_covers_longer": 0})
    every = set().union(*(B.cited(e) for e in x.added)) if x.added else set()
    for i, j in E.pairs(ents, x.canon, x.page_id):
        long_, short = (ents[i], ents[j]) if len(ents[i]) >= len(ents[j]) else (ents[j], ents[i])
        if covers(long_, short, x.page_id):
            rest = [e for e in x.added if e is not short]
            still = set().union(*(B.cited(e) for e in rest)) if rest else set()
            out["P13_kept_for_an_exhibit" if every - still else "P13_covered_repeat_left"] += 1
        elif covers(short, long_, x.page_id):
            out["P13_shorter_covers_longer"] += 1
    return out


def _words(name: str) -> set[str]:
    """A provider's words, credentials aside: a title's provider is its
    rows' provider, or that name with words left out (``stages.group.Canon``)."""
    from . import finisher as FN

    return {w for w in re.findall(r"[a-z]{2,}", (name or "").lower()) if w not in FN._CRED}


def _title_parts(title: str) -> tuple[str, list[str]]:
    return _TITLE_HEAD.split(title, maxsplit=1)[0].strip(), _MDY.findall(title)


def p14(x: Inputs) -> Counter[str]:
    out: Counter[str] = Counter({"P14_title_provider_not_in_its_rows": 0, "P14_title_date_not_in_its_rows": 0})
    for n, title in x.titles.items():
        rows = [r for r in x.final_rows if r.get("exhibit") == n]
        prov, dates = _title_parts(title)
        provs = {str(r.get("provider") or "") for r in rows}
        mine = _words(prov)
        if prov not in provs and not (mine and any(mine <= _words(p) for p in provs)):
            out["P14_title_provider_not_in_its_rows"] += 1
        have = {f"{d[5:7]}-{d[8:10]}-{d[:4]}" for d in (str(r.get("date") or "") for r in rows) if len(d) == 10}
        out["P14_title_date_not_in_its_rows"] += sum(1 for d in dates if d not in have)
    return out


CHECKS: dict[str, Callable[[Inputs], Counter[str]]] = {"11": p11, "12": p12, "13": p13, "14": p14}


def run(x: Inputs) -> Result:
    """Each proof, routed by ``ENFORCE`` and ``RECORD``."""
    r = Result()
    found: dict[str, Counter[str]] = {n: f(x) for n, f in CHECKS.items()}
    if x.graded is not None:
        found["15"] = Counter(GP.codes(x.graded))
    for n, got in found.items():
        hits = {k: v for k, v in got.items() if v}
        state = "ok" if not hits else ", ".join(f"{k}={v}" for k, v in sorted(hits.items()))
        if n in ENFORCE:
            r.lines.append(f"{n}. {state}")
            if hits:
                r.fails.append(n)
        elif n in RECORD:
            r.lines.append(f"{n}. (recorded, never fails the run) {state}")
            r.recorded.update({k: int(v) for k, v in got.items()})
    return r


__all__ = ["CHECKS", "ENFORCE", "RECORD", "Inputs", "Result", "p11", "p12", "p13", "p14", "run"]
