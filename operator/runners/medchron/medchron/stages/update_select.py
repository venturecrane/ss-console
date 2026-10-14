"""`update_select`: the new documents become new exhibits, and only their
pages the delivery did not already read go to composition. $0.

Every pulled document (the named ids, after the firm's exclusions, plus any
attachment folded from a named email) is a candidate, in pull order:

* byte-identical to a delivered exhibit: skipped (it IS a delivered record);
* not a PDF or an image: skipped and counted (it cannot be filed as an
  exhibit page the citation would point at);
* its text names a different person (``gates/foreign_identity``): HOLD, exit 2,
  until a person releases it;
* otherwise it becomes ``Exhibit N - <name> (Medical Records)``, numbered after
  the highest exhibit the delivered folder holds, its PDF built in
  ``out/<unit>/`` from the source as it is (so its page numbers are the
  source's), and its pages classified by ``update/select.classify_new``
  against the delivered exhibits' text. A page the transcription could not
  read holds (exit 2): it is never skipped silently.

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
from ..update import select as S
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


def _exhibit(sr: StageRun, r: dict[str, Any], n: int, seen: set[str], cdir: Path) -> tuple[dict, dict]:
    """(new exhibit record, units row) for one candidate."""
    out = sr.slug_dir / "out" / sr.unit.unit
    title = f"Exhibit {n} - {_stem(str(r.get('name') or ''))} (Medical Records)"
    npages = _write_pdf(Path(r["path"]), str(r.get("ext") or "").lower(), out / f"{title}.pdf")
    tp = sr.slug_dir / "text" / f"{r['id']}.txt"
    pages = S.pages_of_text(tp.read_text(encoding="utf-8", errors="replace") if tp.is_file() else "")
    row = S.row(n, title, S.classify_new(pages, npages, seen), pages)
    text_path = cdir / f"{r['id']}.txt"
    text_path.write_text(S.selected_text(pages, row["read"]), encoding="utf-8")
    rec = {**row, "id": str(r["id"]), "source_name": str(r.get("name") or ""), "sha256": r.get("sha256")}
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
    delivered_sha = {e["sha256"]: e["name"] for e in p["exhibits"]}
    rows, skipped = _candidates(sr, delivered_sha)
    holds = _identity_holds(sr, rows)
    if holds:
        sr.log(HELD + f"{len(holds)} document(s) name a different person than the client: {holds[:5]}")
        return 2
    seen = S.seen_shingles([sr.slug_dir / "delivered" / e["name"] for e in p["exhibits"]])
    recs, units = [], []
    for k, r in enumerate(rows, 1):
        rec, urow = _exhibit(sr, r, int(p["top_exhibit"]) + k, seen, cdir)
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
