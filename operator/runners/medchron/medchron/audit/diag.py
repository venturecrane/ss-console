"""Explain the audit ledger's orphans and carry verdicts across a strip.

An audit row is keyed by (exhibit, page_spec, claim text). A row becomes an
orphan (a key no current claim produces) when the strip removed pages and
renumbered the citation (`remap`: same words, same paper, the verdict
carries), when the pages moved for another reason (`recited`: different
paper, no carry), when a repair changed the words (`rewrite`: re-audit), or
when nothing relates to it any more (`dropped`).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from . import claims as CL


def page_remap(slug_dir: Path, unit: str) -> dict[int, dict[int, int]]:
    """{exhibit: {old_page: new_page}} for the strip on record, or {} when no
    strip has been applied (no result file, or no .orig exhibits)."""
    out_dir = slug_dir / "out" / unit
    result = None
    for name in (f"strip_result-{unit}.json", "strip_result.json"):
        p = slug_dir / name
        if p.is_file():
            result = json.loads(p.read_text(encoding="utf-8"))
            break
    if not result:
        return {}
    pm = out_dir / "page_map.json"
    totals = (
        {e["exhibit"]: e["total_pages"] for e in json.loads(pm.read_text(encoding="utf-8"))} if pm.is_file() else {}
    )
    origs = {p.name for p in out_dir.iterdir() if p.name.endswith(".pdf.orig")} if out_dir.is_dir() else set()
    remap: dict[int, dict[int, int]] = {}
    for ex_s, drops in (result.get("drops") or {}).items():
        ex = int(ex_s)
        if not drops or not any(o.startswith(f"Exhibit {ex} - ") for o in origs):
            continue
        total = totals.get(ex) or ((result.get("new_page_counts") or {}).get(ex_s, 0) + len(drops))
        dropped = set(drops)
        m: dict[int, int] = {}
        n = 0
        for old in range(1, total + 1):
            if old in dropped:
                continue
            n += 1
            m[old] = n
        remap[ex] = m
    return remap


def remap_pages(pages: list[int], remap: dict[int, dict[int, int]], exhibit: int) -> list[int] | None:
    m = remap.get(exhibit)
    if not m:
        return None
    out = []
    for p in pages:
        if p not in m:
            return None  # a cited page was dropped: not a clean remap
        out.append(m[p])
    return out


def classify_orphans(
    rows: list[dict[str, Any]], live: list[dict[str, Any]], remap: dict[int, dict[int, int]]
) -> list[tuple[dict[str, Any], str, dict[str, Any] | None]]:
    cur = {c["key"]: c for c in live}
    by_ex_text: dict[tuple, list] = {}
    by_ex_pages: dict[tuple, list] = {}
    for c in live:
        by_ex_text.setdefault((c["exhibit"], c["claim"][:500]), []).append(c)
        by_ex_pages.setdefault((c["exhibit"], tuple(c["pages"])), []).append(c)
    latest = {r["key"]: r for r in rows if r.get("kind") == "real"}
    out = []
    for key, r in latest.items():
        if key in cur:
            continue
        ex = r.get("exhibit")
        same_text = by_ex_text.get((ex, (r.get("claim") or "")[:500]), [])
        new_pages = remap_pages(r.get("pages") or [], remap, ex)
        if same_text:
            hit = next((c for c in same_text if new_pages and c["pages"] == new_pages), None)
            out.append((r, "remap", hit) if hit is not None else (r, "recited", same_text[0]))
            continue
        at_pages = by_ex_pages.get((ex, tuple(r.get("pages") or [])), []) or (
            by_ex_pages.get((ex, tuple(new_pages)), []) if new_pages else []
        )
        out.append((r, "rewrite", at_pages[0]) if at_pages else (r, "dropped", None))
    return out


def rekey_rows(
    results_path: Path,
    rows: list[dict[str, Any]],
    live: list[dict[str, Any]],
    remap: dict[int, dict[int, int]],
    doc_sha: str | None = None,
) -> int:
    """Carry a verdict across a page remap: for every orphan classed remap
    whose current twin has the remapped pages and no verdict of its own,
    append a copy under the new key. Returns the count carried."""
    have = {r["key"] for r in rows if r.get("kind") == "real"}
    carried = 0
    for r, cls, hit in classify_orphans(rows, live, remap):
        if cls != "remap" or hit is None or hit["key"] in have:
            continue
        if remap_pages(r.get("pages") or [], remap, r.get("exhibit")) != hit["pages"]:
            continue
        rec = dict(r)
        rec.update(
            {
                "key": hit["key"],
                "page_spec": hit["page_spec"],
                "pages": hit["pages"],
                "rekeyed_from": r["key"],
                "rekeyed_pages": r.get("pages"),
            }
        )
        if doc_sha:
            rec["doc_sha"] = doc_sha
        CL.append_row(results_path, rec)
        have.add(hit["key"])
        carried += 1
    return carried
