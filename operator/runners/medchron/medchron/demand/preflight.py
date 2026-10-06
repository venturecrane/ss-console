"""`preflight`: everything that can be known for free before anything is paid.

1. **Extract**, page by page, and decide per PAGE whether the text layer can be
   read. Three bills on 2026-09-24 read as blank because their text layers were
   control characters, which the chronology's glyph detector does not catch;
   each was found after a summary existed and each re-ran it. Here a page is
   routed to transcription now when its layer is glyph indices, a cipher,
   control or private-use characters, or empty on a page that carries an image.
2. **Premise scan** by document name and email subject (acceptance, release,
   prior demand, policy-limits letter, denial, lawsuit, litigation funding):
   one matter that day had settled at the limit months earlier, with the
   acceptance letter at the top of the file.
3. **Bill reconciliation**: the Medicals tab against the bills in the file,
   both directions (a provider pool built from the tab omitted three billed
   providers that day).
4. **The estimate**: characters through the summary, transcription pages, and
   the fixed drafting tail, at the firm's measured rates.
"""

from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path
from typing import Any, Callable

from ..stages.extract import docx_text, glyph_junk, not_english
from .firm import DemandFirm

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".tif", ".tiff"}
CONTROL_THRESHOLD = 0.10  # share of a page's characters that are control/private-use/replacement
MIN_PAGE_CHARS = 25
BILL_NAME = re.compile(
    r"(?i)\b(bill|billing|statement|ledger|itemi[sz]|ub-?04|hcfa|cms-?1500|invoice|balance|charges)\b"
)


def junk_page(text: str) -> bool:
    """A text layer that cannot be read: control characters, private-use
    glyphs and replacement marks (the 2026-09-24 bills), or glyph indices, or
    a cipher. The share is over non-whitespace characters."""
    chars = [c for c in text if not c.isspace()]
    if not chars:
        return False
    bad = sum(1 for c in chars if unicodedata.category(c) in ("Cc", "Co", "Cs") or c == "�")
    return bad / len(chars) > CONTROL_THRESHOLD or glyph_junk(text) or not_english(text)


def _pdf_pages(path: Path) -> list[dict[str, Any]]:
    import pymupdf

    out = []
    with pymupdf.open(str(path)) as doc:
        for i, page in enumerate(doc, 1):
            text = page.get_text() or ""
            has_image = bool(page.get_images(full=False))
            out.append({"page": i, "text": text, "has_image": has_image})
    return out


def _needs_reading(p: dict[str, Any]) -> bool:
    stripped = p["text"].strip()
    if len(stripped) < MIN_PAGE_CHARS:
        return p["has_image"]
    return junk_page(stripped)


def extract_one(row: dict[str, Any], text_dir: Path) -> dict[str, Any]:
    """One corpus document: its text with ``[p.N]`` markers, and the pages a
    machine must transcribe (``transcribe``: page numbers, or "all")."""
    rec = {k: row.get(k) for k in ("id", "name", "folder", "kind", "ext", "subject")}
    out = text_dir / f"{row['id']}.txt"
    ext = (row.get("ext") or "").lower()
    try:
        if row.get("text_path"):  # an email body, already text
            text = Path(row["text_path"]).read_text(encoding="utf-8", errors="replace")
            rec.update(pages=1, transcribe=[])
        elif ext == ".pdf":
            pages = _pdf_pages(Path(row["path"]))
            bad = [p["page"] for p in pages if _needs_reading(p)]
            text = "\n".join(f"[p.{p['page']}]\n{'' if p['page'] in bad else p['text']}" for p in pages)
            rec.update(pages=len(pages), transcribe=bad)
        elif ext in IMAGE_EXTS:
            text = ""
            rec.update(pages=1, transcribe="all")
        elif ext == ".docx":
            text = docx_text(Path(row["path"]))
            rec.update(pages=1, transcribe=[])
        else:
            rec.update(pages=0, transcribe=[], unextractable=f"no extractor for {ext or 'this file'}")
            return rec
    except Exception as exc:  # noqa: BLE001 - one broken file is one row, named
        rec.update(pages=0, transcribe=[], unextractable=f"{type(exc).__name__}: {str(exc)[:120]}")
        return rec
    out.write_text(text, encoding="utf-8")
    rec.update(text_path=str(out), chars=len(text), source_path=row.get("path"))
    return rec


# ---- premise scan --------------------------------------------------------------
def premise_scan(rows: list[dict[str, Any]], firm: DemandFirm) -> list[dict[str, str]]:
    """Every name or subject that matches an authored premise class."""
    hits = []
    pats = {cls: [re.compile(p, re.I) for p in pl] for cls, pl in firm.get("premise", "scan").items()}
    for r in rows:
        label = " ".join(str(r.get(k) or "") for k in ("name", "subject"))
        for cls, ps in pats.items():
            if any(p.search(label) for p in ps):
                hits.append({"class": cls, "document": str(r.get("name")), "id": str(r.get("id"))})
    return hits


# ---- bill reconciliation ---------------------------------------------------------
def _tokens(name: str) -> set[str]:
    stop = {"medical", "center", "group", "inc", "llc", "the", "and", "of", "health", "care", "clinic"}
    return {t for t in re.findall(r"[a-z]{4,}", name.lower()) if t not in stop}


def _head(r: dict[str, Any]) -> str:
    """A bill's first page carries the provider's letterhead; its file name
    often does not ("ER bill 1-15-26")."""
    p = r.get("text_path")
    try:
        return Path(p).read_text(encoding="utf-8", errors="replace")[:3000] if p else ""
    except OSError:
        return ""


def reconcile(medicals: list[dict[str, Any]], rows: list[dict[str, Any]]) -> dict[str, Any]:
    """The Medicals tab against the file's bills, by provider-name tokens.
    Coarse on purpose: it flags for the gap audit, it never decides."""
    bills = [r for r in rows if BILL_NAME.search(str(r.get("name") or ""))]
    bill_tokens = [
        (r, _tokens(" ".join([str(r.get("name") or ""), str(r.get("folder") or ""), _head(r)]))) for r in bills
    ]
    tab_without_bill, matched = [], set()
    for m in medicals:
        toks = _tokens(m.get("provider") or "")
        hit = [r for r, bt in bill_tokens if toks & bt]
        matched.update(id(r) for r in hit)
        if not hit:
            tab_without_bill.append(m.get("provider"))
    bills_off_tab = [str(r.get("name")) for r, _ in bill_tokens if id(r) not in matched]
    return {
        "tab_providers": len(medicals),
        "tab_charges": [c for m in medicals for c in m.get("charges") or []],
        "bills_in_file": len(bills),
        "tab_providers_without_a_bill_in_file": tab_without_bill,
        "bills_whose_provider_is_not_on_the_tab": bills_off_tab,
    }


# ---- the estimate ----------------------------------------------------------------
def estimate(extracted: list[dict[str, Any]], firm: DemandFirm) -> dict[str, Any]:
    chars = sum(int(r.get("chars") or 0) for r in extracted)
    pages = sum(
        (int(r.get("pages") or 1) if r.get("transcribe") == "all" else len(r.get("transcribe") or []))
        for r in extracted
    )
    b = firm.data["budget"]
    # Transcribed pages enter the summary too: about 2,500 characters a page.
    usd = (chars + pages * 2500) / 1e6 * b["usd_per_million_chars"] + pages * b["usd_per_scanned_page"]
    usd += b["usd_drafting_fixed"]
    total_pages = sum(int(r.get("pages") or 0) for r in extracted)
    return {"pages": total_pages, "characters": chars, "transcription_pages": pages, "usd": round(usd, 2)}


def run(data: Path, firm: DemandFirm, facts: dict[str, Any], log: Callable[[str], None]) -> dict[str, Any]:
    corpus = json.loads((data / "corpus.json").read_text(encoding="utf-8"))
    text_dir = data / "text"
    text_dir.mkdir(parents=True, exist_ok=True)
    extracted = [extract_one(r, text_dir) for r in corpus]
    with (data / "extracted.jsonl").open("w", encoding="utf-8") as fh:
        for r in extracted:
            fh.write(json.dumps(r) + "\n")
    report = {
        "documents": len(extracted),
        "unextractable": [{"name": r["name"], "why": r["unextractable"]} for r in extracted if r.get("unextractable")],
        "premise_hits": premise_scan(extracted, firm),
        "bills": reconcile(facts.get("medicals") or [], extracted),
        "estimate": estimate(extracted, firm),
    }
    (data / "preflight.json").write_text(json.dumps(report, indent=1), encoding="utf-8")
    e = report["estimate"]
    log(
        f"preflight: {report['documents']} documents, {e['pages']} pages, {e['characters']:,} characters, "
        f"{e['transcription_pages']} pages to transcribe, {len(report['premise_hits'])} premise hits"
    )
    return report
