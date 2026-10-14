"""The composed entries an update keeps, and the AUDIT document. $0.

Compose ran over the selected pages of the new exhibits only, through the
seat's own map, repair, assemble and merge stages, so its output cites files
by name and assemble numbered them by first citation. Here:

1. citations are mapped back to the exhibit numbers the update assigned
   (`renumber`; an unknown source refuses);
2. an entry is KEPT unless a delivered entry has its date and provider (or
   reads alike) AND it states no value or finding the delivered entries of
   that date lack (`keep_decision`, ``update/repeats.py``): dropping an entry
   that adds one would lose it, and the audit cannot see a dropped value. A
   kept restatement of a delivered visit is cut to its "Additional findings";
   a new entry another new entry of its date and provider covers is dropped;
3. an entry carrying an unresolved "(FILE: ...)" citation refuses: the audit
   extracts only "(Exhibit N ...)" claims, so it would ship unaudited;
4. a new exhibit no kept entry cites is not an exhibit (every visit it holds
   is already in the chronology); the cited ones are renumbered without gaps
   (`compact`), BEFORE the audit, because a claim's audit key hashes its
   exhibit number; one that reuses a firm-numbered file keeps that number;
5. the audit document is the new entries, the dated added prior history and
   one claim per new diagnosis code, under the delivered Exhibit List plus
   the new exhibits' rows and a row per firm-numbered file the list never
   named, so every new citation resolves and no delivered claim is in it to
   be audited or edited.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path
from typing import Any, Callable

from . import body as B, demographics as D, repeats as R

CITED_EX = re.compile(r"\(Exhibit (\d+)(?: - p\. [0-9,\s\-]+)?\)")
ASSEMBLED = re.compile(r"\(Exhibit (\d+)((?: - p\. [0-9,\s\-]+)?)\)")


class CollectRefusal(ValueError):
    pass


def renumber(text: str, exhibit_map: dict[str, int]) -> str:
    """assemble numbers sources by first citation; the update numbered its new
    exhibits already and their names say so ("Exhibit 6 - ..."). An assemble
    number that is not one of them raises."""
    to_ex: dict[int, int] = {}
    for name, k in exhibit_map.items():
        m = re.match(r"Exhibit (\d+) - ", name)
        if not m:
            raise CollectRefusal(f"a cited source is not one of the update's exhibits: {name[:80]!r}")
        to_ex[int(k)] = int(m.group(1))

    def sub(m: re.Match[str]) -> str:
        k = int(m.group(1))
        if k not in to_ex:
            raise CollectRefusal(f"assembled citation Exhibit {k} maps to no exhibit")
        return f"(Exhibit {to_ex[k]}{m.group(2)})"

    return ASSEMBLED.sub(sub, text)


def composed(run_dir: Path) -> list[str]:
    """Every entry compose produced (standalone and merged), renumbered to
    the update's exhibits, with no street address or phone number in a
    body (``demographics.py``): the source proof 10 measures against."""
    em = json.loads((run_dir / "exhibit_map.json").read_text(encoding="utf-8"))
    src = ""
    for f in ("entries.md", "merged.md"):
        p = run_dir / f
        if p.is_file():
            src += p.read_text(encoding="utf-8") + "\n\n"
    _, ents = B.split_body(renumber(src, em))
    return [D.scrub(e) for e in ents]


def keep_decision(e: str, base_ents: list[str], canon: Callable[[str], str]) -> str:
    """'keep' (whole or as an "Additional findings" row), or the reason the
    entry is not added (``update/repeats.py``)."""
    verdict, _ = R.keep_decision(e, base_ents, canon)
    return "keep" if verdict.startswith("keep") else verdict


def collect(
    comp: list[str], base_ents: list[str], incident: str, canon: Callable[[str], str]
) -> tuple[list[str], dict]:
    """(kept entries in date order, report). Refuses an unresolved citation."""
    keep: list[str] = []
    rep: Counter[str] = Counter()
    for e in comp:
        verdict, kept = R.keep_decision(e, base_ents, canon)
        if kept is None:
            rep[verdict] += 1
            continue
        keep.append(kept)
        if verdict != "keep":
            rep[verdict] += 1
    keep, draws = R.merge_draws(keep)
    if draws:
        rep[R.SAME_DRAW] += draws
    keep, twins = R.dedupe_new(keep, canon)
    if twins:
        rep[R.REPEAT_NEW] += twins
    for e in keep:
        rep["kept: " + ("after" if B.iso(e) >= incident else "before") + " the incident"] += 1
    raw = [e.splitlines()[0] for e in keep if "(FILE:" in e]
    if raw:
        raise CollectRefusal(
            f"{len(raw)} new entr(y/ies) carry an unresolved (FILE: ...) citation, which the audit cannot see: "
            f"{raw[:5]}"
        )
    return sorted(keep, key=B.iso), {**dict(rep), "composed": len(comp), "kept": len(keep)}


def compact(entries: list[str], new_exhibits: list[dict[str, Any]]) -> tuple[list[str], list[dict], list[dict], dict]:
    """(entries, cited exhibits renumbered without gaps, uncited exhibits,
    {composed number: final number}). Pure: the stage renames the files. An
    exhibit that reuses a firm-numbered file ("reused") keeps that number."""
    cited = {int(m.group(1)) for e in entries for m in CITED_EX.finditer(e)}
    if not new_exhibits:
        return entries, [], [], {}
    fresh = [r["exhibit"] for r in new_exhibits if not r.get("reused")]
    top = (min(fresh) if fresh else 1) - 1
    keep = [dict(r) for r in sorted(new_exhibits, key=lambda r: r["exhibit"]) if r["exhibit"] in cited]
    uncited = [dict(r) for r in new_exhibits if r["exhibit"] not in cited]
    remap = {r["exhibit"]: r["exhibit"] for r in keep if r.get("reused")}
    remap.update({r["exhibit"]: top + 1 + i for i, r in enumerate(x for x in keep if not x.get("reused"))})
    for r in keep:
        n = remap[r["exhibit"]]
        r["composed_as"] = r["exhibit"]
        r["title"] = re.sub(r"^Exhibit \d+ - ", f"Exhibit {n} - ", r["title"])
        r["exhibit"] = n
    for r in uncited:
        r["composed_as"] = r["exhibit"]

    def sub(m: re.Match[str]) -> str:
        n = int(m.group(1))
        return m.group(0) if n not in remap else m.group(0).replace(f"Exhibit {n}", f"Exhibit {remap[n]}", 1)

    return [CITED_EX.sub(sub, e) for e in entries], keep, uncited, remap


def exhibit_rows(new_exhibits: list[dict[str, Any]]) -> list[str]:
    """Exhibit List rows for the new exhibits, in the delivered row shape."""
    return [
        f"| {r['exhibit']} | {r['reused'] if r.get('reused') else r['title'].split(' - ', 1)[1]} |"
        for r in new_exhibits
    ]


def folder_rows(base_md: str, folder: list[dict[str, Any]], taken: set[int]) -> list[str]:
    """A row for each numbered file in the chronology folder that neither the
    delivered Exhibit List nor a new exhibit names: the firm's own exhibit,
    named by its file, so the list has no gap and every number it cites
    resolves."""
    listed = B.delivered_numbers(base_md) | taken
    return [
        f"| {r['exhibit']} | {r['name']} |"
        for r in sorted(folder, key=lambda r: int(r["exhibit"]))
        if int(r["exhibit"]) not in listed
    ]


_NUMBERED = re.compile(r"^\|\s*(\d+)\s*\|")


def with_rows(ex_sec: str, rows: list[str]) -> str:
    """An Exhibit List section with `rows` added, every numbered row in
    exhibit-number order after the header rows."""
    if not rows:
        return ex_sec
    tail = ex_sec[len(ex_sec.rstrip("\n")) :] or "\n"
    lines = ex_sec.rstrip("\n").splitlines()
    idx = [i for i, ln in enumerate(lines) if ln.strip().startswith("|")]
    table = [lines[i] for i in idx] + rows
    head = [r for r in table if not _NUMBERED.match(r.strip())]
    numbered = sorted((r for r in table if _NUMBERED.match(r.strip())), key=lambda r: int(r.split("|")[1]))
    return "\n".join(lines[: idx[0]] + head + numbered + lines[idx[-1] + 1 :]) + tail


def audit_document(
    base_md: str,
    entries: list[str],
    new_exhibits: list[dict[str, Any]],
    pmh: str | None = None,
    *,
    icd_claims: list[str] | None = None,
    folder: list[dict[str, Any]] | None = None,
) -> str:
    """The document the audit reads: the new entries only, the dated
    paragraph of prior history they add (when there is one), and a claim per
    diagnosis code they record first (``update/icd.py``)."""
    b = B.sections(base_md)
    rows = exhibit_rows(new_exhibits) + folder_rows(base_md, folder or [], {r["exhibit"] for r in new_exhibits})
    ex = with_rows(b["## Exhibit List"], rows)
    pre = "".join(p.strip() + "\n\n" for p in [pmh or "", *(icd_claims or [])] if p.strip())
    return (
        b["title"].rstrip()
        + "\n\n## Treatment Timeline\n\n## Diagnostic Highlights\n\n## Medical Chronology\n\n"
        + pre
        + "\n\n".join(entries)
        + "\n\n## Exhibit List"
        + ex
    )


def index_rows(run_dir: Path) -> list[str]:
    """The compose pass's INDEX rows (date | provider | codes | file)."""
    rows: list[str] = []
    for p in sorted(run_dir.iterdir()):
        if re.match(r"map-\d+(-\d+)?\.md$", p.name):
            m = re.search(r"##\s*INDEX\s*\n(.*?)(?=\n##\s|\Z)", p.read_text(encoding="utf-8"), re.S)
            if m:
                rows.extend(ln.strip() for ln in m.group(1).splitlines() if ln.strip())
    return rows
