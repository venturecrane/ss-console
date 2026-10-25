"""The apply half of ``exhibit_merge``: every artefact that carries a delivered
exhibit number, rewritten for a merge, and the audit ledger carried across.

The list is closed on purpose (``ARTEFACTS``). A merge that renumbered the
chronology and missed one file would ship a package that disagrees with
itself; ``NOT_REWRITTEN`` names every other file that can hold an exhibit
number and why it is left, and tests/test_exhibit_merge.py enumerates a real
run's tree against the two lists, so a new artefact cannot slip past both.
Split from ``exhibit_merge`` (2026-10-25) for size.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Callable

from ..audit import claims as CL
from ..audit.page_text import exhibit_paths
from .base import StageRun, read_json
from .limitations_rules import MERGES_FILE

#: Every artefact a merge rewrites, as globs under the slug directory
#: (``{u}`` the unit, ``{s}`` the joint-matter suffix ``-<unit>`` or "").
ARTEFACTS: tuple[tuple[str, str], ...] = (
    ("runs/{u}/final-chronology.md", "re-pointed and renumbered, duplicates deleted, lists rebuilt"),
    ("runs/{u}/entries_scoped_final.md", "renumbered; B's pre-strip pages mapped through the strip to A's"),
    ("runs/{u}/entries_final.md", "as entries_scoped_final.md (the coverage gate's fallback body)"),
    ("runs/{u}/" + MERGES_FILE, "the record of each merge, read by Limitations"),
    ("out/{u}/page_map.json", "B's row folded into A's (files without start_page), the rest renumbered"),
    ("out/{u}/Exhibit *.pdf", "B deleted, later exhibits renamed to their new number"),
    ("out/{u}/Exhibit *.pdf.orig", "as the exhibit"),
    ("out/{u}/Exhibit *.pdf.stripped", "as the exhibit"),
    ("out/{u}/auditpages/*", "cleared: every cached render is keyed by an exhibit number"),
    ("out/{u}/audit-results.jsonl", "each live claim's real verdict copied under its new key"),
    ("out/{u}/repair-edits.jsonl", "exhibit, pages and key renumbered (the dropped-claims list reads it)"),
    ("out/{u}/review-left-out.json", "exhibit renumbered, B's pages mapped to A's"),
    ("strip_result{s}.json", "keys renumbered, B dropped"),
    ("nonrecord{s}.json", "keys renumbered, B dropped"),
    ("scanned_labels{s}.json", "keys renumbered, B dropped"),
    ("record_control{s}.json", "renumbered; a control page in B mapped to A's"),
    ("drops{s}.json", "manual_drops keys renumbered, B dropped"),
)

#: Files that can carry an exhibit number and are deliberately not rewritten.
NOT_REWRITTEN: tuple[tuple[str, str], ...] = (
    ("runs/{u}/exhibit_map.json", "assemble's per-file numbers, not delivered exhibit numbers"),
    ("runs/{u}/exhibit_remap.json", "written by exhibits and read by no stage"),
    ("runs/{u}/entries.md", "per-file numbers from assemble, before exhibits renumbers"),
    ("runs/{u}/merged.md", "per-file numbers from assemble"),
    ("runs/{u}/clusters.md", "per-file numbers from assemble"),
    ("runs/{u}/entries_scoped.md", "per-file numbers from filter, before exhibits renumbers"),
    ("runs/{u}/entries_condensed.md", "condense's input to summarize, already consumed"),
    ("runs/{u}/entries_full.md", "filter's copy of the full assembly, per-file numbers"),
    ("runs/{u}/delivery.json", "upload's record of what it filed; upload runs after the merge"),
    ("runs/{u}/map-*.md", "the composer's per-file numbers"),
    ("runs/{u}/full_cites.json", "per-file numbers from filter"),
    ("runs/{u}/omitted_*.json", "per-file numbers from filter"),
    ("runs/{u}/log-*.txt", "stage logs: history"),
    ("runs/{u}/state.json", "stage records"),
    ("runs/{u}/usage-ledger.jsonl", "spend history"),
    ("runs/{u}/review*", "the review re-runs after a merge (manifest reopens it)"),
    ("out/{u}/upload_manifest.json", "manifest re-runs after a merge"),
    ("out/{u}/audit-controls.json", "rewritten by every audit round"),
    ("out/{u}/audit-dropped-claims.json", "rewritten by the audit from repair-edits.jsonl"),
    ("out/{u}/audit-retired-controls.json", "control rows are not copied (``copy_verdicts``)"),
    ("out/{u}/*.docx", "render re-runs after a merge"),
)

EXHIBIT_FILE = re.compile(r"^Exhibit (\d+) - (.+\.pdf(?:\.orig|\.stripped)?)$")
LABEL = re.compile(r"^Ex(\d+)p(\d+)$")


def write_json(path: Path, body: Any) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(body, indent=1), encoding="utf-8")
    os.replace(tmp, path)


def write_text(path: Path, text: str) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def keyed(body: dict[str, Any], plan: Any) -> dict[str, Any]:
    """A dict keyed by exhibit number, renumbered with the dropped ones gone.
    A key that is not a number is kept as it is."""
    out: dict[str, Any] = {}
    for k, v in body.items():
        if not str(k).isdigit():
            out[k] = v
        elif int(k) not in plan.dropped:
            out[str(plan.new(int(k)))] = v
    return out


def page_map(pm: list[dict[str, Any]], plan: Any) -> list[dict[str, Any]]:
    """B's row folded into A's: B's files join A's WITHOUT ``start_page`` or
    ``pages`` (their pages are A's; no reader may page into A by them) and
    with ``merged_from``. Every row renumbered and retitled."""
    by = {int(e["exhibit"]): e for e in pm}
    out: list[dict[str, Any]] = []
    for e in pm:
        ex = int(e["exhibit"])
        if ex in plan.dropped:
            continue
        row = dict(e, files=list(e.get("files") or []))
        for m in plan.merges:
            if m.kept == ex:
                for f in by[m.dropped].get("files") or []:
                    g = {k: v for k, v in f.items() if k not in ("start_page", "pages")}
                    row["files"].append({**g, "merged_from": m.dropped, "merged_pages": f.get("pages")})
        row["exhibit"] = plan.new(ex)
        row["title"] = f"Exhibit {plan.new(ex)} - " + str(e["title"]).split(" - ", 1)[1]
        out.append(row)
    return out


def rename_pdfs(out: Path, plan: Any, log: Callable[[str], None]) -> None:
    """B's PDFs (with their .orig and .stripped) deleted, later ones renamed
    down. Ascending order never lands on a name still in use."""
    files = sorted((int(m.group(1)), m.group(2), p) for p in out.iterdir() if (m := EXHIBIT_FILE.match(p.name)))
    for ex, _rest, p in files:
        if ex in plan.dropped:
            p.unlink()
            log(f"  removed {p.name}")
    for ex, rest, p in files:
        if ex in plan.dropped or plan.new(ex) == ex:
            continue
        dest = out / f"Exhibit {plan.new(ex)} - {rest}"
        if dest.exists():
            raise FileExistsError(f"{dest.name} exists; refusing to overwrite it")
        p.rename(dest)


def clear_cache(out: Path) -> int:
    cache, n = out / "auditpages", 0
    if cache.is_dir():
        for f in cache.iterdir():
            if f.is_file():
                f.unlink()
                n += 1
    return n


def slug_files(sr: StageRun, plan: Any) -> None:
    """The slug-level files keyed by exhibit (``strip_result``, the classifier
    labels, the record control, authored drops)."""
    d, sfx = sr.slug_dir, f"-{sr.unit.unit}" if sr.job.joint else ""
    p = d / f"strip_result{sfx}.json"
    if p.is_file():
        r = read_json(p, {}) or {}
        r["drops"] = keyed(r.get("drops") or {}, plan)
        r["new_page_counts"] = keyed(r.get("new_page_counts") or {}, plan)
        r["unverifiable"] = [
            [plan.new(int(u[0])), *u[1:]] for u in r.get("unverifiable") or [] if int(u[0]) not in plan.dropped
        ]
        r["unstripped"] = [
            {**u, "exhibit": plan.new(int(u["exhibit"]))}
            for u in r.get("unstripped") or []
            if int(u["exhibit"]) not in plan.dropped
        ]
        write_json(p, r)
    p = d / f"nonrecord{sfx}.json"
    if p.is_file():
        write_json(p, keyed(read_json(p, {}) or {}, plan))
    p = d / f"scanned_labels{sfx}.json"
    if p.is_file():
        r = read_json(p, {}) or {}
        labels = {}
        for k, v in (r.get("labels") or {}).items():
            m = LABEL.match(k)
            if not m:
                labels[k] = v
            elif int(m.group(1)) not in plan.dropped:
                labels[f"Ex{plan.new(int(m.group(1)))}p{m.group(2)}"] = v
        r["labels"], r["nonrecord"] = labels, keyed(r.get("nonrecord") or {}, plan)
        write_json(p, r)
    p = d / f"record_control{sfx}.json"
    if p.is_file():
        r = read_json(p, {}) or {}
        if str(r.get("exhibit") or "").isdigit():
            ex = int(r["exhibit"])
            page = plan.pre_page(ex, int(r.get("page") or 1)) if ex in plan.dropped else r.get("page")
            r = {**r, "exhibit": plan.new(ex), "page": page if page is not None else r.get("page")}
            write_json(p, r)
    p = d / f"drops{sfx}.json"
    if p.is_file():
        r = read_json(p, {}) or {}
        r["manual_drops"] = keyed(r.get("manual_drops") or {}, plan)
        write_json(p, r)


def _spec(plan: Any, ex: int, spec: Any) -> Any:
    if ex not in plan.dropped or not spec:
        return spec
    from .strip import compress, expand

    try:
        pages = [plan.page(ex, p) for p in expand(str(spec))]
    except ValueError:
        return spec
    return compress([p for p in pages if p]) or spec


def ledgers(out: Path, plan: Any, keymap: dict[str, str]) -> None:
    """``repair-edits.jsonl`` (the audit rebuilds the dropped-claims list the
    attorney's Review Notes print from it) and ``review-left-out.json``: each
    row's exhibit, pages and live key renumbered."""
    p = out / "repair-edits.jsonl"
    if p.is_file():
        rows = CL.read_rows(p)
        for r in rows:
            if isinstance(r.get("exhibit"), int):
                ex = r["exhibit"]
                r["page_spec"] = _spec(plan, ex, r.get("page_spec"))
                r["exhibit"] = plan.new(ex)
            if r.get("key") in keymap:
                r["key"] = keymap[r["key"]]
        write_text(p, "".join(json.dumps(r) + "\n" for r in rows))
    p = out / "review-left-out.json"
    if p.is_file():
        rec = read_json(p, {}) or {}
        for e in rec.get("entries") or []:
            if isinstance(e, dict) and isinstance(e.get("exhibit"), int):
                ex = e["exhibit"]
                if ex in plan.dropped:
                    e["pages"] = [plan.page(ex, int(x)) or x for x in e.get("pages") or [] if str(x).isdigit()]
                e["exhibit"] = plan.new(ex)
        write_json(p, rec)


def record(rd: Path, plan: Any, pm_before: list[dict[str, Any]], pm_after: list[dict[str, Any]]) -> list[dict]:
    """``exhibit_merges.json``: the earlier rows renumbered, this run's added."""
    p = rd / MERGES_FILE
    rows = [dict(r, kept=plan.new(int(r["kept"]))) for r in read_json(p, []) or [] if isinstance(r, dict)]
    by = {int(e["exhibit"]): e for e in pm_before}
    after = {int(e["exhibit"]): e for e in pm_after}
    for m in plan.merges:
        a, b = by[m.kept], by[m.dropped]
        rows.append(
            {
                "kept": plan.new(m.kept),
                "kept_title": after[plan.new(m.kept)]["title"],
                "dropped_old": m.dropped,
                "files_kept": [f["file"] for f in a.get("files") or [] if f.get("file")],
                "files_dropped": [f["file"] for f in b.get("files") or [] if f.get("file")],
                "pages": len(m.pages),
            }
        )
    write_json(p, rows)
    return rows


# ---- the audit ledger --------------------------------------------------------------
def _pages_key(spec: str | None) -> tuple[int, ...]:
    return tuple(sorted(set(CL.parse_pages(spec))))


def copy_verdicts(results: Path, old: str, new: str, plan: Any, keep: tuple[set[int], set[int]]) -> dict[str, str]:
    """Copy each real verdict onto its claim's new key (a key hashes the
    exhibit and pages, so a renumbered claim is a new key), SUPPORTED first so
    a deleted duplicate's verdict never displaces the kept claim's. Returns
    old key -> new key. Control rows are not copied: ``audit/controls`` counts
    every control row on file, so a copy would be counted twice and re-tested
    under a key nothing ran."""
    rows = CL.read_rows(results)
    latest = {r["key"]: r for r in rows if r.get("kind") == "real" and "key" in r}
    stale = CL.pending_reaudit(rows)
    body_new = CL.body_of(new)
    now = {(c["exhibit"], _pages_key(c["page_spec"]), c["claim"]): c for c in CL.extract_claims(body_new, keep[1])}
    sha = CL.doc_sha_of(body_new)
    have = set(latest)
    keymap: dict[str, str] = {}
    olds = CL.extract_claims(CL.body_of(old), keep[0])
    olds.sort(key=lambda c: (latest.get(c["key"]) or {}).get("verdict") != "SUPPORTED")
    for c in olds:
        pages = [plan.page(c["exhibit"], p) for p in CL.parse_pages(c["page_spec"])]
        if None in pages:
            continue
        hit = now.get((plan.new(c["exhibit"]), tuple(sorted({p for p in pages if p})), c["claim"]))
        if hit is None:
            continue
        keymap[c["key"]] = hit["key"]
        r = latest.get(c["key"])
        if r is None or c["key"] in stale or hit["key"] in have:
            continue
        rec = {
            **r,
            "key": hit["key"],
            "exhibit": hit["exhibit"],
            "page_spec": hit["page_spec"],
            "pages": CL.parse_pages(hit["page_spec"]) or [1],
            "rekeyed_from": c["key"],
            "rekeyed_by": "exhibit_merge",
            "doc_sha": sha,
        }
        CL.append_row(results, rec)
        have.add(hit["key"])
    return keymap


def uncached_claims(sr: StageRun) -> list[str]:
    """What the audit would pay for now: every live claim key without a cached
    SUPPORTED verdict (no row, a last verdict not SUPPORTED, or marked for a
    fresh grading), and every SUPPORTED control the round would re-test (not
    retired, not yet re-tested, with another institution to re-test against).
    Empty means the audit costs nothing."""
    from pypdf import PdfReader

    from ..audit import controls, retired
    from ..audit.control_runs import retest_exhibit

    out = sr.slug_dir / "out" / sr.unit.unit
    doc = sr.slug_dir / "runs" / sr.unit.unit / "final-chronology.md"
    pdfs = exhibit_paths(out)
    npages = {k: len(PdfReader(str(p)).pages) for k, p in pdfs.items()}
    rows = CL.read_rows(out / "audit-results.jsonl")
    stale = CL.pending_reaudit(rows)
    claims = CL.extract_claims(CL.body_of(doc.read_text(encoding="utf-8")), set(npages))
    latest = CL.latest_real(rows, {c["key"] for c in claims})
    todo: list[str] = []
    for c in claims:
        if any(p < 1 or p > npages[c["exhibit"]] for p in CL.parse_pages(c["page_spec"])):
            continue  # graded PAGE_OUT_OF_RANGE at no cost
        last = latest.get(c["key"])
        if last is None or last.get("verdict") != "SUPPORTED" or c["key"] in stale:
            todo.append(c["key"])
    gone = retired.keys(out)
    retested = {str(r["retest_of"]) for r in rows if r.get("retest_of")}
    for r in controls.originals(rows):
        k = str(r.get("key") or "")
        if r.get("verdict") != "SUPPORTED" or k in gone or k in retested or "-ctl" not in k:
            continue
        used = k.rpartition("-ctl")[2]
        if retest_exhibit(int(r["exhibit"]), int(used) if used.isdigit() else -1, pdfs) is not None:
            todo.append(k)
    return todo
