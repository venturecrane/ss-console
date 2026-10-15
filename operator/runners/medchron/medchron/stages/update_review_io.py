"""The update review's $0 inputs and its checkpoint (``stages/update_review.py``
is the stage): the page images and page text the review and finisher are
shown, the pages of a new exhibit the delivery already read, and the
checkpoint that keeps a paid review or a completed round from being paid for
twice. Split out of the stage on 2026-10-15 (the module-size ceiling); the
behavior is the stage's, unchanged."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from ..audit.page_text import exhibit_paths
from ..audit.render import img_block, render
from ..update import review_parts as RP, review_repair as RR, select as S
from .base import StageRun, read_json
from .update_base import update_dir

Images = Callable[[int, int], dict[str, Any] | None]


def _image_source(sr: StageRun) -> Images:
    out = sr.slug_dir / "out" / sr.unit.unit
    pdfs = exhibit_paths(out)

    def get(ex: int, page: int) -> dict[str, Any] | None:
        if ex not in pdfs:
            return None
        p = render(pdfs[ex], page, out / "auditpages", f"ex{ex}")
        return img_block(p) if p else None

    return get


def page_counts(out: Path) -> Callable[[int], int]:
    from pypdf import PdfReader

    pdfs, cache = exhibit_paths(out), {}

    def n(ex: int) -> int:
        if ex not in cache:
            try:
                cache[ex] = len(PdfReader(str(pdfs[ex])).pages) if ex in pdfs else 0
            except Exception:  # noqa: BLE001 - an unreadable exhibit shows no pages; its claims still audit
                cache[ex] = 0
        return cache[ex]

    return n


def page_text(sr: StageRun) -> RR.Evidence:
    """(exhibit, page) -> the text the update read off that page of an added
    exhibit (``text/<id>.txt``, in the exhibit's final number), or None."""
    cache: dict[int, dict[int, str]] = {}
    for r in read_json(update_dir(sr) / "new_exhibits.final.json", []):
        ex, doc = (r.get("exhibit"), r.get("id")) if isinstance(r, dict) else (None, None)
        tp = sr.slug_dir / "text" / f"{doc}.txt"
        if isinstance(ex, int) and isinstance(doc, str) and tp.is_file():
            cache[ex] = S.pages_of_text(tp.read_text(encoding="utf-8", errors="replace"))

    def get(ex: int, page: int) -> str | None:
        return cache.get(ex, {}).get(page)

    return get


def already_pages(sr: StageRun) -> RP.Already:
    """exhibit -> {page: the delivered page it is a copy of}, from the page
    identity the update decided before vision (``update/pageid``): a new
    exhibit's page the delivery already read."""
    ud = update_dir(sr)
    ident = (read_json(ud / "page_identity.json", {}) or {}).get("pages") or {}
    out: RP.Already = {}
    for r in read_json(ud / "new_exhibits.final.json", []):
        ex, doc = (r.get("exhibit"), r.get("id")) if isinstance(r, dict) else (None, None)
        rows = ident.get(doc) if isinstance(doc, str) and isinstance(ident, dict) else None
        if not isinstance(ex, int) or not isinstance(rows, dict):
            continue
        for page, v in rows.items():
            if isinstance(v, list) and len(v) >= 3 and v[0] == "already" and str(page).isdigit():
                home = str(v[1]).replace("exhibit:", "Exhibit ")
                out.setdefault(ex, {})[int(page)] = f"{home} p. {v[2]}"
    return out


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


# ---- the checkpoint: a completed round is never paid for twice ---------------------
CHECKPOINT = "review_checkpoint.json"


@dataclass
class At:
    """Where a resumed loop starts: after round `done` (0: none finished),
    with that round's document and record. ``rec["pending"]`` holds a review
    already paid for in the round that did not finish."""

    done: int
    md: str
    rec: dict[str, Any]


def compose_sha(ud: Path) -> str:
    """The identity of what the loop reviews: the composed new entries and
    the delivered document (``update_collect``, which also clears the
    checkpoint: a new collect is a new review)."""
    parts = [(ud / n).read_text(encoding="utf-8") if (ud / n).is_file() else "" for n in ("new_entries.md", "base.md")]
    return _sha("\0".join(parts))


def _checkpoint(ud: Path, at: At) -> None:
    row = {"compose_sha": compose_sha(ud), "done": at.done, "md": at.md, "rec": at.rec}
    (ud / CHECKPOINT).write_text(json.dumps(row), encoding="utf-8")


def _resume(sr: StageRun, ud: Path) -> At | None:
    """The last completed step of an interrupted loop over the same composed
    entries (a connection failure, a requeue), or None."""
    row = read_json(ud / CHECKPOINT, {})
    if not isinstance(row, dict) or not row.get("md") or row.get("compose_sha") != compose_sha(ud):
        return None
    done = row.get("done")
    if not isinstance(done, int) or not isinstance(row.get("rec"), dict):
        return None
    sr.log(f"update review: resuming after round {done}; what was paid for is not paid again")
    return At(done, str(row["md"]), row["rec"])


def _end(ud: Path) -> None:
    (ud / CHECKPOINT).unlink(missing_ok=True)
