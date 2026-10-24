"""`exhibits`: build the exhibit SET and remap the chronology's citations
onto it. $0, mechanical.

An exhibit is one provider's source records merged in date order: no cover
page, no bates stamp, no added structure, named by the firm's convention
(`Exhibit <n> - <Provider> - <MM-DD-YYYY> - <MM-DD-YYYY> (<Record Type>).pdf`).
Citations in the assembled chronology point at per-file exhibit numbers;
this step merges each provider's files into one PDF and rewrites every
citation to `(Exhibit <group> - p. <offset + original page>)`, so a reader
who opens the exhibit lands on the page the sentence came from.

ONE EXHIBIT PER PROVIDER: the firm reads an exhibit as a provider's record
set (a first cut split bulk productions, inferring a rule from one instance
in the exemplar, and the firm asked why two providers had been split).
Exhibits are built only for provider groups the chronology actually cites;
a citation in the text overrides the undated-lane presumption, a cited
sentinel lane refuses, and a citation that cannot be remapped is exit 1.

Every cited record gets pages (2026-10-24 audit, rows 7 and 8). A cited
.docx/.doc narrative report has no pages of its own, and a PDF pypdf cannot
page (encrypted, damaged) is tried with pymupdf; either way, when no reader
pages the file, its exhibit pages are rendered from the extracted text the
map read (``rendered_from_text`` in page_map.json), and Records Reviewed and
Limitations names each such file. A file nothing can page is recorded as its
error in the page map, never a crash.
"""

from __future__ import annotations

import json
import re
from io import BytesIO
from pathlib import Path
from typing import Any

from .base import StageRun, read_json, read_jsonl
from .group import index_rows

CITE = re.compile(r"\(Exhibit (\d+)(?: - p\. ([0-9,\s\-]+))?\)")
# What can become exhibit pages: a PDF as it is, an image as one page. A
# client's phone photo of a consent form or a lab header IS a record the
# chronology cites; skipping it left the citation pointing at nothing
# (live-caught 2026-09-15: twelve cited photos, zero exhibit pages).
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp", ".gif", ".webp"}
PAGE_SOURCES = IMAGE_EXTS | {".pdf"}


def _reader(path: str, ext: str) -> Any:
    """Pages of a source file: the PDF itself, or an image as one PDF page
    at its own size (pymupdf, already a runtime dependency)."""
    from pypdf import PdfReader

    if ext == ".pdf":
        return PdfReader(path)
    # `import pymupdf`, never `import fitz`: the alias prints a deprecation
    # notice to STDOUT, and the daemon reads the runner's stdout as its JSON
    # verdict. Live 2026-09-16: one such line turned a recoverable refusal
    # into "exited without a verdict", which the ledger records as failed,
    # a state with no way back (tests/test_runner_stdout_is_the_verdict.py).
    import pymupdf

    with pymupdf.open(path) as img:
        return PdfReader(BytesIO(img.convert_to_pdf()))


#: Word-processing files: extract reads their text (stages/extract.py
#: WORD_EXTS), and a cited one becomes exhibit pages rendered from that text.
WORD_EXTS = {".docx", ".doc"}
PAGE_MARK = re.compile(r"\[p\.(\d+)\]")
_PAGE_W, _PAGE_H, _MARGIN = 612, 792, 54


def _text_reader(text: str) -> Any:
    """Exhibit pages made from a document's extracted text. Text that carries
    page marks becomes one page per marked page, scaled to fit, so a citation
    to page N lands on page N; text without marks flows onto as many pages as
    it needs. The words are the document's own, never composed; the layout is
    not, and the limitations say so."""
    import html

    import pymupdf
    from pypdf import PdfReader

    marks = list(PAGE_MARK.finditer(text))
    if marks:
        segs = [text[m.end() : marks[k + 1].start() if k + 1 < len(marks) else len(text)] for k, m in enumerate(marks)]
    else:
        segs = []
    rect = pymupdf.Rect(_MARGIN, _MARGIN, _PAGE_W - _MARGIN, _PAGE_H - _MARGIN)
    css = "body { font-family: sans-serif; font-size: 10pt; } p { white-space: pre-wrap; margin: 0; }"
    doc = pymupdf.open()
    try:
        if segs:
            for seg in segs:
                page = doc.new_page(width=_PAGE_W, height=_PAGE_H)
                page.insert_htmlbox(rect, f"<p>{html.escape(seg.strip())}</p>", css=css, scale_low=0)
        else:
            story = pymupdf.Story(html=f"<p>{html.escape(text.strip())}</p>", user_css=css)
            buf = BytesIO()
            writer = pymupdf.DocumentWriter(buf)
            more = True
            while more:
                dev = writer.begin_page(pymupdf.Rect(0, 0, _PAGE_W, _PAGE_H))
                more, _ = story.place(rect)
                story.draw(dev)
                writer.end_page()
            writer.close()
            return PdfReader(BytesIO(buf.getvalue()))
        return PdfReader(BytesIO(doc.tobytes()))
    finally:
        doc.close()


def _pages_of(path: str, ext: str, text_path: str | None) -> tuple[list[Any], bool]:
    """(pages, rendered_from_text) for one source file. A PDF is paged by
    pypdf, then by pymupdf (it opens files pypdf cannot, an encrypted Kaiser
    production among them), then from its extracted text; a word file from
    its text. pypdf reads lazily, so `.pages` is touched INSIDE each attempt:
    outside it, the first encrypted PDF crashed the stage (2026-10-24 audit,
    row 8). Raises when nothing could page the file."""
    errors: list[str] = []
    if ext in PAGE_SOURCES:
        try:
            r = _reader(path, ext)
            return list(r.pages), False
        except Exception as exc:  # noqa: BLE001 - one reader failing falls through to the next
            errors.append(f"{type(exc).__name__}: {exc}")
        if ext == ".pdf":
            try:
                import pymupdf
                from pypdf import PdfReader

                with pymupdf.open(path) as doc:
                    if doc.needs_pass:
                        raise ValueError("the file needs a password to open")
                    return list(PdfReader(BytesIO(doc.convert_to_pdf())).pages), False
            except Exception as exc:  # noqa: BLE001 - recorded, and the text is tried next
                errors.append(f"{type(exc).__name__}: {exc}")
    if text_path and Path(text_path).is_file():
        text = Path(text_path).read_text(encoding="utf-8", errors="replace")
        if text.strip():
            return list(_text_reader(text).pages), True
    raise ValueError("; ".join(errors) or f"no pages and no extracted text for a {ext or 'extensionless'} file")


BILL = re.compile(r"(?i)\bbill|ledger|invoice|statement|charges\b")
CERT = re.compile(r"(?i)certif")
EXHIBIT_FILE = re.compile(r"Exhibit \d+ - .*\.pdf(\.orig|\.stripped)?$")


def us(d: str) -> str:
    return f"{d[5:7]}-{d[8:10]}-{d[0:4]}" if d and d[0] != "9" else ""


def _clear_stale(out: Path) -> None:
    """A rebuild that changes an exhibit's TITLE otherwise leaves the old file
    beside the new one, and glob-based downstream stages classify the stale
    one. Only exhibit pdfs and their strip derivatives, never worksheets."""
    for p in out.iterdir():
        if EXHIBIT_FILE.match(p.name):
            p.unlink()


def remap_citations(text: str, remap: dict[int, tuple[int, int]]) -> tuple[str, set[int]]:
    missing: set[int] = set()

    def sub(m: re.Match) -> str:
        old = int(m.group(1))
        if old not in remap:
            missing.add(old)
            return m.group(0)
        new, off = remap[old]
        if not m.group(2):
            return f"(Exhibit {new})"
        pages = []
        for tok in re.split(r",\s*", m.group(2).strip()):
            rng = re.match(r"(\d+)\s*-\s*(\d+)$", tok.strip())
            if rng:
                pages.append(f"{int(rng.group(1)) + off - 1}-{int(rng.group(2)) + off - 1}")
            elif tok.strip().isdigit():
                pages.append(str(int(tok.strip()) + off - 1))
        return f"(Exhibit {new} - p. {', '.join(pages)})" if pages else f"(Exhibit {new})"

    return CITE.sub(sub, text), missing


def run(sr: StageRun) -> int:
    from pypdf import PdfWriter

    d = sr.slug_dir
    unit = sr.unit.unit
    rd = d / "runs" / unit
    out = d / "out" / unit
    out.mkdir(parents=True, exist_ok=True)
    _clear_stale(out)
    groups = read_json(d / "groups" / f"{unit}.json", [])
    exmap: dict[str, int] = read_json(rd / "exhibit_map.json", {})
    files = read_json(d / "units" / f"{unit}.json", [])
    byname = {f["name"] + (f.get("ext") or ""): f for f in files}
    raw = {r["id"]: r for r in read_jsonl(d / "raw_manifest.jsonl") if r.get("ok")}
    idx_dates, _ = index_rows(rd, {f["id"]: name for name, f in byname.items()})
    scoped, merged = rd / "entries_scoped.md", rd / "merged.md"
    if not scoped.is_file() and merged.is_file() and merged.stat().st_size > 50:
        sr.log("REFUSING TO BUILD: merged.md holds merged cluster entries but entries_scoped.md does not exist")
        return 1
    src_text = (scoped if scoped.is_file() else rd / "entries.md").read_text(encoding="utf-8")
    cited_old = {int(m.group(1)) for m in re.finditer(r"\(Exhibit (\d+)", src_text)}

    def text_cited(g: dict[str, Any]) -> bool:
        ids = set(g["file_ids"])
        return any(old in cited_old for name, old in exmap.items() if (byname.get(name) or {}).get("id") in ids)

    live = []
    for g in groups:
        if not any((byname.get(n) or {}).get("id") in set(g["file_ids"]) for n in exmap):
            continue
        if g.get("exhibit", True):
            live.append(g)
        elif text_cited(g):
            if g["provider"].startswith("(unattributed"):
                sr.log("REFUSING TO BUILD: an entry cites a file in the unresolved sentinel lane")
                return 1
            sr.log(f"  !! lane '{g['provider']}' has no dated entries but IS cited; included")
            live.append(g)
    live.sort(key=lambda g: g["first"])

    page_map: list[dict[str, Any]] = []
    remap: dict[int, tuple[int, int]] = {}
    n = 0
    for g in live:
        gfiles = []
        for name, old in sorted(exmap.items(), key=lambda kv: kv[1]):
            f = byname.get(name)
            if not f or f["id"] not in g["file_ids"]:
                continue
            ds = sorted(idx_dates.get(name, []))
            gfiles.append((ds[0] if ds else "9999", name, old, f))
        gfiles.sort(key=lambda x: (x[0], x[2]))
        if not gfiles:
            continue
        n += 1
        w = PdfWriter()
        entries: list[dict[str, Any]] = []
        cursor = 1
        for _first, name, old, f in gfiles:
            rec = raw.get(f["id"])
            ext = (f.get("ext") or "").lower()
            if not rec or ext not in PAGE_SOURCES | WORD_EXTS:
                continue
            try:
                pages, rendered = _pages_of(rec["path"], ext, f.get("text_path"))
                for pg in pages:
                    w.add_page(pg)
            except Exception as exc:  # noqa: BLE001 - an unreadable file is recorded as that file's error in the exhibit list and the loop continues
                entries.append({"file": name, "error": str(exc)[:200]})
                sr.log(f"  !! {name}: could not be paged: {str(exc)[:120]}")
                continue
            remap[old] = (n, cursor)
            row: dict[str, Any] = {"file": name, "old_exhibit": old, "start_page": cursor, "pages": len(pages)}
            if rendered:
                row["rendered_from_text"] = True
                sr.log(f"  {name}: exhibit pages rendered from its extracted text ({len(pages)} pp.)")
            entries.append(row)
            cursor += len(pages)
        if cursor == 1:
            n -= 1
            continue
        names = " ".join(e["file"] for e in entries)
        rt = (
            "Certified Medical Records"
            if CERT.search(names)
            else "Medical Records & Bills"
            if BILL.search(names)
            else "Medical Records"
        )
        ud = sorted({dt for _f, name, _o, _x in gfiles for dt in idx_dates.get(name, [])})
        span = us(ud[0]) if ud else us(g["first"])
        if len(ud) > 1 and ud[-1] != ud[0]:
            span += f" - {us(ud[-1])}"
        title = f"Exhibit {n} - {g['provider']} - {span} ({rt})"
        with (out / (re.sub(r"[/:]", "-", title) + ".pdf")).open("wb") as fh:
            w.write(fh)
        page_map.append(
            {
                "exhibit": n,
                "title": title,
                "provider": g["provider"],
                "record_type": rt,
                "total_pages": cursor - 1,
                "files": entries,
            }
        )
        sr.log(f"Exhibit {n}: {cursor - 1:5d} pp  {g['provider'][:44]}")

    remapped, missing = remap_citations(src_text, remap)
    (rd / "entries_final.md").write_text(remapped, encoding="utf-8")
    (out / "page_map.json").write_text(json.dumps(page_map, indent=1), encoding="utf-8")
    (rd / "exhibit_remap.json").write_text(
        json.dumps({str(k): v for k, v in remap.items()}, indent=1), encoding="utf-8"
    )
    uncited = [(g["provider"], len(g["file_ids"])) for g in groups if g not in live]
    sr.log(f"{len(page_map)} exhibits, {sum(e['total_pages'] for e in page_map)} pages -> {out}")
    if missing:
        sr.log(f"citations to old exhibit(s) {sorted(missing)} could not be remapped")
        return 1
    if uncited:
        sr.log("not exhibited (no cited records): " + ", ".join(f"{p} x{c}" for p, c in uncited[:8]))
    return 0
