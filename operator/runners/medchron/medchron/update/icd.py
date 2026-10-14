"""The Diagnostic Highlights rows an update adds, audited like any claim. $0.

Before, a code the new entries recorded first went straight into the table
from the compose pass's INDEX row: never checked against its page, and given
whatever the code tables said, an ICD-9 label included, for a scanned ICD-10
code that had lost its letter. Now:

* `claims` (at collect) turns each code a kept new entry records first into a
  CLAIM, "Diagnosis code <code> recorded on MM/DD/YYYY. (Exhibit N - p. P)",
  cited to the page of the entry that records it, and the claim goes into the
  AUDIT document like every other new sentence. The code is the one
  ``icd_tables.resolve`` reads for the record's date.
* `rows` (at assemble) builds a table row ONLY from a claim the audit kept, in
  the code the audited claim states (a repair that corrected the code is the
  code shown). A code with no description reads ``NOT_LEGIBLE``, never blank,
  and is listed in the report.
"""

from __future__ import annotations

import re
from typing import Any, Callable

from . import body as B

ICD_SHAPE = re.compile(r"[A-Z]\d{2}(\.[0-9A-Z]{1,4}|[0-9A-Z]{0,4})|\d{3}(\.\d{1,2})?|[VE]\d{2,3}(\.\d)?")
CLAIM = re.compile(
    r"^Diagnosis code (\S+) recorded on (\d{2})/(\d{2})/(\d{4})\.\s*\((Exhibit \d+(?: - p\. [0-9,\s\-]+)?)\)\s*$"
)
NOT_LEGIBLE = "code as recorded not legible"
HEAD = "| ICD Code | Description | First Diagnosed | Reference |"
#: (code, record date ISO, entry text) -> (code shown, description or None, how)
Resolve = Callable[[str, str, str], tuple[str, str | None, str]]


def index_codes(index_rows: list[str], kept_dates: set[str]) -> dict[str, tuple[str, str, str]]:
    """{dotless: (first iso, code, provider)} from INDEX rows, build_doc's cleaning rules."""
    icd: dict[str, tuple[str, str, str]] = {}
    for line in index_rows:
        p = [x.strip() for x in line.split("|")]
        if len(p) < 3 or not re.match(r"^\d{4}-\d{2}-\d{2}$", p[0]) or p[0] not in kept_dates:
            continue
        for code in re.split(r"[,/;]\s*", p[2]):
            code = re.sub(r"\s*\(.*?\)", "", code).strip()
            code = re.sub(r"(?i)\s+(?:as recorded|history).*$", "", code).strip()
            if not code or code == "--" or not ICD_SHAPE.fullmatch(code):
                continue
            key = code.replace(".", "")
            prior = icd.get(key)
            if prior is None or p[0] < prior[0]:
                icd[key] = (p[0], code, p[1])
            elif p[0] == prior[0] and "." in code and "." not in prior[1]:
                icd[key] = (prior[0], code, prior[2])
    return icd


def delivered_firsts(base_rows: list[str]) -> dict[str, tuple[str, str, str]]:
    """{dotless: (first iso, code, row)} for the delivered table's rows."""
    rows: dict[str, tuple[str, str, str]] = {}
    for line in base_rows[1:]:
        c = [x.strip() for x in line.strip().strip("|").split("|")]
        m = re.match(r"(\d{2})/(\d{2})/(\d{4})", c[2] if len(c) > 2 else "")
        first = f"{m.group(3)}-{m.group(1)}-{m.group(2)}" if m else "9999-12-31"
        rows[c[0].replace(".", "")] = (first, c[0], line.strip())
    return rows


def _toks(s: str) -> set[str]:
    return set(re.findall(r"[a-z]{4,}", (s or "").lower()))


def claims(
    index_rows: list[str], entries: list[str], base_rows: list[str], resolve: Resolve
) -> tuple[list[str], list[dict[str, Any]]]:
    """(claim lines for the audit document, a log row per code)."""
    have = delivered_firsts(base_rows)
    out: list[str] = []
    log: list[dict[str, Any]] = []
    new = index_codes(index_rows, {B.iso(e) for e in entries})
    for _key, (first, code, prov) in sorted(new.items(), key=lambda kv: (kv[1][0], kv[1][1])):
        same = [e for e in entries if B.iso(e) == first]
        shown, desc, how = resolve(code, first, "\n".join(same))
        k = shown.replace(".", "")
        if k in have and have[k][0] <= first:
            continue
        best = max(same, key=lambda e: len(_toks(B.provider_of(e)) & _toks(prov)), default="")
        cite = B.CITE.search(best)
        if not cite:
            log.append({"code": code, "first": first, "skipped": "no entry of that date carries a citation"})
            continue
        out.append(f"Diagnosis code {shown} recorded on {first[5:7]}/{first[8:10]}/{first[:4]}. {cite.group(0)}")
        log.append({"code": code, "shown": shown, "first": first, "how": how, "described": desc is not None})
    return out, log


def split_claims(pre: str) -> tuple[str, list[tuple[str, str, str]]]:
    """(the preamble without its diagnosis claims, [(code, first iso, ref)])."""
    keep: list[str] = []
    got: list[tuple[str, str, str]] = []
    for para in re.split(r"\n\s*\n", pre or ""):
        m = CLAIM.match(para.strip())
        if m:
            got.append((m.group(1), f"{m.group(4)}-{m.group(2)}-{m.group(3)}", m.group(5)))
        elif para.strip():
            keep.append(para.strip("\n"))
    return "\n\n".join(keep), got


def rows(
    base_rows: list[str], audited: list[tuple[str, str, str]], entries: list[str], resolve: Resolve
) -> tuple[list[str], list[dict[str, Any]], list[str]]:
    """(table rows, rows added or moved, codes shown as not legible). Delivered
    rows kept as delivered; a row per audited claim, sorted by (first date,
    code) as build_doc sorts."""
    table = delivered_firsts(base_rows)
    changed: list[dict[str, Any]] = []
    illegible: list[str] = []
    for code, first, ref in audited:
        text = "\n".join(e for e in entries if B.iso(e) == first)
        shown, desc, _how = resolve(code, first, text)
        key = shown.replace(".", "")
        if key in table and table[key][0] <= first:
            continue
        if desc is None:
            illegible.append(shown)
        line = f"| {shown} | {desc or NOT_LEGIBLE} | {first[5:7]}/{first[8:10]}/{first[:4]} | {ref} |"
        changed.append({"code": shown, "was": table[key][2] if key in table else None, "now": line})
        table[key] = (first, shown, line)
    head = base_rows[:1] or [HEAD]
    return head + [r[2] for r in sorted(table.values(), key=lambda r: (r[0], r[1]))], changed, illegible
