"""`update_select`: the new documents become new exhibits, and only their
pages the delivery did not already read go to composition. $0.

Every pulled document (the named ids, after the firm's exclusions, plus any
attachment folded from a named email) is a candidate, in pull order:

* byte-identical to a delivered exhibit (one the delivered Exhibit List
  names): skipped (it IS a delivered record);
* byte-identical to a FIRM-numbered file in the chronology folder the list
  never named ("Exhibit 6- ..."): read, and it takes that file's number; it
  is never filed again, and the Exhibit List names the firm's file;
* not a PDF or an image: skipped and counted (it cannot be filed as an
  exhibit page the citation would point at);
* its text names a different person (``gates/foreign_identity``): HOLD, exit 2,
  until a person releases it;
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
from pathlib import Path
from typing import Any

from ..gates import foreign_identity as FI
from ..update import body as B, select as S
from ..update.locate import vendor_name
from .base import StageRun, read_json, read_jsonl
from .update_base import HELD, plan, update_dir

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
        tp = sr.slug_dir / "text" / f"{r['id']}.txt"
        text = tp.read_text(encoding="utf-8", errors="replace") if tp.is_file() else ""
        found = FI.findings(text, surnames, dobs)
        if found and str(r["id"]) not in free:
            holds.append(f"{r['id']}: {'; '.join(found)}")
    return holds


def _firm_stem(name: str) -> str:
    """The firm's numbered file name without its "Exhibit N-" and extension."""
    return _stem(re.sub(r"^Exhibit\s*\d+\s*[-–:]\s*", "", name, flags=re.I))


def _exhibit(
    sr: StageRun, r: dict[str, Any], n: int, seen: set[str], cdir: Path, earlier: S.Earlier, firm: str | None = None
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
    row = S.row(n, title, S.classify_new(pages, npages, seen, earlier), pages)
    text_path = cdir / f"{r['id']}.txt"
    text_path.write_text(S.selected_text(pages, row["read"]), encoding="utf-8")
    rec = {**row, "id": str(r["id"]), "source_name": vendor_name(r), "sha256": r.get("sha256")}
    if firm:
        rec["reused"] = firm
    unit_row = {
        "id": str(r["id"]),
        "name": title,
        "ext": ".pdf",
        "text_path": str(text_path),
        "compose": bool(row["read"]),
    }
    return rec, unit_row


def run(sr: StageRun) -> int:
    p = plan(sr)
    if not p:
        sr.log("update_select: no update plan (update_base has not run)")
        return 1
    ud = update_dir(sr)
    cdir = ud / "compose"
    cdir.mkdir(parents=True, exist_ok=True)
    base = ud / "base.md"  # update_base writes it with the plan; without it every folder exhibit counts as read
    listed = (
        B.delivered_numbers(base.read_text(encoding="utf-8"))
        if base.is_file()
        else {e["exhibit"] for e in p["exhibits"]}
    )
    read_by_delivery = [e for e in p["exhibits"] if e["exhibit"] in listed]
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
    recs, units, earlier, k = [], [], S.Earlier(), 0
    for r in rows:
        f = firm.get(r.get("sha256") or "")
        if f is None:
            k += 1
        n = int(f["exhibit"]) if f else int(p["top_exhibit"]) + k
        rec, urow = _exhibit(sr, r, n, seen, cdir, earlier, f["name"] if f else None)
        recs.append(rec)
        units.append(urow)
    unread = [f"Exhibit {r['exhibit']} p. {r['unread'][:6]}" for r in recs if r["unread"]]
    if unread:
        sr.log(HELD + f"page(s) the transcription could not read: {unread[:5]}")
        return 2
    _write(sr, ud, recs, units, skipped)
    pages = sum(len(r["read"]) for r in recs)
    if not pages:
        sr.log(HELD + "nothing new to read: every page of the new documents is already in the delivered exhibits")
        return 2
    sr.log(f"{len(recs)} new exhibit(s) from {len(rows)} document(s); {pages} page(s) to compose; skipped {skipped}")
    return 0


def _write(sr: StageRun, ud: Path, recs: list[dict], units: list[dict], skipped: dict) -> None:
    (sr.slug_dir / "units").mkdir(parents=True, exist_ok=True)
    (sr.slug_dir / "units" / f"{sr.unit.unit}.json").write_text(json.dumps(units, indent=1), encoding="utf-8")
    (ud / "new_exhibits.json").write_text(json.dumps(recs, indent=1), encoding="utf-8")
    (ud / "selection.json").write_text(
        json.dumps({"exhibits": recs, "skipped": skipped, "excluded_named": _excluded_named(sr)}, indent=1),
        encoding="utf-8",
    )


def _excluded_named(sr: StageRun) -> list[dict[str, Any]]:
    return list(read_json(sr.slug_dir / "named_excluded.json", []) or [])
