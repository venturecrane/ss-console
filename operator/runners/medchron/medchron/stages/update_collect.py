"""`update_collect` ($0) and `update_summary` (paid, one call): from the
composed entries to the AUDIT document.

`update_collect` keeps what the new documents add (``update/collect.py``),
spells a new entry's provider the delivered way when that is safe
(``update/providers.py``), settles a date read off a handwritten page against
the provider's typed statements (``update/handwritten.py``), compacts the new
exhibits' numbers before the audit can key a claim to them, renames their
files to match, and writes the audit document over
``runs/<unit>/final-chronology.md``: the new entries, plus a claim per
diagnosis code they record first (``update/icd.py``), so every Diagnostic
Highlights row the update adds is audited. Nothing new to add holds with
``update_held:`` (the delivered chronology already lists every visit the new
documents record), naming how many documents were read.

`update_summary` runs only when the update adds a pre-incident visit. The
delivered Prior Medical History is NEVER rewritten: the added pre-incident
entries alone are summarised with the summarize stage's own prompt, model
tier and containment rule (a sentence may cite only pages its source entries
cited), under the line "Additional prior history from records received
MM-DD-YY", and that paragraph goes at the head of the audit document, so each
of its sentences is audited like any new claim.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

from .. import icd_tables, llm, prompts
from ..audit import claims as CL
from ..update import body as B, collect as C, handwritten as H, icd, providers as P, select as S
from .base import StageRefusal, StageRun, read_json
from .group import Canon
from .summarize import beyond_source
from .update_base import HELD, plan, update_dir


def _rename(
    out: Path, before: list[dict[str, Any]], after: list[dict[str, Any]], uncited: list[dict], ud: Path
) -> None:
    """Exhibit files to their compacted titles, through temporary names (two
    exhibits may swap numbers); an uncited one leaves out/. Rerunnable."""
    old = {r["exhibit"]: r["title"] for r in before}
    staged = []
    for r in after:
        src = out / f"{old[r['composed_as']]}.pdf"
        dst = out / f"{r['title']}.pdf"
        if src == dst or not src.is_file():
            continue
        tmp = out / f".renumber-{r['composed_as']}.pdf"
        os.replace(src, tmp)
        staged.append((tmp, dst))
    for tmp, dst in staged:
        os.replace(tmp, dst)
    (ud / "uncited").mkdir(exist_ok=True)
    for r in uncited:
        src = out / f"{old[r['composed_as']]}.pdf"
        if src.is_file():
            os.replace(src, ud / "uncited" / src.name)


def _page_map(out: Path, keep: list[dict[str, Any]], provisional: dict[int, str]) -> None:
    """Exhibit -> its one source file, for the text-mode audit's page index.
    The file name is the one compose read it under (the units file row)."""
    rows = [
        {
            "exhibit": r["exhibit"],
            "title": r["title"],
            "total_pages": r["pages"],
            "files": [{"file": f"{provisional[r['composed_as']]}.pdf", "start_page": 1, "pages": r["pages"]}],
        }
        for r in keep
    ]
    (out / "page_map.json").write_text(json.dumps(rows, indent=1), encoding="utf-8")


def _page_texts(sr: StageRun, provisional: list[dict[str, Any]]) -> tuple[dict[int, dict[int, str]], list[str]]:
    """({new exhibit: {page: text}}, every page the matter's typed statements
    could be on: the new documents' and the delivered exhibits')."""
    by_ex: dict[int, dict[int, str]] = {}
    for r in provisional:
        tp = sr.slug_dir / "text" / f"{r['id']}.txt"
        by_ex[int(r["exhibit"])] = (
            S.pages_of_text(tp.read_text(encoding="utf-8", errors="replace")) if tp.is_file() else {}
        )
    pool = [t for pages in by_ex.values() for t in pages.values()]
    for e in plan(sr).get("exhibits") or []:
        p = sr.slug_dir / "delivered" / e["name"]
        if p.is_file():
            pool.extend(S.pdf_pages(p))
    return by_ex, pool


def _settle_dates(
    sr: StageRun, entries: list[str], provisional: list[dict[str, Any]]
) -> tuple[list[str], list[dict[str, str]]]:
    """Each entry cited to a HANDWRITTEN page takes the one typed date of
    service of its provider near its reading, or shows the reading as an
    open date (``update/handwritten.py``)."""
    by_ex, pool = _page_texts(sr, provisional)
    out, log = [], []
    for e in entries:
        hand = any(
            H.is_handwritten(by_ex.get(int(m.group(1)), {}).get(p))
            for m in B.CITE.finditer(e)
            for p in CL.parse_pages(m.group(2))
        )
        if not hand:
            out.append(e)
            continue
        settled, row = H.redate(e, H.service_dates(pool, B.provider_of(e)))
        out.append(settled)
        log.append({**row, "provider": B.provider_of(e)})
    return sorted(out, key=B.iso), log


def _icd_claims(sr: StageRun, ud: Path, rd: Path, base_md: str, entries: list[str]) -> list[str]:
    tables = icd_tables.load(sr.job.install_root)
    rows = C.index_rows(rd)
    (ud / "new_index.json").write_text(json.dumps(rows, indent=1), encoding="utf-8")
    claims, log = icd.claims(
        rows,
        entries,
        B.table_rows(B.sections(base_md)["## Diagnostic Highlights"]),
        lambda code, on, text: icd_tables.resolve(code, tables, on, text),
    )
    (ud / "icd_claims.json").write_text(json.dumps({"claims": claims, "log": log}, indent=1), encoding="utf-8")
    return claims


def _shape(sr: StageRun, base_ents: list[str], rd: Path, provisional: list[dict]) -> tuple[list[str], dict]:
    """The kept entries, respelled and dated, and the report."""
    try:
        entries, rep = C.collect(C.composed(rd), base_ents, sr.job.incident_date, Canon(sr.cfg))
    except C.CollectRefusal as exc:
        raise StageRefusal(str(exc)) from exc
    entries, spelled = P.snap(entries, base_ents)
    entries, dated = _settle_dates(sr, entries, provisional)
    return entries, {**rep, "provider_spelling": spelled, "handwritten_dates": dated}


def run_collect(sr: StageRun) -> int:
    ud, rd = update_dir(sr), sr.slug_dir / "runs" / sr.unit.unit
    out = sr.slug_dir / "out" / sr.unit.unit
    base_md = (ud / "base.md").read_text(encoding="utf-8")
    _, base_ents = B.entries_of(base_md)
    provisional = read_json(ud / "new_exhibits.json", [])
    entries, rep = _shape(sr, base_ents, rd, provisional)
    entries, keep, uncited, remap = C.compact(entries, provisional)
    if not keep:
        sr.log(
            HELD + f"nothing new to add: the {len(provisional)} new document(s) record no visit the delivered "
            f"chronology does not already list ({ {k: v for k, v in rep.items() if isinstance(v, int)} })"
        )
        return 2
    try:
        claims = _icd_claims(sr, ud, rd, base_md, entries)
    except icd_tables.TablesMissing as exc:
        sr.log(str(exc))
        return 1
    _rename(out, provisional, keep, uncited, ud)
    _page_map(out, keep, {r["exhibit"]: r["title"] for r in provisional})
    (ud / "new_exhibits.final.json").write_text(json.dumps(keep, indent=1), encoding="utf-8")
    (ud / "new_exhibits.uncited.json").write_text(json.dumps(uncited, indent=1), encoding="utf-8")
    (ud / "remap.json").write_text(json.dumps({str(k): v for k, v in remap.items()}, indent=1), encoding="utf-8")
    (ud / "new_entries.md").write_text("\n\n".join(entries) + "\n", encoding="utf-8")
    (ud / "compose_report.json").write_text(json.dumps(rep, indent=1), encoding="utf-8")
    doc = C.audit_document(base_md, entries, keep, icd_claims=claims, folder=plan(sr).get("exhibits") or [])
    (rd / "final-chronology.md").write_text(doc, encoding="utf-8")
    counts = {k: v for k, v in rep.items() if isinstance(v, int)}
    sr.log(
        f"audit document: {len(entries)} new entries, {len(claims)} diagnosis claim(s), {len(keep)} new exhibit(s) "
        f"cited, {len(uncited)} uncited; {counts}"
    )
    return 0


def run_summary(sr: StageRun) -> int:
    """The prior history the added pre-incident entries hold, as a dated
    paragraph under the delivered Prior Medical History, which is never
    rewritten (the firm has read it; a rewrite could lose or move a fact)."""
    ud, rd = update_dir(sr), sr.slug_dir / "runs" / sr.unit.unit
    base_md = (ud / "base.md").read_text(encoding="utf-8")
    _, new_ents = B.split_body("\n\n" + (ud / "new_entries.md").read_text(encoding="utf-8"))
    added_pre = [e for e in new_ents if B.iso(e) < sr.job.incident_date]
    if not added_pre:
        sr.log("no pre-incident visit added; the delivered Prior Medical History stands alone")
        return 0
    r = sr.doorway.call(
        "summarize",
        model=llm.model_for(sr.cfg, "judgment"),
        system=prompts.load("summarize-system", sr.cfg),
        max_tokens=16000,
        messages=[{"role": "user", "content": "\n\n".join(added_pre)}],
        effort="high",
        timeout=240.0,
        custom_id="update-summarize",
    )
    block = r.text.strip()
    bad = beyond_source("\n".join(added_pre), block)
    if bad or not B.PMH_HEAD.match(block.splitlines()[0] if block else ""):
        why = "reaches beyond its source: " + str(bad[:5]) if bad else "does not open with its heading"
        sr.log(f"REJECTED: the added prior history {why}")
        return 1
    stamp = read_json(ud / "received.json", {}).get("mmddyy") or time.strftime("%m-%d-%y")
    (ud / "received.json").write_text(json.dumps({"mmddyy": stamp}), encoding="utf-8")
    added = f"Additional prior history from records received {stamp}\n" + block.split("\n", 1)[1].strip("\n")
    (ud / "pmh.md").write_text(added + "\n", encoding="utf-8")
    keep = read_json(ud / "new_exhibits.final.json", [])
    claims = read_json(ud / "icd_claims.json", {}).get("claims") or []
    doc = C.audit_document(base_md, new_ents, keep, added, icd_claims=claims, folder=plan(sr).get("exhibits") or [])
    (rd / "final-chronology.md").write_text(doc, encoding="utf-8")
    sr.log(f"prior history added from {len(added_pre)} pre-incident entr(y/ies), under the delivered one")
    return 0
