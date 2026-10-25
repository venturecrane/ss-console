"""`content_dupes`: one copy of a document whose every page another pulled
document already holds. $0, after ``extract_after_fold`` and before vision.

WHY (2026-10-25, the Pamplona job). The pull keeps one copy of a file whose
BYTES repeat another's (stages/download, sha256). The firm had saved one
558-page Kaiser ER PDF twice under two names, 8 bytes apart, and two Valley Hospital
exports holding the same 547 pages in another order: every byte-different, so
both copies of each were read, composed, cited and built into exhibits, and the
duplicates were caught only at delivery (stages/exhibit_merge settles a job
already past that point). Caught here, before the paid vision pass, the copy
is read once and nothing downstream sees it.

The rule is ``exhibit_merge``'s, measured on those files: a page's signature is
its whitespace-normalized text (from ``text/<id>.txt``, the extract's own
``[p.N]`` pages) when it has at least 40 characters, else its 72-dpi grayscale
rendering. A document whose EVERY page pairs one to one with a page of a larger
(or, at equal size, earlier) document is a copy of it, provided the pairing
shifted by one page does not also hold (the falsifier; a one-page document is
never marked). A partial overlap is never a copy.

A copy is marked on ITS OWN raw_manifest row (``duplicate_of`` the kept id,
``duplicate_kind: content``, the path kept), rewritten in place: every reader of
``duplicate_of`` (extract.live_rows, the coverage gate, covered, decisions,
Limitations) then retires it as it retires a byte copy, and a second row would
be read as a second file. It leaves ``extracted.jsonl`` and ``scan_queue.json``
as extract would have written them for a retired row, and ``text_duplicates.json``
carries ``{kept, dropped, kind: content}`` for Records Reviewed and Limitations,
which says the files differ and one copy was read. Once the documents are
routed (billing documents chosen, units or exhibits built: a job resumed past
this point) the stage writes nothing: a manifest changed under them would
disagree with them, and a copy already exhibited is ``exhibit_merge``'s to
settle. On a joint matter a copy is only marked against a document in the
same client's folders: each client's copy of a shared form is that client's
record.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from .base import StageRun, read_json, read_jsonl
from .exhibit_merge import Blind, Sigs, contained
from .extract import live_rows
from .limitations import CONTENT_KIND
from ..update.select import pages_of_text

TEXT_DUPES = "text_duplicates.json"
PAGED = {".pdf", ".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp", ".gif", ".webp"}


def _key(r: dict[str, Any]) -> str:
    return str(r.get("name") or "") + str(r.get("ext") or "")


def sigs_of(row: dict[str, Any], ex: dict[str, Any]) -> Sigs | None:
    """A pulled document's page signatures: its extracted text per page, and
    its own pages drawn where the text is thin. None when it has no pages."""
    ext = str(row.get("ext") or "").lower()
    path = Path(row["path"]) if row.get("path") and ext in PAGED and Path(row["path"]).is_file() else None
    tp = ex.get("text_path")
    if tp and Path(tp).is_file():
        text = Path(tp).read_text(encoding="utf-8", errors="replace")
        pages = pages_of_text(text)
        n = int(ex.get("pages") or 0) or max(pages, default=0)
        texts = [pages.get(i, "") for i in range(1, n + 1)] if pages else [text]
        return Sigs(path, texts) if texts else None
    if path is None:
        return None
    try:
        return Sigs(path)
    except Exception:  # noqa: BLE001 - a file pymupdf cannot open has no pages to compare; it is read as it is
        return None


def _owner(sr: StageRun) -> Any:
    """Whose folder a pulled row sits in on a joint matter: the unit whose
    ``folder_prefix`` starts its folder, else None (shared). Two clients'
    copies of one form are each that client's record, so a copy is only ever
    marked against a document in the same client's folders (or both shared)."""
    prefixes = [(u.unit, u.folder_prefix or "") for u in sr.job.units] if sr.job.joint else []

    def of(r: dict[str, Any]) -> str | None:
        folder = str(r.get("folder") or "")
        return next((u for u, px in prefixes if px and folder.startswith(px)), None)

    return of


def find(sr: StageRun, rows: list[dict[str, Any]]) -> dict[str, str]:
    """{copy id: kept id}."""
    live, _ = live_rows(rows)
    extracted = {str(r.get("id")): r for r in read_jsonl(sr.slug_dir / "extracted.jsonl")}
    first = {}
    for i, r in enumerate(rows):
        first.setdefault(str(r.get("id")), i)
    docs: list[tuple[str, Sigs]] = []
    for fid, r in live.items():
        ex = extracted.get(str(fid))
        if ex is None or ex.get("error"):
            continue
        s = sigs_of(r, ex)
        if s is not None and len(s):
            docs.append((str(fid), s))
    docs.sort(key=lambda d: (-len(d[1]), first.get(d[0], 0)))
    owner = _owner(sr)
    copies: dict[str, str] = {}
    for i, (bid, b) in enumerate(docs):
        for aid, a in docs[:i]:
            if aid in copies or owner(live[aid]) != owner(live[bid]):
                continue
            try:
                mapping, why = contained(b, a)
            except Blind as exc:
                sr.log(f"  {_key(live[bid])}: pairs with {_key(live[aid])} but {exc}; read as it is")
                continue
            if mapping is not None:
                sr.log(f"  {_key(live[bid])}: all {len(b)} page(s) are in {_key(live[aid])} ({why})")
                copies[bid] = aid
                break
    return copies


def _rewrite_jsonl(path: Path, rows: list[str]) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text("".join(r + "\n" for r in rows), encoding="utf-8")
    os.replace(tmp, path)


def mark(d: Path, copies: dict[str, str]) -> None:
    """The copies' raw_manifest rows rewritten in place (every row of the id,
    so no older row reads as live), and the copies gone from what extract
    hands vision and the units."""
    raw = d / "raw_manifest.jsonl"
    lines = raw.read_text(encoding="utf-8").splitlines()
    out: list[str] = []
    for ln in lines:
        r = json.loads(ln) if ln.strip() else None
        if r is not None and r.get("ok") and str(r.get("id")) in copies and not r.get("duplicate_of"):
            ln = json.dumps({**r, "duplicate_of": copies[str(r["id"])], "duplicate_kind": CONTENT_KIND})
        out.append(ln)
    _rewrite_jsonl(raw, out)
    ext = d / "extracted.jsonl"
    if ext.is_file():
        keep = [ln for ln in ext.read_text(encoding="utf-8").splitlines() if ln.strip()]
        _rewrite_jsonl(ext, [ln for ln in keep if str(json.loads(ln).get("id")) not in copies])
    q = d / "scan_queue.json"
    if q.is_file():
        body = read_json(q, [])
        tmp = q.with_name(q.name + ".tmp")
        tmp.write_text(json.dumps([x for x in body if str(x.get("id")) not in copies], indent=1), encoding="utf-8")
        os.replace(tmp, q)


def run(sr: StageRun) -> int:
    d = sr.slug_dir
    built = [u.unit for u in sr.job.units if (d / "out" / u.unit / "page_map.json").is_file()]
    if built:
        sr.log(f"exhibits are already built for {', '.join(built)}; a copy there is exhibit_merge's to settle")
        return 0
    later = [n for n in ("billing_docs.json", "units") if (d / n).exists()]
    if later:
        sr.log(f"the documents are already routed ({', '.join(later)}); a copy is settled at exhibit_merge")
        return 0
    rows = read_jsonl(d / "raw_manifest.jsonl")
    copies = find(sr, rows)
    if not copies:
        sr.log("no pulled document holds only pages another already holds")
        return 0
    by = {str(r.get("id")): r for r in rows if r.get("ok")}
    mark(d, copies)
    have = read_json(d / TEXT_DUPES, []) or []
    seen = {(t.get("kept"), t.get("dropped")) for t in have if isinstance(t, dict)}
    for bid, aid in sorted(copies.items()):
        row = {"kept": _key(by[aid]), "dropped": _key(by[bid]), "kind": CONTENT_KIND}
        if (row["kept"], row["dropped"]) not in seen:
            have.append(row)
    tmp = d / (TEXT_DUPES + ".tmp")
    tmp.write_text(json.dumps(have, indent=1), encoding="utf-8")
    os.replace(tmp, d / TEXT_DUPES)
    sr.log(f"{len(copies)} document(s) hold only pages another already holds; one copy of each is read")
    return 0
