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
* its text names a different person (``gates/foreign_identity``): left out
  BY RULE (2026-10-24, the Captain; it used to hold until a person released
  it), recorded ``by: "rule"`` in ``identity_release.json``, kept in
  ``selection.json`` "skipped" under the rule's own reason, and named in the
  update's Records Reviewed paragraph (``update/reviewed.py``); what it says
  about the other person stays in ``identity_holds.json`` for SMD;
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
  against the delivered exhibits' text AND the new documents' pages read
  before it (``select.Earlier``: a re-filed copy of a new record is not read
  twice unless it states a clinical value the first did not). The new
  documents are READ largest first (``pageid.reading_order``; numbered in
  pull order), so a page two of them share is read in the larger; a document
  every page of which repeats one read before it is a re-filed copy, cited
  there and never an exhibit (``select.new_home``); and of two readings of
  one page, the one that adds nothing is not read (``update/twins``). A
  124-page file that was pages 281 to 404 of a 404-page file was once filed
  as a second exhibit and a visit itemized twice (2026-10-14). A page the
  transcription could not read holds (exit 2): it is never skipped silently.
  The one exception (2026-10-25): an IMAGE file (a photograph) the
  transcription could not read is set aside by rule and named
  (``_set_aside``), never a hold.

Writes ``units/<unit>.json`` for compose (selected pages only, page-marked)
and the selection record (``update_collect`` writes the text-mode audit's
``page_map.json`` once the exhibit numbers are final). A run where no page is
left to read is an ANSWER (2026-10-25, ``update/outcome``): the job ends
delivered already current, nothing filed, every document's reason recorded.
"""

from __future__ import annotations

import json
import re
import shutil
from dataclasses import dataclass, field
from io import BytesIO
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ..gates import foreign_identity as FI
from ..update import body as B, medlegal as ML, pageid as PI, pageid_io as PG, select as S, subsets as SUB, twins as TW
from ..update import outcome as UO
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
    skipped: dict[str, list[str]] = {
        "byte-identical to a delivered exhibit": [],
        "not a PDF or an image": [],
        FI.WITHHELD_REASON: [],
    }
    withheld = FI.withheld(sr.slug_dir)
    out = []
    for r in read_jsonl(sr.slug_dir / "raw_manifest.jsonl"):
        if not r.get("ok") or r.get("duplicate_of") or not r.get("path"):
            continue
        if FI.matches(str(r["id"]), withheld):
            # Keyed by how it is disclosed: the firm's direction, or the rule's.
            skipped.setdefault(FI.withheld_reason(sr.slug_dir, str(r["id"])), []).append(str(r["id"]))
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
    """Each row's id with what names another person (SMD's detail), as
    ``"<id>: <findings>"``; [] when none does."""
    surnames, dobs = FI.identities(sr.job.units)
    free = FI.released(sr.slug_dir)
    holds = []
    for r in rows:
        if FI.matches(str(r["id"]), free):
            continue
        found = _page_findings(sr, str(r["id"]), surnames, dobs)
        if found:
            holds.append(f"{r['id']}: {'; '.join(found)}")
    return holds


def _leave_out_foreign(
    sr: StageRun, ud: Path, rows: list[dict[str, Any]], skipped: dict[str, list[str]]
) -> list[dict[str, Any]]:
    """`rows` without the ones whose text names another person: each is
    withheld by rule (2026-10-24) and recorded in `skipped` under the rule's
    reason, which the Records Reviewed paragraph names. The findings (a name,
    a date of birth) stay in identity_holds.json for SMD, never in the log."""
    holds = _identity_holds(sr, rows)
    if not holds:
        return rows
    (ud / "identity_holds.json").write_text(json.dumps(holds, indent=1), encoding="utf-8")
    ids = [h.split(":", 1)[0] for h in holds]
    FI.withhold_by_rule(sr.slug_dir, ids, ground=FI.RULE_ANOTHER_PERSON)
    skipped.setdefault(FI.RULE_REASONS[FI.RULE_ANOTHER_PERSON], []).extend(ids)
    sr.log(
        f"{len(ids)} document(s) name a different person than the client; left out by rule and disclosed "
        f"(document ids: {ids[:5]})"
    )
    return [r for r in rows if str(r["id"]) not in set(ids)]


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


@dataclass
class _Read:
    """One candidate's pages as decided (``_classify``)."""

    pdf: Path  # the exhibit PDF, built under a provisional name
    pages: dict[int, str]
    decisions: dict[int, tuple[str, str]]
    medlegal: bool
    home: str | None  # the new document it re-files whole (``select.new_home``)
    #: {page: (document, page)}: a page read from a smaller copy's richer
    #: reading of the same scan (``update/twins``).
    readings: dict[int, tuple[str, int]] = field(default_factory=dict)


def _classify(
    sr: StageRun,
    rows: list[dict[str, Any]],
    seen: S.Delivered,
    ident: PG.Loaded,
    text_of: Callable[[str, int], str],
    firm: dict[str, str],
) -> dict[str, _Read]:
    """{id: _Read} for every candidate, its pages decided in READING order
    (``pageid.reading_order``: the most pages first). A page two new
    documents share is read in the larger and is a repeat in the smaller,
    and a smaller document every page of which repeats one read before it is
    re-filed whole there (``select.new_home``). `firm`: {id: the firm's
    numbered file name}; such a file is never re-filed whole."""
    out = sr.slug_dir / "out" / sr.unit.unit
    pdfs = {str(r["id"]): out / f".pending-{r['id']}.pdf" for r in rows}
    sizes = [
        (str(r["id"]), _write_pdf(Path(r["path"]), str(r.get("ext") or "").lower(), pdfs[str(r["id"])])) for r in rows
    ]
    by_id, npages, order = {str(r["id"]): r for r in rows}, dict(sizes), PI.reading_order(sizes)
    earlier, got = S.Earlier(), {}
    for rid in order:
        image = PG.reasons(ident.pages.get(rid) or {}, _texts(sr, rid), text_of)
        got[rid] = _read_one(sr, by_id[rid], pdfs[rid], npages[rid], seen, earlier, firm.get(rid), image)
    _settle_twins(got, ident)
    by_doc: dict[str, set[str]] = {}
    for rid in (d for d in order if not got[d].medlegal):
        c = got[rid]
        hits = {pg: h.other for pg, h in (ident.pages.get(rid) or {}).items() if c.decisions[pg][1] == S.OVERLAP}
        if rid not in firm:
            c.home = S.new_home(S.row(0, "", c.decisions, c.pages), c.pages, by_doc, hits)
        by_doc[rid] = set().union(set(), *(S.shingles(t) for t in c.pages.values()))
    return got


def _settle_twins(got: dict[str, _Read], ident: PG.Loaded) -> None:
    """Of a page two new documents both still read, picture-paired (or at
    its neighbours' offset), the reading that adds nothing is not read
    (``update/twins``)."""
    for rid, c in got.items():
        by_other: dict[str, dict[int, int]] = {}
        for pg, h in (ident.pages.get(rid) or {}).items():
            if h.other in got and not got[h.other].medlegal and not c.medlegal:
                by_other.setdefault(h.other, {})[pg] = h.page
        for other, pairs in by_other.items():
            o = got[other]
            for p, q in TW.settle((c.pages, c.decisions), (o.pages, o.decisions), pairs).items():
                o.readings[p] = (rid, q)


def _read_one(
    sr: StageRun,
    r: dict[str, Any],
    pdf: Path,
    npages: int,
    seen: S.Delivered,
    earlier: S.Earlier,
    firm: str | None,
    image: dict[int, str] | None,
) -> _Read:
    """One candidate's pages decided against the delivery and the new pages
    read before it (`earlier`; this document's pages join it)."""
    pages = _texts(sr, str(r["id"]))
    if ML.is_medlegal([vendor_name(r), firm or ""], pages.get(1) or ""):
        return _Read(pdf, pages, {i: (S.EXCLUDED, ML.REASON) for i in range(1, npages + 1)}, True, None)
    return _Read(pdf, pages, S.classify_new(pages, npages, seen, earlier, image), False, None)


def _exhibit(
    sr: StageRun,
    r: dict[str, Any],
    n: int,
    seen: S.Delivered,
    cdir: Path,
    earlier: S.Earlier,
    firm: str | None = None,
    image: dict[int, str] | None = None,
) -> tuple[dict, dict]:
    """(new exhibit record, units row) for one candidate decided now, in the
    order this is called (``_classify`` decides a run's candidates in reading
    order and builds each with ``_build``)."""
    pdf = sr.slug_dir / "out" / sr.unit.unit / f".pending-{r['id']}.pdf"
    npages = _write_pdf(Path(r["path"]), str(r.get("ext") or "").lower(), pdf)
    return _build(sr, r, n, cdir, _read_one(sr, r, pdf, npages, seen, earlier, firm, image), firm)


def _build(sr: StageRun, r: dict[str, Any], n: int, cdir: Path, c: _Read, firm: str | None = None) -> tuple[dict, dict]:
    """(new exhibit record, units row) for one decided candidate. `firm`: the
    name of the firm's numbered file in the chronology folder this source is
    byte-identical to; the exhibit then takes that file's number and is never
    filed again (the matter holds it)."""
    title = f"Exhibit {n} - " + (_firm_stem(firm) if firm else f"{_stem(str(r.get('name') or ''))} (Medical Records)")
    c.pdf.replace(c.pdf.with_name(f"{title}.pdf"))
    row = S.row(n, title, c.decisions, c.pages)
    text_path = cdir / f"{r['id']}.txt"
    text_path.write_text(S.selected_text(c.pages, row["read"]), encoding="utf-8")
    rec = {**row, "id": str(r["id"]), "source_name": vendor_name(r), "sha256": r.get("sha256")}
    if firm:
        rec["reused"] = firm
    if c.medlegal:  # an expert's report is reviewed, never itemized as treatment
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
    rows = _leave_out_foreign(sr, ud, rows, skipped)
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
    ordered = [x for x in rows if str(x["id"]) not in extracts]
    firm_of = {str(r["id"]): firm[r["sha256"]]["name"] for r in ordered if (r.get("sha256") or "") in firm}
    read = _classify(sr, ordered, seen, ident, text_of, firm_of)
    recs, units, k, whole = [], [], 0, {}
    for r in ordered:  # exhibit numbers follow pull order
        rid, f = str(r["id"]), firm.get(r.get("sha256") or "")
        c = read[rid]
        if c.home is not None:  # every page repeats a larger new document: cited there
            c.pdf.unlink(missing_ok=True)
            whole[rid] = SUB.Subset(
                rid, c.home, len(c.decisions), sum(why == S.OVERLAP for _, why in c.decisions.values())
            )
            continue
        n = int(f["exhibit"]) if f else int(p["top_exhibit"]) + k + 1
        rec, urow = _build(sr, r, n, cdir, c, f["name"] if f else None)
        if rec["unread"] and not f and str(r.get("ext") or "").lower() in UO.IMAGE_EXTS:
            _set_aside(sr, rec, skipped)  # an image the transcription could not read: named, never a hold
            continue
        sub = None if f else _refiled_whole(sr, rec, ident.pages.get(rid) or {}, by_ex)
        if sub is not None:  # every page already delivered: cited there, never a new exhibit
            whole[sub.doc] = sub
            continue
        k += 0 if f else 1
        recs.append(rec)
        units.append(urow)
    extracts = {**extracts, **PG.merge_extracts(whole, {})}
    unread = [f"Exhibit {r['exhibit']} p. {r['unread'][:6]}" for r in recs if r["unread"]]
    if unread:
        sr.log(HELD + f"page(s) the transcription could not read: {unread[:5]}")
        return 2
    subs = _subset_records(rows, recs, extracts, {int(e["exhibit"]): e["name"] for e in read_by_delivery})
    _write(sr, ud, recs, units, skipped, subs, _readings(sr, read))
    pages = sum(len(r["read"]) for r in recs)
    if not pages:
        # An answer, not a hold (2026-10-25): delivered already current, nothing filed (``update/outcome``).
        sr.log("nothing new to read: no page of the new documents is left to read; the delivered chronology is current")
        return UO.EXIT
    sr.log(
        f"{len(recs)} new exhibit(s) from {len(rows)} document(s); {pages} page(s) to compose; "
        f"{len(subs)} re-filed extract(s) cited in the whole document; skipped {skipped}"
    )
    return 0


def _set_aside(sr: StageRun, rec: dict[str, Any], skipped: dict[str, list[str]]) -> None:
    """An image file (a photograph, a phone picture of a page) whose one
    reading the transcription could not make (2026-10-25): not an exhibit and
    not a hold. It is recorded in ``selection.json`` "skipped" under
    ``outcome.IMAGE_UNREAD``, named in Records Reviewed as not read, and left
    uncovered so the next update reads it again (``update/coverage``)."""
    (sr.slug_dir / "out" / sr.unit.unit / f"{rec['title']}.pdf").unlink(missing_ok=True)
    (update_dir(sr) / "compose" / f"{rec['id']}.txt").unlink(missing_ok=True)
    skipped.setdefault(UO.IMAGE_UNREAD, []).append(str(rec["id"]))
    sr.log(f"an image the page transcription could not read is set aside and named (document id {rec['id']})")


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


def _readings(sr: StageRun, read: dict[str, _Read]) -> list[dict[str, Any]]:
    """Writes each page read from a copy's richer reading into its own
    document's transcript (``text/<id>.txt``: compose, the audit and every
    later check read the cited page from it) and returns one record per page.
    Only the page's body changes; its ``[p.N]`` line is kept. A redo finds
    the two readings equal, so the copy's page is a confirmed repeat and
    nothing is written again."""
    out: list[dict[str, Any]] = []
    for rid, c in read.items():
        if not c.readings:
            continue
        tp = sr.slug_dir / "text" / f"{rid}.txt"
        tp.write_text(
            _with_pages(tp.read_text(encoding="utf-8", errors="replace"), c.pages, set(c.readings)), encoding="utf-8"
        )
        out += [{"doc": rid, "page": p, "from_doc": d, "from_page": q} for p, (d, q) in sorted(c.readings.items())]
    return out


def _with_pages(text: str, pages: dict[int, str], which: set[int]) -> str:
    """`text` (a page-marked transcript) with the bodies of the pages in
    `which` replaced by `pages`."""
    marks = list(S.PAGE_MARK.finditer(text))
    out, at = [], 0
    for i, m in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(text)
        n = int(m.group(1))
        if n in which:
            out += [text[at : m.end()], "\n" + pages[n].strip("\n") + "\n"]
            at = end
    return "".join([*out, text[at:]])


def _write(
    sr: StageRun,
    ud: Path,
    recs: list[dict],
    units: list[dict],
    skipped: dict,
    subs: list[dict],
    readings: list[dict] | None = None,
) -> None:
    (sr.slug_dir / "units").mkdir(parents=True, exist_ok=True)
    (sr.slug_dir / "units" / f"{sr.unit.unit}.json").write_text(json.dumps(units, indent=1), encoding="utf-8")
    (ud / "new_exhibits.json").write_text(json.dumps(recs, indent=1), encoding="utf-8")
    sel = {"exhibits": recs, "skipped": skipped, "excluded_named": _excluded_named(sr), "subsets": subs}
    sel["readings"] = readings or []
    (ud / "selection.json").write_text(json.dumps(sel, indent=1), encoding="utf-8")


def _excluded_named(sr: StageRun) -> list[dict[str, Any]]:
    return list(read_json(sr.slug_dir / "named_excluded.json", []) or [])
