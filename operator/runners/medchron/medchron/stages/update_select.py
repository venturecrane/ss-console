"""`update_select`: the new documents become new exhibits, and only their
pages the delivery did not already read go to composition. $0.

Every pulled document (the named ids, after the firm's exclusions, plus any
attachment folded from a named email) is a candidate, in pull order:

* byte-identical to a delivered exhibit (one the delivered Exhibit List
  names): skipped (it IS a delivered record);
* byte-identical to a FIRM-numbered file in the chronology folder the list
  never named ("Exhibit 6- ..."): read, and it takes that file's number; it
  is never filed again, and the Exhibit List names the firm's file;
* a medical-legal expert report (``update/medlegal``: a life care plan, an
  IME, a record review, by name or first-page text): an exhibit whose every
  page is excluded, never composed, named in Records Reviewed as reviewed and
  not itemized;
* not a PDF or an image: skipped and counted (it cannot be filed as an
  exhibit page the citation would point at);
* its text names a different person (``gates/foreign_identity``): HOLD, exit 2,
  until a person releases it;
* a page-for-page extract of a LARGER new document or of a delivered exhibit,
  by its words (``update/subsets.py``) or by its pictures (``update/pageid``,
  ``stages/update_pages``, which runs before the vision pass):
  not an exhibit; the whole document is, and the extract is recorded in
  ``selection.json`` "subsets" (its id, name and the whole one's exhibit) so
  Records Reviewed names it as a re-filed extract;
* a document NONE of whose pages is read because every one is already in a
  delivered exhibit (by picture confirmed by words, or by words alone; blank
  pages aside): a re-filed copy of delivered pages, recorded in "subsets"
  against the delivered exhibit most of its pages are in, never a new
  exhibit (``select.delivered_home``; a re-printed hospital record was once
  filed again as a new exhibit, 2026-10-14);
* otherwise it becomes ``Exhibit N - <name> (Medical Records)``, numbered after
  the highest exhibit the delivered folder holds, its PDF built in
  ``out/<unit>/`` from the source as it is (so its page numbers are the
  source's), and its pages classified by ``update/select.classify_new``
  against the delivered exhibits' text AND the new documents' pages before it
  (``select.Earlier``: a re-filed copy of a new record is not read twice
  unless it states a clinical value the first did not). A page the
  transcription could not read holds (exit 2): it is never skipped silently.

Writes ``units/<unit>.json`` for compose (selected pages only, page-marked)
and the selection record (``update_collect`` writes the text-mode audit's
``page_map.json`` once the exhibit numbers are final). A run where no page is left to read holds with ``update_held:``:
the delivered chronology already carries everything these documents say.
"""

from __future__ import annotations

import json
import re
import shutil
from io import BytesIO
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ..gates import foreign_identity as FI
from ..update import body as B, medlegal as ML, pageid as PI, pageid_io as PG, select as S, subsets as SUB
from ..update.locate import vendor_name
from .base import StageRun, read_json, read_jsonl
from .transcript import split_pages
from .update_base import HELD, plan, update_dir
from .vision import load_partial

PAGE_SOURCES = {".pdf", ".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp", ".gif", ".webp"}


def _stem(name: str) -> str:
    stem = re.sub(r"\.(pdf|jpe?g|png|tiff?|bmp|gif|webp)$", "", name, flags=re.I)
    return re.sub(r"\s+", " ", re.sub(r"[^A-Za-z0-9&,.'()\- ]+", " ", stem)).strip() or "Record"


def _write_pdf(src: Path, ext: str, dest: Path) -> int:
    """The exhibit's PDF from the source as it is; returns its page count."""
    import pymupdf

    dest.parent.mkdir(parents=True, exist_ok=True)
    if ext == ".pdf":
        shutil.copyfile(src, dest)
    else:
        with pymupdf.open(str(src)) as img:
            dest.write_bytes(img.convert_to_pdf())
    from pypdf import PdfReader

    return len(PdfReader(BytesIO(dest.read_bytes())).pages)


def _candidates(sr: StageRun, delivered_sha: dict[str, str]) -> tuple[list[dict[str, Any]], dict[str, list[str]]]:
    skipped: dict[str, list[str]] = {"byte-identical to a delivered exhibit": [], "not a PDF or an image": []}
    out = []
    for r in read_jsonl(sr.slug_dir / "raw_manifest.jsonl"):
        if not r.get("ok") or r.get("duplicate_of") or not r.get("path"):
            continue
        if r.get("sha256") in delivered_sha:
            skipped["byte-identical to a delivered exhibit"].append(str(r["id"]))
            continue
        if str(r.get("ext") or "").lower() not in PAGE_SOURCES:
            skipped["not a PDF or an image"].append(str(r["id"]))
            continue
        out.append(r)
    return out, skipped


def _identity_holds(sr: StageRun, rows: list[dict[str, Any]]) -> list[str]:
    surnames, dobs = FI.identities(sr.job.units)
    free = FI.released(sr.slug_dir)
    holds = []
    for r in rows:
        if str(r["id"]) in free:
            continue
        found = _page_findings(sr, str(r["id"]), surnames, dobs)
        if found:
            holds.append(f"{r['id']}: {'; '.join(found)}")
    return holds


def _page_findings(sr: StageRun, rid: str, surnames: set[str], dobs: set[str]) -> list[str]:
    """What names another person, page by page. A hit on a TEXT-LAYER page
    is re-read on that page's vision transcript when the vision pass made
    one, and holds only if it survives: a garbled OCR layer once read the
    client's own date of birth as another person's. A page vision never
    read keeps its text-layer finding."""
    tp = sr.slug_dir / "text" / f"{rid}.txt"
    doc = tp.read_text(encoding="utf-8", errors="replace") if tp.is_file() else ""
    pages, firsts = split_pages(doc), FI.first_names(sr.job.units)
    seen = load_partial(sr.slug_dir / "partial" / f"{rid}.jsonl")
    out: set[str] = set()
    for n, (suffix, text) in pages.items():
        found = FI.findings(text, surnames, dobs, firsts, doc)
        if found and "machine transcription" not in suffix and n in seen:
            found = FI.findings(seen[n], surnames, dobs, firsts, doc)
        out |= set(found)
    if not pages and doc:
        out |= set(FI.findings(doc, surnames, dobs, firsts))
    return sorted(out)


def _firm_stem(name: str) -> str:
    """The firm's numbered file name without its "Exhibit N-" and extension."""
    return _stem(re.sub(r"^Exhibit\s*\d+\s*[-–:]\s*", "", name, flags=re.I))


def _exhibit(
    sr: StageRun,
    r: dict[str, Any],
    n: int,
    seen: set[str],
    cdir: Path,
    earlier: S.Earlier,
    firm: str | None = None,
    image: dict[int, str] | None = None,
) -> tuple[dict, dict]:
    """(new exhibit record, units row) for one candidate. `firm`: the name of
    the firm's numbered file in the chronology folder this source is
    byte-identical to; the exhibit then takes that file's number and is never
    filed again (the matter holds it)."""
    out = sr.slug_dir / "out" / sr.unit.unit
    title = f"Exhibit {n} - " + (_firm_stem(firm) if firm else f"{_stem(str(r.get('name') or ''))} (Medical Records)")
    npages = _write_pdf(Path(r["path"]), str(r.get("ext") or "").lower(), out / f"{title}.pdf")
    tp = sr.slug_dir / "text" / f"{r['id']}.txt"
    pages = S.pages_of_text(tp.read_text(encoding="utf-8", errors="replace") if tp.is_file() else "")
    medlegal = ML.is_medlegal([vendor_name(r), firm or ""], pages.get(1) or "")
    if medlegal:  # an expert's report is reviewed, never itemized as treatment
        row = S.row(n, title, {i: (S.EXCLUDED, ML.REASON) for i in range(1, npages + 1)}, pages)
    else:
        row = S.row(n, title, S.classify_new(pages, npages, seen, earlier, image), pages)
    text_path = cdir / f"{r['id']}.txt"
    text_path.write_text(S.selected_text(pages, row["read"]), encoding="utf-8")
    rec = {**row, "id": str(r["id"]), "source_name": vendor_name(r), "sha256": r.get("sha256")}
    if firm:
        rec["reused"] = firm
    if medlegal:
        rec["not_itemized"] = ML.REASON
    unit_row = {
        "id": str(r["id"]),
        "name": title,
        "ext": ".pdf",
        "text_path": str(text_path),
        "compose": bool(row["read"]),
    }
    return rec, unit_row


def delivered_read(sr: StageRun, p: dict[str, Any]) -> list[dict[str, Any]]:
    """The plan's exhibits the delivered Exhibit List names: what the delivery
    READ. A firm-numbered file in the folder the list never named was never
    read, so it covers nothing."""
    base = update_dir(sr) / "base.md"  # update_base writes it with the plan; without it every folder exhibit counts
    listed = (
        B.delivered_numbers(base.read_text(encoding="utf-8"))
        if base.is_file()
        else {e["exhibit"] for e in p["exhibits"]}
    )
    return [e for e in p["exhibits"] if e["exhibit"] in listed]


def run(sr: StageRun) -> int:
    p = plan(sr)
    if not p:
        sr.log("update_select: no update plan (update_base has not run)")
        return 1
    ud = update_dir(sr)
    cdir = ud / "compose"
    cdir.mkdir(parents=True, exist_ok=True)
    read_by_delivery = delivered_read(sr, p)
    listed = {e["exhibit"] for e in read_by_delivery}
    firm = {e["sha256"]: e for e in p["exhibits"] if e["exhibit"] not in listed}
    delivered_sha = {e["sha256"]: e["name"] for e in read_by_delivery}
    rows, skipped = _candidates(sr, delivered_sha)
    holds = _identity_holds(sr, rows)
    if holds:
        # Document ids and a count only: what each record says about the other
        # person (a name, a date of birth) stays in identity_holds.json for SMD,
        # never in the job's reason (2026-10-13).
        (ud / "identity_holds.json").write_text(json.dumps(holds, indent=1), encoding="utf-8")
        ids = [h.split(":", 1)[0] for h in holds]
        sr.log(HELD + f"{len(holds)} document(s) name a different person than the client (document ids: {ids[:5]})")
        return 2
    # Only what the delivery READ is "already read": a firm-numbered file the
    # delivered list never named was never read, so its words exclude nothing.
    seen = S.seen_shingles([sr.slug_dir / "delivered" / e["name"] for e in read_by_delivery])
    ident = PG.load(ud)
    extracts = PG.merge_extracts(
        SUB.refiled_subsets(
            [(str(r["id"]), _texts(sr, str(r["id"]))) for r in rows if str(r["id"]) not in ident.extracts],
            {str(r["id"]) for r in rows if (r.get("sha256") or "") in firm},
        ),
        ident.extracts,
    )
    text_of = _text_of(sr, {int(e["exhibit"]): e["name"] for e in read_by_delivery})
    by_ex = S.shingles_by_exhibit({int(e["exhibit"]): sr.slug_dir / "delivered" / e["name"] for e in read_by_delivery})
    recs, units, earlier, k, whole = [], [], S.Earlier(), 0, {}
    for r in (x for x in rows if str(x["id"]) not in extracts):
        f = firm.get(r.get("sha256") or "")
        n = int(f["exhibit"]) if f else int(p["top_exhibit"]) + k + 1
        found = ident.pages.get(str(r["id"])) or {}
        image = PG.reasons(found, _texts(sr, str(r["id"])), text_of)
        rec, urow = _exhibit(sr, r, n, seen, cdir, earlier, f["name"] if f else None, image)
        sub = None if f else _refiled_whole(sr, rec, found, by_ex)
        if sub is not None:  # every page already delivered: cited there, never a new exhibit
            whole[sub.doc] = sub
            continue
        k += 0 if f else 1
        recs.append(rec)
        units.append(urow)
    extracts = {**extracts, **whole}
    unread = [f"Exhibit {r['exhibit']} p. {r['unread'][:6]}" for r in recs if r["unread"]]
    if unread:
        sr.log(HELD + f"page(s) the transcription could not read: {unread[:5]}")
        return 2
    subs = _subset_records(rows, recs, extracts, {int(e["exhibit"]): e["name"] for e in read_by_delivery})
    _write(sr, ud, recs, units, skipped, subs)
    pages = sum(len(r["read"]) for r in recs)
    if not pages:
        sr.log(HELD + "nothing new to read: every page of the new documents is already in the delivered exhibits")
        return 2
    sr.log(
        f"{len(recs)} new exhibit(s) from {len(rows)} document(s); {pages} page(s) to compose; "
        f"{len(subs)} re-filed extract(s) cited in the whole document; skipped {skipped}"
    )
    return 0


def _refiled_whole(
    sr: StageRun, rec: dict[str, Any], found: dict[int, PG.Page], by_ex: dict[int, set[str]]
) -> SUB.Subset | None:
    """The re-filed-copy record for a new document every page of which the
    delivery already read (``select.delivered_home``), or None. Its exhibit
    PDF is removed: it is cited in the delivered exhibit, never filed again,
    and it takes no exhibit number."""
    if rec.get("not_itemized"):
        return None
    image_ex = {pg: n for pg, h in found.items() if (n := PG.delivered_exhibit(h.other)) is not None}
    home = S.delivered_home(rec, _texts(sr, rec["id"]), by_ex, image_ex)
    if home is None:
        return None
    (sr.slug_dir / "out" / sr.unit.unit / f"{rec['title']}.pdf").unlink(missing_ok=True)
    (update_dir(sr) / "compose" / f"{rec['id']}.txt").unlink(missing_ok=True)
    return SUB.Subset(rec["id"], f"{PI.DELIVERED}{home}", int(rec["pages"]), int(rec["excluded"].get(S.ALREADY, 0)))


def _text_of(sr: StageRun, delivered: dict[int, str]) -> Callable[[str, int], str]:
    """Reads the page a picture matched: a delivered exhibit's text layer
    ("exhibit:N") or a new document's transcription."""
    cache: dict[str, dict[int, str]] = {}

    def get(other: str, page: int) -> str:
        if other not in cache:
            n = PG.delivered_exhibit(other)
            pdf = sr.slug_dir / "delivered" / delivered.get(n, "") if n is not None else None
            if pdf is not None:
                cache[other] = dict(enumerate(S.pdf_pages(pdf), 1)) if pdf.is_file() else {}
            else:
                cache[other] = _texts(sr, other)
        return cache[other].get(page, "")

    return get


def _texts(sr: StageRun, rid: str) -> dict[int, str]:
    tp = sr.slug_dir / "text" / f"{rid}.txt"
    return S.pages_of_text(tp.read_text(encoding="utf-8", errors="replace") if tp.is_file() else "")


def _subset_records(
    rows: list[dict], recs: list[dict], extracts: dict[str, SUB.Subset], delivered: dict[int, str]
) -> list[dict[str, Any]]:
    """One record per re-filed extract: what it is and the exhibit it is
    cited in (the whole document's, or the delivered exhibit it re-files)."""
    by_id = {r["id"]: r for r in recs}
    src = {str(r["id"]): r for r in rows}
    out = []
    for d, s in extracts.items():
        n = PG.delivered_exhibit(s.parent)
        if n is not None:
            sup = {"id": "", "exhibit": n, "title": delivered.get(n, f"Exhibit {n}")}
        else:
            sup = {"id": s.parent, "exhibit": by_id[s.parent]["exhibit"], "title": by_id[s.parent]["title"]}
        out.append(
            {"id": d, "source_name": vendor_name(src[d]), "pages": s.pages, "matched": s.matched, "subset_of": sup}
        )
    return out


def _write(sr: StageRun, ud: Path, recs: list[dict], units: list[dict], skipped: dict, subs: list[dict]) -> None:
    (sr.slug_dir / "units").mkdir(parents=True, exist_ok=True)
    (sr.slug_dir / "units" / f"{sr.unit.unit}.json").write_text(json.dumps(units, indent=1), encoding="utf-8")
    (ud / "new_exhibits.json").write_text(json.dumps(recs, indent=1), encoding="utf-8")
    sel = {"exhibits": recs, "skipped": skipped, "excluded_named": _excluded_named(sr), "subsets": subs}
    (ud / "selection.json").write_text(json.dumps(sel, indent=1), encoding="utf-8")


def _excluded_named(sr: StageRun) -> list[dict[str, Any]]:
    return list(read_json(sr.slug_dir / "named_excluded.json", []) or [])
