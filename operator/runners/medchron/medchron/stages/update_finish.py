"""The update's document, its proofs and its manifest. $0, no model.

* `update_assemble`: the audited new entries into the delivered chronology
  (``update/assemble.py``), the new exhibit files to their final titles, and
  the Records Reviewed paragraph about the update, every figure from the
  run's own artifacts. Refuses when the audit left a new exhibit uncited (a
  repair dropped every claim citing it): its number is fixed by then.
* `update_render`: the updated markdown through the seat renderer; for a
  chronology another renderer made, every delivered paragraph then gets its
  delivered runs back (``docx_md.carry_runs``), bold labels included.
* `update_proofs`: every proof in ``update/proofs.py``; exit 1 (the job FAILS)
  when any does not hold. Proof 10 is the hard gate on a dropped value.
* `update_manifest`: what goes onto the matter, into the delivered chronology's
  own folder: the updated document and the new exhibits. The delivered
  exhibits and every other file there are already on the matter and stay.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any

from .. import icd_tables
from ..audit import claims as CL, coverage as audit_coverage
from ..audit.page_text import exhibit_paths
from ..audit.run import AuditPaths
from ..update import assemble as A, body as B, collect as C, docx_md, proofs as P
from ..update.select import ALREADY, BLANK, OVERLAP
from .base import StageRefusal, StageRun, read_json
from .group import Canon
from .render import chronology_name, render_text
from .update_base import headings, plan, update_dir


def _pages(n: int) -> str:
    return f"{n:,} page" + ("" if n == 1 else "s")


def page_counts(r: dict[str, Any]) -> dict[str, int]:
    """What happened to a new document's pages, counted, never concluded."""
    ex = r.get("excluded") or {}
    return {
        "read": len(r.get("read") or []),
        "repeat": int(ex.get(ALREADY, 0)) + int(ex.get(OVERLAP, 0)),
        "blank": int(ex.get(BLANK, 0)),
        "unread": len(r.get("unread") or []),
    }


def uncited_name(r: dict[str, Any]) -> str:
    """An uncited new document as the Records Reviewed note names it: its
    file name and what was done with its pages. Only what was read is
    stated: an 834-page file was once said to "record no visit" while 64 of
    its image pages had never been read (2026-10-13)."""
    if r.get("not_itemized"):
        return f"{r.get('source_name') or r.get('title') or 'a document'} ({_pages(int(r.get('pages') or 0))}: {r['not_itemized']})"
    c = page_counts(r)
    parts = [f"{_pages(c['read'])} read"]
    if c["repeat"]:
        parts.append(f"{c['repeat']:,} repeating page{'' if c['repeat'] == 1 else 's'} already read")
    if c["blank"]:
        parts.append(f"{_pages(c['blank'])} with no text")
    if c["unread"]:
        parts.append(f"{_pages(c['unread'])} not transcribed")
    name = str(r.get("source_name") or r.get("title") or "a document")
    return f"{name} ({_pages(int(r.get('pages') or 0))}: {', '.join(parts)})"


def update_note(sr: StageRun, ud: Path) -> Any:
    """The Records Reviewed paragraph: counts and titles only."""
    sel = read_json(ud / "selection.json", {})
    uncited = read_json(ud / "new_exhibits.uncited.json", [])
    keep = read_json(ud / "new_exhibits.final.json", [])

    def note(retitled: dict[int, str]) -> str:
        today = time.strftime("%m/%d/%Y")
        docs = len(keep) + len(uncited)
        pages = sum(int(r["pages"]) for r in keep + uncited)
        c = {k: sum(page_counts(r)[k] for r in keep + uncited) for k in ("read", "repeat", "blank")}
        names = "; ".join(f"Exhibit {n} - {t}" for n, t in sorted(retitled.items()))
        out = (
            f"Update of {today}: {docs} medical record document(s) filed on the matter after the earlier delivery "
            f"({_pages(pages)}) were reviewed: {_pages(c['read'])} were read. Visits they record that this "
            f"chronology did not already list were added, before and after the incident, and they are cited as "
            f"{names}."
        )
        if c["repeat"]:
            out += f" {_pages(c['repeat'])} repeat pages already read and were not read again."
        if c["blank"]:
            out += f" {_pages(c['blank'])} carry no text."
        if uncited:
            out += f" {len(uncited)} of those documents are not cited: {'; '.join(uncited_name(r) for r in uncited)}."
        same = len((sel.get("skipped") or {}).get("byte-identical to a delivered exhibit") or [])
        if same:
            out += f" {same} document(s) were identical to an exhibit already delivered."
        return out

    return note


def run_assemble(sr: StageRun) -> int:
    ud, rd = update_dir(sr), sr.slug_dir / "runs" / sr.unit.unit
    out = sr.slug_dir / "out" / sr.unit.unit
    base_md = (ud / "base.md").read_text(encoding="utf-8")
    keep = read_json(ud / "new_exhibits.final.json", [])
    try:
        tables = icd_tables.load(sr.job.install_root)
    except icd_tables.TablesMissing as exc:
        sr.log(str(exc))
        return 1
    try:
        md, rep = A.assemble(
            base_md,
            (rd / "final-chronology.md").read_text(encoding="utf-8"),
            describe=lambda code, on, text: icd_tables.resolve(code, tables, on, text),
            canon=Canon(sr.cfg),
            headings=headings(sr.cfg),
            new_exhibits=keep,
            update_note=update_note(sr, ud),
            folder=plan(sr).get("exhibits") or [],
            provider_names=read_json(ud / "provider_claims.json", {}).get("composed") or {},
        )
    except ValueError as exc:
        raise StageRefusal(str(exc)) from exc
    if rep["new_exhibits_uncited"]:
        raise StageRefusal(
            f"new exhibit(s) {rep['new_exhibits_uncited']} are cited by no added entry after the audit; "
            "their numbers are fixed by the audit, so the update stops rather than leave a gap"
        )
    for r in (x for x in keep if not x.get("reused")):  # a reused exhibit is the firm's file
        final = f"Exhibit {r['exhibit']} - {rep['new_exhibits_titled'][str(r['exhibit'])]}"
        if final != r["title"] and (out / f"{r['title']}.pdf").is_file():
            os.replace(out / f"{r['title']}.pdf", out / f"{final}.pdf")
        r.setdefault("provisional_title", r["title"])
        r["title"] = final
    (ud / "new_exhibits.final.json").write_text(json.dumps(keep, indent=1), encoding="utf-8")
    (ud / "updated-chronology.md").write_text(md, encoding="utf-8")
    rep["provider_spelling_flags"] = read_json(ud / "compose_report.json", {}).get("provider_spelling") or []
    (ud / "assemble_report.json").write_text(json.dumps(rep, indent=1), encoding="utf-8")
    sr.log(
        f"updated chronology: {rep['delivered_entries']} delivered + {rep['new_entries']} new = {rep['output_entries']} entries"
    )
    return 0


def run_render(sr: StageRun) -> int:
    ud = update_dir(sr)
    text = (ud / "updated-chronology.md").read_text(encoding="utf-8")
    dst = sr.slug_dir / "out" / sr.unit.unit / chronology_name(sr)
    rc = render_text(text, dst, cfg=sr.cfg, incident_date=sr.job.incident_date, log=sr.log)
    p = plan(sr)
    if rc != 0 or p.get("renderer") != "foreign":
        return rc
    # A chronology another renderer made was carried as text; its delivered
    # paragraphs get their delivered runs back (bold labels included).
    n = docx_md.carry_runs(sr.slug_dir / "delivered" / p["chronology"]["name"], dst)
    sr.log(f"  delivered formatting carried: {n} paragraph(s) given their delivered runs")
    return 0


def _composed(ud: Path, rd: Path) -> tuple[list[str], set[int]]:
    """Every composed entry in FINAL numbers; an uncited exhibit's entries are
    checked too, under numbers no exhibit uses."""
    remap = {int(k): v for k, v in read_json(ud / "remap.json", {}).items()}
    unc = read_json(ud / "new_exhibits.uncited.json", [])
    for i, r in enumerate(unc):
        remap[int(r["composed_as"])] = 10_000 + i
    comp = C.composed(rd)

    def sub(m: Any) -> str:
        n = int(m.group(1))
        return m.group(0).replace(f"Exhibit {n}", f"Exhibit {remap.get(n, n)}", 1)

    keep = read_json(ud / "new_exhibits.final.json", [])
    return [C.CITED_EX.sub(sub, e) for e in comp], {r["exhibit"] for r in keep} | {10_000 + i for i in range(len(unc))}


def run_proofs(sr: StageRun) -> int:
    ud, rd = update_dir(sr), sr.slug_dir / "runs" / sr.unit.unit
    out = sr.slug_dir / "out" / sr.unit.unit
    p = plan(sr)
    base_md = (ud / "base.md").read_text(encoding="utf-8")
    upd_md = (ud / "updated-chronology.md").read_text(encoding="utf-8")
    keep_ex = set(exhibit_paths(out))
    paths = AuditPaths(sr.slug_dir, sr.unit.unit)
    gate, _ = audit_coverage.check(paths, lambda _m: None)
    composed, new_ex = _composed(ud, rd)
    rep = read_json(ud / "assemble_report.json", {})
    x = P.Inputs(
        delivered_lines=docx_md.summary_lines(sr.slug_dir / "delivered" / p["chronology"]["name"]),
        updated_lines=docx_md.summary_lines(out / chronology_name(sr)),
        base_md=base_md,
        updated_md=upd_md,
        base_claims=CL.extract_claims(CL.body_of(base_md), keep_ex),
        updated_claims=CL.extract_claims(CL.body_of(upd_md), keep_ex),
        audit_rows=CL.read_rows(paths.results),
        composed=composed,
        audit_claims=CL.extract_claims(CL.body_of(paths.doc.read_text(encoding="utf-8")), keep_ex),
        new_exhibits=new_ex,
        gate_passed=gate,
        pre_note_itemized=bool(rep.get("pre_note_itemized")),
    )
    r = P.run(x)
    for ln in r.lines:
        sr.log(ln)
    (ud / "proofs.json").write_text(json.dumps({"lines": r.lines, "fails": r.fails}, indent=1), encoding="utf-8")
    if r.fails:
        sr.log(f"update_proofs_failed: {', '.join(r.fails)}; nothing is filed")
        return 1
    sr.log("PROOFS PASS")
    return 0


def run_manifest(sr: StageRun) -> int:
    ud = update_dir(sr)
    out = sr.slug_dir / "out" / sr.unit.unit
    p = plan(sr)
    # A new exhibit that reuses a firm-numbered file is already on the matter.
    filed = [r for r in read_json(ud / "new_exhibits.final.json", []) if not r.get("reused")]
    names = [chronology_name(sr)] + [f"{r['title']}.pdf" for r in filed]
    manifest = []
    for n in names:
        f = out / n
        if not f.is_file():
            sr.log(f"missing deliverable: {n}")
            return 1
        data = f.read_bytes()
        manifest.append(
            {
                "name": n,
                "folder": p["folder_name"],
                "local_path": str(f),
                "sha256": hashlib.sha256(data).hexdigest(),
                "bytes": len(data),
            }
        )
    (out / "upload_manifest.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    (ud / "already_filed.json").write_text(json.dumps([e["name"] for e in p["exhibits"]], indent=1), encoding="utf-8")
    sr.log(
        f"{len(manifest)} file(s) into the delivered chronology's folder '{p['folder_name']}'; replaces {p['chronology']['name']} "
        f"(renderer of the delivered document: {p.get('renderer', 'seat')})"
    )
    _, ents = B.entries_of((ud / "updated-chronology.md").read_text(encoding="utf-8"))
    sr.log(f"  {len(ents)} entries in the updated chronology")
    return 0
