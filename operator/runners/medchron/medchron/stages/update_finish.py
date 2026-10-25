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
import re
import time
from datetime import date
from pathlib import Path
from typing import Any

from .. import icd_tables
from ..audit import claims as CL, coverage as audit_coverage, retired
from ..audit.page_text import exhibit_paths
from ..audit.run import AuditPaths
from ..update import assemble as A, body as B, collect as C, docx_md, handwritten as H, proofs as P
from ..update import review_repair as RR, reviewed as RV
from ..update.billing_page import BILLING
from ..update.select import ALREADY, BLANK, OVERLAP
from .base import StageRefusal, StageRun, read_json, read_jsonl
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
        "billing": int(ex.get(BILLING, 0)),
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
    if c["billing"]:
        parts.append(f"{_pages(c['billing'])} of billing")
    if c["unread"]:
        parts.append(f"{_pages(c['unread'])} not transcribed")
    name = str(r.get("source_name") or r.get("title") or "a document")
    return f"{name} ({_pages(int(r.get('pages') or 0))}: {', '.join(parts)})"


def added_when(ud: Path, incident: str | None) -> str:
    """ ", before the incident", ", after the incident", both, or nothing:
    what the audited added entries (``runs/<unit>/final-chronology.md``)
    actually are, never a fixed phrase."""
    doc = ud.parent / "final-chronology.md"
    if not incident or not doc.is_file():
        return ""
    try:
        _, ents = B.entries_of(doc.read_text(encoding="utf-8"))
    except ValueError:
        return ""
    sides = [
        s
        for s, hit in (
            ("before", any(B.iso(e) < incident for e in ents)),
            ("after", any(B.iso(e) >= incident for e in ents)),
        )
        if hit
    ]
    return f", {' and '.join(sides)} the incident" if sides else ""


def update_note(sr: StageRun, ud: Path) -> Any:
    """The Records Reviewed paragraph: counts and titles only."""
    sel = read_json(ud / "selection.json", {})
    uncited = read_json(ud / "new_exhibits.uncited.json", [])
    keep = read_json(ud / "new_exhibits.final.json", [])
    # Every document the pull holds, the copies it set aside included (``update/reviewed.py``).
    filed = RV.accounted(
        read_jsonl(ud.parents[2] / "raw_manifest.jsonl"),
        sel,
        read_json(ud / "update_plan.json", {}).get("exhibits") or [],
        keep,
        uncited,
    )

    when = added_when(ud, getattr(getattr(sr, "job", None), "incident_date", None))

    def note(retitled: dict[int, str]) -> str:
        today = time.strftime("%m/%d/%Y")
        pages = sum(int(r["pages"]) for r in keep + uncited)
        c = {k: sum(page_counts(r)[k] for r in keep + uncited) for k in ("read", "repeat", "blank")}
        names = "; ".join(f"Exhibit {n} - {t}" for n, t in sorted(retitled.items()))
        out = (
            f"Update of {today}: {filed['docs']} medical record document(s) filed on the matter after the earlier "
            f"delivery were reviewed. Of the {_pages(pages)} of new records, {_pages(c['read'])} were read. Visits "
            f"they record that this chronology did not already list were added{when}, and they are cited as {names}."
        )
        if c["repeat"]:
            out += f" {_pages(c['repeat'])} repeat pages already read and were not read again."
        if c["blank"]:
            out += f" {_pages(c['blank'])} carry no text."
        if uncited:
            out += f" {len(uncited)} of those documents are not cited: {'; '.join(uncited_name(r) for r in uncited)}."
        if filed["copies"]:
            n = len(filed["copies"])
            what = "was a copy and was" if n == 1 else "were copies and were"
            out += f" {n} {what} not read again: {'; '.join(filed['copies'])}."
        refiled = [A.refiled_row(r) for r in keep if r.get("refiled")]
        if refiled:
            out += (
                f" {len(refiled)} of those documents repeat visits the chronology already listed: {'; '.join(refiled)}."
            )
        if filed["unreadable"]:
            out += (
                f" {len(filed['unreadable'])} could not be read as record pages (not a PDF or an image): "
                f"{'; '.join(filed['unreadable'])}."
            )
        if filed["unread"]:
            out += f" {len(filed['unread'])} were not read: {'; '.join(filed['unread'])}."
        if filed.get("left_out"):
            n = len(filed["left_out"])
            what = "document names" if n == 1 else "documents name"
            out += (
                f" {n} {what} a person other than the client, so {'it was' if n == 1 else 'they were'} left "
                f"out of this chronology: {'; '.join(filed['left_out'])}."
            )
        for why, names in sorted((filed.get("excluded") or {}).items()):
            n = len(names)
            out += f" {n} requested document{'' if n == 1 else 's'} {'was' if n == 1 else 'were'} not read ({why}): "
            out += f"{'; '.join(names)}."
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
    for r in (x for x in keep if not x.get("reused") and not x.get("refiled")):  # the firm's file, or a re-filed copy
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


def _remap(ud: Path) -> dict[int, int]:
    """{composed number: final number}; an uncited exhibit's under numbers no
    exhibit uses."""
    remap = {int(k): v for k, v in read_json(ud / "remap.json", {}).items()}
    for i, r in enumerate(read_json(ud / "new_exhibits.uncited.json", [])):
        remap[int(r["composed_as"])] = 10_000 + i
    return remap


def _composed(ud: Path, rd: Path, incident_date: str | None = None) -> tuple[list[str], set[int]]:
    """Every composed entry in FINAL numbers; an uncited exhibit's entries are
    checked too, under numbers no exhibit uses. A yearless date takes its
    neighbours' year exactly as collect gave it one (``handwritten.with_years``,
    same entries, same fallback), so proof 10 reads it on the date the update
    carries it under rather than on no date at all."""
    remap = _remap(ud)
    y = re.match(r"\d{4}", str(incident_date or ""))
    comp, _ = H.with_years(C.composed(rd), int(y.group()) if y else date.today().year)

    def sub(m: Any) -> str:
        n = int(m.group(1))
        return m.group(0).replace(f"Exhibit {n}", f"Exhibit {remap.get(n, n)}", 1)

    keep = read_json(ud / "new_exhibits.final.json", [])
    unc = read_json(ud / "new_exhibits.uncited.json", [])
    return [C.CITED_EX.sub(sub, e) for e in comp], {r["exhibit"] for r in keep} | {10_000 + i for i in range(len(unc))}


def _redates(ud: Path) -> tuple[set[str], list[tuple[str, str, set[int]]]]:
    """The collect stage's handwritten re-dates (``compose_report.json``), in
    FINAL numbers: the readings they removed, and where each entry moved."""
    named, moves = H.moves_of(read_json(ud / "compose_report.json", {}))
    remap = _remap(ud)
    return named, [(frm, to, {remap.get(n, n) for n in ex}) for frm, to, ex in moves]


def run_proofs(sr: StageRun) -> int:
    ud, rd = update_dir(sr), sr.slug_dir / "runs" / sr.unit.unit
    out = sr.slug_dir / "out" / sr.unit.unit
    p = plan(sr)
    base_md = (ud / "base.md").read_text(encoding="utf-8")
    upd_md = (ud / "updated-chronology.md").read_text(encoding="utf-8")
    keep_ex = set(exhibit_paths(out))
    paths = AuditPaths(sr.slug_dir, sr.unit.unit)
    gate, _ = audit_coverage.check(paths, lambda _m: None)
    composed, new_ex = _composed(ud, rd, sr.job.incident_date)
    redate_named, redate_moves = _redates(ud)
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
        respelled=dict(rep.get("provider_spelling_from_audit") or {}),
        review_named=RR.named_values(read_json(ud / "review_repair.json", {})) | redate_named,
        redate_moves=redate_moves,
        retired_controls=retired.keys(out),
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
    # A new exhibit that reuses a firm-numbered file is already on the matter;
    # a re-filed copy of a delivered exhibit (the update review) is not filed again.
    filed = [r for r in read_json(ud / "new_exhibits.final.json", []) if not r.get("reused") and not r.get("refiled")]
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
