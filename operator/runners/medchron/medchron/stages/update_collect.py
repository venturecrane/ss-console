"""`update_collect` ($0) and `update_summary` (paid, one call): from the
composed entries to the AUDIT document.

`update_collect` keeps what the new documents add (``update/collect.py``),
spells a new entry's provider the delivered way when that is safe
(``update/providers.py``), settles a date read off a handwritten page against
the provider's typed statements (``update/handwritten.py``), writes a page
legend's codes as their meanings (``update/legend.py``), compacts the new
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
cited), under the line "Additional prior history from records filed through
MM-DD-YY" (the newest added document's filing date on the matter; "since the
prior chronology" when none is on record, never the run date), and that
paragraph goes at the head of the audit document, so each of its sentences is
audited like any new claim. A reply with no heading, no text, or a citation
beyond its source is refused.
"""

from __future__ import annotations

import json
import os
import re
from datetime import date
from pathlib import Path
from typing import Any

from .. import icd_tables, llm, meta_markers, prompts
from ..audit import claims as CL
from ..update import body as B, collect as C, handwritten as H, icd, legend as LG, providers as P
from ..update import records_dates as RD, select as S, uncited as U
from .base import StageRefusal, StageRun, read_json
from .group import Canon
from .summarize import beyond_source
from .update_base import HELD, headings, plan, update_dir


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


def _new_pages(sr: StageRun, provisional: list[dict[str, Any]]) -> dict[int, dict[int, str]]:
    """{new exhibit (as composed): {page: text}}."""
    by_ex: dict[int, dict[int, str]] = {}
    for r in provisional:
        tp = sr.slug_dir / "text" / f"{r['id']}.txt"
        by_ex[int(r["exhibit"])] = (
            S.pages_of_text(tp.read_text(encoding="utf-8", errors="replace")) if tp.is_file() else {}
        )
    return by_ex


def _page_texts(sr: StageRun, provisional: list[dict[str, Any]]) -> tuple[dict[int, dict[int, str]], list[str]]:
    """({new exhibit: {page: text}}, every page the matter's typed statements
    could be on: the new documents' and the delivered exhibits')."""
    by_ex = _new_pages(sr, provisional)
    pool = [t for pages in by_ex.values() for t in pages.values()]
    for e in plan(sr).get("exhibits") or []:
        p = sr.slug_dir / "delivered" / e["name"]
        if p.is_file():
            pool.extend(S.pdf_pages(p))
    return by_ex, pool


def _hand_docs(e: str, by_ex: dict[int, dict[int, str]]) -> list[str] | None:
    """Every page of the documents holding a handwritten page `e` cites, or
    None when it cites none: their typed statements are the provider's own."""
    exs = sorted(
        {
            int(m.group(1))
            for m in B.CITE.finditer(e)
            for p in CL.parse_pages(m.group(2))
            if H.is_handwritten(by_ex.get(int(m.group(1)), {}).get(p))
        }
    )
    return [t for x in exs for _, t in sorted(by_ex[x].items())] if exs else None


def _ledger(ex: int, pages: dict[int, str], cited: set[int]) -> tuple[list[tuple[int, int]], set[int]]:
    """(every row of the document's handwritten pages, in page order: a
    ledger runs on from page to page; the indexes of the rows on `cited`)."""
    rows: list[tuple[int, int]] = []
    on: set[int] = set()
    for p, t in sorted(pages.items()):
        got = H.rows(t)
        if p in cited:
            on |= set(range(len(rows), len(rows) + len(got)))
        rows += got
    return rows, on


def _by_rows(e: str, by_ex: dict[int, dict[int, str]], dos: set[date]) -> tuple[str, date | None] | None:
    """The ledger's row order on the entry's reading (``handwritten.row_verdict``),
    from the first cited document whose ledger has the reading as a row on a
    page the entry cites."""
    r = H.reading_of(e)
    cited: dict[int, set[int]] = {}
    for m in B.CITE.finditer(e):
        cited.setdefault(int(m.group(1)), set()).update(CL.parse_pages(m.group(2)))
    for ex, pages in sorted(cited.items()):
        rows, on = _ledger(ex, by_ex.get(ex, {}), pages)
        got = H.row_verdict(r, rows, dos, on) if r is not None and on else None
        if got is not None:
            return got
    return None


def _settle_dates(
    sr: StageRun, entries: list[str], provisional: list[dict[str, Any]]
) -> tuple[list[str], list[dict[str, str]]]:
    """Each entry cited to a HANDWRITTEN page takes a typed date of service of
    its provider, or shows its reading as written (``update/handwritten.py``).
    A reading that is a row of a handwritten ledger page is settled by the
    ledger's ROW ORDER (``handwritten.row_verdict``); otherwise the provider's
    other readings decide whether a re-date is unambiguous (``decide``). A
    yearless reading (``handwritten.NO_YEAR``) only row order settles. An
    entry dated by a printout's as-of or print date is typed, never a reading.
    Entries are returned in date order: a re-date moves its entry."""
    by_ex, pool = _page_texts(sr, provisional)
    hand: dict[int, set[date]] = {}
    readings: dict[str, list[date]] = {}
    for i, e in enumerate(entries):
        docs = _hand_docs(e, by_ex)
        if docs is None or not B.is_service(e):
            continue  # a printout's as-of or print date is typed and never a visit: no handwritten reading
        hand[i] = H.service_dates(pool, B.provider_of(e), docs)
        r = H.reading_of(e)
        if r is not None:
            readings.setdefault(B.provider_of(e), []).append(r)
    out, log = [], []
    for i, e in enumerate(entries):
        if i not in hand:
            out.append(e)
            if H.NO_YEAR in e.partition("\n")[0]:  # a yearless date no handwritten page settles: SMD reads it
                log.append({"reading": e[:10], "verdict": "open", "year": "not stated", "provider": B.provider_of(e)})
            continue
        by_rows = _by_rows(e, by_ex, hand[i])
        settled, row = H.redate(e, hand[i], tuple(readings.get(B.provider_of(e), ())), by_rows)
        out.append(settled)
        log.append({**row, "provider": B.provider_of(e), **({"by": "ledger row order"} if by_rows else {})})
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


def _process_notes(entries: list[str]) -> tuple[list[str], list[dict[str, str]]]:
    """The entries without the pipeline's notes about its own reading
    (``meta_markers.process_notes``); each note is a report row for SMD."""
    out, rows = [], []
    for e in entries:
        clean, notes = meta_markers.process_notes(e)
        out.append(clean)
        head = clean.partition("\n")[0]
        rows += [{"entry": head, "provider": B.provider_of(clean), "note": n} for n in notes]
    return out, rows


def report_lines(rep: dict[str, Any]) -> list[str]:
    """What SMD reads in the job log about what was kept out of the firm's
    text: every handwritten date left unsettled, every re-date, and every
    process note taken out."""
    out = []
    for r in rep.get("handwritten_dates") or []:
        if r.get("verdict") == "open" and r.get("year"):
            out.append(
                f"date NOT SETTLED: {r.get('provider')} visit read as {r.get('reading')}; the record states no year, "
                f"the year shown is its neighbouring entries', marked {H.NO_YEAR}; SMD checks the page"
            )
        elif r.get("verdict") == "open":
            out.append(
                f"handwritten date NOT SETTLED: {r.get('provider')} visit read as {r.get('reading')}; no typed date "
                f"of service settles it, so it is shown as written, marked {H.MARK_OPEN}; SMD checks the page"
            )
        elif r.get("verdict") == "redated":
            out.append(
                f"handwritten date moved: {r.get('provider')} visit read as {r.get('reading')} is dated "
                f"{r.get('date')} ({r.get('by') or 'the provider statement'})"
            )
    cited = rep.get("uncited_paragraphs") or []
    if cited:
        left = sum(r.get("cite") is None for r in cited)
        out.append(
            f"{len(cited)} uncited paragraph(s) of added entries cited to the page that closes their run, so the "
            f"audit grades them; {left} left uncited (the entry cites no page)"
        )
    for r in rep.get("process_notes") or []:
        out.append(f"note kept out of the chronology: {r.get('entry')} {r.get('provider')}: {str(r.get('note'))[:200]}")
    return out


def _shape(sr: StageRun, base_md: str, rd: Path, provisional: list[dict]) -> tuple[list[str], dict]:
    """The kept entries, dated, their near provider names flagged, and the report."""
    _, base_ents = B.entries_of(base_md)
    # A yearless heading is an entry of its own; it takes its neighbours' year until row order settles it.
    y = re.match(r"\d{4}", str(sr.job.incident_date or ""))
    comp, _ = H.with_years(C.composed(rd), int(y.group()) if y else date.today().year)
    try:
        entries, rep = C.collect(comp, base_ents, sr.job.incident_date, Canon(sr.cfg))
    except C.CollectRefusal as exc:
        raise StageRefusal(str(exc)) from exc
    entries = [meta_markers.strip(e) for e in entries]  # the pipeline's own notes never reach the firm
    entries, notes = _process_notes(entries)
    entries, spelled = P.snap(entries, base_ents, list(B.exhibit_titles(base_md).values()))
    entries, dated = _settle_dates(sr, entries, provisional)
    new_pages = _new_pages(sr, provisional)
    entries, coded = LG.expand(entries, new_pages)  # a legend's code is written as its meaning
    entries, records = RD.mark(entries, new_pages)
    # No paragraph of an added entry goes unaudited (``update/uncited.py``).
    entries, cited = U.cite_all(entries, headings(sr.cfg) if sr.cfg is not None else set())
    return entries, {
        **rep,
        "provider_spelling": spelled,
        "handwritten_dates": dated,
        "legend_codes": coded,
        "records_dates": records,
        "process_notes": notes,
        "uncited_paragraphs": cited,
    }


def run_collect(sr: StageRun) -> int:
    ud, rd = update_dir(sr), sr.slug_dir / "runs" / sr.unit.unit
    out = sr.slug_dir / "out" / sr.unit.unit
    base_md = (ud / "base.md").read_text(encoding="utf-8")
    provisional = read_json(ud / "new_exhibits.json", [])
    entries, rep = _shape(sr, base_md, rd, provisional)
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
    names, composed = P.claims(entries, rep["provider_spelling"])  # a near name's spelling is audited
    (ud / "provider_claims.json").write_text(json.dumps({"claims": names, "composed": composed}), encoding="utf-8")
    claims = claims + names
    _rename(out, provisional, keep, uncited, ud)
    _page_map(out, keep, {r["exhibit"]: r["title"] for r in provisional})
    (ud / "new_exhibits.final.json").write_text(json.dumps(keep, indent=1), encoding="utf-8")
    (ud / "new_exhibits.uncited.json").write_text(json.dumps(uncited, indent=1), encoding="utf-8")
    (ud / "remap.json").write_text(json.dumps({str(k): v for k, v in remap.items()}, indent=1), encoding="utf-8")
    (ud / "new_entries.md").write_text("\n\n".join(entries) + "\n", encoding="utf-8")
    (ud / "compose_report.json").write_text(json.dumps(rep, indent=1), encoding="utf-8")
    (ud / "review_checkpoint.json").unlink(missing_ok=True)  # a new collect is a new update review
    doc = C.audit_document(base_md, entries, keep, icd_claims=claims, folder=plan(sr).get("exhibits") or [])
    (rd / "final-chronology.md").write_text(doc, encoding="utf-8")
    counts = {k: v for k, v in rep.items() if isinstance(v, int)}
    for ln in report_lines(rep):
        sr.log("  " + ln)
    sr.log(
        f"audit document: {len(entries)} new entries, {len(claims)} diagnosis claim(s), {len(keep)} new exhibit(s) "
        f"cited, {len(uncited)} uncited; {counts}"
    )
    return 0


def added_history_line(sr: StageRun, keep: list[dict[str, Any]]) -> str:
    """The line the added prior history sits under. Its date is the newest
    filing date ("created" on the matter's listing) of the documents the
    update adds: authored data, never the day the update ran. With no such
    date it carries none."""
    ids = {str(r.get("id")) for r in keep}
    days = sorted(
        str(d.get("created"))[:10]
        for d in sr.manifest()
        if str(d.get("id")) in ids and re.match(r"\d{4}-\d{2}-\d{2}", str(d.get("created") or ""))
    )
    if not days:
        return "Additional prior history from records filed since the prior chronology"
    y, m, d = days[-1].split("-")
    return f"Additional prior history from records filed through {m}-{d}-{y[2:]}"


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
    head, _, text = block.partition("\n")
    why = (
        "reaches beyond its source: " + str(bad[:5])
        if bad
        else "does not open with its heading"
        if not B.PMH_HEAD.match(head)
        else "carries a heading and no text"
        if not text.strip()
        else ""
    )
    if why:
        sr.log(f"REJECTED: the added prior history {why}")
        return 1
    keep = read_json(ud / "new_exhibits.final.json", [])
    added = added_history_line(sr, keep) + "\n" + text.strip("\n")
    (ud / "pmh.md").write_text(added + "\n", encoding="utf-8")
    claims = read_json(ud / "icd_claims.json", {}).get("claims") or []
    claims += read_json(ud / "provider_claims.json", {}).get("claims") or []
    doc = C.audit_document(base_md, new_ents, keep, added, icd_claims=claims, folder=plan(sr).get("exhibits") or [])
    (rd / "final-chronology.md").write_text(doc, encoding="utf-8")
    sr.log(f"prior history added from {len(added_pre)} pre-incident entr(y/ies), under the delivered one")
    return 0
