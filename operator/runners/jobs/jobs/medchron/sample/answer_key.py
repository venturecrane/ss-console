"""What a model WOULD have produced at each paid seam, from the spec instead.

Three seams, each written in the exact shape the production stage writes or
reads, so the production stages downstream cannot tell the difference:

* transcription (``vision``): one ``partial/<file id>.jsonl`` checkpoint row
  per scanned page, the shape vision checkpoints as it reads. The real vision
  stage then runs, finds every page already read, and writes the transcripts
  and ``ocr_results.jsonl`` itself without a single request.
* billing (``billing_extract``): one ``billing_extract.jsonl`` record per
  billing document, the shape its reader appends. The real stage then skips
  every document as done.
* composition (``map``): one ``map-01.md`` in the house map format, every
  paragraph citing the file and page the spec says proves it. This is THE seam
  where model-extracted entries become data: ``assemble`` and everything
  after it is code.

The same functions are the answer key for a rehearsal: ``expected_citations``
says, for every chronology paragraph, which file and page it must cite.
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any

from ... import wording
from . import layout
from .model import Document, Encounter, Page, Plan, norm, us_date

ILLEGIBLE = "[illegible]"


def transcription(plan: Plan, doc: Document, page: Page) -> str:
    """The page as a careful reader of its image would transcribe it."""
    if page.illegible:
        return ILLEGIBLE
    text = "\n".join(layout.page_text(plan, doc, page))
    return ("[handwritten]\n" + text) if page.hand else text


def seed_vision(plan: Plan, slug_dir: Path) -> int:
    part = slug_dir / "partial"
    part.mkdir(parents=True, exist_ok=True)
    n = 0
    for doc in plan.documents:
        rows = [{"page": pg.number, "text": transcription(plan, doc, pg)} for pg in doc.pages if pg.scan]
        if rows:
            (part / f"{doc.file_id}.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
            n += len(rows)
    return n


def billing_record(doc: Document) -> dict[str, Any]:
    chunks = []
    for pg in doc.pages:
        if pg.bill is not None:
            chunks.append(pg.bill)
        else:
            chunks.append({"doc_type": "RECORDS_ONLY", "provider": None, "patient": None, "date_first": None,
                           "date_last": None, "printed_totals": [], "printed_payments": [], "line_items": [],
                           "notes": f"page {pg.number}: not a billing page"})  # fmt: skip
    return {"file": doc.name, "pages": len(doc.pages), "chunks": chunks, "usage": [], "failures": []}


def seed_billing(plan: Plan, slug_dir: Path, names: list[str]) -> int:
    """One record per document the billing decision selected, by name."""
    by_name = {d.name: d for d in plan.documents}
    rows = [billing_record(by_name[n]) for n in names if n in by_name]
    (slug_dir / "billing_extract.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    return len(rows)


#: What a careful reader of each page kind would call it, in the scanned
#: classifier's label set (prompts/classify-system.md). A provider's fax cover
#: is the cover form the release went out under; its certification is CERT.
CLASSIFY = {"fax_cover": "ORDER", "custodian_cert": "CERT", "blank": "BLANK"}


def page_label(doc: Document, page: Page) -> str:
    return CLASSIFY.get(page.kind, "RECORD")


def seed_classify(plan: Plan, slug_dir: Path, unit: str) -> dict[str, Any]:
    """``scanned_labels.json``, the scanned classifier's output, answered from
    the spec. The stage has no per-page checkpoint to seed, so its one output
    file is written instead (as condense's is), in its exact shape: a label
    for every page it would have asked about (scanned, near a file's head,
    from ``nonrecord.json``'s unknowns), the non-record subset, and
    ``controls_ok`` from the record control ``decide_control`` chose: the key
    must call that page RECORD, or nothing it says is used, as in production."""
    from ...stages.classify import NONRECORD_LABELS, file_heads

    out = slug_dir / "out" / unit
    page_map = json.loads((out / "page_map.json").read_text(encoding="utf-8"))
    nr = json.loads((slug_dir / "nonrecord.json").read_text(encoding="utf-8"))
    by_name = {d.file_name: d for d in plan.documents}
    heads = file_heads(page_map)

    def label(ex: int, p: int) -> str:
        src = source_page(page_map, ex, p)
        if src is None:
            return "?"
        doc = by_name[src[0]]
        return page_label(doc, doc.pages[src[1] - 1])

    labels: dict[str, str] = {}
    nonrec: dict[str, list[tuple[int, str]]] = {}
    for ex in sorted(int(k) for k in nr):
        for p in sorted(set(nr[str(ex)]["unknown"]) & heads.get(ex, set())):
            lab = labels[f"Ex{ex}p{p}"] = label(ex, p)
            if lab in NONRECORD_LABELS:
                nonrec.setdefault(str(ex), []).append((p, lab))
    rc = json.loads((slug_dir / "record_control.json").read_text(encoding="utf-8"))
    ok = not rc.get("skip") and label(int(rc["exhibit"]), int(rc["page"])) == "RECORD"
    body = {"labels": labels, "nonrecord": nonrec if ok else {}, "controls_ok": ok, "answered_by": "answer key"}
    (slug_dir / "scanned_labels.json").write_text(json.dumps(body, indent=1), encoding="utf-8")
    return body


def provider_line(plan: Plan, enc: Encounter) -> str:
    return f"{enc.signer} ({plan.providers[enc.provider].facility})"


def _cite(plan: Plan, doc_id: str, page: int) -> str:
    return f"(FILE: {plan.doc(doc_id).file_name}, p. {page})"


def entry_text(plan: Plan, enc: Encounter) -> str:
    order = {h: i for i, h in enumerate(plan.headings)}
    paras = sorted(enc.paras, key=lambda p: order.get(p.heading, len(order)))
    out = [us_date(enc.date), f"{provider_line(plan, enc)} | {paras[0].heading}", ""]
    current = paras[0].heading
    for para in paras:
        if para.heading != current:
            out += [para.heading, ""]
            current = para.heading
        out += [f"{wording.fill(para.text)} {_cite(plan, para.doc, para.page)}", ""]
    return "\n".join(out).rstrip()


def _billing_dates(plan: Plan) -> list[str]:
    rows = set()
    for doc in plan.documents:
        for pg in doc.pages:
            bill = pg.bill or {}
            for item in bill.get("line_items") or []:
                d = item["date"]
                iso = f"{d[6:10]}-{d[0:2]}-{d[3:5]}"
                rows.add(f"{iso} | {bill['provider']} | {_cite(plan, doc.id, pg.number)}")
    return sorted(rows)


def map_text(plan: Plan) -> str:
    """The whole matter as one map chunk, in the five-block house shape."""
    entries = "\n\n".join(entry_text(plan, e) for e in plan.encounters)
    index = []
    cited: dict[str, int] = defaultdict(int)
    for e in plan.encounters:
        files = sorted({plan.doc(p.doc).file_name for p in e.paras})
        for f in files:
            cited[f] += 1
        index.append(f"{e.date} | {provider_line(plan, e)} | {', '.join(e.icd) or '--'} | {'; '.join(files)}")
    seen = []
    for d in plan.documents:
        tag = (
            f"entries: {cited[d.file_name]}" if cited[d.file_name] else "nothing extractable: no dated encounter cited"
        )
        seen.append(f"=== FILE: {d.file_name} (fileId {d.file_id}, {len(d.pages)} pages) === {tag}")
    conflicts = "\n".join(f"- {c}" for c in plan.conflicts) or "none observed"
    return (
        f"## ENTRIES\n{entries}\n\n## INDEX\n" + "\n".join(index) + "\n\n## BILLING-DATES\n"
        + ("\n".join(_billing_dates(plan)) or "none in this chunk")
        + f"\n\n## CONFLICTS / REFERENCED-BUT-ABSENT\n{conflicts}\n\n## FILES-SEEN\n" + "\n".join(seen) + "\n"
    )  # fmt: skip


def expected_citations(plan: Plan) -> list[dict[str, Any]]:
    """Every chronology paragraph and the file page it must cite: the answer
    key a rehearsal of a chronology change is scored against."""
    out = []
    for e in plan.encounters:
        for p in e.paras:
            doc = plan.doc(p.doc)
            out.append({"date": e.date, "provider": provider_line(plan, e), "heading": p.heading,
                        "text": wording.fill(p.text), "file": doc.file_name, "file_id": doc.file_id,
                        "page": p.page, "evidence": p.evidence})  # fmt: skip
    return out


def write(plan: Plan, path: Path) -> None:
    """The answer key as JSON: citations, defects, and every page's text."""
    pages = {d.file_name: {str(pg.number): transcription(plan, d, pg) for pg in d.pages} for d in plan.documents}
    body = {
        "matter": plan.matter,
        "citations": expected_citations(plan),
        "defects": plan.defects,
        "conflicts": plan.conflicts,
        "page_texts": pages,
    }
    path.write_text(json.dumps(body, indent=1, sort_keys=True), encoding="utf-8")


_CITE = re.compile(r"\(Exhibit (\d+) - p\. (\d+)\)")
_DATE = re.compile(r"^(\d{2})/(\d{2})/(\d{4})$")


def cited_paragraphs(final_md: str) -> list[tuple[str, str, int, int]]:
    """(entry date ISO, paragraph text, exhibit, exhibit page) for every cited
    paragraph in the Medical Chronology section of ``final-chronology.md``."""
    body = final_md.split("## Medical Chronology", 1)[1].split("## Exhibit List", 1)[0]
    out, day = [], ""
    for block in re.split(r"\n\s*\n", body):
        line = block.strip()
        m = _DATE.match(line.splitlines()[0]) if line else None
        if m:
            day = f"{m.group(3)}-{m.group(1)}-{m.group(2)}"
            continue
        for c in _CITE.finditer(line):
            out.append((day, _CITE.sub("", line).strip(), int(c.group(1)), int(c.group(2))))
    return out


def source_page(page_map: list[dict[str, Any]], exhibit: int, page: int) -> tuple[str, int] | None:
    """The matter file and its own page that an exhibit page is."""
    for ex in page_map:
        if ex["exhibit"] != exhibit:
            continue
        for f in ex["files"]:
            start, n = int(f.get("start_page", 0)), int(f.get("pages", 0))
            if start <= page < start + n:
                return str(f["file"]), page - start + 1
    return None


def unstripped_page(drops: dict[str, list[int]] | None, exhibit: int, page: int) -> int:
    """The pre-strip exhibit page a stripped exhibit's ``page`` is: the
    ``page``-th page the strip kept (``strip_result.json`` ``drops``)."""
    gone = set((drops or {}).get(str(exhibit)) or [])
    kept = 0
    old = 0
    while kept < page:
        old += 1
        if old not in gone:
            kept += 1
    return old


def score(
    plan: Plan, final_md: str, page_map: list[dict[str, Any]], drops: dict[str, list[int]] | None = None
) -> list[str]:
    """Every way a built chronology departs from the key: a paragraph the key
    does not hold, one cited to the wrong page, one whose page does not print
    its fact, and one the key holds that the chronology lost. Empty is a pass.
    This is the $0 rehearsal of a change to the stages after the map. After
    the strip, ``drops`` maps each cited page back to its pre-strip page."""
    key = {(c["date"], norm(c["text"])): c for c in expected_citations(plan)}
    by_name = {d.file_name: d for d in plan.documents}
    seen, problems = set(), []
    for day, text, exhibit, page in cited_paragraphs(final_md):
        want = key.get((day, norm(text)))
        if want is None:
            problems.append(f"{day}: paragraph not in the answer key: {text[:70]!r}")
            continue
        seen.add((day, norm(text)))
        got = source_page(page_map, exhibit, unstripped_page(drops, exhibit, page))
        if got is None or got != (want["file"], want["page"]):
            problems.append(f"{day}: {text[:50]!r} cites {got}, the key says {want['file']} p. {want['page']}")
            continue
        doc = by_name[got[0]]
        if norm(want["evidence"]) not in norm(transcription(plan, doc, doc.pages[got[1] - 1])):
            problems.append(f"{day}: {got[0]} p. {got[1]} does not print {want['evidence']!r}")
    problems += [f"{d}: lost from the chronology: {t[:70]!r}" for d, t in sorted(set(key) - seen)]
    return problems
